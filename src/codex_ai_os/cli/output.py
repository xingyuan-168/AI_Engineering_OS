"""Stable CLI response envelope and rendering (self-contained, ADR-0016)."""

from __future__ import annotations

import json
from typing import Any

import typer


def success_envelope(data: dict[str, Any], *, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": True, "data": data}
    if meta:
        payload["meta"] = meta
    return payload


def error_envelope(
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    *,
    retryable: bool = False,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message, "retryable": retryable}
    if details is not None:
        error["details"] = details
    return {"ok": False, "error": error}


def emit(payload: dict[str, Any], *, json_output: bool, human: str) -> None:
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    else:
        typer.echo(human)
