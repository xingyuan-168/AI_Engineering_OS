from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).parents[2]


def test_required_agent_profiles_are_valid_and_unique() -> None:
    expected = {
        "product-manager",
        "architect",
        "backend-engineer",
        "database-engineer",
        "qa",
        "security-reviewer",
        "reviewer",
    }
    profiles: list[dict[str, Any]] = []
    for path in sorted((REPO_ROOT / ".codex" / "agents").glob("*.toml")):
        value: object = tomllib.loads(path.read_text(encoding="utf-8"))
        assert isinstance(value, dict)
        profile = value
        assert isinstance(profile.get("name"), str)
        assert isinstance(profile.get("description"), str)
        assert isinstance(profile.get("developer_instructions"), str)
        profiles.append(profile)

    assert {str(profile["name"]) for profile in profiles} == expected
    assert len(profiles) == len(expected)


def test_review_agents_are_read_only_and_other_agents_inherit_permissions() -> None:
    profile_dir = REPO_ROOT / ".codex" / "agents"
    for name in ("security-reviewer", "reviewer"):
        profile = tomllib.loads((profile_dir / f"{name}.toml").read_text(encoding="utf-8"))
        assert profile["sandbox_mode"] == "read-only"

    backend = tomllib.loads(
        (profile_dir / "backend-engineer.toml").read_text(encoding="utf-8")
    )
    assert "sandbox_mode" not in backend
