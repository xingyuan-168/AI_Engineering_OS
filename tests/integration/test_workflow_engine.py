from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

import pytest

from codex_ai_os.adapters.git import (
    GitEvidenceError,
    GitEvidenceResult,
    GitEvidenceVerifier,
)
from codex_ai_os.adapters.worktree import WorktreeSpec, WorktreeState
from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.application.workflow import WorkflowEngine, WorkflowError
from codex_ai_os.application.worktree import WorktreeServiceError
from codex_ai_os.domain.config import ProjectType
from codex_ai_os.domain.workflow import (
    ChangeKind,
    Gate,
    PushStatus,
    RunStatus,
    TaskCompletion,
    WorkflowPhase,
)
from codex_ai_os.infrastructure.workflows import WorkflowConflictError


def _engine(tmp_path: Path) -> WorkflowEngine:
    ProjectInitializer().initialize(
        tmp_path,
        project_id="PROJECT-WORKFLOW",
        name="Workflow pilot",
        project_type=ProjectType.BACKEND,
        schema_version="1.0",
    )
    return WorkflowEngine(
        tmp_path,
        git_evidence=_AllowingGitEvidence(),
        worktrees=_NoopWorktrees(),
    )


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


class _FailingWorktrees:
    def allocate(
        self,
        *,
        run_id: str,
        task_id: str,
        base_ref: str = "HEAD",
    ) -> WorktreeState:
        raise WorktreeServiceError("WORKTREE_BLOCKED", "simulated allocation failure")


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


class _RejectingGitEvidence(GitEvidenceVerifier):
    def verify(self, completion: TaskCompletion) -> GitEvidenceResult:
        raise GitEvidenceError("remote branch is stale")


def _completion(
    task_id: str,
    marker: str = "a",
    artifact_path: str = "docs/PROJECT_MASTER.md",
) -> TaskCompletion:
    return TaskCompletion(
        task_id=task_id,
        change_kind=ChangeKind.REPOSITORY,
        branch=f"agent/test/{task_id}",
        commit_sha=marker * 40,
        remote_name="origin",
        push_status=PushStatus.PUSHED,
        artifact_paths_and_hashes={artifact_path: marker * 64},
        verification_results=("checks: passed",),
    )


def _complete_current(engine: WorkflowEngine, run_id: str, marker: str = "a"):
    current = engine.status(run_id)
    assert current.active_task is not None
    assert current.next_action is not None
    allowed = current.next_action.allowed_paths[0]
    artifact_path = f"{allowed}{current.active_task.id}.md" if allowed.endswith("/") else allowed
    return engine.complete_task(
        run_id,
        _completion(current.active_task.id, marker, artifact_path),
    )


def test_start_is_idempotent_for_same_active_goal(tmp_path: Path) -> None:
    engine = _engine(tmp_path)

    first = engine.start("Build ERP procurement API")
    second = engine.start("Build ERP procurement API")

    assert first.run.id == second.run.id
    assert first.run.workflow_phase is WorkflowPhase.INTAKE
    assert first.run.run_status is RunStatus.RUNNING
    assert first.active_task == second.active_task


@pytest.mark.parametrize(
    ("workflow_name", "phase"),
    [
        ("feature-development", WorkflowPhase.REQUIREMENTS),
        ("bug-fix", WorkflowPhase.IMPLEMENTATION),
        ("release", WorkflowPhase.VERIFY),
    ],
)
def test_expansion_workflows_start_at_governed_phase(
    tmp_path: Path, workflow_name: str, phase: WorkflowPhase
) -> None:
    engine = _engine(tmp_path)

    result = engine.start("Governed change", workflow_name=workflow_name)

    assert result.run.workflow_name == workflow_name
    assert result.run.workflow_phase is phase
    assert result.next_action is not None
    assert result.next_action.requires_repository_change is True


def test_full_new_project_gate_sequence(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    result = engine.start("Build ERP procurement API")
    run_id = result.run.id

    result = _complete_current(engine, run_id, "1")
    assert result.next_action is not None
    assert result.next_action.gate is Gate.G0
    result = engine.submit_approval(
        run_id, gate=Gate.G0, approved=True, reviewer="user", reason="ok"
    )
    assert result.run.workflow_phase is WorkflowPhase.REQUIREMENTS

    result = _complete_current(engine, run_id, "2")
    assert result.next_action is not None
    assert result.next_action.gate is Gate.G1
    engine.submit_approval(run_id, gate=Gate.G1, approved=True, reviewer="user", reason="ok")

    result = _complete_current(engine, run_id, "3")
    assert result.run.workflow_phase is WorkflowPhase.DESIGN
    result = _complete_current(engine, run_id, "4")
    assert result.next_action is not None
    assert result.next_action.gate is Gate.G2
    engine.submit_approval(run_id, gate=Gate.G2, approved=True, reviewer="user", reason="ok")

    result = _complete_current(engine, run_id, "5")
    assert result.run.workflow_phase is WorkflowPhase.VERIFY
    result = _complete_current(engine, run_id, "6")
    assert result.next_action is not None
    assert result.next_action.gate is Gate.G3
    engine.submit_approval(run_id, gate=Gate.G3, approved=True, reviewer="user", reason="ok")

    result = _complete_current(engine, run_id, "7")
    assert result.run.workflow_phase is WorkflowPhase.MEMORY
    result = _complete_current(engine, run_id, "8")
    assert result.next_action is not None
    assert result.next_action.gate is Gate.G4
    result = engine.submit_approval(
        run_id, gate=Gate.G4, approved=True, reviewer="user", reason="ok"
    )

    assert result.run.workflow_phase is WorkflowPhase.COMPLETED
    assert result.run.run_status is RunStatus.COMPLETED
    assert result.next_action is not None
    assert result.next_action.kind.value == "complete"
    assert result.run.state_version == 13
    with engine.store.database.connection() as connection:
        task_count = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        approval_count = connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]
        artifact_count = connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
        event_count = connection.execute(
            "SELECT COUNT(*) FROM events WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
        handoff_count = connection.execute("SELECT COUNT(*) FROM handoffs").fetchone()[0]
        event_payload: Any = connection.execute(
            "SELECT payload_json FROM events WHERE run_id = ? AND event_type = 'task.completed' "
            "ORDER BY sequence LIMIT 1",
            (run_id,),
        ).fetchone()[0]
    assert task_count == 8
    assert approval_count == 5
    assert artifact_count == 8
    assert event_count == 43
    assert handoff_count == 8
    assert '"verified_at":"2026-08-21T00:00:00+00:00"' in str(event_payload)


def test_gate_cannot_be_bypassed_or_mismatched(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    started = engine.start("Build ERP procurement API")

    with pytest.raises(WorkflowError, match="not waiting"):
        engine.submit_approval(
            started.run.id,
            gate=Gate.G0,
            approved=True,
            reviewer="user",
            reason="too early",
        )

    _complete_current(engine, started.run.id)
    with pytest.raises(WorkflowError, match="waiting for G0"):
        engine.submit_approval(
            started.run.id,
            gate=Gate.G1,
            approved=True,
            reviewer="user",
            reason="wrong gate",
        )


def test_task_completion_is_idempotent_and_conflicting_evidence_is_rejected(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    started = engine.start("Build ERP procurement API")
    assert started.active_task is not None
    completion = _completion(started.active_task.id)

    with pytest.raises(WorkflowError) as stale:
        engine.complete_task(
            started.run.id,
            completion,
            expected_task_version=started.active_task.state_version + 1,
            idempotency_key="complete-intake",
        )
    assert stale.value.code == "STATE_VERSION_CONFLICT"

    first = engine.complete_task(
        started.run.id,
        completion,
        expected_task_version=started.active_task.state_version,
        idempotency_key="complete-intake",
    )
    repeated = engine.complete_task(
        started.run.id,
        completion,
        expected_task_version=started.active_task.state_version,
        idempotency_key="complete-intake",
    )

    assert repeated.run.state_version == first.run.state_version
    with pytest.raises(WorkflowError, match="different evidence") as conflicting:
        engine.complete_task(started.run.id, _completion(started.active_task.id, "b"))
    assert conflicting.value.code == "IDEMPOTENCY_CONFLICT"


def test_rejected_gate_blocks_and_resume_creates_new_task(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    started = engine.start("Build ERP procurement API")
    _complete_current(engine, started.run.id)
    rejected = engine.submit_approval(
        started.run.id,
        gate=Gate.G0,
        approved=False,
        reviewer="user",
        reason="scope incomplete",
    )
    assert rejected.run.run_status is RunStatus.BLOCKED
    assert rejected.next_action is None

    resumed = engine.resume(started.run.id)

    assert resumed.run.run_status is RunStatus.RUNNING
    assert resumed.run.workflow_phase is WorkflowPhase.INTAKE
    assert resumed.active_task is not None


def test_pause_and_resume_preserve_the_active_task(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    started = engine.start("Build ERP procurement API")
    assert started.active_task is not None

    paused = engine.pause(started.run.id, reason="simulate interruption")
    resumed = engine.resume(started.run.id)

    assert paused.run.run_status is RunStatus.PAUSED
    assert resumed.run.run_status is RunStatus.RUNNING
    assert resumed.active_task is not None
    assert resumed.active_task.id == started.active_task.id
    with engine.store.database.connection() as connection:
        pause_events = connection.execute(
            "SELECT COUNT(*) FROM events WHERE run_id = ? "
            "AND event_type = 'workflow.paused'",
            (started.run.id,),
        ).fetchone()[0]
    assert pause_events == 1


def test_mutating_task_requires_git_evidence(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    started = engine.start("Build ERP procurement API")
    assert started.active_task is not None

    with pytest.raises(WorkflowError, match="requires pushed Git evidence"):
        engine.complete_task(
            started.run.id,
            TaskCompletion(
                task_id=started.active_task.id,
                change_kind=ChangeKind.NONE,
            ),
        )


def test_artifact_must_stay_inside_next_action_allowed_paths(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    started = engine.start("Build ERP procurement API")
    assert started.active_task is not None
    completion = _completion(
        started.active_task.id,
        artifact_path="docs/UNASSIGNED.md",
    )

    with pytest.raises(WorkflowError, match="outside task allowed paths") as raised:
        engine.complete_task(started.run.id, completion)

    assert raised.value.code == "PATH_POLICY_VIOLATION"
    assert engine.status(started.run.id).run.state_version == 0


def test_worktree_allocation_failure_blocks_run_and_task(tmp_path: Path) -> None:
    ProjectInitializer().initialize(
        tmp_path,
        project_id="PROJECT-WORKFLOW",
        name="Workflow pilot",
        project_type=ProjectType.BACKEND,
        schema_version="1.0",
    )
    engine = WorkflowEngine(
        tmp_path,
        git_evidence=_AllowingGitEvidence(),
        worktrees=_FailingWorktrees(),
    )

    with pytest.raises(WorkflowError, match="simulated allocation failure") as raised:
        engine.start("Build ERP procurement API")

    assert raised.value.code == "WORKTREE_BLOCKED"
    with engine.store.database.connection() as connection:
        run = connection.execute(
            "SELECT run_status FROM workflow_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        task = connection.execute(
            "SELECT status FROM tasks ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        blocked_events = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type IN "
            "('task.blocked', 'workflow.blocked')"
        ).fetchone()[0]
    assert run[0] == "blocked"
    assert task[0] == "blocked"
    assert blocked_events == 2


def test_invalid_git_evidence_does_not_advance_state(tmp_path: Path) -> None:
    ProjectInitializer().initialize(
        tmp_path,
        project_id="PROJECT-WORKFLOW",
        name="Workflow pilot",
        project_type=ProjectType.BACKEND,
        schema_version="1.0",
    )
    engine = WorkflowEngine(
        tmp_path,
        git_evidence=_RejectingGitEvidence(),
        worktrees=_NoopWorktrees(),
    )
    started = engine.start("Build ERP procurement API")
    assert started.active_task is not None

    with pytest.raises(WorkflowError, match="remote branch is stale") as raised:
        engine.complete_task(started.run.id, _completion(started.active_task.id))

    assert raised.value.code == "GIT_EVIDENCE_INVALID"
    current = engine.status(started.run.id)
    assert current.run.state_version == 0
    assert current.run.run_status is RunStatus.RUNNING


def test_real_pushed_git_evidence_creates_verified_handoff(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    project = tmp_path / "project"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(tmp_path, "init", "-b", "main", str(project))
    _git(project, "config", "user.name", "Test User")
    _git(project, "config", "user.email", "test@example.invalid")
    ProjectInitializer().initialize(
        project,
        project_id="PROJECT-REAL-GIT",
        name="Real Git workflow",
        project_type=ProjectType.BACKEND,
        schema_version="1.0",
    )
    _git(project, "add", ".")
    _git(project, "commit", "-m", "test: initialize project")
    _git(project, "remote", "add", "origin", str(remote))
    _git(project, "push", "-u", "origin", "main")

    engine = WorkflowEngine(project)
    started = engine.start("Build ERP procurement API")
    assert started.active_task is not None
    assert started.active_task.worktree is not None
    assert started.active_task.branch is not None
    assert started.next_action is not None
    assert started.next_action.worktree == started.active_task.worktree
    assert started.next_action.branch == started.active_task.branch
    task_worktree = Path(started.active_task.worktree)
    artifact = task_worktree / "docs" / "PROJECT_MASTER.md"
    artifact.write_text(
        artifact.read_text(encoding="utf-8") + "\nVerified intake evidence.\n",
        encoding="utf-8",
    )
    _git(task_worktree, "add", "docs/PROJECT_MASTER.md")
    _git(task_worktree, "commit", "-m", "docs: add intake evidence")
    _git(task_worktree, "push", "-u", "origin", started.active_task.branch)
    commit_sha = _git(task_worktree, "rev-parse", "HEAD")
    committed = _git_bytes(
        task_worktree, "show", f"{commit_sha}:docs/PROJECT_MASTER.md"
    )
    digest = hashlib.sha256(committed).hexdigest()

    result = engine.complete_task(
        started.run.id,
        TaskCompletion(
            task_id=started.active_task.id,
            change_kind=ChangeKind.REPOSITORY,
            branch=started.active_task.branch,
            commit_sha=commit_sha,
            remote_name="origin",
            push_status=PushStatus.PUSHED,
            artifact_paths_and_hashes={"docs/PROJECT_MASTER.md": digest},
            verification_results=("document governance: passed",),
        ),
    )

    assert result.run.run_status is RunStatus.NEEDS_APPROVAL
    with engine.store.database.connection() as connection:
        handoff = connection.execute(
            "SELECT producer, consumer, commit_refs_json FROM handoffs"
        ).fetchone()
    assert handoff["producer"] == "product-manager"
    assert handoff["consumer"] == "gate:G0"
    assert commit_sha in str(handoff["commit_refs_json"])

    approved = engine.submit_approval(
        started.run.id,
        gate=Gate.G0,
        approved=True,
        reviewer="fixture-reviewer",
        reason="intake evidence accepted",
    )
    assert approved.active_task is not None
    assert approved.active_task.worktree is not None
    assert approved.active_task.branch is not None
    assert approved.active_task.branch != started.active_task.branch
    assert _git(Path(approved.active_task.worktree), "rev-parse", "HEAD") == commit_sha


def test_stale_state_version_is_rejected(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    started = engine.start("Build ERP procurement API")
    stale_run = engine.store.get_run(started.run.id)
    _complete_current(engine, started.run.id)

    with pytest.raises(WorkflowConflictError, match="version conflict"):
        engine.store.resume_transition(
            run=stale_run,
            checkpoint=stale_run.checkpoint,
            next_task=None,
        )


def _git(cwd: Path, *arguments: str) -> str:
    return _git_bytes(cwd, *arguments).decode("utf-8").strip()


def _git_bytes(cwd: Path, *arguments: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(cwd), *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    return result.stdout
