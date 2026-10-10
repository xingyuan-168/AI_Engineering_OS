"""Single internal Git subprocess wrapper shared across the runtime."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from codex_ai_os.adapters.process import COMMAND_CANCEL, ExecutionStopped, owned_run
from codex_ai_os.infrastructure.errors import DiagnosticError


def ignored_untracked_paths(git: GitRunner, paths: list[str]) -> set[str]:
    """Ask Git, including negations; no probe files or index changes are needed."""
    ignored = git.run(
        "check-ignore", "--no-index", "-z", "--stdin", input_data="\0".join(paths) + "\0"
    )
    tracked = git.run("ls-files", "--cached", "-z", "--", *(":(literal)" + p for p in paths))
    if ignored.returncode not in {0, 1} or tracked.returncode:
        raise DiagnosticError(
            "cannot verify runtime path ignore/tracking status",
            code="GIT_IGNORE_CHECK_FAILED",
            details={
                "ignore_exit": ignored.returncode,
                "tracked_exit": tracked.returncode,
                "stderr": ignored.stderr + tracked.stderr,
            },
        )
    return (set(ignored.stdout.split("\0")) - set(tracked.stdout.split("\0"))) - {""}


@dataclass(frozen=True, slots=True)
class GitRunner:
    """Run Git commands in one repository without shell interpolation."""

    root: Path

    def run(
        self, *args: str, timeout: float = 30.0, input_data: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        return self._execute(args, timeout, input_data, True)

    def run_bytes(
        self, *args: str, timeout: float = 30.0, input_data: bytes | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        return self._execute(args, timeout, input_data, False)

    def _execute(self, args, timeout, input_data, text_output):
        command = ["git", "-C", str(self.root), *args]
        try:
            return owned_run(
                command,
                cwd=self.root,
                timeout=timeout,
                input_data=input_data,
                text_output=text_output,
            )
        except ExecutionStopped as exc:
            if COMMAND_CANCEL.get() is not None:
                raise
            if exc.status == "timed_out":
                raise subprocess.TimeoutExpired(
                    command, timeout, exc.details.get("stdout"), exc.details.get("stderr")
                ) from exc
            raise DiagnosticError(str(exc), code="GIT_CONTROL_FAILED", details=exc.details) from exc
        except OSError as exc:
            if COMMAND_CANCEL.get() is not None:
                raise ExecutionStopped(
                    "unavailable", "Git control/start failed: " + str(exc)
                ) from exc
            raise
