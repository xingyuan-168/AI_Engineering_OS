"""Lightweight project memory with two storage layers (ADR-0016).

The Git-tracked JSONL file "docs/memory/memory.jsonl" is the single source
of truth: one JSON object per line with the fields "id", "type", "title",
"summary", "source", "source_commit", "tags", "status", and
"superseded_by". The SQLite "memory_index" table is a locally rebuildable
search index refreshed by "reindex()" or the "codex-os memory reindex"
command.

Single-writer rule: subagents never write the JSONL directly. They submit
candidates ("record_candidate") into the ignored runtime state directory;
the main session merges them into the JSONL at task finish. Secrets and
chat-log content are rejected.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from codex_ai_os.infrastructure.database import Database

MEMORY_JSONL = "docs/memory/memory.jsonl"
CANDIDATE_DIRECTORY = ".codex-os/state/memory-candidates"

MEMORY_TYPES: tuple[str, ...] = ("decision", "bug", "lesson", "pattern", "project-summary")
MEMORY_STATUSES: tuple[str, ...] = ("active", "superseded", "invalid")

_SECRET_PATTERN = re.compile(
    r"(?i)(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"(?:password|token|secret|api[_-]?key)\s*[=:]\s*\S+)"
)
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_COMMIT_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")


class MemoryStoreError(RuntimeError):
    """Raised when a memory record violates the JSONL contract."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    """One memory record as stored on the JSONL line."""

    id: str
    record_type: str
    title: str
    summary: str
    source: str
    source_commit: str | None
    tags: tuple[str, ...]
    status: str
    superseded_by: str | None
    line_number: int | None

    def to_json_line(self) -> str:
        payload = {
            "id": self.id,
            "type": self.record_type,
            "title": self.title,
            "summary": self.summary,
            "source": self.source,
            "source_commit": self.source_commit,
            "tags": list(self.tags),
            "status": self.status,
            "superseded_by": self.superseded_by,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class ReindexResult:
    indexed: int
    removed: int
    invalid_lines: tuple[str, ...]


class MemoryStore:
    def __init__(self, database: Database, project_root: Path) -> None:
        self.database = database
        self.root = project_root.resolve()

    def record(
        self,
        *,
        record_type: str,
        title: str,
        summary: str,
        source: str,
        source_commit: str | None = None,
        tags: tuple[str, ...] = (),
        status: str = "active",
        superseded_by: str | None = None,
    ) -> MemoryEntry:
        """Append one validated record to the JSONL source of truth.

        Main-session writer only (single-writer rule, ADR-0016).
        """

        entry, line = self._build_entry(
            record_type=record_type,
            title=title,
            summary=summary,
            source=source,
            source_commit=source_commit,
            tags=tags,
            status=status,
            superseded_by=superseded_by,
        )
        entries, invalid = self._parse_jsonl()
        if invalid:
            raise MemoryStoreError(
                "MEMORY_JSONL_INVALID",
                "docs/memory/memory.jsonl contains invalid lines; fix them first: "
                + "; ".join(invalid[:3]),
            )
        known_ids = {item.id for item in entries}
        if entry.id in known_ids:
            raise MemoryStoreError("MEMORY_DUPLICATE", f"memory id already exists: {entry.id}")
        for item in entries:
            if (
                item.status == "active"
                and item.record_type == entry.record_type
                and item.title.casefold() == entry.title.casefold()
            ):
                raise MemoryStoreError(
                    "MEMORY_DUPLICATE",
                    "an active memory with the same type and title exists: " + item.title,
                )
        path = self._jsonl_path()
        lines = [item.to_json_line() for item in entries]
        lines.append(line)
        _atomic_write(path, "\n".join(lines) + "\n")
        self.reindex()
        return entry

    def record_candidate(
        self,
        *,
        record_type: str,
        title: str,
        summary: str,
        source: str,
        source_commit: str | None = None,
        tags: tuple[str, ...] = (),
        status: str = "active",
        superseded_by: str | None = None,
    ) -> MemoryEntry:
        """Submit a candidate from a subagent without touching the JSONL."""

        entry, _ = self._build_entry(
            record_type=record_type,
            title=title,
            summary=summary,
            source=source,
            source_commit=source_commit,
            tags=tags,
            status=status,
            superseded_by=superseded_by,
        )
        directory = self.root / CANDIDATE_DIRECTORY
        directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(_entry_payload(entry), ensure_ascii=False, indent=2)
        _atomic_write(directory / (entry.id + ".json"), payload + "\n")
        return entry

    def candidates(self) -> tuple[MemoryEntry, ...]:
        """List submitted candidates awaiting the main-session merge."""

        directory = self.root / CANDIDATE_DIRECTORY
        if not directory.is_dir():
            return ()
        found: list[MemoryEntry] = []
        for path in sorted(directory.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                found.append(_entry_from_payload(raw, line_number=None))
            except (OSError, UnicodeError, ValueError, MemoryStoreError):
                continue
        return tuple(found)

    def load(self) -> tuple[tuple[MemoryEntry, ...], tuple[str, ...]]:
        """Return (entries, invalid-line descriptions) from the JSONL."""

        entries, invalid = self._parse_jsonl()
        return tuple(entries), tuple(invalid)

    def reindex(self) -> ReindexResult:
        """Rebuild the SQLite search index from the JSONL source of truth."""

        entries, invalid = self._parse_jsonl()
        now = _utc_now()
        with self.database.connection() as connection:
            before = int(connection.execute("SELECT COUNT(*) FROM memory_index").fetchone()[0])
            connection.execute("DELETE FROM memory_index")
            for entry in entries:
                connection.execute(
                    """
                    INSERT INTO memory_index(
                        id, record_type, title, summary, source, source_commit,
                        tags, status, superseded_by, line_number, indexed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.id,
                        entry.record_type,
                        entry.title,
                        entry.summary,
                        entry.source,
                        entry.source_commit,
                        json.dumps(list(entry.tags), ensure_ascii=False),
                        entry.status,
                        entry.superseded_by,
                        entry.line_number,
                        now,
                    ),
                )
            connection.commit()
        return ReindexResult(len(entries), max(before - len(entries), 0), tuple(invalid))

    def search(
        self,
        query: str = "",
        *,
        record_types: tuple[str, ...] = (),
        statuses: tuple[str, ...] = ("active",),
        limit: int = 20,
    ) -> tuple[MemoryEntry, ...]:
        """Search the rebuildable index (reindexes automatically when empty)."""

        if limit < 1 or limit > 100:
            raise MemoryStoreError("MEMORY_LIMIT", "memory search limit must be within 1..100")
        unknown_types = set(record_types) - set(MEMORY_TYPES)
        if unknown_types:
            raise MemoryStoreError(
                "MEMORY_TYPE", "unsupported memory types: " + str(sorted(unknown_types))
            )
        unknown_statuses = set(statuses) - set(MEMORY_STATUSES)
        if unknown_statuses or not statuses:
            raise MemoryStoreError(
                "MEMORY_STATUS", "unsupported memory statuses: " + str(sorted(unknown_statuses))
            )
        with self.database.connection() as connection:
            count = int(connection.execute("SELECT COUNT(*) FROM memory_index").fetchone()[0])
        if count == 0 and self._jsonl_path().is_file():
            self.reindex()
        clauses = ["status IN (" + ",".join("?" for _ in statuses) + ")"]
        parameters: list[object] = list(statuses)
        if record_types:
            clauses.append(
                "record_type IN (" + ",".join("?" for _ in record_types) + ")"
            )
            parameters.extend(record_types)
        for term in _query_terms(query):
            clauses.append(
                "(title LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\' "
                "OR tags LIKE ? ESCAPE '\\' OR source LIKE ? ESCAPE '\\')"
            )
            escaped = "%" + _escape_like(term) + "%"
            parameters.extend([escaped, escaped, escaped, escaped])
        sql = (
            "SELECT * FROM memory_index WHERE "
            + " AND ".join(clauses)
            + " ORDER BY line_number LIMIT ?"
        )
        parameters.append(limit)
        with self.database.connection() as connection:
            rows = connection.execute(sql, tuple(parameters)).fetchall()
        return tuple(_entry_from_row(row) for row in rows)

    def _jsonl_path(self) -> Path:
        return self.root / MEMORY_JSONL

    def _parse_jsonl(self) -> tuple[list[MemoryEntry], list[str]]:
        path = self._jsonl_path()
        if not path.is_file():
            return [], []
        entries: list[MemoryEntry] = []
        invalid: list[str] = []
        seen_ids: set[str] = set()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            return [], [MEMORY_JSONL + ": unreadable: " + str(exc)]
        for number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                raw = json.loads(stripped)
                entry = _entry_from_payload(raw, line_number=number)
            except (ValueError, MemoryStoreError) as exc:
                invalid.append(f"{MEMORY_JSONL}:{number}: {exc}")
                continue
            if entry.id in seen_ids:
                invalid.append(f"{MEMORY_JSONL}:{number}: duplicate id {entry.id}")
                continue
            seen_ids.add(entry.id)
            entries.append(entry)
        return entries, invalid

    def _build_entry(
        self,
        *,
        record_type: str,
        title: str,
        summary: str,
        source: str,
        source_commit: str | None,
        tags: tuple[str, ...],
        status: str,
        superseded_by: str | None,
    ) -> tuple[MemoryEntry, str]:
        normalized_type = record_type.strip().casefold()
        if normalized_type not in MEMORY_TYPES:
            raise MemoryStoreError(
                "MEMORY_TYPE",
                f"unsupported memory type {record_type!r}; expected one of {list(MEMORY_TYPES)}",
            )
        normalized_status = status.strip().casefold()
        if normalized_status not in MEMORY_STATUSES:
            raise MemoryStoreError(
                "MEMORY_STATUS",
                f"unsupported memory status {status!r}; expected one of {list(MEMORY_STATUSES)}",
            )
        clean_title = title.strip()
        clean_summary = " ".join(summary.split())
        clean_source = source.strip()
        if not clean_title or len(clean_title) > 200:
            raise MemoryStoreError("MEMORY_TITLE", "memory title must be 1..200 characters")
        if not clean_summary or len(clean_summary) > 2000:
            raise MemoryStoreError(
                "MEMORY_SUMMARY", "memory summary must be 1..2000 characters"
            )
        if not clean_source or len(clean_source) > 1024:
            raise MemoryStoreError(
                "MEMORY_SOURCE", "memory source must be 1..1024 characters"
            )
        if "\n" in clean_title or "\n" in clean_source:
            raise MemoryStoreError("MEMORY_FIELD", "memory fields must be single-line")
        if source_commit is not None:
            source_commit = source_commit.strip() or None
            if source_commit is not None and _COMMIT_PATTERN.match(source_commit) is None:
                raise MemoryStoreError(
                    "MEMORY_COMMIT", "source_commit must be a 7..64 hex commit sha"
                )
        clean_tags = tuple(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))
        if len(clean_tags) > 12:
            raise MemoryStoreError("MEMORY_TAGS", "memory accepts at most 12 tags")
        if normalized_status == "superseded" and not (superseded_by or "").strip():
            raise MemoryStoreError(
                "MEMORY_SUPERSEDED", "superseded memory requires superseded_by"
            )
        blob = clean_title + " " + clean_summary
        if _SECRET_PATTERN.search(blob) or _SECRET_PATTERN.search(clean_source):
            raise MemoryStoreError(
                "MEMORY_SECRET", "memory content contains a suspected secret"
            )
        identity = "|".join(
            (normalized_type, clean_title.casefold(), clean_summary, clean_source)
        )
        entry_id = "MEM-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
        entry = MemoryEntry(
            id=entry_id,
            record_type=normalized_type,
            title=clean_title,
            summary=clean_summary,
            source=clean_source,
            source_commit=source_commit,
            tags=clean_tags,
            status=normalized_status,
            superseded_by=(superseded_by or "").strip() or None,
            line_number=None,
        )
        return entry, entry.to_json_line()


def _entry_payload(entry: MemoryEntry) -> dict[str, object]:
    return json.loads(entry.to_json_line())


def _entry_from_payload(raw: object, *, line_number: int | None) -> MemoryEntry:
    if not isinstance(raw, dict):
        raise ValueError("memory line must be a JSON object")
    record_type = str(raw.get("type", "")).strip().casefold()
    if record_type not in MEMORY_TYPES:
        raise ValueError("unsupported type: " + repr(record_type))
    status = str(raw.get("status", "active")).strip().casefold()
    if status not in MEMORY_STATUSES:
        raise ValueError("unsupported status: " + repr(status))
    entry_id = str(raw.get("id", "")).strip()
    if not entry_id or _ID_PATTERN.match(entry_id) is None:
        raise ValueError("id is required (1..64 word characters)")
    title = str(raw.get("title", "")).strip()
    summary = " ".join(str(raw.get("summary", "")).split())
    source = str(raw.get("source", "")).strip()
    if not title:
        raise ValueError("title is required")
    if not summary:
        raise ValueError("summary is required")
    if not source:
        raise ValueError("source is required")
    tags_raw = raw.get("tags", [])
    if not isinstance(tags_raw, list) or not all(isinstance(tag, str) for tag in tags_raw):
        raise ValueError("tags must be a string array")
    superseded_by = raw.get("superseded_by")
    if superseded_by is not None and not str(superseded_by).strip():
        superseded_by = None
    if status == "superseded" and not (superseded_by or "").strip():
        raise ValueError("superseded memory requires superseded_by")
    source_commit = raw.get("source_commit")
    if source_commit is not None and not str(source_commit).strip():
        source_commit = None
    return MemoryEntry(
        id=entry_id,
        record_type=record_type,
        title=title,
        summary=summary,
        source=source,
        source_commit=str(source_commit) if source_commit else None,
        tags=tuple(str(tag) for tag in tags_raw),
        status=status,
        superseded_by=str(superseded_by) if superseded_by else None,
        line_number=line_number,
    )


def _entry_from_row(row: sqlite3.Row) -> MemoryEntry:
    tags_raw = json.loads(str(row["tags"]))
    return MemoryEntry(
        id=str(row["id"]),
        record_type=str(row["record_type"]),
        title=str(row["title"]),
        summary=str(row["summary"]),
        source=str(row["source"]),
        source_commit=str(row["source_commit"]) if row["source_commit"] is not None else None,
        tags=tuple(str(tag) for tag in tags_raw),
        status=str(row["status"]),
        superseded_by=str(row["superseded_by"]) if row["superseded_by"] is not None else None,
        line_number=int(row["line_number"]),
    )


def _query_terms(query: str) -> list[str]:
    return [term for term in re.findall(r"[\w.-]+", query, re.UNICODE) if term][:12]


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            delete=False,
            dir=path.parent,
            prefix="." + path.name + ".",
            suffix=".tmp",
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "CANDIDATE_DIRECTORY",
    "MEMORY_JSONL",
    "MEMORY_STATUSES",
    "MEMORY_TYPES",
    "MemoryEntry",
    "MemoryStore",
    "MemoryStoreError",
    "ReindexResult",
]
