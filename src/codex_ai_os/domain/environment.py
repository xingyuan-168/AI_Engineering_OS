"""Declarative OCI-first project environment contracts."""

from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from codex_ai_os.domain.config import EnvironmentMode, SandboxBackend, StrictModel

_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")


def _relative_path(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    if (
        not normalized
        or normalized.startswith(("/", "//"))
        or re.match(r"^[A-Za-z]:", normalized)
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
    ):
        raise ValueError("environment paths must be normalized project-relative paths")
    return normalized


class HostBudget(StrictModel):
    max_project_bytes: int = Field(default=1_073_741_824, ge=1)
    max_git_bytes: int = Field(default=536_870_912, ge=1)
    large_file_bytes: int = Field(default=52_428_800, ge=1)


class EnvironmentServiceSpec(StrictModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")
    dockerfile: str
    stateful: bool = False
    healthcheck_required: bool = True
    persistent_targets: tuple[str, ...] = ()
    backup_command: tuple[str, ...] = ()
    restore_command: tuple[str, ...] = ()

    _dockerfile_path = field_validator("dockerfile")(_relative_path)

    @model_validator(mode="after")
    def stateful_service_has_recovery_contract(self) -> Self:
        if self.stateful and (
            not self.persistent_targets
            or not self.backup_command
            or not self.restore_command
        ):
            raise ValueError(
                "stateful services require persistent targets and backup/restore commands"
            )
        return self


class PersistentMountSpec(StrictModel):
    service: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")
    source: str = Field(min_length=1, max_length=128)
    target: str = Field(pattern=r"^/[^\x00]+$")
    kind: Literal["named-volume", "external-mount"] = "named-volume"


class SharedAssetSpec(StrictModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")
    source_env: str
    target: str = Field(pattern=r"^/[^\x00]+$")
    read_only: Literal[True] = True

    @field_validator("source_env")
    @classmethod
    def source_is_environment_name(cls, value: str) -> str:
        if not _ENV_NAME.fullmatch(value):
            raise ValueError("shared asset source_env must be an uppercase environment name")
        return value


class EnvironmentContract(StrictModel):
    schema_version: Literal["1.2"] = "1.2"
    environment_mode: Literal[EnvironmentMode.OCI_FIRST] = EnvironmentMode.OCI_FIRST
    oci_backend: SandboxBackend = SandboxBackend.PODMAN
    compose_files: tuple[str, ...] = ("compose.yaml",)
    dockerfiles: tuple[str, ...] = ()
    dependency_locks: tuple[str, ...] = ()
    services: tuple[EnvironmentServiceSpec, ...] = ()
    persistent_mounts: tuple[PersistentMountSpec, ...] = ()
    shared_assets: tuple[SharedAssetSpec, ...] = ()
    host_budget: HostBudget = HostBudget()

    @field_validator("compose_files", "dockerfiles", "dependency_locks")
    @classmethod
    def paths_are_project_relative(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_relative_path(value) for value in values)
        if len(set(normalized)) != len(normalized):
            raise ValueError("environment path lists cannot contain duplicates")
        return normalized

    @model_validator(mode="after")
    def service_and_file_declarations_match(self) -> Self:
        names = [service.name for service in self.services]
        if len(set(names)) != len(names):
            raise ValueError("environment service names must be unique")
        declared = set(self.dockerfiles)
        referenced = {service.dockerfile for service in self.services}
        if referenced - declared:
            raise ValueError("service Dockerfiles must be listed in dockerfiles")
        service_names = set(names)
        if any(mount.service not in service_names for mount in self.persistent_mounts):
            raise ValueError("persistent mounts must reference declared services")
        return self


__all__ = [
    "EnvironmentContract",
    "EnvironmentServiceSpec",
    "HostBudget",
    "PersistentMountSpec",
    "SharedAssetSpec",
]
