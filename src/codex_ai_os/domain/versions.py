"""Single immutable runtime version matrix for the governance-core release."""

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
    sqlite_schema: str
    compatible_api_versions: tuple[str, ...]
    compatible_config_schemas: tuple[str, ...]
    compatible_document_schemas: tuple[str, ...]

    @property
    def git_tag(self) -> str:
        return f"v{self.software}"

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


RUNTIME_VERSIONS = RuntimeVersions(
    requirement_baseline="REQ-GC-1.0",
    software="1.0.0",
    plugin="1.0.0",
    api="2.0",
    config_schema="1.2",
    document_schema="1.2",
    sqlite_schema="0001",
    compatible_api_versions=("2.0",),
    compatible_config_schemas=("1.2",),
    compatible_document_schemas=("1.2",),
)


__all__ = ["RUNTIME_VERSIONS", "RuntimeVersions"]
