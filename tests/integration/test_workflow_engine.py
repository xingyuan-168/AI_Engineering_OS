from __future__ import annotations

from pathlib import Path

import pytest

from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.application.workflow import WorkflowEngine, WorkflowError
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
    )
    return WorkflowEngine(tmp_path)


def _completion(task_id: str, marker: str = "a") -> TaskCompletion:
    return TaskCompletion(
        task_id=task_id,
        change_kind=ChangeKind.REPOSITORY,
        branch=f"agent/test/{task_id}",
        commit_sha=marker * 40,
        remote_name="origin",
        push_status=PushStatus.PUSHED,
        artifact_paths_and_hashes={f"docs/{task_id}.md": marker * 64},
        verification_results=("checks: passed",),
    )


def _complete_current(engine: WorkflowEngine, run_id: str, marker: str = "a"):
    current = engine.status(run_id)
    assert current.active_task is not None
    return engine.complete_task(run_id, _completion(current.active_task.id, marker))


def test_start_is_idempotent_for_same_active_goal(tmp_path: Path) -> None:
    engine = _engine(tmp_path)

    first = engine.start("Build ERP procurement API")
    second = engine.start("Build ERP procurement API")

    assert first.run.id == second.run.id
    assert first.run.workflow_phase is WorkflowPhase.INTAKE
    assert first.run.run_status is RunStatus.RUNNING
    assert first.active_task == second.active_task


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
    assert task_count == 8
    assert approval_count == 5
    assert artifact_count == 8
    assert event_count == 35


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

    first = engine.complete_task(started.run.id, completion)
    repeated = engine.complete_task(started.run.id, completion)

    assert repeated.run.state_version == first.run.state_version
    with pytest.raises(WorkflowError, match="different evidence"):
        engine.complete_task(started.run.id, _completion(started.active_task.id, "b"))


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
