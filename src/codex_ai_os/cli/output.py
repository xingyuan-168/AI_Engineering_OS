"""Stable CLI response envelope and rendering."""

from __future__ import annotations

import json
from typing import Any

import typer

from codex_ai_os.application.responses import (
    error_envelope as _error_envelope,
)
from codex_ai_os.application.responses import (
    success_envelope as _success_envelope,
)

error_envelope = _error_envelope
success_envelope = _success_envelope


def emit(payload: dict[str, Any], *, json_output: bool, human: str) -> None:
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    else:
        typer.echo(human)
