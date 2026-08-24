from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from codex_ai_os.adapters.docker import (
    DEFAULT_EXECUTION_IMAGE,
    DockerSandbox,
    DockerSandboxError,
    MountKind,
    SandboxMount,
    SandboxRequest,
)
from codex_ai_os.adapters.worktree import WorktreeSpec, WorktreeState
from codex_ai_os.domain.config import RiskLevel


class _AssignedWorktree:
    def inspect(self, spec: WorktreeSpec) -> WorktreeState:
        return WorktreeState(spec=spec, head_commit=spec.base_commit, dirty=False)

    def plan(self, **_arguments: str) -> WorktreeSpec:
        raise AssertionError("not used")

    def create(self, **_arguments: str) -> WorktreeState:
        raise AssertionError("not used")

    def remove(self, spec: WorktreeSpec, *, approved: bool, merged_into: str) -> None:
        raise AssertionError("not used")


def test_command_enforces_offline_non_root_read_only_resource_limits(tmp_path: Path) -> None:
    request = _request(tmp_path)
    sandbox = DockerSandbox(
        tmp_path,
        worktree_manager=_AssignedWorktree(),
        docker_executable="docker",
    )

    command = sandbox.build_command(request)

    assert command[:3] == ["docker", "run", "--rm"]
    assert _option(command, "--user") == "65532:65532"
    assert _option(command, "--network") == "none"
    assert "--read-only" in command
    assert _option(command, "--cap-drop") == "ALL"
    assert _option(command, "--security-opt") == "no-new-privileges:true"
    assert _option(command, "--pids-limit") == "256"
    assert _option(command, "--memory") == "4g"
    assert _option(command, "--cpus") == "2"
    assert _option(command, "--storage-opt") == "size=10G"
    assert _option(command, "--pull=never") is None
    assert "/tmp:rw,noexec,nosuid,nodev,size=1g" in command
    assert "/deps:rw,nosuid,nodev,size=1g" in command
    assert "GIT_CONFIG_KEY_0=safe.directory" in command
    assert "GIT_CONFIG_VALUE_0=/workspace" in command  # pragma: allowlist secret
    assert DEFAULT_EXECUTION_IMAGE in command
    assert all("docker.sock" not in argument for argument in command)
    assert all(str(tmp_path.resolve()) != argument for argument in command)
    worktree_mount = _option(command, "--mount")
    assert worktree_mount is not None
    assert "target=/workspace,rw" in worktree_mount


@pytest.mark.parametrize(
    ("command", "message"),
    [
        (("curl", "https://example.invalid"), "command is not allowed"),
        (("git", "reset", "--hard"), "Git subcommand is blocked"),
        (("git", "-C", "/", "status"), "path redirection"),
        ((), "command is not allowed"),
    ],
)
def test_command_policy_rejects_unapproved_or_escaping_commands(
    tmp_path: Path, command: tuple[str, ...], message: str
) -> None:
    request = _request(tmp_path, command=command)

    with pytest.raises(DockerSandboxError, match=message):
        DockerSandbox(
            tmp_path,
            worktree_manager=_AssignedWorktree(),
            docker_executable="docker",
        ).build_command(request)


def test_image_must_be_locked_to_full_digest(tmp_path: Path) -> None:
    request = _request(tmp_path, image="python:3.12-slim-bookworm")

    with pytest.raises(DockerSandboxError, match="full sha256 digest"):
        DockerSandbox(
            tmp_path,
            worktree_manager=_AssignedWorktree(),
            docker_executable="docker",
        ).build_command(request)


def test_mount_source_must_stay_in_approved_project_directory(tmp_path: Path) -> None:
    (tmp_path / ".codex-os" / "artifacts").mkdir(parents=True)
    outside = tmp_path.parent / "outside-artifacts"
    outside.mkdir(exist_ok=True)
    request = _request(
        tmp_path,
        mounts=(SandboxMount(MountKind.ARTIFACTS, outside),),
    )

    with pytest.raises(DockerSandboxError, match="mount source escapes"):
        DockerSandbox(
            tmp_path,
            worktree_manager=_AssignedWorktree(),
            docker_executable="docker",
        ).build_command(request)


def test_git_metadata_cache_overlays_windows_worktree_pointer(tmp_path: Path) -> None:
    metadata = tmp_path / ".codex-os" / "cache" / "verification" / "git" / ".git"
    metadata.mkdir(parents=True)
    pointer = metadata.parent / "worktree.git"
    pointer.write_text("gitdir: /git-metadata\n", encoding="utf-8")
    request = _request(
        tmp_path,
        mounts=(
            SandboxMount(MountKind.GIT_METADATA, metadata, read_only=True),
            SandboxMount(MountKind.GIT_POINTER, pointer, read_only=True),
        ),
    )

    command = DockerSandbox(
        tmp_path,
        worktree_manager=_AssignedWorktree(),
        docker_executable="docker",
    ).build_command(request)

    mounts = [command[index + 1] for index, value in enumerate(command) if value == "--mount"]
    assert any("target=/git-metadata,readonly" in mount for mount in mounts)
    assert any("target=/workspace/.git,readonly" in mount for mount in mounts)


def test_run_blocks_when_docker_cli_is_missing(tmp_path: Path) -> None:
    request = _request(tmp_path)

    with pytest.raises(DockerSandboxError, match="Docker CLI is not available") as exc:
        DockerSandbox(
            tmp_path,
            worktree_manager=_AssignedWorktree(),
            docker_executable="",
        ).run(request)

    assert exc.value.code == "SANDBOX_UNAVAILABLE"


def test_run_checks_daemon_and_image_and_redacts_output(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(arguments: list[str], _timeout: float) -> subprocess.CompletedProcess[bytes]:
        calls.append(arguments)
        if "run" in arguments:
            return subprocess.CompletedProcess(
                arguments,
                0,
                stdout=b"token=secret123 ghp_ABCDEFGHIJKLMNOPQRSTUV\n",
                stderr=b"",
            )
        return subprocess.CompletedProcess(arguments, 0, stdout=b"ok\n", stderr=b"")

    request = _request(tmp_path)
    result = DockerSandbox(
        tmp_path,
        worktree_manager=_AssignedWorktree(),
        docker_executable="docker",
        runner=runner,
    ).run(request)

    assert [call[1:3] for call in calls] == [
        ["version", "--format"],
        ["image", "inspect"],
        ["run", "--rm"],
    ]
    assert result.exit_code == 0
    assert result.worktree_dirty is False
    assert result.image_digest.startswith("sha256:")
    assert "secret123" not in result.stdout
    assert "ghp_" not in result.stdout
    assert result.stdout.count("[REDACTED]") == 2
    assert len(result.command_hash) == 64


@pytest.mark.parametrize(
    ("failed_command", "code", "message"),
    [
        ("version", "SANDBOX_UNAVAILABLE", "Docker daemon is unavailable"),
        ("inspect", "SANDBOX_IMAGE_UNAVAILABLE", "approved Docker image is not present"),
    ],
)
def test_run_blocks_when_daemon_or_locked_image_is_unavailable(
    tmp_path: Path, failed_command: str, code: str, message: str
) -> None:
    def runner(arguments: list[str], _timeout: float) -> subprocess.CompletedProcess[bytes]:
        failed = failed_command in arguments
        return subprocess.CompletedProcess(
            arguments,
            1 if failed else 0,
            stdout=b"",
            stderr=b"simulated unavailable\n" if failed else b"",
        )

    with pytest.raises(DockerSandboxError, match=message) as exc:
        DockerSandbox(
            tmp_path,
            worktree_manager=_AssignedWorktree(),
            docker_executable="docker",
            runner=runner,
        ).run(_request(tmp_path))

    assert exc.value.code == code


def test_timeout_stops_named_container_and_reports_limit(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(arguments: list[str], timeout: float) -> subprocess.CompletedProcess[bytes]:
        calls.append(arguments)
        if "run" in arguments:
            raise subprocess.TimeoutExpired(arguments, timeout)
        return subprocess.CompletedProcess(arguments, 0, stdout=b"ok", stderr=b"")

    request = _request(tmp_path)
    sandbox = DockerSandbox(
        tmp_path,
        worktree_manager=_AssignedWorktree(),
        docker_executable="docker",
        runner=runner,
    )

    with pytest.raises(DockerSandboxError, match="exceeded 10 seconds") as exc:
        sandbox.run(request)

    assert exc.value.code == "EXECUTION_LIMIT_EXCEEDED"
    assert calls[-1] == ["docker", "stop", "--time", "5", "codex-os-exec-001"]


def _request(
    root: Path,
    *,
    command: tuple[str, ...] = ("python", "-m", "pytest", "-q"),
    mounts: tuple[SandboxMount, ...] = (),
    image: str = DEFAULT_EXECUTION_IMAGE,
) -> SandboxRequest:
    worktree = root / ".worktrees" / "RUN-001" / "qa" / "TASK-001"
    worktree.mkdir(parents=True, exist_ok=True)
    return SandboxRequest(
        execution_id="EXEC-001",
        task_id="TASK-001",
        worktree=WorktreeSpec(
            workflow_id="RUN-001",
            agent="qa",
            task_id="TASK-001",
            branch="agent/qa/TASK-001",
            path=worktree,
            base_commit="a" * 40,
        ),
        command=command,
        risk_level=RiskLevel.MEDIUM,
        mounts=mounts,
        image=image,
        timeout_seconds=10,
    )


def _option(command: list[str], name: str) -> str | None:
    for index, value in enumerate(command):
        if value == name:
            if "=" in name or index + 1 >= len(command) or command[index + 1].startswith("--"):
                return None
            return command[index + 1]
    raise AssertionError(f"option not found: {name}")
