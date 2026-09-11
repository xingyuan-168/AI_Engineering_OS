from __future__ import annotations

import subprocess
from pathlib import Path

from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.application.repository import RepositoryGovernanceService


def _initialize(root: Path) -> None:
    ProjectInitializer().initialize(
        root,
        project_id="PROJECT-REPO",
        name="Repo",
        project_type="generic",
        include=frozenset(),
    )


def _git_repo(root: Path) -> None:
    (root / ".git").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)


def test_non_git_repository_is_blocked(tmp_path: Path) -> None:
    _initialize(tmp_path)
    report = RepositoryGovernanceService(tmp_path).check()
    assert report.repository_ready is False
    assert any(f.code == "NOT_GIT_REPOSITORY" for f in report.findings)


def test_git_repository_without_remote_blocks(tmp_path: Path) -> None:
    _initialize(tmp_path)
    _git_repo(tmp_path)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    report = RepositoryGovernanceService(tmp_path).check()
    assert report.repository_ready is False
    assert any(f.code == "GITHUB_REMOTE_REQUIRED" for f in report.findings)


def test_output_junk_and_archive_trees_are_flagged(tmp_path: Path) -> None:
    _initialize(tmp_path)
    _git_repo(tmp_path)
    (tmp_path / "output" / "scratch.log").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "scratch.log").write_text("junk", encoding="utf-8")
    (tmp_path / "docs" / "archive").mkdir(parents=True)
    (tmp_path / "docs" / "archive" / "old.md").write_text("old", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    report = RepositoryGovernanceService(tmp_path).check()
    codes = {f.code for f in report.findings}
    assert report.repository_ready is False
    assert "OUTPUT_IMPURE" in codes
    assert "LEGACY_DOC_TREE" in codes
    assert report.hygiene_ok is False


def test_input_tree_is_never_scanned(tmp_path: Path) -> None:
    _initialize(tmp_path)
    _git_repo(tmp_path)
    (tmp_path / "input" / "scratch.log").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "input" / "scratch.log").write_text("user material", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    report = RepositoryGovernanceService(tmp_path).check()
    assert not any("OUTPUT" in f.code for f in report.findings)


def test_missing_gitignore_blocks(tmp_path: Path) -> None:
    _initialize(tmp_path)
    _git_repo(tmp_path)
    (tmp_path / ".gitignore").unlink()
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    report = RepositoryGovernanceService(tmp_path).check()
    finding = next((f for f in report.findings if f.code == "GITIGNORE_INCOMPLETE"), None)
    assert finding is not None and finding.blocking


def test_incomplete_gitignore_lists_missing_items(tmp_path: Path) -> None:
    _initialize(tmp_path)
    _git_repo(tmp_path)
    (tmp_path / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    report = RepositoryGovernanceService(tmp_path).check()
    finding = next((f for f in report.findings if f.code == "GITIGNORE_INCOMPLETE"), None)
    assert finding is not None and finding.blocking
    assert "__pycache__" in finding.message
    assert ".codex-os/state" in finding.message
    assert ".worktrees" in finding.message


def test_gitignore_equivalent_spellings_pass(tmp_path: Path) -> None:
    _initialize(tmp_path)
    _git_repo(tmp_path)
    # Equivalent spellings (character-class glob, anchored forms, parent
    # directory) must satisfy the semantic checklist without a full parser.
    (tmp_path / ".gitignore").write_text(
        "**/__pycache__/\n*.py[cod]\n/.pytest_cache/\n**/.ruff_cache/\n"
        ".venv/**\n/node_modules/\nbuild/\n/dist\n*.log\n"
        ".codex-os/**\n.worktrees/**\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    report = RepositoryGovernanceService(tmp_path).check()
    assert not any(f.code == "GITIGNORE_INCOMPLETE" for f in report.findings)
