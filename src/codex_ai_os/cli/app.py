"""Typer command surface for AI Engineering OS (governance-core, ADR-0016).

Seven governance commands plus the hook bridge: init, check, finish,
memory, worktree, mcp, doctor, and authorize-hook. The CLI answers
"allowed / not allowed and why" and manages the light runtime state; it
never tells Codex how to do professional work.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

from codex_ai_os.application.doctor import DoctorService
from codex_ai_os.application.hook_gateway import authorize_hook_payload
from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.application.repository import RepositoryGovernanceService
from codex_ai_os.cli.output import emit, error_envelope, success_envelope
from codex_ai_os.core.gates import GateError, evaluate_code_start, evaluate_finish
from codex_ai_os.core.worktree import WorktreeError, WorktreeManager, WorktreeRecord
from codex_ai_os.domain.config import ProjectType
from codex_ai_os.infrastructure.config import ConfigError, load_project_config
from codex_ai_os.infrastructure.database import Database, MigrationError
from codex_ai_os.infrastructure.documents import DocumentManager
from codex_ai_os.infrastructure.memory import (
    MemoryEntry,
    MemoryStore,
    MemoryStoreError,
)
from codex_ai_os.infrastructure.path_codec import configure_utf8_stdio
from codex_ai_os.templates.project_docs import INCLUDE_CHOICES

app = typer.Typer(
    name="codex-os",
    help="Local, auditable engineering governance layer for Codex.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)

memory_app = typer.Typer(
    help="Project memory backed by docs/memory/memory.jsonl.", no_args_is_help=True
)
app.add_typer(memory_app, name="memory")

worktree_app = typer.Typer(help="Disposable worktrees under .worktrees/.", no_args_is_help=True)
app.add_typer(worktree_app, name="worktree")


def _fail(code: str, message: str, exit_code: int, json_output: bool) -> None:
    emit(
        error_envelope(code, message),
        json_output=json_output,
        human=f"{code}: {message}",
    )
    raise typer.Exit(code=exit_code)


def _project_database(project_root: Path) -> tuple[Any, Database]:
    config = load_project_config(project_root.resolve())
    database = Database(config.root / ".codex-os" / "state" / "state.db")
    database.migrate()
    return config, database


@app.command("init")
def init_command(
    project_root: Annotated[Path, typer.Argument(help="Project directory.")] = Path("."),
    project_id: Annotated[str, typer.Option("--project-id")] = "PROJECT-LOCAL",
    name: Annotated[str, typer.Option("--name")] = "AI Engineering Project",
    project_type: Annotated[ProjectType, typer.Option("--project-type")] = ProjectType.GENERIC,
    with_extra: Annotated[list[str] | None, typer.Option("--with")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON only.")] = False,
) -> None:
    """Create the project skeleton: config, minimal documents, runtime database."""

    include = _include_keys(with_extra or [])
    try:
        result = ProjectInitializer().initialize(
            project_root,
            project_id=project_id,
            name=name,
            project_type=project_type,
                include=include,
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


def _include_keys(values: list[str]) -> frozenset[str]:
    unknown = set(values) - set(INCLUDE_CHOICES)
    if unknown:
        raise ValueError(f"unknown --with extras: {sorted(unknown)}")
    return frozenset(values)


@app.command("check")
def check_command(
    project_root: Annotated[Path, typer.Argument(help="Project directory.")] = Path("."),
    change_class: Annotated[
        str | None, typer.Option("--change-class", help="Run the Code Start gate for this class.")
    ] = None,
    requirement_id: Annotated[
        str | None,
        typer.Option("--requirement-id", help="Current requirement id for research scoping."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Check GitHub readiness, repository hygiene, the docs/ tree, and
    optionally the Code Start gate (with --change-class)."""

    gate_decision = None
    try:
        config = load_project_config(project_root.resolve())
        repository = RepositoryGovernanceService(config.root).check()
        documents = DocumentManager(config.root).check()
        if change_class is not None:
            gate_decision = evaluate_code_start(
                config.root,
                change_class=change_class,
                requirement_id=requirement_id,
                github_hosts=config.github_hosts,
            )
    except GateError as exc:
        _fail("GATE_INPUT_INVALID", str(exc), 2, json_output)
        return
    except (ConfigError, MigrationError, ValueError, OSError) as exc:
        _fail("CONFIG_INVALID", str(exc), 2, json_output)
        return
    data: dict[str, Any] = {
        "repository": {
            "repository_ready": repository.repository_ready,
            "hygiene_ok": repository.hygiene_ok,
            "remote_host": repository.remote_host,
            "head_commit": repository.head_commit,
            "findings": [item.model_dump(mode="json") for item in repository.findings],
        },
        "documents": {
            "ok": documents.ok,
            "checked_files": documents.checked_files,
            "missing": list(documents.missing),
            "broken_links": list(documents.broken_links),
            "forbidden_directories": list(documents.forbidden_directories),
        },
        "code_start": None
        if gate_decision is None
        else {
            "allowed": gate_decision.allowed,
            "blocked_by": list(gate_decision.blocked_by),
            "findings": [
                {"code": f.code, "message": f.message, "path": f.path, "blocking": f.blocking}
                for f in gate_decision.findings
            ],
        },
    }
    docs_and_repo_ok = repository.repository_ready and documents.ok
    gate_ok = gate_decision is None or gate_decision.allowed
    if docs_and_repo_ok and gate_ok:
        emit(
            success_envelope(data),
            json_output=json_output,
            human="Repository and document checks passed.",
        )
        return
    if gate_decision is not None and not gate_decision.allowed:
        code = gate_decision.blocked_by[0] if gate_decision.blocked_by else "CODE_START_BLOCKED"
    elif not repository.repository_ready and repository.findings:
        code = repository.findings[0].code
    else:
        code = "DOCS_INCOMPLETE"
    emit(
        error_envelope(code, "Repository, document, or Code Start checks failed.", data),
        json_output=json_output,
        human="Repository, document, or Code Start checks failed.",
    )
    raise typer.Exit(code=40)


@app.command("finish")
def finish_command(
    project_root: Annotated[Path, typer.Argument(help="Project directory.")] = Path("."),
    test_command: Annotated[
        str | None, typer.Option("--test-command", help="Verifiable test command to run.")
    ] = None,
    memory_written: Annotated[bool, typer.Option("--memory-written")] = False,
    memory_not_needed: Annotated[bool, typer.Option("--memory-not-needed")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run the Finish gate over task facts and observable repository state."""

    try:
        decision = evaluate_finish(
            project_root,
            test_command=test_command,
            memory_written=memory_written,
            memory_not_needed=memory_not_needed,
        )
    except (ValueError, OSError) as exc:
        _fail("CONFIG_INVALID", str(exc), 2, json_output)
        return
    data = {
        "allowed": decision.allowed,
        "blocked_by": list(decision.blocked_by),
        "findings": [
            {"code": f.code, "message": f.message, "path": f.path, "blocking": f.blocking}
            for f in decision.findings
        ],
    }
    if decision.allowed:
        emit(success_envelope(data), json_output=json_output, human="Finish gate passed.")
        return
    emit(
        error_envelope(
            decision.blocked_by[0] if decision.blocked_by else "FINISH_BLOCKED",
            "Finish gate blocked the task.",
            data,
        ),
        json_output=json_output,
        human="Finish gate blocked: " + ", ".join(decision.blocked_by),
    )
    raise typer.Exit(code=40)


@app.command("doctor")
def doctor_command(
    project_root: Annotated[Path, typer.Argument(help="Project directory.")] = Path("."),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Report runtime diagnostics (Python, Git, SQLite, hooks)."""

    report = DoctorService(project_root).run()
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
        emit(
            success_envelope({"checks": checks}),
            json_output=json_output,
            human="Environment checks passed.",
        )
        return
    emit(
        error_envelope(
            "PATH_ENCODING_CORRUPT" if report.path_encoding_corrupt else "CONFIG_INVALID",
            "Required environment checks failed.",
            {"checks": checks},
        ),
        json_output=json_output,
        human="Required environment checks failed.",
    )
    raise typer.Exit(code=2)


@app.command("authorize-hook")
def authorize_hook_command() -> None:
    """Adjudicate one Codex PreToolUse hook payload from stdin (ADR-0011).

    Reads the hook JSON payload from stdin and prints the hook JSON decision
    (allow is printed as an empty output so the host keeps its default flow).
    The command exits 0 even for denials; a non-zero exit signals that the
    runtime could not adjudicate and the hook must apply its degraded
    fallback rules.
    """

    try:
        payload: object = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError, ValueError):
        raise typer.Exit(code=1) from None
    if not isinstance(payload, dict):
        raise typer.Exit(code=1)
    try:
        output = authorize_hook_payload(payload)
    except Exception:
        # Fail-closed signalling: the hook script applies its degraded rules.
        raise typer.Exit(code=1) from None
    if output:
        typer.echo(json.dumps(output, ensure_ascii=False))


@app.command("mcp")
def mcp_command() -> None:
    """Run the bundled Model Context Protocol server over stdio."""

    from codex_ai_os.cli.mcp_server import run_server

    run_server()


@memory_app.command("search")
def memory_search_command(
    query: Annotated[str, typer.Argument(help="Search text.")] = "",
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    limit: Annotated[int, typer.Option("--limit")] = 20,
    record_type: Annotated[str | None, typer.Option("--type")] = None,
    status: Annotated[str, typer.Option("--status")] = "active",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Search the project memory index rebuilt from docs/memory/memory.jsonl."""

    try:
        _, database = _project_database(project_root)
        store = MemoryStore(database, project_root.resolve())
        types = (record_type,) if record_type else ()
        records = store.search(query, record_types=types, statuses=(status,), limit=limit)
    except (ConfigError, MemoryStoreError, MigrationError, ValueError, OSError) as exc:
        _fail("CONFIG_INVALID", str(exc), 2, json_output)
        return
    data = {"results": [_memory_payload(record) for record in records]}
    emit(
        success_envelope(data),
        json_output=json_output,
        human=f"{len(records)} memory record(s) found.",
    )


@memory_app.command("record")
def memory_record_command(
    title: Annotated[str, typer.Option("--title")],
    summary: Annotated[str, typer.Option("--summary")],
    source: Annotated[str, typer.Option("--source", help="Source path or URL.")],
    record_type: Annotated[str, typer.Option("--type")] = "decision",
    source_commit: Annotated[str | None, typer.Option("--source-commit")] = None,
    tags: Annotated[str, typer.Option("--tags", help="Comma separated tags.")] = "",
    status: Annotated[str, typer.Option("--status")] = "active",
    superseded_by: Annotated[str | None, typer.Option("--superseded-by")] = None,
    candidate: Annotated[bool, typer.Option("--candidate")] = False,
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Record one memory entry (main session) or a candidate (--candidate)."""

    tag_tuple = tuple(tag.strip() for tag in tags.split(",") if tag.strip())
    try:
        _, database = _project_database(project_root)
        store = MemoryStore(database, project_root.resolve())
        writer = store.record_candidate if candidate else store.record
        entry = writer(
            record_type=record_type,
            title=title,
            summary=summary,
            source=source,
            source_commit=source_commit,
            tags=tag_tuple,
            status=status,
            superseded_by=superseded_by,
        )
    except (ConfigError, MemoryStoreError, MigrationError, ValueError, OSError) as exc:
        _fail("CONFIG_INVALID", str(exc), 2, json_output)
        return
    emit(
        success_envelope({"entry": _memory_payload(entry), "candidate": candidate}),
        json_output=json_output,
        human=("Candidate stored: " if candidate else "Memory recorded: ") + entry.id,
    )


@memory_app.command("reindex")
def memory_reindex_command(
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Rebuild the SQLite memory index from docs/memory/memory.jsonl."""

    try:
        _, database = _project_database(project_root)
        store = MemoryStore(database, project_root.resolve())
        result = store.reindex()
    except (ConfigError, MemoryStoreError, MigrationError, ValueError, OSError) as exc:
        _fail("CONFIG_INVALID", str(exc), 2, json_output)
        return
    data = {
        "indexed": result.indexed,
        "removed": result.removed,
        "invalid_lines": list(result.invalid_lines),
    }
    ok = not result.invalid_lines
    envelope = (
        success_envelope(data)
        if ok
        else error_envelope("MEMORY_JSONL_INVALID", "memory.jsonl contains invalid lines", data)
    )
    emit(
        envelope,
        json_output=json_output,
        human=(
            f"Reindexed {result.indexed} memory record(s)."
            if ok
            else f"Reindexed with {len(result.invalid_lines)} invalid line(s)."
        ),
    )
    if not ok:
        raise typer.Exit(code=2)


@memory_app.command("candidates")
def memory_candidates_command(
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List subagent memory candidates awaiting the main-session merge."""

    try:
        _, database = _project_database(project_root)
        store = MemoryStore(database, project_root.resolve())
        records = store.candidates()
    except (ConfigError, MemoryStoreError, MigrationError, ValueError, OSError) as exc:
        _fail("CONFIG_INVALID", str(exc), 2, json_output)
        return
    data = {"results": [_memory_payload(record) for record in records]}
    emit(
        success_envelope(data),
        json_output=json_output,
        human=f"{len(records)} candidate(s) waiting.",
    )


@memory_app.command("candidate")
def memory_candidate_command(
    candidate_id: Annotated[str, typer.Argument(help="Candidate id, e.g. MEM-...")],
    accept: Annotated[
        bool, typer.Option("--accept", help="Merge the candidate into the JSONL.")
    ] = False,
    reject: Annotated[
        bool, typer.Option("--reject", help="Discard the candidate.")
    ] = False,
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Accept or reject one subagent memory candidate (main session only)."""

    if accept == reject:
        _fail("CONFIG_INVALID", "pass exactly one of --accept or --reject", 2, json_output)
        return
    try:
        _, database = _project_database(project_root)
        store = MemoryStore(database, project_root.resolve())
        if accept:
            entry = store.accept_candidate(candidate_id)
        else:
            store.reject_candidate(candidate_id)
            entry = None
    except MemoryStoreError as exc:
        code = (
            "MEMORY_CANDIDATE_MISSING"
            if exc.code == "MEMORY_CANDIDATE_MISSING"
            else "CONFIG_INVALID"
        )
        _fail(code, str(exc), 2, json_output)
        return
    except (ConfigError, MigrationError, ValueError, OSError) as exc:
        _fail("CONFIG_INVALID", str(exc), 2, json_output)
        return
    data = {
        "accepted": accept,
        "entry": _memory_payload(entry) if entry is not None else None,
    }
    if entry is not None:
        message = "Candidate accepted: " + entry.id
    else:
        message = "Candidate rejected: " + candidate_id
    emit(success_envelope(data), json_output=json_output, human=message)


def _memory_payload(record: MemoryEntry) -> dict[str, object]:
    return {
        "id": record.id,
        "type": record.record_type,
        "title": record.title,
        "summary": record.summary,
        "source": record.source,
        "source_commit": record.source_commit,
        "tags": list(record.tags),
        "status": record.status,
    }


@worktree_app.command("prepare")
def worktree_prepare_command(
    name: Annotated[str | None, typer.Argument(help="Worktree name (slug).")] = None,
    base_ref: Annotated[str, typer.Option("--base-ref")] = "HEAD",
    task_id: Annotated[str | None, typer.Option("--task-id")] = None,
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Create a disposable worktree under .worktrees/ and register it."""

    try:
        config, database = _project_database(project_root)
        manager = WorktreeManager(project_root.resolve(), database=database)
        record = manager.prepare(
            name=name,
            task_id=task_id,
            base_ref=base_ref,
            target_branch=config.target_branch,
        )
    except (ConfigError, MigrationError, WorktreeError, ValueError, OSError) as exc:
        _fail("WORKTREE_FAILED", str(exc), 2, json_output)
        return
    emit(
        success_envelope(_worktree_payload(record)),
        json_output=json_output,
        human=f"Prepared {record.path} on {record.branch}.",
    )


@worktree_app.command("check")
def worktree_check_command(
    name: Annotated[str, typer.Argument(help="Worktree name.")],
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Report registration, existence, and cleanliness of one worktree."""

    try:
        _, database = _project_database(project_root)
        manager = WorktreeManager(project_root.resolve(), database=database)
        record = manager.check(name=name)
    except (ConfigError, MigrationError, WorktreeError, ValueError, OSError) as exc:
        _fail("WORKTREE_FAILED", str(exc), 2, json_output)
        return
    emit(
        success_envelope(_worktree_payload(record)),
        json_output=json_output,
        human=f"{record.name}: status={record.status} clean={record.clean}",
    )


@worktree_app.command("finish")
def worktree_finish_command(
    name: Annotated[str, typer.Argument(help="Worktree name.")],
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Mark a clean worktree as finished and ready for merge review."""

    try:
        _, database = _project_database(project_root)
        manager = WorktreeManager(project_root.resolve(), database=database)
        record = manager.finish(name=name)
    except (ConfigError, MigrationError, WorktreeError, ValueError, OSError) as exc:
        _fail("WORKTREE_FAILED", str(exc), 2, json_output)
        return
    emit(
        success_envelope(_worktree_payload(record)),
        json_output=json_output,
        human=f"{record.name}: ready for merge review.",
    )


@worktree_app.command("cleanup")
def worktree_cleanup_command(
    name: Annotated[str, typer.Argument(help="Worktree name.")],
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Remove a merged disposable worktree and its branch, then unregister it."""

    try:
        _, database = _project_database(project_root)
        manager = WorktreeManager(project_root.resolve(), database=database)
        record = manager.cleanup(name=name)
    except (ConfigError, MigrationError, WorktreeError, ValueError, OSError) as exc:
        _fail("WORKTREE_FAILED", str(exc), 2, json_output)
        return
    emit(
        success_envelope(_worktree_payload(record)),
        json_output=json_output,
        human=f"{record.name}: removed and unregistered.",
    )


@worktree_app.command("list")
def worktree_list_command(
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List registered worktrees."""

    try:
        _, database = _project_database(project_root)
        manager = WorktreeManager(project_root.resolve(), database=database)
        records = manager.list()
    except (ConfigError, MigrationError, WorktreeError, ValueError, OSError) as exc:
        _fail("WORKTREE_FAILED", str(exc), 2, json_output)
        return
    data = {"results": [_worktree_payload(record) for record in records]}
    emit(
        success_envelope(data),
        json_output=json_output,
        human=f"{len(records)} worktree(s) registered.",
    )


def _worktree_payload(record: WorktreeRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "name": record.name,
        "path": record.path,
        "branch": record.branch,
        "task_id": record.task_id,
        "status": record.status,
        "clean": record.clean,
    }


def main() -> None:
    configure_utf8_stdio()
    app()


if __name__ == "__main__":
    main()
