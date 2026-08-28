"""SQLite persistence, leasing, idempotency, and reconciliation for host operations."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from codex_ai_os.domain.ids import new_id
from codex_ai_os.domain.operations import (
    HostOperation,
    HostOperationKind,
    HostOperationStatus,
    ReconciliationOutcome,
)
from codex_ai_os.infrastructure.database import Database


class HostOperationError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class HostOperationStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def ensure_pending(
        self,
        *,
        project_id: str,
        kind: HostOperationKind,
        idempotency_key: str,
        request: dict[str, Any],
        run_id: str | None = None,
        task_id: str | None = None,
        task_group_id: str | None = None,
        handoff_id: str | None = None,
        release_id: str | None = None,
        expected_state_version: int | None = None,
        expected_task_version: int | None = None,
    ) -> HostOperation:
        with self.database.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                operation = self.ensure_pending_in_transaction(
                    connection,
                    project_id=project_id,
                    kind=kind,
                    idempotency_key=idempotency_key,
                    request=request,
                    run_id=run_id,
                    task_id=task_id,
                    task_group_id=task_group_id,
                    handoff_id=handoff_id,
                    release_id=release_id,
                    expected_state_version=expected_state_version,
                    expected_task_version=expected_task_version,
                )
                connection.commit()
            except (sqlite3.Error, HostOperationError):
                connection.rollback()
                raise
        return operation

    def ensure_pending_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        project_id: str,
        kind: HostOperationKind,
        idempotency_key: str,
        request: dict[str, Any],
        run_id: str | None = None,
        task_id: str | None = None,
        task_group_id: str | None = None,
        handoff_id: str | None = None,
        release_id: str | None = None,
        expected_state_version: int | None = None,
        expected_task_version: int | None = None,
    ) -> HostOperation:
        """Create/replay an intent inside the caller's approval transaction."""

        if not connection.in_transaction:
            raise HostOperationError(
                "CONFIG_INVALID", "host operation intent requires an active transaction"
            )
        normalized_key = idempotency_key.strip()
        if not normalized_key:
            raise HostOperationError("CONFIG_INVALID", "idempotency_key is required")
        request_hash = _request_hash(request)
        existing = connection.execute(
            "SELECT * FROM host_operations WHERE project_id = ? AND kind = ? "
            "AND idempotency_key = ?",
            (project_id, kind.value, normalized_key),
        ).fetchone()
        if existing is not None:
            if str(existing["request_hash"]) != request_hash:
                raise HostOperationError(
                    "IDEMPOTENCY_CONFLICT",
                    "idempotency key was already used with a different request",
                )
            return _from_row(existing)
        now = _utc_now()
        operation_id = new_id("OP")
        connection.execute(
            """
            INSERT INTO host_operations(
                operation_id, project_id, run_id, task_id, task_group_id,
                handoff_id, release_id, kind, idempotency_key, request_hash,
                status, expected_state_version, expected_task_version,
                state_version, lease_owner, lease_expires_at, attempt_count,
                request_json, result_json, error_code, created_at, started_at,
                ended_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, 0,
                      NULL, NULL, 0, ?, '{}', NULL, ?, NULL, NULL, ?)
            """,
            (
                operation_id,
                project_id,
                run_id,
                task_id,
                task_group_id,
                handoff_id,
                release_id,
                kind.value,
                normalized_key,
                request_hash,
                expected_state_version,
                expected_task_version,
                _json(_redact(request)),
                now,
                now,
            ),
        )
        row = connection.execute(
            "SELECT * FROM host_operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        assert row is not None
        return _from_row(row)

    def get(self, operation_id: str) -> HostOperation:
        with self.database.read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM host_operations WHERE operation_id = ?", (operation_id,)
            ).fetchone()
        if row is None:
            raise HostOperationError(
                "RECOVERY_UNAVAILABLE", f"host operation not found: {operation_id}"
            )
        return _from_row(row)

    def find_by_idempotency(
        self,
        *,
        project_id: str,
        kind: HostOperationKind,
        idempotency_key: str,
    ) -> HostOperation | None:
        with self.database.read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM host_operations WHERE project_id = ? AND kind = ? "
                "AND idempotency_key = ?",
                (project_id, kind.value, idempotency_key.strip()),
            ).fetchone()
        return _from_row(row) if row is not None else None

    def acquire(
        self,
        operation_id: str,
        *,
        expected_version: int,
        lease_owner: str,
        lease_seconds: int = 60,
    ) -> HostOperation:
        if not lease_owner.strip() or lease_seconds < 1 or lease_seconds > 3600:
            raise HostOperationError("CONFIG_INVALID", "invalid operation lease")
        now = _now()
        now_text = now.isoformat()
        expires = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self.database.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = self._required_row(connection, operation_id)
                self._require_version(row, expected_version)
                status = HostOperationStatus(str(row["status"]))
                if status is HostOperationStatus.SUCCEEDED:
                    connection.rollback()
                    return _from_row(row)
                if status is HostOperationStatus.RECONCILE_REQUIRED:
                    raise HostOperationError(
                        "RECONCILIATION_REQUIRED",
                        "operation outcome must be reconciled before retry",
                    )
                if status is HostOperationStatus.RUNNING:
                    lease_expires = str(row["lease_expires_at"] or "")
                    if lease_expires > now_text:
                        raise HostOperationError(
                            "OPERATION_LEASED",
                            "host operation has an active lease",
                            retryable=True,
                        )
                    connection.execute(
                        "UPDATE host_operations SET status = 'reconcile_required', "
                        "state_version = state_version + 1, lease_owner = NULL, "
                        "lease_expires_at = NULL, error_code = ?, updated_at = ? "
                        "WHERE operation_id = ? AND state_version = ?",
                        (
                            "LEASE_EXPIRED_OUTCOME_UNKNOWN",
                            now_text,
                            operation_id,
                            expected_version,
                        ),
                    )
                    connection.commit()
                    raise HostOperationError(
                        "RECONCILIATION_REQUIRED",
                        "expired running operation may have applied its side effect",
                    )
                changed = connection.execute(
                    """
                    UPDATE host_operations
                    SET status = 'running', state_version = state_version + 1,
                        lease_owner = ?, lease_expires_at = ?,
                        attempt_count = attempt_count + 1,
                        started_at = COALESCE(started_at, ?), ended_at = NULL,
                        error_code = NULL, updated_at = ?
                    WHERE operation_id = ? AND state_version = ?
                      AND status IN ('pending', 'failed')
                    """,
                    (
                        lease_owner,
                        expires,
                        now_text,
                        now_text,
                        operation_id,
                        expected_version,
                    ),
                ).rowcount
                if changed != 1:
                    raise HostOperationError(
                        "STATE_VERSION_CONFLICT", "host operation changed during acquire"
                    )
                connection.commit()
            except (sqlite3.Error, HostOperationError):
                if connection.in_transaction:
                    connection.rollback()
                raise
        return self.get(operation_id)

    def mark_succeeded(
        self,
        operation_id: str,
        *,
        expected_version: int,
        lease_owner: str,
        result: dict[str, Any],
    ) -> HostOperation:
        return self._finish(
            operation_id,
            expected_version=expected_version,
            lease_owner=lease_owner,
            status=HostOperationStatus.SUCCEEDED,
            result=result,
            error_code=None,
        )

    def mark_failed(
        self,
        operation_id: str,
        *,
        expected_version: int,
        lease_owner: str,
        error_code: str,
        result: dict[str, Any] | None = None,
    ) -> HostOperation:
        if not error_code.strip():
            raise HostOperationError("CONFIG_INVALID", "error_code is required")
        return self._finish(
            operation_id,
            expected_version=expected_version,
            lease_owner=lease_owner,
            status=HostOperationStatus.FAILED,
            result=result or {},
            error_code=error_code,
        )

    def mark_outcome_unknown(
        self,
        operation_id: str,
        *,
        expected_version: int,
        lease_owner: str,
        error_code: str = "OUTCOME_UNKNOWN",
    ) -> HostOperation:
        return self._finish(
            operation_id,
            expected_version=expected_version,
            lease_owner=lease_owner,
            status=HostOperationStatus.RECONCILE_REQUIRED,
            result={},
            error_code=error_code,
        )

    def require_reconciliation(
        self,
        operation_id: str,
        *,
        expected_version: int,
        error_code: str = "OUTCOME_UNKNOWN",
    ) -> HostOperation:
        """Move an unfinished operation to reconciliation after external inspection."""

        if not error_code.strip():
            raise HostOperationError("CONFIG_INVALID", "error_code is required")
        now = _utc_now()
        with self.database.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = self._required_row(connection, operation_id)
                self._require_version(row, expected_version)
                status = HostOperationStatus(str(row["status"]))
                if status is HostOperationStatus.RECONCILE_REQUIRED:
                    connection.commit()
                    return _from_row(row)
                if status not in {
                    HostOperationStatus.PENDING,
                    HostOperationStatus.RUNNING,
                    HostOperationStatus.FAILED,
                }:
                    raise HostOperationError(
                        "STATE_VERSION_CONFLICT",
                        "completed operation cannot require reconciliation",
                    )
                changed = connection.execute(
                    """
                    UPDATE host_operations
                    SET status = 'reconcile_required',
                        state_version = state_version + 1,
                        lease_owner = NULL, lease_expires_at = NULL,
                        error_code = ?, updated_at = ?
                    WHERE operation_id = ? AND state_version = ?
                      AND status IN ('pending', 'running', 'failed')
                    """,
                    (error_code, now, operation_id, expected_version),
                ).rowcount
                if changed != 1:
                    raise HostOperationError(
                        "STATE_VERSION_CONFLICT",
                        "operation changed while requiring reconciliation",
                    )
                connection.commit()
            except (sqlite3.Error, HostOperationError):
                connection.rollback()
                raise
        return self.get(operation_id)

    def reconcile(
        self,
        operation_id: str,
        *,
        expected_version: int,
        outcome: ReconciliationOutcome,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> HostOperation:
        status = {
            ReconciliationOutcome.SUCCEEDED: HostOperationStatus.SUCCEEDED,
            ReconciliationOutcome.NOT_APPLIED: HostOperationStatus.PENDING,
            ReconciliationOutcome.FAILED: HostOperationStatus.FAILED,
        }[outcome]
        now = _utc_now()
        with self.database.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = self._required_row(connection, operation_id)
                self._require_version(row, expected_version)
                if (
                    HostOperationStatus(str(row["status"]))
                    is not HostOperationStatus.RECONCILE_REQUIRED
                ):
                    raise HostOperationError(
                        "STATE_VERSION_CONFLICT", "operation does not require reconciliation"
                    )
                changed = connection.execute(
                    """
                    UPDATE host_operations
                    SET status = ?, state_version = state_version + 1,
                        result_json = ?, error_code = ?, lease_owner = NULL,
                        lease_expires_at = NULL, ended_at = ?, updated_at = ?
                    WHERE operation_id = ? AND state_version = ?
                      AND status = 'reconcile_required'
                    """,
                    (
                        status.value,
                        _json(result or {}),
                        error_code,
                        now if status is not HostOperationStatus.PENDING else None,
                        now,
                        operation_id,
                        expected_version,
                    ),
                ).rowcount
                if changed != 1:
                    raise HostOperationError(
                        "STATE_VERSION_CONFLICT", "operation changed during reconciliation"
                    )
                connection.commit()
            except (sqlite3.Error, HostOperationError):
                connection.rollback()
                raise
        return self.get(operation_id)

    def recoverable(self, run_id: str, *, limit: int = 4) -> tuple[HostOperation, ...]:
        if limit < 1 or limit > 4:
            raise HostOperationError("CONFIG_INVALID", "recovery limit must be 1..4")
        with self.database.read_connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM host_operations
                WHERE run_id = ?
                  AND status IN ('pending', 'running', 'failed', 'reconcile_required')
                ORDER BY created_at, operation_id LIMIT ?
                """,
                (run_id, limit),
            ).fetchall()
        return tuple(_from_row(row) for row in rows)

    def _finish(
        self,
        operation_id: str,
        *,
        expected_version: int,
        lease_owner: str,
        status: HostOperationStatus,
        result: dict[str, Any],
        error_code: str | None,
    ) -> HostOperation:
        now = _utc_now()
        with self.database.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = self._required_row(connection, operation_id)
                self._require_version(row, expected_version)
                if str(row["lease_owner"] or "") != lease_owner:
                    raise HostOperationError(
                        "OPERATION_LEASED", "operation lease owner does not match"
                    )
                changed = connection.execute(
                    """
                    UPDATE host_operations
                    SET status = ?, state_version = state_version + 1,
                        result_json = ?, error_code = ?, lease_owner = NULL,
                        lease_expires_at = NULL, ended_at = ?, updated_at = ?
                    WHERE operation_id = ? AND state_version = ? AND status = 'running'
                    """,
                    (
                        status.value,
                        _json(result),
                        error_code,
                        now,
                        now,
                        operation_id,
                        expected_version,
                    ),
                ).rowcount
                if changed != 1:
                    raise HostOperationError(
                        "STATE_VERSION_CONFLICT", "operation changed during completion"
                    )
                connection.commit()
            except (sqlite3.Error, HostOperationError):
                connection.rollback()
                raise
        return self.get(operation_id)

    @staticmethod
    def _required_row(
        connection: sqlite3.Connection, operation_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM host_operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        if row is None:
            raise HostOperationError(
                "RECOVERY_UNAVAILABLE", f"host operation not found: {operation_id}"
            )
        return row

    @staticmethod
    def _require_version(row: sqlite3.Row, expected: int) -> None:
        actual = int(row["state_version"])
        if actual != expected:
            raise HostOperationError(
                "STATE_VERSION_CONFLICT",
                f"expected operation version {expected}, found {actual}",
                retryable=True,
            )


def _from_row(row: sqlite3.Row) -> HostOperation:
    request = cast(dict[str, Any], json.loads(str(row["request_json"])))
    result = cast(dict[str, Any], json.loads(str(row["result_json"])))
    return HostOperation(
        operation_id=str(row["operation_id"]),
        project_id=str(row["project_id"]),
        run_id=str(row["run_id"]) if row["run_id"] is not None else None,
        task_id=str(row["task_id"]) if row["task_id"] is not None else None,
        task_group_id=(
            str(row["task_group_id"]) if row["task_group_id"] is not None else None
        ),
        handoff_id=str(row["handoff_id"]) if row["handoff_id"] is not None else None,
        release_id=str(row["release_id"]) if row["release_id"] is not None else None,
        kind=HostOperationKind(str(row["kind"])),
        idempotency_key=str(row["idempotency_key"]),
        request_hash=str(row["request_hash"]),
        status=HostOperationStatus(str(row["status"])),
        expected_state_version=(
            int(row["expected_state_version"])
            if row["expected_state_version"] is not None
            else None
        ),
        expected_task_version=(
            int(row["expected_task_version"])
            if row["expected_task_version"] is not None
            else None
        ),
        state_version=int(row["state_version"]),
        lease_owner=str(row["lease_owner"]) if row["lease_owner"] is not None else None,
        lease_expires_at=(
            str(row["lease_expires_at"]) if row["lease_expires_at"] is not None else None
        ),
        attempt_count=int(row["attempt_count"]),
        request=request,
        result=result,
        error_code=str(row["error_code"]) if row["error_code"] is not None else None,
        created_at=str(row["created_at"]),
        started_at=str(row["started_at"]) if row["started_at"] is not None else None,
        ended_at=str(row["ended_at"]) if row["ended_at"] is not None else None,
        updated_at=str(row["updated_at"]),
    )


def _request_hash(request: dict[str, Any]) -> str:
    return hashlib.sha256(_json(request).encode("utf-8")).hexdigest()


def _redact(value: object) -> object:
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        return {
            str(key): (
                "[REDACTED]" if _sensitive_key(str(key)) else _redact(item)
            )
            for key, item in mapping.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in cast(list[object] | tuple[object, ...], value)]
    return value


def _sensitive_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    return any(
        marker in normalized
        for marker in ("token", "password", "secret", "authorization", "api_key")
    )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now() -> datetime:
    return datetime.now(UTC)


def _utc_now() -> str:
    return _now().isoformat()


__all__ = ["HostOperationError", "HostOperationStore"]
