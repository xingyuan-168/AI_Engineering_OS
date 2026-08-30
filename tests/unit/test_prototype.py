from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.application.prototype import (
    REQUIRED_PROTOTYPE_STATES,
    PrototypeReviewService,
    validate_html_prototype,
)
from codex_ai_os.application.workflow import WorkflowEngine, WorkflowError
from codex_ai_os.domain.config import GitPushPolicy, ProjectType
from codex_ai_os.domain.governance import (
    ReviewDecision,
    ReviewFinding,
    ReviewFindingSeverity,
    ReviewFindingStatus,
)
from codex_ai_os.domain.invocation import InvocationContext, InvocationSource
from codex_ai_os.domain.workflow import Gate, WorkflowPhase, WorkflowRun


def test_offline_html_prototype_validator_accepts_complete_accessible_flow() -> None:
    states = "".join(
        f'<section data-state="{state}">{state}</section>'
        for state in sorted(REQUIRED_PROTOTYPE_STATES)
    )
    content = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><style>:focus{{outline:2px solid}}</style></head>
<body><main>{states}<label for="name">Name</label><input id="name">
<button type="button">Continue</button><script>document.body.dataset.ready='true';</script>
</main></body></html>""".encode()

    report = validate_html_prototype(content)

    assert report.valid is True
    assert set(report.states) == REQUIRED_PROTOTYPE_STATES
    assert report.control_count == 2


def test_offline_html_prototype_validator_fails_closed() -> None:
    report = validate_html_prototype(
        b'<html><head><script src="https://example.invalid/app.js"></script></head>'
        b'<body><input placeholder="TODO"></body></html>'
    )

    assert report.valid is False
    assert any("external" in finding for finding in report.findings)
    assert any("interaction states" in finding for finding in report.findings)
    assert any("unlabelled" in finding for finding in report.findings)
    assert any("placeholder" in finding for finding in report.findings)


def test_prototype_review_records_exact_commit_evidence(tmp_path: Path) -> None:
    root = tmp_path / "frontend"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Fixture")
    _git(root, "config", "user.email", "fixture@example.invalid")
    ProjectInitializer().initialize(
        root,
        project_id="PROJECT-PROTOTYPE",
        name="Prototype fixture",
        project_type=ProjectType.FRONTEND,
        git_push_policy=GitPushPolicy.FIXTURE_LOCAL_ONLY,
    )
    _git(root, "add", ".")
    _git(root, "commit", "-m", "chore: initialize prototype fixture")
    engine = WorkflowEngine(root)
    started = engine.start("Review an HTML prototype")
    assert started.active_task is not None
    task_id = started.active_task.id
    assert started.next_action is not None
    assert started.next_action.worktree is not None
    worktree = Path(started.next_action.worktree)
    prototype_path = "docs/prototypes/main/index.html"
    target = worktree / prototype_path
    target.parent.mkdir(parents=True)
    target.write_bytes(_valid_prototype())
    _git(worktree, "add", prototype_path)
    _git(worktree, "commit", "-m", "feat: add reviewed HTML prototype")
    head = _git_output(worktree, "rev-parse", "HEAD")
    branch = _git_output(worktree, "branch", "--show-current")
    digest = hashlib.sha256(
        subprocess.check_output(["git", "-C", str(worktree), "show", f"{head}:{prototype_path}"])
    ).hexdigest()
    checkpoint = {
        **started.run.checkpoint,
        "next_action": {
            "kind": "approval",
            "gate": "G2",
            "prompt": "Review G2",
            "risk_level": "medium",
        },
        "pending_gate": "G2",
        "last_completed_task": started.active_task.id,
        "last_commit_sha": head,
    }
    with engine.store.database.connection() as connection:
        connection.execute(
            """
            UPDATE tasks SET agent = 'frontend-engineer', producer = 'frontend-engineer',
                skill = 'html-prototype', status = 'completed', state_version = 1,
                head_commit = ?, branch = ?, worktree = ?, updated_at = ?
            WHERE id = ?
            """,
            (head, branch, str(worktree), "2026-08-30T00:00:00+00:00", started.active_task.id),
        )
        connection.execute(
            """
            UPDATE workflow_runs SET workflow_phase = 'prototype',
                run_status = 'needs_approval', state_version = 1,
                checkpoint_json = ?, updated_at = ? WHERE id = ?
            """,
            (
                json.dumps(checkpoint, separators=(",", ":")),
                "2026-08-30T00:00:00+00:00",
                started.run.id,
            ),
        )
        connection.commit()

    service = PrototypeReviewService(root)

    def submit(
        idempotency_key: str,
        *,
        invocation: InvocationContext | None,
        findings: tuple[ReviewFinding, ...] = (),
    ) -> WorkflowRun:
        return service.submit(
            run_id=started.run.id,
            task_id=task_id,
            expected_task_version=1,
            expected_state_version=1,
            idempotency_key=idempotency_key,
            prototype_path=prototype_path,
            prototype_hash=digest,
            reviewed_commit=head,
            decision=ReviewDecision.ACCEPTED,
            reviewer="ux-owner",
            reason="interaction flow confirmed",
            findings=findings,
            invocation=invocation,
        )

    with pytest.raises(WorkflowError, match="trusted invocation context"):
        submit("prototype-review-untrusted", invocation=None)
    with pytest.raises(WorkflowError, match="open high/critical"):
        submit(
            "prototype-review-open-finding",
            findings=(
                ReviewFinding(
                    id="UX-1",
                    severity=ReviewFindingSeverity.HIGH,
                    status=ReviewFindingStatus.OPEN,
                    summary="Critical flow is inaccessible",
                ),
            ),
            invocation=InvocationContext.local(InvocationSource.CLI),
        )

    reviewed = submit(
        "prototype-review-accepted",
        invocation=InvocationContext.local(InvocationSource.CLI),
    )

    assert reviewed.workflow_phase is WorkflowPhase.PROTOTYPE
    assert reviewed.state_version == 2
    with engine.store.database.read_connection() as connection:
        artifact = connection.execute(
            "SELECT artifact_type, source_commit FROM artifact_evidence "
            "WHERE run_id = ? AND path = ?",
            (started.run.id, prototype_path),
        ).fetchone()
        check = connection.execute(
            "SELECT check_name, status, source_commit FROM check_evidence WHERE run_id = ?",
            (started.run.id,),
        ).fetchone()
        review = connection.execute(
            "SELECT review_type, decision, reviewed_commit FROM review_evidence WHERE run_id = ?",
            (started.run.id,),
        ).fetchone()
        review_event = connection.execute(
            "SELECT payload_json FROM events WHERE run_id = ? "
            "AND event_type = 'prototype.reviewed'",
            (started.run.id,),
        ).fetchone()
    assert artifact is not None and artifact["artifact_type"] == "html-prototype"
    assert artifact["source_commit"] == head
    assert check is not None and check["check_name"] == "html-prototype-validator"
    assert check["status"] == "passed" and check["source_commit"] == head
    assert review is not None and review["review_type"] == "handoff"
    assert review["decision"] == "accepted" and review["reviewed_commit"] == head
    assert review_event is not None
    assert json.loads(str(review_event["payload_json"]))["review"]["decision"] == "accepted"
    bundle = engine.evidence_store.evaluate_gate_bundle(
        run_id=started.run.id,
        gate=Gate.G2,
        state_version=reviewed.state_version,
        source_commit=head,
    )
    assert "artifact_type:html-prototype" not in bundle.missing
    assert "check:html-prototype-validator" not in bundle.missing
    assert "review:ux-prototype" not in bundle.missing


def _valid_prototype() -> bytes:
    states = "".join(
        f'<section data-state="{state}">{state}</section>'
        for state in sorted(REQUIRED_PROTOTYPE_STATES)
    )
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        "<style>button:focus{outline:2px solid}</style></head>"
        f'<body>{states}<label for="name">Name</label><input id="name">'
        '<button type="button">Continue</button><script>document.body.dataset.ready="1";</script>'
        "</body></html>"
    ).encode()


def _git(root: Path, *arguments: str) -> None:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr


def _git_output(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()
