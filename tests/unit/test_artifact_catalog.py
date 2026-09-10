# pyright: reportPrivateUsage=false
"""Artifact catalog unit tests (ADR-0012)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

import codex_ai_os.cli.app as cli_app
from codex_ai_os.application.artifact_catalog import (
    PROJECT_CATALOG_PATH,
    load_effective_catalog,
    resolve_artifact_reference,
    validate_catalog,
)
from codex_ai_os.application.governance_policy import GovernancePolicyError
from codex_ai_os.application.workflow import _input_artifacts_for
from codex_ai_os.domain.artifacts import (
    DEFAULT_ARTIFACT_CATALOG,
    load_artifact_catalog,
)
from codex_ai_os.domain.workflow import PHASE_DEFINITIONS, WorkflowPhase
from codex_ai_os.infrastructure.documents import DocumentManager
from codex_ai_os.templates.project_docs import documents_for

runner = CliRunner()


def _entry(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": "scope",
        "canonical_path": "docs/SCOPE.md",
        "phases": ["intake", "requirements"],
        "gate": "G0",
        "producer": "product-manager",
        "reviewers": ["project-manager"],
        "required": True,
    }
    entry.update(overrides)
    return entry


def _baseline_entries() -> list[dict[str, Any]]:
    return [
        {
            "id": artifact.id,
            "canonical_path": artifact.canonical_path,
            "phases": list(artifact.phases),
            "gate": artifact.gate,
            "producer": artifact.producer,
            "reviewers": list(artifact.reviewers),
            "required": artifact.required,
        }
        for artifact in DEFAULT_ARTIFACT_CATALOG.artifacts
    ]


def _write_catalog(path: Path, entries: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({"schema_version": "1.2", "artifacts": entries}, sort_keys=True),
        encoding="utf-8",
    )
    return path


def _write_override(tmp_path: Path, entries: list[dict[str, Any]]) -> Path:
    return _write_catalog(tmp_path / PROJECT_CATALOG_PATH, entries)


def test_builtin_catalog_loads_with_unique_ids_and_paths() -> None:
    catalog = DEFAULT_ARTIFACT_CATALOG
    assert catalog.path_for("scope") == "docs/SCOPE.md"
    assert catalog.by_path["docs/PRODUCT_DESIGN.md"].id == "product-design"
    assert len(catalog.by_id) == len(catalog.by_path) == len(catalog.artifacts)
    assert len(catalog.artifacts) >= 20


def test_catalog_phase_and_gate_queries() -> None:
    catalog = DEFAULT_ARTIFACT_CATALOG
    assert set(catalog.paths_for_phase("intake")) == {
        "docs/PROJECT_MASTER.md",
        "docs/SCOPE.md",
    }
    required = catalog.required_paths_for_gate("G2")
    assert {
        "docs/PRODUCT_DESIGN.md",
        "docs/INTERACTION_DESIGN.md",
        "docs/UI_DESIGN.md",
    } <= set(required)
    optional_g2 = {
        spec.canonical_path for spec in catalog.artifacts_for_gate("G2") if not spec.required
    }
    assert not set(required) & optional_g2


def test_duplicate_artifact_id_is_rejected(tmp_path: Path) -> None:
    path = _write_catalog(
        tmp_path / "catalog.yaml",
        [_entry(), _entry(canonical_path="docs/OTHER.md")],
    )
    with pytest.raises(ValueError, match="duplicate artifact id 'scope'"):
        load_artifact_catalog(path)


def test_duplicate_canonical_path_is_rejected(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path / "catalog.yaml", [_entry(), _entry(id="scope-two")])
    with pytest.raises(ValueError, match="duplicate canonical path"):
        load_artifact_catalog(path)


def test_unknown_gate_is_rejected(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path / "catalog.yaml", [_entry(gate="G5")])
    with pytest.raises(ValueError, match="invalid gate"):
        load_artifact_catalog(path)


def test_unsafe_canonical_path_is_rejected(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path / "catalog.yaml", [_entry(canonical_path="../escape.md")])
    with pytest.raises(ValueError, match="unsafe canonical path"):
        load_artifact_catalog(path)


def test_resolve_artifact_reference_and_unknown_id_fails_closed() -> None:
    assert resolve_artifact_reference("artifact:scope") == "docs/SCOPE.md"
    assert resolve_artifact_reference("docs/LITERAL.md") == "docs/LITERAL.md"
    with pytest.raises((GovernancePolicyError, ValueError), match="unknown artifact"):
        resolve_artifact_reference("artifact:does-not-exist")


def test_validate_catalog_accepts_builtin_catalog(tmp_path: Path) -> None:
    report = validate_catalog(tmp_path)
    assert report["ok"] is True
    assert report["findings"] == []
    ids = {entry["id"] for entry in report["artifacts"]}
    assert {"project-master", "product-design", "ux-research", "changelog"} <= ids


def test_override_may_add_artifacts(tmp_path: Path) -> None:
    entries = _baseline_entries() + [
        {
            "id": "project-rulebook",
            "canonical_path": "docs/RULEBOOK.md",
            "phases": ["requirements"],
            "gate": "G1",
            "producer": "product-manager",
            "reviewers": ["reviewer"],
            "required": True,
        }
    ]
    _write_override(tmp_path, entries)
    report = validate_catalog(tmp_path)
    assert report["ok"] is True
    assert "project-rulebook" in {entry["id"] for entry in report["artifacts"]}
    merged = load_effective_catalog(tmp_path)
    assert merged.path_for("project-rulebook") == "docs/RULEBOOK.md"
    assert merged.path_for("scope") == "docs/SCOPE.md"


def test_override_cannot_remove_baseline_required_artifact(tmp_path: Path) -> None:
    from codex_ai_os.domain.artifacts import ArtifactCatalogError

    entries = [entry for entry in _baseline_entries() if entry["id"] != "project-master"]
    _write_override(tmp_path, entries)
    with pytest.raises(ArtifactCatalogError, match="project-master"):
        load_effective_catalog(tmp_path)


def test_override_cannot_relax_required_to_optional(tmp_path: Path) -> None:
    from codex_ai_os.domain.artifacts import ArtifactCatalogError

    entries = _baseline_entries()
    for entry in entries:
        if entry["id"] == "ui-design":
            entry["required"] = False
    _write_override(tmp_path, entries)
    with pytest.raises(ArtifactCatalogError, match="relaxes baseline required"):
        load_effective_catalog(tmp_path)


def test_override_unknown_role_or_phase_is_reported(tmp_path: Path) -> None:
    entries = _baseline_entries()
    for entry in entries:
        if entry["id"] == "scope":
            entry["producer"] = "not-a-role"
            entry["phases"] = ["not-a-phase"]
    _write_override(tmp_path, entries)
    report = validate_catalog(tmp_path)
    assert report["ok"] is False
    assert any("not-a-role" in str(finding) for finding in report["findings"])
    assert any("not-a-phase" in str(finding) for finding in report["findings"])


def test_phase_definitions_derive_paths_from_catalog() -> None:
    catalog = DEFAULT_ARTIFACT_CATALOG
    intake = PHASE_DEFINITIONS[WorkflowPhase.INTAKE].allowed_paths
    assert set(intake) == {"docs/PROJECT_MASTER.md", "docs/SCOPE.md"}
    requirements = PHASE_DEFINITIONS[WorkflowPhase.REQUIREMENTS].allowed_paths
    assert catalog.path_for("product-requirements") in requirements
    assert catalog.path_for("scope") in requirements
    research = PHASE_DEFINITIONS[WorkflowPhase.RESEARCH].allowed_paths
    assert "docs/ADR/" in research
    prototype = PHASE_DEFINITIONS[WorkflowPhase.PROTOTYPE].allowed_paths
    for artifact_id in ("product-design", "interaction-design", "ui-design"):
        assert catalog.path_for(artifact_id) in prototype
    assert "docs/prototypes/" in prototype
    design = PHASE_DEFINITIONS[WorkflowPhase.DESIGN].allowed_paths
    for artifact_id in ("architecture", "api-spec", "database", "security", "environment-doc"):
        assert catalog.path_for(artifact_id) in design
    assert ".codex-os/environment.yaml" in design
    assert "docker/" in design
    implementation = PHASE_DEFINITIONS[WorkflowPhase.IMPLEMENTATION].allowed_paths
    assert "src/" in implementation
    assert "pyproject.toml" in implementation
    assert catalog.path_for("changelog") in implementation
    verify = PHASE_DEFINITIONS[WorkflowPhase.VERIFY].allowed_paths
    assert catalog.path_for("test-plan") in verify
    assert "reports/" in verify
    memory = PHASE_DEFINITIONS[WorkflowPhase.MEMORY].allowed_paths
    assert "docs/ADR/" in memory
    assert ".codex-os/memory/" in memory


def test_input_artifacts_resolve_through_catalog() -> None:
    catalog = DEFAULT_ARTIFACT_CATALOG
    assert _input_artifacts_for(WorkflowPhase.PROTOTYPE) == (
        catalog.path_for("product-design"),
        catalog.path_for("interaction-design"),
        catalog.path_for("ui-design"),
    )
    assert _input_artifacts_for(WorkflowPhase.IMPLEMENTATION) == (
        catalog.path_for("architecture"),
        catalog.path_for("api-spec"),
        catalog.path_for("database"),
    )
    assert _input_artifacts_for(WorkflowPhase.INTAKE) == ("business_goal",)
    assert _input_artifacts_for(WorkflowPhase.VERIFY) == ("implementation_commit", "test_plan")


def test_cli_artifact_validate_succeeds(tmp_path: Path) -> None:
    result = runner.invoke(
        cli_app.app,
        ["artifact", "validate", "--project-root", str(tmp_path), "--json"],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True


def test_cli_artifact_validate_reports_config_failure(tmp_path: Path) -> None:
    entries = [entry for entry in _baseline_entries() if entry["id"] != "project-master"]
    _write_override(tmp_path, entries)
    result = runner.invoke(
        cli_app.app,
        ["artifact", "validate", "--project-root", str(tmp_path), "--json"],
    )
    assert result.exit_code == 2
    assert "CONFIG_INVALID" in result.stdout


def test_frontend_templates_contain_design_triad() -> None:
    documents = documents_for("frontend")
    for artifact_id in ("product-design", "interaction-design", "ui-design"):
        template = documents[DEFAULT_ARTIFACT_CATALOG.path_for(artifact_id)]
        assert template.lstrip().startswith("# ")
        assert "## " in template
    for artifact_id in ("ux-research", "user-flow", "wireframe", "ui-spec"):
        assert DEFAULT_ARTIFACT_CATALOG.path_for(artifact_id) in documents


def test_backend_templates_do_not_include_frontend_triad() -> None:
    documents = documents_for("backend")
    for artifact_id in ("product-design", "interaction-design", "ui-design"):
        assert DEFAULT_ARTIFACT_CATALOG.path_for(artifact_id) not in documents


def test_initialized_fullstack_project_passes_document_check(tmp_path: Path) -> None:
    manager = DocumentManager(tmp_path)
    manager.initialize_documents("ERP", "fullstack")

    report = manager.check("fullstack")

    assert report.ok, report
