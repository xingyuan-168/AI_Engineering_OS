from pathlib import Path

import pytest

from codex_ai_os.domain.config import (
    DEFAULT_EXECUTION_POLICY,
    EnvironmentMode,
    GitPushPolicy,
    NetworkMode,
    ProjectConfig,
    ProjectType,
    SandboxBackend,
)
from codex_ai_os.infrastructure.config import (
    ConfigError,
    ProjectRootError,
    load_execution_policy,
    load_project_config,
    load_yaml_model,
    merge_execution_policy,
)


def test_project_config_resolves_source_inside_root(tmp_path: Path) -> None:
    config = ProjectConfig(
        project_id="PROJECT-ERP",
        name="erp-pilot",
        root=tmp_path,
        project_type=ProjectType.BACKEND,
    )

    assert config.root == tmp_path.resolve()
    assert config.source_of_truth == (tmp_path / "docs").resolve()
    assert config.git_push_policy is GitPushPolicy.REMOTE_REQUIRED
    assert config.environment_mode is EnvironmentMode.LEGACY
    assert DEFAULT_EXECUTION_POLICY.sandbox is SandboxBackend.PODMAN


def test_project_config_rejects_source_outside_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="source_of_truth"):
        ProjectConfig(
            project_id="PROJECT-ERP",
            name="erp-pilot",
            root=tmp_path,
            source_of_truth=Path("..") / "outside",
        )


def test_project_loader_resolves_portable_root(tmp_path: Path) -> None:
    config_dir = tmp_path / ".codex-os"
    config_dir.mkdir()
    (tmp_path / "docs").mkdir()
    (config_dir / "project.yaml").write_text(
        "schema_version: '1.2'\n"
        "project_id: PROJECT-PORTABLE\n"
        "name: portable\n"
        "root: .\n"
        "source_of_truth: docs\n",
        encoding="utf-8",
    )

    config = load_project_config(tmp_path)

    assert config.root == tmp_path.resolve()
    assert config.source_of_truth == (tmp_path / "docs").resolve()
    assert config.environment_mode is EnvironmentMode.LEGACY


def test_project_loader_rejects_managed_worktree_and_guides_to_coordinator(
    tmp_path: Path,
) -> None:
    coordinator = tmp_path / "coordinator"
    worktree = tmp_path / "managed"
    git_dir = coordinator / ".git" / "worktrees" / "managed"
    git_dir.mkdir(parents=True)
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {git_dir.as_posix()}\n", encoding="utf-8")
    (git_dir / "commondir").write_text("../..\n", encoding="utf-8")

    with pytest.raises(ProjectRootError, match="use coordinator root") as caught:
        load_project_config(worktree)

    assert caught.value.code == "MANAGED_WORKTREE_ROOT"
    assert caught.value.coordinator_root == coordinator.resolve()


def test_yaml_loader_rejects_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "project.yaml"
    path.write_text(
        "schema_version: '1.0'\n"
        "project_id: PROJECT-ERP\n"
        "name: erp-pilot\n"
        f"root: '{tmp_path.as_posix()}'\n"
        "unknown: true\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="unknown"):
        load_yaml_model(path, ProjectConfig)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"allowed_commands": ["git", "curl"]}, "allowed_commands"),
        ({"allowed_commands": ["git", "cmd", "powershell"]}, "allowed_commands"),
        ({"allowed_mounts": ["worktree", "host-root"]}, "allowed_mounts"),
        ({"network": NetworkMode.ALLOWLIST}, "network"),
        ({"allow_host_execution": True}, "host execution"),
        ({"max_duration_seconds": 1801}, "cannot increase"),
        ({"approval_for": ["delete"]}, "cannot remove"),
        ({"sandbox": "docker"}, "sandbox"),
    ],
)
def test_policy_merge_rejects_relaxation(override: dict[str, object], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        merge_execution_policy(DEFAULT_EXECUTION_POLICY, override)


def test_policy_merge_allows_tightening() -> None:
    tightened = merge_execution_policy(
        DEFAULT_EXECUTION_POLICY,
        {
            "allowed_commands": ["git", "python", "pytest"],
            "max_duration_seconds": 600,
            "approval_for": [
                "network",
                "migration",
                "delete",
                "credential",
                "release",
                "write",
            ],
        },
    )

    assert tightened.allowed_commands == frozenset({"git", "python", "pytest"})
    assert tightened.max_duration_seconds == 600
    assert "write" in tightened.approval_for


def test_execution_policy_loader_supports_explicit_podman(tmp_path: Path) -> None:
    policy_dir = tmp_path / ".codex-os"
    policy_dir.mkdir()
    (policy_dir / "execution-policy.yaml").write_text(
        "schema_version: '1.0'\nsandbox: podman\n",
        encoding="utf-8",
    )

    assert load_execution_policy(tmp_path).sandbox is SandboxBackend.PODMAN
