"""Finish cancellation/diagnostic adapter for the shared stdlib process controller."""

from __future__ import annotations

import threading
from contextvars import ContextVar

from codex_ai_os.infrastructure.errors import DiagnosticError
from codex_ai_os.process_control import CommandStopped, WindowsJob, run_owned

__all__ = ["COMMAND_CANCEL", "ExecutionStopped", "WindowsJob", "owned_run", "raise_if_cancelled"]

COMMAND_CANCEL: ContextVar[threading.Event | None] = ContextVar(
    "finish_command_cancel", default=None
)


class ExecutionStopped(DiagnosticError):
    def __init__(self, status: str, message: str) -> None:
        super().__init__(message, code="FINISH_" + status.upper())
        self.status = status


def raise_if_cancelled() -> None:
    signal = COMMAND_CANCEL.get()
    if signal is not None and signal.is_set():
        raise ExecutionStopped("cancelled", "Finish request was cancelled")


def owned_run(command, *, cancel=None, **kwargs):
    try:
        return run_owned(command, cancel=cancel or COMMAND_CANCEL.get(), **kwargs)
    except CommandStopped as exc:
        error = ExecutionStopped(exc.status, str(exc))
        error.details.update(exc.details)
        raise error from exc
