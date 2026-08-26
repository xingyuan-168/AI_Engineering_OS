"""Strict configuration models and security policy types."""

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


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class NetworkMode(StrEnum):
    DISABLED = "disabled"
    ALLOWLIST = "allowlist"


class GitPushPolicy(StrEnum):
    REMOTE_REQUIRED = "remote_required"
    FIXTURE_LOCAL_ONLY = "fixture_local_only"


class SandboxBackend(StrEnum):
    DOCKER = "docker"
    PODMAN = "podman"


class ProjectConfig(StrictModel):
    schema_version: Literal["1.0", "1.1", "1.2"] = "1.2"
    project_id: str = Field(pattern=r"^PROJECT-[A-Z0-9][A-Z0-9-]*$")
    name: str = Field(min_length=1, max_length=100)
    root: Path
    project_type: ProjectType = ProjectType.GENERIC
    risk_level: RiskLevel = RiskLevel.MEDIUM
    source_of_truth: Path = Path("docs")
    active_workflow: str = "new-project"
    approval_policy: Literal["critical-gates-human"] = "critical-gates-human"
    default_agent_profile: str = "standard"
    git_push_policy: GitPushPolicy = GitPushPolicy.REMOTE_REQUIRED
    target_branch: str = Field(default="main", pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
    github_hosts: frozenset[str] = frozenset({"github.com"})
    max_parallel_agents: int = Field(default=4, ge=1, le=4)

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


class ExecutionPolicy(StrictModel):
    schema_version: Literal["1.0", "1.1", "1.2"] = "1.2"
    sandbox: SandboxBackend = SandboxBackend.DOCKER
    network: NetworkMode = NetworkMode.DISABLED
    allowed_network_hosts: frozenset[str] = frozenset()
    allowed_mounts: frozenset[str] = frozenset({"worktree", "artifacts", "cache"})
    allowed_commands: frozenset[str] = frozenset(
        {"git", "python", "pytest", "ruff", "pyright", "pip-audit", "detect-secrets"}
    )
    approval_for: frozenset[str] = frozenset(
        {"network", "migration", "delete", "credential", "release"}
    )
    max_duration_seconds: int = Field(default=1800, ge=1, le=7200)
    allow_host_execution: bool = False


DEFAULT_EXECUTION_POLICY = ExecutionPolicy()
