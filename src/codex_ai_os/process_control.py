"""Owned external command trees. The bootstrap waits until containment is established."""

from __future__ import annotations

import base64
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

_BOOTSTRAP = """
import base64, json, pathlib, subprocess, sys, time
p = json.loads(sys.stdin.readline())
error = None
if time.monotonic() >= p['deadline']:
    error = {'status': 'timed_out', 'message': 'deadline expired before command start'}
else:
    try:
        result = subprocess.run(p['command'], shell=isinstance(p['command'], str),
                                input=base64.b64decode(p['input']))
    except OSError as exc:
        error = {'status': 'unavailable', 'message': str(exc),
                 'exception_type': type(exc).__name__, 'errno': exc.errno,
                 'winerror': getattr(exc, 'winerror', None), 'path': exc.filename}
if error is not None:
    pathlib.Path(p['diagnostic']).write_text(json.dumps(error), encoding='utf-8')
    sys.exit(127)
sys.exit(result.returncode)
"""


class CommandStopped(RuntimeError):
    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.details: dict[str, Any] = {}


class WindowsJob:
    """Kill-on-close Job; no breakaway flags. No user command runs before assignment."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes as w

        class Basic(ctypes.Structure):
            _fields_ = [
                ("user", ctypes.c_longlong),
                ("job", ctypes.c_longlong),
                ("flags", w.DWORD),
                ("min_ws", ctypes.c_size_t),
                ("max_ws", ctypes.c_size_t),
                ("active", w.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", w.DWORD),
                ("scheduling", w.DWORD),
            ]

        class IO(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_ulonglong)
                for name in ("read", "write", "other", "read_bytes", "write_bytes", "other_bytes")
            ]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("basic", Basic),
                ("io", IO),
                ("process_mem", ctypes.c_size_t),
                ("job_mem", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t),
                ("peak_job", ctypes.c_size_t),
            ]

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [w.LPVOID, w.LPCWSTR]
        self.api.CreateJobObjectW.restype = w.HANDLE
        self.api.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD]
        self.api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.api.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
        self.api.CloseHandle.argtypes = [w.HANDLE]
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limit = Extended()
        limit.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limit), ctypes.sizeof(limit)
        ):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, process: subprocess.Popen) -> None:
        import ctypes

        if not self.api.AssignProcessToJobObject(self.handle, int(cast(Any, process)._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def terminate(self) -> None:
        import ctypes

        if not self.api.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())
        # Query active-process accounting before closing the handle.
        from ctypes import wintypes as w

        class Accounting(ctypes.Structure):
            _fields_ = [
                ("user", ctypes.c_longlong),
                ("kernel", ctypes.c_longlong),
                ("period_user", ctypes.c_longlong),
                ("period_kernel", ctypes.c_longlong),
                ("faults", w.DWORD),
                ("total", w.DWORD),
                ("active", w.DWORD),
                ("terminated", w.DWORD),
            ]

        self.api.QueryInformationJobObject.argtypes = [
            w.HANDLE,
            ctypes.c_int,
            w.LPVOID,
            w.DWORD,
            w.LPVOID,
        ]
        deadline = time.monotonic() + 5
        while True:
            info = Accounting()
            if not self.api.QueryInformationJobObject(
                self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if not info.active:
                return
            if time.monotonic() >= deadline:
                raise CommandStopped("failed", "owned command tree did not terminate")
            time.sleep(0.02)

    def close(self) -> None:
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def run_owned(
    command,
    *,
    cwd: Path,
    timeout: float,
    cancel: threading.Event | None = None,
    on_spawn=None,
    text_output: bool = True,
    input_data: bytes | str | None = None,
) -> subprocess.CompletedProcess:
    deadline = time.monotonic() + timeout
    if cancel is not None and cancel.is_set():
        raise CommandStopped("cancelled", "request cancelled before command start")
    job = None
    process = None
    assigned = False
    stopped = None
    with (
        tempfile.TemporaryDirectory(prefix="codex-command-") as directory,
        tempfile.TemporaryFile() as stdout,
        tempfile.TemporaryFile() as stderr,
    ):
        diagnostic = Path(directory) / "launch.json"
        try:
            job = WindowsJob() if os.name == "nt" else None
            process = subprocess.Popen(
                [sys.executable, "-c", _BOOTSTRAP],
                cwd=cwd,
                stdin=subprocess.PIPE,
                stdout=stdout,
                stderr=stderr,
                start_new_session=os.name != "nt",
            )
            if job:
                job.assign(process)
                assigned = True
            if on_spawn:
                on_spawn(process.pid)
            if cancel and cancel.is_set():
                raise CommandStopped("cancelled", "request cancelled before command start")
            if time.monotonic() >= deadline:
                raise CommandStopped("timed_out", "deadline expired before command start")
            assert process.stdin is not None
            raw = input_data.encode("utf-8") if isinstance(input_data, str) else input_data or b""
            payload = {
                "command": command,
                "input": base64.b64encode(raw).decode("ascii"),
                "deadline": deadline,
                "diagnostic": str(diagnostic),
            }
            process.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
            process.stdin.close()
            while process.poll() is None:
                if cancel and cancel.is_set():
                    stopped = CommandStopped("cancelled", "request was cancelled")
                    break
                if time.monotonic() >= deadline:
                    stopped = CommandStopped("timed_out", "external command exceeded its deadline")
                    break
                time.sleep(0.03)
        finally:
            try:
                if process:
                    if job:
                        if assigned:
                            job.terminate()
                        else:
                            process.kill()  # exact process handle created by this invocation
                    else:
                        with suppress(ProcessLookupError):
                            os.killpg(process.pid, signal.SIGKILL)  # pyright: ignore[reportAttributeAccessIssue]
                    process.wait(timeout=5)
                    if not job:
                        _confirm_group_exit(process.pid)
            finally:
                if job:
                    job.close()
        stdout.seek(0)
        stderr.seek(0)
        raw_output, raw_errors = stdout.read(), stderr.read()
        output = raw_output.decode("utf-8", errors="replace")
        errors = raw_errors.decode("utf-8", errors="replace")
        if not stopped and diagnostic.exists():
            launch = json.loads(diagnostic.read_text(encoding="utf-8"))
            stopped = CommandStopped(launch.pop("status"), launch.pop("message"))
            stopped.details.update(launch)
        if stopped:
            stopped.details.update(stdout=output, stderr=errors)
            raise stopped
        assert process is not None
        return subprocess.CompletedProcess(
            command,
            process.returncode,
            output if text_output else raw_output,
            errors if text_output else raw_errors,
        )


def _confirm_group_exit(pgid: int) -> None:
    deadline = time.monotonic() + 5
    while True:
        try:
            os.killpg(pgid, 0)  # pyright: ignore[reportAttributeAccessIssue]
        except ProcessLookupError:
            return
        if time.monotonic() >= deadline:
            raise CommandStopped("failed", "owned process group exit could not be confirmed")
        time.sleep(0.02)
