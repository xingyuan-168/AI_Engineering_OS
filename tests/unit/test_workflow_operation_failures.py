# pyright: reportPrivateUsage=false
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

import pytest

from codex_ai_os.adapters.worktree import WorktreeSpec, WorktreeState
from codex_ai_os.application.coordination import CoordinationService
from codex_ai_os.application.g4 import (
    G4PublicationResult,
    G4Publisher,
    ReleaseGovernanceError,
)
from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.application.verification_cache import (
    VerificationCachePrepareError,
    VerificationCachePreparer,
)
from codex_ai_os.application.workflow import WorkflowEngine, WorkflowError, WorkflowResult
from codex_ai_os.application.worktree import TaskWorktreeAllocator
from codex_ai_os.domain.config import GitPushPolicy, ProjectType
from codex_ai_os.domain.governance import G4ApprovalInput
from codex_ai_os.domain.invocation import InvocationContext, InvocationSource
from codex_ai_os.domain.operations import HostOperation, HostOperationKind
from codex_ai_os.domain.workflow import WorkflowRun
from codex_ai_os.infrastructure.coordination import CoordinationError


def test_host_operation_dispatch_rejects_incomplete_and_unsupported_intents(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    run_id = engine.start("Exercise host operation recovery").run.id

    cases: tuple[
        tuple[HostOperationKind, str | None, dict[str, Any], str], ...
    ] = (
        (HostOperationKind.INTEGRATION_PREPARE, None, {}, "RECOVERY_UNAVAILABLE"),
        (HostOperationKind.INTEGRATION_PREPARE, run_id, {}, "RECOVERY_UNAVAILABLE"),
        (HostOperationKind.RELEASE_PREPARE, None, {}, "RECOVERY_UNAVAILABLE"),
        (HostOperationKind.RELEASE_PREPARE, run_id, {}, "RECOVERY_UNAVAILABLE"),
        (HostOperationKind.RELEASE_PUBLISH, None, {}, "RECOVERY_UNAVAILABLE"),
        (HostOperationKind.RELEASE_PUBLISH, run_id, {}, "RECOVERY_UNAVAILABLE"),
        (HostOperationKind.VERIFICATION_PREPARE, None, {}, "RECOVERY_UNAVAILABLE"),
        (HostOperationKind.DATABASE_MIGRATE, run_id, {}, "CONFIG_INVALID"),
    )
    for index, (kind, operation_run_id, request, code) in enumerate(cases):
        operation = _pending(
            engine,
            kind=kind,
            key=f"invalid-{index}",
            run_id=operation_run_id,
            request=request,
        )
        with pytest.raises(WorkflowError) as failed:
            _execute(engine, operation)
        assert failed.value.code == code
        assert engine.operations.get(operation.operation_id).status.value == "failed"


def test_host_operation_replay_and_idempotency_are_fail_closed(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    run_id = engine.start("Exercise host operation replay").run.id
    pending = _pending(
        engine,
        kind=HostOperationKind.DATABASE_MIGRATE,
        key="completed",
        run_id=run_id,
        request={},
    )
    running = engine.operations.acquire(
        pending.operation_id,
        expected_version=0,
        lease_owner="os:fixture",
    )
    succeeded = engine.operations.mark_succeeded(
        running.operation_id,
        expected_version=running.state_version,
        lease_owner="os:fixture",
        result={"done": True},
    )
    result = engine.execute_host_operation(
        succeeded.operation_id,
        expected_operation_version=succeeded.state_version,
        idempotency_key=succeeded.idempotency_key,
        invocation=_context(),
    )
    assert result.run.id == run_id

    conflict = _pending(
        engine,
        kind=HostOperationKind.DATABASE_MIGRATE,
        key="wrong-key",
        run_id=run_id,
        request={},
    )
    with pytest.raises(WorkflowError) as wrong_key:
        engine.execute_host_operation(
            conflict.operation_id,
            expected_operation_version=0,
            idempotency_key="different",
            invocation=_context(),
        )
    assert wrong_key.value.code == "IDEMPOTENCY_CONFLICT"


@pytest.mark.parametrize("outcome_unknown", [False, True])
def test_verification_prepare_failure_records_retry_state(
    tmp_path: Path, outcome_unknown: bool
) -> None:
    engine = _engine(tmp_path)
    run_id = engine.start(f"Verification failure {outcome_unknown}").run.id
    engine.verification_cache_preparer = cast(
        VerificationCachePreparer,
        _FailingCachePreparer(outcome_unknown=outcome_unknown),
    )
    operation = _pending(
        engine,
        kind=HostOperationKind.VERIFICATION_PREPARE,
        key=f"verification-{outcome_unknown}",
        run_id=run_id,
        request={},
    )
    with pytest.raises(WorkflowError) as failed:
        _execute(engine, operation)
    assert failed.value.code == (
        "RECONCILIATION_REQUIRED" if outcome_unknown else "DEPENDENCY_UNVERIFIED"
    )
    assert engine.operations.get(operation.operation_id).status.value == (
        "reconcile_required" if outcome_unknown else "failed"
    )


@pytest.mark.parametrize(
    ("governance_code", "workflow_code", "status"),
    [
        ("REMOTE_UNREACHABLE", "RECONCILIATION_REQUIRED", "reconcile_required"),
        ("VERSION_MISMATCH", "VERSION_MISMATCH", "failed"),
    ],
)
def test_release_publish_failure_records_retry_state(
    tmp_path: Path,
    governance_code: str,
    workflow_code: str,
    status: str,
) -> None:
    engine = _engine(tmp_path)
    run_id = engine.start(f"Publish failure {governance_code}").run.id
    engine.g4_publisher = _FailingPublisher(governance_code)
    operation = _pending(
        engine,
        kind=HostOperationKind.RELEASE_PUBLISH,
        key=f"publish-{governance_code}",
        run_id=run_id,
        request={"approval": _approval_payload()},
    )
    with pytest.raises(WorkflowError) as failed:
        _execute(engine, operation)
    assert failed.value.code == workflow_code
    assert engine.operations.get(operation.operation_id).status.value == status


@pytest.mark.parametrize("coordination_code", ["REMOTE_UNREACHABLE", "MERGE_CONFLICT"])
def test_integration_merge_failure_records_retry_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    coordination_code: str,
) -> None:
    engine = _engine(tmp_path)
    run_id = engine.start(f"Merge failure {coordination_code}").run.id

    def fail_merge(_service: CoordinationService, _operation: HostOperation) -> None:
        raise CoordinationError(coordination_code, "simulated merge failure")

    monkeypatch.setattr(CoordinationService, "execute_merge_operation", fail_merge)
    operation = _pending(
        engine,
        kind=HostOperationKind.INTEGRATION_MERGE,
        key=f"merge-{coordination_code}",
        run_id=run_id,
        request={},
    )
    with pytest.raises(WorkflowError) as failed:
        _execute(engine, operation)
    assert failed.value.code == (
        "RECONCILIATION_REQUIRED"
        if coordination_code == "REMOTE_UNREACHABLE"
        else coordination_code
    )
    assert engine.operations.get(operation.operation_id).status.value == (
        "reconcile_required"
        if coordination_code == "REMOTE_UNREACHABLE"
        else "failed"
    )


def test_release_cache_binding_requires_valid_succeeded_operation(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    run_id = engine.start("Exercise release cache binding").run.id
    engine.config = engine.config.model_copy(
        update={"git_push_policy": GitPushPolicy.REMOTE_REQUIRED}
    )
    with pytest.raises(WorkflowError) as missing_operation:
        engine._release_cache_binding(run_id)
    assert missing_operation.value.code == "SANDBOX_DEPENDENCIES_UNAVAILABLE"

    operation = _pending(
        engine,
        kind=HostOperationKind.VERIFICATION_PREPARE,
        key="cache-binding",
        run_id=run_id,
        request={},
    )
    running = engine.operations.acquire(
        operation.operation_id,
        expected_version=0,
        lease_owner="os:fixture",
    )
    succeeded = engine.operations.mark_succeeded(
        running.operation_id,
        expected_version=running.state_version,
        lease_owner="os:fixture",
        result={},
    )
    with engine.store.database.connection() as connection:
        connection.execute(
            "UPDATE host_operations SET result_json = '[]' WHERE operation_id = ?",
            (succeeded.operation_id,),
        )
        connection.commit()
    with pytest.raises(WorkflowError) as invalid_result:
        engine._release_cache_binding(run_id)
    assert invalid_result.value.code == "DEPENDENCY_UNVERIFIED"

    with engine.store.database.connection() as connection:
        connection.execute(
            "UPDATE host_operations SET result_json = '{}' WHERE operation_id = ?",
            (succeeded.operation_id,),
        )
        connection.commit()
    with pytest.raises(WorkflowError) as missing_lock:
        engine._release_cache_binding(run_id)
    assert missing_lock.value.code == "DEPENDENCY_UNVERIFIED"

    lock_hash = "a" * 64
    with engine.store.database.connection() as connection:
        connection.execute(
            "UPDATE host_operations SET result_json = ? WHERE operation_id = ?",
            (json.dumps({"uv_lock_hash": lock_hash}), succeeded.operation_id),
        )
        connection.commit()
    with pytest.raises(WorkflowError) as missing_manifest:
        engine._release_cache_binding(run_id)
    assert missing_manifest.value.code == "SANDBOX_DEPENDENCIES_UNAVAILABLE"

    manifest = (
        engine.config.root
        / ".codex-os"
        / "cache"
        / "verification"
        / lock_hash
        / "verification-cache-manifest.json"
    )
    manifest.parent.mkdir(parents=True)
    manifest.write_bytes(b'{"approved":true}\n')
    assert engine._release_cache_binding(run_id) == {
        "operation_id": succeeded.operation_id,
        "operation_request_hash": succeeded.request_hash,
        "uv_lock_hash": lock_hash,
        "manifest_hash": hashlib.sha256(manifest.read_bytes()).hexdigest(),
    }


class _FailingCachePreparer:
    def __init__(self, *, outcome_unknown: bool) -> None:
        self.outcome_unknown = outcome_unknown

    def prepare(self, _operation: HostOperation) -> dict[str, Any]:
        raise VerificationCachePrepareError(
            "DEPENDENCY_UNVERIFIED",
            "simulated verification cache failure",
            outcome_unknown=self.outcome_unknown,
        )


class _FailingPublisher(G4Publisher):
    def __init__(self, code: str) -> None:
        self.code = code

    def verify_publication_intent(
        self, run: WorkflowRun, approval: G4ApprovalInput
    ) -> dict[str, object]:
        del run, approval
        raise AssertionError("publication verification should not be called directly")

    def authorize_and_publish(
        self, run: WorkflowRun, approval: G4ApprovalInput
    ) -> G4PublicationResult:
        del run, approval
        raise ReleaseGovernanceError(self.code, "simulated publication failure")


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
            agent="fixture-agent",
            task_id=task_id,
            branch=f"agent/fixture-agent/{task_id}",
            path=Path("C:/fixture") / run_id / task_id,
            base_commit=base_ref,
        )
        return WorktreeState(spec=spec, head_commit=base_ref, dirty=False)


def _engine(tmp_path: Path) -> WorkflowEngine:
    root = tmp_path / "project"
    ProjectInitializer().initialize(
        root,
        project_id="PROJECT-OPERATION-FAILURES",
        name="Operation failure fixture",
        project_type=ProjectType.BACKEND,
        schema_version="1.0",
    )
    return WorkflowEngine(
        root,
        worktrees=cast(TaskWorktreeAllocator, _NoopWorktrees()),
    )


def _pending(
    engine: WorkflowEngine,
    *,
    kind: HostOperationKind,
    key: str,
    run_id: str | None,
    request: dict[str, Any],
) -> HostOperation:
    return engine.operations.ensure_pending(
        project_id=engine.config.project_id,
        run_id=run_id,
        kind=kind,
        idempotency_key=key,
        request=request,
    )


def _execute(engine: WorkflowEngine, operation: HostOperation) -> WorkflowResult:
    return engine.execute_host_operation(
        operation.operation_id,
        expected_operation_version=operation.state_version,
        idempotency_key=operation.idempotency_key,
        invocation=_context(),
    )


def _context() -> InvocationContext:
    return InvocationContext.local(InvocationSource.CLI)


def _approval_payload() -> dict[str, object]:
    return {
        "pr_number": 42,
        "pr_url": "https://github.com/example/ai-os/pull/42",
        "merge_commit": "e" * 40,
        "version": "0.2.0",
        "release_authority": {
            "authorized": True,
            "scope": "tag-and-github-release",
            "authorized_by": "release-owner",
        },
    }
