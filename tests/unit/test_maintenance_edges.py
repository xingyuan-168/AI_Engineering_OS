# pyright: reportPrivateUsage=false
from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from codex_ai_os.application.maintenance import (
    MaintenanceOperationError,
    ReconciledOperation,
    ScheduledOperation,
    _migration_result,
    host_operation_action,
)
from codex_ai_os.domain.operations import (
    HostOperation,
    HostOperationKind,
    HostOperationStatus,
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
