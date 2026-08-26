"""Typer command surface for AI Engineering OS."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from codex_ai_os.application.doctor import DoctorService
from codex_ai_os.application.execution import ExecutionServiceError
from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.application.repository import RepositoryGovernanceService
from codex_ai_os.application.verification import VerificationService
from codex_ai_os.application.workflow import WorkflowEngine, WorkflowError, WorkflowResult
from codex_ai_os.application.worktree import WorktreeService, WorktreeServiceError
from codex_ai_os.cli.output import emit, error_envelope, success_envelope
from codex_ai_os.domain.config import ProjectType, RiskLevel
from codex_ai_os.domain.coordination import HandoffReviewInput
from codex_ai_os.domain.governance import (
    G4ApprovalInput,
    ReleaseAuthority,
    ReviewDecision,
)
from codex_ai_os.domain.workflow import Gate
from codex_ai_os.infrastructure.config import ConfigError, load_project_config
from codex_ai_os.infrastructure.database import Database, MigrationError
from codex_ai_os.infrastructure.documents import DocumentManager

app = typer.Typer(
    name="codex-os",
    help="Local, auditable engineering workflow runtime for Codex.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
run_app = typer.Typer(help="Start a registered workflow.", no_args_is_help=True)
handoff_app = typer.Typer(help="Review structured Agent Handoffs.", no_args_is_help=True)
worktree_app = typer.Typer(help="Manage governed Worktree lifecycle.", no_args_is_help=True)
app.add_typer(run_app, name="run")
app.add_typer(handoff_app, name="handoff")
app.add_typer(worktree_app, name="worktree")


@app.command("doctor")
def doctor_command(
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON only.")] = False,
) -> None:
    """Check required local runtime dependencies."""

    report = DoctorService().run()
    checks = [
        {
            "name": check.name,
            "required": check.required,
            "ok": check.ok,
            "detail": check.detail,
        }
        for check in report.checks
    ]
    if report.ok:
        payload = success_envelope({"checks": checks})
        emit(payload, json_output=json_output, human="Environment checks passed.")
        return

    payload = error_envelope(
        "SANDBOX_UNAVAILABLE" if not report.sandbox_available else "CONFIG_INVALID",
        "Required environment checks failed.",
        {"checks": checks},
    )
    emit(payload, json_output=json_output, human="Required environment checks failed.")
    raise typer.Exit(code=50 if not report.sandbox_available else 2)


@app.command("init")
def init_command(
    project_root: Annotated[Path, typer.Argument(help="Project directory.")] = Path("."),
    project_id: Annotated[str, typer.Option("--project-id")] = "PROJECT-LOCAL",
    name: Annotated[str, typer.Option("--name")] = "AI Engineering Project",
    project_type: Annotated[ProjectType, typer.Option("--project-type")] = ProjectType.GENERIC,
    risk_level: Annotated[RiskLevel, typer.Option("--risk-level")] = RiskLevel.MEDIUM,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON only.")] = False,
) -> None:
    """Create a project configuration, document skeleton, and runtime database."""

    try:
        result = ProjectInitializer().initialize(
            project_root,
            project_id=project_id,
            name=name,
            project_type=project_type,
            risk_level=risk_level,
        )
    except (ConfigError, MigrationError, ValueError, OSError) as exc:
        _fail("CONFIG_INVALID", str(exc), 2, json_output)
        return

    data: dict[str, Any] = {
        "project_id": result.config.project_id,
        "root": result.config.root.as_posix(),
        "created_paths": list(result.created_paths),
        "database": result.database_path.as_posix(),
        "context": result.context_path.as_posix(),
        "documents_ok": result.document_report.ok,
        "repository_ready": result.repository_ready,
        "repository_blockers": list(result.repository_blockers),
    }
    emit(
        success_envelope(data),
        json_output=json_output,
        human=f"Initialized {result.config.project_id} at {result.config.root}",
    )


@app.command("repo-check")
def repository_check_command(
    project_root: Annotated[Path, typer.Argument(help="Project directory.")] = Path("."),
    target_branch: Annotated[str | None, typer.Option("--target-branch")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Check GitHub readiness, repository hygiene, and blocking findings."""

    try:
        report = RepositoryGovernanceService(project_root).check(
            target_branch=target_branch
        )
    except (ConfigError, MigrationError, ValueError, OSError) as exc:
        _fail("CONFIG_INVALID", str(exc), 2, json_output)
        return
    data = report.model_dump(mode="json")
    if report.repository_ready:
        emit(
            success_envelope(data),
            json_output=json_output,
            human="Repository governance checks passed.",
        )
        return
    emit(
        error_envelope(
            report.findings[0].code,
            "Repository governance checks failed.",
            data,
        ),
        json_output=json_output,
        human="Repository governance checks failed.",
    )
    raise typer.Exit(code=40)


@app.command("status")
def status_command(
    project_root: Annotated[Path, typer.Argument(help="Project directory.")] = Path("."),
    run_id: Annotated[str | None, typer.Option("--run-id")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON only.")] = False,
) -> None:
    """Show project runtime and document status."""

    if run_id is not None:
        try:
            result = WorkflowEngine(project_root, readonly=True).status(run_id)
        except WorkflowError as exc:
            _workflow_fail(exc, json_output)
            return
        except (ConfigError, MigrationError, ValueError, OSError) as exc:
            _fail("CONFIG_INVALID", str(exc), 2, json_output)
            return
        _emit_workflow(result, json_output=json_output)
        return

    try:
        config = load_project_config(project_root.resolve())
        database = Database(config.root / ".codex-os" / "state" / "state.db")
        version = database.current_version()
        report = DocumentManager(config.root).check(config.project_type.value)
        with database.read_connection() as connection:
            event_count = int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            workflow_count = int(
                connection.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0]
            )
    except (ConfigError, MigrationError, ValueError, OSError) as exc:
        _fail("CONFIG_INVALID", str(exc), 2, json_output)
        return

    data = {
        "project_id": config.project_id,
        "root": config.root.as_posix(),
        "schema_version": version,
        "events": event_count,
        "workflows": workflow_count,
        "documents_ok": report.ok,
    }
    emit(
        success_envelope(data),
        json_output=json_output,
        human=(
            f"{config.project_id}: schema={version}, workflows={workflow_count}, "
            f"documents={'ok' if report.ok else 'invalid'}"
        ),
    )


@app.command("check-docs")
def check_docs_command(
    project_root: Annotated[Path, typer.Argument(help="Project directory.")] = Path("."),
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON only.")] = False,
) -> None:
    """Check required documents, headings, local links, and copy directories."""

    try:
        config = load_project_config(project_root.resolve())
        report = DocumentManager(config.root).check(config.project_type.value)
    except (ConfigError, ValueError, OSError) as exc:
        _fail("CONFIG_INVALID", str(exc), 2, json_output)
        return

    data = {
        "checked_files": report.checked_files,
        "missing": list(report.missing),
        "broken_links": list(report.broken_links),
        "invalid_documents": list(report.invalid_documents),
        "forbidden_directories": list(report.forbidden_directories),
        "metadata_errors": list(report.metadata_errors),
        "placeholder_findings": list(report.placeholder_findings),
        "version_mismatches": list(report.version_mismatches),
        "stale_documents": list(report.stale_documents),
        "impact_findings": list(report.impact_findings),
    }
    if report.ok:
        emit(
            success_envelope(data),
            json_output=json_output,
            human=f"Document checks passed ({report.checked_files} files).",
        )
        return

    emit(
        error_envelope("DOCS_INCOMPLETE", "Document governance checks failed.", data),
        json_output=json_output,
        human="Document governance checks failed.",
    )
    raise typer.Exit(code=10)


@app.command("mcp")
def mcp_command() -> None:
    """Run the bundled Model Context Protocol server over stdio."""

    from codex_ai_os.cli.mcp_server import run_server

    run_server()


@run_app.command("new-project")
def run_new_project_command(
    goal: Annotated[str, typer.Option("--goal", help="Business goal for the project.")],
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON only.")] = False,
    profile: Annotated[list[str] | None, typer.Option("--profile")] = None,
    target_branch: Annotated[str | None, typer.Option("--target-branch")] = None,
) -> None:
    """Start or return the active idempotent new-project run for a goal."""

    try:
        result = WorkflowEngine(project_root).start(
            goal,
            workflow_name="new-project",
            profiles=tuple(profile or ()),
            target_branch=target_branch,
        )
    except WorkflowError as exc:
        _workflow_fail(exc, json_output)
        return
    except (ConfigError, MigrationError, ValueError, OSError) as exc:
        _fail("CONFIG_INVALID", str(exc), 2, json_output)
        return
    _emit_workflow(result, json_output=json_output)


def _run_named_workflow(
    workflow_name: str,
    goal: str,
    project_root: Path,
    json_output: bool,
    profiles: tuple[str, ...] = (),
    target_branch: str | None = None,
) -> None:
    try:
        result = WorkflowEngine(project_root).start(
            goal,
            workflow_name=workflow_name,
            profiles=profiles,
            target_branch=target_branch,
        )
    except WorkflowError as exc:
        _workflow_fail(exc, json_output)
        return
    except (ConfigError, MigrationError, ValueError, OSError) as exc:
        _fail("CONFIG_INVALID", str(exc), 2, json_output)
        return
    _emit_workflow(result, json_output=json_output)


@run_app.command("feature-development")
def run_feature_development_command(
    goal: Annotated[str, typer.Option("--goal")],
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json_output: Annotated[bool, typer.Option("--json")] = False,
    profile: Annotated[list[str] | None, typer.Option("--profile")] = None,
    target_branch: Annotated[str | None, typer.Option("--target-branch")] = None,
) -> None:
    """Start a feature-development workflow at detailed requirements."""

    _run_named_workflow(
        "feature-development",
        goal,
        project_root,
        json_output,
        tuple(profile or ()),
        target_branch,
    )


@run_app.command("bug-fix")
def run_bug_fix_command(
    goal: Annotated[str, typer.Option("--goal")],
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json_output: Annotated[bool, typer.Option("--json")] = False,
    profile: Annotated[list[str] | None, typer.Option("--profile")] = None,
    target_branch: Annotated[str | None, typer.Option("--target-branch")] = None,
) -> None:
    """Start a bug-fix workflow at isolated implementation."""

    _run_named_workflow(
        "bug-fix", goal, project_root, json_output, tuple(profile or ()), target_branch
    )


@run_app.command("release")
def run_release_workflow_command(
    goal: Annotated[str, typer.Option("--goal")],
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Start a release workflow at verification."""

    _run_named_workflow("release", goal, project_root, json_output)


@app.command("step")
def step_command(
    run_id: Annotated[str, typer.Argument(help="Workflow run ID.")],
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON only.")] = False,
) -> None:
    """Return the current deterministic next action without executing it."""

    try:
        result = WorkflowEngine(project_root, readonly=True).status(run_id)
    except WorkflowError as exc:
        _workflow_fail(exc, json_output)
        return
    except (ConfigError, MigrationError, ValueError, OSError) as exc:
        _fail("CONFIG_INVALID", str(exc), 2, json_output)
        return
    _emit_workflow(result, json_output=json_output)


@app.command("approve")
def approve_command(
    run_id: Annotated[str, typer.Argument(help="Workflow run ID.")],
    gate: Annotated[Gate, typer.Option("--gate")],
    reason: Annotated[str, typer.Option("--reason")],
    reviewer: Annotated[str, typer.Option("--reviewer")] = "user",
    pr_number: Annotated[int | None, typer.Option("--pr-number")] = None,
    pr_url: Annotated[str | None, typer.Option("--pr-url")] = None,
    merge_commit: Annotated[str | None, typer.Option("--merge-commit")] = None,
    version: Annotated[str | None, typer.Option("--version")] = None,
    release_authorized: Annotated[bool, typer.Option("--release-authorized")] = False,
    release_authorized_by: Annotated[
        str | None, typer.Option("--release-authorized-by")
    ] = None,
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON only.")] = False,
) -> None:
    """Approve the exact gate currently requested by a workflow."""

    _approval_command(
        run_id,
        gate=gate,
        approved=True,
        reviewer=reviewer,
        reason=reason,
        project_root=project_root,
        json_output=json_output,
        pr_number=pr_number,
        pr_url=pr_url,
        merge_commit=merge_commit,
        version=version,
        release_authorized=release_authorized,
        release_authorized_by=release_authorized_by,
    )


@app.command("reject")
def reject_command(
    run_id: Annotated[str, typer.Argument(help="Workflow run ID.")],
    gate: Annotated[Gate, typer.Option("--gate")],
    reason: Annotated[str, typer.Option("--reason")],
    reviewer: Annotated[str, typer.Option("--reviewer")] = "user",
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON only.")] = False,
) -> None:
    """Reject the current gate and block the workflow with a recorded reason."""

    _approval_command(
        run_id,
        gate=gate,
        approved=False,
        reviewer=reviewer,
        reason=reason,
        project_root=project_root,
        json_output=json_output,
    )


@app.command("resume")
def resume_command(
    run_id: Annotated[str, typer.Argument(help="Workflow run ID.")],
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON only.")] = False,
) -> None:
    """Resume a paused, blocked, or failed workflow from its checkpoint."""

    try:
        result = WorkflowEngine(project_root).resume(run_id)
    except WorkflowError as exc:
        _workflow_fail(exc, json_output)
        return
    except (ConfigError, MigrationError, ValueError, OSError) as exc:
        _fail("CONFIG_INVALID", str(exc), 2, json_output)
        return
    _emit_workflow(result, json_output=json_output)


@app.command("verify")
def verification_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    task_id: Annotated[str, typer.Option("--task-id")],
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run the complete governed quality set in the configured OCI sandbox."""

    try:
        result = VerificationService(project_root).run(run_id=run_id, task_id=task_id)
    except (ConfigError, ExecutionServiceError, MigrationError, ValueError, OSError) as exc:
        _fail(getattr(exc, "code", "CONFIG_INVALID"), str(exc), 50, json_output)
        return
    data = {
        "run_id": result.run_id,
        "task_id": result.task_id,
        "source_commit": result.source_commit,
        "valid": result.valid,
        "checks": [check.model_dump(mode="json") for check in result.checks],
        "blockers": list(result.blockers),
    }
    if result.valid:
        emit(success_envelope(data), json_output=json_output, human="Verification passed.")
        return
    emit(
        error_envelope("EXECUTION_FAILED", "Verification failed.", data),
        json_output=json_output,
        human="Verification failed.",
    )
    raise typer.Exit(code=50)


@handoff_app.command("review")
def handoff_review_command(
    handoff_id: Annotated[str, typer.Argument()],
    reviewer: Annotated[str, typer.Option("--reviewer")],
    reviewed_commit: Annotated[str, typer.Option("--reviewed-commit")],
    decision: Annotated[str, typer.Option("--decision")],
    reason: Annotated[str, typer.Option("--reason")],
    report_ref: Annotated[str, typer.Option("--report-ref")],
    report_hash: Annotated[str, typer.Option("--report-hash")],
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Review a ready Handoff and integrate it only when accepted."""

    try:
        review = HandoffReviewInput(
            handoff_id=handoff_id,
            reviewer=reviewer,
            reviewed_commit=reviewed_commit,
            decision=ReviewDecision(decision),
            reason=reason,
            report_ref=report_ref,
            report_hash=report_hash,
        )
        result = WorkflowEngine(project_root).review_handoff(review)
    except (WorkflowError, ConfigError, MigrationError, ValueError, OSError) as exc:
        _fail(getattr(exc, "code", "CONFIG_INVALID"), str(exc), 40, json_output)
        return
    _emit_workflow(result, json_output=json_output)


@worktree_app.command("cleanup")
def worktree_cleanup_command(
    task_id: Annotated[str, typer.Argument()],
    merged_into: Annotated[str, typer.Option("--merged-into")],
    reason: Annotated[str, typer.Option("--reason")],
    approved_by: Annotated[str, typer.Option("--approved-by")],
    requested_by: Annotated[str, typer.Option("--requested-by")] = "codex-host",
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Clean an accepted, merged, clean Worktree with explicit approval."""

    try:
        WorktreeService(project_root).cleanup(
            task_id=task_id,
            approved=True,
            merged_into=merged_into,
            requested_by=requested_by,
            approved_by=approved_by,
            reason=reason,
        )
    except (WorktreeServiceError, ConfigError, MigrationError, ValueError, OSError) as exc:
        _fail(getattr(exc, "code", "WORKTREE_BLOCKED"), str(exc), 40, json_output)
        return
    emit(
        success_envelope({"task_id": task_id, "cleanup_status": "completed"}),
        json_output=json_output,
        human="Worktree cleanup completed.",
    )


def _approval_command(
    run_id: str,
    *,
    gate: Gate,
    approved: bool,
    reviewer: str,
    reason: str,
    project_root: Path,
    json_output: bool,
    pr_number: int | None = None,
    pr_url: str | None = None,
    merge_commit: str | None = None,
    version: str | None = None,
    release_authorized: bool = False,
    release_authorized_by: str | None = None,
) -> None:
    try:
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
        result = WorkflowEngine(project_root).submit_approval(
            run_id,
            gate=gate,
            approved=approved,
            reviewer=reviewer,
            reason=reason,
            g4_evidence=g4_evidence,
        )
    except WorkflowError as exc:
        _workflow_fail(exc, json_output)
        return
    except (ConfigError, MigrationError, ValueError, OSError) as exc:
        _fail("CONFIG_INVALID", str(exc), 2, json_output)
        return
    _emit_workflow(result, json_output=json_output)


def _emit_workflow(result: WorkflowResult, *, json_output: bool) -> None:
    action = (
        result.next_action.model_dump(mode="json") if result.next_action is not None else None
    )
    data: dict[str, Any] = {
        "project_id": result.run.project_id,
        "workflow_name": result.run.workflow_name,
        "goal": result.run.goal,
        "active_task": (
            result.active_task.model_dump(mode="json")
            if result.active_task is not None
            else None
        ),
        "next_actions": [
            next_action.model_dump(mode="json") for next_action in result.next_actions
        ],
        "integration": (
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
    }
    emit(
        success_envelope(
            data,
            run_id=result.run.id,
            run_status=result.run.run_status.value,
            workflow_phase=result.run.workflow_phase.value,
            state_version=result.run.state_version,
            next_actions=[
                next_action.model_dump(mode="json")
                for next_action in result.next_actions
            ]
            or ([action] if action is not None else []),
        ),
        json_output=json_output,
        human=(
            f"{result.run.id}: phase={result.run.workflow_phase.value}, "
            f"status={result.run.run_status.value}"
        ),
    )


def _workflow_fail(error: WorkflowError, json_output: bool) -> None:
    _fail(error.code, str(error), error.exit_code, json_output)


def _fail(code: str, message: str, exit_code: int, json_output: bool) -> None:
    emit(
        error_envelope(code, message, {}),
        json_output=json_output,
        human=f"{code}: {message}",
    )
    raise typer.Exit(code=exit_code)
