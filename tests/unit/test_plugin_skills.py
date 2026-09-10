from __future__ import annotations

from pathlib import Path

EXPECTED_SKILLS = {
    "governance-entry",
    "open-source-research",
    "html-prototype",
    "frontend-design-review",
    "worktree-protocol",
    "document-impact",
    "memory-protocol",
    "finish-checklist",
}

SKILLS_ROOT = Path(__file__).resolve().parents[2] / "plugins" / "ai-engineering-os" / "skills"


def test_skill_set_is_converged() -> None:
    names = {path.name for path in SKILLS_ROOT.iterdir() if path.is_dir()}
    assert names == EXPECTED_SKILLS


def test_each_skill_has_manifest_and_agent_profile() -> None:
    for name in sorted(EXPECTED_SKILLS):
        skill_md = SKILLS_ROOT / name / "SKILL.md"
        assert skill_md.is_file(), name
        text = skill_md.read_text(encoding="utf-8")
        assert text.startswith("---"), name
        assert "name: " + name in text, name
        assert "description:" in text, name
        assert (SKILLS_ROOT / name / "agents" / "openai.yaml").is_file(), name


def test_skill_documents_are_compact() -> None:
    for name in sorted(EXPECTED_SKILLS):
        skill_md = SKILLS_ROOT / name / "SKILL.md"
        lines = skill_md.read_text(encoding="utf-8").splitlines()
        assert len(lines) <= 60, (name, len(lines))
