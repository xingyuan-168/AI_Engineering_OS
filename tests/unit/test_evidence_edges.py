# pyright: reportPrivateUsage=false
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import cast

import pytest

from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.application.workflow import WorkflowEngine
from codex_ai_os.domain.config import GitPushPolicy, ProjectType
from codex_ai_os.domain.governance import (
    ArtifactEvidenceInput,
    ReviewDecision,
    ReviewEvidenceInput,
)
from codex_ai_os.domain.workflow import ChangeKind, Gate, PushStatus, TaskCompletion
from codex_ai_os.infrastructure.evidence import EvidenceError, GateBundle


def test_migrated_gate_artifacts_are_reverified_and_bound(tmp_path: Path) -> None:
    root = _project(tmp_path / "migration")
    engine = WorkflowEngine(root)
    current = engine.start("Revalidate a migrated approval")
    assert current.active_task is not None
    metadata = json.dumps(
        {
            "schema_version": "1.2",
            "document_version": "0.2.0",
            "status": "review-ready",
            "owner": "fixture-owner",
            "requirement_refs": ["REQ-1.6.2"],
        },
        separators=(",", ":"),
    )
    for path, title in (
        ("docs/PROJECT_MASTER.md", "Project Master"),
        ("docs/SCOPE.md", "Scope"),
    ):
        (root / path).write_text(
            f"<!-- codex-os-document: {metadata} -->\n# {title}\n\n"
            "Verified policy: 未批准占位内容必须阻塞。\n",
            encoding="utf-8",
        )
    _git(root, "add", "docs/PROJECT_MASTER.md", "docs/SCOPE.md")
    _git(root, "commit", "-m", "docs: approve migrated gate evidence")
    head = _git_output(root, "rev-parse", "HEAD")
    artifacts: list[tuple[str, str]] = []
    for path in ("docs/PROJECT_MASTER.md", "docs/SCOPE.md"):
        content = subprocess.check_output(["git", "-C", str(root), "show", f"{head}:{path}"])
        artifacts.append((path, hashlib.sha256(content).hexdigest()))
    with engine.store.database.connection() as connection:
        connection.execute(
            """
            UPDATE tasks SET status = 'completed', head_commit = NULL,
                output_ref = ?, worktree = ?, updated_at = ? WHERE id = ?
            """,
            (
                json.dumps({"commit_sha": head}),
                str(root),
                "2026-01-01T00:00:01+00:00",
                current.active_task.id,
            ),
        )
        for path, digest in artifacts:
            connection.execute(
                """
                INSERT INTO artifacts(
                    id, run_id, task_id, path, kind, content_hash,
                    source_commit, status, created_at
                ) VALUES (?, ?, ?, ?, 'document', ?, ?, 'verified', ?)
                """,
                (
                    f"ARTIFACT-{path.rsplit('/', 1)[-1]}",
                    current.run.id,
                    current.active_task.id,
                    path,
                    digest,
                    head,
                    "2026-01-01T00:00:01+00:00",
                ),
            )
        connection.execute(
            """
            INSERT INTO approvals(
                id, run_id, gate, decision, reviewer, reason,
                evidence_refs_json, state_version, created_at,
                evidence_bundle_id, evidence_bundle_hash, release_authority_json
            ) VALUES (?, ?, 'G0', 'approved', 'legacy-owner', 'legacy approval',
                      '[]', 1, ?, NULL, NULL, NULL)
            """,
            (
                "APPROVAL-LEGACY-G0",
                current.run.id,
                "2026-01-01T00:00:02+00:00",
            ),
        )
        connection.commit()

    bundle_ids = engine.evidence_store.audit_migrated_run(current.run.id)

    assert len(bundle_ids) == 1
    with engine.store.database.connection() as connection:
        approval = connection.execute(
            """
            SELECT evidence_bundle_id, evidence_bundle_hash
            FROM approvals WHERE id = 'APPROVAL-LEGACY-G0'
            """
        ).fetchone()
        promoted = connection.execute(
            "SELECT COUNT(*) FROM artifact_evidence WHERE run_id = ?",
            (current.run.id,),
        ).fetchone()
    assert approval is not None
    assert approval["evidence_bundle_id"] == bundle_ids[0]
    assert len(str(approval["evidence_bundle_hash"])) == 64
    assert promoted is not None and promoted[0] == 2


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
    head = _git_output(root, "rev-parse", "HEAD")
    report_hash = hashlib.sha256(report.read_bytes()).hexdigest()
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
    with pytest.raises(EvidenceError, match="missing or unsafe"):
        store.record_review(
            project_id=current.run.project_id,
            run_id=current.run.id,
            task_id=None,
            review=review,
        )
    _git(root, "add", "reviews/report.md")
    _git(root, "commit", "-m", "test: add review report")
    head = _git_output(root, "rev-parse", "HEAD")
    committed_report = subprocess.check_output(
        ["git", "-C", str(root), "show", f"{head}:reviews/report.md"]
    )
    review = review.model_copy(
        update={
            "reviewed_commit": head,
            "report_hash": hashlib.sha256(committed_report).hexdigest(),
        }
    )
    report.write_text("# Review\n\nTampered current file.\n", encoding="utf-8")
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
    findings = store._document_findings(
        current.run.id,
        head,
        frozenset({"docs/PROJECT_MASTER.md", "docs/SCOPE.md"}),
    )
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


def test_gate_preflight_is_read_only_and_matches_approval(tmp_path: Path) -> None:
    root = _project(tmp_path / "preflight")
    engine = WorkflowEngine(root)
    current = engine.start("Validate exact G0 preflight")
    assert current.active_task is not None
    assert current.next_action is not None
    assert current.next_action.worktree is not None
    assert current.next_action.gate_requirements is not None
    assert current.next_action.gate_requirements["gate"] == "G0"
    worktree = Path(current.next_action.worktree)
    metadata = json.dumps(
        {
            "schema_version": "1.2",
            "document_version": "0.0.1",
            "status": "review-ready",
            "owner": "product-manager",
            "requirement_refs": ["REQ-G0"],
        },
        separators=(",", ":"),
    )
    for relative in ("docs/PROJECT_MASTER.md", "docs/SCOPE.md"):
        (worktree / relative).write_text(
            f"# Gate document\n\n<!-- codex-os-document: {metadata} -->\n\nApproved content.\n",
            encoding="utf-8",
        )
    _git(worktree, "add", "docs/PROJECT_MASTER.md", "docs/SCOPE.md")
    _git(worktree, "commit", "-m", "docs: prepare G0 evidence")
    head = _git_output(worktree, "rev-parse", "HEAD")
    artifacts: list[ArtifactEvidenceInput] = []
    hashes: dict[str, str] = {}
    for relative in ("docs/PROJECT_MASTER.md", "docs/SCOPE.md"):
        content = subprocess.check_output(
            ["git", "-C", str(worktree), "show", f"{head}:{relative}"]
        )
        digest = hashlib.sha256(content).hexdigest()
        hashes[relative] = digest
        artifacts.append(
            ArtifactEvidenceInput(
                path=relative,
                artifact_type="governance-document",
                sha256=digest,
                source_commit=head,
            )
        )
    waiting = engine.complete_task(
        current.run.id,
        TaskCompletion(
            task_id=current.active_task.id,
            change_kind=ChangeKind.REPOSITORY,
            branch=current.next_action.branch,
            commit_sha=head,
            push_status=PushStatus.LOCAL_ONLY,
            artifact_paths_and_hashes=hashes,
            artifacts=tuple(artifacts),
        ),
        expected_task_version=current.active_task.state_version,
        idempotency_key="preflight-complete",
    )
    assert waiting.next_action is not None
    assert waiting.next_action.gate_requirements is not None
    with engine.store.database.read_connection() as connection:
        before = int(
            connection.execute("SELECT COUNT(*) FROM gate_evidence_bundles").fetchone()[0]
        )
    result = WorkflowEngine(root, readonly=True).gate_preflight(
        waiting.run.id,
        gate=Gate.G0,
        expected_state_version=waiting.run.state_version,
    )
    with engine.store.database.read_connection() as connection:
        after = int(
            connection.execute("SELECT COUNT(*) FROM gate_evidence_bundles").fetchone()[0]
        )
    assert result["valid"] is False
    assert before == after == 0
    assert any(
        action.get("operation") == "set_document_version"
        for action in cast(list[dict[str, object]], result["repair_actions"])
    )
    for relative in ("docs/PROJECT_MASTER.md", "docs/SCOPE.md"):
        path = worktree / relative
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                '"document_version":"0.0.1"',
                '"document_version":"0.1.0"',
            ),
            encoding="utf-8",
        )
    _git(worktree, "add", "docs/PROJECT_MASTER.md", "docs/SCOPE.md")
    _git(worktree, "commit", "-m", "docs: repair G0 document versions")
    repaired_head = _git_output(worktree, "rev-parse", "HEAD")
    repaired_artifacts: list[ArtifactEvidenceInput] = []
    repaired_hashes: dict[str, str] = {}
    for relative in ("docs/PROJECT_MASTER.md", "docs/SCOPE.md"):
        content = subprocess.check_output(
            ["git", "-C", str(worktree), "show", f"{repaired_head}:{relative}"]
        )
        digest = hashlib.sha256(content).hexdigest()
        repaired_hashes[relative] = digest
        repaired_artifacts.append(
            ArtifactEvidenceInput(
                path=relative,
                artifact_type="governance-document",
                sha256=digest,
                source_commit=repaired_head,
            )
        )
    completed_task = engine.store.get_task(current.active_task.id)
    amendment = TaskCompletion(
        task_id=current.active_task.id,
        change_kind=ChangeKind.REPOSITORY,
        branch=current.next_action.branch,
        commit_sha=repaired_head,
        push_status=PushStatus.LOCAL_ONLY,
        artifact_paths_and_hashes=repaired_hashes,
        artifacts=tuple(repaired_artifacts),
    )
    amended = engine.amend_task_evidence(
        waiting.run.id,
        amendment,
        expected_task_version=completed_task.state_version,
        expected_state_version=waiting.run.state_version,
        idempotency_key="amend-g0-evidence",
    )
    replayed = engine.amend_task_evidence(
        waiting.run.id,
        amendment,
        expected_task_version=completed_task.state_version,
        expected_state_version=waiting.run.state_version,
        idempotency_key="amend-g0-evidence",
    )
    assert replayed.run.state_version == amended.run.state_version
    result = WorkflowEngine(root, readonly=True).gate_preflight(
        amended.run.id,
        gate=Gate.G0,
        expected_state_version=amended.run.state_version,
    )
    assert result["valid"] is True
    approved = engine.submit_approval(
        amended.run.id,
        gate=Gate.G0,
        approved=True,
        reviewer="independent-user",
        reason="preflight snapshot reviewed",
        expected_state_version=amended.run.state_version,
        idempotency_key="approve-g0",
        evidence_bundle_hash=str(result["evidence_bundle_hash"]),
    )
    assert approved.run.workflow_phase.value == "requirements"


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
