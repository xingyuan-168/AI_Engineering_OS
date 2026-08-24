"""Project-isolated, source-verifiable Memory lifecycle and FTS5 storage."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from codex_ai_os.domain.governance import MemoryStatus
from codex_ai_os.domain.ids import new_id
from codex_ai_os.infrastructure.database import Database


class MemoryStoreError(RuntimeError):
    """Raised when Memory provenance, lifecycle, scope, or redaction policy fails."""


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
        allowed_types = {"decision", "project", "bug", "experience", "release", "failure"}
        if record_type not in allowed_types:
            raise MemoryStoreError(f"unsupported Memory record type: {record_type}")
        if scope != "project":
            raise MemoryStoreError("Memory scope must be project")
        if not 0 <= confidence <= 1:
            raise MemoryStoreError("Memory confidence must be within 0..1")
        normalized_title = title.strip()
        if not normalized_title:
            raise MemoryStoreError("Memory title is required")
        content_path = self._safe_file(content_ref)
        content = content_path.read_text(encoding="utf-8")
        if _contains_secret(content) or _contains_secret(normalized_title):
            raise MemoryStoreError("Memory content contains a suspected secret")
        if not source_refs:
            raise MemoryStoreError("Memory requires at least one source reference")
        source_hashes = {source: _sha256(self._safe_file(source)) for source in source_refs}
        now = _utc_now()
        record = MemoryRecord(
            id=new_id("MEMORY"),
            project_id=self.project_id,
            run_id=run_id,
            task_id=task_id,
            record_type=record_type,
            title=normalized_title,
            content_ref=content_ref,
            source_refs=source_refs,
            source_hashes=source_hashes,
            confidence=confidence,
            tags=tuple(dict.fromkeys(tags)),
            scope=scope,
            status=MemoryStatus.PENDING.value,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
        )
        with self.database.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO memory_records(
                        id, project_id, run_id, task_id, record_type, title, content_ref,
                        source_refs_json, source_hashes_json, confidence, tags_json,
                        scope, status, created_at, updated_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
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
                connection.execute(
                    """
                    INSERT INTO memory_search_documents(
                        memory_id, project_id, title, content, tags, source_ref,
                        status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (
                        record.id,
                        record.project_id,
                        record.title,
                        content,
                        " ".join(record.tags),
                        record.content_ref,
                        now,
                        now,
                    ),
                )
                _event(connection, record, "memory.pending", {})
                connection.commit()
            except sqlite3.Error:
                connection.rollback()
                raise
        return record

    def activate(self, memory_id: str, *, reviewer: str = "memory-manager") -> MemoryRecord:
        return self.review(
            memory_id,
            reviewer=reviewer,
            decision="activate",
            reason="source, Secret, scope, and confidence checks passed",
        )

    def review(
        self,
        memory_id: str,
        *,
        reviewer: str,
        decision: str,
        reason: str,
    ) -> MemoryRecord:
        decisions = {"activate", "needs_review", "revoke", "expire", "delete"}
        if decision not in decisions:
            raise MemoryStoreError(f"unsupported Memory review decision: {decision}")
        if not reviewer.strip() or not reason.strip():
            raise MemoryStoreError("Memory review requires reviewer and reason")
        record = self.get(memory_id)
        if decision == "activate" and record.status == MemoryStatus.ACTIVE.value:
            return record
        secret_ok = False
        scope_ok = record.scope == "project" and record.project_id == self.project_id
        confidence_ok = record.confidence >= 0.7
        try:
            self._verify_sources(record)
            content = self._safe_file(record.content_ref).read_text(encoding="utf-8")
            secret_ok = not _contains_secret(content) and not _contains_secret(record.title)
        except MemoryStoreError:
            secret_ok = False
        if decision == "activate" and not (secret_ok and scope_ok and confidence_ok):
            raise MemoryStoreError(
                "Memory activation requires matching sources, Secret/scope checks, "
                "and confidence >= 0.7"
            )
        target = {
            "activate": MemoryStatus.ACTIVE.value,
            "needs_review": MemoryStatus.NEEDS_REVIEW.value,
            "revoke": MemoryStatus.REVOKED.value,
            "expire": MemoryStatus.EXPIRED.value,
            "delete": MemoryStatus.DELETED.value,
        }[decision]
        now = _utc_now()
        review_id = new_id("MEMORYREVIEW")
        with self.database.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO memory_reviews(
                        id, memory_id, reviewer, decision, reason, source_hashes_json,
                        secret_check_passed, scope_check_passed,
                        confidence_check_passed, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        review_id,
                        memory_id,
                        reviewer.strip(),
                        decision,
                        reason.strip(),
                        _json(record.source_hashes),
                        int(secret_ok),
                        int(scope_ok),
                        int(confidence_ok),
                        now,
                    ),
                )
                changed = connection.execute(
                    "UPDATE memory_records SET status = ?, updated_at = ? "
                    "WHERE id = ? AND project_id = ?",
                    (target, now, memory_id, self.project_id),
                ).rowcount
                if changed != 1:
                    raise MemoryStoreError(f"Memory record not found: {memory_id}")
                connection.execute(
                    "UPDATE memory_search_documents SET status = ?, updated_at = ? "
                    "WHERE memory_id = ? AND project_id = ?",
                    (target, now, memory_id, self.project_id),
                )
                _event(
                    connection,
                    record,
                    f"memory.{target}",
                    {"review_id": review_id, "reviewer": reviewer, "reason": reason},
                    suffix=review_id,
                )
                connection.commit()
            except (sqlite3.Error, MemoryStoreError):
                connection.rollback()
                raise
        return self.get(memory_id)

    def supersede(
        self,
        old_memory_id: str,
        new_memory_id: str,
        *,
        reviewer: str,
        reason: str,
    ) -> MemoryRecord:
        old = self.get(old_memory_id)
        new = self.get(new_memory_id)
        if new.status != MemoryStatus.ACTIVE.value:
            raise MemoryStoreError("superseding Memory must be active")
        if old.status not in {MemoryStatus.ACTIVE.value, MemoryStatus.NEEDS_REVIEW.value}:
            raise MemoryStoreError(f"Memory cannot be superseded from {old.status}")
        now = _utc_now()
        with self.database.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO memory_links(
                        source_memory_id, target_memory_id, relation, created_at
                    ) VALUES (?, ?, 'supersedes', ?)
                    """,
                    (new_memory_id, old_memory_id, now),
                )
                connection.execute(
                    "UPDATE memory_records SET status = 'superseded', updated_at = ? "
                    "WHERE id = ? AND project_id = ?",
                    (now, old_memory_id, self.project_id),
                )
                connection.execute(
                    "UPDATE memory_search_documents SET status = 'superseded', updated_at = ? "
                    "WHERE memory_id = ? AND project_id = ?",
                    (now, old_memory_id, self.project_id),
                )
                _event(
                    connection,
                    old,
                    "memory.superseded",
                    {
                        "superseded_by": new_memory_id,
                        "reviewer": reviewer,
                        "reason": reason,
                    },
                    suffix=new_memory_id,
                )
                connection.commit()
            except sqlite3.Error:
                connection.rollback()
                raise
        return self.get(old_memory_id)

    def invalidate_changed_sources(self) -> tuple[str, ...]:
        invalidated: list[str] = []
        active = self.search("", statuses=(MemoryStatus.ACTIVE.value,), limit=100)
        for record in active:
            try:
                self._verify_sources(record)
            except MemoryStoreError as exc:
                self.review(
                    record.id,
                    reviewer="memory-source-monitor",
                    decision="needs_review",
                    reason=str(exc),
                )
                invalidated.append(record.id)
        return tuple(invalidated)

    def search(
        self,
        query: str,
        *,
        record_types: tuple[str, ...] = (),
        statuses: tuple[str, ...] = ("active",),
        tags: tuple[str, ...] = (),
        created_from: str | None = None,
        created_to: str | None = None,
        source_ref: str | None = None,
        limit: int = 20,
    ) -> list[MemoryRecord]:
        if limit < 1 or limit > 100:
            raise MemoryStoreError("Memory search limit must be within 1..100")
        allowed_statuses = {status.value for status in MemoryStatus}
        if not statuses or set(statuses) - allowed_statuses or "deleted" in statuses:
            raise MemoryStoreError("Memory search statuses are invalid")
        clauses = ["m.project_id = ?"]
        parameters: list[object] = [self.project_id]
        clauses.append(f"m.status IN ({','.join('?' for _ in statuses)})")
        parameters.extend(statuses)
        if record_types:
            clauses.append(f"m.record_type IN ({','.join('?' for _ in record_types)})")
            parameters.extend(record_types)
        if created_from is not None:
            clauses.append("m.created_at >= ?")
            parameters.append(created_from)
        if created_to is not None:
            clauses.append("m.created_at <= ?")
            parameters.append(created_to)
        if source_ref is not None:
            clauses.append("d.source_ref = ?")
            parameters.append(source_ref)
        for tag in tags:
            clauses.append("m.tags_json LIKE ?")
            parameters.append(f'%"{tag}"%')
        normalized_query = query.strip()
        if normalized_query:
            match = _fts_query(normalized_query)
            if not match:
                return []
            sql = (
                "SELECT m.* FROM memory_fts "
                "JOIN memory_search_documents d ON d.rowid = memory_fts.rowid "
                "JOIN memory_records m ON m.id = d.memory_id "
                f"WHERE memory_fts MATCH ? AND {' AND '.join(clauses)} "
                "ORDER BY bm25(memory_fts), m.updated_at DESC, m.id DESC LIMIT ?"
            )
            parameters = [match, *parameters, limit]
        else:
            sql = (
                "SELECT m.* FROM memory_records m "
                "JOIN memory_search_documents d ON d.memory_id = m.id "
                f"WHERE {' AND '.join(clauses)} "
                "ORDER BY m.updated_at DESC, m.id DESC LIMIT ?"
            )
            parameters.append(limit)
        with self.database.connection() as connection:
            rows = connection.execute(sql, tuple(parameters)).fetchall()
        return [_from_row(row) for row in rows]

    def get(self, memory_id: str) -> MemoryRecord:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM memory_records WHERE id = ? AND project_id = ?",
                (memory_id, self.project_id),
            ).fetchone()
        if row is None:
            raise MemoryStoreError(f"Memory record not found: {memory_id}")
        return _from_row(row)

    def _verify_sources(self, record: MemoryRecord) -> None:
        for source, expected in record.source_hashes.items():
            actual = _sha256(self._safe_file(source))
            if actual != expected:
                raise MemoryStoreError(f"Memory source hash changed: {source}")

    def _safe_file(self, relative: str) -> Path:
        path = Path(relative.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise MemoryStoreError(f"Memory path is unsafe: {relative}")
        candidate = self.root.joinpath(*path.parts)
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise MemoryStoreError(f"Memory path is missing: {relative}") from exc
        if (
            not resolved.is_relative_to(self.root)
            or _is_link_like(candidate)
            or not candidate.is_file()
        ):
            raise MemoryStoreError(f"Memory path escapes project: {relative}")
        return candidate


def _event(
    connection: sqlite3.Connection,
    record: MemoryRecord,
    event_type: str,
    details: dict[str, str],
    *,
    suffix: str = "",
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
            f"{record.id}:{event_type}:{suffix}",
            _json({"memory_id": record.id, "source_hashes": record.source_hashes, **details}),
            _utc_now(),
        ),
    )


def _from_row(row: sqlite3.Row) -> MemoryRecord:
    source_refs = _string_tuple(str(row["source_refs_json"]), "Memory source_refs")
    source_hashes = _string_dict(str(row["source_hashes_json"]), "Memory source_hashes")
    tags = _string_tuple(str(row["tags_json"]), "Memory tags")
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


def _fts_query(value: str) -> str:
    tokens = re.findall(r"[\w.-]+", value, re.UNICODE)
    return " AND ".join(f'"{token}"*' for token in tokens[:20])


def _is_link_like(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction is not None and is_junction())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
