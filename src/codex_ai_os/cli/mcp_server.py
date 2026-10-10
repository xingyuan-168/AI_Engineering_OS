"""Model Context Protocol server for AI Engineering OS (governance-core, ADR-0016).

Exactly eight governance tools: project_init, governance_check,
approval_record, context_refresh, worktree_manage, memory_search,
memory_record, and memory_candidate. The MCP server is a governance
capability for Codex, not an operating-system API; it never replaces
Codex's own engineering tools.
"""

from __future__ import annotations

import queue
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import anyio
from anyio import to_thread
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import create_model, model_validator

from codex_ai_os.adapters.process import ExecutionStopped
from codex_ai_os.application.finish_execution import query_execution, run_finish
from codex_ai_os.application.preflight import preflight
from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.core.gates import (
    GateError,
    evaluate_code_start,
    evaluate_frontend,
    write_frontend_approval,
)
from codex_ai_os.core.worktree import WorktreeError, WorktreeManager, WorktreeRecord
from codex_ai_os.domain.config import ProjectType
from codex_ai_os.domain.versions import RUNTIME_VERSIONS
from codex_ai_os.infrastructure.config import ConfigError, load_project_config, resolve_runtime_root
from codex_ai_os.infrastructure.database import Database, MigrationError
from codex_ai_os.infrastructure.documents import DocumentManager
from codex_ai_os.infrastructure.errors import DiagnosticError, error_details
from codex_ai_os.infrastructure.memory import MemoryStore, MemoryStoreError
from codex_ai_os.infrastructure.path_codec import configure_utf8_stdio
from codex_ai_os.runtime_identity import runtime_identity
from codex_ai_os.templates.project_docs import INCLUDE_CHOICES

_ACTIVE_CANCELS: set[threading.Event] = set()
_NO_CONTEXT = cast(Context, None)

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
    project_id: str | None = None,
    name: str | None = None,
    project_type: str = "generic",
    include: list[str] | None = None,
    migrate_runtime: bool = False,
) -> dict[str, Any]:
    """Initialize an idempotent local project, minimal documents, and runtime database."""

    def operation() -> dict[str, Any]:
        if migrate_runtime:
            root = resolve_runtime_root(Path(project_root)).project_root
            load_project_config(root)
            result = Database(root / ".codex-os/state/state.db").migrate(allow_legacy=True)
            return _success(
                migration=result.current_version, backup=str(result.legacy_backup_path or "")
            )
        extras = set(include or ())
        if not project_id or not name:
            raise ValueError("project_id and name are required for project initialization")
        unknown = extras - set(INCLUDE_CHOICES)
        if unknown:
            raise ValueError(f"unknown document extras: {sorted(unknown)}")
        result = ProjectInitializer().initialize(
            Path(project_root),
            project_id=project_id,
            name=name,
            project_type=ProjectType(project_type),
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
async def governance_check(
    project_root: str,
    stage: str,
    change_class: str | None = None,
    requirement_id: str | None = None,
    frontend_impact: str = "none",
    frontend_scope: str = "default",
    test_command: str | None = None,
    base_ref: str | None = None,
    memory_written: bool = False,
    memory_not_needed: bool = False,
    remote: str | None = None,
    run_id: str | None = None,
    action: str = "run",
    ctx: Context = _NO_CONTEXT,
) -> dict[str, Any]:
    """Evaluate one governance gate (stage=start|frontend|finish) for the project."""

    cancel, done, started = threading.Event(), threading.Event(), threading.Event()
    updates: queue.Queue[tuple[int, str]] = queue.Queue()
    if stage == "finish" and action == "run":
        run_id = run_id or str(uuid4())

    def progress(value, message):
        if cancel.is_set():
            raise ExecutionStopped("cancelled", "Finish request was cancelled")
        updates.put_nowait((value, message))

    async def report_updates():
        while not done.is_set() or not updates.empty():
            try:
                value, message = updates.get_nowait()
            except queue.Empty:
                await anyio.sleep(0.03)
                continue
            if ctx:
                try:
                    await ctx.report_progress(value, 8, f"run_id={run_id} {message}")
                except (anyio.BrokenResourceError, anyio.ClosedResourceError):
                    cancel.set()
                    return

    async def watch_request_cancel():
        if ctx:
            # SDK 2's MCPServer facade exposes its request channel via the session;
            # cancellation is a native signal even when the handler is not interrupted.
            await ctx.session._request_outbound.cancel_requested.wait()
            cancel.set()

    def operation() -> dict[str, Any]:
        if action not in {"run", "status"} or (stage != "finish" and (action != "run" or run_id)):
            raise DiagnosticError(
                "execution/status parameters are Finish-only", code="RUN_QUERY_INVALID"
            )
        if action == "status":
            if (
                not run_id
                or any(
                    (
                        test_command,
                        base_ref,
                        remote,
                        requirement_id,
                        memory_written,
                        memory_not_needed,
                    )
                )
                or (
                    change_class is not None
                    or frontend_impact != "none"
                    or frontend_scope != "default"
                )
            ):
                raise DiagnosticError(
                    "status requires run_id and no execution parameters", code="RUN_QUERY_INVALID"
                )
            return _success(
                **query_execution(resolve_runtime_root(Path(project_root)).checkout_root, run_id)
            )
        ready = preflight(Path(project_root))
        resolved = ready.resolved
        root = resolved.checkout_root
        config = ready.config
        if stage == "start":
            if not change_class or not change_class.strip():
                raise DiagnosticError(
                    "Start requires an explicit change_class", code="GATE_INPUT_INVALID"
                )
            decision = evaluate_code_start(
                root,
                change_class=change_class,
                requirement_id=requirement_id,
                github_hosts=config.github_hosts,
                remote=remote,
            )
        elif stage == "frontend":
            decision = evaluate_frontend(
                root,
                impact=frontend_impact,
                scope=frontend_scope,
            )
        elif stage == "finish":
            record = run_finish(
                Path(project_root),
                base_ref=base_ref,
                test_command=test_command,
                change_class=change_class,
                requirement_id=requirement_id,
                memory_written=memory_written,
                memory_not_needed=memory_not_needed,
                remote=remote,
                run_id=run_id,
                cancel=cancel,
                on_progress=progress,
            )
            if record["execution_status"] != "completed":
                return {
                    "ok": False,
                    "error": record["error"],
                    "data": record,
                    "meta": {"runtime": runtime_identity()},
                }
            return _success(**record)
        else:
            raise ValueError("stage must be one of: start, frontend, finish")
        return _success(
            preflight=ready.report(),
            gate=decision.gate.value,
            allowed=decision.allowed,
            blocked_by=list(decision.blocked_by),
            findings=[
                {
                    "code": finding.code,
                    "message": finding.message,
                    "path": finding.path,
                    "blocking": finding.blocking,
                    "details": finding.details,
                }
                for finding in decision.findings
            ],
        )

    def worker():
        started.set()
        try:
            return _invoke(operation)
        finally:
            done.set()

    _ACTIVE_CANCELS.add(cancel)
    try:
        if ctx and run_id:
            await ctx.report_progress(0, 8, "Finish run_id=" + run_id)
        result = None
        async with anyio.create_task_group() as group:
            group.start_soon(report_updates)
            group.start_soon(watch_request_cancel)
            try:
                result = await to_thread.run_sync(worker, abandon_on_cancel=True)
            finally:
                group.cancel_scope.cancel()
        assert result is not None
        return result
    except anyio.get_cancelled_exc_class():
        cancel.set()
        with anyio.CancelScope(shield=True):
            while started.is_set() and not done.is_set():
                await anyio.sleep(0.03)
        raise
    finally:
        if started.is_set() and not done.is_set():
            cancel.set()
            with anyio.CancelScope(shield=True):
                while not done.is_set():
                    await anyio.sleep(0.03)
        _ACTIVE_CANCELS.discard(cancel)


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
        resolved = resolve_runtime_root(Path(project_root))
        root = resolved.checkout_root
        if gate not in {"code_start", "frontend", "finish"}:
            raise ValueError("gate must be one of: code_start, frontend, finish")
        if decision not in {"approved", "rejected"}:
            raise ValueError("decision must be approved or rejected")
        if not decided_by.strip():
            raise ValueError("decided_by is required")
        if not scope.strip():
            raise ValueError("scope is required")
        if gate == "frontend":
            write_frontend_approval(
                root / "docs" / "design" / "UI_SPEC.md",
                scope=scope,
                approved_by=decided_by,
                approved_on=datetime.now(UTC).date().isoformat(),
                decision=decision,
            )
        warnings = []
        approval_id = None
        try:
            database = Database(resolved.project_root / ".codex-os/state/state.db")
            database.migrate()
            approval_id = _record_approval(
                database,
                gate=gate,
                subject=subject,
                decision=decision,
                decided_by=decided_by,
                reason=reason,
            )
        except (MigrationError, sqlite3.Error, OSError) as exc:
            if gate != "frontend":
                raise
            warnings.append("Approval document saved; derived index unavailable: " + str(exc))
        return _success(
            id=approval_id,
            gate=gate,
            subject=subject,
            decision=decision,
            scope=scope,
            warnings=warnings,
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
        root = resolve_runtime_root(Path(project_root)).project_root
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
        root = resolve_runtime_root(Path(project_root)).project_root
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
        root = resolve_runtime_root(Path(project_root)).project_root
        load_project_config(root)
        database = Database(root / ".codex-os" / "state" / "state.db")
        database.migrate()
        store = MemoryStore(database, Path(project_root))
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
        root = resolve_runtime_root(Path(project_root)).project_root
        load_project_config(root)
        database = Database(root / ".codex-os" / "state" / "state.db")
        database.migrate()
        store = MemoryStore(database, Path(project_root))
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


@mcp.tool()
def memory_candidate(
    project_root: str,
    action: str,
    candidate_id: str | None = None,
) -> dict[str, Any]:
    """List, accept, or reject subagent memory candidates (main session only)."""

    def operation() -> dict[str, Any]:
        root = resolve_runtime_root(Path(project_root)).project_root
        load_project_config(root)
        database = Database(root / ".codex-os" / "state" / "state.db")
        database.migrate()
        store = MemoryStore(database, Path(project_root))
        if action == "list":
            return _success(
                results=[
                    {
                        "id": record.id,
                        "type": record.record_type,
                        "title": record.title,
                        "summary": record.summary,
                        "source": record.source,
                        "tags": list(record.tags),
                    }
                    for record in store.candidates()
                ]
            )
        if action == "accept":
            if candidate_id is None:
                raise ValueError("accept requires candidate_id")
            entry = store.accept_candidate(candidate_id)
            return _success(id=entry.id, status=entry.status, accepted=True)
        if action == "reject":
            if candidate_id is None:
                raise ValueError("reject requires candidate_id")
            store.reject_candidate(candidate_id)
            return _success(id=candidate_id, rejected=True)
        raise ValueError("action must be one of: list, accept, reject")

    return _invoke(operation)


# SDK-generated input models otherwise silently ignore unknown attestations.
# Keep published schemas and actual validation strict for these eight tools.
def _status_arguments_only(cls, arguments):
    if (
        isinstance(arguments, dict)
        and arguments.get("stage") == "finish"
        and arguments.get("action") == "status"
        and arguments.keys() - {"project_root", "stage", "action", "run_id"}
    ):
        raise ValueError("status requires run_id and no execution parameters")
    return arguments


for _tool in mcp._tool_manager.list_tools():
    _model = _tool.fn_metadata.arg_model
    if _tool.name == "governance_check":
        _model = create_model(
            _model.__name__,
            __base__=_model,
            __validators__={
                "status_arguments_only": cast(
                    Any, model_validator(mode="before")(_status_arguments_only)
                )
            },
        )
        _tool.fn_metadata.arg_model = _model
    _model.model_config["extra"] = "forbid"
    _model.model_rebuild(force=True)
    _tool.parameters = _model.model_json_schema()


def run_server() -> None:
    configure_utf8_stdio()
    anyio.run(_run_stdio)


async def _run_stdio() -> None:
    from mcp.server.stdio import stdio_server

    incoming, relay = anyio.create_memory_object_stream[Any](16)

    async def forward(read):
        try:
            async with incoming:
                async for message in read:
                    await incoming.send(message)
        finally:
            for cancel in tuple(_ACTIVE_CANCELS):
                cancel.set()

    async with stdio_server() as (read, write), anyio.create_task_group() as group:
        group.start_soon(forward, read)
        try:
            await mcp._lowlevel_server.run(
                relay, write, mcp._lowlevel_server.create_initialization_options()
            )
        finally:
            group.cancel_scope.cancel()


def _success(**data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data, "meta": {"runtime": runtime_identity()}}


def _invoke(operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return operation()
    except (
        ConfigError,
        GateError,
        MemoryStoreError,
        MigrationError,
        WorktreeError,
        DiagnosticError,
        ValueError,
        OSError,
    ) as exc:
        return {
            "ok": False,
            "error": {
                "code": getattr(exc, "code", "GOVERNANCE_CHECK_FAILED"),
                "message": str(exc),
                "details": error_details(exc),
            },
            "meta": {"runtime": runtime_identity()},
        }
    except Exception as exc:  # pragma: no cover - defensive envelope
        return {
            "ok": False,
            "error": {"code": "INTERNAL_ERROR", "message": str(exc)},
            "meta": {"runtime": runtime_identity()},
        }


if __name__ == "__main__":
    run_server()
