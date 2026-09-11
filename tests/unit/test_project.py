from __future__ import annotations

from pathlib import Path

from codex_ai_os.application.project import ProjectInitializer


def test_initialize_creates_baseline(tmp_path: Path) -> None:
    result = ProjectInitializer().initialize(
        tmp_path,
        project_id="PROJECT-INIT",
        name="Init",
        project_type="generic",
        include=frozenset(),
    )
    for relative in (
        ".codex-os/project.yaml",
        ".gitignore",
        "AGENTS.md",
        "README.md",
        "input/.gitkeep",
        "output/.gitkeep",
        "docs/REQUIREMENTS.md",
        "docs/OPEN_SOURCE_RESEARCH.md",
    ):
        assert (tmp_path / relative).is_file(), relative
    assert result.document_report.ok is True
    assert result.database_path.is_file()
    assert result.repository_ready is False
    assert result.repository_blockers == ("NOT_GIT_REPOSITORY",)


def test_initialize_with_conditional_extras(tmp_path: Path) -> None:
    result = ProjectInitializer().initialize(
        tmp_path,
        project_id="PROJECT-EXTRA",
        name="Extra",
        project_type="fullstack",
        include=frozenset({"frontend_design", "docker"}),
    )
    joined = "\n".join(result.created_paths)
    assert "docs/design/PROTOTYPE.html" in joined
    assert "docs/design/UI_SPEC.md" in joined
    assert "compose.yaml" in joined
    assert result.document_report.ok is True


def test_initialize_is_idempotent(tmp_path: Path) -> None:
    initializer = ProjectInitializer()
    first = initializer.initialize(
        tmp_path,
        project_id="PROJECT-IDEM",
        name="Idem",
        project_type="generic",
        include=frozenset(),
    )
    second = initializer.initialize(
        tmp_path,
        project_id="PROJECT-IDEM",
        name="Idem",
        project_type="generic",
        include=frozenset(),
    )
    assert ".codex-os/project.yaml" not in second.created_paths
    assert "AGENTS.md" not in second.created_paths
    assert first.config.project_id == second.config.project_id


def test_fixture_local_policy_reports_ready(tmp_path: Path) -> None:
    result = ProjectInitializer().initialize(
        tmp_path,
        project_id="PROJECT-LOCAL",
        name="Local",
        project_type="generic",
        git_push_policy="fixture_local_only",
        include=frozenset(),
    )
    assert result.repository_ready is True
    assert result.repository_blockers == ()
