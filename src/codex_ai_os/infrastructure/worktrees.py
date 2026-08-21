"""Transactional persistence for task worktree assignments and audit events."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from codex_ai_os.adapters.worktree import WorktreeSpec
from codex_ai_os.domain.ids import new_id
from codex_ai_os.infrastructure.database import Database


class WorktreeStoreError(RuntimeError):
    """Raised when a worktree assignment conflicts with runtime state."""


@dataclass(frozen=True, slots=True)
class WorktreeRecord:
    id: str
    project_id: str
    run_id: str
    task_id: str
    agent: str
    path: Path
    branch: str
    base_commit: str
    status: str
    dirty: bool
    created_at: str
    cleaned_at: str | None

    def to_spec(self) -> WorktreeSpec:
        return WorktreeSpec(
            workflow_id=self.run_id,
            agent=self.agent,
            task_id=self.task_id,
            branch=self.branch,
            path=self.path,
            base_commit=self.base_commit,
        )


@dataclass(frozen=True, slots=True)
class WorktreeReservation:
    record: WorktreeRecord
    created: bool


class WorktreeStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def get_for_task(self, task_id: str) -> WorktreeRecord | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM worktrees WHERE task_id = ?", (task_id,)
            ).fetchone()
        return _record_from_row(row) if row is not None else None

    def reserve(
        self,
        *,
        project_id: str,
        run_id: str,
        task_id: str,
        agent: str,
        spec: WorktreeSpec,
    ) -> WorktreeReservation:
        now = _utc_now()
        record = WorktreeRecord(
            id=new_id("WORKTREE"),
            project_id=project_id,
            run_id=run_id,
            task_id=task_id,
            agent=agent,
            path=spec.path,
            branch=spec.branch,
            base_commit=spec.base_commit,
            status="provisioning",
            dirty=False,
            created_at=now,
            cleaned_at=None,
        )
        with self.database.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing_row = connection.execute(
                    "SELECT * FROM worktrees WHERE task_id = ?", (task_id,)
                ).fetchone()
                if existing_row is not None:
                    existing = _record_from_row(existing_row)
                    if _same_assignment(existing, record):
                        connection.rollback()
                        return WorktreeReservation(record=existing, created=False)
                    raise WorktreeStoreError(
                        f"task already has a different worktree assignment: {task_id}"
                    )

                changed = connection.execute(
                    """
                    UPDATE tasks
                    SET branch = ?, worktree = ?, state_version = state_version + 1,
                        updated_at = ?
                    WHERE id = ? AND run_id = ? AND agent = ?
                      AND status IN ('pending', 'running')
                    """,
                    (spec.branch, spec.path.as_posix(), now, task_id, run_id, agent),
                ).rowcount
                if changed != 1:
                    raise WorktreeStoreError(
                        f"task is not active or does not match assignment: {task_id}"
                    )
                connection.execute(
                    """
                    INSERT INTO worktrees(
                        id, project_id, run_id, task_id, agent, path, branch,
                        base_commit, status, dirty, created_at, cleaned_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'provisioning', 0, ?, NULL)
                    """,
                    (
                        record.id,
                        project_id,
                        run_id,
                        task_id,
                        agent,
                        spec.path.as_posix(),
                        spec.branch,
                        spec.base_commit,
                        now,
                    ),
                )
                _insert_event(
                    connection,
                    record,
                    "worktree.reserved",
                    {"status": "provisioning"},
                )
                connection.commit()
            except (sqlite3.Error, WorktreeStoreError):
                connection.rollback()
                raise
        return WorktreeReservation(record=record, created=True)

    def activate(self, worktree_id: str, *, head_commit: str) -> WorktreeRecord:
        return self._transition(
            worktree_id,
            target_status="active",
            event_type="worktree.created",
            payload={"head_commit": head_commit},
            allowed_statuses=("provisioning", "active"),
        )

    def block(self, worktree_id: str, *, reason: str) -> WorktreeRecord:
        return self._transition(
            worktree_id,
            target_status="blocked",
            event_type="worktree.blocked",
            payload={"reason": reason},
            allowed_statuses=("provisioning", "blocked"),
        )

    def mark_cleaned(self, worktree_id: str, *, merged_into: str) -> WorktreeRecord:
        return self._transition(
            worktree_id,
            target_status="cleaned",
            event_type="worktree.cleaned",
            payload={"merged_into": merged_into},
            allowed_statuses=("active", "cleaned"),
            cleaned=True,
        )

    def _transition(
        self,
        worktree_id: str,
        *,
        target_status: str,
        event_type: str,
        payload: dict[str, str],
        allowed_statuses: tuple[str, ...],
        cleaned: bool = False,
    ) -> WorktreeRecord:
        now = _utc_now()
        with self.database.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM worktrees WHERE id = ?", (worktree_id,)
                ).fetchone()
                if row is None:
                    raise WorktreeStoreError(f"worktree record not found: {worktree_id}")
                record = _record_from_row(row)
                if record.status == target_status:
                    connection.rollback()
                    return record
                if record.status not in allowed_statuses:
                    raise WorktreeStoreError(
                        f"worktree cannot transition from {record.status} to {target_status}"
                    )
                connection.execute(
                    """
                    UPDATE worktrees
                    SET status = ?, dirty = 0, cleaned_at = ?
                    WHERE id = ?
                    """,
                    (target_status, now if cleaned else None, worktree_id),
                )
                _insert_event(connection, record, event_type, payload)
                connection.commit()
            except (sqlite3.Error, WorktreeStoreError):
                connection.rollback()
                raise
        updated = self.get_for_task(record.task_id)
        if updated is None:
            raise WorktreeStoreError(f"worktree record disappeared: {worktree_id}")
        return updated


def _same_assignment(left: WorktreeRecord, right: WorktreeRecord) -> bool:
    return (
        left.project_id,
        left.run_id,
        left.task_id,
        left.agent,
        left.path,
        left.branch,
        left.base_commit,
    ) == (
        right.project_id,
        right.run_id,
        right.task_id,
        right.agent,
        right.path,
        right.branch,
        right.base_commit,
    )


def _insert_event(
    connection: sqlite3.Connection,
    record: WorktreeRecord,
    event_type: str,
    details: dict[str, str],
) -> None:
    payload = {
        "worktree_id": record.id,
        "workflow_id": record.run_id,
        "task_id": record.task_id,
        "agent": record.agent,
        "path": record.path.as_posix(),
        "branch": record.branch,
        "base_commit": record.base_commit,
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
            record.project_id,
            record.run_id,
            record.task_id,
            event_type,
            f"{record.id}:{event_type}",
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            _utc_now(),
        ),
    )


def _record_from_row(row: sqlite3.Row) -> WorktreeRecord:
    return WorktreeRecord(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        run_id=str(row["run_id"]),
        task_id=str(row["task_id"]),
        agent=str(row["agent"]),
        path=Path(str(row["path"])),
        branch=str(row["branch"]),
        base_commit=str(row["base_commit"]),
        status=str(row["status"]),
        dirty=bool(row["dirty"]),
        created_at=str(row["created_at"]),
        cleaned_at=str(row["cleaned_at"]) if row["cleaned_at"] is not None else None,
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
