# pyright: reportPrivateUsage=false
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.application.routing import ProfileRouter
from codex_ai_os.application.workflow import WorkflowEngine
from codex_ai_os.domain.config import ProjectType
from codex_ai_os.infrastructure.database import Database


def test_profile_router_selects_smallest_team_and_large_impact(tmp_path: Path) -> None:
    initialized = ProjectInitializer().initialize(
        tmp_path,
        project_id="PROJECT-ROUTING",
        name="Routing",
        project_type=ProjectType.BACKEND,
        schema_version="1.0",
    )
    router = ProfileRouter(Database(initialized.database_path), initialized.config)

    assert router._select_profiles((), ()) == ("backend-project",)
    assert router._select_profiles(("backend", "backend"), ()) == ("backend-project",)
    assert router._select_profiles((), tuple(f"src/file-{index}.py" for index in range(20))) == (
        "backend-project",
        "large-project",
    )
    frontend = ProfileRouter(
        router.database,
        initialized.config.model_copy(update={"project_type": ProjectType.FRONTEND}),
    )
    assert frontend._select_profiles((), ()) == ("frontend-project",)
    fullstack = ProfileRouter(
        router.database,
        initialized.config.model_copy(update={"project_type": ProjectType.FULLSTACK}),
    )
    assert fullstack._select_profiles((), ()) == ("backend-project", "frontend-project")
    with pytest.raises(ValueError, match="unknown profiles"):
        router._select_profiles(("mobile",), ())

    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "chore: initialize routing fixture")
    started = WorkflowEngine(tmp_path).start("Persist canonical routing")
    decision = router.route(
        run_id=started.run.id,
        requested_profiles=("backend", "large-project"),
        impact_paths=("src/codex_ai_os/infrastructure/migrations/0008.sql",),
        source_commit="a" * 40,
        workflow_name="feature-development",
        dependency_count=1,
        release_required=True,
        override_reason="approved fixture override",
        require_task_mapping=True,
    )
    assert decision.profiles == ("backend-project", "large-project")
    assert decision.requested_profiles == ("backend", "large-project")
    assert decision.override
    assert decision.policy_hash
    assert decision.dimension_scores["release"] == 10
    assert decision.dependencies == (("backend-implementation", "database-migrations"),)
    assert decision.warnings == (
        "profile alias 'backend' is deprecated; use 'backend-project'",
    )
    with router.database.read_connection() as connection:
        row = connection.execute(
            """
            SELECT profiles_json, canonical_profiles_json, dimension_scores_json, workflow
            FROM routing_decisions WHERE run_id = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (started.run.id,),
        ).fetchone()
    assert row is not None
    assert "backend-project" in str(row["profiles_json"])
    assert str(row["profiles_json"]) == str(row["canonical_profiles_json"])
    assert "evidence" in str(row["dimension_scores_json"])
    assert row["workflow"] == "feature-development"
    persisted = router.input_for_run(started.run.id)
    assert persisted.release_required
    assert persisted.dependency_count == 1

    with pytest.raises(ValueError, match="ROUTING_PATH_UNMAPPED"):
        router.route(
            run_id=started.run.id,
            requested_profiles=("backend-project",),
            impact_paths=("docs/unmapped.md",),
            require_task_mapping=True,
        )


def test_profile_router_uses_yaml_profile_names_as_allowed_set(tmp_path: Path) -> None:
    initialized = ProjectInitializer().initialize(
        tmp_path,
        project_id="PROJECT-ROUTING-YAML",
        name="Routing YAML",
        project_type=ProjectType.BACKEND,
        schema_version="1.0",
    )
    profile_dir = tmp_path / "profiles"
    profile_dir.mkdir()
    (profile_dir / "backend-project.yaml").write_text(
        "schema_version: '1.1'\n"
        "name: backend-project\n"
        "description: Backend only fixture\n"
        "project_types: [backend]\n"
        "additional_skills: [backend-implementation]\n"
        "required_artifacts: [docs/API_SPEC.md]\n"
        "additional_reviewers: [reviewer]\n"
        "min_agents: 1\n",
        encoding="utf-8",
    )

    router = ProfileRouter(Database(initialized.database_path), initialized.config)

    assert router.allowed_profiles == frozenset({"backend-project"})
    assert router.select_profiles((), tuple(f"src/file-{index}.py" for index in range(20))) == (
        "backend-project",
    )
    with pytest.raises(ValueError, match="unknown profiles"):
        router.select_profiles(("large-project",), ())


def _git(root: Path, *arguments: str) -> None:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
