from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from codex_ai_os.infrastructure.database import Database, MigrationError
from codex_ai_os.infrastructure.events import EventStore


def _register_project(database: Database, project_id: str = "PROJECT-TEST") -> None:
    with database.connection() as connection:
        connection.execute(
            """
            INSERT INTO projects(id, name, root, config_hash, created_at, updated_at)
            VALUES (?, 'test', 'C:/test', 'hash', 'now', 'now')
            """,
            (project_id,),
        )
        connection.commit()


def test_migrations_enable_required_sqlite_guards(tmp_path: Path) -> None:
    database = Database(tmp_path / ".codex-os" / "state" / "state.db")

    result = database.migrate()

    assert result.applied_versions == ("0001", "0002")
    assert result.current_version == "0002"
    assert result.backup_path is None
    assert database.current_version() == "0002"
    database.integrity_check()
    with database.connection() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert {"schema_migrations", "projects", "workflow_runs", "events"} <= tables


def test_migration_is_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.db")
    database.migrate()

    result = database.migrate()

    assert result.applied_versions == ()
    assert result.current_version == "0002"


def test_applied_migration_checksum_is_immutable(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.db")
    database.migrate()
    with database.connection() as connection:
        connection.execute("UPDATE schema_migrations SET checksum = 'tampered'")
        connection.commit()

    with pytest.raises(MigrationError, match="checksum mismatch"):
        database.migrate()


def test_failed_migration_rolls_back_without_history(tmp_path: Path) -> None:
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    (migration_dir / "0001_broken.sql").write_text(
        "CREATE TABLE valid_before_failure(id INTEGER);\nBROKEN SQL;\n",
        encoding="utf-8",
    )
    database = Database(tmp_path / "state.db", migrations_dir=migration_dir)

    with pytest.raises(MigrationError, match="0001_broken"):
        database.migrate()

    with database.connection() as connection:
        history = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'valid_before_failure'"
        ).fetchone()
    assert history == 0
    assert table is None


def test_existing_database_is_backed_up_before_pending_migration(tmp_path: Path) -> None:
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    (migration_dir / "0001_sample.sql").write_text(
        "CREATE TABLE sample(id INTEGER PRIMARY KEY);\n", encoding="utf-8"
    )
    database = Database(tmp_path / "state" / "state.db", migrations_dir=migration_dir)
    database.migrate()
    (migration_dir / "0002_more.sql").write_text(
        "ALTER TABLE sample ADD COLUMN name TEXT;\n", encoding="utf-8"
    )

    result = database.migrate()

    assert result.applied_versions == ("0002",)
    assert result.backup_path is not None
    assert result.backup_path.is_file()
    assert result.backup_path.with_suffix(".db.sha256").is_file()


def test_event_store_is_append_only_and_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.db")
    database.migrate()
    _register_project(database)
    store = EventStore(database)

    first = store.append(
        project_id="PROJECT-TEST",
        event_type="project.registered",
        payload={"name": "测试"},
        idempotency_key="project-register",
    )
    repeated = store.append(
        project_id="PROJECT-TEST",
        event_type="project.registered",
        payload={"name": "ignored duplicate"},
        idempotency_key="project-register",
    )

    assert repeated == first
    assert store.by_idempotency_key("project-register") == first
    assert store.list_for_project("PROJECT-TEST") == [first]


def test_event_store_enforces_project_foreign_key(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.db")
    database.migrate()

    with pytest.raises(sqlite3.IntegrityError):
        EventStore(database).append(
            project_id="PROJECT-MISSING",
            event_type="invalid",
            payload={},
        )
