from pathlib import Path

from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.domain.config import EnvironmentMode, ProjectType
from codex_ai_os.infrastructure.config import load_environment_contract, load_execution_policy
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
    assert not second.document_report.ok
    assert second.document_report.metadata_errors == (
        "docs/PRODUCT_REQUIREMENTS.md: missing governance metadata",
    )
    assert first.context_path.is_file()
    assert load_execution_policy(tmp_path).sandbox.value == "podman"
    assert first.config.environment_mode is EnvironmentMode.OCI_FIRST
    assert load_environment_contract(tmp_path).oci_backend.value == "podman"
    assert (tmp_path / "compose.yaml").read_text(encoding="utf-8").endswith(
        "services: {}\n"
    )
    assert (tmp_path / ".dockerignore").is_file()
    assert (tmp_path / "docs" / "ENVIRONMENT.md").is_file()
    assert (tmp_path / ".codex-os" / "gates" / "oci-first" / "G3.yaml").is_file()
    assert Database(first.database_path).current_version() == "0007"
    config_text = (tmp_path / ".codex-os" / "project.yaml").read_text(encoding="utf-8")
    assert "schema_version: '1.2'" in config_text
    assert "root: ." in config_text
    assert "source_of_truth: docs" in config_text
    assert "environment_mode: oci-first" in config_text


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
