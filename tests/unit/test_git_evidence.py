from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from codex_ai_os.adapters.git import GitEvidenceError, GitEvidenceService
from codex_ai_os.domain.workflow import ChangeKind, PushStatus, TaskCompletion


def test_git_evidence_accepts_current_pushed_commit_and_blob_hash(tmp_path: Path) -> None:
    repo, _remote, commit_sha = _repository_with_remote(tmp_path)
    expected = hashlib.sha256(b"evidence\n").hexdigest()
    result = GitEvidenceService(repo).verify(_completion(commit_sha, expected))

    assert result.branch == "main"
    assert result.commit_sha == commit_sha
    assert result.remote_name == "origin"
    assert result.artifact_hashes == {"evidence.txt": expected}
    assert result.verified_at.endswith("+00:00")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("dirty", "worktree must be clean"),
        ("wrong_hash", "artifact hash mismatch"),
        ("wrong_branch", "current branch does not match"),
        ("unpushed", "remote branch does not contain current HEAD"),
    ],
)
def test_git_evidence_rejects_inconsistent_repository_facts(
    tmp_path: Path, mutation: str, message: str
) -> None:
    repo, _remote, commit_sha = _repository_with_remote(tmp_path)
    expected = hashlib.sha256(b"evidence\n").hexdigest()
    completion = _completion(commit_sha, expected)

    if mutation == "dirty":
        (repo / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    elif mutation == "wrong_hash":
        completion = _completion(commit_sha, "0" * 64)
    elif mutation == "wrong_branch":
        completion = completion.model_copy(update={"branch": "codex/not-main"})
    elif mutation == "unpushed":
        (repo / "evidence.txt").write_text("new evidence\n", encoding="utf-8")
        _git(repo, "add", "evidence.txt")
        _git(repo, "commit", "-m", "test: unpushed evidence")
        commit_sha = _git(repo, "rev-parse", "HEAD")
        expected = hashlib.sha256(b"new evidence\n").hexdigest()
        completion = _completion(commit_sha, expected)

    with pytest.raises(GitEvidenceError, match=message):
        GitEvidenceService(repo).verify(completion)


def test_git_evidence_requires_regular_committed_artifact(tmp_path: Path) -> None:
    repo, _remote, commit_sha = _repository_with_remote(tmp_path)
    completion = _completion(
        commit_sha,
        hashlib.sha256(b"evidence\n").hexdigest(),
    ).model_copy(update={"artifact_paths_and_hashes": {"missing.txt": "a" * 64}})

    with pytest.raises(GitEvidenceError, match="missing from worktree"):
        GitEvidenceService(repo).verify(completion)


def test_local_only_evidence_is_limited_to_explicit_fixture_policy(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git(tmp_path, "init", "-b", "main", str(repo))
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.invalid")
    (repo / "evidence.txt").write_text("local evidence\n", encoding="utf-8")
    _git(repo, "add", "evidence.txt")
    _git(repo, "commit", "-m", "test: local fixture evidence")
    commit_sha = _git(repo, "rev-parse", "HEAD")
    completion = TaskCompletion(
        task_id="TASK-FIXTURE",
        change_kind=ChangeKind.REPOSITORY,
        branch="main",
        commit_sha=commit_sha,
        push_status=PushStatus.LOCAL_ONLY,
        artifact_paths_and_hashes={
            "evidence.txt": hashlib.sha256(b"local evidence\n").hexdigest()
        },
    )

    with pytest.raises(GitEvidenceError, match="requires pushed"):
        GitEvidenceService(repo).verify(completion)

    result = GitEvidenceService(repo, require_push=False).verify(completion)
    assert result.commit_sha == commit_sha
    assert result.remote_name is None
    assert result.remote_url is None


def test_git_evidence_rejects_undeclared_paths_in_task_commit(tmp_path: Path) -> None:
    repo, _remote, base_commit = _repository_with_remote(tmp_path)
    (repo / "evidence.txt").write_text("updated evidence\n", encoding="utf-8")
    (repo / "outside.txt").write_text("undeclared\n", encoding="utf-8")
    _git(repo, "add", "evidence.txt", "outside.txt")
    _git(repo, "commit", "-m", "test: include undeclared path")
    _git(repo, "push", "origin", "main")
    commit_sha = _git(repo, "rev-parse", "HEAD")
    completion = _completion(
        commit_sha,
        hashlib.sha256(b"updated evidence\n").hexdigest(),
    )

    with pytest.raises(GitEvidenceError, match="outside assignment"):
        GitEvidenceService(
            repo,
            base_commit=base_commit,
            allowed_paths=("evidence.txt",),
        ).verify(completion)


def _repository_with_remote(tmp_path: Path) -> tuple[Path, Path, str]:
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(tmp_path, "init", "-b", "main", str(repo))
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.invalid")
    (repo / "evidence.txt").write_text("evidence\n", encoding="utf-8")
    _git(repo, "add", "evidence.txt")
    _git(repo, "commit", "-m", "test: add evidence")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-u", "origin", "main")
    return repo, remote, _git(repo, "rev-parse", "HEAD")


def _completion(commit_sha: str, artifact_hash: str) -> TaskCompletion:
    return TaskCompletion(
        task_id="TASK-TEST",
        change_kind=ChangeKind.REPOSITORY,
        branch="main",
        commit_sha=commit_sha,
        remote_name="origin",
        push_status=PushStatus.PUSHED,
        artifact_paths_and_hashes={"evidence.txt": artifact_hash},
        verification_results=("pytest: passed",),
    )


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
