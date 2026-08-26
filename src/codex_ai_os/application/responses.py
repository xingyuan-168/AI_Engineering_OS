"""Shared API 1.2 response envelope for CLI and MCP adapters."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from codex_ai_os.domain.invocation import InvocationContext, InvocationSource
from codex_ai_os.domain.versions import RUNTIME_VERSIONS


def success_envelope(
    data: dict[str, Any],
    *,
    context: InvocationContext | None = None,
    run_id: str | None = None,
    run_status: str | None = None,
    workflow_phase: str | None = None,
    state_version: int | None = None,
    next_actions: Iterable[dict[str, Any]] = (),
    warnings: Iterable[str] = (),
) -> dict[str, Any]:
    trusted = context or InvocationContext.local(InvocationSource.CLI)
    actions = list(next_actions)
    return {
        "api_version": RUNTIME_VERSIONS.api,
        "ok": True,
        "request_id": trusted.request_id,
        "correlation_id": trusted.correlation_id,
        "run_id": run_id,
        "workflow_phase": workflow_phase,
        "run_status": run_status,
        "state_version": state_version,
        "data": data,
        "next_actions": actions,
        "next_action": actions[0] if len(actions) == 1 else None,
        "warnings": list(warnings),
        "error": None,
    }


def error_envelope(
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    *,
    context: InvocationContext | None = None,
    retryable: bool = False,
    warnings: Iterable[str] = (),
) -> dict[str, Any]:
    trusted = context or InvocationContext.local(InvocationSource.CLI)
    return {
        "api_version": RUNTIME_VERSIONS.api,
        "ok": False,
        "request_id": trusted.request_id,
        "correlation_id": trusted.correlation_id,
        "run_id": None,
        "workflow_phase": None,
        "run_status": None,
        "state_version": None,
        "data": {},
        "next_actions": [],
        "next_action": None,
        "warnings": list(warnings),
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "retryable": retryable,
        },
    }


__all__ = ["error_envelope", "success_envelope"]
