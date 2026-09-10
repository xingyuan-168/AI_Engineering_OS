from __future__ import annotations

from pathlib import Path

import pytest

from codex_ai_os.infrastructure.documents import DocumentManager, PathDeniedError


def test_initialize_and_check_baseline(tmp_path: Path) -> None:
    manager = DocumentManager(tmp_path)
    created = manager.initialize_documents("Demo", "generic", include=frozenset())
    assert "AGENTS.md" in created
    report = manager.check()
    assert report.ok is True
    assert report.missing == ()
    assert report.forbidden_directories == ()


def test_check_reports_missing_and_broken_links(tmp_path: Path) -> None:
    manager = DocumentManager(tmp_path)
    manager.initialize_documents("Demo", "generic", include=frozenset())
    (tmp_path / "docs" / "SCOPE.md").unlink()
    (tmp_path / "docs" / "REQUIREMENTS.md").write_text(
        "[ghost](./GHOST.md)\n", encoding="utf-8"
    )
    report = manager.check()
    assert report.ok is False
    assert "docs/SCOPE.md" in report.missing
    assert any("GHOST.md" in link for link in report.broken_links)


def test_copy_directory_under_docs_is_forbidden(tmp_path: Path) -> None:
    manager = DocumentManager(tmp_path)
    manager.initialize_documents("Demo", "generic", include=frozenset())
    (tmp_path / "docs" / "old").mkdir()
    (tmp_path / "docs" / "old" / "x.md").write_text("x", encoding="utf-8")
    report = manager.check()
    assert report.ok is False
    assert "docs/old" in report.forbidden_directories


def test_input_directory_is_not_flagged(tmp_path: Path) -> None:
    manager = DocumentManager(tmp_path)
    manager.initialize_documents("Demo", "generic", include=frozenset())
    (tmp_path / "input" / "spec.md").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "input" / "spec.md").write_text("user input", encoding="utf-8")
    report = manager.check()
    assert report.forbidden_directories == ()
    assert report.ok is True


def test_check_with_include_counts_conditionals(tmp_path: Path) -> None:
    manager = DocumentManager(tmp_path)
    manager.initialize_documents("Demo", "generic", include=frozenset({"api"}))
    report = manager.check(include=frozenset({"api"}))
    assert report.ok is True
    missing = manager.check(include=frozenset({"api", "database"}))
    assert "docs/DATABASE.md" in missing.missing


def test_write_atomic_refuses_escapes(tmp_path: Path) -> None:
    manager = DocumentManager(tmp_path)
    with pytest.raises(PathDeniedError):
        manager.write_atomic("../outside.md", "no", overwrite=True)
