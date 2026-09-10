"""Artifact catalog — single source of truth for governed document artifacts.

ADR-0012: every governed document artifact is declared once here and every
governance consumer (gate rules, profiles, phase definitions, input artifact
maps) references artifacts by id instead of repeating canonical paths.

Layering note: this module lives in the domain layer because
``domain.workflow.PHASE_DEFINITIONS`` derives its document paths from the
baseline catalog at import time. It therefore loads the packaged/repository
YAML directly, mirroring the packaged-resource fallback used for Gate rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from codex_ai_os.domain.governance import is_unsafe_repository_path

GATE_VALUES: frozenset[str] = frozenset({"G0", "G1", "G2", "G3", "G4"})

_ARTIFACT_ID_PATTERN = r"^[a-z0-9][a-z0-9-]*$"


class ArtifactCatalogError(ValueError):
    """Raised when an artifact catalog file is structurally invalid."""


class ArtifactSpec(BaseModel):
    """One governed document artifact declared by the catalog."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=_ARTIFACT_ID_PATTERN)
    canonical_path: str
    phases: tuple[str, ...]
    gate: str | None = None
    producer: str
    reviewers: tuple[str, ...] = ()
    required: bool


class _ArtifactCatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = Field(pattern=r"^1\.2$")
    artifacts: tuple[ArtifactSpec, ...]


@dataclass(frozen=True, slots=True)
class ArtifactCatalog:
    """Validated, duplicate-free artifact catalog."""

    artifacts: tuple[ArtifactSpec, ...]
    by_id: dict[str, ArtifactSpec]
    by_path: dict[str, ArtifactSpec]

    def path_for(self, artifact_id: str) -> str | None:
        spec = self.by_id.get(artifact_id)
        return None if spec is None else spec.canonical_path

    def artifacts_for_phase(self, phase: str) -> tuple[ArtifactSpec, ...]:
        return tuple(spec for spec in self.artifacts if phase in spec.phases)

    def paths_for_phase(self, phase: str) -> tuple[str, ...]:
        return tuple(sorted(spec.canonical_path for spec in self.artifacts_for_phase(phase)))

    def artifacts_for_gate(self, gate: str) -> tuple[ArtifactSpec, ...]:
        return tuple(spec for spec in self.artifacts if spec.gate == gate)

    def required_paths_for_gate(self, gate: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                spec.canonical_path
                for spec in self.artifacts_for_gate(gate)
                if spec.required
            )
        )

    def optional_paths_for_gate(self, gate: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                spec.canonical_path
                for spec in self.artifacts_for_gate(gate)
                if not spec.required
            )
        )


_PHASE_TOKEN_PATTERN = r"^[a-z][a-z0-9-]*$"


def _validate_spec(spec: ArtifactSpec, *, location: str) -> None:
    if not spec.phases:
        raise ArtifactCatalogError(
            f"{location}: artifact {spec.id!r} must declare at least one phase"
        )
    for phase in spec.phases:
        if not re.fullmatch(_PHASE_TOKEN_PATTERN, phase):
            raise ArtifactCatalogError(
                f"{location}: artifact {spec.id!r} declares invalid phase {phase!r}"
            )
    if spec.gate is not None and spec.gate not in GATE_VALUES:
        raise ArtifactCatalogError(
            f"{location}: artifact {spec.id!r} declares invalid gate {spec.gate!r}"
        )
    if is_unsafe_repository_path(spec.canonical_path):
        raise ArtifactCatalogError(
            f"{location}: artifact {spec.id!r} declares unsafe canonical path "
            f"{spec.canonical_path!r}"
        )


def build_catalog(artifacts: tuple[ArtifactSpec, ...], *, location: str) -> ArtifactCatalog:
    by_id: dict[str, ArtifactSpec] = {}
    by_path: dict[str, ArtifactSpec] = {}
    for spec in artifacts:
        _validate_spec(spec, location=location)
        if spec.id in by_id:
            raise ArtifactCatalogError(f"{location}: duplicate artifact id {spec.id!r}")
        if spec.canonical_path in by_path:
            raise ArtifactCatalogError(
                f"{location}: duplicate canonical path {spec.canonical_path!r} "
                f"({by_path[spec.canonical_path].id!r} and {spec.id!r})"
            )
        by_id[spec.id] = spec
        by_path[spec.canonical_path] = spec
    return ArtifactCatalog(artifacts=artifacts, by_id=by_id, by_path=by_path)


def load_artifact_catalog(path: Path) -> ArtifactCatalog:
    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
        model = _ArtifactCatalogModel.model_validate(raw)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ArtifactCatalogError(f"cannot read artifact catalog {path}: {exc}") from exc
    except ValueError as exc:
        raise ArtifactCatalogError(f"invalid artifact catalog {path}: {exc}") from exc
    return build_catalog(model.artifacts, location=str(path))


_PACKAGED_CATALOG = (
    Path(__file__).resolve().parents[1] / "resources" / "catalog" / "artifacts.yaml"
)
_REPOSITORY_CATALOG = Path(__file__).resolve().parents[3] / "catalog" / "artifacts.yaml"

DEFAULT_ARTIFACT_CATALOG: ArtifactCatalog = load_artifact_catalog(
    _PACKAGED_CATALOG if _PACKAGED_CATALOG.is_file() else _REPOSITORY_CATALOG
)


def artifact_paths_for_phase(phase: str) -> tuple[str, ...]:
    """Baseline catalog document paths written in ``phase`` (ADR-0012)."""

    return DEFAULT_ARTIFACT_CATALOG.paths_for_phase(phase)
