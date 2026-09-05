"""Audited Docker execution use case for assigned task worktrees."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from codex_ai_os.adapters.docker import (
    DockerSandbox,
    DockerSandboxError,
    SandboxExecutor,
    SandboxRequest,
)
from codex_ai_os.adapters.podman import PodmanSandbox
from codex_ai_os.domain.config import SandboxBackend
from codex_ai_os.infrastructure.config import load_execution_policy, load_project_config
from codex_ai_os.infrastructure.database import Database
from codex_ai_os.infrastructure.documents import DocumentManager
from codex_ai_os.infrastructure.executions import (
    ExecutionRecord,
    ExecutionStore,
    ExecutionStoreError,
)
from codex_ai_os.infrastructure.worktrees import WorktreeStore


class ExecutionServiceError(RuntimeError):
    def __init__(
        self, code: str, message: str, *, record: ExecutionRecord | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.record = record


class ExecutionService:
    def __init__(
        self,
        project_root: Path,
        *,
        sandbox: SandboxExecutor | None = None,
    ) -> None:
        self.config = load_project_config(project_root.resolve())
        database = Database(self.config.root / ".codex-os" / "state" / "state.db")
        database.migrate()
        self.store = ExecutionStore(database)
        self.worktrees = WorktreeStore(database)
        policy = load_execution_policy(self.config.root)
        if sandbox is not None:
            self.sandbox = sandbox
        elif policy.sandbox is SandboxBackend.PODMAN:
            self.sandbox = PodmanSandbox(self.config.root, policy=policy)
        else:
            self.sandbox = DockerSandbox(self.config.root, policy=policy)
        self.documents = DocumentManager(self.config.root)

    def execute(self, request: SandboxRequest) -> ExecutionRecord:
        assignment = self.worktrees.get_for_task(request.task_id)
        if assignment is None:
            raise ExecutionServiceError(
                "WORKTREE_BLOCKED", f"task has no worktree assignment: {request.task_id}"
            )
        if assignment.status != "active" or assignment.to_spec() != request.worktree:
            raise ExecutionServiceError(
                "WORKTREE_BLOCKED", "execution request does not match the active worktree"
            )

        started_at = _utc_now()
        try:
            reservation = self.store.start(request, started_at=started_at)
        except ExecutionStoreError as exc:
            raise ExecutionServiceError("STATE_CONFLICT", str(exc)) from exc
        if not reservation.created:
            return reservation.record

        try:
            result = self.sandbox.run(request)
        except DockerSandboxError as exc:
            stderr_ref = self._write_log(request.execution_id, "stderr", str(exc))
            try:
                self.store.fail(
                    request.execution_id,
                    error_code=exc.code,
                    stderr_ref=stderr_ref,
                    ended_at=_utc_now(),
                    worktree_dirty=exc.code in {"EXECUTION_LIMIT_EXCEEDED", "WORKTREE_BLOCKED"},
                )
            except ExecutionStoreError as store_exc:
                raise ExecutionServiceError("STATE_CONFLICT", str(store_exc)) from store_exc
            raise ExecutionServiceError(exc.code, str(exc)) from exc

        stdout_ref = self._write_log(request.execution_id, "stdout", result.stdout)
        stderr_ref = self._write_log(request.execution_id, "stderr", result.stderr)
        try:
            record = self.store.finish(
                result,
                stdout_ref=stdout_ref,
                stderr_ref=stderr_ref,
            )
        except ExecutionStoreError as exc:
            raise ExecutionServiceError("STATE_CONFLICT", str(exc)) from exc
        if record.status != "completed":
            raise ExecutionServiceError(
                record.error_code or "EXECUTION_FAILED",
                f"sandbox command failed with exit code {record.exit_code}",
                record=record,
            )
        return record

    def _write_log(self, execution_id: str, stream: str, content: str) -> str:
        relative = f".codex-os/logs/executions/{execution_id}.{stream}.log"
        self.documents.write_atomic(relative, content, overwrite=True)
        return relative


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
