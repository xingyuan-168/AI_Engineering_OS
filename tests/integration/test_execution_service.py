from __future__ import annotations

from pathlib import Path

import pytest

from codex_ai_os.adapters.docker import (
    DEFAULT_EXECUTION_IMAGE,
    DockerSandboxError,
    SandboxRequest,
    SandboxResult,
    command_hash,
)
from codex_ai_os.adapters.git import GitEvidenceResult, GitEvidenceVerifier
from codex_ai_os.adapters.worktree import WorktreeSpec, WorktreeState
from codex_ai_os.application.execution import ExecutionService, ExecutionServiceError
from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.application.workflow import WorkflowEngine
from codex_ai_os.domain.config import ProjectType, RiskLevel
from codex_ai_os.domain.workflow import TaskCompletion
from codex_ai_os.infrastructure.database import Database
from codex_ai_os.infrastructure.worktrees import WorktreeStore


class _AllowingGitEvidence(GitEvidenceVerifier):
    def verify(self, completion: TaskCompletion) -> GitEvidenceResult:
        return GitEvidenceResult(
            branch=completion.branch or "missing",
            commit_sha=completion.commit_sha or "missing",
            remote_name=completion.remote_name or "missing",
            remote_url="test://origin",
            artifact_hashes=dict(completion.artifact_paths_and_hashes),
            verified_at="2026-08-21T00:00:00+00:00",
        )


class _SuccessfulSandbox:
    def __init__(self, *, exit_code: int = 0, dirty: bool = False) -> None:
        self.exit_code = exit_code
        self.dirty = dirty
        self.calls = 0

    def run(self, request: SandboxRequest) -> SandboxResult:
        self.calls += 1
        return SandboxResult(
            execution_id=request.execution_id,
            command_hash=command_hash(request.command),
            image_digest=request.image.rsplit("@", 1)[1],
            container_name=f"codex-os-{request.execution_id.lower()}",
            exit_code=self.exit_code,
            stdout="tests passed\n",
            stderr="" if self.exit_code == 0 else "tests failed\n",
            worktree_dirty=self.dirty,
            started_at="2026-08-21T00:00:01+00:00",
            ended_at="2026-08-21T00:00:02+00:00",
        )


class _UnavailableSandbox:
    def run(self, request: SandboxRequest) -> SandboxResult:
        raise DockerSandboxError("SANDBOX_UNAVAILABLE", "Docker daemon is unavailable")


class _NoopWorktrees:
    def allocate(
        self,
        *,
        run_id: str,
        task_id: str,
        base_ref: str = "HEAD",
    ) -> WorktreeState:
        spec = WorktreeSpec(
            workflow_id=run_id,
            agent="test-agent",
            task_id=task_id,
            branch=f"agent/test-agent/{task_id}",
            path=Path("C:/fixture") / run_id / task_id,
            base_commit=base_ref,
        )
        return WorktreeState(spec=spec, head_commit=base_ref, dirty=False)


def test_successful_execution_is_idempotent_and_audited(tmp_path: Path) -> None:
    request = _assigned_request(tmp_path)
    sandbox = _SuccessfulSandbox()
    service = ExecutionService(tmp_path, sandbox=sandbox)

    first = service.execute(request)
    repeated = service.execute(request)

    assert repeated == first
    assert sandbox.calls == 1
    assert first.status == "completed"
    assert first.exit_code == 0
    assert first.stdout_ref is not None
    assert (tmp_path / first.stdout_ref).read_text(encoding="utf-8") == "tests passed\n"
    with service.store.database.connection() as connection:
        events = [
            str(row[0])
            for row in connection.execute(
                "SELECT event_type FROM events WHERE task_id = ? "
                "AND event_type LIKE 'execution.%' ORDER BY sequence",
                (request.task_id,),
            ).fetchall()
        ]
    assert events == ["execution.started", "execution.completed"]


def test_nonzero_execution_is_failed_and_marks_dirty_worktree(tmp_path: Path) -> None:
    request = _assigned_request(tmp_path)
    service = ExecutionService(
        tmp_path,
        sandbox=_SuccessfulSandbox(exit_code=7, dirty=True),
    )

    with pytest.raises(ExecutionServiceError, match="exit code 7") as exc:
        service.execute(request)

    assert exc.value.code == "EXECUTION_FAILED"
    record = service.store.get(request.execution_id)
    assert record is not None
    assert record.status == "failed"
    assert record.exit_code == 7
    assignment = service.worktrees.get_for_task(request.task_id)
    assert assignment is not None
    assert assignment.dirty is True
    with service.store.database.connection() as connection:
        dirty_events = connection.execute(
            "SELECT COUNT(*) FROM events WHERE task_id = ? "
            "AND event_type = 'worktree.dirty'",
            (request.task_id,),
        ).fetchone()[0]
    assert dirty_events == 1


def test_unavailable_sandbox_is_persisted_as_failure(tmp_path: Path) -> None:
    request = _assigned_request(tmp_path)
    service = ExecutionService(tmp_path, sandbox=_UnavailableSandbox())

    with pytest.raises(ExecutionServiceError, match="Docker daemon is unavailable") as exc:
        service.execute(request)

    assert exc.value.code == "SANDBOX_UNAVAILABLE"
    record = service.store.get(request.execution_id)
    assert record is not None
    assert record.status == "failed"
    assert record.error_code == "SANDBOX_UNAVAILABLE"
    assert record.stderr_ref is not None
    assert (tmp_path / record.stderr_ref).is_file()


def _assigned_request(root: Path) -> SandboxRequest:
    ProjectInitializer().initialize(
        root,
        project_id="PROJECT-EXECUTION",
        name="Execution fixture",
        project_type=ProjectType.BACKEND,
    )
    engine = WorkflowEngine(
        root,
        git_evidence=_AllowingGitEvidence(),
        worktrees=_NoopWorktrees(),
    )
    started = engine.start("Execute verification in Docker")
    assert started.active_task is not None
    worktree = root / ".worktrees" / started.run.id / started.active_task.agent
    worktree /= started.active_task.id
    worktree.mkdir(parents=True)
    spec = WorktreeSpec(
        workflow_id=started.run.id,
        agent=started.active_task.agent,
        task_id=started.active_task.id,
        branch=f"agent/{started.active_task.agent}/{started.active_task.id}",
        path=worktree.resolve(),
        base_commit="a" * 40,
    )
    database = Database(root / ".codex-os" / "state" / "state.db")
    reservation = WorktreeStore(database).reserve(
        project_id=started.run.project_id,
        run_id=started.run.id,
        task_id=started.active_task.id,
        agent=started.active_task.agent,
        spec=spec,
    )
    WorktreeStore(database).activate(reservation.record.id, head_commit="a" * 40)
    return SandboxRequest(
        execution_id="EXEC-001",
        task_id=started.active_task.id,
        worktree=spec,
        command=("pytest", "-q"),
        risk_level=RiskLevel.MEDIUM,
        image=DEFAULT_EXECUTION_IMAGE,
        timeout_seconds=30,
    )
