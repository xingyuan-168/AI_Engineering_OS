from pathlib import Path

from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.domain.config import ProjectType
from codex_ai_os.infrastructure.database import Database


def test_project_initialization_is_idempotent_and_preserves_user_content(tmp_path: Path) -> None:
    initializer = ProjectInitializer()

    first = initializer.initialize(
        tmp_path,
        project_id="PROJECT-ERP",
        name="ERP pilot",
        project_type=ProjectType.BACKEND,
    )
    product_requirements = tmp_path / "docs" / "PRODUCT_REQUIREMENTS.md"
    product_requirements.write_text("# User-owned requirements\n", encoding="utf-8")
    second = initializer.initialize(
        tmp_path,
        project_id="PROJECT-IGNORED",
        name="ignored",
        project_type=ProjectType.FRONTEND,
    )

    assert first.config.project_id == "PROJECT-ERP"
    assert second.config.project_id == "PROJECT-ERP"
    assert second.created_paths == ()
    assert product_requirements.read_text(encoding="utf-8") == "# User-owned requirements\n"
    assert first.document_report.ok
    assert second.document_report.ok
    assert first.context_path.is_file()
    assert Database(first.database_path).current_version() == "0001"


def test_project_registration_and_init_event_are_persisted(tmp_path: Path) -> None:
    result = ProjectInitializer().initialize(
        tmp_path,
        project_id="PROJECT-ERP",
        name="ERP pilot",
        project_type=ProjectType.BACKEND,
    )

    with Database(result.database_path).connection() as connection:
        project = connection.execute("SELECT * FROM projects").fetchone()
        event_count = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    assert project["id"] == "PROJECT-ERP"
    assert event_count == 1
