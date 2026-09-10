"""YAML loading and safety-preserving configuration merge (ADR-0016 surface)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

import yaml
from pydantic import BaseModel, ValidationError

from codex_ai_os.domain.config import ProjectConfig


class ConfigError(ValueError):
    """Raised when a configuration file is invalid or unsafe."""


class ProjectRootError(ConfigError):
    """Raised when a public call targets a different or managed checkout."""

    def __init__(self, code: str, message: str, *, coordinator_root: Path | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.coordinator_root = coordinator_root


def load_yaml_model[ModelT: BaseModel](path: Path, model: type[ModelT]) -> ModelT:
    """Load one UTF-8 YAML mapping into a strict Pydantic model."""

    raw = _load_yaml_mapping(path)

    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid configuration {path}: {exc}") from exc


def _load_yaml_mapping(path: Path) -> dict[str, object]:
    """Read a UTF-8 YAML mapping without applying a model yet."""

    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read configuration {path}: {exc}") from exc

    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ConfigError(f"configuration {path} must contain a YAML mapping")

    return cast(dict[str, object], raw)


def load_project_config(project_root: Path) -> ProjectConfig:
    requested_root = project_root.resolve()
    coordinator_root = _managed_worktree_coordinator(requested_root)
    if coordinator_root is not None:
        raise ProjectRootError(
            "MANAGED_WORKTREE_ROOT",
            "public runtime calls from a managed Worktree are not allowed; "
            f"use coordinator root {coordinator_root}",
            coordinator_root=coordinator_root,
        )

    path = requested_root / ".codex-os" / "project.yaml"
    raw = _load_yaml_mapping(path)
    configured = Path(str(raw.get("root", ".")))
    configured_root = (
        configured.resolve()
        if configured.is_absolute()
        else (requested_root / configured).resolve()
    )
    if configured_root != requested_root:
        raise ProjectRootError(
            "PROJECT_ROOT_MISMATCH",
            f"configured project root {configured_root} does not match requested root "
            f"{requested_root}; invoke the coordinator root explicitly",
            coordinator_root=configured_root,
        )
    raw["root"] = requested_root
    try:
        return ProjectConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid configuration {path}: {exc}") from exc


def _managed_worktree_coordinator(project_root: Path) -> Path | None:
    marker = project_root / ".git"
    if not marker.is_file():
        return None
    try:
        first_line = marker.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, UnicodeError, IndexError):
        return None
    prefix = "gitdir:"
    if not first_line.casefold().startswith(prefix):
        return None
    raw_git_dir = Path(first_line[len(prefix) :].strip())
    git_dir = (
        raw_git_dir.resolve()
        if raw_git_dir.is_absolute()
        else (project_root / raw_git_dir).resolve()
    )
    common_file = git_dir / "commondir"
    try:
        raw_common = Path(common_file.read_text(encoding="utf-8").strip())
    except (OSError, UnicodeError):
        return None
    common_dir = (
        raw_common.resolve()
        if raw_common.is_absolute()
        else (git_dir / raw_common).resolve()
    )
    return common_dir.parent if common_dir.name == ".git" else None
