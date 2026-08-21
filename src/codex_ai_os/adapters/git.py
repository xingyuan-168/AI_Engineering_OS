"""Git repository evidence validation without shell interpolation."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Protocol

from codex_ai_os.domain.workflow import ChangeKind, TaskCompletion


class GitEvidenceError(RuntimeError):
    """Raised when task completion evidence does not match repository facts."""


@dataclass(frozen=True, slots=True)
class GitEvidenceResult:
    branch: str
    commit_sha: str
    remote_name: str
    remote_url: str
    artifact_hashes: dict[str, str]
    verified_at: str


class GitEvidenceVerifier(Protocol):
    def verify(self, completion: TaskCompletion) -> GitEvidenceResult:
        """Validate repository completion evidence against immutable Git facts."""

        ...


class GitEvidenceService:
    """Validate a pushed task commit and its committed artifact content."""

    def __init__(self, project_root: Path, *, timeout_seconds: float = 30.0) -> None:
        self.root = project_root.resolve()
        self.timeout_seconds = timeout_seconds

    def verify(self, completion: TaskCompletion) -> GitEvidenceResult:
        if completion.change_kind is not ChangeKind.REPOSITORY:
            raise GitEvidenceError("Git evidence verification requires change_kind=repository")
        if not completion.branch or not completion.commit_sha or not completion.remote_name:
            raise GitEvidenceError("branch, commit SHA, and remote name are required")

        top_level = Path(self._text("rev-parse", "--show-toplevel")).resolve()
        if top_level != self.root:
            raise GitEvidenceError(
                f"project root must be the Git top level: project={self.root}, git={top_level}"
            )

        status = self._text("status", "--porcelain", "--untracked-files=all")
        if status:
            raise GitEvidenceError("worktree must be clean before task completion")

        branch = self._text("branch", "--show-current")
        if not branch:
            raise GitEvidenceError("detached HEAD cannot provide task branch evidence")
        if branch != completion.branch:
            raise GitEvidenceError(
                f"current branch does not match evidence: current={branch}, "
                f"evidence={completion.branch}"
            )

        commit_sha = self._text("rev-parse", "--verify", f"{completion.commit_sha}^{{commit}}")
        head_sha = self._text("rev-parse", "HEAD")
        if head_sha != commit_sha:
            raise GitEvidenceError(
                f"task commit must be current HEAD: head={head_sha}, evidence={commit_sha}"
            )

        remote_url = self._text("remote", "get-url", completion.remote_name)
        remote_ref = f"refs/heads/{branch}"
        remote_line = self._text(
            "ls-remote",
            "--exit-code",
            completion.remote_name,
            remote_ref,
        )
        remote_sha = remote_line.split(maxsplit=1)[0] if remote_line else ""
        if remote_sha != commit_sha:
            raise GitEvidenceError(
                f"remote branch does not contain current HEAD: remote={remote_sha or 'missing'}, "
                f"head={commit_sha}"
            )

        verified_hashes: dict[str, str] = {}
        for raw_path, expected_hash in completion.artifact_paths_and_hashes.items():
            relative = _normalize_artifact_path(raw_path)
            candidate = self.root.joinpath(*relative.parts)
            try:
                resolved = candidate.resolve(strict=True)
            except OSError as exc:
                raise GitEvidenceError(f"artifact is missing from worktree: {relative}") from exc
            if resolved != self.root and self.root not in resolved.parents:
                raise GitEvidenceError(f"artifact resolves outside project root: {relative}")
            if candidate.is_symlink() or not candidate.is_file():
                raise GitEvidenceError(f"artifact must be a regular file: {relative}")

            tree_entry = self._bytes(
                "ls-tree",
                "-z",
                commit_sha,
                "--",
                relative.as_posix(),
            )
            if not tree_entry:
                raise GitEvidenceError(f"artifact is not present in task commit: {relative}")
            mode = tree_entry.split(maxsplit=1)[0]
            if mode not in {b"100644", b"100755"}:
                raise GitEvidenceError(
                    f"artifact must be a committed regular file, mode={mode.decode()}: {relative}"
                )

            committed = self._bytes("show", f"{commit_sha}:{relative.as_posix()}")
            actual_hash = hashlib.sha256(committed).hexdigest()
            if actual_hash.lower() != expected_hash.lower():
                raise GitEvidenceError(
                    f"artifact hash mismatch for {relative}: expected={expected_hash}, "
                    f"committed={actual_hash}"
                )
            verified_hashes[relative.as_posix()] = actual_hash

        return GitEvidenceResult(
            branch=branch,
            commit_sha=commit_sha,
            remote_name=completion.remote_name,
            remote_url=remote_url,
            artifact_hashes=verified_hashes,
            verified_at=datetime.now(UTC).isoformat(),
        )

    def _text(self, *arguments: str) -> str:
        return self._bytes(*arguments).decode("utf-8", errors="strict").strip()

    def _bytes(self, *arguments: str) -> bytes:
        try:
            result = subprocess.run(
                ["git", "-C", str(self.root), *arguments],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitEvidenceError(f"Git command could not run: git {' '.join(arguments)}") from exc
        if result.returncode != 0:
            error = result.stderr.decode("utf-8", errors="replace").strip()
            raise GitEvidenceError(
                f"Git command failed ({result.returncode}): git {' '.join(arguments)}: {error}"
            )
        return result.stdout


def _normalize_artifact_path(raw_path: str) -> PurePosixPath:
    normalized = raw_path.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or normalized.startswith("/"):
        raise GitEvidenceError(f"artifact path must be project-relative: {raw_path}")
    if path == PurePosixPath(".") or ".." in path.parts:
        raise GitEvidenceError(f"artifact path is unsafe: {raw_path}")
    return path
