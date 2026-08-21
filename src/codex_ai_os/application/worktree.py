"""Application service coordinating Git worktrees with runtime audit state."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from codex_ai_os.adapters.worktree import (
    GitWorktreeError,
    GitWorktreeManager,
    WorktreeManager,
    WorktreeSpec,
    WorktreeState,
)
from codex_ai_os.infrastructure.config import load_project_config
from codex_ai_os.infrastructure.database import Database
from codex_ai_os.infrastructure.workflows import WorkflowNotFoundError, WorkflowStore
from codex_ai_os.infrastructure.worktrees import (
    WorktreeRecord,
    WorktreeStore,
    WorktreeStoreError,
)


class WorktreeServiceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TaskWorktreeAllocator(Protocol):
    def allocate(
        self,
        *,
        run_id: str,
        task_id: str,
        base_ref: str = "HEAD",
    ) -> WorktreeState: ...


class WorktreeService:
    def __init__(
        self,
        project_root: Path,
        *,
        manager: WorktreeManager | None = None,
    ) -> None:
        self.config = load_project_config(project_root.resolve())
        database = Database(self.config.root / ".codex-os" / "state" / "state.db")
        database.migrate()
        self.workflow_store = WorkflowStore(database)
        self.store = WorktreeStore(database)
        self.manager = manager or GitWorktreeManager(self.config.root)

    def allocate(
        self,
        *,
        run_id: str,
        task_id: str,
        base_ref: str = "HEAD",
    ) -> WorktreeState:
        record: WorktreeRecord | None = None
        owns_reservation = False
        try:
            run = self.workflow_store.get_run(run_id)
            task = self.workflow_store.get_task(task_id)
        except WorkflowNotFoundError as exc:
            raise WorktreeServiceError("RECOVERY_UNAVAILABLE", str(exc)) from exc
        if run.project_id != self.config.project_id or task.run_id != run.id:
            raise WorktreeServiceError(
                "WORKTREE_BLOCKED", "task, workflow, and project identity do not match"
            )

        existing = self.store.get_for_task(task.id)
        if existing is not None:
            if existing.status == "active":
                return self._inspect(existing.to_spec())
            if existing.status == "provisioning":
                state = self._inspect(existing.to_spec())
                self.store.activate(existing.id, head_commit=state.head_commit)
                return state
            raise WorktreeServiceError(
                "WORKTREE_BLOCKED",
                f"worktree assignment is {existing.status}: {existing.path}",
            )

        try:
            planned = self.manager.plan(
                workflow_id=run.id,
                agent=task.agent,
                task_id=task.id,
                base_ref=base_ref,
            )
            reservation = self.store.reserve(
                project_id=run.project_id,
                run_id=run.id,
                task_id=task.id,
                agent=task.agent,
                spec=planned,
            )
            record = reservation.record
            owns_reservation = reservation.created
            if not owns_reservation:
                try:
                    state = self.manager.inspect(record.to_spec())
                except GitWorktreeError as exc:
                    raise WorktreeServiceError(
                        "WORKTREE_BLOCKED",
                        f"worktree provisioning is already in progress: {record.path}",
                    ) from exc
                self.store.activate(record.id, head_commit=state.head_commit)
                return state
            state = self.manager.create(
                workflow_id=run.id,
                agent=task.agent,
                task_id=task.id,
                base_ref=base_ref,
            )
            if state.spec != planned:
                raise GitWorktreeError("created worktree differs from reserved assignment")
            self.store.activate(record.id, head_commit=state.head_commit)
            return state
        except (GitWorktreeError, WorktreeStoreError) as exc:
            if record is not None and owns_reservation:
                self.store.block(record.id, reason=str(exc))
            raise WorktreeServiceError("WORKTREE_BLOCKED", str(exc)) from exc

    def cleanup(self, *, task_id: str, approved: bool, merged_into: str) -> None:
        record = self.store.get_for_task(task_id)
        if record is None:
            raise WorktreeServiceError(
                "RECOVERY_UNAVAILABLE", f"worktree assignment not found: {task_id}"
            )
        if record.status == "cleaned":
            return
        if record.status != "active":
            raise WorktreeServiceError(
                "WORKTREE_BLOCKED", f"worktree is not active: {record.status}"
            )
        try:
            self.manager.remove(
                record.to_spec(), approved=approved, merged_into=merged_into
            )
            self.store.mark_cleaned(record.id, merged_into=merged_into)
        except (GitWorktreeError, WorktreeStoreError) as exc:
            raise WorktreeServiceError("WORKTREE_BLOCKED", str(exc)) from exc

    def _inspect(self, spec: WorktreeSpec) -> WorktreeState:
        try:
            return self.manager.inspect(spec)
        except GitWorktreeError as exc:
            raise WorktreeServiceError("WORKTREE_BLOCKED", str(exc)) from exc
