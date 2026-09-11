"""Strict configuration models (governance-core, slimmed by ADR-0016).

The project configuration keeps only the fields the runtime still reads:
identity, template selection (project_type), push policy, target branch,
GitHub hosts, and the formal code paths the Code Start boundary protects.
Execution-policy and risk tiers are gone: there is no second runtime to
configure, and the hook judges operations directly (ADR-0016).
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    """Base model that rejects unknown configuration fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ProjectType(StrEnum):
    BACKEND = "backend"
    FRONTEND = "frontend"
    FULLSTACK = "fullstack"
    DESKTOP = "desktop"
    GENERIC = "generic"


class GitPushPolicy(StrEnum):
    REMOTE_REQUIRED = "remote_required"
    FIXTURE_LOCAL_ONLY = "fixture_local_only"


class ProjectConfig(StrictModel):
    schema_version: Literal["1.0", "1.1", "1.2"] = "1.2"
    project_id: str = Field(pattern=r"^PROJECT-[A-Z0-9][A-Z0-9-]*$")
    name: str = Field(min_length=1, max_length=100)
    root: Path
    project_type: ProjectType = ProjectType.GENERIC
    source_of_truth: Path = Path("docs")
    git_push_policy: GitPushPolicy = GitPushPolicy.REMOTE_REQUIRED
    target_branch: str = Field(default="main", pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
    github_hosts: frozenset[str] = frozenset({"github.com"})
    code_paths: tuple[str, ...] = ("src",)

    @field_validator("root")
    @classmethod
    def root_must_exist(cls, value: Path) -> Path:
        resolved = value.resolve()
        if not resolved.is_dir():
            raise ValueError("project root must be an existing directory")
        return resolved

    @model_validator(mode="after")
    def source_must_stay_inside_root(self) -> Self:
        source = self.source_of_truth
        candidate = source.resolve() if source.is_absolute() else (self.root / source).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError("source_of_truth must stay inside project root")
        object.__setattr__(self, "source_of_truth", candidate)
        return self

    @field_validator("github_hosts")
    @classmethod
    def github_hosts_are_normalized(cls, values: frozenset[str]) -> frozenset[str]:
        if not values:
            raise ValueError("github_hosts cannot be empty")
        normalized = frozenset(value.strip().casefold() for value in values)
        if any(
            not value
            or "/" in value
            or "\\" in value
            or ":" in value
            or value.startswith(".")
            for value in normalized
        ):
            raise ValueError("github_hosts must contain host names only")
        return normalized

    @field_validator("code_paths")
    @classmethod
    def code_paths_are_repository_relative(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values:
            raise ValueError("code_paths cannot be empty")
        for value in values:
            normalized = value.replace("\\", "/").strip("/")
            if (
                not normalized
                or normalized.startswith(".")
                or ":" in normalized
                or normalized.startswith("/")
            ):
                raise ValueError(f"code_paths entries must be relative repository paths: {value!r}")
        return tuple(dict.fromkeys(value.replace("\\", "/").strip("/") for value in values))
