from pathlib import Path

import yaml

from codex_ai_os.domain.versions import RUNTIME_VERSIONS
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
    project = yaml.safe_load(
        (ROOT / ".codex-os" / "project.yaml").read_text(encoding="utf-8")
    )
    report = DocumentManager(ROOT).check(
        "backend",
        expected_document_version=str(project["document_version"]),
    )

    assert report.ok, report
    traceability = yaml.safe_load(
        (ROOT / ".codex-os" / "test-traceability.yaml").read_text(encoding="utf-8")
    )
    assert traceability["schema_version"] == "1.2"
    assert len(traceability["entries"]) >= 8


def test_active_documents_track_runtime_version_facts() -> None:
    """ADR-0010: prose version facts derive from RUNTIME_VERSIONS, never drift.

    Each entry pins the document line that must carry the current runtime
    value; bumping RUNTIME_VERSIONS without updating these documents fails
    the suite before the change can be committed.
    """
    software = RUNTIME_VERSIONS.software
    sqlite = RUNTIME_VERSIONS.sqlite_schema
    anchors: dict[str, tuple[str, ...]] = {
        "docs/PROJECT_MASTER.md": (
            f"软件/CLI/Plugin `{software}`",
            f"SQLite `{sqlite}`",
        ),
        "docs/BUSINESS_RULES.md": (
            f"软件、CLI、Plugin 核心版本为 `{software}`",
            f"SQLite 通过 `0001-{sqlite}` 管理",
        ),
        "docs/TECH_STACK.md": (
            f"| SQLite Schema | `{sqlite}` |",
            f"Git tag 为 `v{software}`",
        ),
        "docs/TEST_PLAN.md": (
            f"SQLite {sqlite}",
            f"fresh install `0001 -> {sqlite}`",
        ),
        "docs/RELEASE_CHECKLIST.md": (
            f"发行版 `{software}`",
            f"SQLite `{sqlite}`",
        ),
        "docs/ARCHITECTURE.md": (
            f"migrations 0001-{sqlite}",
        ),
        "docs/MIGRATION_SPEC.md": (
            f"fresh install `0001 -> {sqlite}`",
        ),
        "docs/PRODUCT_REQUIREMENTS.md": (
            f"软件/CLI/Plugin `{software}`",
            f"追加式 `{sqlite}`",
        ),
    }
    for relative, expected_texts in anchors.items():
        text = (ROOT / relative).read_text(encoding="utf-8")
        for expected in expected_texts:
            assert expected in text, f"{relative} is missing current version fact: {expected}"


def test_status_vocabulary_separates_run_status_from_host_operations() -> None:
    """ADR-0010: reconcile_required is a Host Operation state, not a run_status."""
    workflow_spec = (ROOT / "docs" / "WORKFLOW_SPEC.md").read_text(encoding="utf-8")
    assert "三个独立词汇表" in workflow_spec
    assert "`reconcile_required` 是 Host Operation 状态，不是 `run_status`" in workflow_spec

    scope = (ROOT / "docs" / "SCOPE.md").read_text(encoding="utf-8")
    assert "宿主 PreToolUse Hook 是防御纵深" in scope
    assert "不依赖提示词或人工自觉" not in scope

    security = (ROOT / "docs" / "SECURITY.md").read_text(encoding="utf-8")
    assert "ADR-0009 §3" in security
