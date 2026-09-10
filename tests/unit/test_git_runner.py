"""Unit tests for the shared GitRunner subprocess wrapper (ADR-0011)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from codex_ai_os.adapters.git import GitRunner


def _init_repository(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "runner@example.test"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "Git Runner"],
        check=True,
        capture_output=True,
    )
    (root / "README.md").write_text("# runner fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", "chore: fixture"],
        check=True,
        capture_output=True,
    )


@pytest.fixture()
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    _init_repository(root)
    return root


def test_run_returns_utf8_text_process(repository: Path) -> None:
    result = GitRunner(repository).run("rev-parse", "HEAD")

    assert result.returncode == 0
    assert isinstance(result.stdout, str)
    assert len(result.stdout.strip()) == 40
    int(result.stdout.strip(), 16)


def test_run_bytes_returns_bytes_process(repository: Path) -> None:
    result = GitRunner(repository).run_bytes("rev-parse", "HEAD")

    assert result.returncode == 0
    assert isinstance(result.stdout, bytes)


def test_failure_returncode_is_not_raised(repository: Path) -> None:
    result = GitRunner(repository).run("not-a-real-git-subcommand")

    assert result.returncode != 0
    assert result.stderr.strip()


def test_builds_git_c_root_prefixed_command(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        captured.append(command)
        return subprocess.CompletedProcess(command, 0, b"abc", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    GitRunner(repository).run_bytes("status", "--porcelain")

    assert captured == [["git", "-C", str(repository), "status", "--porcelain"]]
