"""Model Context Protocol server for AI Engineering OS (governance-core, ADR-0016).

Exactly seven governance tools: project_init, governance_check,
approval_record, context_refresh, worktree_manage, memory_search, and
memory_record. The MCP server is a governance capability for Codex, not an
operating-system API; it never replaces Codex's own engineering tools.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.core.gates import (
    GateError,
    evaluate_code_start,
    evaluate_finish,
    evaluate_frontend,
    write_frontend_approval,
)
from codex_ai_os.core.worktree import WorktreeError, WorktreeManager, WorktreeRecord
from codex_ai_os.domain.config import ProjectType, RiskLevel
from codex_ai_os.domain.versions import RUNTIME_VERSIONS
from codex_ai_os.infrastructure.config import ConfigError, load_project_config
from codex_ai_os.infrastructure.database import Database, MigrationError
from codex_ai_os.infrastructure.documents import DocumentManager
from codex_ai_os.infrastructure.memory import MemoryStore, MemoryStoreError
from codex_ai_os.infrastructure.path_codec import configure_utf8_stdio
from codex_ai_os.templates.project_docs import INCLUDE_CHOICES

mcp = MCPServer(
    "AI Engineering OS",
    version=RUNTIME_VERSIONS.software,
    instructions=(
        "Governance tools for Codex. Call governance_check at task start "
        "(stage=start), before frontend implementation (stage=frontend), and "
        "at task end (stage=finish); record user approvals with "
        "approval_record; keep Codex's native engineering workflow. These "
        "tools never replace Codex's own engineering capabilities."
    ),
)


@mcp.tool()
def project_init(
    project_root: str,
    project_id: str,
    name: str,
    project_type: str = "generic",
    risk_level: str = "medium",
    include: list[str] | None = None,
) -> dict[str, Any]:
    """Initialize an idempotent local project, minimal documents, and runtime database."""

    def operation() -> dict[str, Any]:
        extras = set(include or ())
        unknown = extras - set(INCLUDE_CHOICES)
        if unknown:
            raise ValueError(f"unknown document extras: {sorted(unknown)}")
        result = ProjectInitializer().initialize(
            Path(project_root),
            project_id=project_id,
            name=name,
            project_type=ProjectType(project_type),
            risk_level=RiskLevel(risk_level),
            include=frozenset(extras),
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
def governance_check(
    project_root: str,
    stage: str,
    change_class: str = "small_change",
    requirement_id: str | None = None,
    frontend_impact: str = "none",
    frontend_scope: str = "default",
    tests_passed: bool = False,
    docs_synced: bool = False,
    memory_written: bool = False,
    memory_not_needed: bool = False,
) -> dict[str, Any]:
    """Evaluate one governance gate (stage=start|frontend|finish) for the project."""

    def operation() -> dict[str, Any]:
        root = Path(project_root).resolve()
        if stage == "start":
            decision = evaluate_code_start(
                root,
                change_class=change_class,
                requirement_id=requirement_id,
            )
        elif stage == "frontend":
            decision = evaluate_frontend(
                root,
                impact=frontend_impact,
                scope=frontend_scope,
            )
        elif stage == "finish":
            decision = evaluate_finish(
                root,
                tests_passed=tests_passed,
                docs_synced=docs_synced,
                memory_written=memory_written,
                memory_not_needed=memory_not_needed,
            )
        else:
            raise ValueError("stage must be one of: start, frontend, finish")
        return _success(
            gate=decision.gate.value,
            allowed=decision.allowed,
            blocked_by=list(decision.blocked_by),
            findings=[
                {
                    "code": finding.code,
                    "message": finding.message,
                    "path": finding.path,
                    "blocking": finding.blocking,
                }
                for finding in decision.findings
            ],
        )

    return _invoke(operation)


@mcp.tool()
def approval_record(
    project_root: str,
    gate: str,
    subject: str,
    decision: str,
    decided_by: str,
    scope: str = "default",
    reason: str | None = None,
) -> dict[str, Any]:
    """Record one user approval or rejection for a governance gate.

    Frontend approvals are additionally written into the Git-tracked
    docs/design/UI_SPEC.md metadata so the approval fact survives database
    resets; the SQLite row is an index only. A scope never inherits
    another scope's approval.
    """

    def operation() -> dict[str, Any]:
        root = Path(project_root).resolve()
        if gate not in {"code_start", "frontend", "finish"}:
            raise ValueError("gate must be one of: code_start, frontend, finish")
        if decision not in {"approved", "rejected"}:
            raise ValueError("decision must be approved or rejected")
        if not decided_by.strip():
            raise ValueError("decided_by is required")
        if not scope.strip():
            raise ValueError("scope is required")
        database = Database(root / ".codex-os" / "state" / "state.db")
        database.migrate()
        approval_id = _record_approval(
            database, gate=gate, subject=subject, decision=decision,
            decided_by=decided_by, reason=reason,
        )
        if gate == "frontend" and decision == "approved":
            write_frontend_approval(
                root / "docs" / "design" / "UI_SPEC.md",
                scope=scope,
                approved_on=datetime.now(UTC).date().isoformat(),
            )
        return _success(
            id=approval_id, gate=gate, subject=subject, decision=decision, scope=scope
        )

    return _invoke(operation)


def _record_approval(
    database: Database,
    *,
    gate: str,
    subject: str,
    decision: str,
    decided_by: str,
    reason: str | None,
) -> str:
    approval_id = "APPROVAL-" + datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    with database.connection() as connection:
        connection.execute(
            "INSERT INTO approvals(id, subject, gate, decision, decided_by, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                approval_id,
                subject.strip(),
                gate,
                decision,
                decided_by.strip(),
                reason,
                datetime.now(UTC).isoformat(),
            ),
        )
        connection.commit()
    return approval_id


@mcp.tool()
def context_refresh(project_root: str) -> dict[str, Any]:
    """Regenerate the derived PROJECT_CONTEXT.md cache from the docs/ tree."""

    def operation() -> dict[str, Any]:
        root = Path(project_root).resolve()
        context_path = DocumentManager(root).generate_context()
        return _success(context=context_path.as_posix())

    return _invoke(operation)


@mcp.tool()
def worktree_manage(
    project_root: str,
    action: str,
    name: str | None = None,
    task_id: str | None = None,
    base_ref: str = "HEAD",
) -> dict[str, Any]:
    """Manage disposable worktrees (action=prepare|check|finish|cleanup|list)."""

    def operation() -> dict[str, Any]:
        root = Path(project_root).resolve()
        config = load_project_config(root)
        database = Database(root / ".codex-os" / "state" / "state.db")
        database.migrate()
        manager = WorktreeManager(root, database=database)
        if action == "prepare":
            record = manager.prepare(
                name=name,
                task_id=task_id,
                base_ref=base_ref,
                target_branch=config.target_branch,
            )
            return _success(**_worktree_data(record))
        if action == "check":
            if name is None:
                raise ValueError("check requires name")
            return _success(**_worktree_data(manager.check(name=name)))
        if action == "finish":
            if name is None:
                raise ValueError("finish requires name")
            return _success(**_worktree_data(manager.finish(name=name)))
        if action == "cleanup":
            if name is None:
                raise ValueError("cleanup requires name")
            return _success(**_worktree_data(manager.cleanup(name=name)))
        if action == "list":
            return _success(results=[_worktree_data(item) for item in manager.list()])
        raise ValueError("action must be one of: prepare, check, finish, cleanup, list")

    return _invoke(operation)


def _worktree_data(record: WorktreeRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "name": record.name,
        "path": record.path,
        "branch": record.branch,
        "task_id": record.task_id,
        "status": record.status,
        "clean": record.clean,
    }


@mcp.tool()
def memory_search(
    project_root: str,
    query: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    """Search the project memory index rebuilt from docs/memory/memory.jsonl."""

    def operation() -> dict[str, Any]:
        root = Path(project_root).resolve()
        load_project_config(root)
        database = Database(root / ".codex-os" / "state" / "state.db")
        database.migrate()
        store = MemoryStore(database, root)
        records = store.search(query, statuses=("active",), limit=limit)
        return _success(
            results=[
                {
                    "id": record.id,
                    "type": record.record_type,
                    "title": record.title,
                    "summary": record.summary,
                    "source": record.source,
                    "source_commit": record.source_commit,
                    "tags": list(record.tags),
                    "status": record.status,
                }
                for record in records
            ]
        )

    return _invoke(operation)


@mcp.tool()
def memory_record(
    project_root: str,
    record_type: str,
    title: str,
    summary: str,
    source: str,
    source_commit: str | None = None,
    tags: list[str] | None = None,
    candidate: bool = False,
) -> dict[str, Any]:
    """Record one memory entry; subagents must set candidate=true."""

    def operation() -> dict[str, Any]:
        root = Path(project_root).resolve()
        load_project_config(root)
        database = Database(root / ".codex-os" / "state" / "state.db")
        database.migrate()
        store = MemoryStore(database, root)
        writer = store.record_candidate if candidate else store.record
        entry = writer(
            record_type=record_type,
            title=title,
            summary=summary,
            source=source,
            source_commit=source_commit,
            tags=tuple(tags or ()),
        )
        return _success(
            id=entry.id,
            type=entry.record_type,
            title=entry.title,
            status=entry.status,
            candidate=candidate,
        )

    return _invoke(operation)


def run_server() -> None:
    configure_utf8_stdio()
    mcp.run()


def _success(**data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data}


def _invoke(operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return operation()
    except (
        ConfigError,
        GateError,
        MemoryStoreError,
        MigrationError,
        WorktreeError,
        ValueError,
        OSError,
    ) as exc:
        return {"ok": False, "error": {"code": "GOVERNANCE_CHECK_FAILED", "message": str(exc)}}
    except Exception as exc:  # pragma: no cover - defensive envelope
        return {"ok": False, "error": {"code": "INTERNAL_ERROR", "message": str(exc)}}


if __name__ == "__main__":
    run_server()
