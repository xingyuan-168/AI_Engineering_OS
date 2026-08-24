# pyright: reportPrivateUsage=false
from __future__ import annotations

from pathlib import Path

import pytest

from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.application.routing import ProfileRouter
from codex_ai_os.domain.config import ProjectType
from codex_ai_os.infrastructure.database import Database


def test_profile_router_selects_smallest_team_and_large_impact(tmp_path: Path) -> None:
    initialized = ProjectInitializer().initialize(
        tmp_path,
        project_id="PROJECT-ROUTING",
        name="Routing",
        project_type=ProjectType.BACKEND,
        schema_version="1.1",
    )
    router = ProfileRouter(Database(initialized.database_path), initialized.config)

    assert router._select_profiles((), ()) == ("backend",)
    assert router._select_profiles(("backend", "backend"), ()) == ("backend",)
    assert router._select_profiles((), tuple(f"src/file-{index}.py" for index in range(20))) == (
        "backend",
        "large",
    )
    frontend = ProfileRouter(
        router.database,
        initialized.config.model_copy(update={"project_type": ProjectType.FRONTEND}),
    )
    assert frontend._select_profiles((), ()) == ("frontend",)
    fullstack = ProfileRouter(
        router.database,
        initialized.config.model_copy(update={"project_type": ProjectType.FULLSTACK}),
    )
    assert fullstack._select_profiles((), ()) == ("backend", "frontend")
    with pytest.raises(ValueError, match="unknown profiles"):
        router._select_profiles(("mobile",), ())
