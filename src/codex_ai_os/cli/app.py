"""Typer command surface for AI Engineering OS (governance-core surface, ADR-0016)."""

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
from codex_ai_os.domain.config import ProjectType, RiskLevel
from codex_ai_os.infrastructure.config import ConfigError, load_project_config
from codex_ai_os.infrastructure.database import Database, MigrationError
from codex_ai_os.infrastructure.documents import DocumentManager
from codex_ai_os.infrastructure.memory import MemoryEntry, MemoryStore, MemoryStoreError
from codex_ai_os.infrastructure.path_codec import configure_utf8_stdio

app = typer.Typer(
    name="codex-os",
    help="Local, auditable engineering governance layer for Codex.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)

memory_app = typer.Typer(help="Search governed project Memory.", no_args_is_help=True)
app.add_typer(memory_app, name="memory")


def _fail(code: str, message: str, exit_code: int, json_output: bool) -> None:
    emit(
        error_envelope(code, message),
        json_output=json_output,
        human=f"{code}: {message}",
    )
    raise typer.Exit(code=exit_code)


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


@app.command("init")
def init_command(
    project_root: Annotated[Path, typer.Argument(help="Project directory.")] = Path("."),
    project_id: Annotated[str, typer.Option("--project-id")] = "PROJECT-LOCAL",
    name: Annotated[str, typer.Option("--name")] = "AI Engineering Project",
    project_type: Annotated[ProjectType, typer.Option("--project-type")] = ProjectType.GENERIC,
    risk_level: Annotated[RiskLevel, typer.Option("--risk-level")] = RiskLevel.MEDIUM,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON only.")] = False,
) -> None:
    """Create a project configuration, minimal documents, and runtime database."""

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
        report = RepositoryGovernanceService(project_root).check(target_branch=target_branch)
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


@app.command("check-docs")
def check_docs_command(
    project_root: Annotated[Path, typer.Argument(help="Project directory.")] = Path("."),
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON only.")] = False,
) -> None:
    """Check required documents, headings, local links, and copy directories."""

    try:
        config = load_project_config(project_root.resolve())
        report = DocumentManager(config.root).check(
            config.project_type.value,
            expected_document_version=config.document_version,
        )
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
        "traceability_errors": list(report.traceability_errors),
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


def _memory_store(project_root: Path) -> MemoryStore:
    config = load_project_config(project_root.resolve())
    database = Database(config.root / ".codex-os" / "state" / "state.db")
    database.migrate()
    return MemoryStore(database, config.root)


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
        store = _memory_store(project_root)
        types = (record_type,) if record_type else ()
        records = store.search(
            query,
            record_types=types,
            statuses=(status,),
            limit=limit,
        )
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
        store = _memory_store(project_root)
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
        store = _memory_store(project_root)
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
    envelope = success_envelope(data) if ok else error_envelope(
        "MEMORY_JSONL_INVALID", "memory.jsonl contains invalid lines", data
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
        store = _memory_store(project_root)
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


@app.command("mcp")
def mcp_command() -> None:
    """Run the bundled Model Context Protocol server over stdio."""

    from codex_ai_os.cli.mcp_server import run_server

    run_server()


def main() -> None:
    configure_utf8_stdio()
    app()


if __name__ == "__main__":
    main()
