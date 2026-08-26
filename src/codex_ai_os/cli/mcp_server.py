"""stdio MCP server exposing the deterministic AI Engineering OS runtime."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from pydantic import ValidationError

from codex_ai_os.application.execution import ExecutionServiceError
from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.application.release import ReleaseCandidateService
from codex_ai_os.application.repository import RepositoryGovernanceService
from codex_ai_os.application.verification import VerificationService
from codex_ai_os.application.workflow import WorkflowEngine, WorkflowError, WorkflowResult
from codex_ai_os.application.worktree import WorktreeService, WorktreeServiceError
from codex_ai_os.domain.config import ProjectType, RiskLevel
from codex_ai_os.domain.coordination import HandoffReviewInput
from codex_ai_os.domain.governance import (
    ArtifactEvidenceInput,
    CheckEvidenceInput,
    G4ApprovalInput,
    ReleaseAuthority,
    ReviewDecision,
)
from codex_ai_os.domain.versions import RUNTIME_VERSIONS
from codex_ai_os.domain.workflow import (
    ChangeKind,
    Gate,
    PushStatus,
    RunStatus,
    TaskCompletion,
    WorkflowPhase,
)
from codex_ai_os.infrastructure.config import ConfigError, load_project_config
from codex_ai_os.infrastructure.coordination import CoordinationError
from codex_ai_os.infrastructure.database import Database, MigrationError
from codex_ai_os.infrastructure.documents import DocumentManager
from codex_ai_os.infrastructure.memory import MemoryRecord, MemoryStore, MemoryStoreError

mcp = MCPServer(
    "AI Engineering OS",
    version=RUNTIME_VERSIONS.software,
    instructions=(
        "Use these tools to run deterministic engineering workflows. Execute the returned "
        "next_action in Codex, then report repository evidence through task_complete."
    ),
)


@mcp.tool()
def project_init(
    project_root: str,
    project_id: str,
    name: str,
    project_type: str = "generic",
    risk_level: str = "medium",
) -> dict[str, Any]:
    """Initialize an idempotent local project, governed documents, and runtime database."""

    def operation() -> dict[str, Any]:
        result = ProjectInitializer().initialize(
            Path(project_root),
            project_id=project_id,
            name=name,
            project_type=ProjectType(project_type),
            risk_level=RiskLevel(risk_level),
        )
        return _success(
            project_id=result.config.project_id,
            root=result.config.root.as_posix(),
            created_paths=list(result.created_paths),
            database=result.database_path.as_posix(),
            context=result.context_path.as_posix(),
            documents_ok=result.document_report.ok,
            repository_ready=result.repository_ready,
            repository_blockers=list(result.repository_blockers),
        )

    return _invoke(operation)


@mcp.tool()
def repository_check(
    project_root: str,
    target_branch: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Validate formal GitHub readiness, hygiene, tracked content, and path safety."""

    def operation() -> dict[str, Any]:
        report = RepositoryGovernanceService(Path(project_root)).check(
            target_branch=target_branch,
            run_id=run_id,
        )
        return _success(**report.model_dump(mode="json"))

    return _invoke(operation)


@mcp.tool()
def workflow_start(
    project_root: str,
    goal: str,
    workflow_name: str = "new-project",
    profiles: list[str] | None = None,
    target_branch: str | None = None,
) -> dict[str, Any]:
    """Start, or idempotently return, a supported governed workflow for a goal."""

    return _invoke(
        lambda: _workflow_payload(
            WorkflowEngine(Path(project_root)).start(
                goal,
                workflow_name=workflow_name,
                profiles=tuple(profiles or ()),
                target_branch=target_branch,
            ),
            Path(project_root),
        )
    )


@mcp.tool()
def workflow_status(project_root: str, run_id: str) -> dict[str, Any]:
    """Return the persisted dual-axis workflow state and current host action."""

    return _invoke(
        lambda: _workflow_payload(
            WorkflowEngine(Path(project_root), readonly=True).status(run_id),
            Path(project_root),
        )
    )


@mcp.tool()
def workflow_step(project_root: str, run_id: str) -> dict[str, Any]:
    """Return the deterministic next action without executing or advancing it."""

    return _invoke(
        lambda: _workflow_payload(
            WorkflowEngine(Path(project_root), readonly=True).status(run_id),
            Path(project_root),
        )
    )


@mcp.tool()
def workflow_resume(project_root: str, run_id: str) -> dict[str, Any]:
    """Resume a paused, blocked, or failed workflow from its persisted checkpoint."""

    return _invoke(
        lambda: _workflow_payload(
            WorkflowEngine(Path(project_root)).resume(run_id), Path(project_root)
        )
    )


@mcp.tool()
def approval_submit(
    project_root: str,
    run_id: str,
    gate: str,
    approved: bool,
    reviewer: str,
    reason: str,
    pr_number: int | None = None,
    pr_url: str | None = None,
    merge_commit: str | None = None,
    version: str | None = None,
    release_authorized: bool = False,
    release_authorized_by: str | None = None,
) -> dict[str, Any]:
    """Approve or reject exactly the gate currently requested by a workflow."""

    def operation() -> dict[str, Any]:
        complete_g4 = all(
            value is not None
            for value in (pr_number, pr_url, merge_commit, version, release_authorized_by)
        )
        g4_evidence = None
        if complete_g4:
            assert pr_number is not None
            assert pr_url is not None
            assert merge_commit is not None
            assert version is not None
            assert release_authorized_by is not None
            g4_evidence = G4ApprovalInput(
                pr_number=pr_number,
                pr_url=pr_url,
                merge_commit=merge_commit,
                version=version,
                release_authority=ReleaseAuthority(
                    authorized=release_authorized,
                    scope="tag-and-github-release",
                    authorized_by=release_authorized_by,
                ),
            )
        return _workflow_payload(
            WorkflowEngine(Path(project_root)).submit_approval(
                run_id,
                gate=Gate(gate),
                approved=approved,
                reviewer=reviewer,
                reason=reason,
                g4_evidence=g4_evidence,
            ),
            Path(project_root),
        )

    return _invoke(operation)


@mcp.tool()
def task_complete(
    project_root: str,
    run_id: str,
    task_id: str,
    change_kind: str,
    branch: str | None = None,
    commit_sha: str | None = None,
    remote_name: str | None = None,
    push_status: str = "not_required",
    artifact_paths_and_hashes: dict[str, str] | None = None,
    verification_results: list[str] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    checks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Complete the active task with artifact hashes, verification, and required Git evidence."""

    def operation() -> dict[str, Any]:
        structured_artifacts = tuple(
            ArtifactEvidenceInput.model_validate(item) for item in artifacts or ()
        )
        structured_checks = tuple(
            CheckEvidenceInput.model_validate(item) for item in checks or ()
        )
        compatibility_artifacts = artifact_paths_and_hashes or {
            artifact.path: artifact.sha256 for artifact in structured_artifacts
        }
        completion = TaskCompletion(
            task_id=task_id,
            change_kind=ChangeKind(change_kind),
            branch=branch,
            commit_sha=commit_sha,
            remote_name=remote_name,
            push_status=PushStatus(push_status),
            artifact_paths_and_hashes=compatibility_artifacts,
            verification_results=tuple(verification_results or ()),
            artifacts=structured_artifacts,
            checks=structured_checks,
        )
        result = WorkflowEngine(Path(project_root)).complete_task(run_id, completion)
        return _workflow_payload(result, Path(project_root))

    return _invoke(operation)


@mcp.tool()
def handoff_review(
    project_root: str,
    handoff_id: str,
    reviewer: str,
    reviewed_commit: str,
    decision: str,
    reason: str,
    report_ref: str,
    report_hash: str,
    findings: list[str] | None = None,
    risks: list[str] | None = None,
) -> dict[str, Any]:
    """Accept, reject, or block a ready Handoff and integrate only accepted evidence."""

    def operation() -> dict[str, Any]:
        review = HandoffReviewInput(
            handoff_id=handoff_id,
            reviewer=reviewer,
            reviewed_commit=reviewed_commit,
            decision=ReviewDecision(decision),
            reason=reason,
            findings=tuple(findings or ()),
            risks=tuple(risks or ()),
            report_ref=report_ref,
            report_hash=report_hash,
        )
        result = WorkflowEngine(Path(project_root)).review_handoff(review)
        return _workflow_payload(result, Path(project_root))

    return _invoke(operation)


@mcp.tool()
def worktree_cleanup(
    project_root: str,
    task_id: str,
    merged_into: str,
    requested_by: str,
    approved_by: str,
    reason: str,
) -> dict[str, Any]:
    """Clean only a merged, clean, review-free Worktree with explicit approval."""

    def operation() -> dict[str, Any]:
        WorktreeService(Path(project_root)).cleanup(
            task_id=task_id,
            approved=True,
            merged_into=merged_into,
            requested_by=requested_by,
            approved_by=approved_by,
            reason=reason,
        )
        return _success(task_id=task_id, cleanup_status="completed")

    return _invoke(operation)


@mcp.tool()
def docs_check(project_root: str) -> dict[str, Any]:
    """Check required documents, headings, local links, and forbidden copy directories."""

    def operation() -> dict[str, Any]:
        config = load_project_config(Path(project_root).resolve())
        report = DocumentManager(config.root).check(config.project_type.value)
        return _success(
            valid=report.ok,
            checked_files=report.checked_files,
            missing=list(report.missing),
            broken_links=list(report.broken_links),
            invalid_documents=list(report.invalid_documents),
            forbidden_directories=list(report.forbidden_directories),
            metadata_errors=list(report.metadata_errors),
            placeholder_findings=list(report.placeholder_findings),
            version_mismatches=list(report.version_mismatches),
            stale_documents=list(report.stale_documents),
            impact_findings=list(report.impact_findings),
        )

    return _invoke(operation)


@mcp.tool()
def verification_run(
    project_root: str,
    run_id: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Run quality checks through ExecutionService, or a deprecated read-only health check."""

    def operation() -> dict[str, Any]:
        if (run_id is None) != (task_id is None):
            raise ValueError("run_id and task_id must be supplied together")
        if run_id is not None and task_id is not None:
            result = VerificationService(Path(project_root)).run(
                run_id=run_id, task_id=task_id
            )
            return _success(
                valid=result.valid,
                run_id=result.run_id,
                task_id=result.task_id,
                source_commit=result.source_commit,
                checks=[check.model_dump(mode="json") for check in result.checks],
                blockers=list(result.blockers),
            )
        config = load_project_config(Path(project_root).resolve())
        database = Database(config.root / ".codex-os" / "state" / "state.db")
        database.migrate()
        database.integrity_check()
        report = DocumentManager(config.root).check(config.project_type.value)
        checks = [
            {"name": "sqlite_integrity", "ok": True},
            {"name": "document_governance", "ok": report.ok},
        ]
        return _success(
            valid=all(bool(check["ok"]) for check in checks),
            checks=checks,
            deprecated=True,
            warning="Supply run_id and task_id for governed verification.",
        )

    return _invoke(operation)


@mcp.tool()
def release_candidate_create(project_root: str, run_id: str) -> dict[str, Any]:
    """Build and assemble a candidate in the active Release Worktree and artifact area."""

    def operation() -> dict[str, Any]:
        if load_project_config(Path(project_root)).schema_version == "1.0":
            return _legacy_release_candidate(Path(project_root), run_id)
        candidate = ReleaseCandidateService(Path(project_root)).create(run_id)
        return _success(
            candidate_id=candidate.id,
            version=candidate.version,
            source_commit=candidate.source_commit,
            release_worktree=candidate.release_worktree,
            manifest_path=candidate.manifest_path,
            manifest_hash=candidate.manifest_hash,
            artifact_root=candidate.artifact_root,
            artifacts=candidate.artifacts,
            sbom_path=candidate.sbom_path,
            sbom_hash=candidate.sbom_hash,
            checksums_path=candidate.checksums_path,
            checksums_hash=candidate.checksums_hash,
            rollback_path=candidate.rollback_path,
            rollback_hash=candidate.rollback_hash,
            created=candidate.created,
        )

    return _invoke(operation)


def _legacy_release_candidate(project_root: Path, run_id: str) -> dict[str, Any]:
    root = project_root.resolve()
    result = WorkflowEngine(root).status(run_id)
    if (
        result.run.workflow_phase is not WorkflowPhase.RELEASE
        or result.run.run_status is not RunStatus.RUNNING
        or result.active_task is None
    ):
        raise WorkflowError(
            "RELEASE_NOT_READY",
            "release candidate requires an active release-phase task after G3 approval",
            30,
        )
    database = Database(root / ".codex-os" / "state" / "state.db")
    with database.connection() as connection:
        rows = connection.execute(
            """
            SELECT path, kind, content_hash, source_commit, status
            FROM artifacts WHERE run_id = ? ORDER BY path, content_hash
            """,
            (run_id,),
        ).fetchall()
    manifest = {
        "schema_version": 1,
        "candidate_id": f"RC-{run_id}",
        "project_id": result.run.project_id,
        "run_id": run_id,
        "goal": result.run.goal,
        "config_hash": result.run.config_hash,
        "release_task_id": result.active_task.id,
        "artifacts": [dict(row) for row in rows],
    }
    content = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    relative = "release/manifest.json"
    target = root / relative
    if target.is_file():
        if target.read_text(encoding="utf-8") != content:
            raise WorkflowError(
                "STATE_CONFLICT",
                f"release candidate already exists with different content: {relative}",
                30,
            )
        created = False
    else:
        created = DocumentManager(root).write_atomic(relative, content, overwrite=False)
    return _success(
        candidate_id=manifest["candidate_id"],
        path=relative,
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        created=created,
        deprecated=True,
        warning="Schema 1.0 release candidates use the compatibility path.",
    )


@mcp.tool()
def memory_search(
    project_root: str,
    query: str,
    limit: int = 20,
    record_types: list[str] | None = None,
    statuses: list[str] | None = None,
    tags: list[str] | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    source_ref: str | None = None,
) -> dict[str, Any]:
    """Search active local memory metadata by title, tags, or referenced content path."""

    def operation() -> dict[str, Any]:
        config = load_project_config(Path(project_root).resolve())
        database = Database(config.root / ".codex-os" / "state" / "state.db")
        database.migrate()
        records = MemoryStore(database, config.root, config.project_id).search(
            query,
            record_types=tuple(record_types or ()),
            statuses=tuple(statuses or ("active",)),
            tags=tuple(tags or ()),
            created_from=created_from,
            created_to=created_to,
            source_ref=source_ref,
            limit=limit,
        )
        return _success(
            query=query,
            results=[
                {
                    "id": record.id,
                    "run_id": record.run_id,
                    "task_id": record.task_id,
                    "record_type": record.record_type,
                    "title": record.title,
                    "content_ref": record.content_ref,
                    "source_refs": list(record.source_refs),
                    "source_hashes": record.source_hashes,
                    "confidence": record.confidence,
                    "tags": list(record.tags),
                    "scope": record.scope,
                    "status": record.status,
                    "created_at": record.created_at,
                    "updated_at": record.updated_at,
                    "expires_at": record.expires_at,
                }
                for record in records
            ],
        )

    return _invoke(operation)


@mcp.tool()
def memory_candidate_submit(
    project_root: str,
    record_type: str,
    title: str,
    content_ref: str,
    source_refs: list[str],
    confidence: float,
    tags: list[str] | None = None,
    run_id: str | None = None,
    task_id: str | None = None,
    expires_at: str | None = None,
) -> dict[str, Any]:
    """Submit a source-linked pending Memory candidate after Secret and scope checks."""

    def operation() -> dict[str, Any]:
        config = load_project_config(Path(project_root).resolve())
        database = Database(config.root / ".codex-os" / "state" / "state.db")
        database.migrate()
        record = MemoryStore(database, config.root, config.project_id).create_candidate(
            record_type=record_type,
            title=title,
            content_ref=content_ref,
            source_refs=tuple(source_refs),
            confidence=confidence,
            tags=tuple(tags or ()),
            run_id=run_id,
            task_id=task_id,
            expires_at=expires_at,
        )
        return _success(record=_memory_payload(record))

    return _invoke(operation)


@mcp.tool()
def memory_review(
    project_root: str,
    memory_id: str,
    reviewer: str,
    decision: str,
    reason: str,
) -> dict[str, Any]:
    """Review a Memory candidate or change its governed lifecycle state."""

    def operation() -> dict[str, Any]:
        config = load_project_config(Path(project_root).resolve())
        database = Database(config.root / ".codex-os" / "state" / "state.db")
        database.migrate()
        record = MemoryStore(database, config.root, config.project_id).review(
            memory_id,
            reviewer=reviewer,
            decision=decision,
            reason=reason,
        )
        return _success(record=_memory_payload(record))

    return _invoke(operation)


def run_server() -> None:
    """Run the local server over the default stdio transport."""

    mcp.run()


def _workflow_payload(result: WorkflowResult, project_root: Path) -> dict[str, Any]:
    database = Database(project_root.resolve() / ".codex-os" / "state" / "state.db")
    with database.connection() as connection:
        groups = [
            dict(row)
            for row in connection.execute(
                "SELECT id, name, phase, status, base_commit, state_version "
                "FROM task_groups WHERE run_id = ? ORDER BY created_at, id",
                (result.run.id,),
            ).fetchall()
        ]
        dependencies = [
            dict(row)
            for row in connection.execute(
                """
                SELECT d.task_id, d.depends_on_task_id, d.dependency_type
                FROM task_dependencies d JOIN tasks t ON t.id = d.task_id
                WHERE t.run_id = ? ORDER BY d.task_id, d.depends_on_task_id
                """,
                (result.run.id,),
            ).fetchall()
        ]
        handoffs = [
            dict(row)
            for row in connection.execute(
                "SELECT id, task_id, producer, consumer, status, reviewer, "
                "reviewed_commit, decision_reason, updated_at FROM handoffs "
                "WHERE run_id = ? ORDER BY created_at, id",
                (result.run.id,),
            ).fetchall()
        ]
        merges = [
            dict(row)
            for row in connection.execute(
                "SELECT task_id, source_commit, integration_branch, merge_commit, "
                "status, conflict_paths_json, error_code FROM integration_merges "
                "WHERE run_id = ? ORDER BY created_at, id",
                (result.run.id,),
            ).fetchall()
        ]
    return _success(
        run=result.run.model_dump(mode="json"),
        next_action=(
            result.next_action.model_dump(mode="json") if result.next_action is not None else None
        ),
        next_actions=[action.model_dump(mode="json") for action in result.next_actions],
        active_task=(
            result.active_task.model_dump(mode="json") if result.active_task is not None else None
        ),
        task_groups=groups,
        task_dependencies=dependencies,
        handoffs=handoffs,
        integration_merges=merges,
        integration=(
            {
                "status": result.integration_result.status,
                "branch": result.integration_result.integration_branch,
                "head": result.integration_result.integration_head,
                "merge_commit": result.integration_result.merge_commit,
                "conflict_paths": list(result.integration_result.conflict_paths),
            }
            if result.integration_result is not None
            else None
        ),
    )


def _memory_payload(record: MemoryRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "run_id": record.run_id,
        "task_id": record.task_id,
        "record_type": record.record_type,
        "title": record.title,
        "content_ref": record.content_ref,
        "source_refs": list(record.source_refs),
        "source_hashes": record.source_hashes,
        "confidence": record.confidence,
        "tags": list(record.tags),
        "scope": record.scope,
        "status": record.status,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "expires_at": record.expires_at,
    }


def _success(**data: Any) -> dict[str, Any]:
    return {"ok": True, **data}


def _invoke(operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return operation()
    except WorkflowError as exc:
        return {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
    except MemoryStoreError as exc:
        return {"ok": False, "error": {"code": "MEMORY_INVALID", "message": str(exc)}}
    except (CoordinationError, ExecutionServiceError, WorktreeServiceError) as exc:
        return {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
    except (ConfigError, MigrationError, ValidationError, ValueError, OSError) as exc:
        return {"ok": False, "error": {"code": "CONFIG_INVALID", "message": str(exc)}}


if __name__ == "__main__":
    run_server()
