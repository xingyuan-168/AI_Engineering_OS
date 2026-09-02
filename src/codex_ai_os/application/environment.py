"""Read-only OCI project-environment governance and footprint audit."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml

from codex_ai_os.domain.config import EnvironmentMode
from codex_ai_os.domain.environment import (
    EnvironmentCheckReport,
    EnvironmentContract,
    EnvironmentDiskUsage,
    EnvironmentFinding,
    EnvironmentRepairAction,
)
from codex_ai_os.infrastructure.config import (
    ConfigError,
    load_environment_contract,
    load_project_config,
)

CommandRunner = Callable[[list[str], Path, float], subprocess.CompletedProcess[bytes]]

_HOST_DEPENDENCIES = frozenset(
    {
        ".venv",
        "node_modules",
        "target",
        ".next",
        "build",
        "dist",
        ".gradle",
        ".m2",
        ".npm",
        ".pnpm-store",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
    }
)
_RUNTIME_DIRS = frozenset({"state", "logs", "cache", "tmp", "artifacts", "context"})
_SHARED_ASSET_DIRS = frozenset({"models", "datasets", "checkpoints"})
_DIGEST = re.compile(r"@sha256:[0-9a-fA-F]{64}(?:\s|$)")


class EnvironmentGovernanceError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}
        self.retryable = retryable


class EnvironmentGovernanceService:
    def __init__(
        self,
        project_root: Path,
        *,
        runner: CommandRunner | None = None,
    ) -> None:
        self.root = project_root.resolve()
        self.config = load_project_config(self.root)
        self.runner = runner or _run_command

    def check(self) -> EnvironmentCheckReport:
        findings: list[EnvironmentFinding] = []
        repairs: list[EnvironmentRepairAction] = []
        contract: EnvironmentContract | None = None
        contract_hash: str | None = None
        compose_hashes: dict[str, str] = {}
        backend_available = False
        compose_available = False

        if self.config.environment_mode is EnvironmentMode.LEGACY:
            findings.append(
                _finding(
                    "ENVIRONMENT_ADOPTION_REQUIRED",
                    "legacy project must explicitly adopt the OCI-first environment contract",
                    path=".codex-os/project.yaml",
                )
            )
            repairs.append(
                EnvironmentRepairAction(
                    operation="environment_adopt",
                    path=".codex-os/environment.yaml",
                    expected="governed task commit",
                    actual="missing",
                )
            )
        else:
            try:
                contract = load_environment_contract(self.root)
            except ConfigError as exc:
                findings.append(
                    _finding(
                        "ENVIRONMENT_CONTRACT_MISSING",
                        str(exc),
                        path=".codex-os/environment.yaml",
                    )
                )
                repairs.append(
                    EnvironmentRepairAction(
                        operation="repair_environment_contract",
                        path=".codex-os/environment.yaml",
                    )
                )

        if contract is not None:
            contract_path = self.root / ".codex-os" / "environment.yaml"
            contract_hash = _sha256_file(contract_path)
            backend_available, compose_available = self._backend_checks(contract, findings)
            self._contract_files(contract, findings, repairs, compose_hashes)

        disk_usage = self._host_audit(
            contract.host_budget.large_file_bytes if contract is not None else 52_428_800,
            findings,
            repairs,
        )
        if contract is not None:
            disk_usage = self._with_oci_usage(contract, disk_usage)
            if disk_usage.project_bytes > contract.host_budget.max_project_bytes:
                findings.append(
                    _finding(
                        "HOST_DEPENDENCY_PRESENT",
                        "project directory exceeds its declared host budget",
                        details={
                            "actual_bytes": disk_usage.project_bytes,
                            "max_bytes": contract.host_budget.max_project_bytes,
                        },
                    )
                )
                repairs.append(
                    EnvironmentRepairAction(
                        operation="classify_host_footprint",
                        expected=str(contract.host_budget.max_project_bytes),
                        actual=str(disk_usage.project_bytes),
                    )
                )
            if disk_usage.git_bytes > contract.host_budget.max_git_bytes:
                findings.append(
                    _finding(
                        "ENVIRONMENT_NOT_REBUILDABLE",
                        ".git directory exceeds its declared budget; "
                        "inspect history and LFS policy",
                        path=".git",
                        details={
                            "actual_bytes": disk_usage.git_bytes,
                            "max_bytes": contract.host_budget.max_git_bytes,
                        },
                    )
                )

        ordered = tuple(sorted(findings, key=lambda item: (item.code, item.path or "")))
        return EnvironmentCheckReport(
            ok=not any(item.blocking for item in ordered),
            environment_mode=self.config.environment_mode,
            oci_backend=contract.oci_backend if contract is not None else None,
            backend_available=backend_available,
            compose_available=compose_available,
            contract_hash=contract_hash,
            compose_hashes=compose_hashes,
            disk_usage=disk_usage,
            findings=ordered,
            repair_actions=tuple(repairs),
            checked_at=datetime.now(UTC).isoformat(),
        )

    def require_valid(self) -> EnvironmentCheckReport:
        report = self.check()
        if not report.ok:
            finding = next(item for item in report.findings if item.blocking)
            raise EnvironmentGovernanceError(
                finding.code,
                finding.message,
                details={"path": finding.path, **finding.details},
                retryable=True,
            )
        return report

    def _backend_checks(
        self,
        contract: EnvironmentContract,
        findings: list[EnvironmentFinding],
    ) -> tuple[bool, bool]:
        engine = contract.oci_backend.value
        version = self._run([engine, "--version"])
        backend_available = version.returncode == 0
        if not backend_available:
            findings.append(
                _finding(
                    "OCI_BACKEND_UNAVAILABLE",
                    f"configured OCI backend is unavailable: {engine}",
                    path=".codex-os/environment.yaml",
                    details={"backend": engine},
                )
            )
            return False, False
        compose = self._run([engine, "compose", "version"])
        compose_available = compose.returncode == 0
        if not compose_available:
            findings.append(
                _finding(
                    "OCI_BACKEND_UNAVAILABLE",
                    f"Compose is unavailable for configured backend: {engine}",
                    path=".codex-os/environment.yaml",
                    details={"backend": engine, "component": "compose"},
                )
            )
        return backend_available, compose_available

    def _contract_files(
        self,
        contract: EnvironmentContract,
        findings: list[EnvironmentFinding],
        repairs: list[EnvironmentRepairAction],
        compose_hashes: dict[str, str],
    ) -> None:
        for required in (".dockerignore", "docker/README.md", "docs/ENVIRONMENT.md"):
            if not (self.root / required).is_file():
                findings.append(
                    _finding(
                        "ENVIRONMENT_CONTRACT_MISSING",
                        "required environment artifact is missing",
                        path=required,
                    )
                )
                repairs.append(
                    EnvironmentRepairAction(operation="add_environment_artifact", path=required)
                )

        if not contract.services:
            findings.append(
                _finding(
                    "COMPOSE_INVALID",
                    "environment contract must declare at least one service before G2",
                    path=".codex-os/environment.yaml",
                )
            )
        if not contract.dockerfiles:
            findings.append(
                _finding(
                    "ENVIRONMENT_NOT_REBUILDABLE",
                    "environment contract must declare Dockerfiles before G2",
                    path=".codex-os/environment.yaml",
                )
            )
        if not contract.dependency_locks:
            findings.append(
                _finding(
                    "ENVIRONMENT_NOT_REBUILDABLE",
                    "environment contract must declare dependency lockfiles before G2",
                    path=".codex-os/environment.yaml",
                )
            )

        for relative in contract.dependency_locks:
            if not (self.root / relative).is_file():
                findings.append(
                    _finding(
                        "ENVIRONMENT_NOT_REBUILDABLE",
                        "declared dependency lockfile is missing",
                        path=relative,
                    )
                )
        for relative in contract.dockerfiles:
            path = self.root / relative
            if not path.is_file():
                findings.append(
                    _finding(
                        "ENVIRONMENT_CONTRACT_MISSING",
                        "declared Dockerfile is missing",
                        path=relative,
                    )
                )
                continue
            text = path.read_text(encoding="utf-8")
            from_lines = [
                line.strip()
                for line in text.splitlines()
                if line.lstrip().upper().startswith("FROM ")
            ]
            if not from_lines or any(_DIGEST.search(line) is None for line in from_lines):
                findings.append(
                    _finding(
                        "ENVIRONMENT_NOT_REBUILDABLE",
                        "every Dockerfile FROM must bind a sha256 digest",
                        path=relative,
                    )
                )

        compose_documents: dict[str, Mapping[str, Any]] = {}
        for relative in contract.compose_files:
            path = self.root / relative
            if not path.is_file():
                findings.append(
                    _finding("COMPOSE_INVALID", "declared Compose file is missing", path=relative)
                )
                continue
            compose_hashes[relative] = _sha256_file(path)
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, yaml.YAMLError) as exc:
                findings.append(
                    _finding("COMPOSE_INVALID", f"cannot parse Compose: {exc}", path=relative)
                )
                continue
            if not isinstance(raw, Mapping):
                findings.append(
                    _finding("COMPOSE_INVALID", "Compose root must be a mapping", path=relative)
                )
                continue
            compose_documents[relative] = cast(Mapping[str, Any], raw)

        combined_services: dict[str, Mapping[str, Any]] = {}
        combined_volumes: set[str] = set()
        for relative, document in compose_documents.items():
            raw_services = document.get("services")
            if not isinstance(raw_services, Mapping) or not raw_services:
                findings.append(
                    _finding("COMPOSE_INVALID", "Compose services cannot be empty", path=relative)
                )
                continue
            for name, value in cast(Mapping[str, Any], raw_services).items():
                if isinstance(value, Mapping):
                    combined_services[str(name)] = cast(Mapping[str, Any], value)
            volumes = document.get("volumes")
            if isinstance(volumes, Mapping):
                volume_mapping = cast(Mapping[str, Any], volumes)
                combined_volumes.update(str(name) for name in volume_mapping)

        declared_services = {service.name: service for service in contract.services}
        for name, service in declared_services.items():
            compose_service = combined_services.get(name)
            if compose_service is None:
                findings.append(
                    _finding(
                        "COMPOSE_INVALID", "declared service is absent from Compose", path=name
                    )
                )
                continue
            self._validate_build(name, service.dockerfile, compose_service, findings)
            if service.healthcheck_required and not isinstance(
                compose_service.get("healthcheck"), Mapping
            ):
                findings.append(
                    _finding(
                        "ENVIRONMENT_NOT_REBUILDABLE", "service healthcheck is required", path=name
                    )
                )
            mounts = _volume_strings(compose_service.get("volumes"))
            for target in service.persistent_targets:
                if not any(_mount_target(item) == target for item in mounts):
                    findings.append(
                        _finding(
                            "PERSISTENCE_UNDECLARED",
                            "stateful target is not mounted",
                            path=f"{name}:{target}",
                        )
                    )

        for mount in contract.persistent_mounts:
            service = combined_services.get(mount.service)
            mounts = _volume_strings(service.get("volumes")) if service else ()
            expected = f"{mount.source}:{mount.target}"
            if not any(item == expected or item.startswith(expected + ":") for item in mounts):
                findings.append(
                    _finding(
                        "PERSISTENCE_UNDECLARED",
                        "declared persistent mount is absent from Compose",
                        path=mount.service,
                    )
                )
            if mount.kind == "named-volume" and mount.source not in combined_volumes:
                findings.append(
                    _finding(
                        "PERSISTENCE_UNDECLARED",
                        "named volume is not declared at Compose root",
                        path=mount.source,
                    )
                )

        all_mounts = tuple(
            item
            for service in combined_services.values()
            for item in _volume_strings(service.get("volumes"))
        )
        for asset in contract.shared_assets:
            prefix = "${" + asset.source_env + "}:" + asset.target
            if not any(item.startswith(prefix) and item.endswith(":ro") for item in all_mounts):
                findings.append(
                    _finding(
                        "SHARED_ASSET_POLICY_VIOLATION",
                        "shared asset must use its approved environment root and a read-only mount",
                        path=asset.name,
                    )
                )

    def _validate_build(
        self,
        service_name: str,
        declared_dockerfile: str,
        compose_service: Mapping[str, Any],
        findings: list[EnvironmentFinding],
    ) -> None:
        build = compose_service.get("build")
        if not isinstance(build, (str, Mapping)):
            findings.append(
                _finding(
                    "COMPOSE_INVALID", "service build configuration is required", path=service_name
                )
            )
            return
        if isinstance(build, str):
            context_text = build
            dockerfile_text = declared_dockerfile
        else:
            build_mapping = cast(Mapping[str, Any], build)
            context_text = str(build_mapping.get("context", "."))
            dockerfile_text = str(build_mapping.get("dockerfile", declared_dockerfile))
        context = (self.root / context_text).resolve()
        if not context.is_relative_to(self.root):
            findings.append(
                _finding("COMPOSE_INVALID", "build context escapes project root", path=service_name)
            )
        dockerfile = (
            (context / dockerfile_text).resolve()
            if not Path(dockerfile_text).is_absolute()
            else Path(dockerfile_text).resolve()
        )
        expected = (self.root / declared_dockerfile).resolve()
        if dockerfile != expected or not dockerfile.is_relative_to(self.root):
            findings.append(
                _finding(
                    "COMPOSE_INVALID",
                    "Compose Dockerfile differs from the environment contract",
                    path=service_name,
                )
            )

    def _host_audit(
        self,
        large_file_bytes: int,
        findings: list[EnvironmentFinding],
        repairs: list[EnvironmentRepairAction],
    ) -> EnvironmentDiskUsage:
        project_bytes = 0
        git_bytes = 0
        dependency_bytes = 0
        for current, directories, files in os.walk(self.root, topdown=True, followlinks=False):
            current_path = Path(current)
            relative_dir = current_path.relative_to(self.root)
            kept: list[str] = []
            for name in directories:
                candidate = current_path / name
                relative = relative_dir / name
                folded = name.casefold()
                if _is_link_like(candidate):
                    try:
                        resolved = candidate.resolve(strict=True)
                    except OSError:
                        resolved = candidate.resolve()
                    if not resolved.is_relative_to(self.root):
                        findings.append(
                            _finding(
                                "SHARED_ASSET_POLICY_VIOLATION",
                                "symlink or junction escapes project root",
                                path=relative.as_posix(),
                            )
                        )
                    continue
                if relative.parts[:1] == (".git",):
                    continue
                if (
                    relative.parts[:1] == (".codex-os",)
                    and len(relative.parts) > 1
                    and relative.parts[1] in _RUNTIME_DIRS
                ):
                    continue
                if relative.parts[:1] == (".worktrees",):
                    continue
                if folded in _HOST_DEPENDENCIES:
                    size = _tree_size(candidate)
                    dependency_bytes += size
                    findings.append(
                        _finding(
                            "HOST_DEPENDENCY_PRESENT",
                            "host dependency, cache, or build directory is forbidden",
                            path=relative.as_posix(),
                            details={"bytes": size},
                        )
                    )
                    repairs.append(
                        EnvironmentRepairAction(
                            operation="remove_regenerable_host_dependency",
                            path=relative.as_posix(),
                            actual=str(size),
                        )
                    )
                    continue
                if folded in _SHARED_ASSET_DIRS:
                    findings.append(
                        _finding(
                            "SHARED_ASSET_POLICY_VIOLATION",
                            "models and datasets must use approved external read-only mounts",
                            path=relative.as_posix(),
                        )
                    )
                kept.append(name)
            directories[:] = kept
            for name in files:
                path = current_path / name
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                project_bytes += size
                relative = path.relative_to(self.root).as_posix()
                if size > large_file_bytes:
                    findings.append(
                        _finding(
                            "SHARED_ASSET_POLICY_VIOLATION",
                            "large project file exceeds the declared threshold",
                            path=relative,
                            details={"bytes": size},
                        )
                    )
        git_dir = self.root / ".git"
        if git_dir.is_dir():
            git_bytes = _tree_size(git_dir)
        return EnvironmentDiskUsage(
            project_bytes=project_bytes,
            git_bytes=git_bytes,
            host_dependency_bytes=dependency_bytes,
        )

    def _with_oci_usage(
        self,
        contract: EnvironmentContract,
        disk: EnvironmentDiskUsage,
    ) -> EnvironmentDiskUsage:
        result = self._run([contract.oci_backend.value, "system", "df", "--format", "json"])
        if result.returncode != 0:
            return disk
        try:
            payload: object = json.loads(result.stdout.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            return disk
        images, cache, volumes = _parse_oci_usage(payload)
        return disk.model_copy(
            update={
                "oci_images_bytes": images,
                "oci_build_cache_bytes": cache,
                "oci_volumes_bytes": volumes,
            }
        )

    def _run(self, argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        try:
            return self.runner(argv, self.root, 30.0)
        except (OSError, subprocess.SubprocessError) as exc:
            return subprocess.CompletedProcess(argv, 1, b"", str(exc).encode("utf-8"))


def _finding(
    code: str,
    message: str,
    *,
    path: str | None = None,
    details: dict[str, Any] | None = None,
) -> EnvironmentFinding:
    return EnvironmentFinding(code=code, message=message, path=path, details=details or {})


def _volume_strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    items = cast(list[object], value)
    return tuple(item for item in items if isinstance(item, str))


def _mount_target(value: str) -> str:
    parts = value.split(":")
    return parts[1] if len(parts) >= 2 else value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_size(root: Path) -> int:
    total = 0
    for current, _directories, files in os.walk(root, followlinks=False):
        for name in files:
            try:
                total += (Path(current) / name).stat().st_size
            except OSError:
                continue
    return total


def _is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction()) if callable(is_junction) else False


def _parse_oci_usage(payload: object) -> tuple[int | None, int | None, int | None]:
    if not isinstance(payload, Mapping):
        return None, None, None
    data = cast(Mapping[str, Any], payload)
    return (
        _sum_size(data.get("Images")),
        _sum_size(data.get("BuildCache")),
        _sum_size(data.get("Volumes")),
    )


def _sum_size(value: object) -> int | None:
    if not isinstance(value, list):
        return None
    items = cast(list[object], value)
    total = 0
    found = False
    for item in items:
        if not isinstance(item, Mapping):
            continue
        mapping = cast(Mapping[str, Any], item)
        for key in ("Size", "UsageData", "Reclaimable"):
            raw = mapping.get(key)
            if isinstance(raw, int):
                total += raw
                found = True
                break
            if isinstance(raw, Mapping):
                usage = cast(Mapping[str, Any], raw)
                nested_size = usage.get("Size")
            else:
                nested_size = None
            if isinstance(nested_size, int):
                total += nested_size
                found = True
                break
    return total if found else None


def _run_command(
    argv: list[str],
    cwd: Path,
    timeout: float,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        argv,
        cwd=cwd,
        check=False,
        capture_output=True,
        timeout=timeout,
    )


__all__ = ["EnvironmentGovernanceError", "EnvironmentGovernanceService"]
