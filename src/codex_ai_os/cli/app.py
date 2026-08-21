"""Typer command surface for AI Engineering OS."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from codex_ai_os.application.doctor import DoctorService
from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.cli.output import emit, error_envelope, success_envelope
from codex_ai_os.domain.config import ProjectType, RiskLevel
from codex_ai_os.infrastructure.config import ConfigError, load_project_config
from codex_ai_os.infrastructure.database import Database, MigrationError
from codex_ai_os.infrastructure.documents import DocumentManager

app = typer.Typer(
    name="codex-os",
    help="Local, auditable engineering workflow runtime for Codex.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)


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
    }
    emit(
        success_envelope(data),
        json_output=json_output,
        human=f"Initialized {result.config.project_id} at {result.config.root}",
    )


@app.command("status")
def status_command(
    project_root: Annotated[Path, typer.Argument(help="Project directory.")] = Path("."),
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON only.")] = False,
) -> None:
    """Show project runtime and document status."""

    try:
        config = load_project_config(project_root.resolve())
        database = Database(config.root / ".codex-os" / "state" / "state.db")
        version = database.current_version()
        report = DocumentManager(config.root).check(config.project_type.value)
        with database.connection() as connection:
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


def _fail(code: str, message: str, exit_code: int, json_output: bool) -> None:
    emit(
        error_envelope(code, message, {}),
        json_output=json_output,
        human=f"{code}: {message}",
    )
    raise typer.Exit(code=exit_code)
