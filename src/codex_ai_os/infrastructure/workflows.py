"""Transactional persistence for workflow runs, tasks, approvals, and artifacts."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any, cast

from codex_ai_os.domain.config import RiskLevel
from codex_ai_os.domain.ids import new_id
from codex_ai_os.domain.workflow import (
    Gate,
    RunStatus,
    TaskCompletion,
    TaskRecord,
    TaskStatus,
    WorkflowPhase,
    WorkflowRun,
)
from codex_ai_os.infrastructure.database import Database


class WorkflowNotFoundError(LookupError):
    """Raised when a workflow run or task does not exist."""


class WorkflowConflictError(RuntimeError):
    """Raised when optimistic concurrency or idempotency checks fail."""


class WorkflowStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def project_config_hash(self, project_id: str) -> str:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT config_hash FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        if row is None:
            raise WorkflowNotFoundError(f"project not registered: {project_id}")
        return str(row["config_hash"])

    def find_active_run(
        self, project_id: str, workflow_name: str, goal: str
    ) -> WorkflowRun | None:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM workflow_runs
                WHERE project_id = ? AND workflow_name = ? AND goal = ?
                  AND run_status NOT IN ('completed', 'cancelled')
                ORDER BY created_at DESC LIMIT 1
                """,
                (project_id, workflow_name, goal),
            ).fetchone()
        return _run_from_row(row) if row is not None else None

    def create_run(self, run: WorkflowRun, task: TaskRecord) -> WorkflowRun:
        with self.database.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO workflow_runs(
                        id, project_id, workflow_name, goal, workflow_phase, run_status,
                        state_version, risk_level, checkpoint_json, config_hash,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.id,
                        run.project_id,
                        run.workflow_name,
                        run.goal,
                        run.workflow_phase.value,
                        run.run_status.value,
                        run.state_version,
                        run.risk_level.value,
                        _json(run.checkpoint),
                        run.config_hash,
                        run.created_at,
                        run.updated_at,
                    ),
                )
                _insert_task(connection, task)
                _insert_event(
                    connection,
                    project_id=run.project_id,
                    run_id=run.id,
                    task_id=None,
                    event_type="workflow.started",
                    idempotency_key=f"{run.id}:0:workflow.started",
                    payload={"workflow_phase": run.workflow_phase.value},
                )
                _insert_event(
                    connection,
                    project_id=run.project_id,
                    run_id=run.id,
                    task_id=task.id,
                    event_type="task.created",
                    idempotency_key=f"{run.id}:0:task.created:{task.id}",
                    payload={"agent": task.agent, "input_hash": task.input_hash},
                )
                connection.commit()
            except sqlite3.Error:
                connection.rollback()
                raise
        return self.get_run(run.id)

    def get_run(self, run_id: str) -> WorkflowRun:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise WorkflowNotFoundError(f"workflow run not found: {run_id}")
        return _run_from_row(row)

    def get_task(self, task_id: str) -> TaskRecord:
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise WorkflowNotFoundError(f"task not found: {task_id}")
        return _task_from_row(row)

    def active_task(self, run_id: str) -> TaskRecord | None:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM tasks
                WHERE run_id = ? AND status IN ('pending', 'running')
                ORDER BY created_at DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        return _task_from_row(row) if row is not None else None

    def block_run_for_task(
        self,
        *,
        run: WorkflowRun,
        task: TaskRecord,
        error_code: str,
        reason: str,
        recovery_base_ref: str,
    ) -> WorkflowRun:
        now = _utc_now()
        new_version = run.state_version + 1
        checkpoint = {
            **run.checkpoint,
            "next_action": None,
            "blocker": {"code": error_code, "reason": reason, "task_id": task.id},
            "recovery_base_ref": recovery_base_ref,
        }
        with self.database.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                _assert_run_version(connection, run.id, run.state_version)
                changed = connection.execute(
                    """
                    UPDATE tasks
                    SET status = 'blocked', state_version = state_version + 1,
                        updated_at = ?
                    WHERE id = ? AND status IN ('pending', 'running')
                    """,
                    (now, task.id),
                ).rowcount
                if changed != 1:
                    raise WorkflowConflictError(f"task is no longer active: {task.id}")
                _update_run(
                    connection,
                    run,
                    target_phase=run.workflow_phase,
                    target_status=RunStatus.BLOCKED,
                    checkpoint=checkpoint,
                    now=now,
                )
                _insert_event(
                    connection,
                    project_id=run.project_id,
                    run_id=run.id,
                    task_id=task.id,
                    event_type="task.blocked",
                    idempotency_key=f"{run.id}:{new_version}:task.blocked:{task.id}",
                    payload={"error_code": error_code, "reason": reason},
                )
                _insert_event(
                    connection,
                    project_id=run.project_id,
                    run_id=run.id,
                    task_id=task.id,
                    event_type="workflow.blocked",
                    idempotency_key=f"{run.id}:{new_version}:workflow.blocked",
                    payload={"error_code": error_code, "reason": reason},
                )
                connection.commit()
            except (sqlite3.Error, WorkflowConflictError):
                connection.rollback()
                raise
        return self.get_run(run.id)

    def complete_task_and_transition(
        self,
        *,
        run: WorkflowRun,
        task: TaskRecord,
        completion: TaskCompletion,
        target_phase: WorkflowPhase,
        target_status: RunStatus,
        checkpoint: dict[str, Any],
        next_task: TaskRecord | None,
        verified_git_evidence: dict[str, Any] | None = None,
    ) -> WorkflowRun:
        completion_json = completion.model_dump_json()
        now = _utc_now()
        new_version = run.state_version + 1

        with self.database.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                _assert_run_version(connection, run.id, run.state_version)
                changed = connection.execute(
                    """
                    UPDATE tasks
                    SET status = ?, branch = ?, output_ref = ?, review_status = ?,
                        state_version = state_version + 1, updated_at = ?
                    WHERE id = ? AND status IN ('pending', 'running')
                    """,
                    (
                        TaskStatus.COMPLETED.value,
                        completion.branch,
                        completion_json,
                        "verified" if completion.verification_results else "pending",
                        now,
                        task.id,
                    ),
                ).rowcount
                if changed != 1:
                    raise WorkflowConflictError(f"task is no longer active: {task.id}")

                for path, digest in completion.artifact_paths_and_hashes.items():
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO artifacts(
                            id, run_id, task_id, path, kind, content_hash,
                            source_commit, status, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'verified', ?)
                        """,
                        (
                            new_id("ARTIFACT"),
                            run.id,
                            task.id,
                            path,
                            _artifact_kind(path),
                            digest,
                            completion.commit_sha,
                            now,
                        ),
                    )

                _update_run(
                    connection,
                    run,
                    target_phase=target_phase,
                    target_status=target_status,
                    checkpoint=checkpoint,
                    now=now,
                )
                if next_task is not None:
                    _insert_task(connection, next_task)
                    _insert_task_created_event(connection, run, next_task, new_version)

                _insert_event(
                    connection,
                    project_id=run.project_id,
                    run_id=run.id,
                    task_id=task.id,
                    event_type="task.completed",
                    idempotency_key=f"{run.id}:{new_version}:task.completed:{task.id}",
                    payload={
                        **completion.model_dump(mode="json"),
                        "git_verification": verified_git_evidence,
                    },
                )
                consumer = (
                    next_task.agent
                    if next_task is not None
                    else f"gate:{checkpoint.get('pending_gate') or 'workflow'}"
                )
                handoff_id = new_id("HANDOFF")
                connection.execute(
                    """
                    INSERT INTO handoffs(
                        id, run_id, task_id, producer, consumer, status,
                        artifact_refs_json, commit_refs_json, tests_json,
                        risks_json, open_questions_json, created_at, accepted_at
                    ) VALUES (?, ?, ?, ?, ?, 'ready', ?, ?, ?, '[]', '[]', ?, NULL)
                    """,
                    (
                        handoff_id,
                        run.id,
                        task.id,
                        task.agent,
                        consumer,
                        _json(completion.artifact_paths_and_hashes),
                        _json(verified_git_evidence or {}),
                        _json(completion.verification_results),
                        now,
                    ),
                )
                _insert_event(
                    connection,
                    project_id=run.project_id,
                    run_id=run.id,
                    task_id=task.id,
                    event_type="handoff.created",
                    idempotency_key=f"{run.id}:{new_version}:handoff.created:{task.id}",
                    payload={
                        "handoff_id": handoff_id,
                        "producer": task.agent,
                        "consumer": consumer,
                        "artifacts": completion.artifact_paths_and_hashes,
                        "git_verification": verified_git_evidence,
                        "verification_results": completion.verification_results,
                    },
                )
                _insert_event(
                    connection,
                    project_id=run.project_id,
                    run_id=run.id,
                    task_id=next_task.id if next_task is not None else None,
                    event_type="workflow.transitioned",
                    idempotency_key=f"{run.id}:{new_version}:workflow.transitioned",
                    payload={
                        "from_phase": run.workflow_phase.value,
                        "to_phase": target_phase.value,
                        "run_status": target_status.value,
                    },
                )
                if target_status is RunStatus.NEEDS_APPROVAL:
                    _insert_event(
                        connection,
                        project_id=run.project_id,
                        run_id=run.id,
                        task_id=None,
                        event_type="approval.requested",
                        idempotency_key=f"{run.id}:{new_version}:approval.requested",
                        payload={"gate": checkpoint.get("pending_gate")},
                        approval_required=True,
                    )
                connection.commit()
            except (sqlite3.Error, WorkflowConflictError):
                connection.rollback()
                raise
        return self.get_run(run.id)

    def approval_transition(
        self,
        *,
        run: WorkflowRun,
        gate: Gate,
        approved: bool,
        reviewer: str,
        reason: str,
        target_phase: WorkflowPhase,
        target_status: RunStatus,
        checkpoint: dict[str, Any],
        next_task: TaskRecord | None,
    ) -> WorkflowRun:
        now = _utc_now()
        new_version = run.state_version + 1
        decision = "approved" if approved else "rejected"
        with self.database.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                _assert_run_version(connection, run.id, run.state_version)
                connection.execute(
                    """
                    INSERT INTO approvals(
                        id, run_id, gate, decision, reviewer, reason,
                        evidence_refs_json, state_version, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id("APPROVAL"),
                        run.id,
                        gate.value,
                        decision,
                        reviewer,
                        reason,
                        _json(checkpoint.get("evidence_refs", [])),
                        new_version,
                        now,
                    ),
                )
                _update_run(
                    connection,
                    run,
                    target_phase=target_phase,
                    target_status=target_status,
                    checkpoint=checkpoint,
                    now=now,
                )
                if next_task is not None:
                    _insert_task(connection, next_task)
                    _insert_task_created_event(connection, run, next_task, new_version)
                _insert_event(
                    connection,
                    project_id=run.project_id,
                    run_id=run.id,
                    task_id=next_task.id if next_task is not None else None,
                    event_type=f"approval.{decision}",
                    idempotency_key=f"{run.id}:{new_version}:approval:{gate.value}",
                    payload={"gate": gate.value, "reviewer": reviewer, "reason": reason},
                )
                connection.commit()
            except (sqlite3.Error, WorkflowConflictError):
                connection.rollback()
                raise
        return self.get_run(run.id)

    def resume_transition(
        self,
        *,
        run: WorkflowRun,
        checkpoint: dict[str, Any],
        next_task: TaskRecord | None,
    ) -> WorkflowRun:
        now = _utc_now()
        new_version = run.state_version + 1
        with self.database.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                _assert_run_version(connection, run.id, run.state_version)
                _update_run(
                    connection,
                    run,
                    target_phase=run.workflow_phase,
                    target_status=RunStatus.RUNNING,
                    checkpoint=checkpoint,
                    now=now,
                )
                if next_task is not None:
                    _insert_task(connection, next_task)
                    _insert_task_created_event(connection, run, next_task, new_version)
                _insert_event(
                    connection,
                    project_id=run.project_id,
                    run_id=run.id,
                    task_id=next_task.id if next_task is not None else None,
                    event_type="workflow.resumed",
                    idempotency_key=f"{run.id}:{new_version}:workflow.resumed",
                    payload={"workflow_phase": run.workflow_phase.value},
                )
                connection.commit()
            except (sqlite3.Error, WorkflowConflictError):
                connection.rollback()
                raise
        return self.get_run(run.id)


def _assert_run_version(connection: sqlite3.Connection, run_id: str, expected: int) -> None:
    row = connection.execute(
        "SELECT state_version FROM workflow_runs WHERE id = ?", (run_id,)
    ).fetchone()
    if row is None:
        raise WorkflowNotFoundError(f"workflow run not found: {run_id}")
    if int(row["state_version"]) != expected:
        raise WorkflowConflictError(
            f"workflow state version conflict: expected {expected}, got {row['state_version']}"
        )


def _update_run(
    connection: sqlite3.Connection,
    run: WorkflowRun,
    *,
    target_phase: WorkflowPhase,
    target_status: RunStatus,
    checkpoint: dict[str, Any],
    now: str,
) -> None:
    changed = connection.execute(
        """
        UPDATE workflow_runs
        SET workflow_phase = ?, run_status = ?, state_version = state_version + 1,
            checkpoint_json = ?, updated_at = ?
        WHERE id = ? AND state_version = ?
        """,
        (
            target_phase.value,
            target_status.value,
            _json(checkpoint),
            now,
            run.id,
            run.state_version,
        ),
    ).rowcount
    if changed != 1:
        raise WorkflowConflictError(f"workflow transition conflict: {run.id}")


def _insert_task(connection: sqlite3.Connection, task: TaskRecord) -> None:
    connection.execute(
        """
        INSERT INTO tasks(
            id, run_id, agent, status, input_hash, branch, worktree,
            output_ref, review_status, state_version, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task.id,
            task.run_id,
            task.agent,
            task.status.value,
            task.input_hash,
            task.branch,
            task.worktree,
            task.output_ref,
            task.review_status,
            task.state_version,
            task.created_at,
            task.updated_at,
        ),
    )


def _insert_event(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    run_id: str,
    task_id: str | None,
    event_type: str,
    idempotency_key: str,
    payload: dict[str, Any],
    approval_required: bool = False,
) -> None:
    connection.execute(
        """
        INSERT INTO events(
            event_id, project_id, run_id, task_id, event_type,
            idempotency_key, payload_json, approval_required, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_id("EVENT"),
            project_id,
            run_id,
            task_id,
            event_type,
            idempotency_key,
            _json(payload),
            int(approval_required),
            _utc_now(),
        ),
    )


def _insert_task_created_event(
    connection: sqlite3.Connection,
    run: WorkflowRun,
    task: TaskRecord,
    state_version: int,
) -> None:
    _insert_event(
        connection,
        project_id=run.project_id,
        run_id=run.id,
        task_id=task.id,
        event_type="task.created",
        idempotency_key=f"{run.id}:{state_version}:task.created:{task.id}",
        payload={"agent": task.agent, "input_hash": task.input_hash},
    )


def _run_from_row(row: sqlite3.Row) -> WorkflowRun:
    return WorkflowRun(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        workflow_name=str(row["workflow_name"]),
        goal=str(row["goal"]),
        workflow_phase=WorkflowPhase(str(row["workflow_phase"])),
        run_status=RunStatus(str(row["run_status"])),
        state_version=int(row["state_version"]),
        risk_level=RiskLevel(str(row["risk_level"])),
        checkpoint=_object_dict(str(row["checkpoint_json"])),
        config_hash=str(row["config_hash"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _task_from_row(row: sqlite3.Row) -> TaskRecord:
    return TaskRecord(
        id=str(row["id"]),
        run_id=str(row["run_id"]),
        agent=str(row["agent"]),
        status=TaskStatus(str(row["status"])),
        input_hash=str(row["input_hash"]),
        branch=str(row["branch"]) if row["branch"] is not None else None,
        worktree=str(row["worktree"]) if row["worktree"] is not None else None,
        output_ref=str(row["output_ref"]) if row["output_ref"] is not None else None,
        review_status=(
            str(row["review_status"]) if row["review_status"] is not None else None
        ),
        state_version=int(row["state_version"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _object_dict(raw: str) -> dict[str, Any]:
    value: object = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("stored JSON must be an object")
    object_value = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in object_value):
        raise ValueError("stored JSON object keys must be strings")
    return cast(dict[str, Any], object_value)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _artifact_kind(path: str) -> str:
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else "file"
    return {"md": "document", "json": "report", "xml": "report"}.get(suffix, "file")
