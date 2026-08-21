import pytest
from pydantic import ValidationError

from codex_ai_os.domain.workflow import (
    AUTOMATIC_NEXT_PHASE,
    GATE_AFTER_PHASE,
    PHASE_AFTER_APPROVAL,
    WORKFLOW_START_PHASE,
    ActionKind,
    ChangeKind,
    Gate,
    NextAction,
    PushStatus,
    TaskCompletion,
    WorkflowPhase,
)


def test_gate_and_automatic_phase_maps_cover_new_project() -> None:
    assert GATE_AFTER_PHASE[WorkflowPhase.INTAKE] is Gate.G0
    assert GATE_AFTER_PHASE[WorkflowPhase.MEMORY] is Gate.G4
    assert PHASE_AFTER_APPROVAL[Gate.G4] is WorkflowPhase.COMPLETED
    assert AUTOMATIC_NEXT_PHASE[WorkflowPhase.RESEARCH] is WorkflowPhase.DESIGN
    assert WORKFLOW_START_PHASE == {
        "new-project": WorkflowPhase.INTAKE,
        "feature-development": WorkflowPhase.REQUIREMENTS,
        "bug-fix": WorkflowPhase.IMPLEMENTATION,
        "release": WorkflowPhase.VERIFY,
    }


def test_model_task_requires_host_routing_fields() -> None:
    with pytest.raises(ValidationError, match="model_task requires"):
        NextAction(kind=ActionKind.MODEL_TASK)


def test_approval_action_requires_gate() -> None:
    with pytest.raises(ValidationError, match="requires a gate"):
        NextAction(kind=ActionKind.APPROVAL)


def test_repository_completion_requires_delivery_evidence() -> None:
    with pytest.raises(ValidationError, match="requires branch"):
        TaskCompletion(task_id="TASK-1", change_kind=ChangeKind.REPOSITORY)

    with pytest.raises(ValidationError, match="requires pushed or local-only"):
        TaskCompletion(
            task_id="TASK-1",
            change_kind=ChangeKind.REPOSITORY,
            branch="agent/backend/TASK-1",
            commit_sha="a" * 40,
            remote_name="origin",
            artifact_paths_and_hashes={"src/app.py": "b" * 64},
        )


def test_repository_completion_accepts_complete_evidence() -> None:
    completion = TaskCompletion(
        task_id="TASK-1",
        change_kind=ChangeKind.REPOSITORY,
        branch="agent/backend/TASK-1",
        commit_sha="a" * 40,
        remote_name="origin",
        push_status=PushStatus.PUSHED,
        artifact_paths_and_hashes={"src/app.py": "b" * 64},
        verification_results=("pytest: passed",),
    )

    assert completion.push_status is PushStatus.PUSHED


def test_repository_completion_accepts_explicit_local_fixture_evidence() -> None:
    completion = TaskCompletion(
        task_id="TASK-1",
        change_kind=ChangeKind.REPOSITORY,
        branch="agent/backend/TASK-1",
        commit_sha="a" * 40,
        push_status=PushStatus.LOCAL_ONLY,
        artifact_paths_and_hashes={"src/app.py": "b" * 64},
    )

    assert completion.remote_name is None


def test_read_only_completion_cannot_claim_push() -> None:
    with pytest.raises(ValidationError, match="not_required"):
        TaskCompletion(
            task_id="TASK-1",
            change_kind=ChangeKind.NONE,
            push_status=PushStatus.PUSHED,
        )
