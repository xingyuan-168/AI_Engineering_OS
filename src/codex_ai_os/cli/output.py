"""Stable CLI response envelope and rendering."""

from __future__ import annotations

import json
from typing import Any

import typer

from codex_ai_os.domain.ids import new_id


def success_envelope(
    data: dict[str, Any],
    *,
    run_id: str | None = None,
    run_status: str | None = None,
    workflow_phase: str | None = None,
    state_version: int | None = None,
    next_action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": True,
        "request_id": new_id("REQ"),
        "run_id": run_id,
        "run_status": run_status,
        "workflow_phase": workflow_phase,
        "state_version": state_version,
        "next_action": next_action,
        "data": data,
        "error": None,
    }


def error_envelope(code: str, message: str, details: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": False,
        "request_id": new_id("REQ"),
        "run_id": None,
        "run_status": None,
        "workflow_phase": None,
        "state_version": None,
        "next_action": None,
        "data": {},
        "error": {"code": code, "message": message, "details": details},
    }


def emit(payload: dict[str, Any], *, json_output: bool, human: str) -> None:
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    else:
        typer.echo(human)
