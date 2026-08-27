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

CommandRunner = Callable[[list[str], Path, float], subprocess.CompletedProcess[bytes]]


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
            cached = _load_json(manifest_path)
            if (
                cached.get("operation_request_hash") == operation.request_hash
                and cached.get("uv_lock_hash") == lock_hash
                and cached.get("expires_at") == expires_at
                and _manifest_files_exist(target, cached)
            ):
                return cached
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
            trivy_cache = staging / "trivy"
            trivy_cache.mkdir()

            self._run(
                [
                    "uv",
                    "export",
                    "--locked",
                    "--format",
                    "requirements-txt",
                    "--no-dev",
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
                "execution_image": RUNTIME_VERSIONS.execution_image,
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

    def _run(self, command: list[str], error_code: str) -> None:
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


def _required_text(request: dict[str, Any], key: str) -> str:
    value = request.get(key)
    if not isinstance(value, str) or not value.strip():
        raise VerificationCachePrepareError("CONFIG_INVALID", f"{key} is required")
    return value


def _manifest_files_exist(root: Path, manifest: Mapping[str, object]) -> bool:
    files = manifest.get("files")
    if not isinstance(files, dict):
        return False
    paths: list[str] = []
    raw_files = cast(dict[object, object], files)
    for raw_path in raw_files:
        if not isinstance(raw_path, str):
            return False
        paths.append(raw_path)
    return all((root / path).is_file() for path in paths)


def _file_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "verification-cache-manifest.json":
            hashes[path.relative_to(root).as_posix()] = _sha256(path)
    return hashes


def _tree_hash(root: Path) -> str:
    payload = json.dumps(_file_hashes(root), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise VerificationCachePrepareError(
            "DEPENDENCY_UNVERIFIED", f"expected JSON object: {path}"
        )
    return cast(dict[str, Any], value)


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
    "CommandRunner",
    "VerificationCachePrepareError",
    "VerificationCachePreparer",
]
