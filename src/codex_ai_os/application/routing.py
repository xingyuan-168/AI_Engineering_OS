"""Deterministic runtime Profile routing and task blueprint generation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar

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
    warnings: tuple[str, ...] = ()


class ProfileRouter:
    """Choose the smallest built-in implementation team for an approved impact."""

    ALLOWED_PROFILES: ClassVar[frozenset[str]] = frozenset(
        {"backend-project", "frontend-project", "large-project"}
    )
    PROFILE_ALIASES: ClassVar[dict[str, str]] = {
        "backend": "backend-project",
        "frontend": "frontend-project",
        "large": "large-project",
    }

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
        workflow_name: str = "new-project",
    ) -> RoutingDecision:
        profiles = self._select_profiles(requested_profiles, impact_paths)
        tasks = self._tasks(profiles)
        warnings = self._warnings_for_aliases(requested_profiles)
        rationale = (
            f"project_type={self.config.project_type.value}; profiles={','.join(profiles)}; "
            f"impact_paths={len(impact_paths)}; max_parallel={self.config.max_parallel_agents}"
        )
        if warnings:
            rationale = f"{rationale}; warnings={'; '.join(warnings)}"
        payload = {
            "profiles": profiles,
            "tasks": [task.model_dump(mode="json") for task in tasks],
            "rationale": rationale,
            "source_commit": source_commit,
            "warnings": warnings,
            "workflow_name": workflow_name,
        }
        decision_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        decision = RoutingDecision(profiles, tasks, rationale, decision_hash, warnings)
        self._persist(
            run_id, requested_profiles, impact_paths, source_commit, workflow_name, decision
        )
        return decision

    def select_profiles(
        self, requested: tuple[str, ...], impact_paths: tuple[str, ...]
    ) -> tuple[str, ...]:
        return self._select_profiles(requested, impact_paths)

    def _select_profiles(
        self, requested: tuple[str, ...], impact_paths: tuple[str, ...]
    ) -> tuple[str, ...]:
        canonical_requested = tuple(self._canonical_profile(item) for item in requested)
        if requested:
            unknown = set(canonical_requested) - self.ALLOWED_PROFILES
            if unknown:
                raise ValueError(f"unknown profiles: {sorted(unknown)}")
            selected = list(dict.fromkeys(canonical_requested))
        elif self.config.project_type is ProjectType.FRONTEND:
            selected = ["frontend-project"]
        elif self.config.project_type is ProjectType.FULLSTACK:
            selected = ["backend-project", "frontend-project"]
        else:
            selected = ["backend-project"]
        if len(impact_paths) >= 20 and "large-project" not in selected:
            selected.append("large-project")
        return tuple(selected[:4])

    @staticmethod
    def _tasks(profiles: tuple[str, ...]) -> tuple[TaskBlueprint, ...]:
        tasks: list[TaskBlueprint] = []
        if "backend-project" in profiles:
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
        if "frontend-project" in profiles:
            tasks.append(
                TaskBlueprint(
                    key="frontend-implementation",
                    agent="frontend-engineer",
                    skill="frontend-implementation",
                    prompt="Implement the approved accessible frontend slice.",
                    allowed_paths=("src/codex_ai_os/frontend/", "tests/frontend/"),
                )
            )
        if "large-project" in profiles:
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

    def _canonical_profile(self, profile: str) -> str:
        return self.PROFILE_ALIASES.get(profile, profile)

    def _warnings_for_aliases(self, requested: tuple[str, ...]) -> tuple[str, ...]:
        aliases = tuple(dict.fromkeys(item for item in requested if item in self.PROFILE_ALIASES))
        if not aliases:
            return ()
        return tuple(
            f"profile alias '{alias}' is deprecated; use '{self.PROFILE_ALIASES[alias]}'"
            for alias in aliases
        )

    def _persist(
        self,
        run_id: str,
        requested_profiles: tuple[str, ...],
        impact_paths: tuple[str, ...],
        source_commit: str | None,
        workflow_name: str,
        decision: RoutingDecision,
    ) -> None:
        with self.database.connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO routing_decisions(
                        id, project_id, run_id, profiles_json, project_type,
                        impact_json, rationale, source_commit, decision_hash,
                        routing_input_json, dimension_scores_json, total_score,
                        risk_level, workflow, canonical_profiles_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        json.dumps(
                            {
                                "project_type": self.config.project_type.value,
                                "requested_profiles": list(requested_profiles),
                                "impact_paths": list(impact_paths),
                            },
                            sort_keys=True,
                        ),
                        json.dumps(
                            {
                                "workflow": 5,
                                "risk": _score_for_risk(self.config.risk_level.value),
                                "profile": len(decision.profiles),
                                "impact": min(10, len(impact_paths)),
                                "dependencies": 0,
                                "evidence": 5,
                                "release": 0,
                            },
                            sort_keys=True,
                        ),
                        float(min(10, 5 + len(decision.profiles))),
                        self.config.risk_level.value,
                        workflow_name,
                        json.dumps(decision.profiles),
                        datetime.now(UTC).isoformat(),
                    ),
                )
                connection.commit()
            except sqlite3.Error:
                connection.rollback()
                raise


def _score_for_risk(risk_level: str) -> int:
    return {"low": 2, "medium": 5, "high": 8, "critical": 10}.get(risk_level, 5)
