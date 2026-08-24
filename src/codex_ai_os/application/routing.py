"""Deterministic runtime Profile routing and task blueprint generation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from codex_ai_os.domain.config import ProjectConfig, ProjectType
from codex_ai_os.domain.coordination import TaskBlueprint
from codex_ai_os.domain.ids import new_id
from codex_ai_os.infrastructure.database import Database


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    profiles: tuple[str, ...]
    tasks: tuple[TaskBlueprint, ...]
    rationale: str
    decision_hash: str


class ProfileRouter:
    """Choose the smallest built-in implementation team for an approved impact."""

    ALLOWED_PROFILES = frozenset({"backend", "frontend", "large"})

    def __init__(self, database: Database, config: ProjectConfig) -> None:
        self.database = database
        self.config = config

    def route(
        self,
        *,
        run_id: str,
        requested_profiles: tuple[str, ...] = (),
        impact_paths: tuple[str, ...] = (),
        source_commit: str | None = None,
    ) -> RoutingDecision:
        profiles = self._select_profiles(requested_profiles, impact_paths)
        tasks = self._tasks(profiles)
        rationale = (
            f"project_type={self.config.project_type.value}; profiles={','.join(profiles)}; "
            f"impact_paths={len(impact_paths)}; max_parallel={self.config.max_parallel_agents}"
        )
        payload = {
            "profiles": profiles,
            "tasks": [task.model_dump(mode="json") for task in tasks],
            "rationale": rationale,
            "source_commit": source_commit,
        }
        decision_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        decision = RoutingDecision(profiles, tasks, rationale, decision_hash)
        self._persist(run_id, impact_paths, source_commit, decision)
        return decision

    def _select_profiles(
        self, requested: tuple[str, ...], impact_paths: tuple[str, ...]
    ) -> tuple[str, ...]:
        if requested:
            unknown = set(requested) - self.ALLOWED_PROFILES
            if unknown:
                raise ValueError(f"unknown profiles: {sorted(unknown)}")
            selected = list(dict.fromkeys(requested))
        elif self.config.project_type is ProjectType.FRONTEND:
            selected = ["frontend"]
        elif self.config.project_type is ProjectType.FULLSTACK:
            selected = ["backend", "frontend"]
        else:
            selected = ["backend"]
        if len(impact_paths) >= 20 and "large" not in selected:
            selected.append("large")
        return tuple(selected[:4])

    @staticmethod
    def _tasks(profiles: tuple[str, ...]) -> tuple[TaskBlueprint, ...]:
        tasks: list[TaskBlueprint] = []
        if "backend" in profiles:
            tasks.append(
                TaskBlueprint(
                    key="backend-implementation",
                    agent="backend-engineer",
                    skill="backend-implementation",
                    prompt="Implement the approved backend application and persistence slice.",
                    allowed_paths=(
                        "src/codex_ai_os/application/",
                        "src/codex_ai_os/infrastructure/",
                        "src/codex_ai_os/adapters/",
                        "src/codex_ai_os/domain/",
                        "src/codex_ai_os/cli/",
                        "tests/backend/",
                        "pyproject.toml",
                        "uv.lock",
                    ),
                )
            )
        if "frontend" in profiles:
            tasks.append(
                TaskBlueprint(
                    key="frontend-implementation",
                    agent="frontend-engineer",
                    skill="frontend-implementation",
                    prompt="Implement the approved accessible frontend slice.",
                    allowed_paths=("src/codex_ai_os/frontend/", "tests/frontend/"),
                )
            )
        if "large" in profiles:
            tasks.append(
                TaskBlueprint(
                    key="database-migrations",
                    agent="database-engineer",
                    skill="database-design",
                    prompt="Implement the approved additive migrations and recovery tests.",
                    allowed_paths=(
                        "src/codex_ai_os/infrastructure/migrations/",
                        "tests/migrations/",
                    ),
                )
            )
            tasks.append(
                TaskBlueprint(
                    key="implementation-tests",
                    agent="qa",
                    skill="testing",
                    prompt="Implement independent integration and governance regression tests.",
                    allowed_paths=("tests/integration/", "tests/governance/"),
                )
            )
        return tuple(tasks)

    def _persist(
        self,
        run_id: str,
        impact_paths: tuple[str, ...],
        source_commit: str | None,
        decision: RoutingDecision,
    ) -> None:
        with self.database.connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO routing_decisions(
                        id, project_id, run_id, profiles_json, project_type,
                        impact_json, rationale, source_commit, decision_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id("ROUTING"),
                        self.config.project_id,
                        run_id,
                        json.dumps(decision.profiles),
                        self.config.project_type.value,
                        json.dumps(impact_paths),
                        decision.rationale,
                        source_commit,
                        decision.decision_hash,
                        datetime.now(UTC).isoformat(),
                    ),
                )
                connection.commit()
            except sqlite3.Error:
                connection.rollback()
                raise
