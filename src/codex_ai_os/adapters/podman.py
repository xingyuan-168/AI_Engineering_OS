"""Podman implementation of the locked OCI sandbox contract."""

from __future__ import annotations

import shutil
from pathlib import Path

from codex_ai_os.adapters.docker import (
    DEFAULT_EXECUTION_POLICY,
    DockerSandbox,
    DockerSandboxError,
    ProcessRunner,
    SandboxRequest,
)
from codex_ai_os.adapters.worktree import WorktreeManager
from codex_ai_os.domain.config import ExecutionPolicy


class PodmanSandbox(DockerSandbox):
    """Run the same policy through a local or machine-backed Podman service."""

    engine_name = "Podman"

    def __init__(
        self,
        project_root: Path,
        *,
        policy: ExecutionPolicy = DEFAULT_EXECUTION_POLICY,
        worktree_manager: WorktreeManager | None = None,
        podman_executable: str | None = None,
        runner: ProcessRunner | None = None,
        max_output_bytes: int = 1024 * 1024,
    ) -> None:
        executable = shutil.which("podman") if podman_executable is None else podman_executable
        super().__init__(
            project_root,
            policy=policy,
            worktree_manager=worktree_manager,
            docker_executable=executable or "",
            runner=runner,
            max_output_bytes=max_output_bytes,
        )

    def build_command(self, request: SandboxRequest) -> list[str]:
        command = super().build_command(request)
        storage_index = command.index("--storage-opt")
        del command[storage_index : storage_index + 2]
        read_only_index = command.index("--read-only")
        command.insert(read_only_index + 1, "--read-only-tmpfs=false")
        return command

    def _require_engine_ready(self, image: str) -> None:
        assert self.docker_executable is not None
        info = self.runner(
            [self.docker_executable, "info", "--format", "{{.Version.Version}}"],
            15.0,
        )
        if info.returncode != 0:
            raise DockerSandboxError(
                "SANDBOX_UNAVAILABLE",
                _podman_error("Podman service or machine is unavailable", info.stderr),
            )
        inspected = self.runner(
            [self.docker_executable, "image", "inspect", image],
            30.0,
        )
        if inspected.returncode != 0:
            raise DockerSandboxError(
                "SANDBOX_IMAGE_UNAVAILABLE",
                _podman_error("approved Podman image is not present", inspected.stderr),
            )


def _podman_error(prefix: str, stderr: bytes) -> str:
    detail = stderr[:4096].decode("utf-8", errors="replace").strip()
    return f"{prefix}: {detail or 'command failed'}"
