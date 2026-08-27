"""Application coordination for accepted Handoffs and serialized integration merges."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from codex_ai_os.domain.config import GitPushPolicy
from codex_ai_os.domain.coordination import HandoffReviewInput, TaskGroupView
from codex_ai_os.domain.ids import new_id
from codex_ai_os.domain.operations import HostOperation, HostOperationKind
from codex_ai_os.infrastructure.config import load_project_config
from codex_ai_os.infrastructure.coordination import (
    CoordinationError,
    CoordinationStore,
    HandoffDecisionResult,
)
from codex_ai_os.infrastructure.database import Database

GitRunner = Callable[[list[str], Path, float], subprocess.CompletedProcess[bytes]]


@dataclass(frozen=True, slots=True)
class IntegrationResult:
    handoff_id: str
    status: str
    integration_branch: str
    integration_head: str
    merge_commit: str | None
    conflict_paths: tuple[str, ...]
    task_group: TaskGroupView


class CoordinationService:
    def __init__(self, project_root: Path, *, runner: GitRunner | None = None) -> None:
        self.root = project_root.resolve()
        self.config = load_project_config(self.root)
        self.database = Database(self.root / ".codex-os" / "state" / "state.db")
        self.database.migrate()
        self.store = CoordinationStore(self.database)
        self.runner = runner or _run_git

    def ensure_integration_worktree(self, run_id: str, *, base_commit: str) -> Path:
        with self.database.connection() as connection:
            run = connection.execute(
                "SELECT project_id, integration_branch FROM workflow_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise CoordinationError("RECOVERY_UNAVAILABLE", f"run not found: {run_id}")
            existing = connection.execute(
                "SELECT * FROM workflow_worktrees WHERE run_id = ? AND kind = 'integration'",
                (run_id,),
            ).fetchone()
        if existing is not None:
            path = Path(str(existing["path"])).resolve()
            state = self._git(path, "rev-parse", "HEAD")
            if state.returncode != 0 or not path.is_relative_to(self.root / ".worktrees"):
                raise CoordinationError("WORKTREE_BLOCKED", "integration Worktree is invalid")
            return path

        branch = f"workflow/{run_id}/integration"
        path = (self.root / ".worktrees" / run_id / "coordinator" / "integration").resolve()
        if not path.is_relative_to((self.root / ".worktrees").resolve()):
            raise CoordinationError("PATH_ESCAPE", "integration path escapes managed root")
        path.parent.mkdir(parents=True, exist_ok=True)
        branch_exists = self._git(
            self.root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"
        ).returncode == 0
        args = ("worktree", "add", str(path), branch) if branch_exists else (
            "worktree",
            "add",
            "-b",
            branch,
            str(path),
            base_commit,
        )
        created = self._git(self.root, *args)
        if created.returncode != 0:
            raise CoordinationError(
                "WORKTREE_BLOCKED", f"cannot create integration Worktree: {_stderr(created)}"
            )
        head = self._require_git(path, "rev-parse", "HEAD")
        now = _utc_now()
        with self.database.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO workflow_worktrees(
                        id, project_id, run_id, kind, path, branch, base_commit,
                        head_commit, status, created_at
                    ) VALUES (?, ?, ?, 'integration', ?, ?, ?, ?, 'active', ?)
                    """,
                    (
                        new_id("WORKFLOWWORKTREE"),
                        str(run["project_id"]),
                        run_id,
                        path.as_posix(),
                        branch,
                        base_commit,
                        head,
                        now,
                    ),
                )
                connection.execute(
                    """
                    UPDATE workflow_runs SET integration_branch = ?, base_commit = ?,
                        integration_head = ?, state_version = state_version + 1,
                        updated_at = ? WHERE id = ?
                    """,
                    (branch, base_commit, head, now, run_id),
                )
                connection.commit()
            except sqlite3.Error:
                connection.rollback()
                self._git(self.root, "worktree", "remove", str(path))
                raise
        return path

    def review_and_integrate(self, review: HandoffReviewInput) -> IntegrationResult:
        decision = self.prepare_review(review)
        if decision.decision != "accepted":
            return self.decision_result(decision)
        return self._merge(decision)

    def prepare_review(self, review: HandoffReviewInput) -> HandoffDecisionResult:
        self._verify_review_report(review)
        return self.store.review_handoff(review)

    def decision_result(self, decision: HandoffDecisionResult) -> IntegrationResult:
        group = self.store.group_view(decision.task_group_id)
        with self.database.read_connection() as connection:
            run = connection.execute(
                "SELECT integration_branch, integration_head FROM workflow_runs "
                "WHERE id = (SELECT run_id FROM tasks WHERE id = ?)",
                (decision.task_id,),
            ).fetchone()
        if run is None:
            raise CoordinationError("RECOVERY_UNAVAILABLE", "Handoff run not found")
        return IntegrationResult(
            handoff_id=decision.handoff_id,
            status=decision.decision,
            integration_branch=str(run["integration_branch"] or ""),
            integration_head=str(run["integration_head"] or ""),
            merge_commit=None,
            conflict_paths=(),
            task_group=group,
        )

    def execute_merge_operation(self, operation: HostOperation) -> IntegrationResult:
        if operation.kind is not HostOperationKind.INTEGRATION_MERGE:
            raise CoordinationError(
                "CONFIG_INVALID", "host operation is not an integration merge"
            )
        request = operation.request
        required = ("handoff_id", "task_id", "task_group_id", "source_commit")
        if not all(isinstance(request.get(key), str) for key in required):
            raise CoordinationError(
                "RECOVERY_UNAVAILABLE", "integration merge request is incomplete"
            )
        decision = HandoffDecisionResult(
            handoff_id=str(request["handoff_id"]),
            task_id=str(request["task_id"]),
            task_group_id=str(request["task_group_id"]),
            decision="accepted",
            source_commit=str(request["source_commit"]),
            operation_id=operation.operation_id,
        )
        return self._merge(decision)

    def _merge(self, decision: HandoffDecisionResult) -> IntegrationResult:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT t.branch, t.run_id, w.path, w.branch AS integration_branch,
                       w.head_commit
                FROM tasks t
                JOIN workflow_worktrees w ON w.run_id = t.run_id AND w.kind = 'integration'
                WHERE t.id = ? AND w.status = 'active'
                """,
                (decision.task_id,),
            ).fetchone()
        if row is None or row["branch"] is None:
            raise CoordinationError("WORKTREE_BLOCKED", "integration or source branch is missing")
        run_id = str(row["run_id"])
        source_branch = str(row["branch"])
        integration_branch = str(row["integration_branch"])
        worktree = Path(str(row["path"])).resolve()
        token = self._acquire_lock(run_id)
        try:
            dirty = self._require_git(worktree, "status", "--porcelain")
            if dirty:
                raise CoordinationError("WORKTREE_DIRTY", "integration Worktree is not clean")
            before = self._require_git(worktree, "rev-parse", "HEAD")
            if before != str(row["head_commit"]):
                raise CoordinationError(
                    "STATE_VERSION_CONFLICT", "integration HEAD differs from persisted state"
                )
            source_valid = self._git(
                self.root,
                "merge-base",
                "--is-ancestor",
                decision.source_commit,
                source_branch,
            )
            if source_valid.returncode != 0:
                raise CoordinationError("REVIEW_STALE", "reviewed Commit is not on source branch")
            already = self._git(
                worktree, "merge-base", "--is-ancestor", decision.source_commit, "HEAD"
            )
            if already.returncode == 0:
                merge_commit = before
                parent_text = self._require_git(
                    worktree, "show", "-s", "--format=%P", "HEAD"
                )
                parents = tuple(parent_text.split())
            else:
                merged = self._git(
                    worktree, "merge", "--no-ff", "--no-edit", decision.source_commit
                )
                if merged.returncode != 0:
                    conflicts = tuple(
                        path
                        for path in self._require_git(
                            worktree, "diff", "--name-only", "--diff-filter=U"
                        ).splitlines()
                        if path
                    )
                    self._git(worktree, "merge", "--abort")
                    group = self.store.record_merge(
                        decision=decision,
                        source_branch=source_branch,
                        integration_branch=integration_branch,
                        integration_head_before=before,
                        merge_commit=None,
                        parent_commits=(),
                        status="conflicted",
                        conflict_paths=conflicts,
                        error_code="MERGE_CONFLICT",
                    )
                    return IntegrationResult(
                        handoff_id=decision.handoff_id,
                        status="conflicted",
                        integration_branch=integration_branch,
                        integration_head=before,
                        merge_commit=None,
                        conflict_paths=conflicts,
                        task_group=group,
                    )
                merge_commit = self._require_git(worktree, "rev-parse", "HEAD")
                parents = tuple(
                    self._require_git(worktree, "show", "-s", "--format=%P", "HEAD").split()
                )
                valid_parents = (
                    len(parents) >= 2
                    and before in parents
                    and decision.source_commit in parents
                )
                if not valid_parents:
                    raise CoordinationError(
                        "GIT_EVIDENCE_INVALID", "integration merge is not a --no-ff merge"
                    )
            if self.config.git_push_policy is GitPushPolicy.REMOTE_REQUIRED:
                pushed = self._git(worktree, "push", "-u", "origin", integration_branch)
                if pushed.returncode != 0:
                    group = self.store.record_merge(
                        decision=decision,
                        source_branch=source_branch,
                        integration_branch=integration_branch,
                        integration_head_before=before,
                        merge_commit=merge_commit,
                        parent_commits=parents,
                        status="blocked",
                        error_code="REMOTE_UNREACHABLE",
                    )
                    raise CoordinationError(
                        "REMOTE_UNREACHABLE",
                        f"integration push failed: {_stderr(pushed)}; group={group.id}",
                    )
            group = self.store.record_merge(
                decision=decision,
                source_branch=source_branch,
                integration_branch=integration_branch,
                integration_head_before=before,
                merge_commit=merge_commit,
                parent_commits=parents,
                status="merged",
            )
            with self.database.connection() as connection:
                connection.execute(
                    "UPDATE workflow_worktrees SET head_commit = ?, "
                    "state_version = state_version + 1 WHERE run_id = ? AND kind = 'integration'",
                    (merge_commit, run_id),
                )
                connection.execute(
                    "UPDATE workflow_runs SET integration_head = ?, updated_at = ? WHERE id = ?",
                    (merge_commit, _utc_now(), run_id),
                )
                connection.commit()
            return IntegrationResult(
                handoff_id=decision.handoff_id,
                status="merged",
                integration_branch=integration_branch,
                integration_head=merge_commit,
                merge_commit=merge_commit,
                conflict_paths=(),
                task_group=group,
            )
        finally:
            self._release_lock(run_id, token)

    def _acquire_lock(self, run_id: str) -> str:
        token = secrets.token_hex(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now = datetime.now(UTC)
        expires = now + timedelta(minutes=5)
        with self.database.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT expires_at FROM coordination_locks WHERE lock_key = ?",
                    (f"integration:{run_id}",),
                ).fetchone()
                if existing is not None:
                    existing_expiry = datetime.fromisoformat(str(existing["expires_at"]))
                    if existing_expiry > now:
                        raise CoordinationError(
                            "STATE_VERSION_CONFLICT", "integration merge lock is held"
                        )
                    connection.execute(
                        "DELETE FROM coordination_locks WHERE lock_key = ?",
                        (f"integration:{run_id}",),
                    )
                connection.execute(
                    """
                    INSERT INTO coordination_locks(
                        lock_key, run_id, owner, lease_token_hash,
                        acquired_at, expires_at, state_version
                    ) VALUES (?, ?, 'integration-service', ?, ?, ?, 0)
                    """,
                    (
                        f"integration:{run_id}",
                        run_id,
                        token_hash,
                        now.isoformat(),
                        expires.isoformat(),
                    ),
                )
                connection.commit()
            except (sqlite3.Error, CoordinationError):
                connection.rollback()
                raise
        return token

    def _release_lock(self, run_id: str, token: str) -> None:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with self.database.connection() as connection:
            connection.execute(
                "DELETE FROM coordination_locks WHERE lock_key = ? AND lease_token_hash = ?",
                (f"integration:{run_id}", token_hash),
            )
            connection.commit()

    def _verify_review_report(self, review: HandoffReviewInput) -> None:
        path = (self.root / review.report_ref).resolve()
        allowed_roots = (self.root, (self.root / ".worktrees").resolve())
        if not any(path.is_relative_to(root) for root in allowed_roots) or not path.is_file():
            raise CoordinationError("REVIEW_STALE", "review report is missing or unsafe")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != review.report_hash.casefold():
            raise CoordinationError("REVIEW_STALE", "review report hash mismatch")

    def _git(self, cwd: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
        try:
            return self.runner(["git", "-C", str(cwd), *arguments], cwd, 60.0)
        except (OSError, subprocess.SubprocessError) as exc:
            return subprocess.CompletedProcess([], 1, b"", str(exc).encode())

    def _require_git(self, cwd: Path, *arguments: str) -> str:
        result = self._git(cwd, *arguments)
        if result.returncode != 0:
            raise CoordinationError("GIT_EVIDENCE_INVALID", _stderr(result))
        return result.stdout.decode("utf-8", errors="replace").strip()


def _run_git(
    arguments: list[str], cwd: Path, timeout: float
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        arguments,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def _stderr(process: subprocess.CompletedProcess[bytes]) -> str:
    return process.stderr.decode("utf-8", errors="replace").strip() or (
        f"exit code {process.returncode}"
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
