"""Load and validate additive project profile definitions."""

from __future__ import annotations

from pathlib import Path

from codex_ai_os.domain.profiles import ProjectProfile
from codex_ai_os.infrastructure.config import ConfigError, load_yaml_model


def load_project_profiles(directory: Path) -> dict[str, ProjectProfile]:
    if not directory.is_dir():
        raise ConfigError(f"profile directory not found: {directory}")
    profiles: dict[str, ProjectProfile] = {}
    for path in sorted(directory.glob("*.yaml")):
        profile = load_yaml_model(path, ProjectProfile)
        if path.stem != profile.name:
            raise ConfigError(f"profile filename must match its name: {path.name}")
        if profile.name in profiles:
            raise ConfigError(f"duplicate project profile: {profile.name}")
        profiles[profile.name] = profile
    if not profiles:
        raise ConfigError(f"no project profiles found: {directory}")
    return profiles
