"""Cleanup scope probes never execute the submitted deletion commands."""

from pathlib import Path

import pytest

from codex_ai_os.adapters.git import GitRunner
from codex_ai_os.application import cleanup_policy


def probe(target: Path, checkout: Path):
    return cleanup_policy.check_cleanup(
        f'Remove-Item -LiteralPath "{target}" -Recurse', checkout, checkout
    )


@pytest.fixture
def container(tmp_path, monkeypatch):
    temp = tmp_path / "temporary"
    leaf = temp / "task-container"
    leaf.mkdir(parents=True)
    monkeypatch.setattr(cleanup_policy.tempfile, "gettempdir", lambda: str(temp))
    return leaf


def test_temporary_container_can_hold_synthetic_tracked_repositories(container, governed_repo):
    repo = container / "fixture"
    repo.mkdir()
    git = GitRunner(repo)
    assert git.run("init").returncode == 0
    (repo / "input").mkdir()
    asset = repo / "input/data.txt"
    asset.write_text("synthetic fixture", encoding="utf-8")
    assert git.run("add", "input/data.txt").returncode == 0
    assert probe(container, governed_repo)[0] == "CLEANUP_TARGET_CHECKED"
    assert asset.read_text(encoding="utf-8") == "synthetic fixture"
    assert probe(repo, governed_repo)[0] == "CLEANUP_TARGET_UNSAFE"


def test_temporary_container_allows_internal_links_without_following(container, governed_repo):
    fixture = container / "fixture"
    fixture.mkdir()
    (fixture / "input").mkdir()
    link = container / "current"
    link.symlink_to(fixture, target_is_directory=True)
    assert probe(container, governed_repo)[0] == "CLEANUP_TARGET_CHECKED"
    assert link.is_symlink() and fixture.is_dir()
    assert probe(link, governed_repo)[0] == "CLEANUP_TARGET_UNSAFE"


def test_temporary_container_rejects_external_links(container, governed_repo):
    (container / "external").symlink_to(governed_repo, target_is_directory=True)
    result = probe(container, governed_repo)
    assert result[0] == "CLEANUP_TARGET_UNSAFE" and "outside" in result[1]


def test_temporary_container_rejects_git_links_outside(container, governed_repo):
    fixture = container / "fixture"
    fixture.mkdir()
    (fixture / ".git").write_text(f"gitdir: {governed_repo / '.git'}\n", encoding="utf-8")
    result = probe(container, governed_repo)
    assert result[0] == "CLEANUP_TARGET_UNSAFE" and "outside" in result[1]


def test_temporary_container_rejects_current_checkout_and_ancestors(container):
    current = container / "fixture"
    current.mkdir()
    assert probe(container, current)[0] == "CLEANUP_TARGET_UNSAFE"


def test_temporary_container_keeps_project_build_rules(container, governed_repo):
    leaf = governed_repo / "build/task"
    (leaf / "input").mkdir(parents=True)
    assert probe(leaf, governed_repo)[0] == "CLEANUP_TARGET_UNSAFE"


def test_temporary_container_reports_inspection_failure(container, governed_repo, monkeypatch):
    def unavailable(*args, **kwargs):
        raise PermissionError("fixture traversal denied")

    monkeypatch.setattr(cleanup_policy.os, "scandir", unavailable)
    result = probe(container, governed_repo)
    assert result[0] == "CLEANUP_TARGET_UNSAFE" and "denied" in result[1]


@pytest.mark.parametrize("reference", ["commondir", "objects/info/alternates", "config"])
def test_git_metadata_cannot_associate_external_assets(container, governed_repo, reference):
    metadata = container / "fixture/.git"
    metadata.mkdir(parents=True)
    path = metadata / reference
    path.parent.mkdir(parents=True, exist_ok=True)
    value = governed_repo.as_posix()
    if reference == "config":
        value = f"[core]\nworktree = {value}\n"
    path.write_text(value, encoding="utf-8")
    result = probe(container, governed_repo)
    assert result[0] == "CLEANUP_TARGET_UNSAFE" and "outside" in result[1]


def test_inspection_timeout_never_reports_checked(container, governed_repo, monkeypatch):
    def expired(deadline):
        raise TimeoutError("inspection budget exhausted")

    monkeypatch.setattr(cleanup_policy, "_remaining", expired)
    assert probe(container, governed_repo)[0] == "CLEANUP_INSPECTION_TIMEOUT"
