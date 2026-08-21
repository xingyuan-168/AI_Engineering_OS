"""YAML loading and safety-preserving configuration merge."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel, ValidationError

from codex_ai_os.domain.config import (
    DEFAULT_EXECUTION_POLICY,
    ExecutionPolicy,
    NetworkMode,
    ProjectConfig,
)


class ConfigError(ValueError):
    """Raised when a configuration file is invalid or unsafe."""


def load_yaml_model[ModelT: BaseModel](path: Path, model: type[ModelT]) -> ModelT:
    """Load one UTF-8 YAML mapping into a strict Pydantic model."""

    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read configuration {path}: {exc}") from exc

    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ConfigError(f"configuration {path} must contain a YAML mapping")

    try:
        return model.model_validate(cast(dict[str, object], raw))
    except ValidationError as exc:
        raise ConfigError(f"invalid configuration {path}: {exc}") from exc


def load_project_config(project_root: Path) -> ProjectConfig:
    return load_yaml_model(project_root / ".codex-os" / "project.yaml", ProjectConfig)


def load_execution_policy(project_root: Path) -> ExecutionPolicy:
    path = project_root / ".codex-os" / "execution-policy.yaml"
    if not path.is_file():
        return DEFAULT_EXECUTION_POLICY
    return load_yaml_model(path, ExecutionPolicy)


def merge_execution_policy(
    base: ExecutionPolicy,
    override: Mapping[str, Any],
) -> ExecutionPolicy:
    """Apply a project/workflow override without relaxing security."""

    unknown = set(override) - set(ExecutionPolicy.model_fields)
    if unknown:
        raise ConfigError(f"unknown execution policy fields: {sorted(unknown)}")

    candidate_data = base.model_dump(mode="python")
    candidate_data.update(override)
    try:
        candidate = ExecutionPolicy.model_validate(candidate_data)
    except ValidationError as exc:
        raise ConfigError(f"invalid execution policy override: {exc}") from exc

    _require_subset("allowed_mounts", candidate.allowed_mounts, base.allowed_mounts)
    _require_subset("allowed_commands", candidate.allowed_commands, base.allowed_commands)
    _require_subset(
        "allowed_network_hosts", candidate.allowed_network_hosts, base.allowed_network_hosts
    )

    if not candidate.approval_for.issuperset(base.approval_for):
        raise ConfigError("approval_for cannot remove required approvals")
    if candidate.max_duration_seconds > base.max_duration_seconds:
        raise ConfigError("max_duration_seconds cannot increase")
    if base.network is NetworkMode.DISABLED and candidate.network is not NetworkMode.DISABLED:
        raise ConfigError("network cannot be enabled by a lower-priority configuration")
    if not base.allow_host_execution and candidate.allow_host_execution:
        raise ConfigError("host execution cannot be enabled by a lower-priority configuration")
    if candidate.sandbox != base.sandbox:
        raise ConfigError("sandbox cannot be changed by a lower-priority configuration")

    return candidate


def _require_subset(name: str, candidate: frozenset[str], base: frozenset[str]) -> None:
    added = candidate - base
    if added:
        raise ConfigError(f"{name} cannot add permissions: {sorted(added)}")
