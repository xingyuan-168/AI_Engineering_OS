from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.application.workflow import WorkflowEngine
from codex_ai_os.domain.config import GitPushPolicy, ProjectType
from codex_ai_os.domain.workflow import Gate
from codex_ai_os.infrastructure.evidence import GateBundle


@pytest.fixture
def evidence_project(tmp_path: Path) -> tuple[WorkflowEngine, str, str]:
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    ProjectInitializer().initialize(
        tmp_path, project_id="PROJECT-FRESHNESS", name="Evidence freshness",
        project_type=ProjectType.BACKEND,
        git_push_policy=GitPushPolicy.FIXTURE_LOCAL_ONLY, schema_version="1.1",
    )
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "chore: initialize fixture")
    engine = WorkflowEngine(tmp_path)
    run = engine.start("Verify evidence freshness").run
    return engine, run.id, _git(tmp_path, "rev-parse", "HEAD")


def test_later_failed_check_shadows_earlier_success(
    evidence_project: tuple[WorkflowEngine, str, str],
) -> None:
    engine, run_id, head = evidence_project
    _check(engine, run_id, head, "first", "passed")
    green = _bundle(engine, run_id, head)
    assert "ruff" in green.check_names
    # A caller-supplied execution timestamp must not resurrect the old success.
    _check(engine, run_id, head, "later", "failed")
    red = _bundle(engine, run_id, head)
    assert "check:ruff" in red.missing
    assert "ruff" not in red.check_names
    assert red.bundle_hash != green.bundle_hash


def test_g3_does_not_inherit_ancestor_checks_or_reviews(
    evidence_project: tuple[WorkflowEngine, str, str],
) -> None:
    engine, run_id, head = evidence_project
    _check(engine, run_id, head, "check", "passed")
    _review(engine, run_id, head, "review", "accepted")
    _git(engine.config.root, "commit", "--allow-empty", "-m", "feat: change source baseline")
    new_head = _git(engine.config.root, "rev-parse", "HEAD")
    bundle = _bundle(engine, run_id, new_head)
    assert {"check:ruff", "review:code"} <= set(bundle.missing)


@pytest.mark.parametrize("decision", ["rejected", "blocked"])
def test_later_review_denial_shadows_accepted_review(
    evidence_project: tuple[WorkflowEngine, str, str], decision: str,
) -> None:
    engine, run_id, head = evidence_project
    _review(engine, run_id, head, "first", "accepted")
    assert "code" in _bundle(engine, run_id, head).review_types
    _review(engine, run_id, head, "later", decision)
    assert "review:code" in _bundle(engine, run_id, head).missing
    _review(engine, run_id, head, "repaired", "accepted")
    assert "code" in _bundle(engine, run_id, head).review_types


def test_preflight_rehashes_audit_reports(
    evidence_project: tuple[WorkflowEngine, str, str],
) -> None:
    engine, run_id, head = evidence_project
    relative, _ = _check(engine, run_id, head, "check", "passed")
    assert "ruff" in _bundle(engine, run_id, head).check_names
    (engine.config.root / relative).write_text("changed after registration", encoding="utf-8")
    assert "check:ruff" in _bundle(engine, run_id, head).missing


def test_inherited_artifact_must_match_target_commit_blob(
    evidence_project: tuple[WorkflowEngine, str, str],
) -> None:
    engine, run_id, head = evidence_project
    root = engine.config.root
    path = "README.md"
    digest = hashlib.sha256((root / path).read_bytes()).hexdigest()
    with engine.store.database.connection() as connection:
        connection.execute(
            "INSERT INTO artifact_evidence(id,run_id,path,artifact_type,content_hash,"
            "source_commit,status,created_at) VALUES ('ARTIFACT-OLD',?,?,'document',?,?,"
            "'verified','2026-09-05T00:00:00+00:00')", (run_id, path, digest, head),
        )
        connection.commit()
    assert path in _bundle(engine, run_id, head).artifact_paths
    (root / path).write_text("# Changed project\n", encoding="utf-8")
    _git(root, "add", path)
    _git(root, "commit", "-m", "docs: replace artifact")
    assert path not in _bundle(engine, run_id, _git(root, "rev-parse", "HEAD")).artifact_paths


def _bundle(engine: WorkflowEngine, run_id: str, head: str) -> GateBundle:
    return engine.evidence_store.evaluate_gate_bundle(
        run_id=run_id, gate=Gate.G3, state_version=0, source_commit=head,
    )


def _report(engine: WorkflowEngine, run_id: str, label: str) -> tuple[str, str]:
    relative = f".codex-os/artifacts/{run_id}/{label}.json"
    path = engine.config.root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps({"attempt": label}).encode("utf-8")
    path.write_bytes(content)
    return relative, hashlib.sha256(content).hexdigest()


def _check(
    engine: WorkflowEngine, run_id: str, head: str, label: str, status: str,
) -> tuple[str, str]:
    relative, digest = _report(engine, run_id, label)
    with engine.store.database.connection() as connection:
        connection.execute(
            "INSERT INTO check_evidence(id,run_id,check_name,command_hash,exit_code,"
            "report_path,report_hash,source_commit,status,executed_at) "
            "VALUES (?,?,'ruff',?,?,?,?,?,?,?)",
            (f"CHECK-{label}", run_id, "a" * 64, 0 if status == "passed" else 1,
             relative, digest, head, status, "2026-09-05T00:00:00+00:00"),
        )
        connection.commit()
    return relative, digest


def _review(engine: WorkflowEngine, run_id: str, head: str, label: str, decision: str) -> None:
    relative, digest = _report(engine, run_id, label)
    with engine.store.database.connection() as connection:
        connection.execute(
            "INSERT INTO review_evidence(id,project_id,run_id,review_type,reviewer,"
            "reviewed_commit,decision,findings_json,risks_json,report_ref,report_hash,"
            "created_at) VALUES (?,?,?,'code','reviewer',?,?,'[]','[]',?,?,?)",
            (f"REVIEW-{label}", engine.config.project_id, run_id, head, decision,
             relative, digest, "2026-09-05T00:00:00+00:00"),
        )
        connection.commit()


def _git(root: Path, *argv: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *argv], text=True).strip()
