"""Durable host-operation contracts for external side effects."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from codex_ai_os.domain.config import StrictModel


class HostOperationKind(StrEnum):
    INTEGRATION_PREPARE = "integration_prepare"
    INTEGRATION_MERGE = "integration_merge"
    RELEASE_PREPARE = "release_prepare"
    RELEASE_PUBLISH = "release_publish"
    VERIFICATION_PREPARE = "verification_prepare"
    DATABASE_MIGRATE = "database_migrate"


class HostOperationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RECONCILE_REQUIRED = "reconcile_required"


class ReconciliationOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    NOT_APPLIED = "not_applied"
    FAILED = "failed"


class HostOperation(StrictModel):
    operation_id: str
    project_id: str
    run_id: str | None = None
    task_id: str | None = None
    task_group_id: str | None = None
    handoff_id: str | None = None
    release_id: str | None = None
    kind: HostOperationKind
    idempotency_key: str
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: HostOperationStatus
    expected_state_version: int | None = Field(default=None, ge=0)
    expected_task_version: int | None = Field(default=None, ge=0)
    state_version: int = Field(ge=0)
    lease_owner: str | None = None
    lease_expires_at: str | None = None
    attempt_count: int = Field(ge=0)
    request: dict[str, Any]
    result: dict[str, Any]
    error_code: str | None = None
    created_at: str
    started_at: str | None = None
    ended_at: str | None = None
    updated_at: str


__all__ = [
    "HostOperation",
    "HostOperationKind",
    "HostOperationStatus",
    "ReconciliationOutcome",
]
