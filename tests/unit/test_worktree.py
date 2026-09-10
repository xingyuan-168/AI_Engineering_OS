from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from codex_ai_os.core.worktree import WorktreeError, WorktreeManager
from codex_ai_os.infrastructure.database import Database


def _git_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".git").mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "README.md").write_text("# t\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


def _manager(root: Path) -> WorktreeManager:
    database = Database(root / ".codex-os" / "state.db")
    database.migrate()
    return WorktreeManager(root, database=database)


def test_prepare_check_finish_cleanup_cycle(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    manager = _manager(tmp_path)
    record = manager.prepare(name="demo", task_id="TASK-1")
    assert record.name == "demo"
    assert record.branch == "codex/wt-demo"
    assert record.status == "active"
    assert (tmp_path / ".worktrees" / "demo").is_dir()
    assert len(manager.list()) == 1

    checked = manager.check(name="demo")
    assert checked.task_id == "TASK-1"

    manager.finish(name="demo")
    manager.cleanup(name="demo")
    assert manager.list() == ()
    assert not (tmp_path / ".worktrees" / "demo").exists()


def test_finish_refuses_dirty_worktree(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    manager = _manager(tmp_path)
    manager.prepare(name="dirty")
    (tmp_path / ".worktrees" / "dirty" / "extra.txt").write_text("wip", encoding="utf-8")
    with pytest.raises(WorktreeError) as excinfo:
        manager.finish(name="dirty")
    assert excinfo.value.code == "WORKTREE_DIRTY"
    manager.cleanup(name="dirty", force=True)
    assert manager.list() == ()


def test_cleanup_unregisters_so_names_reuse(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    manager = _manager(tmp_path)
    manager.prepare(name="reuse")
    manager.cleanup(name="reuse")
    again = manager.prepare(name="reuse")
    assert again.name == "reuse"
    manager.cleanup(name="reuse")
