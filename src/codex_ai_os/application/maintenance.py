"""Explicit maintenance operation scheduling for Plugin API 1.2."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from codex_ai_os.application.verification_cache import (
    DEFAULT_VERIFICATION_PLATFORM,
    DEFAULT_VERIFICATION_PYTHON,
    VerificationCachePrepareError,
    validate_verification_target,
)
from codex_ai_os.domain.config import RiskLevel
from codex_ai_os.domain.invocation import InvocationContext
from codex_ai_os.domain.operations import (
    HostOperation,
    HostOperationKind,
    HostOperationStatus,
    ReconciliationOutcome,
)
from codex_ai_os.domain.versions import RUNTIME_VERSIONS
from codex_ai_os.domain.workflow import ActionKind, NextAction
from codex_ai_os.infrastructure.config import load_project_config
from codex_ai_os.infrastructure.database import Database, MigrationResult
from codex_ai_os.infrastructure.operations import HostOperationError, HostOperationStore
from codex_ai_os.infrastructure.workflows import WorkflowNotFoundError, WorkflowStore


class MaintenanceOperationError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class ScheduledOperation:
    operation: HostOperation

    @property
    def next_action(self) -> NextAction:
        return host_operation_action(self.operation)


@dataclass(frozen=True, slots=True)
class DatabaseMigrationCommandResult:
    migration: MigrationResult
    operation: HostOperation


@dataclass(frozen=True, slots=True)
class ReconciledOperation:
    operation: HostOperation

    @property
    def next_action(self) -> NextAction | None:
        if self.operation.status is HostOperationStatus.PENDING:
            return host_operation_action(self.operation)
        return None


class VerificationPrepareService:
    """Persist the approved network preparation intent before cache side effects."""

    def __init__(self, project_root: Path) -> None:
        self.config = load_project_config(project_root.resolve())
        self.database = Database(self.config.root / ".codex-os" / "state" / "state.db")
        self.operations = HostOperationStore(self.database)
        self.workflows = WorkflowStore(self.database)

    def prepare(
        self,
        *,
        run_id: str,
        expected_state_version: int,
        idempotency_key: str,
        network_approval_ref: str,
        expires_at: str,
        target_python: str = DEFAULT_VERIFICATION_PYTHON,
        platform: str = DEFAULT_VERIFICATION_PLATFORM,
    ) -> ScheduledOperation:
        if not network_approval_ref.strip():
            raise MaintenanceOperationError(
                "APPROVAL_REQUIRED", "network_approval_ref is required"
            )
        if not expires_at.strip():
            raise MaintenanceOperationError("CONFIG_INVALID", "expires_at is required")
        try:
            validate_verification_target(platform, target_python, expires_at)
        except VerificationCachePrepareError as exc:
            raise MaintenanceOperationError(exc.code, str(exc)) from exc
        lock_path = self.config.root / "uv.lock"
        if not lock_path.is_file():
            raise MaintenanceOperationError(
                "DEPENDENCY_UNVERIFIED", "uv.lock is required for verification prepare"
            )
        try:
            run = self.workflows.get_run(run_id)
        except WorkflowNotFoundError as exc:
            raise MaintenanceOperationError("RECOVERY_UNAVAILABLE", str(exc)) from exc
        if run.project_id != self.config.project_id:
            raise MaintenanceOperationError(
                "STATE_VERSION_CONFLICT", "workflow belongs to a different project"
            )
        if run.state_version != expected_state_version:
            raise MaintenanceOperationError(
                "STATE_VERSION_CONFLICT",
                f"expected workflow state version {expected_state_version}, "
                f"found {run.state_version}",
                retryable=True,
            )
        request = {
            "schema_version": RUNTIME_VERSIONS.api,
            "run_id": run_id,
            "expected_state_version": expected_state_version,
            "network_approval_ref": network_approval_ref,
            "expires_at": expires_at,
            "target_python": target_python,
            "platform": platform,
            "uv_lock_hash": _sha256(lock_path),
            "execution_image": RUNTIME_VERSIONS.execution_image,
        }
        try:
            operation = self.operations.ensure_pending(
                project_id=self.config.project_id,
                run_id=run_id,
                kind=HostOperationKind.VERIFICATION_PREPARE,
                idempotency_key=idempotency_key,
                request=request,
                expected_state_version=expected_state_version,
            )
        except HostOperationError as exc:
            raise MaintenanceOperationError(
                exc.code, str(exc), retryable=exc.retryable
            ) from exc
        return ScheduledOperation(operation)


class DatabaseMigrationService:
    """Run explicit database migration and persist an auditable operation record."""

    def __init__(self, project_root: Path) -> None:
        self.config = load_project_config(project_root.resolve())
        self.database = Database(self.config.root / ".codex-os" / "state" / "state.db")

    def migrate(
        self,
        *,
        expected_schema_version: str,
        target_schema_version: str,
        idempotency_key: str,
        invocation: InvocationContext,
    ) -> DatabaseMigrationCommandResult:
        if target_schema_version != RUNTIME_VERSIONS.sqlite_schema:
            raise MaintenanceOperationError(
                "CONFIG_INVALID",
                f"unsupported target schema {target_schema_version}; "
                f"expected {RUNTIME_VERSIONS.sqlite_schema}",
            )
        current = self.database.current_version() or "none"
        if current != expected_schema_version:
            raise MaintenanceOperationError(
                "STATE_VERSION_CONFLICT",
                f"expected SQLite schema {expected_schema_version}, found {current}",
                retryable=True,
            )
        migration = self.database.migrate(applied_by=invocation.principal)
        request = {
            "schema_version": RUNTIME_VERSIONS.api,
            "expected_schema_version": expected_schema_version,
            "target_schema_version": target_schema_version,
            "previous_schema_version": current,
            "applied_versions": list(migration.applied_versions),
            "backup_path": (
                migration.backup_path.as_posix()
                if migration.backup_path is not None
                else None
            ),
        }
        operations = HostOperationStore(self.database)
        try:
            operation = operations.ensure_pending(
                project_id=self.config.project_id,
                kind=HostOperationKind.DATABASE_MIGRATE,
                idempotency_key=idempotency_key,
                request=request,
            )
            if operation.status is HostOperationStatus.SUCCEEDED:
                return DatabaseMigrationCommandResult(migration, operation)
            acquired = operations.acquire(
                operation.operation_id,
                expected_version=operation.state_version,
                lease_owner=invocation.principal,
            )
            operation = operations.mark_succeeded(
                acquired.operation_id,
                expected_version=acquired.state_version,
                lease_owner=invocation.principal,
                result=request,
            )
        except HostOperationError as exc:
            raise MaintenanceOperationError(
                exc.code, str(exc), retryable=exc.retryable
            ) from exc
        return DatabaseMigrationCommandResult(migration, operation)


class HostOperationMaintenanceService:
    """Expose explicit reconciliation without replaying unknown side effects."""

    def __init__(self, project_root: Path) -> None:
        self.config = load_project_config(project_root.resolve())
        self.database = Database(self.config.root / ".codex-os" / "state" / "state.db")
        self.database.migrate()
        self.operations = HostOperationStore(self.database)

    def reconcile(
        self,
        *,
        operation_id: str,
        expected_operation_version: int,
        idempotency_key: str,
        outcome: ReconciliationOutcome,
        error_code: str | None = None,
    ) -> ReconciledOperation:
        try:
            operation = self.operations.get(operation_id)
            if operation.project_id != self.config.project_id:
                raise HostOperationError(
                    "RECOVERY_UNAVAILABLE",
                    "host operation belongs to a different project",
                )
            if operation.idempotency_key != idempotency_key:
                raise HostOperationError(
                    "IDEMPOTENCY_CONFLICT",
                    "host operation idempotency key does not match persisted intent",
                )
            reconciled = self.operations.reconcile(
                operation_id,
                expected_version=expected_operation_version,
                outcome=outcome,
                error_code=error_code,
            )
        except HostOperationError as exc:
            raise MaintenanceOperationError(
                exc.code, str(exc), retryable=exc.retryable
            ) from exc
        return ReconciledOperation(reconciled)


def host_operation_action(operation: HostOperation) -> NextAction:
    dependencies = tuple(
        item
        for item in (operation.handoff_id, operation.release_id, operation.task_id)
        if item is not None
    )
    return NextAction(
        kind=ActionKind.HOST_OPERATION,
        operation_id=operation.operation_id,
        task_id=operation.task_id,
        task_group_id=operation.task_group_id,
        dependencies=dependencies,
        prompt=f"Execute or reconcile persisted {operation.kind.value} Host Operation.",
        risk_level=RiskLevel.HIGH,
        requires_repository_change=operation.kind is HostOperationKind.DATABASE_MIGRATE,
        expected_state_version=operation.expected_state_version,
        expected_operation_version=operation.state_version,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


__all__ = [
    "DatabaseMigrationCommandResult",
    "DatabaseMigrationService",
    "HostOperationMaintenanceService",
    "MaintenanceOperationError",
    "ReconciledOperation",
    "ScheduledOperation",
    "VerificationPrepareService",
    "host_operation_action",
]
