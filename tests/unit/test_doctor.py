from __future__ import annotations

import subprocess

import pytest

from codex_ai_os.application import doctor as doctor_module
from codex_ai_os.application.doctor import DoctorService


def test_doctor_reports_missing_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_which(name: str) -> str | None:
        return None if name in {"docker", "podman"} else f"C:/{name}.exe"

    def fake_run(command: tuple[str, ...], *, timeout: int) -> subprocess.CompletedProcess[str]:
        del timeout
        return subprocess.CompletedProcess(command, 0, "version", "")

    monkeypatch.setattr(doctor_module.shutil, "which", fake_which)
    monkeypatch.setattr(doctor_module, "_run_command", fake_run)

    report = DoctorService().run()

    assert not report.ok
    assert not report.sandbox_available


def test_doctor_passes_when_required_tools_are_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_which(name: str) -> str:
        return f"C:/{name}.exe"

    def fake_run(command: tuple[str, ...], *, timeout: int) -> subprocess.CompletedProcess[str]:
        del timeout
        return subprocess.CompletedProcess(command, 0, "version", "")

    monkeypatch.setattr(doctor_module.shutil, "which", fake_which)
    monkeypatch.setattr(doctor_module, "_run_command", fake_run)

    report = DoctorService().run()

    assert report.ok
    assert report.sandbox_available


def test_doctor_accepts_podman_when_docker_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_which(name: str) -> str | None:
        return None if name == "docker" else f"C:/{name}.exe"

    def fake_run(command: tuple[str, ...], *, timeout: int) -> subprocess.CompletedProcess[str]:
        del timeout
        return subprocess.CompletedProcess(command, 0, "version", "")

    monkeypatch.setattr(doctor_module.shutil, "which", fake_which)
    monkeypatch.setattr(doctor_module, "_run_command", fake_run)

    report = DoctorService().run()

    assert report.ok
    assert report.sandbox_available
    assert next(check for check in report.checks if check.name == "podman").ok
