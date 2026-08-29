"""Prepare lock-bound offline verification caches as a host operation."""

from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import sys
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from codex_ai_os.application.offline_audit import AuditSnapshotError, build_snapshot
from codex_ai_os.domain.operations import HostOperation, HostOperationKind
from codex_ai_os.domain.versions import RUNTIME_VERSIONS
from codex_ai_os.infrastructure.config import load_execution_policy

CommandRunner = Callable[[list[str], Path, float], subprocess.CompletedProcess[bytes]]
DEFAULT_VERIFICATION_PLATFORM = "linux-amd64"
DEFAULT_VERIFICATION_PYTHON = "3.12"


class VerificationCachePrepareError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        outcome_unknown: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.outcome_unknown = outcome_unknown


class VerificationCachePreparer:
    """Build wheelhouse, dependency audit, and Trivy cache artifacts after approval."""

    def __init__(
        self,
        project_root: Path,
        *,
        runner: CommandRunner | None = None,
    ) -> None:
        self.root = project_root.resolve()
        self.runner = runner or _run_command

    def prepare(self, operation: HostOperation) -> dict[str, Any]:
        if operation.kind is not HostOperationKind.VERIFICATION_PREPARE:
            raise VerificationCachePrepareError(
                "CONFIG_INVALID",
                f"unsupported verification cache operation: {operation.kind.value}",
            )
        request = operation.request
        lock_hash = _required_text(request, "uv_lock_hash")
        expires_at = _required_text(request, "expires_at")
        target_python = _required_text(request, "target_python")
        target_platform = _required_text(request, "platform")
        approval_ref = _required_text(request, "network_approval_ref")
        requested_image = _required_text(request, "execution_image")
        platform_payload = validate_verification_target(
            target_platform, target_python, expires_at
        )
        if requested_image != RUNTIME_VERSIONS.execution_image:
            raise VerificationCachePrepareError(
                "EXECUTION_IMAGE_MISMATCH",
                "verification prepare authorization does not match the runtime image",
            )
        lock_path = self.root / "uv.lock"
        if not lock_path.is_file():
            raise VerificationCachePrepareError(
                "DEPENDENCY_UNVERIFIED", "uv.lock is required for verification prepare"
            )
        actual_lock_hash = _sha256(lock_path)
        if actual_lock_hash != lock_hash:
            raise VerificationCachePrepareError(
                "DEPENDENCY_UNVERIFIED",
                "uv.lock changed after verification prepare was authorized",
            )

        cache_root = self.root / ".codex-os" / "cache" / "verification"
        target = cache_root / lock_hash
        manifest_path = target / "verification-cache-manifest.json"
        if manifest_path.is_file():
            try:
                cached = validate_verification_cache(
                    target,
                    lock_path=lock_path,
                    expected_platform=target_platform,
                    expected_python=target_python,
                    expected_request_hash=operation.request_hash,
                )
                if cached.get("expires_at") == expires_at:
                    return cached
            except (VerificationCachePrepareError, OSError, json.JSONDecodeError):
                pass
            self._retain_existing_cache(target)

        staging = cache_root / f".staging-{operation.operation_id}-{operation.attempt_count}"
        if staging.exists():
            raise VerificationCachePrepareError(
                "OPERATION_RECONCILE_REQUIRED",
                f"verification cache staging path already exists: {staging}",
                outcome_unknown=True,
            )
        try:
            staging.mkdir(parents=True)
            requirements = staging / "requirements.txt"
            audit_report = staging / "pip-audit-report.json"
            audit_snapshot = staging / "audit-snapshot.json"
            image_sbom = staging / "image-sbom.cdx.json"
            image_scan = staging / "image-scan.json"
            trivy_cache = staging / "trivy"
            trivy_cache.mkdir()

            self._run(
                [
                    "uv",
                    "export",
                    "--locked",
                    "--format",
                    "requirements-txt",
                    "--no-emit-project",
                    "--output-file",
                    str(requirements),
                ],
                "DEPENDENCY_UNVERIFIED",
            )
            self._run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "download",
                    "--require-hashes",
                    "--only-binary=:all:",
                    "--platform",
                    _pip_platform(target_platform),
                    "--python-version",
                    target_python,
                    "--implementation",
                    "cp",
                    "--abi",
                    "cp312",
                    "--dest",
                    str(staging),
                    "-r",
                    str(requirements),
                ],
                "DEPENDENCY_UNVERIFIED",
            )
            self._run(
                [
                    "pip-audit",
                    "-r",
                    str(requirements),
                    "-f",
                    "json",
                    "-o",
                    str(audit_report),
                ],
                "DEPENDENCY_UNVERIFIED",
            )
            audit_payload = _load_json(audit_report)
            snapshot_payload = build_snapshot(
                lock_content=lock_path.read_bytes(),
                requirements_content=requirements.read_bytes(),
                audit_report=cast(Mapping[str, Any], audit_payload),
                tool_version="pip-audit",
                target_platform=platform_payload,
            )
            audit_snapshot.write_text(
                json.dumps(snapshot_payload, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            self._run(
                [
                    "trivy",
                    "image",
                    "--download-db-only",
                    "--cache-dir",
                    str(trivy_cache),
                ],
                "DEPENDENCY_UNVERIFIED",
            )
            if not any(path.is_file() for path in trivy_cache.rglob("*")):
                raise VerificationCachePrepareError(
                    "DEPENDENCY_UNVERIFIED", "verification prepare produced no Trivy DB files"
                )
            engine = load_execution_policy(self.root).sandbox.value
            inspected = self._run(
                [
                    engine,
                    "image",
                    "inspect",
                    requested_image,
                    "--format",
                    "{{json .}}",
                ],
                "SANDBOX_IMAGE_UNAVAILABLE",
            )
            image = _load_json_bytes(inspected.stdout, "OCI image inspection")
            platform_digest = _platform_digest(image)
            _require_image_platform(image, platform_payload)
            self._run(
                [
                    "trivy",
                    "image",
                    "--offline-scan",
                    "--skip-db-update",
                    "--cache-dir",
                    str(trivy_cache),
                    "--format",
                    "cyclonedx",
                    "--output",
                    str(image_sbom),
                    requested_image,
                ],
                "DEPENDENCY_UNVERIFIED",
            )
            self._run(
                [
                    "trivy",
                    "image",
                    "--offline-scan",
                    "--skip-db-update",
                    "--cache-dir",
                    str(trivy_cache),
                    "--format",
                    "json",
                    "--severity",
                    "HIGH,CRITICAL",
                    "--exit-code",
                    "1",
                    "--output",
                    str(image_scan),
                    requested_image,
                ],
                "DEPENDENCY_UNVERIFIED",
            )
            _validate_image_reports(image_sbom, image_scan)
            wheel_files = sorted(path.name for path in staging.glob("*.whl"))
            if not wheel_files:
                raise VerificationCachePrepareError(
                    "DEPENDENCY_UNVERIFIED", "verification prepare produced no wheels"
                )
            manifest = {
                "schema_version": RUNTIME_VERSIONS.api,
                "kind": HostOperationKind.VERIFICATION_PREPARE.value,
                "operation_id": operation.operation_id,
                "operation_request_hash": operation.request_hash,
                "network_approval_ref": approval_ref,
                "uv_lock_hash": lock_hash,
                "target_python": target_python,
                "platform": target_platform,
                "execution_image": requested_image,
                "image": {
                    "engine": engine,
                    "registry_index_digest": requested_image.rsplit("@", 1)[1],
                    "platform_digest": platform_digest,
                    "os": str(image.get("Os") or image.get("os") or "").casefold(),
                    "architecture": str(
                        image.get("Architecture") or image.get("architecture") or ""
                    ).casefold(),
                },
                "expires_at": expires_at,
                "prepared_at": datetime.now(UTC).isoformat(),
                "requirements": {
                    "path": "requirements.txt",
                    "sha256": _sha256(requirements),
                },
                "pip_audit_snapshot": {
                    "path": "audit-snapshot.json",
                    "sha256": _sha256(audit_snapshot),
                },
                "trivy_cache": {
                    "path": "trivy",
                    "sha256": _tree_hash(trivy_cache),
                },
                "image_sbom": {
                    "path": "image-sbom.cdx.json",
                    "sha256": _sha256(image_sbom),
                },
                "image_scan": {
                    "path": "image-scan.json",
                    "sha256": _sha256(image_scan),
                },
                "wheelhouse": {"path": ".", "wheels": wheel_files},
                "files": _file_hashes(staging),
                "read_only": True,
            }
            (staging / "verification-cache-manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            if target.exists():
                self._retain_existing_cache(target)
            _mark_read_only(staging)
            staging.rename(target)
        except (OSError, subprocess.SubprocessError) as exc:
            raise VerificationCachePrepareError(
                "OPERATION_RECONCILE_REQUIRED",
                f"verification cache prepare outcome is unknown: {exc}",
                outcome_unknown=True,
            ) from exc
        except (AuditSnapshotError, json.JSONDecodeError, UnicodeError) as exc:
            raise VerificationCachePrepareError("DEPENDENCY_UNVERIFIED", str(exc)) from exc
        final_manifest = _load_json(manifest_path)
        return final_manifest

    def _retain_existing_cache(self, target: Path) -> None:
        if not target.exists():
            return
        retained = target.with_name(
            f"{target.name}.retained-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}"
        )
        try:
            target.rename(retained)
        except OSError as exc:
            raise VerificationCachePrepareError(
                "OPERATION_RECONCILE_REQUIRED",
                f"cannot retain existing verification cache: {exc}",
                outcome_unknown=True,
            ) from exc

    def _run(
        self, command: list[str], error_code: str
    ) -> subprocess.CompletedProcess[bytes]:
        try:
            result = self.runner(command, self.root, 600.0)
        except (OSError, subprocess.SubprocessError) as exc:
            raise VerificationCachePrepareError(
                "OPERATION_RECONCILE_REQUIRED",
                f"host command outcome is unknown: {command[0]}: {exc}",
                outcome_unknown=True,
            ) from exc
        if result.returncode != 0:
            raise VerificationCachePrepareError(
                error_code,
                _command_error(command, result),
            )
        return result


def _required_text(request: dict[str, Any], key: str) -> str:
    value = request.get(key)
    if not isinstance(value, str) or not value.strip():
        raise VerificationCachePrepareError("CONFIG_INVALID", f"{key} is required")
    return value


def validate_verification_cache(
    root: Path,
    *,
    lock_path: Path,
    expected_platform: str = DEFAULT_VERIFICATION_PLATFORM,
    expected_python: str = DEFAULT_VERIFICATION_PYTHON,
    expected_request_hash: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fail closed unless every approved cache input and file is reproducible."""

    if _is_link_like(root):
        raise VerificationCachePrepareError(
            "DEPENDENCY_UNVERIFIED", "verification cache root cannot be a link or junction"
        )
    cache = root.resolve()
    manifest_path = cache / "verification-cache-manifest.json"
    if not manifest_path.is_file():
        raise VerificationCachePrepareError(
            "SANDBOX_DEPENDENCIES_UNAVAILABLE",
            "verification cache manifest is missing",
        )
    manifest = _load_json(manifest_path)
    if manifest.get("schema_version") != RUNTIME_VERSIONS.api:
        raise VerificationCachePrepareError(
            "DEPENDENCY_UNVERIFIED", "unsupported verification cache schema"
        )
    if manifest.get("kind") != HostOperationKind.VERIFICATION_PREPARE.value:
        raise VerificationCachePrepareError(
            "DEPENDENCY_UNVERIFIED", "invalid verification cache kind"
        )
    if not isinstance(manifest.get("network_approval_ref"), str) or not str(
        manifest["network_approval_ref"]
    ).strip():
        raise VerificationCachePrepareError(
            "APPROVAL_REQUIRED", "verification cache has no network approval reference"
        )
    if expected_request_hash is not None and (
        manifest.get("operation_request_hash") != expected_request_hash
    ):
        raise VerificationCachePrepareError(
            "DEPENDENCY_UNVERIFIED", "verification cache request hash drifted"
        )
    if not lock_path.is_file():
        raise VerificationCachePrepareError(
            "SANDBOX_DEPENDENCIES_UNAVAILABLE", "uv.lock is required for verification"
        )
    lock_hash = _sha256(lock_path)
    if manifest.get("uv_lock_hash") != lock_hash:
        raise VerificationCachePrepareError(
            "DEPENDENCY_UNVERIFIED", "verification cache does not match uv.lock"
        )
    if manifest.get("target_python") != expected_python:
        raise VerificationCachePrepareError(
            "PLATFORM_MISMATCH", "verification cache Python target does not match"
        )
    if manifest.get("platform") != expected_platform:
        raise VerificationCachePrepareError(
            "PLATFORM_MISMATCH", "verification cache platform does not match"
        )
    _target_platform(expected_platform, expected_python)
    if manifest.get("execution_image") != RUNTIME_VERSIONS.execution_image:
        raise VerificationCachePrepareError(
            "EXECUTION_IMAGE_MISMATCH", "verification cache execution image drifted"
        )
    _require_fresh_manifest(manifest, now=now)
    if manifest.get("read_only") is not True:
        raise VerificationCachePrepareError(
            "DEPENDENCY_UNVERIFIED", "verification cache is not declared read-only"
        )

    files_value = manifest.get("files")
    if not isinstance(files_value, dict):
        raise VerificationCachePrepareError(
            "DEPENDENCY_UNVERIFIED", "verification cache file hashes are missing"
        )
    expected_files: dict[str, str] = {}
    for raw_path, raw_hash in cast(dict[object, object], files_value).items():
        if not isinstance(raw_path, str) or not isinstance(raw_hash, str):
            raise VerificationCachePrepareError(
                "DEPENDENCY_UNVERIFIED", "verification cache file hash entry is invalid"
            )
        safe_path = _safe_relative(raw_path)
        if safe_path != raw_path or not _is_sha256(raw_hash):
            raise VerificationCachePrepareError(
                "DEPENDENCY_UNVERIFIED", "verification cache file hash entry is invalid"
            )
        expected_files[raw_path] = raw_hash
    actual_files = _file_hashes(cache)
    if actual_files != expected_files:
        raise VerificationCachePrepareError(
            "DEPENDENCY_UNVERIFIED", "verification cache file hashes drifted"
        )

    _require_file_descriptor(
        cache, manifest.get("requirements"), expected_path="requirements.txt"
    )
    _require_file_descriptor(
        cache, manifest.get("pip_audit_snapshot"), expected_path="audit-snapshot.json"
    )
    _require_file_descriptor(
        cache, manifest.get("image_sbom"), expected_path="image-sbom.cdx.json"
    )
    _require_file_descriptor(
        cache, manifest.get("image_scan"), expected_path="image-scan.json"
    )
    _validate_image_reports(cache / "image-sbom.cdx.json", cache / "image-scan.json")
    image = _as_mapping(manifest.get("image"), "image")
    expected_architectures = {
        "linux-amd64": {"amd64", "x86_64"},
        "linux-arm64": {"arm64", "aarch64"},
    }[expected_platform]
    if (
        image.get("registry_index_digest")
        != RUNTIME_VERSIONS.execution_image.rsplit("@", 1)[1]
        or not _is_digest(image.get("platform_digest"))
        or image.get("os") != "linux"
        or image.get("architecture") not in expected_architectures
    ):
        raise VerificationCachePrepareError(
            "EXECUTION_IMAGE_MISMATCH", "verification image identity drifted"
        )
    trivy = _as_mapping(manifest.get("trivy_cache"), "trivy_cache")
    trivy_root = cache / "trivy"
    if (
        trivy.get("path") != "trivy"
        or not trivy_root.is_dir()
        or not any(path.is_file() for path in trivy_root.rglob("*"))
        or trivy.get("sha256") != _tree_hash(trivy_root)
    ):
        raise VerificationCachePrepareError(
            "DEPENDENCY_UNVERIFIED", "Trivy cache hash drifted"
        )
    wheelhouse = _as_mapping(manifest.get("wheelhouse"), "wheelhouse")
    wheels_value = wheelhouse.get("wheels")
    if wheelhouse.get("path") != "." or not isinstance(wheels_value, list):
        raise VerificationCachePrepareError(
            "DEPENDENCY_UNVERIFIED", "verification wheelhouse manifest is invalid"
        )
    wheels = tuple(cast(list[object], wheels_value))
    if not wheels or not all(
        isinstance(item, str)
        and item == Path(item).name
        and item.casefold().endswith(".whl")
        for item in wheels
    ):
        raise VerificationCachePrepareError(
            "DEPENDENCY_UNVERIFIED", "verification wheel list is invalid"
        )
    actual_wheels = tuple(sorted(path.name for path in cache.glob("*.whl")))
    if tuple(sorted(cast(tuple[str, ...], wheels))) != actual_wheels:
        raise VerificationCachePrepareError(
            "DEPENDENCY_UNVERIFIED", "verification wheelhouse contents drifted"
        )
    if not _tree_is_read_only(cache):
        raise VerificationCachePrepareError(
            "DEPENDENCY_UNVERIFIED", "verification cache permissions are writable"
        )
    return manifest


def validate_verification_target(
    target_platform: str,
    target_python: str,
    expires_at: str,
    *,
    now: datetime | None = None,
) -> dict[str, str]:
    platform_payload = _target_platform(target_platform, target_python)
    _require_future_expiry(expires_at, now=now)
    return platform_payload


def _file_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if _is_link_like(path):
            raise VerificationCachePrepareError(
                "DEPENDENCY_UNVERIFIED",
                f"verification cache contains a link or junction: {path}",
            )
        if path.is_file() and path.name != "verification-cache-manifest.json":
            hashes[path.relative_to(root).as_posix()] = _sha256(path)
    return hashes


def _tree_hash(root: Path) -> str:
    payload = json.dumps(_file_hashes(root), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _target_platform(name: str, python_version: str) -> dict[str, str]:
    if python_version != "3.12":
        raise VerificationCachePrepareError(
            "PLATFORM_MISMATCH", "0.2.0 verification requires Python 3.12"
        )
    platforms = {
        "linux-amd64": {"system": "linux", "machine": "x86_64"},
        "linux-arm64": {"system": "linux", "machine": "aarch64"},
    }
    try:
        selected = platforms[name]
    except KeyError as exc:
        raise VerificationCachePrepareError(
            "PLATFORM_MISMATCH", f"unsupported verification platform: {name}"
        ) from exc
    return {**selected, "python": python_version}


def _pip_platform(name: str) -> str:
    return {
        "linux-amd64": "manylinux2014_x86_64",
        "linux-arm64": "manylinux2014_aarch64",
    }[name]


def _require_future_expiry(value: str, *, now: datetime | None = None) -> None:
    expiry = _parse_timestamp(value, "expires_at")
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if expiry <= current:
        raise VerificationCachePrepareError(
            "VERIFICATION_CACHE_EXPIRED", "verification cache expiry must be in the future"
        )


def _require_fresh_manifest(
    manifest: Mapping[str, object], *, now: datetime | None = None
) -> None:
    prepared = _parse_timestamp(manifest.get("prepared_at"), "prepared_at")
    expiry = _parse_timestamp(manifest.get("expires_at"), "expires_at")
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if prepared > current or expiry <= prepared:
        raise VerificationCachePrepareError(
            "DEPENDENCY_UNVERIFIED", "verification cache timestamps are invalid"
        )
    if expiry <= current:
        raise VerificationCachePrepareError(
            "VERIFICATION_CACHE_EXPIRED", "verification cache approval expired"
        )


def _parse_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise VerificationCachePrepareError(
            "DEPENDENCY_UNVERIFIED", f"verification cache {field} is missing"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise VerificationCachePrepareError(
            "DEPENDENCY_UNVERIFIED", f"verification cache {field} is invalid"
        ) from exc
    if parsed.tzinfo is None:
        raise VerificationCachePrepareError(
            "DEPENDENCY_UNVERIFIED", f"verification cache {field} has no timezone"
        )
    return parsed.astimezone(UTC)


def _safe_relative(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = Path(normalized)
    if (
        not normalized
        or path.is_absolute()
        or normalized.startswith("/")
        or ".." in normalized.split("/")
    ):
        raise VerificationCachePrepareError(
            "DEPENDENCY_UNVERIFIED", f"unsafe verification cache path: {value}"
        )
    return normalized


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _is_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("sha256:")
        and _is_sha256(value.removeprefix("sha256:"))
    )


def _as_mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise VerificationCachePrepareError(
            "DEPENDENCY_UNVERIFIED", f"verification cache {field} is invalid"
        )
    return cast(dict[str, object], value)


def _require_file_descriptor(
    root: Path, value: object, *, expected_path: str
) -> None:
    descriptor = _as_mapping(value, expected_path)
    path_value = descriptor.get("path")
    hash_value = descriptor.get("sha256")
    if path_value != expected_path or not isinstance(hash_value, str):
        raise VerificationCachePrepareError(
            "DEPENDENCY_UNVERIFIED", f"verification cache {expected_path} is invalid"
        )
    path = root / expected_path
    if not path.is_file() or hash_value != _sha256(path):
        raise VerificationCachePrepareError(
            "DEPENDENCY_UNVERIFIED", f"verification cache {expected_path} hash drifted"
        )


def _tree_is_read_only(root: Path) -> bool:
    write_bits = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
    paths = (root, *root.rglob("*"))
    return all(
        not _is_link_like(path) and path.stat().st_mode & write_bits == 0
        for path in paths
    )


def _is_link_like(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction is not None and is_junction())


def _load_json(path: Path) -> dict[str, Any]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise VerificationCachePrepareError(
            "DEPENDENCY_UNVERIFIED", f"expected JSON object: {path}"
        )
    return cast(dict[str, Any], value)


def _load_json_bytes(content: bytes, label: str) -> dict[str, Any]:
    try:
        value: object = json.loads(content.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise VerificationCachePrepareError(
            "SANDBOX_IMAGE_UNAVAILABLE", f"{label} returned invalid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise VerificationCachePrepareError(
            "SANDBOX_IMAGE_UNAVAILABLE", f"{label} returned a non-object response"
        )
    return cast(dict[str, Any], value)


def _platform_digest(image: Mapping[str, object]) -> str:
    for key in ("Digest", "digest", "Id", "ID", "id"):
        value = image.get(key)
        if _is_digest(value):
            return cast(str, value).casefold()
    raise VerificationCachePrepareError(
        "SANDBOX_IMAGE_UNAVAILABLE", "OCI image inspection has no platform digest"
    )


def _require_image_platform(
    image: Mapping[str, object], expected: Mapping[str, str]
) -> None:
    system = str(image.get("Os") or image.get("os") or "").casefold()
    architecture = str(
        image.get("Architecture") or image.get("architecture") or ""
    ).casefold()
    expected_architecture = {"x86_64": "amd64", "aarch64": "arm64"}.get(
        expected["machine"], expected["machine"]
    )
    if system != expected["system"] or architecture not in {
        expected["machine"],
        expected_architecture,
    }:
        raise VerificationCachePrepareError(
            "PLATFORM_MISMATCH", "OCI image platform does not match verification target"
        )


def _validate_image_reports(sbom_path: Path, scan_path: Path) -> None:
    sbom = _load_json(sbom_path)
    if sbom.get("bomFormat") != "CycloneDX" or not isinstance(
        sbom.get("serialNumber"), str
    ):
        raise VerificationCachePrepareError(
            "DEPENDENCY_UNVERIFIED", "Trivy image SBOM is invalid"
        )
    scan = _load_json(scan_path)
    results = scan.get("Results")
    if not isinstance(results, list):
        raise VerificationCachePrepareError(
            "DEPENDENCY_UNVERIFIED", "Trivy image scan has no Results array"
        )
    blockers: list[str] = []
    for raw_result in cast(list[object], results):
        if not isinstance(raw_result, dict):
            raise VerificationCachePrepareError(
                "DEPENDENCY_UNVERIFIED", "Trivy image scan result is invalid"
            )
        raw_vulnerabilities = cast(dict[str, object], raw_result).get("Vulnerabilities")
        if raw_vulnerabilities is None:
            vulnerabilities: list[object] = []
        elif isinstance(raw_vulnerabilities, list):
            vulnerabilities = cast(list[object], raw_vulnerabilities)
        else:
            raise VerificationCachePrepareError(
                "DEPENDENCY_UNVERIFIED", "Trivy vulnerabilities value is invalid"
            )
        for raw_finding in vulnerabilities:
            if not isinstance(raw_finding, dict):
                raise VerificationCachePrepareError(
                    "DEPENDENCY_UNVERIFIED", "Trivy vulnerability entry is invalid"
                )
            finding = cast(dict[str, object], raw_finding)
            if str(finding.get("Severity") or "").casefold() in {"high", "critical"}:
                blockers.append(str(finding.get("VulnerabilityID") or "unknown"))
    if blockers:
        raise VerificationCachePrepareError(
            "DEPENDENCY_UNVERIFIED",
            f"image has unapproved high/critical findings: {blockers}",
        )


def _mark_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        mode = stat.S_IREAD | (stat.S_IEXEC if path.is_dir() else 0)
        with suppress(OSError):
            path.chmod(mode)
    with suppress(OSError):
        root.chmod(stat.S_IREAD | stat.S_IEXEC)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _command_error(
    command: list[str], result: subprocess.CompletedProcess[bytes]
) -> str:
    stderr = result.stderr.decode("utf-8", errors="replace").strip()
    stdout = result.stdout.decode("utf-8", errors="replace").strip()
    detail = stderr or stdout or f"exit code {result.returncode}"
    return f"{command[0]} failed: {detail[:1000]}"


def _run_command(
    command: list[str], cwd: Path, timeout: float
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        command,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


__all__ = [
    "DEFAULT_VERIFICATION_PLATFORM",
    "DEFAULT_VERIFICATION_PYTHON",
    "CommandRunner",
    "VerificationCachePrepareError",
    "VerificationCachePreparer",
    "validate_verification_cache",
    "validate_verification_target",
]
