"""Project-isolated, source-verifiable memory lifecycle storage."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from codex_ai_os.domain.ids import new_id
from codex_ai_os.infrastructure.database import Database


class MemoryStoreError(RuntimeError):
    """Raised when memory provenance, scope, or redaction policy fails."""


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    id: str
    project_id: str
    run_id: str | None
    task_id: str | None
    record_type: str
    title: str
    content_ref: str
    source_refs: tuple[str, ...]
    source_hashes: dict[str, str]
    confidence: float
    tags: tuple[str, ...]
    scope: str
    status: str
    created_at: str
    updated_at: str
    expires_at: str | None


class MemoryStore:
    def __init__(self, database: Database, project_root: Path, project_id: str) -> None:
        self.database = database
        self.root = project_root.resolve()
        self.project_id = project_id

    def create_candidate(
        self,
        *,
        record_type: str,
        title: str,
        content_ref: str,
        source_refs: tuple[str, ...],
        confidence: float,
        tags: tuple[str, ...] = (),
        scope: str = "project",
        run_id: str | None = None,
        task_id: str | None = None,
        expires_at: str | None = None,
    ) -> MemoryRecord:
        if record_type not in {"decision", "project", "bug", "experience"}:
            raise MemoryStoreError(f"unsupported memory record type: {record_type}")
        if scope != "project":
            raise MemoryStoreError("V1 memory scope must be project")
        if not 0 <= confidence <= 1:
            raise MemoryStoreError("memory confidence must be within 0..1")
        content_path = self._safe_file(content_ref)
        content = content_path.read_text(encoding="utf-8")
        if _contains_secret(content):
            raise MemoryStoreError("memory content contains a suspected secret")
        if not source_refs:
            raise MemoryStoreError("memory requires at least one source reference")
        source_hashes = {source: _sha256(self._safe_file(source)) for source in source_refs}
        now = _utc_now()
        record = MemoryRecord(
            id=new_id("MEMORY"),
            project_id=self.project_id,
            run_id=run_id,
            task_id=task_id,
            record_type=record_type,
            title=title.strip(),
            content_ref=content_ref,
            source_refs=source_refs,
            source_hashes=source_hashes,
            confidence=confidence,
            tags=tags,
            scope=scope,
            status="candidate",
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
        )
        if not record.title:
            raise MemoryStoreError("memory title is required")
        with self.database.connection() as connection:
            connection.execute(
                """
                INSERT INTO memory_records(
                    id, project_id, run_id, task_id, record_type, title, content_ref,
                    source_refs_json, source_hashes_json, confidence, tags_json,
                    scope, status, created_at, updated_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate', ?, ?, ?)
                """,
                (
                    record.id,
                    record.project_id,
                    record.run_id,
                    record.task_id,
                    record.record_type,
                    record.title,
                    record.content_ref,
                    _json(record.source_refs),
                    _json(record.source_hashes),
                    record.confidence,
                    _json(record.tags),
                    record.scope,
                    record.created_at,
                    record.updated_at,
                    record.expires_at,
                ),
            )
            _event(connection, record, "memory.candidate", {})
            connection.commit()
        return record

    def activate(self, memory_id: str) -> MemoryRecord:
        record = self.get(memory_id)
        if record.status == "active":
            return record
        if record.status != "candidate":
            raise MemoryStoreError(f"memory cannot activate from {record.status}")
        if record.confidence < 0.7:
            raise MemoryStoreError("memory confidence is below activation threshold")
        self._verify_sources(record)
        now = _utc_now()
        with self.database.connection() as connection:
            connection.execute(
                "UPDATE memory_records SET status = 'active', updated_at = ? "
                "WHERE id = ? AND status = 'candidate'",
                (now, memory_id),
            )
            _event(connection, record, "memory.activated", {})
            connection.commit()
        return self.get(memory_id)

    def invalidate_changed_sources(self) -> tuple[str, ...]:
        invalidated: list[str] = []
        for record in self.search("", limit=1000):
            try:
                self._verify_sources(record)
            except MemoryStoreError as exc:
                now = _utc_now()
                with self.database.connection() as connection:
                    connection.execute(
                        "UPDATE memory_records SET status = 'invalidated', updated_at = ? "
                        "WHERE id = ? AND status = 'active'",
                        (now, record.id),
                    )
                    _event(
                        connection,
                        record,
                        "memory.invalidated",
                        {"reason": str(exc)},
                    )
                    connection.commit()
                invalidated.append(record.id)
        return tuple(invalidated)

    def search(self, query: str, *, limit: int = 20) -> list[MemoryRecord]:
        bounded = max(1, min(limit, 1000))
        pattern = f"%{query.strip()}%"
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM memory_records
                WHERE project_id = ? AND status = 'active'
                  AND (title LIKE ? OR tags_json LIKE ? OR content_ref LIKE ?)
                ORDER BY updated_at DESC, id DESC LIMIT ?
                """,
                (self.project_id, pattern, pattern, pattern, bounded),
            ).fetchall()
        return [_from_row(row) for row in rows]

    def get(self, memory_id: str) -> MemoryRecord:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM memory_records WHERE id = ? AND project_id = ?",
                (memory_id, self.project_id),
            ).fetchone()
        if row is None:
            raise MemoryStoreError(f"memory record not found: {memory_id}")
        return _from_row(row)

    def _verify_sources(self, record: MemoryRecord) -> None:
        for source, expected in record.source_hashes.items():
            actual = _sha256(self._safe_file(source))
            if actual != expected:
                raise MemoryStoreError(f"memory source hash changed: {source}")

    def _safe_file(self, relative: str) -> Path:
        path = Path(relative.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise MemoryStoreError(f"memory path is unsafe: {relative}")
        candidate = self.root.joinpath(*path.parts)
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise MemoryStoreError(f"memory path is missing: {relative}") from exc
        if (
            not resolved.is_relative_to(self.root)
            or candidate.is_symlink()
            or not candidate.is_file()
        ):
            raise MemoryStoreError(f"memory path escapes project: {relative}")
        return candidate


def _event(
    connection: sqlite3.Connection,
    record: MemoryRecord,
    event_type: str,
    details: dict[str, str],
) -> None:
    connection.execute(
        """
        INSERT INTO events(
            event_id, project_id, run_id, task_id, event_type,
            idempotency_key, payload_json, approval_required, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
        """,
        (
            new_id("EVENT"),
            record.project_id,
            record.run_id,
            record.task_id,
            event_type,
            f"{record.id}:{event_type}",
            _json({"memory_id": record.id, "source_hashes": record.source_hashes, **details}),
            _utc_now(),
        ),
    )


def _from_row(row: sqlite3.Row) -> MemoryRecord:
    source_refs = _string_tuple(str(row["source_refs_json"]), "memory source_refs")
    source_hashes = _string_dict(str(row["source_hashes_json"]), "memory source_hashes")
    tags = _string_tuple(str(row["tags_json"]), "memory tags")
    return MemoryRecord(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        run_id=str(row["run_id"]) if row["run_id"] is not None else None,
        task_id=str(row["task_id"]) if row["task_id"] is not None else None,
        record_type=str(row["record_type"]),
        title=str(row["title"]),
        content_ref=str(row["content_ref"]),
        source_refs=source_refs,
        source_hashes=source_hashes,
        confidence=float(row["confidence"]),
        tags=tags,
        scope=str(row["scope"]),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        expires_at=str(row["expires_at"]) if row["expires_at"] is not None else None,
    )


def _string_tuple(raw: str, label: str) -> tuple[str, ...]:
    value: object = json.loads(raw)
    if not isinstance(value, list):
        raise MemoryStoreError(f"{label} must be a JSON string array")
    object_value = cast(list[object], value)
    if not all(isinstance(item, str) for item in object_value):
        raise MemoryStoreError(f"{label} must be a JSON string array")
    return tuple(cast(list[str], object_value))


def _string_dict(raw: str, label: str) -> dict[str, str]:
    value: object = json.loads(raw)
    if not isinstance(value, dict):
        raise MemoryStoreError(f"{label} must be a JSON string object")
    object_value = cast(dict[object, object], value)
    if not all(
        isinstance(key, str) and isinstance(item, str) for key, item in object_value.items()
    ):
        raise MemoryStoreError(f"{label} keys and values must be strings")
    return cast(dict[str, str], object_value)


def _contains_secret(content: str) -> bool:
    return bool(
        re.search(
            r"(?i)(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
            r"(?:password|token|secret|api[_-]?key)\s*[=:]\s*\S+)",
            content,
        )
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
