"""Recoverable adoption, preparation, and verification of OCI project environments."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml

from codex_ai_os.application.environment import (
    CommandRunner,
    EnvironmentGovernanceError,
    EnvironmentGovernanceService,
)
from codex_ai_os.application.project import environment_scaffold_files
from codex_ai_os.domain.config import EnvironmentMode, SandboxBackend
from codex_ai_os.domain.environment import EnvironmentContract
from codex_ai_os.domain.invocation import InvocationContext
from codex_ai_os.domain.operations import HostOperation, HostOperationKind, HostOperationStatus
from codex_ai_os.infrastructure.config import load_environment_contract, load_project_config
from codex_ai_os.infrastructure.database import Database
from codex_ai_os.infrastructure.documents import DocumentManager
from codex_ai_os.infrastructure.operations import HostOperationError, HostOperationStore
from codex_ai_os.infrastructure.workflows import WorkflowNotFoundError, WorkflowStore


class EnvironmentOperationError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
        outcome_unknown: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}
        self.retryable = retryable
        self.outcome_unknown = outcome_unknown


@dataclass(frozen=True, slots=True)
class EnvironmentOperationResult:
    operation: HostOperation
    created_paths: tuple[str, ...] = ()


class EnvironmentOperationService:
    """Persist environment intents before any repository or Compose side effect."""

    def __init__(
        self,
        project_root: Path,
        *,
        runner: CommandRunner | None = None,
    ) -> None:
        self.config = load_project_config(project_root.resolve())
        self.database = Database(self.config.root / ".codex-os" / "state" / "state.db")
        self.database.migrate()
        self.operations = HostOperationStore(self.database)
        self.workflows = WorkflowStore(self.database)
        self.runner = runner or _run_command

    def adopt(
        self,
        *,
        run_id: str,
        task_id: str,
        expected_state_version: int,
        expected_task_version: int,
        idempotency_key: str,
        oci_backend: SandboxBackend,
        invocation: InvocationContext,
    ) -> EnvironmentOperationResult:
        _run, task = self._require_run_task(
            run_id,
            task_id,
            expected_state_version=expected_state_version,
            expected_task_version=expected_task_version,
        )
        if self.config.environment_mode is not EnvironmentMode.LEGACY:
            raise EnvironmentOperationError(
                "STATE_VERSION_CONFLICT", "project already uses an explicit environment mode"
            )
        if not task.worktree or not task.branch:
            raise EnvironmentOperationError(
                "ENVIRONMENT_ADOPTION_REQUIRED",
                "environment adoption requires an assigned task Worktree and branch",
            )
        target_root = Path(task.worktree).resolve()
        required_paths = (
            *tuple(environment_scaffold_files(self.config.name)),
            ".codex-os/project.yaml",
            "docs/ENVIRONMENT.md",
            "AGENTS.md",
        )
        denied = [path for path in required_paths if not _path_allowed(path, task.allowed_paths)]
        if denied:
            raise EnvironmentOperationError(
                "PATH_DENIED",
                "environment adoption paths are outside the task contract",
                details={"paths": denied},
            )
        request = {
            "operation_type": "environment_adopt",
            "run_id": run_id,
            "task_id": task_id,
            "branch": task.branch,
            "worktree": target_root.as_posix(),
            "expected_state_version": expected_state_version,
            "expected_task_version": expected_task_version,
            "oci_backend": oci_backend.value,
        }
        try:
            operation = self.operations.ensure_pending(
                project_id=self.config.project_id,
                run_id=run_id,
                task_id=task_id,
                kind=HostOperationKind.INTEGRATION_PREPARE,
                idempotency_key=idempotency_key,
                request=request,
                expected_state_version=expected_state_version,
                expected_task_version=expected_task_version,
            )
            if operation.status is HostOperationStatus.SUCCEEDED:
                paths = operation.result.get("created_paths", [])
                return EnvironmentOperationResult(
                    operation,
                    tuple(str(path) for path in cast(list[object], paths)),
                )
            acquired = self.operations.acquire(
                operation.operation_id,
                expected_version=operation.state_version,
                lease_owner=invocation.principal,
            )
            try:
                created = self._apply_adoption(target_root, oci_backend=oci_backend)
            except (EnvironmentOperationError, OSError, UnicodeError, yaml.YAMLError) as exc:
                self.operations.mark_failed(
                    acquired.operation_id,
                    expected_version=acquired.state_version,
                    lease_owner=invocation.principal,
                    error_code=getattr(exc, "code", "CONFIG_INVALID"),
                    result={"message": str(exc)},
                )
                if isinstance(exc, EnvironmentOperationError):
                    raise
                raise EnvironmentOperationError("CONFIG_INVALID", str(exc)) from exc
            succeeded = self.operations.mark_succeeded(
                acquired.operation_id,
                expected_version=acquired.state_version,
                lease_owner=invocation.principal,
                result={**request, "created_paths": list(created)},
            )
        except HostOperationError as exc:
            raise EnvironmentOperationError(exc.code, str(exc), retryable=exc.retryable) from exc
        return EnvironmentOperationResult(succeeded, created)

    def prepare(
        self,
        *,
        run_id: str,
        task_id: str,
        expected_state_version: int,
        expected_task_version: int,
        idempotency_key: str,
        network_approval_ref: str,
        expires_at: str,
    ) -> EnvironmentOperationResult:
        if not network_approval_ref.strip():
            raise EnvironmentOperationError("APPROVAL_REQUIRED", "network_approval_ref is required")
        expiry = _parse_future(expires_at)
        run, task = self._require_run_task(
            run_id,
            task_id,
            expected_state_version=expected_state_version,
            expected_task_version=expected_task_version,
        )
        source_root = self._source_root(task.worktree)
        source_commit = _source_commit(source_root, run.checkpoint, task.head_commit)
        snapshot = self._environment_snapshot(source_root)
        request = {
            "operation_type": "environment_prepare",
            "run_id": run_id,
            "task_id": task_id,
            "source_commit": source_commit,
            "expected_state_version": expected_state_version,
            "expected_task_version": expected_task_version,
            "network_approval_ref": network_approval_ref,
            "expires_at": expiry.isoformat(),
            "source_root": source_root.as_posix(),
            **snapshot,
        }
        return EnvironmentOperationResult(
            self._schedule(
                kind=HostOperationKind.VERIFICATION_PREPARE,
                run_id=run_id,
                task_id=task_id,
                expected_state_version=expected_state_version,
                expected_task_version=expected_task_version,
                idempotency_key=idempotency_key,
                request=request,
            )
        )

    def verify(
        self,
        *,
        run_id: str,
        task_id: str,
        expected_state_version: int,
        expected_task_version: int,
        idempotency_key: str,
        prepare_operation_id: str,
    ) -> EnvironmentOperationResult:
        run, task = self._require_run_task(
            run_id,
            task_id,
            expected_state_version=expected_state_version,
            expected_task_version=expected_task_version,
        )
        try:
            prepared = self.operations.get(prepare_operation_id)
        except HostOperationError as exc:
            raise EnvironmentOperationError(exc.code, str(exc)) from exc
        if (
            prepared.kind is not HostOperationKind.VERIFICATION_PREPARE
            or prepared.request.get("operation_type") != "environment_prepare"
            or prepared.run_id != run_id
            or prepared.status is not HostOperationStatus.SUCCEEDED
        ):
            raise EnvironmentOperationError(
                "ENVIRONMENT_NOT_REBUILDABLE",
                "environment_verify requires a succeeded prepare operation from this Workflow",
            )
        source_root = self._source_root(task.worktree)
        source_commit = _source_commit(source_root, run.checkpoint, task.head_commit)
        snapshot = self._environment_snapshot(source_root)
        request = {
            "operation_type": "environment_verify",
            "run_id": run_id,
            "task_id": task_id,
            "source_commit": source_commit,
            "expected_state_version": expected_state_version,
            "expected_task_version": expected_task_version,
            "prepare_operation_id": prepare_operation_id,
            "prepare_request_hash": prepared.request_hash,
            "source_root": source_root.as_posix(),
            **snapshot,
        }
        return EnvironmentOperationResult(
            self._schedule(
                kind=HostOperationKind.VERIFICATION_PREPARE,
                run_id=run_id,
                task_id=task_id,
                expected_state_version=expected_state_version,
                expected_task_version=expected_task_version,
                idempotency_key=idempotency_key,
                request=request,
            )
        )

    def execute_acquired(
        self,
        operation: HostOperation,
        *,
        lease_owner: str,
    ) -> HostOperation:
        operation_type = operation.request.get("operation_type")
        if operation_type == "environment_prepare":
            return self._execute_prepare(operation, lease_owner=lease_owner)
        if operation_type == "environment_verify":
            return self._execute_verify(operation, lease_owner=lease_owner)
        raise EnvironmentOperationError(
            "CONFIG_INVALID", f"unsupported environment operation: {operation_type}"
        )

    def _execute_prepare(
        self,
        operation: HostOperation,
        *,
        lease_owner: str,
    ) -> HostOperation:
        _parse_future(str(operation.request.get("expires_at", "")))
        source_root, contract = self._require_snapshot(operation)
        argv = [
            *_compose_argv(contract, operation.run_id or operation.operation_id),
            "build",
            "--pull",
        ]
        result = self._run(argv, cwd=source_root)
        if result.returncode != 0:
            return self._mark_command_failed(operation, lease_owner, result, "COMPOSE_BUILD_FAILED")
        image_result = self._run(
            [
                *_compose_argv(contract, operation.run_id or operation.operation_id),
                "config",
                "--images",
            ],
            cwd=source_root,
        )
        if image_result.returncode != 0:
            return self._mark_command_failed(
                operation, lease_owner, image_result, "ENVIRONMENT_NOT_REBUILDABLE"
            )
        images = tuple(
            line.strip()
            for line in image_result.stdout.decode("utf-8", errors="replace").splitlines()
            if line.strip()
        )
        digests: dict[str, str] = {}
        for image in images:
            inspect = self._run(
                [contract.oci_backend.value, "image", "inspect", image, "--format", "{{.Id}}"],
                cwd=source_root,
            )
            if inspect.returncode != 0:
                return self._mark_command_failed(
                    operation, lease_owner, inspect, "ENVIRONMENT_NOT_REBUILDABLE"
                )
            digests[image] = inspect.stdout.decode("utf-8", errors="replace").strip()
        return self.operations.mark_succeeded(
            operation.operation_id,
            expected_version=operation.state_version,
            lease_owner=lease_owner,
            result={
                "source_commit": operation.request.get("source_commit"),
                "contract_hash": operation.request.get("contract_hash"),
                "compose_hashes": operation.request.get("compose_hashes"),
                "lock_hashes": operation.request.get("lock_hashes"),
                "oci_backend": contract.oci_backend.value,
                "images": digests,
                "build_log_hash": _sha256_bytes(result.stdout + result.stderr),
                "network_approval_ref": operation.request.get("network_approval_ref"),
            },
        )

    def _execute_verify(
        self,
        operation: HostOperation,
        *,
        lease_owner: str,
    ) -> HostOperation:
        source_root, contract = self._require_snapshot(operation)
        prepared_id = str(operation.request.get("prepare_operation_id", ""))
        prepared = self.operations.get(prepared_id)
        if prepared.status is not HostOperationStatus.SUCCEEDED:
            raise EnvironmentOperationError(
                "ENVIRONMENT_NOT_REBUILDABLE", "prepare operation is no longer succeeded"
            )
        project_ref = operation.run_id or operation.operation_id
        base = _compose_argv(contract, project_ref)
        commands: list[list[str]] = [
            [*base, "up", "-d", "--no-build", "--pull", "never"],
            [*base, "ps", "--all"],
        ]
        probes: dict[str, str] = {}
        try:
            for command in commands:
                self._require_command(command, cwd=source_root)
            for service in contract.services:
                self._require_command(
                    [*base, "exec", "-T", service.name, *service.smoke_command],
                    cwd=source_root,
                )
                if service.stateful:
                    before = self._require_command(
                        [*base, "exec", "-T", service.name, *service.persistence_probe_command],
                        cwd=source_root,
                    )
                    probes[service.name] = before.stdout.decode("utf-8", errors="replace").strip()
            self._require_command([*base, "down", "--remove-orphans"], cwd=source_root)
            self._require_command(
                [*base, "up", "-d", "--no-build", "--pull", "never"], cwd=source_root
            )
            for service in contract.services:
                if service.stateful:
                    after = (
                        self._require_command(
                            [*base, "exec", "-T", service.name, *service.persistence_probe_command],
                            cwd=source_root,
                        )
                        .stdout.decode("utf-8", errors="replace")
                        .strip()
                    )
                    if not probes[service.name] or after != probes[service.name]:
                        raise EnvironmentOperationError(
                            "ENVIRONMENT_NOT_REBUILDABLE",
                            f"persistence probe changed after recreate: {service.name}",
                        )
        except EnvironmentOperationError as exc:
            self._run([*base, "down", "--remove-orphans"], cwd=source_root)
            failed = self.operations.mark_failed(
                operation.operation_id,
                expected_version=operation.state_version,
                lease_owner=lease_owner,
                error_code=exc.code,
                result={"message": str(exc)},
            )
            raise EnvironmentOperationError(
                exc.code,
                str(exc),
                details={"operation_id": failed.operation_id},
                retryable=True,
            ) from exc
        self._run([*base, "down", "--remove-orphans"], cwd=source_root)
        report = EnvironmentGovernanceService(
            source_root, runner=self.runner, config=self.config
        ).require_valid()
        result_payload = {
            "source_commit": operation.request.get("source_commit"),
            "prepare_operation_id": prepared_id,
            "contract_hash": report.contract_hash,
            "compose_hashes": report.compose_hashes,
            "oci_backend": contract.oci_backend.value,
            "network": "offline-internal-only",
            "container_recreate": "passed",
            "storage_persistence": "passed",
            "environment_smoke": "passed",
            "host_cleanliness": "passed",
            "probes": probes,
        }
        audit_path = (
            self.config.root
            / ".codex-os"
            / "artifacts"
            / "environment"
            / f"{operation.operation_id}.json"
        )
        DocumentManager(self.config.root).write_atomic(
            audit_path.relative_to(self.config.root),
            json.dumps(result_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            overwrite=True,
        )
        result_payload["report_path"] = audit_path.relative_to(self.config.root).as_posix()
        result_payload["report_hash"] = _sha256_file(audit_path)
        return self.operations.mark_succeeded(
            operation.operation_id,
            expected_version=operation.state_version,
            lease_owner=lease_owner,
            result=result_payload,
        )

    def _apply_adoption(
        self,
        target_root: Path,
        *,
        oci_backend: SandboxBackend,
    ) -> tuple[str, ...]:
        manager = DocumentManager(target_root)
        created: list[str] = []
        for relative, content in environment_scaffold_files(
            self.config.name, oci_backend=oci_backend
        ).items():
            if manager.write_atomic(relative, content, overwrite=False):
                created.append(relative)
        project_path = target_root / ".codex-os" / "project.yaml"
        raw = yaml.safe_load(project_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise EnvironmentOperationError("CONFIG_INVALID", "project.yaml must be a mapping")
        project = cast(dict[str, object], raw)
        existing_mode = project.get("environment_mode")
        if existing_mode not in {None, "legacy"}:
            raise EnvironmentOperationError(
                "STATE_VERSION_CONFLICT", "task Worktree already has a different environment mode"
            )
        project["environment_mode"] = "oci-first"
        manager.write_atomic(
            ".codex-os/project.yaml",
            yaml.safe_dump(project, allow_unicode=True, sort_keys=False),
            overwrite=True,
        )
        if (
            "docs/ENVIRONMENT.md" not in created
            and not (target_root / "docs" / "ENVIRONMENT.md").exists()
        ):
            manager.write_atomic(
                "docs/ENVIRONMENT.md",
                "# Project environment\n\nComplete the OCI-first contract before G2.\n",
                overwrite=False,
            )
            created.append("docs/ENVIRONMENT.md")
        if not (target_root / "AGENTS.md").exists():
            manager.write_atomic(
                "AGENTS.md",
                "# Project Instructions\n\n"
                "Project dependencies, builds, tests, and services must run in the "
                "configured OCI environment. Persistent volumes must not be deleted.\n",
                overwrite=False,
            )
            created.append("AGENTS.md")
        return tuple(sorted([*created, ".codex-os/project.yaml"]))

    def _require_run_task(
        self,
        run_id: str,
        task_id: str,
        *,
        expected_state_version: int,
        expected_task_version: int,
    ) -> tuple[Any, Any]:
        try:
            run = self.workflows.get_run(run_id)
            task = self.workflows.get_task(task_id)
        except WorkflowNotFoundError as exc:
            raise EnvironmentOperationError("RECOVERY_UNAVAILABLE", str(exc)) from exc
        if run.project_id != self.config.project_id or task.run_id != run_id:
            raise EnvironmentOperationError(
                "STATE_VERSION_CONFLICT", "run/task do not belong to this project"
            )
        if run.state_version != expected_state_version:
            raise EnvironmentOperationError(
                "STATE_VERSION_CONFLICT",
                f"expected workflow version {expected_state_version}, found {run.state_version}",
                retryable=True,
            )
        if task.state_version != expected_task_version:
            raise EnvironmentOperationError(
                "STATE_VERSION_CONFLICT",
                f"expected task version {expected_task_version}, found {task.state_version}",
                retryable=True,
            )
        return run, task

    def _source_root(self, task_worktree: str | None) -> Path:
        source_root = Path(task_worktree).resolve() if task_worktree else self.config.root
        if not source_root.is_dir():
            raise EnvironmentOperationError(
                "RECOVERY_UNAVAILABLE",
                f"registered source Worktree is unavailable: {source_root}",
                retryable=True,
            )
        return source_root

    def _environment_snapshot(self, source_root: Path) -> dict[str, object]:
        try:
            report = EnvironmentGovernanceService(
                source_root, runner=self.runner, config=self.config
            ).require_valid()
        except EnvironmentGovernanceError as exc:
            raise EnvironmentOperationError(
                exc.code, str(exc), details=exc.details, retryable=True
            ) from exc
        contract = load_environment_contract(source_root)
        return {
            "oci_backend": contract.oci_backend.value,
            "contract_hash": report.contract_hash,
            "compose_hashes": report.compose_hashes,
            "lock_hashes": {
                relative: _sha256_file(source_root / relative)
                for relative in contract.dependency_locks
            },
        }

    def _require_snapshot(self, operation: HostOperation) -> tuple[Path, EnvironmentContract]:
        request = operation.request
        source_root_value = request.get("source_root")
        if not isinstance(source_root_value, str) or not source_root_value:
            raise EnvironmentOperationError(
                "RECOVERY_UNAVAILABLE", "environment operation has no registered source root"
            )
        source_root = self._source_root(source_root_value)
        if operation.task_id:
            try:
                task = self.workflows.get_task(operation.task_id)
            except WorkflowNotFoundError as exc:
                raise EnvironmentOperationError("RECOVERY_UNAVAILABLE", str(exc)) from exc
            registered_root = self._source_root(task.worktree)
            if source_root != registered_root:
                raise EnvironmentOperationError(
                    "ENVIRONMENT_NOT_REBUILDABLE",
                    "environment source root no longer matches the registered task Worktree",
                    retryable=True,
                )
        current = self._environment_snapshot(source_root)
        for field in ("oci_backend", "contract_hash", "compose_hashes", "lock_hashes"):
            if request.get(field) != current.get(field):
                raise EnvironmentOperationError(
                    "ENVIRONMENT_NOT_REBUILDABLE",
                    f"environment snapshot drifted: {field}",
                    details={"field": field},
                    retryable=True,
                )
        actual_commit = _source_commit(source_root, {}, None)
        if request.get("source_commit") != actual_commit:
            raise EnvironmentOperationError(
                "ENVIRONMENT_NOT_REBUILDABLE",
                "environment source Commit drifted",
                details={"expected": request.get("source_commit"), "actual": actual_commit},
                retryable=True,
            )
        return source_root, load_environment_contract(source_root)

    def _schedule(
        self,
        *,
        kind: HostOperationKind,
        run_id: str,
        task_id: str,
        expected_state_version: int,
        expected_task_version: int,
        idempotency_key: str,
        request: dict[str, Any],
    ) -> HostOperation:
        try:
            return self.operations.ensure_pending(
                project_id=self.config.project_id,
                run_id=run_id,
                task_id=task_id,
                kind=kind,
                idempotency_key=idempotency_key,
                request=request,
                expected_state_version=expected_state_version,
                expected_task_version=expected_task_version,
            )
        except HostOperationError as exc:
            raise EnvironmentOperationError(exc.code, str(exc), retryable=exc.retryable) from exc

    def _mark_command_failed(
        self,
        operation: HostOperation,
        lease_owner: str,
        result: subprocess.CompletedProcess[bytes],
        code: str,
    ) -> HostOperation:
        failed = self.operations.mark_failed(
            operation.operation_id,
            expected_version=operation.state_version,
            lease_owner=lease_owner,
            error_code=code,
            result={
                "exit_code": result.returncode,
                "output_hash": _sha256_bytes(result.stdout + result.stderr),
            },
        )
        raise EnvironmentOperationError(
            code,
            f"environment command failed with exit code {result.returncode}",
            details={"operation_id": failed.operation_id},
            retryable=True,
        )

    def _require_command(
        self, argv: list[str], *, cwd: Path
    ) -> subprocess.CompletedProcess[bytes]:
        result = self._run(argv, cwd=cwd)
        if result.returncode != 0:
            raise EnvironmentOperationError(
                "ENVIRONMENT_NOT_REBUILDABLE",
                f"environment command failed with exit code {result.returncode}",
                details={"command_hash": _sha256_bytes("\0".join(argv).encode())},
                retryable=True,
            )
        return result

    def _run(
        self, argv: list[str], *, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        validate_environment_command(argv)
        try:
            return self.runner(argv, cwd or self.config.root, 1800.0)
        except subprocess.TimeoutExpired as exc:
            raise EnvironmentOperationError(
                "ENVIRONMENT_NOT_REBUILDABLE", "environment command timed out", retryable=True
            ) from exc
        except OSError as exc:
            raise EnvironmentOperationError(
                "OCI_BACKEND_UNAVAILABLE", str(exc), retryable=True
            ) from exc


def validate_environment_command(argv: list[str]) -> None:
    normalized = [item.casefold() for item in argv]
    if not normalized or normalized[0] not in {"docker", "podman"}:
        raise EnvironmentOperationError(
            "OCI_BACKEND_UNAVAILABLE", "environment operations require an OCI backend argv"
        )
    destructive_volume = (
        "volume" in normalized and any(item in {"rm", "prune"} for item in normalized)
    ) or (
        "down" in normalized
        and any(item in {"-v", "--volume", "--volumes"} for item in normalized)
    )
    dangerous_prune = "prune" in normalized and any(
        item in {"-a", "--all", "--volume", "--volumes"} for item in normalized
    )
    if destructive_volume or dangerous_prune:
        raise EnvironmentOperationError(
            "VOLUME_DELETE_APPROVAL_REQUIRED",
            "persistent OCI volume deletion is unavailable from the Agent operation path",
        )
    mutation_tokens = {"install", "add", "build", "wheel"}
    package_managers = {"pip", "pip3", "npm", "pnpm", "yarn", "poetry", "cargo"}
    for index, item in enumerate(normalized):
        if item in package_managers and mutation_tokens.intersection(normalized[index + 1 :]):
            raise EnvironmentOperationError(
                "HOST_DEPENDENCY_PRESENT",
                "runtime dependency mutation is forbidden during environment verification",
            )


def _compose_argv(contract: EnvironmentContract, project_ref: str) -> list[str]:
    project_name = "codex-os-" + re.sub(r"[^a-z0-9]", "", project_ref.casefold())[-24:]
    argv = [contract.oci_backend.value, "compose", "--project-name", project_name]
    for compose_file in contract.compose_files:
        argv.extend(["-f", compose_file])
    return argv


def _source_commit(root: Path, checkpoint: dict[str, Any], task_commit: str | None) -> str:
    candidates = (
        task_commit,
        checkpoint.get("last_commit_sha"),
        checkpoint.get("source_commit"),
    )
    for candidate in candidates:
        if isinstance(candidate, str) and re.fullmatch(
            r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", candidate
        ):
            return candidate.casefold()
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    commit = result.stdout.strip()
    if result.returncode != 0 or not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", commit):
        raise EnvironmentOperationError(
            "ENVIRONMENT_NOT_REBUILDABLE", "a full source Commit is required"
        )
    return commit.casefold()


def _path_allowed(path: str, allowed: tuple[str, ...]) -> bool:
    normalized = path.replace("\\", "/")
    return any(
        normalized == item.rstrip("/") or normalized.startswith(item.rstrip("/") + "/")
        for item in allowed
    )


def _parse_future(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EnvironmentOperationError("CONFIG_INVALID", "expires_at must be ISO 8601") from exc
    if parsed.tzinfo is None:
        raise EnvironmentOperationError("CONFIG_INVALID", "expires_at must include timezone")
    if parsed.astimezone(UTC) <= datetime.now(UTC):
        raise EnvironmentOperationError("APPROVAL_REQUIRED", "network approval has expired")
    return parsed.astimezone(UTC)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _run_command(argv: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        argv,
        cwd=cwd,
        check=False,
        capture_output=True,
        timeout=timeout,
    )


__all__ = [
    "EnvironmentOperationError",
    "EnvironmentOperationResult",
    "EnvironmentOperationService",
]
