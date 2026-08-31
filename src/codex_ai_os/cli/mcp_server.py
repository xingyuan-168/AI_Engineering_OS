"""stdio MCP server exposing the deterministic AI Engineering OS runtime."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from contextvars import ContextVar
from pathlib import Path
from typing import Any, cast

from mcp.server import MCPServer
from pydantic import ValidationError

from codex_ai_os.application.execution import ExecutionServiceError
from codex_ai_os.application.maintenance import (
    DatabaseMigrationService,
    HostOperationMaintenanceService,
    MaintenanceOperationError,
    VerificationPrepareService,
)
from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.application.prototype import PrototypeReviewService
from codex_ai_os.application.release import ReleaseCandidateService
from codex_ai_os.application.repository import RepositoryGovernanceService
from codex_ai_os.application.responses import error_envelope, success_envelope
from codex_ai_os.application.verification import VerificationService
from codex_ai_os.application.verification_cache import (
    DEFAULT_VERIFICATION_PLATFORM,
    DEFAULT_VERIFICATION_PYTHON,
)
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
    ReviewFinding,
)
from codex_ai_os.domain.invocation import InvocationContext, InvocationSource
from codex_ai_os.domain.operations import ReconciliationOutcome
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
from codex_ai_os.infrastructure.path_codec import configure_utf8_stdio

mcp = MCPServer(
    "AI Engineering OS",
    version=RUNTIME_VERSIONS.software,
    instructions=(
        "Use these tools to run deterministic engineering workflows. Execute the returned "
        "next_action in Codex, then report repository evidence through task_complete."
    ),
)

_INVOCATION_CONTEXT: ContextVar[InvocationContext | None] = ContextVar(
    "codex_ai_os_invocation_context", default=None
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
    impact_paths: list[str] | None = None,
    dependency_count: int = 0,
    release_required: bool = False,
    override_reason: str | None = None,
) -> dict[str, Any]:
    """Start, or idempotently return, a supported governed workflow for a goal."""

    return _invoke(
        lambda: _workflow_payload(
            WorkflowEngine(Path(project_root)).start(
                goal,
                workflow_name=workflow_name,
                profiles=tuple(profiles or ()),
                target_branch=target_branch,
                impact_paths=tuple(impact_paths or ()),
                dependency_count=dependency_count,
                release_required=release_required,
                override_reason=override_reason,
            ),
            Path(project_root),
            warnings=(
                "workflow_start is deprecated in API 1.2; use workflow_create then workflow_begin",
            ),
        )
    )


@mcp.tool()
def workflow_create(
    project_root: str,
    goal: str,
    idempotency_key: str,
    workflow_name: str = "new-project",
    profiles: list[str] | None = None,
    target_branch: str | None = None,
    impact_paths: list[str] | None = None,
    dependency_count: int = 0,
    release_required: bool = False,
    override_reason: str | None = None,
    document_version_target: str | None = None,
) -> dict[str, Any]:
    """Create a routed workflow without allocating a task or Worktree."""

    return _invoke(
        lambda: _workflow_payload(
            WorkflowEngine(Path(project_root)).create(
                goal,
                workflow_name=workflow_name,
                profiles=tuple(profiles or ()),
                target_branch=target_branch,
                impact_paths=tuple(impact_paths or ()),
                dependency_count=dependency_count,
                release_required=release_required,
                override_reason=override_reason,
                document_version_target=document_version_target,
                idempotency_key=idempotency_key,
            ),
            Path(project_root),
        )
    )


@mcp.tool()
def workflow_begin(
    project_root: str,
    run_id: str,
    expected_state_version: int,
    idempotency_key: str,
) -> dict[str, Any]:
    """Begin a created workflow and allocate its first task."""

    return _invoke(
        lambda: _workflow_payload(
            WorkflowEngine(Path(project_root)).begin(
                run_id,
                expected_state_version=expected_state_version,
                idempotency_key=idempotency_key,
            ),
            Path(project_root),
        )
    )


@mcp.tool()
def workflow_cancel(
    project_root: str,
    run_id: str,
    reason: str,
    expected_state_version: int,
    idempotency_key: str,
) -> dict[str, Any]:
    """Cancel a workflow without hiding running or published host side effects."""

    def operation() -> dict[str, Any]:
        context = _current_context()
        return _workflow_payload(
            WorkflowEngine(Path(project_root)).cancel(
                run_id,
                reason=reason,
                expected_state_version=expected_state_version,
                idempotency_key=idempotency_key,
                requested_by=context.principal,
            ),
            Path(project_root),
        )

    return _invoke(operation)


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
def gate_preflight(
    project_root: str,
    run_id: str,
    gate: str,
    expected_state_version: int | None = None,
) -> dict[str, Any]:
    """Read-only validation of the exact rules used by Gate approval."""

    return _invoke(
        lambda: _success(
            **cast(
                dict[str, Any],
                WorkflowEngine(Path(project_root), readonly=True).gate_preflight(
                    run_id,
                    gate=Gate(gate),
                    expected_state_version=expected_state_version,
                ),
            )
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
    expected_state_version: int | None = None,
    idempotency_key: str | None = None,
    evidence_bundle_hash: str | None = None,
    pr_number: int | None = None,
    pr_url: str | None = None,
    merge_commit: str | None = None,
    version: str | None = None,
    release_authorized: bool = False,
    release_authorized_by: str | None = None,
) -> dict[str, Any]:
    """Approve or reject exactly the gate currently requested by a workflow."""

    def operation() -> dict[str, Any]:
        context = _current_context()
        warnings = _trusted_reviewer_warnings(reviewer, context)
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
                expected_state_version=expected_state_version,
                idempotency_key=idempotency_key,
                evidence_bundle_hash=evidence_bundle_hash,
                g4_evidence=g4_evidence,
                invocation=context,
            ),
            Path(project_root),
            warnings=warnings,
        )

    return _invoke(operation)


@mcp.tool()
def task_complete(
    project_root: str,
    run_id: str,
    task_id: str,
    change_kind: str,
    expected_task_version: int | None = None,
    idempotency_key: str | None = None,
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
        config = load_project_config(Path(project_root).resolve())
        warnings: tuple[str, ...] = ()
        if config.schema_version == RUNTIME_VERSIONS.api:
            if expected_task_version is None or not (idempotency_key or "").strip():
                raise WorkflowError(
                    "CONFIG_INVALID",
                    "API 1.2 task_complete requires expected_task_version and idempotency_key",
                    2,
                )
        elif expected_task_version is None or not (idempotency_key or "").strip():
            warnings = (
                "Legacy task_complete without expected_task_version/idempotency_key is "
                "deprecated and will be removed in 0.3.0.",
            )
        structured_artifacts = tuple(
            ArtifactEvidenceInput.model_validate(item) for item in artifacts or ()
        )
        structured_checks = tuple(CheckEvidenceInput.model_validate(item) for item in checks or ())
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
        result = WorkflowEngine(Path(project_root)).complete_task(
            run_id,
            completion,
            expected_task_version=expected_task_version,
            idempotency_key=idempotency_key,
        )
        return _workflow_payload(result, Path(project_root), warnings=warnings)

    return _invoke(operation)


@mcp.tool()
def task_amend_evidence(
    project_root: str,
    run_id: str,
    task_id: str,
    expected_task_version: int,
    expected_state_version: int,
    idempotency_key: str,
    branch: str,
    commit_sha: str,
    push_status: str,
    remote_name: str | None = None,
    artifact_paths_and_hashes: dict[str, str] | None = None,
    verification_results: list[str] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    checks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Append corrected commit-bound task evidence before the pending Gate is approved."""

    def operation() -> dict[str, Any]:
        structured_artifacts = tuple(
            ArtifactEvidenceInput.model_validate(item) for item in artifacts or ()
        )
        structured_checks = tuple(CheckEvidenceInput.model_validate(item) for item in checks or ())
        completion = TaskCompletion(
            task_id=task_id,
            change_kind=ChangeKind.REPOSITORY,
            branch=branch,
            commit_sha=commit_sha,
            remote_name=remote_name,
            push_status=PushStatus(push_status),
            artifact_paths_and_hashes=artifact_paths_and_hashes
            or {artifact.path: artifact.sha256 for artifact in structured_artifacts},
            verification_results=tuple(verification_results or ()),
            artifacts=structured_artifacts,
            checks=structured_checks,
        )
        return _workflow_payload(
            WorkflowEngine(Path(project_root)).amend_task_evidence(
                run_id,
                completion,
                expected_task_version=expected_task_version,
                expected_state_version=expected_state_version,
                idempotency_key=idempotency_key,
            ),
            Path(project_root),
        )

    return _invoke(operation)


@mcp.tool()
def prototype_review_submit(
    project_root: str,
    run_id: str,
    task_id: str,
    expected_task_version: int,
    expected_state_version: int,
    idempotency_key: str,
    prototype_path: str,
    prototype_hash: str,
    reviewed_commit: str,
    decision: str,
    reviewer: str,
    reason: str,
    findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate and independently confirm the current offline HTML prototype."""

    def operation() -> dict[str, Any]:
        context = _current_context()
        warnings = _trusted_reviewer_warnings(reviewer, context)
        PrototypeReviewService(Path(project_root)).submit(
            run_id=run_id,
            task_id=task_id,
            expected_task_version=expected_task_version,
            expected_state_version=expected_state_version,
            idempotency_key=idempotency_key,
            prototype_path=prototype_path,
            prototype_hash=prototype_hash,
            reviewed_commit=reviewed_commit,
            decision=ReviewDecision(decision),
            reviewer=reviewer,
            reason=reason,
            findings=tuple(ReviewFinding.model_validate(item) for item in findings or ()),
            invocation=context,
        )
        return _workflow_payload(
            WorkflowEngine(Path(project_root), readonly=True).status(run_id),
            Path(project_root),
            warnings=warnings,
        )

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
    expected_handoff_version: int | None = None,
    idempotency_key: str | None = None,
    findings: list[dict[str, Any]] | None = None,
    risks: list[str] | None = None,
) -> dict[str, Any]:
    """Accept, reject, or block a ready Handoff and integrate only accepted evidence."""

    def operation() -> dict[str, Any]:
        context = _current_context()
        warnings = _trusted_reviewer_warnings(reviewer, context)
        review = HandoffReviewInput(
            handoff_id=handoff_id,
            expected_handoff_version=expected_handoff_version,
            idempotency_key=idempotency_key,
            reviewer=context.principal,
            reviewed_commit=reviewed_commit,
            decision=ReviewDecision(decision),
            reason=reason,
            findings=tuple(ReviewFinding.model_validate(item) for item in findings or ()),
            risks=tuple(risks or ()),
            report_ref=report_ref,
            report_hash=report_hash,
        )
        result = WorkflowEngine(Path(project_root)).review_handoff(review)
        return _workflow_payload(result, Path(project_root), warnings=warnings)

    return _invoke(operation)


@mcp.tool()
def host_operation_execute(
    project_root: str,
    operation_id: str,
    expected_operation_version: int,
    idempotency_key: str,
) -> dict[str, Any]:
    """Execute or safely replay a persisted host-side operation by idempotency key."""

    def operation() -> dict[str, Any]:
        context = _current_context()
        result = WorkflowEngine(Path(project_root)).execute_host_operation(
            operation_id,
            expected_operation_version=expected_operation_version,
            idempotency_key=idempotency_key,
            invocation=context,
        )
        return _workflow_payload(result, Path(project_root))

    return _invoke(operation)


@mcp.tool()
def host_operation_reconcile(
    project_root: str,
    operation_id: str,
    expected_operation_version: int,
    idempotency_key: str,
    outcome: str,
    error_code: str | None = None,
) -> dict[str, Any]:
    """Reconcile an unknown host-operation outcome before retrying side effects."""

    def operation() -> dict[str, Any]:
        reconciled = HostOperationMaintenanceService(Path(project_root)).reconcile(
            operation_id=operation_id,
            expected_operation_version=expected_operation_version,
            idempotency_key=idempotency_key,
            outcome=ReconciliationOutcome(outcome),
            error_code=error_code,
        )
        action = (
            reconciled.next_action.model_dump(mode="json")
            if reconciled.next_action is not None
            else None
        )
        return _success(
            run_id=reconciled.operation.run_id,
            next_actions=[action] if action is not None else [],
            operation=reconciled.operation.model_dump(mode="json"),
        )

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
def docs_check(
    project_root: str,
    run_id: str | None = None,
    gate: str | None = None,
    expected_state_version: int | None = None,
) -> dict[str, Any]:
    """Check required documents, headings, local links, and forbidden copy directories."""

    def operation() -> dict[str, Any]:
        if gate is not None or run_id is not None:
            if not gate or not run_id:
                raise WorkflowError(
                    "CONFIG_INVALID", "docs_check Gate mode requires run_id and gate", 2
                )
            return _success(
                **cast(
                    dict[str, Any],
                    WorkflowEngine(Path(project_root), readonly=True).gate_preflight(
                        run_id,
                        gate=Gate(gate),
                        expected_state_version=expected_state_version,
                    ),
                )
            )
        config = load_project_config(Path(project_root).resolve())
        report = DocumentManager(config.root).check(
            config.project_type.value,
            expected_document_version=config.document_version,
        )
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
            traceability_errors=list(report.traceability_errors),
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
            result = VerificationService(Path(project_root)).run(run_id=run_id, task_id=task_id)
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
        report = DocumentManager(config.root).check(
            config.project_type.value,
            expected_document_version=config.document_version,
        )
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
def verification_prepare(
    project_root: str,
    run_id: str,
    expected_state_version: int,
    idempotency_key: str,
    network_approval_ref: str,
    expires_at: str,
    target_python: str = DEFAULT_VERIFICATION_PYTHON,
    platform: str = DEFAULT_VERIFICATION_PLATFORM,
) -> dict[str, Any]:
    """Persist an approved network operation to prepare offline verification caches."""

    def operation() -> dict[str, Any]:
        scheduled = VerificationPrepareService(Path(project_root)).prepare(
            run_id=run_id,
            expected_state_version=expected_state_version,
            idempotency_key=idempotency_key,
            network_approval_ref=network_approval_ref,
            expires_at=expires_at,
            target_python=target_python,
            platform=platform,
        )
        action = scheduled.next_action.model_dump(mode="json")
        return _success(
            run_id=scheduled.operation.run_id,
            state_version=scheduled.operation.expected_state_version,
            next_actions=[action],
            operation=scheduled.operation.model_dump(mode="json"),
        )

    return _invoke(operation)


@mcp.tool()
def database_migrate(
    project_root: str,
    expected_schema_version: str,
    target_schema_version: str = "0007",
    idempotency_key: str = "database-migrate",
) -> dict[str, Any]:
    """Run explicit SQLite migration and record the operation audit entry."""

    def operation() -> dict[str, Any]:
        result = DatabaseMigrationService(Path(project_root)).migrate(
            expected_schema_version=expected_schema_version,
            target_schema_version=target_schema_version,
            idempotency_key=idempotency_key,
            invocation=_current_context(),
        )
        return _success(
            applied_versions=list(result.migration.applied_versions),
            current_version=result.migration.current_version,
            backup_path=(
                result.migration.backup_path.as_posix()
                if result.migration.backup_path is not None
                else None
            ),
            operation=result.operation.model_dump(mode="json"),
        )

    return _invoke(operation)


@mcp.tool()
def release_candidate_create(
    project_root: str, run_id: str, expected_task_version: int | None = None
) -> dict[str, Any]:
    """Build and assemble a candidate in the active Release Worktree and artifact area."""

    def operation() -> dict[str, Any]:
        if load_project_config(Path(project_root)).schema_version == "1.0":
            return _legacy_release_candidate(Path(project_root), run_id)
        candidate = ReleaseCandidateService(Path(project_root)).create(
            run_id, expected_task_version=expected_task_version
        )
        return _success(
            candidate_id=candidate.id,
            version=candidate.version,
            source_commit=candidate.source_commit,
            integration_source_commit=candidate.source_commit,
            candidate_commit=candidate.candidate_commit,
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
    expected_version: int | None = None,
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
            expected_version=expected_version,
        )
        return _success(record=_memory_payload(record))

    return _invoke(operation)


def run_server() -> None:
    """Run the local server over the default stdio transport."""

    configure_utf8_stdio()
    mcp.run()


def _workflow_payload(
    result: WorkflowResult, project_root: Path, *, warnings: tuple[str, ...] = ()
) -> dict[str, Any]:
    database = Database(project_root.resolve() / ".codex-os" / "state" / "state.db")
    with database.read_connection() as connection:
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
    actions = [action.model_dump(mode="json") for action in result.next_actions]
    if not actions and result.next_action is not None:
        actions = [result.next_action.model_dump(mode="json")]
    return _success(
        run_id=result.run.id,
        run_status=result.run.run_status.value,
        workflow_phase=result.run.workflow_phase.value,
        state_version=result.run.state_version,
        next_actions=actions,
        run=result.run.model_dump(mode="json"),
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
        cancellation=result.run.checkpoint.get("cancel_request"),
        warnings=warnings,
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
        "state_version": record.state_version,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "expires_at": record.expires_at,
    }


def _success(
    *,
    run_id: str | None = None,
    run_status: str | None = None,
    workflow_phase: str | None = None,
    state_version: int | None = None,
    next_actions: list[dict[str, Any]] | None = None,
    warnings: Iterable[str] | None = None,
    **data: Any,
) -> dict[str, Any]:
    context = _INVOCATION_CONTEXT.get() or InvocationContext.local(InvocationSource.MCP)
    compatibility_warnings = list(warnings or ())
    warning = data.pop("warning", None)
    if isinstance(warning, str):
        compatibility_warnings.append(warning)
    return success_envelope(
        data,
        context=context,
        run_id=run_id,
        run_status=run_status,
        workflow_phase=workflow_phase,
        state_version=state_version,
        next_actions=next_actions or (),
        warnings=compatibility_warnings,
    )


def _current_context() -> InvocationContext:
    return _INVOCATION_CONTEXT.get() or InvocationContext.local(InvocationSource.MCP)


def _trusted_reviewer_warnings(
    requested_reviewer: str, context: InvocationContext
) -> tuple[str, ...]:
    if requested_reviewer.strip() == context.principal:
        return ()
    return (
        "reviewer is display-only compatibility input; trusted local principal "
        f"{context.principal} was used for authority",
    )


def _invoke(operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    context = InvocationContext.local(InvocationSource.MCP)
    token = _INVOCATION_CONTEXT.set(context)
    try:
        return operation()
    except WorkflowError as exc:
        return error_envelope(
            exc.code,
            str(exc),
            exc.details,
            context=context,
            retryable=exc.retryable or _retryable_error(exc.code),
        )
    except MemoryStoreError as exc:
        return error_envelope("MEMORY_INVALID", str(exc), context=context)
    except (CoordinationError, ExecutionServiceError, WorktreeServiceError) as exc:
        return error_envelope(
            exc.code,
            str(exc),
            context=context,
            retryable=_retryable_error(exc.code),
        )
    except MaintenanceOperationError as exc:
        return error_envelope(
            exc.code,
            str(exc),
            context=context,
            retryable=exc.retryable,
        )
    except (ConfigError, MigrationError, ValidationError, ValueError, OSError) as exc:
        return error_envelope("CONFIG_INVALID", str(exc), context=context)
    finally:
        _INVOCATION_CONTEXT.reset(token)


def _retryable_error(code: str) -> bool:
    return code in {
        "STATE_VERSION_CONFLICT",
        "OPERATION_LEASED",
        "REMOTE_UNREACHABLE",
        "PUSH_FAILED",
        "RECONCILIATION_REQUIRED",
    }


if __name__ == "__main__":
    run_server()
