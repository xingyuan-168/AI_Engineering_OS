from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from codex_ai_os.adapters.git import GitEvidenceResult, GitEvidenceVerifier
from codex_ai_os.adapters.worktree import GitWorktreeError, GitWorktreeManager
from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.application.workflow import WorkflowEngine
from codex_ai_os.application.worktree import WorktreeService, WorktreeServiceError
from codex_ai_os.domain.config import ProjectType
from codex_ai_os.domain.workflow import TaskCompletion


class _AllowingGitEvidence(GitEvidenceVerifier):
    def verify(self, completion: TaskCompletion) -> GitEvidenceResult:
        return GitEvidenceResult(
            branch=completion.branch or "missing",
            commit_sha=completion.commit_sha or "missing",
            remote_name=completion.remote_name or "missing",
            remote_url="test://origin",
            artifact_hashes=dict(completion.artifact_paths_and_hashes),
            verified_at="2026-08-21T00:00:00+00:00",
        )


class _FailingCreateManager(GitWorktreeManager):
    def create(
        self,
        *,
        workflow_id: str,
        agent: str,
        task_id: str,
        base_ref: str = "HEAD",
    ):
        raise GitWorktreeError("simulated worktree creation failure")


def test_allocate_is_idempotent_and_persists_task_and_events(tmp_path: Path) -> None:
    repo, engine = _project_with_active_workflow(tmp_path)
    started = engine.status(_active_run_id(engine))
    assert started.active_task is not None
    service = WorktreeService(repo)

    first = service.allocate(run_id=started.run.id, task_id=started.active_task.id)
    repeated = service.allocate(run_id=started.run.id, task_id=started.active_task.id)

    assert repeated == first
    assert first.spec.path.is_dir()
    task = service.workflow_store.get_task(started.active_task.id)
    assert task.branch == first.spec.branch
    assert task.worktree == first.spec.path.as_posix()
    record = service.store.get_for_task(task.id)
    assert record is not None
    assert record.status == "active"
    with service.store.database.connection() as connection:
        event_types = [
            str(row[0])
            for row in connection.execute(
                "SELECT event_type FROM events WHERE task_id = ? "
                "AND event_type LIKE 'worktree.%' ORDER BY sequence",
                (task.id,),
            ).fetchall()
        ]
    assert event_types == ["worktree.reserved", "worktree.created"]

    service.cleanup(task_id=task.id, approved=True, merged_into="main")
    service.cleanup(task_id=task.id, approved=True, merged_into="main")
    cleaned = service.store.get_for_task(task.id)
    assert cleaned is not None
    assert cleaned.status == "cleaned"
    assert cleaned.cleaned_at is not None
    assert not first.spec.path.exists()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{first.spec.branch}")


def test_failed_git_creation_is_recorded_as_blocked(tmp_path: Path) -> None:
    repo, engine = _project_with_active_workflow(tmp_path)
    started = engine.status(_active_run_id(engine))
    assert started.active_task is not None
    service = WorktreeService(
        repo,
        manager=_FailingCreateManager(repo, minimum_free_bytes=0),
    )

    with pytest.raises(WorktreeServiceError, match="simulated worktree creation failure") as exc:
        service.allocate(run_id=started.run.id, task_id=started.active_task.id)

    assert exc.value.code == "WORKTREE_BLOCKED"
    record = service.store.get_for_task(started.active_task.id)
    assert record is not None
    assert record.status == "blocked"
    with service.store.database.connection() as connection:
        blocked = connection.execute(
            "SELECT COUNT(*) FROM events WHERE task_id = ? "
            "AND event_type = 'worktree.blocked'",
            (started.active_task.id,),
        ).fetchone()[0]
    assert blocked == 1


def test_reservation_reports_ownership_for_concurrent_retry(tmp_path: Path) -> None:
    repo, engine = _project_with_active_workflow(tmp_path)
    started = engine.status(_active_run_id(engine))
    assert started.active_task is not None
    service = WorktreeService(repo)
    spec = service.manager.plan(
        workflow_id=started.run.id,
        agent=started.active_task.agent,
        task_id=started.active_task.id,
    )

    first = service.store.reserve(
        project_id=started.run.project_id,
        run_id=started.run.id,
        task_id=started.active_task.id,
        agent=started.active_task.agent,
        spec=spec,
    )
    repeated = service.store.reserve(
        project_id=started.run.project_id,
        run_id=started.run.id,
        task_id=started.active_task.id,
        agent=started.active_task.agent,
        spec=spec,
    )

    assert first.created is True
    assert repeated.created is False
    assert repeated.record == first.record


def _project_with_active_workflow(tmp_path: Path) -> tuple[Path, WorkflowEngine]:
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(tmp_path, "init", "-b", "main", str(repo))
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.invalid")
    ProjectInitializer().initialize(
        repo,
        project_id="PROJECT-WORKTREE",
        name="Worktree fixture",
        project_type=ProjectType.BACKEND,
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "test: initialize project")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-u", "origin", "main")
    engine = WorkflowEngine(repo, git_evidence=_AllowingGitEvidence())
    engine.start("Build an isolated service")
    return repo, engine


def _active_run_id(engine: WorkflowEngine) -> str:
    with engine.store.database.connection() as connection:
        return str(
            connection.execute(
                "SELECT id FROM workflow_runs WHERE run_status = 'running'"
            ).fetchone()[0]
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
