"""Read-only shared Start/Finish/Memory configuration readiness."""

from dataclasses import dataclass
from pathlib import Path

from codex_ai_os.domain.config import ProjectConfig
from codex_ai_os.infrastructure.config import (
    ResolvedRoot,
    load_project_config,
    resolve_runtime_root,
)


@dataclass(frozen=True)
class ProjectPreflight:
    resolved: ResolvedRoot
    config: ProjectConfig

    def report(self) -> dict[str, object]:
        root = self.resolved.project_root
        return {
            "configuration": str(root / ".codex-os/project.yaml"),
            "schema_version": self.config.schema_version,
            "checkout_root": str(self.resolved.checkout_root),
            "coordinator_root": str(root),
            "consumers": {"start": "ready", "finish": "ready", "memory": "ready"},
            "memory_facts": "present"
            if (root / "docs/memory/memory.jsonl").is_file()
            else "not_initialized",
        }


def preflight(root: Path) -> ProjectPreflight:
    resolved = resolve_runtime_root(root)
    return ProjectPreflight(resolved, load_project_config(resolved.project_root))
