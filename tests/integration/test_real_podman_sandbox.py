from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest

from codex_ai_os.adapters.docker import SandboxRequest
from codex_ai_os.adapters.podman import PodmanSandbox
from codex_ai_os.adapters.worktree import GitWorktreeManager
from codex_ai_os.domain.config import RiskLevel

pytestmark = [
    pytest.mark.real_oci,
    pytest.mark.skipif(
        os.getenv("CODEX_OS_REAL_OCI") != "1",
        reason="set CODEX_OS_REAL_OCI=1 to run against a local Podman service",
    ),
]


def test_real_podman_enforces_locked_sandbox_policy(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    _git(project_root, "init", "-b", "main")
    _git(project_root, "config", "user.name", "Codex OCI Verification")
    _git(project_root, "config", "user.email", "codex-oci@example.invalid")
    _git(
        project_root,
        "commit",
        "--allow-empty",
        "-m",
        "chore: initialize OCI verification fixture",
    )

    manager = GitWorktreeManager(project_root)
    state = manager.create(
        workflow_id="RUN-REAL-OCI",
        agent="qa",
        task_id="TASK-REAL-OCI",
    )
    podman_executable = os.getenv("CODEX_OS_PODMAN") or shutil.which("podman")
    assert podman_executable is not None, "Podman executable was not found"

    request = SandboxRequest(
        execution_id="EXEC-REAL-OCI-001",
        task_id=state.spec.task_id,
        worktree=state.spec,
        command=("python", "-c", _POLICY_PROBE),
        risk_level=RiskLevel.HIGH,
        timeout_seconds=30,
    )
    sandbox = PodmanSandbox(
        project_root,
        worktree_manager=manager,
        podman_executable=podman_executable,
    )

    command = sandbox.build_command(request)
    result = sandbox.run(request)

    evidence = {
        "result": asdict(result),
        "policy": {
            "network": _option(command, "--network"),
            "user": _option(command, "--user"),
            "read_only": "--read-only" in command,
            "cap_drop": _option(command, "--cap-drop"),
            "no_new_privileges": _option(command, "--security-opt"),
            "pids_limit": _option(command, "--pids-limit"),
            "memory": _option(command, "--memory"),
            "cpus": _option(command, "--cpus"),
            "pull": "--pull=never" in command,
        },
    }
    print(json.dumps(evidence, indent=2))

    assert result.exit_code == 0, result.stderr
    assert result.worktree_dirty is False
    assert "uid=65532" in result.stdout
    assert "gid=65532" in result.stdout
    assert "rootfs_write_blocked_errno=" in result.stdout
    assert "network_blocked=" in result.stdout
    assert "sandbox_policy=passed" in result.stdout


def _git(root: Path, *arguments: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


def _option(command: list[str], name: str) -> str:
    index = command.index(name)
    return command[index + 1]


_POLICY_PROBE = """
import os
import socket

print(f"uid={os.getuid()}")
print(f"gid={os.getgid()}")
if os.getuid() != 65532 or os.getgid() != 65532:
    raise SystemExit("unexpected container identity")
try:
    with open("/codex-os-rootfs-probe", "w", encoding="utf-8") as handle:
        handle.write("unexpected")
except OSError as exc:
    print(f"rootfs_write_blocked_errno={exc.errno}")
else:
    raise SystemExit("root filesystem unexpectedly writable")
sock = socket.socket()
sock.settimeout(2)
try:
    sock.connect(("1.1.1.1", 53))
except OSError as exc:
    print(f"network_blocked={type(exc).__name__}")
else:
    raise SystemExit("network unexpectedly available")
finally:
    sock.close()
print("sandbox_policy=passed")
""".strip()
