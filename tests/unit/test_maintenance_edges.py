# pyright: reportPrivateUsage=false
from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from codex_ai_os.application.maintenance import (
    MaintenanceOperationError,
    ReconciledOperation,
    ScheduledOperation,
    VerificationPrepareService,
    _migration_result,
    host_operation_action,
)
from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.domain.config import ProjectType
from codex_ai_os.domain.operations import (
    HostOperation,
    HostOperationKind,
    HostOperationStatus,
)
from codex_ai_os.domain.profiles import (
    ProfileGateRequirement,
    ProfileTaskTemplate,
    ProjectProfile,
)
from codex_ai_os.domain.workflow import ActionKind


def test_migration_result_recovery_contract_is_strict(tmp_path: Path) -> None:
    result = _migration_result(
        {
            "applied_versions": ["0007"],
            "current_version": "0007",
            "backup_path": str(tmp_path / "backup.db"),
        }
    )
    assert result.applied_versions == ("0007",)
    assert result.current_version == "0007"
    assert result.backup_path == tmp_path / "backup.db"

    payloads = cast(
        tuple[dict[str, object], ...],
        (
            {},
            {"applied_versions": [7]},
            {"applied_versions": [], "current_version": 7},
            {"applied_versions": [], "backup_path": 7},
        ),
    )
    for payload in payloads:
        with pytest.raises(MaintenanceOperationError) as invalid:
            _migration_result(payload)
        assert invalid.value.code == "RECOVERY_UNAVAILABLE"


def test_host_operation_action_and_reconciliation_state() -> None:
    pending = _operation(HostOperationStatus.PENDING)
    action = host_operation_action(pending)
    assert action.kind is ActionKind.HOST_OPERATION
    assert action.dependencies == ("HANDOFF-1", "RELEASE-1", "TASK-1")
    assert action.requires_repository_change is True
    assert ScheduledOperation(pending).next_action == action
    assert ReconciledOperation(pending).next_action == action
    assert ReconciledOperation(
        pending.model_copy(update={"status": HostOperationStatus.SUCCEEDED})
    ).next_action is None


def test_verification_prepare_input_and_run_boundaries(tmp_path: Path) -> None:
    root = tmp_path / "project"
    ProjectInitializer().initialize(
        root,
        project_id="PROJECT-MAINTENANCE-EDGES",
        name="Maintenance edge fixture",
        project_type=ProjectType.BACKEND,
        schema_version="1.0",
    )
    service = VerificationPrepareService(root)
    cases = (
        {"network_approval_ref": "", "expires_at": "2099-01-01T00:00:00+00:00"},
        {"network_approval_ref": "APPROVAL-1", "expires_at": ""},
        {"network_approval_ref": "APPROVAL-1", "expires_at": "invalid"},
        {
            "network_approval_ref": "APPROVAL-1",
            "expires_at": "2099-01-01T00:00:00+00:00",
        },
    )
    for index, values in enumerate(cases):
        with pytest.raises(MaintenanceOperationError):
            service.prepare(
                run_id="RUN-MISSING",
                expected_state_version=0,
                idempotency_key=f"prepare-{index}",
                **values,
            )
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    with pytest.raises(MaintenanceOperationError) as missing_run:
        service.prepare(
            run_id="RUN-MISSING",
            expected_state_version=0,
            idempotency_key="prepare-missing-run",
            network_approval_ref="APPROVAL-1",
            expires_at="2099-01-01T00:00:00+00:00",
        )
    assert missing_run.value.code == "RECOVERY_UNAVAILABLE"


def test_profile_policy_models_reject_unsafe_and_ambiguous_rules() -> None:
    task = ProfileTaskTemplate(
        key="backend-task",
        agent="backend-engineer",
        skill="backend-implementation",
        prompt="Implement the backend.",
        impact_patterns=("src/**",),
    )
    gate = ProfileGateRequirement(gate="G3", checks=("pytest-coverage",))
    for values in (
        {"impact_patterns": ()},
        {"impact_patterns": ("../escape",)},
        {"impact_patterns": ("src/**",), "depends_on": ("same", "same")},
    ):
        with pytest.raises(ValidationError):
            ProfileTaskTemplate.model_validate(
                {
                    **task.model_dump(mode="python"),
                    **values,
                }
            )
    for values in (
        {"artifacts": ("../escape",)},
        {"artifacts": ("docs/a.md", "docs/a.md")},
        {"checks": ("Invalid Name",)},
    ):
        with pytest.raises(ValidationError):
            ProfileGateRequirement.model_validate({"gate": "G3", **values})

    profile = {
        "schema_version": "1.1",
        "name": "backend-project",
        "description": "Backend profile",
        "project_types": (ProjectType.BACKEND,),
        "additional_skills": ("backend-implementation",),
        "required_artifacts": ("docs/API_SPEC.md",),
        "additional_reviewers": ("code-reviewer",),
        "min_agents": 1,
    }
    invalid_shapes = (
        {"task_templates": (task, task)},
        {
            "task_templates": (
                task.model_copy(update={"depends_on": ("missing-task",)}),
            )
        },
        {"gate_requirements": (gate, gate)},
    )
    for shape in invalid_shapes:
        with pytest.raises(ValidationError):
            ProjectProfile.model_validate({**profile, **shape})


def _operation(status: HostOperationStatus) -> HostOperation:
    return HostOperation(
        operation_id="OP-1",
        project_id="PROJECT-1",
        run_id="RUN-1",
        task_id="TASK-1",
        task_group_id="GROUP-1",
        handoff_id="HANDOFF-1",
        release_id="RELEASE-1",
        kind=HostOperationKind.DATABASE_MIGRATE,
        idempotency_key="operation-1",
        request_hash="a" * 64,
        status=status,
        expected_state_version=3,
        expected_task_version=2,
        state_version=4,
        attempt_count=1,
        request={},
        result={},
        created_at="2026-08-29T00:00:00+00:00",
        updated_at="2026-08-29T00:00:00+00:00",
    )
