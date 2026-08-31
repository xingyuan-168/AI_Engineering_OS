from pathlib import Path

import pytest

from codex_ai_os.infrastructure.documents import DocumentManager, PathDeniedError


def test_atomic_write_does_not_overwrite_existing_content(tmp_path: Path) -> None:
    manager = DocumentManager(tmp_path)
    assert manager.write_atomic("docs/example.md", "# Original\n", overwrite=False)
    assert not manager.write_atomic("docs/example.md", "# Replacement\n", overwrite=False)
    assert (tmp_path / "docs" / "example.md").read_text(encoding="utf-8") == "# Original\n"


@pytest.mark.parametrize("relative", ["../outside.md", Path("..") / "outside.md"])
def test_path_escape_is_denied(tmp_path: Path, relative: str | Path) -> None:
    with pytest.raises(PathDeniedError, match="escapes"):
        DocumentManager(tmp_path).resolve(relative)


def test_document_check_reports_missing_broken_and_invalid_files(tmp_path: Path) -> None:
    manager = DocumentManager(tmp_path)
    manager.write_atomic(
        "docs/README.md",
        "not a heading\n[missing](MISSING.md)\n",
        overwrite=False,
    )

    report = manager.check("backend")

    assert not report.ok
    assert "docs/PROJECT_MASTER.md" in report.missing
    assert "docs/README.md" in report.invalid_documents
    assert "docs/README.md -> MISSING.md" in report.broken_links
    assert "docs/README.md: missing governance metadata" in report.metadata_errors


def test_initialized_documents_pass_governance_check(tmp_path: Path) -> None:
    manager = DocumentManager(tmp_path)
    manager.initialize_documents("ERP", "backend")
    (tmp_path / ".venv" / "Lib" / "package" / "v1").mkdir(parents=True)

    report = manager.check("backend")

    assert report.ok
    assert report.checked_files >= 10


def test_project_owned_copy_directory_is_still_rejected(tmp_path: Path) -> None:
    manager = DocumentManager(tmp_path)
    manager.initialize_documents("ERP", "backend")
    (tmp_path / "src" / "v1").mkdir(parents=True)

    report = manager.check("backend")

    assert not report.ok
    assert report.forbidden_directories == ("src/v1",)


def test_generated_context_contains_source_hashes(tmp_path: Path) -> None:
    manager = DocumentManager(tmp_path)
    manager.write_atomic("docs/README.md", "# Docs\n", overwrite=False)

    path = manager.generate_context()

    context = path.read_text(encoding="utf-8")
    assert "Derived cache" in context
    assert "`docs/README.md`" in context


def test_archived_document_does_not_require_active_metadata(tmp_path: Path) -> None:
    manager = DocumentManager(tmp_path)
    manager.initialize_documents("ERP", "backend")
    manager.write_atomic("docs/archive/legacy.md", "# Legacy\n", overwrite=False)

    report = manager.check("backend")

    assert not any("docs/archive/legacy.md" in item for item in report.metadata_errors)


def test_document_check_uses_project_document_version_target(tmp_path: Path) -> None:
    manager = DocumentManager(tmp_path)
    manager.initialize_documents("ERP", "backend", document_version="0.1.0")

    report = manager.check("backend", expected_document_version="0.2.0")

    assert not report.ok
    assert "docs/README.md" in report.version_mismatches


def test_traceability_reports_unmapped_refs_and_missing_paths(tmp_path: Path) -> None:
    manager = DocumentManager(tmp_path)
    manager.initialize_documents("ERP", "backend")
    manager.write_atomic(
        ".codex-os/test-traceability.yaml",
        """schema_version: '1.2'
entries:
  - id: TRACE-TEST
    requirement_refs: [OTHER-001]
    specification_paths: [docs/README.md]
    test_paths: [tests/missing.py]
""",
        overwrite=False,
    )

    report = manager.check("backend")

    assert not report.ok
    assert any("missing tests/missing.py" in item for item in report.traceability_errors)
    assert any("unmapped PROJECT-INIT" in item for item in report.traceability_errors)
