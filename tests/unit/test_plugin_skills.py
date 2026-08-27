import json
from pathlib import Path

import yaml

from codex_ai_os.domain.versions import RUNTIME_VERSIONS

REPO_ROOT = Path(__file__).parents[2]


def test_plugin_contains_complete_v1_skill_set_without_scaffold_placeholders() -> None:
    skill_root = REPO_ROOT / "plugins" / "ai-engineering-os" / "skills"
    expected = {
        "agent-manager",
        "api-design",
        "architecture-design",
        "backend-implementation",
        "bug-fix-orchestrator",
        "code-review",
        "database-design",
        "execution-manager",
        "feature-development-orchestrator",
        "frontend-implementation",
        "interaction-design",
        "memory-manager",
        "new-project-orchestrator",
        "open-source-research",
        "product-design",
        "release-manager",
        "requirement-analysis",
        "security-review",
        "testing",
        "ui-design",
    }

    discovered: set[str] = set()
    for skill_file in sorted(skill_root.glob("*/SKILL.md")):
        content = skill_file.read_text(encoding="utf-8")
        _, frontmatter, _body = content.split("---", 2)
        metadata = yaml.safe_load(frontmatter)
        assert isinstance(metadata, dict)
        assert metadata["name"] == skill_file.parent.name
        assert isinstance(metadata["description"], str)
        assert "TODO" not in content
        assert (skill_file.parent / "agents" / "openai.yaml").is_file()
        discovered.add(skill_file.parent.name)

    assert discovered == expected


def test_plugin_manifest_version_matches_runtime_matrix() -> None:
    manifest_path = REPO_ROOT / "plugins" / "ai-engineering-os" / ".codex-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["version"] == RUNTIME_VERSIONS.plugin
    assert RUNTIME_VERSIONS.api == "1.2"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert manifest["skills"] == "./skills/"
