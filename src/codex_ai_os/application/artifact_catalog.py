"""Artifact catalog validation, project overrides, and id resolution (ADR-0012).

Governance YAML references artifacts as ``artifact:<id>``; this module resolves
those references against the baseline catalog merged with the project override
``.codex-os/artifact-catalog.yaml``. The override replaces the artifact list
under the same monotonic tightening contract as project Gate rules: baseline
required artifacts must survive and stay required. Structural violations raise
``ArtifactCatalogError``; semantic violations (unknown phase or role names) are
reported as findings by :func:`validate_catalog`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from codex_ai_os.domain.artifacts import (
    DEFAULT_ARTIFACT_CATALOG,
    ArtifactCatalog,
    ArtifactCatalogError,
    ArtifactSpec,
    load_artifact_catalog,
)
from codex_ai_os.domain.workflow import WorkflowPhase

ARTIFACT_REF_PREFIX = "artifact:"
PROJECT_CATALOG_PATH = Path(".codex-os") / "artifact-catalog.yaml"

_WORKFLOW_PHASES: frozenset[str] = frozenset(phase.value for phase in WorkflowPhase)


def _catalog_findings(artifacts: tuple[ArtifactSpec, ...]) -> list[str]:
    from codex_ai_os.application.governance_policy import BASELINE_ROLE_BOUNDARIES

    roles = set(BASELINE_ROLE_BOUNDARIES)
    findings: list[str] = []
    for spec in artifacts:
        unknown_phases = set(spec.phases) - set(_WORKFLOW_PHASES)
        if unknown_phases:
            findings.append(
                f"artifact {spec.id!r} declares unknown phases {sorted(unknown_phases)}"
            )
        if spec.producer not in roles:
            findings.append(f"artifact {spec.id!r} declares unknown producer {spec.producer!r}")
        unknown_reviewers = set(spec.reviewers) - roles
        if unknown_reviewers:
            findings.append(
                f"artifact {spec.id!r} declares unknown reviewers {sorted(unknown_reviewers)}"
            )
    return findings


def load_effective_catalog(project_root: Path) -> ArtifactCatalog:
    """Apply the project override (tightening only) on top of the baseline."""

    override_path = project_root / PROJECT_CATALOG_PATH
    if not override_path.is_file():
        return DEFAULT_ARTIFACT_CATALOG
    override = load_artifact_catalog(override_path)
    for artifact in DEFAULT_ARTIFACT_CATALOG.artifacts:
        if not artifact.required:
            continue
        candidate = override.by_id.get(artifact.id)
        if candidate is None:
            raise ArtifactCatalogError(
                f"{override_path}: project catalog removes baseline required artifact "
                f"{artifact.id} ({artifact.canonical_path})"
            )
        if not candidate.required:
            raise ArtifactCatalogError(
                f"{override_path}: project catalog relaxes baseline required artifact "
                f"{artifact.id} ({artifact.canonical_path})"
            )
        if candidate.canonical_path != artifact.canonical_path:
            raise ArtifactCatalogError(
                f"{override_path}: project catalog moves required artifact "
                f"{artifact.id} from {artifact.canonical_path} to {candidate.canonical_path}"
            )
    return override


def resolve_artifact_reference(value: str, catalog: ArtifactCatalog | None = None) -> str:
    """Resolve ``artifact:<id>`` to its canonical path; other values pass through."""

    if not value.startswith(ARTIFACT_REF_PREFIX):
        return value
    artifact_id = value[len(ARTIFACT_REF_PREFIX) :]
    effective = catalog if catalog is not None else DEFAULT_ARTIFACT_CATALOG
    path = effective.path_for(artifact_id)
    if path is None:
        raise ArtifactCatalogError(f"unknown artifact reference: {value}")
    return path


def resolve_artifact_references(
    values: frozenset[str] | tuple[str, ...], catalog: ArtifactCatalog | None = None
) -> frozenset[str]:
    return frozenset(resolve_artifact_reference(value, catalog) for value in values)


def validate_catalog(project_root: Path) -> dict[str, Any]:
    """Validate baseline + override catalog without raising; returns a report."""

    try:
        catalog = load_effective_catalog(project_root)
    except ArtifactCatalogError as exc:
        return {"ok": False, "artifacts": [], "findings": [str(exc)]}
    findings = _catalog_findings(catalog.artifacts)
    artifacts: list[dict[str, Any]] = [
        {
            "id": spec.id,
            "canonical_path": spec.canonical_path,
            "phases": list(spec.phases),
            "gate": spec.gate,
            "producer": spec.producer,
            "reviewers": list(spec.reviewers),
            "required": spec.required,
        }
        for spec in sorted(catalog.artifacts, key=lambda item: item.id)
    ]
    return {"ok": not findings, "artifacts": artifacts, "findings": findings}
