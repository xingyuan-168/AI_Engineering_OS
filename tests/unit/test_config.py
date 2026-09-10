from __future__ import annotations

from pathlib import Path

import pytest

from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.infrastructure.config import (
    ConfigError,
    ProjectRootError,
    load_project_config,
)


def _initialize(root: Path) -> None:
    ProjectInitializer().initialize(
        root,
        project_id="PROJECT-CFG",
        name="Config",
        project_type="backend",
        risk_level="high",
        include=frozenset(),
    )


def test_round_trip_loads_initialized_config(tmp_path: Path) -> None:
    _initialize(tmp_path)
    config = load_project_config(tmp_path)
    assert config.project_id == "PROJECT-CFG"
    assert config.name == "Config"
    assert config.project_type.value == "backend"
    assert config.risk_level.value == "high"
    assert config.root == tmp_path.resolve()


def test_missing_config_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_project_config(tmp_path)


def test_root_mismatch_is_rejected(tmp_path: Path) -> None:
    _initialize(tmp_path)
    other = tmp_path.parent / "other-root"
    target = other / ".codex-os"
    target.mkdir(parents=True)
    text = (tmp_path / ".codex-os" / "project.yaml").read_text(encoding="utf-8")
    text = text.replace("root: .", "root: " + str(tmp_path.resolve()).replace("\\", "/"))
    (target / "project.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ProjectRootError) as excinfo:
        load_project_config(other)
    assert excinfo.value.code == "PROJECT_ROOT_MISMATCH"


def test_managed_worktree_root_redirects_to_coordinator(tmp_path: Path) -> None:
    main_root = tmp_path / "main"
    worktree_root = tmp_path / "wt"
    worktree_root.mkdir(parents=True)
    (worktree_root / ".git").write_text(
        "gitdir: " + str(main_root / ".git" / "worktrees" / "wt").replace("\\", "/") + "\n",
        encoding="utf-8",
    )
    git_dir = main_root / ".git" / "worktrees" / "wt"
    git_dir.mkdir(parents=True)
    (git_dir / "commondir").write_text(
        str(main_root / ".git").replace("\\", "/") + "\n", encoding="utf-8"
    )
    with pytest.raises(ProjectRootError) as excinfo:
        load_project_config(worktree_root)
    assert excinfo.value.code == "MANAGED_WORKTREE_ROOT"
