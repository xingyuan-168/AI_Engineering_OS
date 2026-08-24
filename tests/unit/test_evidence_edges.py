# pyright: reportPrivateUsage=false
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.application.workflow import WorkflowEngine
from codex_ai_os.domain.config import GitPushPolicy, ProjectType
from codex_ai_os.domain.governance import (
    ArtifactEvidenceInput,
    ReviewDecision,
    ReviewEvidenceInput,
)
from codex_ai_os.domain.workflow import Gate
from codex_ai_os.infrastructure.evidence import EvidenceError, GateBundle


def test_evidence_store_rejects_missing_stale_and_unsafe_artifacts(tmp_path: Path) -> None:
    root = _project(tmp_path / "artifacts")
    engine = WorkflowEngine(root)
    current = engine.start("Exercise evidence validation")
    assert current.active_task is not None
    store = engine.evidence_store
    task_id = current.active_task.id

    with pytest.raises(EvidenceError, match="structured artifacts"):
        store.record_task_evidence(
            run_id=current.run.id,
            task_id=task_id,
            artifacts=(),
            checks=(),
        )
    with pytest.raises(EvidenceError, match="identity mismatch"):
        store.record_task_evidence(
            run_id="RUN-MISSING",
            task_id=task_id,
            artifacts=(_artifact("docs/PROJECT_MASTER.md", "a" * 64),),
            checks=(),
        )
    with pytest.raises(EvidenceError, match="unsafe"):
        store.record_task_evidence(
            run_id=current.run.id,
            task_id=task_id,
            artifacts=(_artifact("../outside.md", "a" * 64),),
            checks=(),
        )
    with pytest.raises(EvidenceError, match="missing or unsafe"):
        store.record_task_evidence(
            run_id=current.run.id,
            task_id=task_id,
            artifacts=(
                _artifact(
                    f".codex-os/artifacts/{current.run.id}/missing.json", "a" * 64
                ),
            ),
            checks=(),
        )
    with pytest.raises(EvidenceError, match="not in source Commit"):
        store.record_task_evidence(
            run_id=current.run.id,
            task_id=task_id,
            artifacts=(_artifact("docs/missing.md", "a" * 64),),
            checks=(),
        )
    head = _git_output(root, "rev-parse", "HEAD")
    with pytest.raises(EvidenceError, match="hash mismatch"):
        store.record_task_evidence(
            run_id=current.run.id,
            task_id=task_id,
            artifacts=(
                ArtifactEvidenceInput(
                    path="docs/PROJECT_MASTER.md",
                    artifact_type="project-master",
                    sha256="a" * 64,
                    source_commit=head,
                ),
            ),
            checks=(),
        )


def test_review_gate_bundle_and_document_revalidation_fail_closed(tmp_path: Path) -> None:
    root = _project(tmp_path / "reviews")
    engine = WorkflowEngine(root)
    current = engine.start("Exercise review and Gate edges")
    assert current.active_task is not None
    store = engine.evidence_store
    report = root / "reviews" / "report.md"
    report.parent.mkdir()
    report.write_text("# Review\n\nAccepted.\n", encoding="utf-8")
    report_hash = hashlib.sha256(report.read_bytes()).hexdigest()
    head = _git_output(root, "rev-parse", "HEAD")
    review = ReviewEvidenceInput(
        review_type="code",
        reviewer="reviewer",
        reviewed_commit=head,
        decision=ReviewDecision.ACCEPTED,
        report_ref="reviews/report.md",
        report_hash=report_hash,
    )

    with pytest.raises(EvidenceError, match="identity mismatch"):
        store.record_review(
            project_id=current.run.project_id,
            run_id="RUN-MISSING",
            task_id=current.active_task.id,
            review=review,
        )
    missing_report = review.model_copy(update={"report_ref": "reviews/missing.md"})
    with pytest.raises(EvidenceError, match="missing or unsafe"):
        store.record_review(
            project_id=current.run.project_id,
            run_id=current.run.id,
            task_id=None,
            review=missing_report,
        )
    store.record_review(
        project_id=current.run.project_id,
        run_id=current.run.id,
        task_id=None,
        review=review,
    )
    bundle = store.build_gate_bundle(
        run_id=current.run.id,
        gate=Gate.G3,
        state_version=current.run.state_version,
        source_commit=head,
    )
    assert bundle.status == "building"
    assert "review:security" in bundle.missing
    with pytest.raises(EvidenceError, match="incomplete"):
        store.require_complete(bundle)

    with engine.store.database.connection() as connection:
        connection.execute(
            "UPDATE tasks SET head_commit = ? WHERE id = ?",
            (head, current.active_task.id),
        )
        connection.commit()
    findings = store._document_findings(current.run.id, Gate.G0, head)
    assert findings
    assert store._is_ancestor(head, head) is True
    assert store._is_ancestor("f" * 40, head) is False

    complete = GateBundle(
        id="GATEBUNDLE-COMPLETE",
        run_id=current.run.id,
        gate=Gate.G0,
        state_version=0,
        source_commit=head,
        status="complete",
        artifact_paths=(),
        check_names=(),
        review_types=(),
        missing=(),
        bundle_hash="a" * 64,
    )
    store.require_complete(complete)


def _artifact(path: str, digest: str) -> ArtifactEvidenceInput:
    return ArtifactEvidenceInput(
        path=path,
        artifact_type="test",
        sha256=digest,
        source_commit="b" * 40,
    )


def _project(root: Path) -> Path:
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Fixture")
    _git(root, "config", "user.email", "fixture@example.invalid")
    ProjectInitializer().initialize(
        root,
        project_id=f"PROJECT-{root.name.upper()}",
        name="Evidence fixture",
        project_type=ProjectType.BACKEND,
        git_push_policy=GitPushPolicy.FIXTURE_LOCAL_ONLY,
        schema_version="1.1",
    )
    _git(root, "add", ".")
    _git(root, "commit", "-m", "chore: initialize evidence fixture")
    return root


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


def _git(root: Path, *arguments: str) -> None:
    _git_output(root, *arguments)
