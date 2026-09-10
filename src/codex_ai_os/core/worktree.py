"""Disposable Git worktree lifecycle for Codex subagents (ADR-0016).

Four capabilities only - prepare, check, finish, cleanup - plus a list.
Worktrees live under ".worktrees/" and are registered in the runtime
database so the PreToolUse hook can treat them as disposable areas;
cleanup unregisters them. There is no coordination, lease, or DAG
machinery: the main Codex session owns merge decisions; AIOS only keeps
the working copies isolated and registered.
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from codex_ai_os.adapters.git import GitRunner
from codex_ai_os.domain.ids import new_id
from codex_ai_os.infrastructure.database import Database

WORKTREE_ROOT = ".worktrees"
BRANCH_PREFIX = "codex/wt-"
_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,47}$")


class WorktreeError(RuntimeError):
    """Raised when a worktree lifecycle step cannot be performed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class WorktreeRecord:
    id: str
    name: str
    path: str
    branch: str
    task_id: str | None
    disposable: bool
    status: str
    clean: bool | None = None


class WorktreeManager:
    def __init__(
        self,
        project_root: Path,
        *,
        database: Database | None = None,
        runner: GitRunner | None = None,
    ) -> None:
        self.root = project_root.resolve()
        self.database = database or Database(self.root / ".codex-os" / "state" / "state.db")
        self.git = runner or GitRunner(self.root)

    def prepare(
        self,
        *,
        name: str | None = None,
        task_id: str | None = None,
        base_ref: str = "HEAD",
    ) -> WorktreeRecord:
        """Create a disposable worktree and register it for hook path checks."""

        slug = _slug(name) or _generated_name()
        path = self.root / WORKTREE_ROOT / slug
        if path.exists():
            raise WorktreeError(
                "WORKTREE_EXISTS", f"worktree path already exists: {WORKTREE_ROOT}/{slug}"
            )
        branch = BRANCH_PREFIX + slug
        result = self.git.run("worktree", "add", "-b", branch, str(path), base_ref)
        if result.returncode != 0:
            raise WorktreeError("WORKTREE_ADD_FAILED", _git_failure("git worktree add", result))
        record_id = new_id("WORKTREE")
        now = _utc_now()
        resolved_task_id = task_id or new_id("TASK")
        with self.database.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT OR IGNORE INTO tasks("
                    "id, title, branch, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, 'in_progress', ?, ?)",
                    (resolved_task_id, "worktree " + slug, branch, now, now),
                )
                connection.execute(
                    """
                    INSERT INTO worktrees(
                        id, task_id, name, path, branch, disposable, status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 1, 'active', ?, ?)
                    """,
                    (
                        record_id,
                        resolved_task_id,
                        slug,
                        f"{WORKTREE_ROOT}/{slug}",
                        branch,
                        now,
                        now,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return WorktreeRecord(
            id=record_id,
            name=slug,
            path=f"{WORKTREE_ROOT}/{slug}",
            branch=branch,
            task_id=resolved_task_id,
            disposable=True,
            status="active",
            clean=True,
        )

    def check(self, *, name: str) -> WorktreeRecord:
        """Report registration, existence, and cleanliness for one worktree."""

        record = self._record(name)
        absolute = self.root / record.path
        if not absolute.is_dir():
            return WorktreeRecord(**{**_fields(record), "clean": None})
        status = GitRunner(absolute).run("status", "--porcelain")
        clean = status.returncode == 0 and not status.stdout.strip()
        return WorktreeRecord(**{**_fields(record), "clean": clean})

    def finish(self, *, name: str) -> WorktreeRecord:
        """Mark a clean worktree as finished and ready for merge review."""

        record = self._record(name)
        absolute = self.root / record.path
        if not absolute.is_dir():
            raise WorktreeError(
                "WORKTREE_MISSING", "worktree directory is missing: " + record.path
            )
        status = GitRunner(absolute).run("status", "--porcelain")
        if status.returncode != 0 or status.stdout.strip():
            raise WorktreeError(
                "WORKTREE_DIRTY",
                "commit or clean the worktree before finish; subagent work must not be lost",
            )
        return self._set_status(record.name, "merged")

    def cleanup(self, *, name: str, force: bool = False) -> WorktreeRecord:
        """Remove a disposable worktree and its branch, unregistering it.

        Registration exists so the hook can treat the path as disposable;
        once the worktree is removed the row goes with it, which frees the
        name, path, and branch for reuse.
        """

        record = self._record(name)
        absolute = self.root / record.path
        if absolute.is_dir():
            arguments = ["worktree", "remove"]
            if force:
                arguments.append("--force")
            arguments.append(str(absolute))
            result = self.git.run(*arguments)
            if result.returncode != 0:
                if not force:
                    dirty = GitRunner(absolute).run("status", "--porcelain")
                    if dirty.returncode == 0 and dirty.stdout.strip():
                        raise WorktreeError(
                            "WORKTREE_DIRTY",
                            "worktree has uncommitted changes; pass force to discard them",
                        )
                raise WorktreeError(
                    "WORKTREE_REMOVE_FAILED", _git_failure("git worktree remove", result)
                )
        branch_delete = self.git.run("branch", "-D", record.branch)
        if branch_delete.returncode != 0 and not _branch_gone(branch_delete):
            raise WorktreeError(
                "BRANCH_DELETE_FAILED", _git_failure("git branch -D", branch_delete)
            )
        with self.database.connection() as connection:
            connection.execute("DELETE FROM worktrees WHERE name = ?", (record.name,))
            connection.commit()
        return WorktreeRecord(**{**_fields(record), "status": "cleaned"})

    def list(self) -> tuple[WorktreeRecord, ...]:
        with self.database.connection() as connection:
            rows = connection.execute("SELECT * FROM worktrees ORDER BY created_at").fetchall()
        return tuple(_record_from_row(row) for row in rows)

    def _record(self, name: str) -> WorktreeRecord:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM worktrees WHERE name = ?", (name,)
            ).fetchone()
        if row is None:
            raise WorktreeError("WORKTREE_UNKNOWN", "worktree is not registered: " + name)
        return _record_from_row(row)

    def _set_status(self, name: str, status: str) -> WorktreeRecord:
        with self.database.connection() as connection:
            connection.execute(
                "UPDATE worktrees SET status = ?, updated_at = ? WHERE name = ?",
                (status, _utc_now(), name),
            )
            connection.commit()
        return self._record(name)


def _record_from_row(row: sqlite3.Row) -> WorktreeRecord:
    return WorktreeRecord(
        id=str(row["id"]),
        name=str(row["name"]),
        path=str(row["path"]),
        branch=str(row["branch"]),
        task_id=str(row["task_id"]) if row["task_id"] is not None else None,
        disposable=bool(row["disposable"]),
        status=str(row["status"]),
    )


def _fields(record: WorktreeRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "name": record.name,
        "path": record.path,
        "branch": record.branch,
        "task_id": record.task_id,
        "disposable": record.disposable,
        "status": record.status,
    }


def _slug(name: str | None) -> str | None:
    if name is None:
        return None
    candidate = name.strip().casefold().replace(" ", "-")
    if _NAME_PATTERN.match(candidate) is None:
        raise WorktreeError(
            "WORKTREE_NAME_INVALID",
            "worktree name must match [a-z0-9][a-z0-9._-]{0,47}: " + repr(name),
        )
    return candidate


def _generated_name() -> str:
    return datetime.now(UTC).strftime("wt-%Y%m%d-%H%M%S")


def _git_failure(label: str, result: subprocess.CompletedProcess[str]) -> str:
    detail = (result.stderr or result.stdout or "").strip().splitlines()
    return label + " failed: " + (detail[-1] if detail else "exit " + str(result.returncode))


def _branch_gone(result: subprocess.CompletedProcess[str]) -> bool:
    combined = (result.stdout + result.stderr).casefold()
    return any(
        marker in combined
        for marker in ("not found", "does not exist", "no such branch", "unknown revision")
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "BRANCH_PREFIX",
    "WORKTREE_ROOT",
    "WorktreeError",
    "WorktreeManager",
    "WorktreeRecord",
]
