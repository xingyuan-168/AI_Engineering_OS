# pyright: reportPrivateUsage=false
from __future__ import annotations

from typing import cast

import pytest

from codex_ai_os.application.workflow import (
    WorkflowError,
    _action_from_checkpoint,
    _actions_from_checkpoint,
    _artifact_path_allowed,
    _coordination_checkpoint,
    _default_profiles,
    _input_artifacts_for,
    _operations_checkpoint,
)
from codex_ai_os.domain.workflow import ActionKind, NextAction, WorkflowPhase


def test_checkpoint_decoding_fails_closed_and_preserves_multiple_actions() -> None:
    action = NextAction(kind=ActionKind.HOST_OPERATION, operation_id="OP-1")
    serialized = action.model_dump(mode="json")
    assert _action_from_checkpoint({}) is None
    assert _action_from_checkpoint({"next_action": serialized}) == action
    assert _actions_from_checkpoint({"next_action": serialized}) == (action,)
    assert _actions_from_checkpoint({"next_actions": [serialized, serialized]}) == (
        action,
        action,
    )

    checkpoints = cast(
        tuple[dict[str, object], ...],
        (
            {"next_action": "invalid"},
            {"next_action": {}},
            {"next_actions": "invalid"},
            {"next_actions": ["invalid"]},
            {"next_actions": [{}]},
        ),
    )
    for checkpoint in checkpoints:
        with pytest.raises(WorkflowError) as invalid:
            _actions_from_checkpoint(checkpoint)
        assert invalid.value.code == "RECOVERY_UNAVAILABLE"


def test_checkpoint_builders_profiles_inputs_and_paths() -> None:
    action = NextAction(kind=ActionKind.HOST_OPERATION, operation_id="OP-1")
    coordinated = _coordination_checkpoint(
        {"last_commit_sha": "a" * 40, "integration_worktree": "old"},
        (action,),
        task_group_id="GROUP-1",
        waiting_handoff="HANDOFF-1",
    )
    assert coordinated["next_action"] == action.model_dump(mode="json")
    assert coordinated["last_commit_sha"] == "a" * 40
    assert coordinated["integration_worktree"] == "old"
    resumed = _operations_checkpoint(coordinated, (action, action), resumed_from="blocked")
    assert resumed["next_action"] is None
    assert resumed["blocker"] is None

    assert _default_profiles("frontend") == ("frontend-project",)
    assert _default_profiles("fullstack") == ("backend-project", "frontend-project")
    assert _default_profiles("backend") == ("backend-project",)
    assert _input_artifacts_for(WorkflowPhase.RELEASE) == ("verification_evidence",)
    assert _artifact_path_allowed("src/module.py", ("src/",))
    assert _artifact_path_allowed("docs/API_SPEC.md", ("docs/API_SPEC.md",))
    assert not _artifact_path_allowed("src/", ("src/",))
    assert not _artifact_path_allowed("../escape", ("src/",))
    assert not _artifact_path_allowed("/absolute", ("src/",))
