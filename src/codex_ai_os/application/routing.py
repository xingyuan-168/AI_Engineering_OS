"""Deterministic runtime Profile routing and task blueprint generation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar, cast

from codex_ai_os.application.governance_policy import (
    GovernancePolicyCompiler,
    GovernancePolicyError,
)
from codex_ai_os.domain.config import ProjectConfig, ProjectType
from codex_ai_os.domain.coordination import TaskBlueprint
from codex_ai_os.domain.ids import new_id
from codex_ai_os.infrastructure.database import Database
from codex_ai_os.infrastructure.profiles import load_project_profiles


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    requested_profiles: tuple[str, ...]
    profiles: tuple[str, ...]
    tasks: tuple[TaskBlueprint, ...]
    dimension_scores: dict[str, int]
    rationale: str
    decision_hash: str
    policy_hash: str
    override: bool = False
    dependencies: tuple[tuple[str, str], ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RoutingInput:
    project_type: str
    workflow_name: str
    risk_level: str
    requested_profiles: tuple[str, ...]
    impact_paths: tuple[str, ...]
    dependency_count: int
    release_required: bool
    override_reason: str | None = None


class ProfileRouter:
    """Choose the smallest built-in implementation team for an approved impact."""

    DEFAULT_PROFILE_NAMES: ClassVar[frozenset[str]] = frozenset(
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
        self.allowed_profiles = self._load_allowed_profiles()

    def route(
        self,
        *,
        run_id: str,
        requested_profiles: tuple[str, ...] = (),
        impact_paths: tuple[str, ...] = (),
        source_commit: str | None = None,
        workflow_name: str = "new-project",
        dependency_count: int = 0,
        release_required: bool = False,
        override_reason: str | None = None,
        require_task_mapping: bool = False,
    ) -> RoutingDecision:
        profiles = self._select_profiles(requested_profiles, impact_paths)
        compiler = GovernancePolicyCompiler(self.config.root)
        try:
            policy = compiler.compile(profiles)
            tasks = (
                compiler.task_blueprints(profiles, impact_paths)
                if impact_paths or require_task_mapping
                else ()
            )
        except GovernancePolicyError as exc:
            raise ValueError(f"{exc.code}: {exc}") from exc
        warnings = self._warnings_for_aliases(requested_profiles)
        evidence_score = min(
            10,
            sum(
                len(requirements.artifacts)
                + len(requirements.artifact_types)
                + len(requirements.checks)
                + len(requirements.reviews)
                + len(requirements.records)
                for requirements in policy.gates.values()
            )
            // 5,
        )
        scores = {
            "workflow": _score_for_workflow(workflow_name),
            "risk": _score_for_risk(self.config.risk_level.value),
            "profile": min(10, len(profiles) * 3),
            "impact": min(10, len(impact_paths)),
            "dependencies": min(10, dependency_count),
            "evidence": evidence_score,
            "release": 10 if release_required else 0,
        }
        dependencies = tuple(
            (dependency, task.key) for task in tasks for dependency in task.depends_on
        )
        rationale = (
            f"project_type={self.config.project_type.value}; profiles={','.join(profiles)}; "
            f"impact_paths={len(impact_paths)}; dependencies={len(dependencies)}; "
            f"max_parallel={self.config.max_parallel_agents}; policy={policy.policy_hash}"
        )
        if override_reason:
            rationale = f"{rationale}; override={override_reason.strip()}"
        if warnings:
            rationale = f"{rationale}; warnings={'; '.join(warnings)}"
        payload = {
            "requested_profiles": requested_profiles,
            "profiles": profiles,
            "tasks": [task.model_dump(mode="json") for task in tasks],
            "dimension_scores": scores,
            "rationale": rationale,
            "source_commit": source_commit,
            "warnings": warnings,
            "workflow_name": workflow_name,
            "policy_hash": policy.policy_hash,
            "override": bool(override_reason),
            "dependencies": dependencies,
        }
        decision_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        decision = RoutingDecision(
            requested_profiles=requested_profiles,
            profiles=profiles,
            tasks=tasks,
            dimension_scores=scores,
            rationale=rationale,
            decision_hash=decision_hash,
            policy_hash=policy.policy_hash,
            override=bool(override_reason),
            dependencies=dependencies,
            warnings=warnings,
        )
        self._persist(
            run_id,
            requested_profiles,
            impact_paths,
            source_commit,
            workflow_name,
            dependency_count,
            release_required,
            override_reason,
            decision,
        )
        return decision

    def input_for_run(self, run_id: str) -> RoutingInput:
        with self.database.read_connection() as connection:
            row = connection.execute(
                """
                SELECT routing_input_json FROM routing_decisions
                WHERE run_id = ? ORDER BY created_at DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            raise ValueError("routing decision is missing")
        parsed: object = json.loads(str(row["routing_input_json"]))
        if not isinstance(parsed, dict):
            raise ValueError("routing input is invalid")
        raw = cast(dict[str, Any], parsed)
        requested = _string_tuple(raw.get("requested_profiles"), "requested_profiles")
        impacts = _string_tuple(raw.get("impact_paths"), "impact_paths")
        return RoutingInput(
            project_type=str(raw.get("project_type") or ""),
            workflow_name=str(raw.get("workflow_name") or ""),
            risk_level=str(raw.get("risk_level") or ""),
            requested_profiles=requested,
            impact_paths=impacts,
            dependency_count=int(raw.get("dependency_count") or 0),
            release_required=bool(raw.get("release_required", False)),
            override_reason=(
                str(raw["override_reason"]) if raw.get("override_reason") is not None else None
            ),
        )

    def select_profiles(
        self, requested: tuple[str, ...], impact_paths: tuple[str, ...]
    ) -> tuple[str, ...]:
        return self._select_profiles(requested, impact_paths)

    def _select_profiles(
        self, requested: tuple[str, ...], impact_paths: tuple[str, ...]
    ) -> tuple[str, ...]:
        canonical_requested = tuple(self._canonical_profile(item) for item in requested)
        if requested:
            unknown = set(canonical_requested) - self.allowed_profiles
            if unknown:
                raise ValueError(f"unknown profiles: {sorted(unknown)}")
            selected = list(dict.fromkeys(canonical_requested))
        elif self.config.project_type is ProjectType.FRONTEND:
            selected = ["frontend-project"]
        elif self.config.project_type is ProjectType.FULLSTACK:
            selected = ["backend-project", "frontend-project"]
        else:
            selected = ["backend-project"]
        frontend_required = self.config.project_type in {
            ProjectType.FRONTEND,
            ProjectType.FULLSTACK,
        } or any(self._frontend_impact(path) for path in impact_paths)
        if frontend_required and "frontend-project" not in selected:
            selected.append("frontend-project")
        if (
            len(impact_paths) >= 20
            and "large-project" in self.allowed_profiles
            and "large-project" not in selected
        ):
            selected.append("large-project")
        return tuple(selected[:4])

    def _canonical_profile(self, profile: str) -> str:
        return self.PROFILE_ALIASES.get(profile, profile)

    @staticmethod
    def _frontend_impact(value: str) -> bool:
        normalized = value.replace("\\", "/").casefold().strip("/")
        parts = set(normalized.split("/"))
        return bool(parts & {"frontend", "ui", "web"}) or normalized.endswith(
            (".html", ".css", ".jsx", ".tsx", ".vue", ".svelte")
        )

    def _load_allowed_profiles(self) -> frozenset[str]:
        profile_dir = self.config.root / "profiles"
        if not profile_dir.is_dir():
            return self.DEFAULT_PROFILE_NAMES
        return frozenset(load_project_profiles(profile_dir))

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
        dependency_count: int,
        release_required: bool,
        override_reason: str | None,
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
                                "workflow_name": workflow_name,
                                "risk_level": self.config.risk_level.value,
                                "requested_profiles": list(requested_profiles),
                                "impact_paths": list(impact_paths),
                                "dependency_count": dependency_count,
                                "release_required": release_required,
                                "override_reason": override_reason,
                                "policy_hash": decision.policy_hash,
                                "override": decision.override,
                                "dependencies": list(decision.dependencies),
                            },
                            sort_keys=True,
                        ),
                        json.dumps(decision.dimension_scores, sort_keys=True),
                        float(sum(decision.dimension_scores.values()) / 7),
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


def _score_for_workflow(workflow_name: str) -> int:
    return {
        "bug-fix": 3,
        "feature-development": 6,
        "new-project": 8,
        "release": 10,
    }.get(workflow_name, 5)


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"routing input {field} is invalid")
    items = cast(list[object], value)
    if not all(isinstance(item, str) for item in items):
        raise ValueError(f"routing input {field} is invalid")
    return tuple(cast(str, item) for item in items)
