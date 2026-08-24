"""Transactional execution records and append-only audit events."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import cast

from codex_ai_os.adapters.docker import SandboxRequest, SandboxResult, command_hash
from codex_ai_os.domain.ids import new_id
from codex_ai_os.infrastructure.database import Database


class ExecutionStoreError(RuntimeError):
    """Raised when execution evidence conflicts with persisted runtime state."""


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    id: str
    run_id: str | None
    task_id: str
    worktree_id: str | None
    status: str
    risk_level: str
    command_hash: str
    image_digest: str | None
    container_id: str | None
    exit_code: int | None
    stdout_ref: str | None
    stderr_ref: str | None
    error_code: str | None
    network_mode: str
    mounts: tuple[dict[str, object], ...]
    started_at: str
    ended_at: str | None


@dataclass(frozen=True, slots=True)
class ExecutionReservation:
    record: ExecutionRecord
    created: bool


class ExecutionStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def get(self, execution_id: str) -> ExecutionRecord | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM executions WHERE id = ?", (execution_id,)
            ).fetchone()
        return _record_from_row(row) if row is not None else None

    def start(self, request: SandboxRequest, *, started_at: str) -> ExecutionReservation:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", request.execution_id):
            raise ExecutionStoreError("execution_id is unsafe")
        digest = request.image.rsplit("@", 1)[1] if "@" in request.image else None
        mounts: tuple[dict[str, object], ...] = tuple(
            {
                "kind": mount.kind.value,
                "source": mount.source.as_posix(),
                "read_only": mount.read_only,
            }
            for mount in request.mounts
        )
        record = ExecutionRecord(
            id=request.execution_id,
            run_id=request.worktree.workflow_id,
            task_id=request.task_id,
            worktree_id=None,
            status="running",
            risk_level=request.risk_level.value,
            command_hash=command_hash(request.command),
            image_digest=digest,
            container_id=f"codex-os-{request.execution_id.lower()}",
            exit_code=None,
            stdout_ref=None,
            stderr_ref=None,
            error_code=None,
            network_mode="disabled",
            mounts=mounts,
            started_at=started_at,
            ended_at=None,
        )
        with self.database.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                identity = _task_identity(connection, request.task_id)
                if identity["run_id"] != request.worktree.workflow_id:
                    raise ExecutionStoreError("execution task does not match workflow")
                existing_row = connection.execute(
                    "SELECT * FROM executions WHERE id = ?", (request.execution_id,)
                ).fetchone()
                if existing_row is not None:
                    existing = _record_from_row(existing_row)
                    if _same_request(existing, record):
                        connection.rollback()
                        return ExecutionReservation(existing, created=False)
                    raise ExecutionStoreError(
                        f"execution_id already refers to different evidence: {request.execution_id}"
                    )
                worktree_row = connection.execute(
                    "SELECT id FROM worktrees WHERE task_id = ? AND status = 'active'",
                    (request.task_id,),
                ).fetchone()
                if worktree_row is None:
                    raise ExecutionStoreError("execution task has no active Worktree")
                connection.execute(
                    """
                    INSERT INTO executions(
                        id, task_id, risk_level, command_hash, image_digest,
                        container_id, exit_code, stdout_ref, stderr_ref,
                        started_at, ended_at, status, error_code, network_mode,
                        mounts_json, run_id, worktree_id, dirty_before, dirty_after
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, NULL,
                              'running', NULL, 'disabled', ?, ?, ?, 0, 0)
                    """,
                    (
                        record.id,
                        record.task_id,
                        record.risk_level,
                        record.command_hash,
                        record.image_digest,
                        record.container_id,
                        record.started_at,
                        _json(mounts),
                        str(identity["run_id"]),
                        str(worktree_row["id"]),
                    ),
                )
                _insert_event(
                    connection,
                    identity,
                    record,
                    "execution.started",
                    {"status": "running"},
                )
                connection.commit()
            except (sqlite3.Error, ExecutionStoreError):
                connection.rollback()
                raise
        return ExecutionReservation(record, created=True)

    def finish(
        self,
        result: SandboxResult,
        *,
        stdout_ref: str,
        stderr_ref: str,
    ) -> ExecutionRecord:
        current = self.get(result.execution_id)
        if current is None:
            raise ExecutionStoreError(
                f"execution record not found: {result.execution_id}"
            )
        if (
            current.command_hash != result.command_hash
            or current.image_digest != result.image_digest
            or current.container_id != result.container_name
        ):
            raise ExecutionStoreError(
                f"sandbox result does not match reserved execution: {result.execution_id}"
            )
        status = "completed" if result.exit_code == 0 else "failed"
        error_code = None if result.exit_code == 0 else "EXECUTION_FAILED"
        return self._finish(
            execution_id=result.execution_id,
            status=status,
            exit_code=result.exit_code,
            stdout_ref=stdout_ref,
            stderr_ref=stderr_ref,
            error_code=error_code,
            ended_at=result.ended_at,
            worktree_dirty=result.worktree_dirty,
        )

    def fail(
        self,
        execution_id: str,
        *,
        error_code: str,
        stderr_ref: str,
        ended_at: str,
        worktree_dirty: bool,
    ) -> ExecutionRecord:
        return self._finish(
            execution_id=execution_id,
            status="failed",
            exit_code=None,
            stdout_ref=None,
            stderr_ref=stderr_ref,
            error_code=error_code,
            ended_at=ended_at,
            worktree_dirty=worktree_dirty,
        )

    def _finish(
        self,
        *,
        execution_id: str,
        status: str,
        exit_code: int | None,
        stdout_ref: str | None,
        stderr_ref: str | None,
        error_code: str | None,
        ended_at: str,
        worktree_dirty: bool,
    ) -> ExecutionRecord:
        with self.database.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM executions WHERE id = ?", (execution_id,)
                ).fetchone()
                if row is None:
                    raise ExecutionStoreError(f"execution record not found: {execution_id}")
                current = _record_from_row(row)
                if current.status != "running":
                    connection.rollback()
                    return current
                identity = _task_identity(connection, current.task_id)
                connection.execute(
                    """
                    UPDATE executions
                    SET status = ?, exit_code = ?, stdout_ref = ?, stderr_ref = ?,
                        error_code = ?, ended_at = ?, dirty_after = ?
                    WHERE id = ? AND status = 'running'
                    """,
                    (
                        status,
                        exit_code,
                        stdout_ref,
                        stderr_ref,
                        error_code,
                        ended_at,
                        int(worktree_dirty),
                        execution_id,
                    ),
                )
                if worktree_dirty:
                    changed = connection.execute(
                        "UPDATE worktrees SET dirty = 1 "
                        "WHERE task_id = ? AND status = 'active' AND dirty = 0",
                        (current.task_id,),
                    ).rowcount
                    if changed:
                        _insert_event(
                            connection,
                            identity,
                            current,
                            "worktree.dirty",
                            {"execution_id": current.id},
                            created_at=ended_at,
                        )
                _insert_event(
                    connection,
                    identity,
                    current,
                    f"execution.{status}",
                    {
                        "status": status,
                        "exit_code": exit_code,
                        "error_code": error_code,
                        "stdout_ref": stdout_ref,
                        "stderr_ref": stderr_ref,
                    },
                    created_at=ended_at,
                )
                connection.commit()
            except (sqlite3.Error, ExecutionStoreError):
                connection.rollback()
                raise
        updated = self.get(execution_id)
        if updated is None:
            raise ExecutionStoreError(f"execution record disappeared: {execution_id}")
        return updated


def _task_identity(connection: sqlite3.Connection, task_id: str) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT t.run_id, w.project_id
        FROM tasks AS t
        JOIN workflow_runs AS w ON w.id = t.run_id
        WHERE t.id = ?
        """,
        (task_id,),
    ).fetchone()
    if row is None:
        raise ExecutionStoreError(f"execution task not found: {task_id}")
    return row


def _insert_event(
    connection: sqlite3.Connection,
    identity: sqlite3.Row,
    record: ExecutionRecord,
    event_type: str,
    details: dict[str, object],
    *,
    created_at: str | None = None,
) -> None:
    payload = {
        "execution_id": record.id,
        "risk_level": record.risk_level,
        "command_hash": record.command_hash,
        "image_digest": record.image_digest,
        "container_id": record.container_id,
        "network_mode": record.network_mode,
        "mounts": record.mounts,
        **details,
    }
    connection.execute(
        """
        INSERT INTO events(
            event_id, project_id, run_id, task_id, event_type,
            idempotency_key, payload_json, approval_required, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
        """,
        (
            new_id("EVENT"),
            str(identity["project_id"]),
            str(identity["run_id"]),
            record.task_id,
            event_type,
            f"{record.id}:{event_type}",
            _json(payload),
            created_at or record.started_at,
        ),
    )


def _record_from_row(row: sqlite3.Row) -> ExecutionRecord:
    mounts_raw: object = json.loads(str(row["mounts_json"]))
    if not isinstance(mounts_raw, list):
        raise ExecutionStoreError("execution mounts_json is invalid")
    mounts_list: list[dict[str, object]] = []
    for raw_mount in cast(list[object], mounts_raw):
        if not isinstance(raw_mount, dict):
            raise ExecutionStoreError("execution mounts_json is invalid")
        object_mount = cast(dict[object, object], raw_mount)
        if not all(isinstance(key, str) for key in object_mount):
            raise ExecutionStoreError("execution mount keys must be strings")
        mounts_list.append({str(key): value for key, value in object_mount.items()})
    mounts = tuple(mounts_list)
    return ExecutionRecord(
        id=str(row["id"]),
        run_id=str(row["run_id"]) if row["run_id"] is not None else None,
        task_id=str(row["task_id"]),
        worktree_id=str(row["worktree_id"]) if row["worktree_id"] is not None else None,
        status=str(row["status"]),
        risk_level=str(row["risk_level"]),
        command_hash=str(row["command_hash"]),
        image_digest=(str(row["image_digest"]) if row["image_digest"] is not None else None),
        container_id=(str(row["container_id"]) if row["container_id"] is not None else None),
        exit_code=int(row["exit_code"]) if row["exit_code"] is not None else None,
        stdout_ref=str(row["stdout_ref"]) if row["stdout_ref"] is not None else None,
        stderr_ref=str(row["stderr_ref"]) if row["stderr_ref"] is not None else None,
        error_code=str(row["error_code"]) if row["error_code"] is not None else None,
        network_mode=str(row["network_mode"]),
        mounts=mounts,
        started_at=str(row["started_at"]),
        ended_at=str(row["ended_at"]) if row["ended_at"] is not None else None,
    )


def _same_request(left: ExecutionRecord, right: ExecutionRecord) -> bool:
    return (
        left.run_id,
        left.task_id,
        left.risk_level,
        left.command_hash,
        left.image_digest,
        left.network_mode,
        left.mounts,
    ) == (
        right.run_id,
        right.task_id,
        right.risk_level,
        right.command_hash,
        right.image_digest,
        right.network_mode,
        right.mounts,
    )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
