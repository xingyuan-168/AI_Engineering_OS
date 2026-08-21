from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from codex_ai_os.adapters.worktree import GitWorktreeError, GitWorktreeManager


def test_create_uses_deterministic_branch_path_and_base_commit(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    manager = GitWorktreeManager(repo, minimum_free_bytes=0)

    state = manager.create(
        workflow_id="RUN-001",
        agent="backend-engineer",
        task_id="TASK-001",
    )

    assert state.spec.branch == "agent/backend-engineer/TASK-001"
    assert state.spec.path == repo / ".worktrees" / "RUN-001" / "backend-engineer" / "TASK-001"
    assert state.head_commit == _git(repo, "rev-parse", "HEAD")
    assert state.dirty is False
    assert _git(state.spec.path, "branch", "--show-current") == state.spec.branch


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workflow_id", "../escape"),
        ("agent", "architect/other"),
        ("task_id", ".."),
    ],
)
def test_plan_rejects_unsafe_identity_segments(
    tmp_path: Path, field: str, value: str
) -> None:
    repo = _repository(tmp_path)
    arguments = {
        "workflow_id": "RUN-001",
        "agent": "architect",
        "task_id": "TASK-001",
    }
    arguments[field] = value

    with pytest.raises(GitWorktreeError, match=f"unsafe {field}"):
        GitWorktreeManager(repo, minimum_free_bytes=0).plan(**arguments)


def test_plan_rejects_dirty_main_worktree(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(GitWorktreeError, match="main worktree must be clean"):
        GitWorktreeManager(repo, minimum_free_bytes=0).plan(
            workflow_id="RUN-001",
            agent="architect",
            task_id="TASK-001",
        )


def test_plan_rejects_existing_local_or_remote_branch(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    branch = "agent/architect/TASK-001"
    _git(repo, "branch", branch)

    with pytest.raises(GitWorktreeError, match="task branch already exists"):
        GitWorktreeManager(repo, minimum_free_bytes=0).plan(
            workflow_id="RUN-001",
            agent="architect",
            task_id="TASK-001",
        )


def test_plan_rejects_symlinked_or_junction_managed_ancestor(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = repo / ".worktrees"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError:
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(outside)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=10,
        )
        if result.returncode != 0:
            pytest.skip("directory symlinks and junctions are unavailable")

    with pytest.raises(GitWorktreeError, match="symlink or junction"):
        GitWorktreeManager(repo, minimum_free_bytes=0).plan(
            workflow_id="RUN-001",
            agent="architect",
            task_id="TASK-001",
        )


def test_cleanup_requires_approval_clean_state_and_merge(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    manager = GitWorktreeManager(repo, minimum_free_bytes=0)
    state = manager.create(
        workflow_id="RUN-001",
        agent="backend-engineer",
        task_id="TASK-001",
    )

    with pytest.raises(GitWorktreeError, match="explicit approval"):
        manager.remove(state.spec, approved=False, merged_into="main")

    (state.spec.path / "change.txt").write_text("change\n", encoding="utf-8")
    with pytest.raises(GitWorktreeError, match="dirty worktree"):
        manager.remove(state.spec, approved=True, merged_into="main")

    _git(state.spec.path, "add", "change.txt")
    _git(state.spec.path, "commit", "-m", "test: task change")
    with pytest.raises(GitWorktreeError, match="not merged"):
        manager.remove(state.spec, approved=True, merged_into="main")

    _git(repo, "merge", "--no-ff", state.spec.branch, "-m", "test: merge task")
    manager.remove(state.spec, approved=True, merged_into="main")

    assert not state.spec.path.exists()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{state.spec.branch}")


def _repository(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(tmp_path, "init", "-b", "main", str(repo))
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.invalid")
    (repo / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "README.md")
    _git(repo, "commit", "-m", "test: initialize repository")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-u", "origin", "main")
    return repo


def _git(cwd: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()
