from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from codex_ai_os.infrastructure.database import Database, MigrationError


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


def test_unknown_database_is_preserved(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    legacy = sqlite3.connect(path)
    legacy.execute("CREATE TABLE projects (id TEXT PRIMARY KEY)")
    legacy.execute("INSERT INTO projects VALUES ('PROJECT-OLD')")
    legacy.commit()
    legacy.close()

    database = Database(path)
    original = path.read_bytes()
    for explicit in (False, True):
        with pytest.raises(MigrationError, match="MIGRATION_UNKNOWN_SCHEMA"):
            database.migrate(allow_legacy=explicit)
        assert path.read_bytes() == original


def legacy_database(path: Path) -> sqlite3.Connection:
    sql = (Path(__file__).parents[1] / "fixtures/runtime_12b723b.sql").read_text(encoding="utf-8")
    connection = sqlite3.connect(path)
    Database._bootstrap_migration_table(connection)
    connection.executescript(sql)
    connection.execute(
        "INSERT INTO schema_migrations VALUES (?, ?, ?, ?)",
        ("0001", "initial", hashlib.sha256(sql.encode()).hexdigest(), "2026-09-11"),
    )
    connection.commit()
    return connection


def test_explicit_migration_backs_up_committed_wal(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    writer = legacy_database(path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("INSERT INTO tasks VALUES ('TASK-WAL','WAL data',NULL,'open','now','now')")
    writer.commit()
    assert Path(str(path) + "-wal").stat().st_size > 0
    database = Database(path)
    with pytest.raises(MigrationError, match="MIGRATION_REQUIRED"):
        database.migrate()
    result = database.migrate(allow_legacy=True)
    assert result.legacy_backup_path is not None
    with sqlite3.connect(result.legacy_backup_path) as backup:
        assert backup.execute("SELECT title FROM tasks").fetchone()[0] == "WAL data"
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert result.legacy_backup_path.with_suffix(".db.sha256").is_file()
    with database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
        assert "target_branch" in {
            row[1] for row in connection.execute("PRAGMA table_info(worktrees)")
        }
    writer.close()


@pytest.mark.parametrize("case", ["busy", "active", "backup_failure", "checksum", "structure"])
def test_unsafe_migration_preserves_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    path = tmp_path / "state.db"
    writer = legacy_database(path)
    if case == "busy":
        writer.execute("BEGIN IMMEDIATE")
    elif case == "active":
        writer.execute(
            "INSERT INTO worktrees VALUES "
            "('w',NULL,'w','.worktrees/w','codex/wt/w',1,'active','n','n')"
        )
        writer.commit()
    elif case == "backup_failure":
        monkeypatch.setattr(
            Path,
            "write_text",
            lambda *a, **kw: (_ for _ in ()).throw(OSError("fixture write failed")),
        )
    elif case == "checksum":
        writer.execute("UPDATE schema_migrations SET checksum='unknown'")
        writer.commit()
    else:
        writer.execute("CREATE VIEW undocumented AS SELECT * FROM tasks")
        writer.commit()
    with pytest.raises(MigrationError):
        Database(path).migrate(allow_legacy=True)
    assert "target_branch" not in {row[1] for row in writer.execute("PRAGMA table_info(worktrees)")}
    writer.close()


def test_corrupt_database_is_preserved(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    path.write_bytes(b"not sqlite and not disposable")
    with pytest.raises(MigrationError):
        Database(path).migrate(allow_legacy=True)
    assert path.read_bytes() == b"not sqlite and not disposable"


def test_current_checksum_does_not_hide_schema_drift(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.db")
    database.migrate()
    with database.connection() as connection:
        connection.execute("ALTER TABLE tasks ADD COLUMN user_asset TEXT")
    with pytest.raises(MigrationError, match="MIGRATION_UNKNOWN_SCHEMA"):
        database.migrate(allow_legacy=True)
