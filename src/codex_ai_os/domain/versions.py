"""Single immutable runtime version matrix for the 0.2.0 release."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class RuntimeVersions:
    """Versions that must move together across runtime and release assets."""

    requirement_baseline: str
    software: str
    plugin: str
    api: str
    config_schema: str
    document_schema: str
    profile_schema: str
    sqlite_schema: str
    execution_image: str
    compatible_api_versions: tuple[str, ...]
    compatible_config_schemas: tuple[str, ...]
    compatible_document_schemas: tuple[str, ...]
    compatible_profile_schemas: tuple[str, ...]

    @property
    def git_tag(self) -> str:
        return f"v{self.software}"

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


RUNTIME_VERSIONS = RuntimeVersions(
    requirement_baseline="REQ-1.6.2",
    software="0.2.0",
    plugin="0.2.0",
    api="1.2",
    config_schema="1.2",
    document_schema="1.2",
    profile_schema="1.2",
    sqlite_schema="0007",
    execution_image=(
        "python:3.12.14-bookworm@"
        "sha256:852282e520cc1754221fb2e061ab35b13b596e8112a731d60e2a8b471c973b7a"
    ),
    compatible_api_versions=("1.0", "1.1", "1.2"),
    compatible_config_schemas=("1.0", "1.1", "1.2"),
    compatible_document_schemas=("1.0", "1.1", "1.2"),
    compatible_profile_schemas=("1.0", "1.1", "1.2"),
)


__all__ = ["RUNTIME_VERSIONS", "RuntimeVersions"]
