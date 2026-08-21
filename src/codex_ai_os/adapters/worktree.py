"""Safe Git worktree lifecycle management for isolated agent tasks."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class GitWorktreeError(RuntimeError):
    """Raised when a worktree operation violates isolation or Git policy."""


@dataclass(frozen=True, slots=True)
class WorktreeSpec:
    workflow_id: str
    agent: str
    task_id: str
    branch: str
    path: Path
    base_commit: str


@dataclass(frozen=True, slots=True)
class WorktreeState:
    spec: WorktreeSpec
    head_commit: str
    dirty: bool


class WorktreeManager(Protocol):
    def plan(
        self,
        *,
        workflow_id: str,
        agent: str,
        task_id: str,
        base_ref: str = "HEAD",
    ) -> WorktreeSpec: ...

    def create(
        self,
        *,
        workflow_id: str,
        agent: str,
        task_id: str,
        base_ref: str = "HEAD",
    ) -> WorktreeState: ...

    def inspect(self, spec: WorktreeSpec) -> WorktreeState: ...

    def remove(self, spec: WorktreeSpec, *, approved: bool, merged_into: str) -> None: ...


class GitWorktreeManager:
    """Create and inspect task worktrees below the repository-owned root."""

    def __init__(
        self,
        project_root: Path,
        *,
        timeout_seconds: float = 30.0,
        minimum_free_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        self.root = project_root.resolve()
        self.worktrees_root = self.root / ".worktrees"
        self.timeout_seconds = timeout_seconds
        self.minimum_free_bytes = minimum_free_bytes

    def plan(
        self,
        *,
        workflow_id: str,
        agent: str,
        task_id: str,
        base_ref: str = "HEAD",
    ) -> WorktreeSpec:
        """Validate all preconditions and return the deterministic worktree spec."""

        top_level = Path(self._text(self.root, "rev-parse", "--show-toplevel")).resolve()
        if top_level != self.root:
            raise GitWorktreeError(
                f"project root must be the Git top level: project={self.root}, git={top_level}"
            )
        for label, value in (
            ("workflow_id", workflow_id),
            ("agent", agent),
            ("task_id", task_id),
        ):
            _validate_segment(label, value)

        if self._text(self.root, "status", "--porcelain", "--untracked-files=all"):
            raise GitWorktreeError("main worktree must be clean before worktree creation")
        if self._text(self.root, "diff", "--name-only", "--diff-filter=U"):
            raise GitWorktreeError("main worktree has unresolved conflicts")

        branch = f"agent/{agent}/{task_id}"
        self._check_ref_format(branch)
        base_commit = self._text(self.root, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
        if self._ref_exists(f"refs/heads/{branch}"):
            raise GitWorktreeError(f"task branch already exists: {branch}")
        self._assert_remote_branch_absent(branch)

        raw_target = self.worktrees_root / workflow_id / agent / task_id
        self._assert_no_linked_ancestor(raw_target)
        target = raw_target.resolve(strict=False)
        worktrees_root = self.worktrees_root.resolve(strict=False)
        if not target.is_relative_to(worktrees_root) or target == worktrees_root:
            raise GitWorktreeError(f"worktree path escapes managed root: {target}")
        if target.exists() or target.is_symlink():
            raise GitWorktreeError(f"worktree target already exists: {target}")
        self._assert_not_overlapping_registered_worktree(target)

        free_bytes = shutil.disk_usage(self.root).free
        if free_bytes < self.minimum_free_bytes:
            raise GitWorktreeError(
                f"insufficient disk space for worktree: free={free_bytes}, "
                f"required={self.minimum_free_bytes}"
            )
        return WorktreeSpec(
            workflow_id=workflow_id,
            agent=agent,
            task_id=task_id,
            branch=branch,
            path=target,
            base_commit=base_commit,
        )

    def create(
        self,
        *,
        workflow_id: str,
        agent: str,
        task_id: str,
        base_ref: str = "HEAD",
    ) -> WorktreeState:
        """Create a new branch and worktree after completing the full preflight."""

        spec = self.plan(
            workflow_id=workflow_id,
            agent=agent,
            task_id=task_id,
            base_ref=base_ref,
        )
        spec.path.parent.mkdir(parents=True, exist_ok=True)
        raw_target = self.worktrees_root / workflow_id / agent / task_id
        self._assert_no_linked_ancestor(raw_target)
        if raw_target.resolve(strict=False) != spec.path:
            raise GitWorktreeError("worktree path changed after preflight")
        self._text(
            self.root,
            "worktree",
            "add",
            "-b",
            spec.branch,
            str(spec.path),
            spec.base_commit,
        )
        state = self.inspect(spec)
        if state.head_commit != spec.base_commit:
            raise GitWorktreeError(
                f"created worktree HEAD differs from base commit: "
                f"head={state.head_commit}, base={spec.base_commit}"
            )
        return state

    def inspect(self, spec: WorktreeSpec) -> WorktreeState:
        """Revalidate an assigned worktree against path, branch, and Git metadata."""

        raw_expected = self.worktrees_root / spec.workflow_id / spec.agent / spec.task_id
        self._assert_no_linked_ancestor(raw_expected)
        expected = raw_expected.resolve(strict=False)
        if spec.path.resolve(strict=False) != expected:
            raise GitWorktreeError(f"worktree path does not match task identity: {spec.path}")
        registered = self._registered_worktrees()
        if spec.path.resolve(strict=False) not in registered:
            raise GitWorktreeError(f"worktree is not registered with Git: {spec.path}")
        if not spec.path.is_dir():
            raise GitWorktreeError(f"registered worktree directory is missing: {spec.path}")

        branch = self._text(spec.path, "branch", "--show-current")
        if branch != spec.branch:
            raise GitWorktreeError(
                f"worktree branch does not match assignment: current={branch}, "
                f"expected={spec.branch}"
            )
        head_commit = self._text(spec.path, "rev-parse", "HEAD")
        dirty = bool(self._text(spec.path, "status", "--porcelain", "--untracked-files=all"))
        return WorktreeState(spec=spec, head_commit=head_commit, dirty=dirty)

    def remove(self, spec: WorktreeSpec, *, approved: bool, merged_into: str) -> None:
        """Remove only a clean, merged worktree while preserving its task branch."""

        if not approved:
            raise GitWorktreeError("worktree cleanup requires explicit approval")
        state = self.inspect(spec)
        if state.dirty:
            raise GitWorktreeError("dirty worktree cannot be cleaned")
        target_commit = self._text(
            self.root, "rev-parse", "--verify", f"{merged_into}^{{commit}}"
        )
        ancestor = self._run(
            self.root,
            "merge-base",
            "--is-ancestor",
            state.head_commit,
            target_commit,
            allowed_returncodes=(0, 1),
        )
        if ancestor.returncode != 0:
            raise GitWorktreeError(
                f"task branch is not merged into cleanup target: {merged_into}"
            )
        self._text(self.root, "worktree", "remove", str(spec.path))

    def _assert_no_linked_ancestor(self, target: Path) -> None:
        current = target.parent
        while current.is_relative_to(self.root):
            if current.exists() and _is_link_like(current):
                raise GitWorktreeError(f"worktree path uses a symlink or junction: {current}")
            if current == self.root:
                break
            current = current.parent

    def _assert_not_overlapping_registered_worktree(self, target: Path) -> None:
        for registered in self._registered_worktrees():
            if registered == self.root:
                continue
            overlaps = (
                target == registered
                or target.is_relative_to(registered)
                or registered.is_relative_to(target)
            )
            if overlaps:
                raise GitWorktreeError(
                    f"worktree target overlaps registered worktree: {registered}"
                )

    def _registered_worktrees(self) -> tuple[Path, ...]:
        output = self._text(self.root, "worktree", "list", "--porcelain", "-z")
        entries: list[Path] = []
        for field in output.split("\x00"):
            if field.startswith("worktree "):
                entries.append(Path(field.removeprefix("worktree ")).resolve(strict=False))
        return tuple(entries)

    def _assert_remote_branch_absent(self, branch: str) -> None:
        remotes = self._text(self.root, "remote").splitlines()
        for remote in remotes:
            result = self._run(
                self.root,
                "ls-remote",
                "--exit-code",
                "--heads",
                remote,
                f"refs/heads/{branch}",
                allowed_returncodes=(0, 2),
            )
            if result.returncode == 0 and result.stdout.strip():
                raise GitWorktreeError(f"task branch already exists on remote {remote}: {branch}")

    def _check_ref_format(self, branch: str) -> None:
        result = self._run(
            self.root,
            "check-ref-format",
            "--branch",
            branch,
            allowed_returncodes=(0, 128),
        )
        if result.returncode != 0:
            raise GitWorktreeError(f"invalid task branch name: {branch}")

    def _ref_exists(self, ref: str) -> bool:
        result = self._run(
            self.root,
            "show-ref",
            "--verify",
            "--quiet",
            ref,
            allowed_returncodes=(0, 1),
        )
        return result.returncode == 0

    def _text(self, cwd: Path, *arguments: str) -> str:
        return self._run(cwd, *arguments).stdout.decode("utf-8", errors="strict").strip()

    def _run(
        self,
        cwd: Path,
        *arguments: str,
        allowed_returncodes: tuple[int, ...] = (0,),
    ) -> subprocess.CompletedProcess[bytes]:
        try:
            result = subprocess.run(
                ["git", "-C", str(cwd), *arguments],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitWorktreeError(
                f"Git command could not run: git {' '.join(arguments)}"
            ) from exc
        if result.returncode not in allowed_returncodes:
            error = result.stderr.decode("utf-8", errors="replace").strip()
            raise GitWorktreeError(
                f"Git command failed ({result.returncode}): "
                f"git {' '.join(arguments)}: {error}"
            )
        return result


def _validate_segment(label: str, value: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value) or value in {
        ".",
        "..",
    }:
        raise GitWorktreeError(f"unsafe {label}: {value}")


def _is_link_like(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction is not None and is_junction())
