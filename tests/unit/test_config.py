from pathlib import Path

import pytest

from codex_ai_os.domain.config import (
    DEFAULT_EXECUTION_POLICY,
    GitPushPolicy,
    NetworkMode,
    ProjectConfig,
    ProjectType,
)
from codex_ai_os.infrastructure.config import ConfigError, load_yaml_model, merge_execution_policy


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


def test_project_config_rejects_source_outside_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="source_of_truth"):
        ProjectConfig(
            project_id="PROJECT-ERP",
            name="erp-pilot",
            root=tmp_path,
            source_of_truth=Path("..") / "outside",
        )


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
        ({"allowed_mounts": ["worktree", "host-root"]}, "allowed_mounts"),
        ({"network": NetworkMode.ALLOWLIST}, "network"),
        ({"allow_host_execution": True}, "host execution"),
        ({"max_duration_seconds": 1801}, "cannot increase"),
        ({"approval_for": ["delete"]}, "cannot remove"),
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
