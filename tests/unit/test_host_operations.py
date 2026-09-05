from __future__ import annotations

from pathlib import Path

import pytest

from codex_ai_os.domain.operations import (
    HostOperation,
    HostOperationKind,
    HostOperationStatus,
    ReconciliationOutcome,
)
from codex_ai_os.infrastructure.database import Database
from codex_ai_os.infrastructure.operations import HostOperationError, HostOperationStore


def test_host_operation_is_idempotent_and_redacts_stored_request(tmp_path: Path) -> None:
    store = _store(tmp_path)
    request = {"remote": "origin", "authorization_token": "never-store-me"}

    created = store.ensure_pending(
        project_id="PROJECT-OPS",
        run_id="RUN-OPS",
        kind=HostOperationKind.INTEGRATION_MERGE,
        idempotency_key="merge:TASK-1",
        request=request,
        expected_state_version=3,
        expected_task_version=1,
    )
    replayed = store.ensure_pending(
        project_id="PROJECT-OPS",
        run_id="RUN-OPS",
        kind=HostOperationKind.INTEGRATION_MERGE,
        idempotency_key="merge:TASK-1",
        request=request,
        expected_state_version=3,
        expected_task_version=1,
    )

    assert replayed.operation_id == created.operation_id
    assert replayed.request["authorization_token"] == "[REDACTED]"
    with pytest.raises(HostOperationError, match="different request") as conflict:
        store.ensure_pending(
            project_id="PROJECT-OPS",
            run_id="RUN-OPS",
            kind=HostOperationKind.INTEGRATION_MERGE,
            idempotency_key="merge:TASK-1",
            request={"remote": "upstream"},
        )
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"


def test_host_operation_lease_completion_and_safe_retry(tmp_path: Path) -> None:
    store = _store(tmp_path)
    pending = _operation(store, "release")

    running = store.acquire(
        pending.operation_id,
        expected_version=0,
        lease_owner="os:test",
    )
    assert running.status is HostOperationStatus.RUNNING
    assert running.state_version == 1
    assert running.attempt_count == 1
    with pytest.raises(HostOperationError) as leased:
        store.acquire(
            running.operation_id,
            expected_version=1,
            lease_owner="os:other",
        )
    assert leased.value.code == "OPERATION_LEASED"
    assert leased.value.retryable is True

    failed = store.mark_failed(
        running.operation_id,
        expected_version=1,
        lease_owner="os:test",
        error_code="REMOTE_UNREACHABLE",
    )
    retried = store.acquire(
        failed.operation_id,
        expected_version=2,
        lease_owner="os:test",
    )
    succeeded = store.mark_succeeded(
        retried.operation_id,
        expected_version=3,
        lease_owner="os:test",
        result={"tag": "v0.2.0"},
    )
    assert succeeded.status is HostOperationStatus.SUCCEEDED
    assert succeeded.attempt_count == 2
    assert succeeded.result == {"tag": "v0.2.0"}


def test_unknown_outcome_requires_reconciliation_before_retry(tmp_path: Path) -> None:
    store = _store(tmp_path)
    running = store.acquire(
        _operation(store, "publish").operation_id,
        expected_version=0,
        lease_owner="os:test",
    )
    unknown = store.mark_outcome_unknown(
        running.operation_id,
        expected_version=1,
        lease_owner="os:test",
    )
    assert unknown.status is HostOperationStatus.RECONCILE_REQUIRED

    with pytest.raises(HostOperationError) as blocked:
        store.acquire(
            unknown.operation_id,
            expected_version=2,
            lease_owner="os:test",
        )
    assert blocked.value.code == "RECONCILIATION_REQUIRED"

    pending = store.reconcile(
        unknown.operation_id,
        expected_version=2,
        outcome=ReconciliationOutcome.NOT_APPLIED,
    )
    assert pending.status is HostOperationStatus.PENDING
    assert pending.ended_at is None
    assert store.acquire(
        pending.operation_id,
        expected_version=3,
        lease_owner="os:test",
    ).attempt_count == 2


def test_unfinished_operation_can_be_marked_for_external_reconciliation(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    operation = _operation(store, "external-inspection")

    unknown = store.require_reconciliation(
        operation.operation_id,
        expected_version=operation.state_version,
        error_code="REMOTE_RESULT_UNKNOWN",
    )

    assert unknown.status is HostOperationStatus.RECONCILE_REQUIRED
    assert unknown.state_version == operation.state_version + 1
    assert unknown.error_code == "REMOTE_RESULT_UNKNOWN"


def test_recovery_is_ordered_and_bounded_to_four(tmp_path: Path) -> None:
    store = _store(tmp_path)
    operations = [_operation(store, f"operation-{index}") for index in range(5)]

    recovered = store.recoverable("RUN-OPS")

    assert len(recovered) == 4
    assert {item.operation_id for item in recovered} <= {
        item.operation_id for item in operations
    }
    with pytest.raises(HostOperationError, match=r"1\.\.4"):
        store.recoverable("RUN-OPS", limit=5)


def test_operation_expected_version_conflict_is_retryable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    operation = _operation(store, "version")

    with pytest.raises(HostOperationError) as conflict:
        store.acquire(
            operation.operation_id,
            expected_version=7,
            lease_owner="os:test",
        )

    assert conflict.value.code == "STATE_VERSION_CONFLICT"
    assert conflict.value.retryable is True


def test_operation_intent_participates_in_callers_transaction(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with store.database.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        operation = store.ensure_pending_in_transaction(
            connection,
            project_id="PROJECT-OPS",
            run_id="RUN-OPS",
            kind=HostOperationKind.INTEGRATION_PREPARE,
            idempotency_key="prepare:rollback",
            request={"base_commit": "a" * 40},
        )
        connection.rollback()
    with pytest.raises(HostOperationError, match="not found"):
        store.get(operation.operation_id)


def test_operation_invalid_transitions_fail_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(HostOperationError) as missing:
        store.get("OP-MISSING")
    assert missing.value.code == "RECOVERY_UNAVAILABLE"

    with store.database.connection() as connection:
        with pytest.raises(HostOperationError, match="active transaction"):
            store.ensure_pending_in_transaction(
                connection,
                project_id="PROJECT-OPS",
                kind=HostOperationKind.RELEASE_PUBLISH,
                idempotency_key="publish",
                request={},
            )
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(HostOperationError, match="idempotency_key"):
            store.ensure_pending_in_transaction(
                connection,
                project_id="PROJECT-OPS",
                kind=HostOperationKind.RELEASE_PUBLISH,
                idempotency_key=" ",
                request={},
            )
        connection.rollback()

    pending = _operation(store, "invalid-transitions")
    with pytest.raises(HostOperationError, match="invalid operation lease"):
        store.acquire(pending.operation_id, expected_version=0, lease_owner="", lease_seconds=0)
    running = store.acquire(pending.operation_id, expected_version=0, lease_owner="os:test")
    with pytest.raises(HostOperationError) as wrong_owner:
        store.mark_succeeded(
            running.operation_id,
            expected_version=running.state_version,
            lease_owner="os:other",
            result={},
        )
    assert wrong_owner.value.code == "OPERATION_LEASED"
    with pytest.raises(HostOperationError, match="error_code"):
        store.mark_failed(
            running.operation_id,
            expected_version=running.state_version,
            lease_owner="os:test",
            error_code=" ",
        )
    succeeded = store.mark_succeeded(
        running.operation_id,
        expected_version=running.state_version,
        lease_owner="os:test",
        result={},
    )
    assert store.acquire(
        succeeded.operation_id,
        expected_version=succeeded.state_version,
        lease_owner="os:test",
    ).status is HostOperationStatus.SUCCEEDED
    with pytest.raises(HostOperationError, match="completed operation"):
        store.require_reconciliation(
            succeeded.operation_id,
            expected_version=succeeded.state_version,
        )
    with pytest.raises(HostOperationError, match="error_code"):
        store.require_reconciliation(
            pending.operation_id,
            expected_version=0,
            error_code=" ",
        )
    with pytest.raises(HostOperationError, match="does not require"):
        store.reconcile(
            _operation(store, "not-unknown").operation_id,
            expected_version=0,
            outcome=ReconciliationOutcome.FAILED,
        )

def _operation(store: HostOperationStore, key: str) -> HostOperation:
    return store.ensure_pending(
        project_id="PROJECT-OPS",
        run_id="RUN-OPS",
        kind=HostOperationKind.RELEASE_PUBLISH,
        idempotency_key=key,
        request={"key": key},
    )


def _store(tmp_path: Path) -> HostOperationStore:
    database = Database(tmp_path / "state.db")
    database.migrate()
    with database.connection() as connection:
        connection.execute(
            "INSERT INTO projects(id, name, root, config_hash, created_at, updated_at) "
            "VALUES ('PROJECT-OPS', 'Operations', ?, 'hash', 'now', 'now')",
            (tmp_path.as_posix(),),
        )
        connection.execute(
            """
            INSERT INTO workflow_runs(
                id, project_id, workflow_name, goal, workflow_phase, run_status,
                risk_level, checkpoint_json, config_hash, created_at, updated_at
            ) VALUES ('RUN-OPS', 'PROJECT-OPS', 'new-project', 'goal', 'implementation',
                      'running', 'high', '{}', 'hash', 'now', 'now')
            """
        )
        connection.commit()
    return HostOperationStore(database)
