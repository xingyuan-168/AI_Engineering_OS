from pathlib import Path

import yaml

from codex_ai_os.domain.workflow import WORKFLOW_START_PHASE, WorkflowPhase
from codex_ai_os.infrastructure.documents import DocumentManager

ROOT = Path(__file__).resolve().parents[2]


def test_workflow_spec_declares_runtime_entry_phases() -> None:
    specification = (ROOT / "docs" / "WORKFLOW_SPEC.md").read_text(encoding="utf-8")

    for workflow_name, phase in WORKFLOW_START_PHASE.items():
        assert f"| `{workflow_name}` | `{phase.value}` |" in specification


def test_configuration_examples_list_every_workflow_phase() -> None:
    expected = "states: [" + ", ".join(phase.value for phase in WorkflowPhase) + "]"

    assert expected in (ROOT / "docs" / "CONFIG_SPEC.md").read_text(encoding="utf-8")
    assert expected in (ROOT / "docs" / "TECH_STACK.md").read_text(encoding="utf-8")


def test_agents_delegates_reading_order_and_verification_sources() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "`docs/PROJECT_MASTER.md` section 3" in agents
    assert "`docs/TEST_PLAN.md` is the human-readable verification contract" in agents
    assert "Before changing a subsystem, read:" not in agents


def test_deployment_does_not_duplicate_release_checklist() -> None:
    deployment = (ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")

    assert "## 发布检查清单" not in deployment
    assert "[RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md)" in deployment


def test_skill_spec_matches_packaged_skill_directories() -> None:
    specification = (ROOT / "docs" / "SKILL_SPEC.md").read_text(encoding="utf-8")
    skill_names = {
        path.name
        for path in (ROOT / "plugins" / "ai-engineering-os" / "skills").iterdir()
        if path.is_dir()
    }

    assert len(skill_names) == 21
    assert all(f"`{name}`" in specification for name in skill_names)


def test_agent_spec_matches_packaged_agent_profiles() -> None:
    specification = (ROOT / "docs" / "AGENT_SPEC.md").read_text(encoding="utf-8")
    profiles = {
        path.stem
        for path in (ROOT / ".codex" / "agents").glob("*.toml")
    }

    assert profiles == {
        "architect",
        "backend-engineer",
        "database-engineer",
        "frontend-engineer",
        "product-manager",
        "qa",
        "reviewer",
        "security-reviewer",
    }
    assert "HTML 原型" in specification


def test_repository_documents_and_traceability_are_governed() -> None:
    report = DocumentManager(ROOT).check(
        "backend",
        expected_document_version="0.2.0",
    )

    assert report.ok, report
    traceability = yaml.safe_load(
        (ROOT / ".codex-os" / "test-traceability.yaml").read_text(encoding="utf-8")
    )
    assert traceability["schema_version"] == "1.2"
    assert len(traceability["entries"]) >= 8
