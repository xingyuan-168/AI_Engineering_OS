"""Environment diagnostics for the supported Windows runtime."""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    name: str
    required: bool
    ok: bool
    detail: str


@dataclass(frozen=True, slots=True)
class DoctorReport:
    checks: tuple[DoctorCheck, ...]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks if check.required)

    @property
    def sandbox_available(self) -> bool:
        return any(check.name == "docker" and check.ok for check in self.checks)


class DoctorService:
    def run(self) -> DoctorReport:
        return DoctorReport(
            checks=(
                self._python_check(),
                self._command_check("git", required=True, version_args=("--version",)),
                self._uv_check(),
                self._sqlite_check(),
                self._docker_check(),
                self._command_check("codex", required=False, version_args=("--version",)),
            )
        )

    @staticmethod
    def _python_check() -> DoctorCheck:
        version = sys.version_info
        ok = version.major == 3 and version.minor == 12
        return DoctorCheck(
            name="python",
            required=True,
            ok=ok,
            detail=f"{version.major}.{version.minor}.{version.micro}",
        )

    def _uv_check(self) -> DoctorCheck:
        executable = shutil.which("uv")
        if executable is None:
            candidate = Path.home() / ".local" / "bin" / "uv.exe"
            executable = str(candidate) if candidate.is_file() else None
        return self._executable_check("uv", executable, required=True, version_args=("--version",))

    def _docker_check(self) -> DoctorCheck:
        executable = shutil.which("docker")
        if executable is None:
            return DoctorCheck("docker", True, False, "Docker Desktop CLI not found")
        result = _run_command((executable, "info", "--format", "{{.ServerVersion}}"), timeout=10)
        return DoctorCheck(
            name="docker",
            required=True,
            ok=result.returncode == 0,
            detail=(result.stdout.strip() or result.stderr.strip() or "Docker daemon unavailable"),
        )

    @staticmethod
    def _sqlite_check() -> DoctorCheck:
        try:
            with sqlite3.connect(":memory:") as connection:
                connection.execute("CREATE VIRTUAL TABLE memory_probe USING fts5(content)")
            return DoctorCheck("sqlite-fts5", True, True, sqlite3.sqlite_version)
        except sqlite3.Error as exc:
            return DoctorCheck("sqlite-fts5", True, False, str(exc))

    def _command_check(
        self,
        name: str,
        *,
        required: bool,
        version_args: tuple[str, ...],
    ) -> DoctorCheck:
        return self._executable_check(
            name,
            shutil.which(name),
            required=required,
            version_args=version_args,
        )

    @staticmethod
    def _executable_check(
        name: str,
        executable: str | None,
        *,
        required: bool,
        version_args: tuple[str, ...],
    ) -> DoctorCheck:
        if executable is None:
            return DoctorCheck(name, required, False, "executable not found")
        result = _run_command((executable, *version_args), timeout=10)
        detail = result.stdout.strip() or result.stderr.strip() or executable
        return DoctorCheck(name, required, result.returncode == 0, detail)


def _run_command(command: tuple[str, ...], *, timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(command, 1, "", str(exc))
