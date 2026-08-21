"""Project initialization use case."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from codex_ai_os.domain.config import ProjectConfig, ProjectType, RiskLevel
from codex_ai_os.infrastructure.config import load_project_config
from codex_ai_os.infrastructure.database import Database
from codex_ai_os.infrastructure.documents import DocumentCheckReport, DocumentManager
from codex_ai_os.infrastructure.events import EventStore
from codex_ai_os.infrastructure.projects import ProjectStore


@dataclass(frozen=True, slots=True)
class ProjectInitResult:
    config: ProjectConfig
    created_paths: tuple[str, ...]
    context_path: Path
    document_report: DocumentCheckReport
    database_path: Path


class ProjectInitializer:
    def initialize(
        self,
        project_root: Path,
        *,
        project_id: str,
        name: str,
        project_type: ProjectType,
        risk_level: RiskLevel = RiskLevel.MEDIUM,
    ) -> ProjectInitResult:
        root = project_root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        documents = DocumentManager(root)
        config_path = root / ".codex-os" / "project.yaml"
        created: list[str] = []

        if config_path.is_file():
            config = load_project_config(root)
        else:
            config = ProjectConfig(
                project_id=project_id,
                name=name,
                root=root,
                project_type=project_type,
                risk_level=risk_level,
            )
            config_text = yaml.safe_dump(
                _serializable_config(config),
                allow_unicode=True,
                sort_keys=False,
            )
            if documents.write_atomic(
                ".codex-os/project.yaml", config_text, overwrite=False
            ):
                created.append(".codex-os/project.yaml")

        for relative, content in _runtime_entry_files().items():
            if documents.write_atomic(relative, content, overwrite=False):
                created.append(relative)

        created.extend(documents.initialize_documents(config.name, config.project_type.value))
        context_path = documents.generate_context()

        database_path = root / ".codex-os" / "state" / "state.db"
        database = Database(database_path)
        database.migrate()
        config_hash = hashlib.sha256(
            json.dumps(_serializable_config(config), sort_keys=True).encode("utf-8")
        ).hexdigest()
        ProjectStore(database).register(config, config_hash)
        EventStore(database).append(
            project_id=config.project_id,
            event_type="project.initialized",
            payload={"created_paths": sorted(created), "config_hash": config_hash},
            idempotency_key=f"project.initialize:{config.project_id}:{config_hash}",
        )

        report = documents.check(config.project_type.value)
        return ProjectInitResult(
            config=config,
            created_paths=tuple(created),
            context_path=context_path,
            document_report=report,
            database_path=database_path,
        )


def _serializable_config(config: ProjectConfig) -> dict[str, object]:
    data = config.model_dump(mode="json")
    return {str(key): value for key, value in data.items()}


def _runtime_entry_files() -> dict[str, str]:
    return {
        ".gitignore": (
            ".codex-os/state/\n.codex-os/logs/\n.codex-os/cache/\n"
            ".codex-os/context/\n.codex-os/tmp/\n.worktrees/\n.env\n"
        ),
        ".codex/hooks.json": json.dumps(
            {
                "description": "AI Engineering OS project hooks",
                "hooks": {},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    }
