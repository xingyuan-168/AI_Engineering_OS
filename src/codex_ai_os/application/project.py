"""Project initialization use case."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from codex_ai_os.domain.config import GitPushPolicy, ProjectConfig, ProjectType, RiskLevel
from codex_ai_os.domain.versions import RUNTIME_VERSIONS
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
    repository_ready: bool
    repository_blockers: tuple[str, ...]


class ProjectInitializer:
    def initialize(
        self,
        project_root: Path,
        *,
        project_id: str,
        name: str,
        project_type: ProjectType,
        risk_level: RiskLevel = RiskLevel.MEDIUM,
        git_push_policy: GitPushPolicy = GitPushPolicy.REMOTE_REQUIRED,
        schema_version: Literal["1.0", "1.1", "1.2"] = "1.2",
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
                schema_version=schema_version,
                project_id=project_id,
                name=name,
                root=root,
                project_type=project_type,
                risk_level=risk_level,
                git_push_policy=git_push_policy,
                document_version="0.1.0",
            )
            config_text = yaml.safe_dump(
                _serializable_config(config),
                allow_unicode=True,
                sort_keys=False,
            )
            if documents.write_atomic(".codex-os/project.yaml", config_text, overwrite=False):
                created.append(".codex-os/project.yaml")

        for relative, content in _runtime_entry_files().items():
            if documents.write_atomic(relative, content, overwrite=False):
                created.append(relative)

        created.extend(
            documents.initialize_documents(
                config.name,
                config.project_type.value,
                document_version=config.document_version or RUNTIME_VERSIONS.software,
            )
        )
        context_path = documents.generate_context()

        database_path = root / ".codex-os" / "state" / "state.db"
        database = Database(database_path)
        database.migrate(app_version=RUNTIME_VERSIONS.software)
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

        report = documents.check(
            config.project_type.value,
            expected_document_version=config.document_version,
        )
        repository_ready, repository_blockers = self._repository_readiness(root, config)
        return ProjectInitResult(
            config=config,
            created_paths=tuple(created),
            context_path=context_path,
            document_report=report,
            database_path=database_path,
            repository_ready=repository_ready,
            repository_blockers=repository_blockers,
        )

    @staticmethod
    def _repository_readiness(root: Path, config: ProjectConfig) -> tuple[bool, tuple[str, ...]]:
        if config.git_push_policy is GitPushPolicy.FIXTURE_LOCAL_ONLY:
            return True, ()
        git_marker = root / ".git"
        if not git_marker.exists():
            return False, ("NOT_GIT_REPOSITORY",)
        return False, ("REPOSITORY_CHECK_REQUIRED",)


def _serializable_config(config: ProjectConfig) -> dict[str, object]:
    data = config.model_dump(mode="json")
    data["root"] = "."
    data["source_of_truth"] = config.source_of_truth.relative_to(config.root).as_posix()
    return {str(key): value for key, value in data.items()}


def _runtime_entry_files() -> dict[str, str]:
    files = {
        ".gitignore": (
            ".codex-os/state/\n.codex-os/logs/\n.codex-os/cache/\n"
            ".codex-os/context/\n.codex-os/tmp/\n.codex-os/artifacts/\n.worktrees/\n"
            ".venv/\n__pycache__/\n*.py[cod]\n.pytest_cache/\n.ruff_cache/\n"
            ".env\nnode_modules/\ndist/\nbuild/\n*.log\n"
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
        ".codex-os/execution-policy.yaml": (
            f"schema_version: '{RUNTIME_VERSIONS.config_schema}'\n"
            "sandbox: docker\n"
            "network: disabled\n"
            "allowed_network_hosts: []\n"
            "allowed_mounts: [worktree, artifacts, cache]\n"
            "allowed_commands: [git, python, pytest, ruff, pyright, pip-audit, detect-secrets]\n"
            "approval_for: [network, migration, delete, credential, release]\n"
            "max_duration_seconds: 1800\n"
            "allow_host_execution: false\n"
        ),
    }
    packaged = Path(__file__).parents[1] / "resources" / "gates"
    repository = Path(__file__).parents[3] / "gates"
    gate_root = packaged if packaged.is_dir() else repository
    for gate in ("G0", "G1", "G2", "G3", "G4"):
        files[f".codex-os/gates/{gate}.yaml"] = (gate_root / f"{gate}.yaml").read_text(
            encoding="utf-8"
        )
    return files
