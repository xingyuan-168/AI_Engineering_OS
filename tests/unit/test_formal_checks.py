# pyright: reportPrivateUsage=false
from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from codex_ai_os.application import formal_checks
from codex_ai_os.application.formal_checks import (
    FormalCheckError,
    check_build_install,
    check_documents,
    check_image_sbom,
    check_image_scan,
    check_real_oci,
)
from codex_ai_os.infrastructure.documents import DocumentCheckReport


def test_formal_image_evidence_requires_cyclonedx_and_zero_blockers(
    tmp_path: Path,
) -> None:
    (tmp_path / "image-sbom.cdx.json").write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "serialNumber": "urn:uuid:00000000-0000-0000-0000-000000000001",
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "image-scan.json").write_text(
        json.dumps({"Results": [{"Vulnerabilities": []}]}), encoding="utf-8"
    )

    check_image_sbom(tmp_path)
    check_image_scan(tmp_path)

    (tmp_path / "image-scan.json").write_text(
        json.dumps(
            {
                "Results": [
                    {
                        "Vulnerabilities": [
                            {"VulnerabilityID": "CVE-FIXTURE", "Severity": "CRITICAL"}
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(FormalCheckError, match="CVE-FIXTURE"):
        check_image_scan(tmp_path)


def test_formal_image_evidence_rejects_unstructured_reports(tmp_path: Path) -> None:
    (tmp_path / "image-sbom.cdx.json").write_text("{}", encoding="utf-8")
    (tmp_path / "image-scan.json").write_text("{}", encoding="utf-8")

    with pytest.raises(FormalCheckError, match="CycloneDX"):
        check_image_sbom(tmp_path)
    with pytest.raises(FormalCheckError, match="Results"):
        check_image_scan(tmp_path)


def test_formal_document_and_build_checks_cover_success_and_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    valid_report = DocumentCheckReport(
        ok=True,
        checked_files=1,
        missing=(),
        broken_links=(),
        invalid_documents=(),
        forbidden_directories=(),
    )

    class _Documents:
        def __init__(self, root: Path) -> None:
            assert root == tmp_path

        def check(self, project_type: str) -> DocumentCheckReport:
            assert project_type == "backend"
            return valid_report

    def load_config(_path: Path, _model: type[Any]) -> Any:
        return SimpleNamespace(project_type=SimpleNamespace(value="backend"))

    monkeypatch.setattr(formal_checks, "load_yaml_model", load_config)
    monkeypatch.setattr(formal_checks, "DocumentManager", _Documents)
    check_documents(tmp_path)

    class _InvalidDocuments(_Documents):
        def check(self, project_type: str) -> DocumentCheckReport:
            return replace(valid_report, ok=False, missing=("docs/API_SPEC.md",))

    monkeypatch.setattr(formal_checks, "DocumentManager", _InvalidDocuments)
    with pytest.raises(FormalCheckError, match="API_SPEC"):
        check_documents(tmp_path)

    calls: list[list[str]] = []

    def build_runner(
        command: list[str], *, cwd: Path, environment: dict[str, str] | None = None
    ) -> None:
        calls.append(command)
        assert cwd.is_dir()
        if "build" in command:
            dist = Path(command[command.index("--outdir") + 1])
            (dist / "fixture.whl").write_bytes(b"wheel")
            (dist / "fixture.tar.gz").write_bytes(b"sdist")
        if environment is not None:
            assert "PYTHONPATH" in environment

    monkeypatch.setattr(formal_checks, "_run", build_runner)
    check_build_install(tmp_path)
    assert len(calls) == 3

    def no_build(
        _command: list[str], *, cwd: Path, environment: dict[str, str] | None = None
    ) -> None:
        del cwd, environment

    monkeypatch.setattr(formal_checks, "_run", no_build)
    with pytest.raises(FormalCheckError, match="exactly one"):
        check_build_install(tmp_path)


def test_formal_cli_and_process_failures_are_structured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert formal_checks.main([]) == 2
    assert formal_checks.main(["unknown", str(tmp_path)]) == 1

    monkeypatch.delenv("container", raising=False)
    with pytest.raises(FormalCheckError, match="not running inside"):
        check_real_oci()
    monkeypatch.setenv("container", "podman")
    with pytest.raises(FormalCheckError, match="Windows host"):
        check_real_oci()

    def successful_process(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess([], 0, b"ok", b"")

    monkeypatch.setattr(formal_checks.subprocess, "run", successful_process)
    formal_checks._run(["tool", "ok"], cwd=tmp_path)
    def failed_process(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess([], 7, b"", b"failed")

    monkeypatch.setattr(formal_checks.subprocess, "run", failed_process)
    with pytest.raises(FormalCheckError, match="failed"):
        formal_checks._run(["tool", "fail"], cwd=tmp_path)

    (tmp_path / "object.json").write_text("{}", encoding="utf-8")
    assert formal_checks._json_object(tmp_path / "object.json") == {}
    (tmp_path / "object.json").write_text("[]", encoding="utf-8")
    with pytest.raises(FormalCheckError, match="object"):
        formal_checks._json_object(tmp_path / "object.json")

    def fail_check(_root: Path) -> None:
        raise OSError("fixture unavailable")

    monkeypatch.setattr(formal_checks, "check_documents", fail_check)
    assert formal_checks.main(["docs", str(tmp_path)]) == 1

    dispatched: list[str] = []

    def dispatch_build_install(_root: Path) -> None:
        dispatched.append("build-install")

    def dispatch_real_oci() -> None:
        dispatched.append("real-oci")

    def dispatch_image_sbom(_root: Path) -> None:
        dispatched.append("image-sbom")

    def dispatch_image_scan(_root: Path) -> None:
        dispatched.append("image-scan")

    monkeypatch.setattr(
        formal_checks,
        "check_build_install",
        dispatch_build_install,
    )
    monkeypatch.setattr(formal_checks, "check_real_oci", dispatch_real_oci)
    monkeypatch.setattr(
        formal_checks,
        "check_image_sbom",
        dispatch_image_sbom,
    )
    monkeypatch.setattr(
        formal_checks,
        "check_image_scan",
        dispatch_image_scan,
    )
    for name in ("build-install", "real-oci", "image-sbom", "image-scan"):
        assert formal_checks.main([name, str(tmp_path)]) == 0
    assert dispatched == ["build-install", "real-oci", "image-sbom", "image-scan"]
