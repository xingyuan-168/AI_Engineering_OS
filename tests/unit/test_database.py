from __future__ import annotations

import sqlite3
from pathlib import Path

from codex_ai_os.infrastructure.database import Database


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.db")
    first = database.migrate()
    assert first.current_version == "0001"
    assert first.legacy_backup_path is None
    second = database.migrate()
    assert second.applied_versions == ()
    assert second.current_version == "0001"
    database.integrity_check()


def test_expected_tables_exist(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.db")
    database.migrate()
    with database.connection() as connection:
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {"schema_migrations", "tasks", "approvals", "worktrees", "memory_index"} <= names


def test_legacy_database_is_exported_and_rebuilt(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    legacy = sqlite3.connect(path)
    legacy.execute("CREATE TABLE projects (id TEXT PRIMARY KEY)")
    legacy.execute("INSERT INTO projects VALUES ('PROJECT-OLD')")
    legacy.commit()
    legacy.close()

    database = Database(path)
    result = database.migrate()
    assert result.legacy_backup_path is not None
    backup = Path(result.legacy_backup_path)
    assert backup.is_file()
    assert Path(str(backup) + ".sha256").is_file()
    assert result.current_version == "0001"
    with database.connection() as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "projects" not in tables
    assert "tasks" in tables
