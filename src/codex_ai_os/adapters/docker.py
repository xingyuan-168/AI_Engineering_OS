"""Locked-down Docker command construction and execution for agent worktrees."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from codex_ai_os.adapters.worktree import (
    GitWorktreeError,
    GitWorktreeManager,
    WorktreeManager,
    WorktreeSpec,
)
from codex_ai_os.domain.config import (
    DEFAULT_EXECUTION_POLICY,
    ExecutionPolicy,
    RiskLevel,
)
from codex_ai_os.domain.versions import RUNTIME_VERSIONS

DEFAULT_EXECUTION_IMAGE = RUNTIME_VERSIONS.execution_image


class DockerSandboxError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class MountKind(StrEnum):
    ARTIFACTS = "artifacts"
    CACHE = "cache"
    GIT_METADATA = "git-metadata"
    GIT_POINTER = "git-pointer"


@dataclass(frozen=True, slots=True)
class SandboxMount:
    kind: MountKind
    source: Path
    read_only: bool = False


@dataclass(frozen=True, slots=True)
class SandboxRequest:
    execution_id: str
    task_id: str
    worktree: WorktreeSpec
    command: tuple[str, ...]
    risk_level: RiskLevel
    mounts: tuple[SandboxMount, ...] = ()
    image: str = DEFAULT_EXECUTION_IMAGE
    timeout_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class SandboxResult:
    execution_id: str
    command_hash: str
    image_digest: str
    container_name: str
    exit_code: int
    stdout: str
    stderr: str
    worktree_dirty: bool
    started_at: str
    ended_at: str


ProcessRunner = Callable[[list[str], float], subprocess.CompletedProcess[bytes]]


class SandboxExecutor(Protocol):
    def run(self, request: SandboxRequest) -> SandboxResult: ...


class DockerSandbox:
    """Run one argv command in a non-root, offline, resource-limited container."""

    engine_name = "Docker"

    def __init__(
        self,
        project_root: Path,
        *,
        policy: ExecutionPolicy = DEFAULT_EXECUTION_POLICY,
        worktree_manager: WorktreeManager | None = None,
        docker_executable: str | None = None,
        runner: ProcessRunner | None = None,
        max_output_bytes: int = 1024 * 1024,
    ) -> None:
        self.root = project_root.resolve()
        self.policy = policy
        self.worktree_manager = worktree_manager or GitWorktreeManager(self.root)
        self.docker_executable = (
            shutil.which("docker") if docker_executable is None else docker_executable
        )
        self.runner = runner or _run_process
        self.max_output_bytes = max_output_bytes

    def build_command(self, request: SandboxRequest) -> list[str]:
        self._validate_request(request)
        container_name = _container_name(request.execution_id)
        command = [
            self.docker_executable or "docker",
            "run",
            "--rm",
            "--name",
            container_name,
            "--pull=never",
            "--user",
            "65532:65532",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            "256",
            "--memory",
            "4g",
            "--cpus",
            "2",
            "--storage-opt",
            "size=10G",
            "--ulimit",
            "nofile=1024:1024",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=1g",
            "--tmpfs",
            "/deps:rw,nosuid,nodev,size=1g",
            "--tmpfs",
            "/dev/shm:rw,noexec,nosuid,nodev,size=64m",
            "--env",
            "HOME=/tmp",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "GIT_CONFIG_COUNT=1",
            "--env",
            "GIT_CONFIG_KEY_0=safe.directory",
            "--env",
            "GIT_CONFIG_VALUE_0=/workspace",  # pragma: allowlist secret
            "--workdir",
            "/workspace",
            "--mount",
            _mount_argument(request.worktree.path, "/workspace", read_only=False),
        ]
        targets: set[str] = {"/workspace"}
        for mount in request.mounts:
            target = _mount_target(mount.kind)
            if target in targets:
                raise DockerSandboxError(
                    "SANDBOX_POLICY_VIOLATION", f"duplicate container mount target: {target}"
                )
            targets.add(target)
            command.extend(
                [
                    "--mount",
                    _mount_argument(mount.source, target, read_only=mount.read_only),
                ]
            )
        return [*command, request.image, *request.command]

    def run(self, request: SandboxRequest) -> SandboxResult:
        if not self.docker_executable:
            raise DockerSandboxError(
                "SANDBOX_UNAVAILABLE",
                f"{self.engine_name} CLI is not available on the host",
            )
        self._require_engine_ready(request.image)
        command = self.build_command(request)
        started_at = _utc_now()
        timeout = float(request.timeout_seconds or self.policy.max_duration_seconds)
        try:
            completed = self.runner(command, timeout)
        except subprocess.TimeoutExpired as exc:
            container_name = _container_name(request.execution_id)
            with suppress(OSError, subprocess.SubprocessError):
                self.runner(
                    [self.docker_executable, "stop", "--time", "5", container_name],
                    15.0,
                )
            raise DockerSandboxError(
                "EXECUTION_LIMIT_EXCEEDED",
                f"container execution exceeded {timeout:g} seconds and was stopped",
            ) from exc
        stdout = _redact_and_limit(completed.stdout, self.max_output_bytes)
        stderr = _redact_and_limit(completed.stderr, self.max_output_bytes)
        try:
            worktree_dirty = self.worktree_manager.inspect(request.worktree).dirty
        except GitWorktreeError as exc:
            raise DockerSandboxError("WORKTREE_BLOCKED", str(exc)) from exc
        return SandboxResult(
            execution_id=request.execution_id,
            command_hash=command_hash(request.command),
            image_digest=request.image.rsplit("@", 1)[1],
            container_name=_container_name(request.execution_id),
            exit_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            worktree_dirty=worktree_dirty,
            started_at=started_at,
            ended_at=_utc_now(),
        )

    def _require_engine_ready(self, image: str) -> None:
        assert self.docker_executable is not None
        version = self.runner(
            [self.docker_executable, "version", "--format", "{{.Server.Version}}"],
            15.0,
        )
        if version.returncode != 0:
            raise DockerSandboxError(
                "SANDBOX_UNAVAILABLE",
                _process_error("Docker daemon is unavailable", version),
            )
        inspected = self.runner(
            [self.docker_executable, "image", "inspect", image],
            30.0,
        )
        if inspected.returncode != 0:
            raise DockerSandboxError(
                "SANDBOX_IMAGE_UNAVAILABLE",
                _process_error("approved Docker image is not present", inspected),
            )

    def _validate_request(self, request: SandboxRequest) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", request.execution_id):
            raise DockerSandboxError("SANDBOX_POLICY_VIOLATION", "execution_id is unsafe")
        if request.task_id != request.worktree.task_id:
            raise DockerSandboxError(
                "SANDBOX_POLICY_VIOLATION", "execution task does not own the worktree"
            )
        self.worktree_manager.inspect(request.worktree)
        _validate_image(request.image)
        _validate_command(request.command, self.policy)
        timeout = request.timeout_seconds or self.policy.max_duration_seconds
        if timeout < 1 or timeout > self.policy.max_duration_seconds:
            raise DockerSandboxError(
                "SANDBOX_POLICY_VIOLATION",
                f"execution timeout must be within 1..{self.policy.max_duration_seconds}",
            )
        for mount in request.mounts:
            policy_kind = (
                "cache"
                if mount.kind in {MountKind.GIT_METADATA, MountKind.GIT_POINTER}
                else mount.kind.value
            )
            if policy_kind not in self.policy.allowed_mounts:
                raise DockerSandboxError(
                    "SANDBOX_POLICY_VIOLATION",
                    f"mount kind is not allowed: {mount.kind.value}",
                )
            expected_root = (
                self.root / ".codex-os" / "artifacts"
                if mount.kind is MountKind.ARTIFACTS
                else self.root / ".codex-os" / "cache"
            )
            _validate_mount_source(
                mount.source,
                expected_root,
                self.root,
                allow_file=mount.kind is MountKind.GIT_POINTER,
            )


def _validate_image(image: str) -> None:
    if not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", image):
        raise DockerSandboxError(
            "SANDBOX_POLICY_VIOLATION", "OCI image must use a full sha256 digest"
        )


def _validate_command(command: tuple[str, ...], policy: ExecutionPolicy) -> None:
    if not command or command[0] not in policy.allowed_commands:
        executable = command[0] if command else "<empty>"
        raise DockerSandboxError(
            "SANDBOX_POLICY_VIOLATION", f"command is not allowed: {executable}"
        )
    if any("\x00" in argument or len(argument) > 8192 for argument in command):
        raise DockerSandboxError("SANDBOX_POLICY_VIOLATION", "command contains an unsafe argument")
    if command[0] == "git":
        forbidden = {"clean", "push", "reset"}
        if any(argument in {"-C", "--git-dir", "--work-tree"} for argument in command[1:]):
            raise DockerSandboxError(
                "SANDBOX_POLICY_VIOLATION", "Git path redirection is not allowed"
            )
        subcommand = next((arg for arg in command[1:] if not arg.startswith("-")), "")
        if subcommand in forbidden:
            raise DockerSandboxError(
                "SANDBOX_POLICY_VIOLATION",
                f"Git subcommand is blocked in the offline sandbox: {subcommand}",
            )


def _validate_mount_source(
    source: Path, expected_root: Path, project_root: Path, *, allow_file: bool = False
) -> None:
    raw = source if source.is_absolute() else project_root / source
    if "," in str(raw):
        raise DockerSandboxError("SANDBOX_POLICY_VIOLATION", "mount source cannot contain a comma")
    try:
        resolved = raw.resolve(strict=True)
        allowed = expected_root.resolve(strict=True)
    except OSError as exc:
        raise DockerSandboxError(
            "SANDBOX_POLICY_VIOLATION", f"mount source is missing: {raw}"
        ) from exc
    expected_type = resolved.is_file() if allow_file else resolved.is_dir()
    if not expected_type or (resolved != allowed and not resolved.is_relative_to(allowed)):
        raise DockerSandboxError(
            "SANDBOX_POLICY_VIOLATION",
            f"mount source escapes {expected_root}: {resolved}",
        )
    current = raw
    while current.is_relative_to(project_root):
        if _is_link_like(current):
            raise DockerSandboxError(
                "SANDBOX_POLICY_VIOLATION",
                f"mount source uses a symlink or junction: {current}",
            )
        if current == project_root:
            break
        current = current.parent


def _mount_target(kind: MountKind) -> str:
    if kind is MountKind.ARTIFACTS:
        return "/artifacts"
    if kind is MountKind.GIT_METADATA:
        return "/git-metadata"
    if kind is MountKind.GIT_POINTER:
        return "/workspace/.git"
    return "/cache"


def _mount_argument(source: Path, target: str, *, read_only: bool) -> str:
    mode = "readonly" if read_only else "rw"
    return f"type=bind,source={source.resolve(strict=True)},target={target},{mode}"


def _container_name(execution_id: str) -> str:
    return f"codex-os-{execution_id.lower()}"


def command_hash(command: tuple[str, ...]) -> str:
    encoded = json.dumps(command, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _run_process(arguments: list[str], timeout: float) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        arguments,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def _process_error(prefix: str, process: subprocess.CompletedProcess[bytes]) -> str:
    stderr = _redact_and_limit(process.stderr, 4096)
    return f"{prefix}: {stderr or f'exit code {process.returncode}'}"


def _redact_and_limit(content: bytes, limit: int) -> str:
    text = content[:limit].decode("utf-8", errors="replace")
    patterns = (
        r"(?i)(authorization:\s*bearer\s+)[^\s]+",
        r"(?i)((?:password|token|secret|api[_-]?key)\s*[=:]\s*)[^\s]+",
        r"\bghp_[A-Za-z0-9]{20,}\b",
        r"\bgithub_pat_[A-Za-z0-9_]{20,}\b",
    )
    for pattern in patterns:
        text = re.sub(pattern, _redacted_match, text)
    if len(content) > limit:
        text += "\n[OUTPUT_TRUNCATED]"
    return text


def _redacted_match(match: re.Match[str]) -> str:
    prefix = match.group(1) if match.lastindex else ""
    return f"{prefix}[REDACTED]"


def _is_link_like(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction is not None and is_junction())


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
