"""Stable diagnostic codes: exception prose and paths are never parsers."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class DiagnosticError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        path: Path | str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.path = str(path) if path is not None else None
        self.details = details or {}


def error_details(exc: Exception) -> dict[str, Any]:
    return {"path": getattr(exc, "path", None), **getattr(exc, "details", {})}
