from pathlib import Path

import pytest

from codex_ai_os.domain.config import ProjectType
from codex_ai_os.domain.profiles import ProjectProfile
from codex_ai_os.infrastructure.config import ConfigError
from codex_ai_os.infrastructure.profiles import load_project_profiles

REPO_ROOT = Path(__file__).parents[2]


def test_v1_project_profiles_are_complete_additive_and_safe() -> None:
    profiles = load_project_profiles(REPO_ROOT / "profiles")

    assert set(profiles) == {"frontend-project", "backend-project", "large-project"}
    assert profiles["large-project"].approval_required
    assert all(profile.additional_skills for profile in profiles.values())
    assert all(profile.required_artifacts for profile in profiles.values())
    assert all(profile.additional_reviewers for profile in profiles.values())
    assert all(profile.min_agents >= 3 for profile in profiles.values())
    assert all(profile.task_templates for profile in profiles.values())
    assert all(profile.gate_requirements for profile in profiles.values())
    assert "frontend-implementation" in profiles["frontend-project"].additional_skills


def test_profile_rejects_escaping_artifact_path() -> None:
    with pytest.raises(ValueError, match="unsafe"):
        ProjectProfile(
            schema_version="1.0",
            name="unsafe-project",
            description="Fixture",
            project_types=(ProjectType.GENERIC,),
            additional_skills=("testing",),
            required_artifacts=("../outside.md",),
            additional_reviewers=("reviewer",),
            min_agents=1,
        )


def test_profile_filename_must_match_declared_name(tmp_path: Path) -> None:
    (tmp_path / "wrong.yaml").write_text(
        "schema_version: '1.0'\n"
        "name: actual-project\n"
        "description: Fixture\n"
        "project_types: [generic]\n"
        "additional_skills: [testing]\n"
        "required_artifacts: [docs/TEST_PLAN.md]\n"
        "additional_reviewers: [reviewer]\n"
        "min_agents: 1\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="filename"):
        load_project_profiles(tmp_path)


def test_profile_schema_12_requires_declarative_tasks_and_gates() -> None:
    with pytest.raises(ValueError, match="task_templates and gate_requirements"):
        ProjectProfile(
            schema_version="1.2",
            name="incomplete-project",
            description="Fixture",
            project_types=(ProjectType.GENERIC,),
            additional_skills=("testing",),
            required_artifacts=("docs/TEST_PLAN.md",),
            additional_reviewers=("reviewer",),
            min_agents=1,
        )
