"""stdio MCP server exposing the deterministic AI Engineering OS runtime."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from pydantic import ValidationError

from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.application.workflow import WorkflowEngine, WorkflowError, WorkflowResult
from codex_ai_os.domain.config import ProjectType, RiskLevel
from codex_ai_os.domain.workflow import (
    ChangeKind,
    Gate,
    PushStatus,
    RunStatus,
    TaskCompletion,
    WorkflowPhase,
)
from codex_ai_os.infrastructure.config import ConfigError, load_project_config
from codex_ai_os.infrastructure.database import Database, MigrationError
from codex_ai_os.infrastructure.documents import DocumentManager

mcp = MCPServer(
    "AI Engineering OS",
    version="0.1.0",
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
        )

    return _invoke(operation)


@mcp.tool()
def workflow_start(project_root: str, goal: str) -> dict[str, Any]:
    """Start, or idempotently return, the active new-project workflow for a goal."""

    return _invoke(lambda: _workflow_payload(WorkflowEngine(Path(project_root)).start(goal)))


@mcp.tool()
def workflow_status(project_root: str, run_id: str) -> dict[str, Any]:
    """Return the persisted dual-axis workflow state and current host action."""

    return _invoke(lambda: _workflow_payload(WorkflowEngine(Path(project_root)).status(run_id)))


@mcp.tool()
def workflow_step(project_root: str, run_id: str) -> dict[str, Any]:
    """Return the deterministic next action without executing or advancing it."""

    return _invoke(lambda: _workflow_payload(WorkflowEngine(Path(project_root)).status(run_id)))


@mcp.tool()
def workflow_resume(project_root: str, run_id: str) -> dict[str, Any]:
    """Resume a paused, blocked, or failed workflow from its persisted checkpoint."""

    return _invoke(lambda: _workflow_payload(WorkflowEngine(Path(project_root)).resume(run_id)))


@mcp.tool()
def approval_submit(
    project_root: str,
    run_id: str,
    gate: str,
    approved: bool,
    reviewer: str,
    reason: str,
) -> dict[str, Any]:
    """Approve or reject exactly the gate currently requested by a workflow."""

    return _invoke(
        lambda: _workflow_payload(
            WorkflowEngine(Path(project_root)).submit_approval(
                run_id,
                gate=Gate(gate),
                approved=approved,
                reviewer=reviewer,
                reason=reason,
            )
        )
    )


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
) -> dict[str, Any]:
    """Complete the active task with artifact hashes, verification, and required Git evidence."""

    def operation() -> dict[str, Any]:
        completion = TaskCompletion(
            task_id=task_id,
            change_kind=ChangeKind(change_kind),
            branch=branch,
            commit_sha=commit_sha,
            remote_name=remote_name,
            push_status=PushStatus(push_status),
            artifact_paths_and_hashes=artifact_paths_and_hashes or {},
            verification_results=tuple(verification_results or ()),
        )
        result = WorkflowEngine(Path(project_root)).complete_task(run_id, completion)
        return _workflow_payload(result)

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
        )

    return _invoke(operation)


@mcp.tool()
def verification_run(project_root: str) -> dict[str, Any]:
    """Run deterministic runtime, SQLite-integrity, and document-governance checks."""

    def operation() -> dict[str, Any]:
        config = load_project_config(Path(project_root).resolve())
        database = Database(config.root / ".codex-os" / "state" / "state.db")
        database.migrate()
        database.integrity_check()
        report = DocumentManager(config.root).check(config.project_type.value)
        checks = [
            {"name": "sqlite_integrity", "ok": True},
            {"name": "document_governance", "ok": report.ok},
        ]
        return _success(valid=all(bool(check["ok"]) for check in checks), checks=checks)

    return _invoke(operation)


@mcp.tool()
def release_candidate_create(project_root: str, run_id: str) -> dict[str, Any]:
    """Create an idempotent release manifest while the workflow release task is active."""

    def operation() -> dict[str, Any]:
        root = Path(project_root).resolve()
        engine = WorkflowEngine(root)
        result = engine.status(run_id)
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
        relative = f"dist/release-candidates/{run_id}.json"
        manager = DocumentManager(root)
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
            created = manager.write_atomic(relative, content, overwrite=False)
        return _success(
            candidate_id=manifest["candidate_id"],
            path=relative,
            sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            created=created,
        )

    return _invoke(operation)


@mcp.tool()
def memory_search(project_root: str, query: str, limit: int = 20) -> dict[str, Any]:
    """Search active local memory metadata by title, tags, or referenced content path."""

    def operation() -> dict[str, Any]:
        config = load_project_config(Path(project_root).resolve())
        database = Database(config.root / ".codex-os" / "state" / "state.db")
        database.migrate()
        bounded_limit = max(1, min(limit, 100))
        pattern = f"%{query.strip()}%"
        with database.connection() as connection:
            rows = connection.execute(
                """
                SELECT id, run_id, task_id, record_type, title, content_ref,
                       source_refs_json, source_hashes_json, confidence, tags_json,
                       scope, status, created_at, updated_at, expires_at
                FROM memory_records
                WHERE project_id = ? AND status = 'active'
                  AND (title LIKE ? OR tags_json LIKE ? OR content_ref LIKE ?)
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                (config.project_id, pattern, pattern, pattern, bounded_limit),
            ).fetchall()
        return _success(query=query, results=[dict(row) for row in rows])

    return _invoke(operation)


def run_server() -> None:
    """Run the local server over the default stdio transport."""

    mcp.run()


def _workflow_payload(result: WorkflowResult) -> dict[str, Any]:
    return _success(
        run=result.run.model_dump(mode="json"),
        next_action=(
            result.next_action.model_dump(mode="json") if result.next_action is not None else None
        ),
        active_task=(
            result.active_task.model_dump(mode="json") if result.active_task is not None else None
        ),
    )


def _success(**data: Any) -> dict[str, Any]:
    return {"ok": True, **data}


def _invoke(operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return operation()
    except WorkflowError as exc:
        return {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
    except (ConfigError, MigrationError, ValidationError, ValueError, OSError) as exc:
        return {"ok": False, "error": {"code": "CONFIG_INVALID", "message": str(exc)}}


if __name__ == "__main__":
    run_server()
