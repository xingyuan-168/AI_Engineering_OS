"""Model Context Protocol server for AI Engineering OS (governance-core surface).

Exposes a small set of deterministic governance tools (ADR-0016). The MCP
server is a governance capability for Codex — not an operating-system API.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.application.repository import RepositoryGovernanceService
from codex_ai_os.domain.config import ProjectType, RiskLevel
from codex_ai_os.domain.versions import RUNTIME_VERSIONS
from codex_ai_os.infrastructure.config import ConfigError, load_project_config
from codex_ai_os.infrastructure.database import Database, MigrationError
from codex_ai_os.infrastructure.documents import DocumentManager
from codex_ai_os.infrastructure.memory import MemoryStore, MemoryStoreError
from codex_ai_os.infrastructure.path_codec import configure_utf8_stdio

mcp = MCPServer(
    "AI Engineering OS",
    version=RUNTIME_VERSIONS.software,
    instructions=(
        "Governance tools for Codex. Call governance-relevant checks at task start "
        "and finish; keep Codex's native engineering workflow. These tools never "
        "replace Codex's own engineering capabilities."
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
    """Initialize an idempotent local project, minimal documents, and runtime database."""

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
def repository_check(project_root: str, target_branch: str | None = None) -> dict[str, Any]:
    """Validate GitHub readiness, repository hygiene, and blocking findings."""

    def operation() -> dict[str, Any]:
        report = RepositoryGovernanceService(Path(project_root)).check(
            target_branch=target_branch
        )
        return _success(**report.model_dump(mode="json"))

    return _invoke(operation)


@mcp.tool()
def docs_check(project_root: str) -> dict[str, Any]:
    """Check required documents, headings, local links, and copy directories."""

    def operation() -> dict[str, Any]:
        config = load_project_config(Path(project_root).resolve())
        report = DocumentManager(config.root).check(
            config.project_type.value,
            expected_document_version=config.document_version,
        )
        return _success(**report.model_dump(mode="json"))

    return _invoke(operation)


@mcp.tool()
def memory_search(
    project_root: str,
    query: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    """Search active project Memory records."""

    def operation() -> dict[str, Any]:
        config = load_project_config(Path(project_root).resolve())
        database = Database(config.root / ".codex-os" / "state" / "state.db")
        database.migrate()
        store = MemoryStore(database, config.root, config.project_id)
        records = store.search(query, statuses=("active",), limit=limit)
        return _success(
            results=[
                {
                    "id": record.id,
                    "record_type": record.record_type,
                    "title": record.title,
                    "content_ref": record.content_ref,
                    "tags": list(record.tags),
                }
                for record in records
            ]
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
        MemoryStoreError,
        MigrationError,
        ValueError,
        OSError,
    ) as exc:
        return {"ok": False, "error": {"code": "GOVERNANCE_CHECK_FAILED", "message": str(exc)}}
    except Exception as exc:  # pragma: no cover - defensive envelope
        return {"ok": False, "error": {"code": "INTERNAL_ERROR", "message": str(exc)}}


if __name__ == "__main__":
    run_server()
