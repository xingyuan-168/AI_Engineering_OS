"""Additive project profiles used to compose governed workflows."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Self

from pydantic import Field, field_validator, model_validator

from codex_ai_os.domain.config import ProjectType, StrictModel


class ProjectProfile(StrictModel):
    schema_version: str = Field(pattern=r"^1\.[01]$")
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")
    description: str = Field(min_length=1, max_length=240)
    project_types: tuple[ProjectType, ...]
    additional_skills: tuple[str, ...]
    required_artifacts: tuple[str, ...]
    additional_reviewers: tuple[str, ...]
    min_agents: int = Field(ge=1, le=20)
    approval_required: bool = False

    @field_validator("additional_skills", "additional_reviewers")
    @classmethod
    def identifiers_are_safe(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values or len(values) != len(set(values)):
            raise ValueError("profile identifiers must be non-empty and unique")
        if not all(re.fullmatch(r"[a-z][a-z0-9-]{1,63}", value) for value in values):
            raise ValueError("profile identifiers must use lowercase kebab-case")
        return values

    @field_validator("required_artifacts")
    @classmethod
    def artifact_paths_are_safe(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values or len(values) != len(set(values)):
            raise ValueError("required artifacts must be non-empty and unique")
        for value in values:
            path = Path(value.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"profile artifact path is unsafe: {value}")
        return values

    @model_validator(mode="after")
    def project_types_are_unique(self) -> Self:
        if not self.project_types or len(self.project_types) != len(set(self.project_types)):
            raise ValueError("project_types must be non-empty and unique")
        return self
