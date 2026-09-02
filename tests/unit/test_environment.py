import json
import subprocess
from pathlib import Path

import yaml

from codex_ai_os.application.environment import EnvironmentGovernanceService
from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.domain.config import ProjectType

_DIGEST = "a" * 64


def _runner(argv: list[str], _cwd: Path, _timeout: float) -> subprocess.CompletedProcess[bytes]:
    if argv[1:3] == ["system", "df"]:
        payload = {"Images": [{"Size": 11}], "BuildCache": [], "Volumes": [{"Size": 7}]}
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload).encode(), b"")
    return subprocess.CompletedProcess(argv, 0, b"ok", b"")


def _valid_project(root: Path) -> None:
    ProjectInitializer().initialize(
        root,
        project_id="PROJECT-ENV",
        name="Environment fixture",
        project_type=ProjectType.BACKEND,
    )
    (root / "docker" / "app.Dockerfile").write_text(
        f"FROM python:3.12-bookworm@sha256:{_DIGEST}\n",
        encoding="utf-8",
    )
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (root / "compose.yaml").write_text(
        "name: environment-fixture\n"
        "services:\n"
        "  app:\n"
        "    build:\n"
        "      context: .\n"
        "      dockerfile: docker/app.Dockerfile\n"
        "    healthcheck:\n"
        "      test: [CMD, python, --version]\n"
        "    networks: [default]\n"
        "networks:\n"
        "  default:\n"
        "    internal: true\n",
        encoding="utf-8",
    )
    environment: dict[str, object] = {
        "schema_version": "1.2",
        "environment_mode": "oci-first",
        "oci_backend": "podman",
        "compose_files": ["compose.yaml"],
        "dockerfiles": ["docker/app.Dockerfile"],
        "dependency_locks": ["uv.lock"],
        "services": [
            {
                "name": "app",
                "dockerfile": "docker/app.Dockerfile",
                "smoke_command": ["python", "--version"],
            }
        ],
        "persistent_mounts": [],
        "shared_assets": [],
        "host_budget": {},
    }
    (root / ".codex-os" / "environment.yaml").write_text(
        yaml.safe_dump(environment, sort_keys=False), encoding="utf-8"
    )


def test_environment_check_accepts_rebuildable_contract(tmp_path: Path) -> None:
    _valid_project(tmp_path)

    report = EnvironmentGovernanceService(tmp_path, runner=_runner).check()

    assert report.ok
    assert report.backend_available
    assert report.compose_available
    assert report.compose_hashes["compose.yaml"]
    assert report.disk_usage.oci_images_bytes == 11
    assert report.disk_usage.oci_volumes_bytes == 7


def test_environment_check_reports_legacy_adoption_without_writing(tmp_path: Path) -> None:
    _valid_project(tmp_path)
    project_path = tmp_path / ".codex-os" / "project.yaml"
    project = yaml.safe_load(project_path.read_text(encoding="utf-8"))
    project.pop("environment_mode")
    project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
    before = (tmp_path / ".codex-os" / "environment.yaml").read_bytes()

    report = EnvironmentGovernanceService(tmp_path, runner=_runner).check()

    assert not report.ok
    assert report.findings[0].code == "ENVIRONMENT_ADOPTION_REQUIRED"
    assert (tmp_path / ".codex-os" / "environment.yaml").read_bytes() == before


def test_environment_check_never_falls_back_to_docker(tmp_path: Path) -> None:
    _valid_project(tmp_path)
    calls: list[list[str]] = []

    def runner(argv: list[str], _cwd: Path, _timeout: float) -> subprocess.CompletedProcess[bytes]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, b"", b"unavailable")

    report = EnvironmentGovernanceService(tmp_path, runner=runner).check()

    assert not report.backend_available
    assert any(item.code == "OCI_BACKEND_UNAVAILABLE" for item in report.findings)
    assert calls == [["podman", "--version"], ["podman", "system", "df", "--format", "json"]]
    assert all(call[0] != "docker" for call in calls)


def test_environment_check_detects_host_dependencies(tmp_path: Path) -> None:
    _valid_project(tmp_path)
    dependency = tmp_path / ".venv"
    dependency.mkdir()
    (dependency / "package.bin").write_bytes(b"dependency")

    report = EnvironmentGovernanceService(tmp_path, runner=_runner).check()

    finding = next(item for item in report.findings if item.code == "HOST_DEPENDENCY_PRESENT")
    assert finding.path == ".venv"
    assert report.disk_usage.host_dependency_bytes == len(b"dependency")


def test_environment_check_requires_digest_and_healthcheck(tmp_path: Path) -> None:
    _valid_project(tmp_path)
    (tmp_path / "docker" / "app.Dockerfile").write_text(
        "FROM python:3.12-bookworm\n", encoding="utf-8"
    )
    compose = yaml.safe_load((tmp_path / "compose.yaml").read_text(encoding="utf-8"))
    compose["services"]["app"].pop("healthcheck")
    (tmp_path / "compose.yaml").write_text(
        yaml.safe_dump(compose, sort_keys=False), encoding="utf-8"
    )

    report = EnvironmentGovernanceService(tmp_path, runner=_runner).check()

    codes = [item.code for item in report.findings]
    assert codes.count("ENVIRONMENT_NOT_REBUILDABLE") >= 2
