from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from codex_ai_os.adapters.docker import DEFAULT_EXECUTION_IMAGE, DockerSandboxError, SandboxRequest
from codex_ai_os.adapters.podman import PodmanSandbox
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


def test_podman_command_preserves_oci_isolation_policy(tmp_path: Path) -> None:
    command = PodmanSandbox(
        tmp_path,
        worktree_manager=_AssignedWorktree(),
        podman_executable="podman",
    ).build_command(_request(tmp_path))

    assert command[:3] == ["podman", "run", "--rm"]
    assert "--read-only" in command
    assert "--read-only-tmpfs=false" in command
    assert "--storage-opt" not in command
    assert _option(command, "--network") == "none"
    assert _option(command, "--user") == "65532:65532"
    assert _option(command, "--pids-limit") == "256"
    assert _option(command, "--memory") == "4g"
    assert _option(command, "--cpus") == "2"
    assert _option(command, "--tmpfs").endswith("size=1g")
    assert DEFAULT_EXECUTION_IMAGE in command


def test_podman_checks_service_and_locked_image(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(arguments: list[str], _timeout: float) -> subprocess.CompletedProcess[bytes]:
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, stdout=b"ok", stderr=b"")

    result = PodmanSandbox(
        tmp_path,
        worktree_manager=_AssignedWorktree(),
        podman_executable="podman",
        runner=runner,
    ).run(_request(tmp_path))

    assert [call[1:3] for call in calls] == [
        ["info", "--format"],
        ["image", "inspect"],
        ["run", "--rm"],
    ]
    assert result.exit_code == 0


def test_podman_reports_missing_cli(tmp_path: Path) -> None:
    with pytest.raises(DockerSandboxError, match="Podman CLI") as exc:
        PodmanSandbox(
            tmp_path,
            worktree_manager=_AssignedWorktree(),
            podman_executable="",
        ).run(_request(tmp_path))

    assert exc.value.code == "SANDBOX_UNAVAILABLE"


def _request(root: Path) -> SandboxRequest:
    worktree = root / ".worktrees" / "RUN-001" / "qa" / "TASK-001"
    worktree.mkdir(parents=True, exist_ok=True)
    return SandboxRequest(
        execution_id="EXEC-PODMAN-001",
        task_id="TASK-001",
        worktree=WorktreeSpec(
            workflow_id="RUN-001",
            agent="qa",
            task_id="TASK-001",
            branch="agent/qa/TASK-001",
            path=worktree,
            base_commit="a" * 40,
        ),
        command=("pytest", "-q"),
        risk_level=RiskLevel.MEDIUM,
        timeout_seconds=10,
    )


def _option(command: list[str], name: str) -> str:
    index = command.index(name)
    return command[index + 1]
