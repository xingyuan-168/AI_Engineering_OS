from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from codex_ai_os.application.coordination import CoordinationService
from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.application.workflow import WorkflowEngine
from codex_ai_os.domain.config import GitPushPolicy, ProjectType
from codex_ai_os.domain.coordination import HandoffReviewInput, TaskBlueprint
from codex_ai_os.domain.governance import (
    ReviewDecision,
    ReviewFinding,
    ReviewFindingSeverity,
    ReviewFindingStatus,
)
from codex_ai_os.infrastructure.coordination import (
    CoordinationError,
    CoordinationStore,
)


def test_coordination_dag_rejection_resubmission_and_blocking(tmp_path: Path) -> None:
    root = _project(tmp_path / "coordination")
    engine = WorkflowEngine(root)
    run = engine.start("Exercise coordination edge states").run
    store = CoordinationStore(engine.store.database)
    blueprints = (
        _blueprint("api", "backend-engineer", ("src/api/",)),
        _blueprint("docs", "product-manager", ("docs/",)),
        _blueprint(
            "database",
            "database-engineer",
            ("src/api/models/",),
            depends_on=("docs",),
        ),
    )
    group, planned = store.create_group(
        run_id=run.id,
        name="edge-cases",
        phase="implementation",
        base_commit="a" * 40,
        blueprints=blueprints,
    )
    by_key = {task.key: task for task in planned}
    assert {task.key for task in store.ready_tasks(group.id)} == {"api", "docs"}
    assert set(by_key["database"].depends_on_task_ids) == {
        by_key["api"].id,
        by_key["docs"].id,
    }

    with pytest.raises(CoordinationError, match=r"1\.\.4"):
        store.ready_tasks(group.id, limit=0)
    with pytest.raises(CoordinationError, match="not found"):
        store.group_view("TASKGROUP-MISSING")
    with pytest.raises(CoordinationError, match="not found"):
        store.submit_handoff(
            task_id="TASK-MISSING",
            source_commit="b" * 40,
            artifact_refs={},
            checks=(),
        )

    api = by_key["api"]
    handoff_id = store.submit_handoff(
        task_id=api.id,
        source_commit="b" * 40,
        artifact_refs={"src/api/app.py": "c" * 64},
        checks=("pytest",),
        risks=("migration",),
        open_questions=("rollout",),
    )
    with pytest.raises(CoordinationError, match="own Handoff"):
        store.review_handoff(_review(handoff_id, api.agent, "b" * 40, "accepted"))
    with pytest.raises(CoordinationError, match="task HEAD"):
        store.review_handoff(_review(handoff_id, "reviewer", "d" * 40, "accepted"))

    rejected = store.review_handoff(
        _review(handoff_id, "reviewer", "b" * 40, "rejected")
    )
    assert rejected.decision == "rejected"
    assert engine.store.get_task(api.id).status.value == "running"

    same_handoff = store.submit_handoff(
        task_id=api.id,
        source_commit="e" * 40,
        artifact_refs={"src/api/app.py": "f" * 64},
        checks=("pytest", "ruff"),
    )
    assert same_handoff == handoff_id
    accepted = store.review_handoff(
        _review(handoff_id, "second-reviewer", "e" * 40, "accepted")
    )
    partially_joined = store.record_merge(
        decision=accepted,
        source_branch="agent/backend/api",
        integration_branch=f"workflow/{run.id}/integration",
        integration_head_before="a" * 40,
        merge_commit="1" * 40,
        parent_commits=("a" * 40, "e" * 40),
        status="merged",
    )
    assert partially_joined.status == "running"

    docs = by_key["docs"]
    docs_handoff = store.submit_handoff(
        task_id=docs.id,
        source_commit="2" * 40,
        artifact_refs={"docs/README.md": "3" * 64},
        checks=(),
    )
    blocked = store.review_handoff(
        _review(docs_handoff, "docs-reviewer", "2" * 40, "blocked")
    )
    assert blocked.decision == "blocked"
    view = store.group_view(group.id)
    assert view.status == "blocked"
    assert docs.id in view.blocked_task_ids


def test_accepted_handoff_creates_idempotent_merge_operation_and_preserves_push_failure(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path / "operation")
    engine = WorkflowEngine(root)
    run = engine.start("Persist accepted handoff operation").run
    store = CoordinationStore(engine.store.database)
    group, tasks = store.create_group(
        run_id=run.id,
        name="operation-review",
        phase="implementation",
        base_commit="a" * 40,
        blueprints=(_blueprint("backend", "backend-engineer", ("src/backend/",)),),
    )
    task = tasks[0]
    handoff_id = store.submit_handoff(
        task_id=task.id,
        source_commit="b" * 40,
        artifact_refs={"src/backend/app.py": "c" * 64},
        checks=("pytest",),
    )
    with engine.store.database.read_connection() as connection:
        handoff = connection.execute(
            "SELECT state_version FROM handoffs WHERE id = ?", (handoff_id,)
        ).fetchone()
    assert handoff is not None
    expected_version = int(handoff["state_version"])

    with pytest.raises(CoordinationError, match="expected Handoff version"):
        store.review_handoff(
            _review(
                handoff_id,
                "reviewer",
                "b" * 40,
                "accepted",
                expected_handoff_version=expected_version + 1,
                idempotency_key="handoff-review-stale",
            )
        )

    review = _review(
        handoff_id,
        "reviewer",
        "b" * 40,
        "accepted",
        expected_handoff_version=expected_version,
        idempotency_key="handoff-review-accepted",
    )
    accepted = store.review_handoff(review)
    assert accepted.operation_id is not None

    replayed = store.review_handoff(review)
    assert replayed.operation_id == accepted.operation_id

    with pytest.raises(CoordinationError, match="idempotency key"):
        store.review_handoff(
            _review(
                handoff_id,
                "reviewer",
                "b" * 40,
                "accepted",
                report_hash="5" * 64,
                expected_handoff_version=expected_version,
                idempotency_key="handoff-review-accepted",
            )
        )

    blocked = store.record_merge(
        decision=accepted,
        source_branch="agent/backend/backend",
        integration_branch=f"workflow/{run.id}/integration",
        integration_head_before="a" * 40,
        merge_commit="d" * 40,
        parent_commits=("a" * 40, "b" * 40),
        status="blocked",
        error_code="REMOTE_UNREACHABLE",
    )
    assert blocked.status == "blocked"
    with engine.store.database.read_connection() as connection:
        handoff_after = connection.execute(
            "SELECT status FROM handoffs WHERE id = ?", (handoff_id,)
        ).fetchone()
        task_after = connection.execute(
            "SELECT status, review_status FROM tasks WHERE id = ?", (task.id,)
        ).fetchone()
        operation = connection.execute(
            "SELECT kind, status FROM host_operations WHERE operation_id = ?",
            (accepted.operation_id,),
        ).fetchone()
        run_after = connection.execute(
            "SELECT run_status, integration_head FROM workflow_runs WHERE id = ?",
            (run.id,),
        ).fetchone()
    assert handoff_after is not None
    assert task_after is not None
    assert operation is not None
    assert run_after is not None
    assert handoff_after["status"] == "accepted"
    assert task_after["status"] == "completed"
    assert task_after["review_status"] == "accepted"
    assert operation["kind"] == "integration_merge"
    assert operation["status"] == "pending"
    assert run_after["run_status"] == "blocked"
    assert run_after["integration_head"] == "d" * 40
    assert group.id == blocked.id


def test_coordination_rejects_invalid_groups_and_cycles(tmp_path: Path) -> None:
    root = _project(tmp_path / "invalid")
    engine = WorkflowEngine(root)
    run = engine.start("Reject invalid coordination graphs").run
    store = CoordinationStore(engine.store.database)

    with pytest.raises(CoordinationError, match=r"1\.\.4"):
        store.create_group(
            run_id=run.id,
            name="empty",
            phase="implementation",
            base_commit="a" * 40,
            blueprints=(),
        )
    duplicate = _blueprint("same", "backend-engineer", ("src/a/",))
    with pytest.raises(CoordinationError, match="unique"):
        store.create_group(
            run_id=run.id,
            name="duplicate",
            phase="implementation",
            base_commit="a" * 40,
            blueprints=(duplicate, duplicate),
        )
    with pytest.raises(CoordinationError, match="unknown dependencies"):
        store.create_group(
            run_id=run.id,
            name="unknown",
            phase="implementation",
            base_commit="a" * 40,
            blueprints=(
                _blueprint(
                    "one", "backend-engineer", ("src/one/",), depends_on=("missing",)
                ),
            ),
        )
    with pytest.raises(CoordinationError, match="cycle"):
        store.create_group(
            run_id=run.id,
            name="cycle",
            phase="implementation",
            base_commit="a" * 40,
            blueprints=(
                _blueprint("one", "backend-engineer", ("src/one/",), depends_on=("two",)),
                _blueprint("two", "qa", ("tests/",), depends_on=("one",)),
            ),
        )
    with pytest.raises(CoordinationError, match="run not found"):
        store.create_group(
            run_id="RUN-MISSING",
            name="missing-run",
            phase="implementation",
            base_commit="a" * 40,
            blueprints=(_blueprint("one", "qa", ("tests/",)),),
        )


def test_coordination_service_provisions_integration_and_preserves_rejected_worktree(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path / "service")
    engine = WorkflowEngine(root)
    run = engine.start("Exercise coordination service boundaries").run
    service = CoordinationService(root)
    head = _git_output(root, "rev-parse", "HEAD")

    with pytest.raises(CoordinationError, match="run not found"):
        service.ensure_integration_worktree("RUN-MISSING", base_commit=head)
    integration = service.ensure_integration_worktree(run.id, base_commit=head)
    assert integration.is_dir()
    assert service.ensure_integration_worktree(run.id, base_commit=head) == integration

    group, tasks = service.store.create_group(
        run_id=run.id,
        name="service-review",
        phase="implementation",
        base_commit=head,
        blueprints=(
            _blueprint("backend", "backend-engineer", ("src/backend/",)),
            _blueprint("security", "security-reviewer", ("security/",)),
        ),
    )
    report = root / "reviews" / "handoff.md"
    report.parent.mkdir()
    report.write_text("# Handoff review\n\nEvidence checked.\n", encoding="utf-8")
    report_hash = hashlib.sha256(report.read_bytes()).hexdigest()

    first = tasks[0]
    handoff = service.store.submit_handoff(
        task_id=first.id,
        source_commit="a" * 40,
        artifact_refs={"src/backend/app.py": "b" * 64},
        checks=("pytest",),
    )
    with pytest.raises(CoordinationError, match="missing or unsafe"):
        service.review_and_integrate(
            _review(
                handoff,
                "reviewer",
                "a" * 40,
                "rejected",
                report_ref="missing.md",
                report_hash=report_hash,
            )
        )
    with pytest.raises(CoordinationError, match="hash mismatch"):
        service.review_and_integrate(
            _review(
                handoff,
                "reviewer",
                "a" * 40,
                "rejected",
                report_ref="reviews/handoff.md",
                report_hash="f" * 64,
            )
        )
    rejected = service.review_and_integrate(
        _review(
            handoff,
            "reviewer",
            "a" * 40,
            "rejected",
            report_ref="reviews/handoff.md",
            report_hash=report_hash,
        )
    )
    assert rejected.status == "rejected"
    assert rejected.integration_branch == f"workflow/{run.id}/integration"
    assert engine.store.get_task(first.id).worktree is None

    second = tasks[1]
    second_handoff = service.store.submit_handoff(
        task_id=second.id,
        source_commit="c" * 40,
        artifact_refs={"security/report.md": "d" * 64},
        checks=(),
    )
    blocked = service.review_and_integrate(
        _review(
            second_handoff,
            "security-lead",
            "c" * 40,
            "blocked",
            report_ref="reviews/handoff.md",
            report_hash=report_hash,
        )
    )
    assert blocked.status == "blocked"
    assert blocked.task_group.id == group.id


def _review(
    handoff_id: str,
    reviewer: str,
    commit: str,
    decision: str,
    *,
    report_ref: str = "reviews/report.md",
    report_hash: str = "4" * 64,
    expected_handoff_version: int | None = None,
    idempotency_key: str | None = None,
) -> HandoffReviewInput:
    return HandoffReviewInput(
        handoff_id=handoff_id,
        expected_handoff_version=expected_handoff_version,
        idempotency_key=idempotency_key,
        reviewer=reviewer,
        reviewed_commit=commit,
        decision=ReviewDecision(decision),
        reason=f"{decision} after evidence review",
        findings=(
            ReviewFinding(
                id="FINDING-1",
                severity=ReviewFindingSeverity.MEDIUM,
                status=ReviewFindingStatus.OPEN,
                summary="finding",
            ),
        ),
        risks=("risk",),
        report_ref=report_ref,
        report_hash=report_hash,
    )


def _blueprint(
    key: str,
    agent: str,
    allowed_paths: tuple[str, ...],
    *,
    depends_on: tuple[str, ...] = (),
) -> TaskBlueprint:
    return TaskBlueprint(
        key=key,
        agent=agent,
        skill="testing",
        prompt=f"Implement {key}",
        allowed_paths=allowed_paths,
        depends_on=depends_on,
    )


def _project(root: Path) -> Path:
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Fixture")
    _git(root, "config", "user.email", "fixture@example.invalid")
    ProjectInitializer().initialize(
        root,
        project_id=f"PROJECT-{root.name.upper()}",
        name="Coordination fixture",
        project_type=ProjectType.BACKEND,
        git_push_policy=GitPushPolicy.FIXTURE_LOCAL_ONLY,
        schema_version="1.1",
    )
    _git(root, "add", ".")
    _git(root, "commit", "-m", "chore: initialize coordination fixture")
    return root


def _git(root: Path, *arguments: str) -> None:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")


def _git_output(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()
