from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from codex_ai_os.core.worktree import WorktreeError, WorktreeManager
from codex_ai_os.infrastructure.database import Database


def _git_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".git").mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "README.md").write_text("# t\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


def _manager(root: Path) -> WorktreeManager:
    database = Database(root / ".codex-os" / "state.db")
    database.migrate()
    return WorktreeManager(root, database=database)


def _commit_all(root: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=root, check=True)


def test_prepare_check_finish_cleanup_cycle(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    manager = _manager(tmp_path)
    record = manager.prepare(name="demo", task_id="TASK-1")
    assert record.name == "demo"
    assert record.branch == "codex/wt-demo"
    assert record.status == "active"
    assert record.target_branch == "main"
    assert (tmp_path / ".worktrees" / "demo").is_dir()
    assert len(manager.list()) == 1

    checked = manager.check(name="demo")
    assert checked.task_id == "TASK-1"

    finished = manager.finish(name="demo")
    assert finished.status == "ready"
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


def test_cleanup_refuses_dirty_worktree_without_force(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    manager = _manager(tmp_path)
    manager.prepare(name="messy")
    (tmp_path / ".worktrees" / "messy" / "extra.txt").write_text("wip", encoding="utf-8")
    with pytest.raises(WorktreeError) as excinfo:
        manager.cleanup(name="messy")
    assert excinfo.value.code == "WORKTREE_DIRTY"
    assert (tmp_path / ".worktrees" / "messy").exists()


def test_cleanup_refuses_unmerged_then_allows_after_merge(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    manager = _manager(tmp_path)
    manager.prepare(name="feat")
    worktree = tmp_path / ".worktrees" / "feat"
    (worktree / "feature.txt").write_text("x", encoding="utf-8")
    _commit_all(worktree, "feature work")
    manager.finish(name="feat")
    record = manager.check(name="feat")
    assert record.status == "ready"

    with pytest.raises(WorktreeError) as excinfo:
        manager.cleanup(name="feat")
    assert excinfo.value.code == "WORKTREE_NOT_MERGED"
    assert (tmp_path / ".worktrees" / "feat").exists()

    subprocess.run(
        ["git", "merge", "-q", "--no-ff", "-m", "merge feature", "codex/wt-feat"],
        cwd=tmp_path,
        check=True,
    )
    manager.cleanup(name="feat")
    assert manager.list() == ()
    assert not (tmp_path / ".worktrees" / "feat").exists()


def test_cleanup_refuses_when_branch_vanishes_but_path_remains(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    manager = _manager(tmp_path)
    record = manager.prepare(name="ghost")
    subprocess.run(
        ["git", "update-ref", "-d", "refs/heads/" + record.branch],
        cwd=tmp_path,
        check=True,
    )
    with pytest.raises(WorktreeError) as excinfo:
        manager.cleanup(name="ghost")
    assert excinfo.value.code == "WORKTREE_NOT_MERGED"
    assert (tmp_path / ".worktrees" / "ghost").exists()


def test_cleanup_unknown_or_fake_worktree_denies(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    manager = _manager(tmp_path)
    with pytest.raises(WorktreeError) as excinfo:
        manager.cleanup(name="never-registered")
    assert excinfo.value.code == "WORKTREE_UNKNOWN"


def test_cleanup_unregisters_so_names_reuse(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    manager = _manager(tmp_path)
    manager.prepare(name="reuse")
    manager.cleanup(name="reuse")
    again = manager.prepare(name="reuse")
    assert again.name == "reuse"
    manager.cleanup(name="reuse")
