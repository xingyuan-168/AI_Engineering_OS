"""Finish adapter diagnostics, not Gate state, resumable jobs or a permission cache."""

from __future__ import annotations

import ctypes
import json
import os
import threading
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from codex_ai_os.adapters.git import GitRunner, ignored_untracked_paths
from codex_ai_os.adapters.process import COMMAND_CANCEL, ExecutionStopped, owned_run
from codex_ai_os.infrastructure.errors import DiagnosticError
from codex_ai_os.infrastructure.files import (
    ExecutionLock,
    atomic_text,
    execution_lock_active,
    file_lock,
)
from codex_ai_os.runtime_identity import runtime_identity

STEPS = (
    "task_diff",
    "test_command",
    "ruff",
    "git_diff",
    "hygiene",
    "disposables",
    "memory_candidates",
    "code_start",
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def process_identity(pid: int) -> str | None:
    if os.name == "nt":
        from ctypes import wintypes as w

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        api.OpenProcess.restype = w.HANDLE
        api.GetProcessTimes.argtypes = [w.HANDLE, w.LPVOID, w.LPVOID, w.LPVOID, w.LPVOID]
        api.GetExitCodeProcess.argtypes = [w.HANDLE, w.LPVOID]
        api.CloseHandle.argtypes = [w.HANDLE]
        handle = api.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            exit_code = w.DWORD()
            if (
                not api.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                or exit_code.value != 259
            ):
                return None
            times = [ctypes.c_ulonglong() for _ in range(4)]
            if not api.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                return None
            return str(times[0].value)
        finally:
            api.CloseHandle(handle)
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except OSError:
        return None


def _record_path(root: Path, run_id: str) -> Path:
    try:
        value = str(UUID(run_id))
    except ValueError as exc:
        raise DiagnosticError("run_id must be a UUID", code="RUN_ID_INVALID") from exc
    path = root.resolve() / ".codex-os/state/check-runs" / (value + ".json")
    _safe_record_path(path)
    return path


def _safe_record_path(path: Path) -> None:
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise DiagnosticError("run record traverses a reparse point", code="RUN_PATH_UNSAFE")


class FinishExecution:
    def __init__(
        self,
        root: Path,
        *,
        base_ref,
        command,
        run_id: str | None = None,
        cancel: threading.Event | None = None,
        on_progress=None,
    ) -> None:
        self.root = root.resolve()
        self.path = _record_path(root, run_id or str(uuid4()))
        for path in (self.path.with_suffix(".lock"), self.path.parent / ".lock"):
            _safe_record_path(path)
        paths = [
            p.relative_to(self.root).as_posix()
            for p in (self.path, self.path.with_suffix(".lock"), self.path.parent / ".lock")
        ]
        if set(paths) != ignored_untracked_paths(GitRunner(self.root), paths):
            raise DiagnosticError(
                "Finish records and locks must be ignored and untracked",
                code="RUN_PATH_NOT_IGNORED",
                path=self.path,
            )
        self.run_id = self.path.stem
        self.cancel = cancel or threading.Event()
        self.current = None
        self.on_progress = on_progress
        self.lock = threading.RLock()
        self.execution_lock = None
        self.record = {
            "record_version": 2,
            "run_id": self.run_id,
            "checkout_root": str(self.root),
            "base_ref": base_ref,
            "test_command": command,
            "runtime": runtime_identity(),
            "started_at": _now(),
            "ended_at": None,
            "owner": {"pid": os.getpid(), "identity": process_identity(os.getpid())},
            "execution_status": "running",
            "decision": None,
            "steps": {name: {"status": "not_run"} for name in STEPS},
        }
        try:
            with file_lock(self.path.parent / ".lock"):
                if self.path.exists() or self.path.with_suffix(".lock").exists():
                    raise DiagnosticError(
                        "run_id already exists; query it or choose a new UUID", code="RUN_ID_EXISTS"
                    )
                self.execution_lock = ExecutionLock(self.path.with_suffix(".lock"))
                self.save()
        except OSError as exc:
            self.close()
            raise DiagnosticError(
                "cannot persist Finish run",
                code="RUN_WRITE_FAILED",
                path=self.path,
                details=_storage_details(exc, self.path),
            ) from exc
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self.execution_lock is not None:
            self.execution_lock.close()

    def save(self) -> None:
        try:
            atomic_text(self.path, json.dumps(self.record, ensure_ascii=False, indent=2) + "\n")
        except OSError as exc:
            raise DiagnosticError(
                "cannot persist Finish run",
                code="RUN_WRITE_FAILED",
                path=self.path,
                details=_storage_details(exc, self.path),
            ) from exc

    def step(self, name, operation):
        if self.cancel.is_set():
            raise ExecutionStopped("cancelled", "Finish request cancelled")
        with self.lock:
            self.current = name
            self.record["steps"][name] = {"status": "running", "started_at": _now()}
            self.save()
        if self.on_progress:
            self.on_progress(STEPS.index(name), name + ": running")
        try:
            result = operation()
            if self.cancel.is_set():
                raise ExecutionStopped("cancelled", "Finish request was cancelled")
        except BaseException:
            self.record["steps"][name].update(status="interrupted", ended_at=_now())
            raise
        with self.lock:
            step = self.record["steps"][name]
            step.update(
                status="failed"
                if isinstance(result, list) and any(f.blocking for f in result)
                else "passed",
                ended_at=_now(),
            )
            if isinstance(result, list):
                step["findings"] = [asdict(f) for f in result]
                if any(f.code.endswith("SKIPPED") for f in result):
                    step["status"] = "skipped"
            if name == "task_diff":
                self.record["resolved_base"] = result[0]
                self.record["formal_paths"] = result[1]
            if name == "code_start" and not result and not self.record.get("formal_paths"):
                step["status"] = "skipped"
            self.save()
        if self.on_progress:
            self.on_progress(STEPS.index(name) + 1, name + ": " + step["status"])
        unavailable = (
            next((f for f in result if f.code.endswith("UNAVAILABLE")), None)
            if isinstance(result, list)
            else None
        )
        if unavailable:
            raise ExecutionStopped("unavailable", unavailable.message)
        return result

    def command(self, command, *, cwd, timeout):
        def spawned(pid):
            self.record["steps"][self.current]["process"] = {
                "pid": pid,
                "identity": process_identity(pid),
            }
            self.save()  # failure stops the waiting bootstrap before executing the command

        try:
            result = owned_run(
                command, cwd=cwd, timeout=timeout, cancel=self.cancel, on_spawn=spawned
            )
        except ExecutionStopped as exc:
            self.record["steps"][self.current].update(exc.details)
            raise
        except OSError as exc:
            raise ExecutionStopped(
                "unavailable", "external command could not start: " + str(exc)
            ) from exc
        self.record["steps"][self.current].update(
            exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr
        )
        return result

    def end(self, *, decision=None, error: BaseException | None = None):
        with self.lock:
            if self.record["execution_status"] != "running":
                return self.record
            previous = deepcopy(self.record)
            status = (
                error.status
                if isinstance(error, ExecutionStopped)
                else "cancelled"
                if isinstance(error, KeyboardInterrupt)
                else "failed"
                if error
                else "completed"
            )
            self.record.update(
                execution_status=status,
                ended_at=_now(),
                decision=asdict(decision) if decision else None,
            )
            if error:
                self.record["error"] = {
                    "code": getattr(error, "code", "FINISH_EXECUTION_FAILED"),
                    "message": str(error),
                    "details": {
                        "path": getattr(error, "path", None),
                        **getattr(error, "details", {}),
                    },
                }
                if self.current:
                    self.record["steps"][self.current].update(status=status, ended_at=_now())
            try:
                self.save()
            except DiagnosticError:
                self.record = previous
                raise
            self.close()
            return self.record


def query_execution(root: Path, run_id: str) -> dict:
    path = _record_path(root, run_id)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DiagnosticError("Finish run was not found", code="RUN_NOT_FOUND", path=path) from exc
    except (OSError, ValueError) as exc:
        raise DiagnosticError(
            "Finish run is unreadable", code="RUN_READ_FAILED", path=path
        ) from exc
    if (
        not isinstance(record, dict)
        or record.get("execution_status")
        not in {
            "running",
            "completed",
            "failed",
            "timed_out",
            "cancelled",
            "interrupted",
            "unavailable",
        }
        or not isinstance(record.get("owner"), dict)
    ):
        raise DiagnosticError(
            "Finish record structure is invalid", code="RUN_READ_FAILED", path=path
        )
    if record.get("checkout_root") != str(root.resolve()):
        raise DiagnosticError("run belongs to another checkout", code="RUN_ROOT_MISMATCH")
    if record["execution_status"] == "running":
        owner = record["owner"]
        if not isinstance(owner.get("pid"), int) or not isinstance(
            owner.get("identity"), (str, type(None))
        ):
            raise DiagnosticError(
                "Finish owner structure is invalid", code="RUN_READ_FAILED", path=path
            )
        if record.get("record_version") != 2:
            return dict(
                record,
                execution_status="unavailable",
                decision=None,
                error={
                    "code": "RUN_LIVENESS_UNVERIFIABLE",
                    "message": "legacy running record has no invocation liveness proof",
                },
            )
        try:
            _safe_record_path(path.with_suffix(".lock"))
            active = execution_lock_active(path.with_suffix(".lock"))
        except OSError as exc:
            return dict(
                record,
                execution_status="unavailable",
                decision=None,
                error={
                    "code": "RUN_LIVENESS_UNAVAILABLE",
                    "message": str(exc),
                    "details": _storage_details(exc),
                },
            )
        if not active:
            try:
                latest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise DiagnosticError(
                    "cannot reread Finish result", code="RUN_READ_FAILED", path=path
                ) from exc
            if (
                not isinstance(latest, dict)
                or latest.get("checkout_root") != record["checkout_root"]
            ):
                raise DiagnosticError("Finish record changed structure", code="RUN_READ_FAILED")
            if latest.get("execution_status") != "running":
                return query_execution(root, run_id)  # Validate the winning terminal result.
            record = deepcopy(latest)
            record.update(
                execution_status="interrupted",
                decision=None,
                observed_at=_now(),
                error={
                    "code": "FINISH_INTERRUPTED",
                    "message": "invocation ended without a persisted terminal result",
                },
            )
            for step in record.get("steps", {}).values():
                if step.get("status") == "running":
                    step["status"] = "interrupted"
    return record  # query never writes or terminates any PID


def run_finish(
    root: Path,
    *,
    base_ref,
    test_command,
    change_class=None,
    requirement_id=None,
    memory_written=False,
    memory_not_needed=False,
    remote=None,
    run_id=None,
    cancel=None,
    on_progress=None,
) -> dict:
    from codex_ai_os.application.preflight import preflight
    from codex_ai_os.core.gates import evaluate_finish

    checkout = preflight(root).resolved.checkout_root
    execution = FinishExecution(
        checkout,
        base_ref=base_ref,
        command=test_command,
        run_id=run_id,
        cancel=cancel,
        on_progress=on_progress,
    )
    token = COMMAND_CANCEL.set(execution.cancel)
    try:
        decision = evaluate_finish(
            checkout,
            base_ref=base_ref,
            test_command=test_command,
            change_class=change_class,
            requirement_id=requirement_id,
            memory_written=memory_written,
            memory_not_needed=memory_not_needed,
            remote=remote,
            observer=execution,
        )
        record = execution.end(decision=decision)
        return dict(
            record,
            allowed=decision.allowed,
            blocked_by=list(decision.blocked_by),
            findings=[asdict(f) for f in decision.findings],
            gate=decision.gate.value,
        )
    except BaseException as exc:
        try:
            record = execution.end(error=exc)
        except (OSError, DiagnosticError) as persistence_error:
            # The in-memory partial result remains reportable even when the disk failed.
            record = deepcopy(execution.record)
            record.update(
                execution_status="failed",
                ended_at=_now(),
                decision=None,
                error={
                    "code": "RUN_WRITE_FAILED",
                    "message": "cannot persist partial Finish result",
                    "details": getattr(persistence_error, "details", {})
                    or _storage_details(persistence_error),
                },
            )
        if isinstance(exc, (SystemExit, KeyboardInterrupt)):
            raise
        return record
    finally:
        COMMAND_CANCEL.reset(token)
        execution.close()


def _storage_details(exc: BaseException, path: Path | None = None) -> dict:
    return {
        "exception_type": type(exc).__name__,
        "exception": str(exc),
        "errno": getattr(exc, "errno", None),
        "winerror": getattr(exc, "winerror", None),
        "path": getattr(exc, "filename", None) or (str(path) if path else None),
    }
