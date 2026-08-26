from __future__ import annotations

import shutil
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

    assert result.applied_versions == (
        "0001",
        "0002",
        "0003",
        "0004",
        "0005",
        "0006",
        "0007",
    )
    assert result.current_version == "0007"
    assert result.backup_path is None
    assert database.current_version() == "0007"
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
    assert {
        "schema_migrations",
        "projects",
        "workflow_runs",
        "events",
        "host_operations",
        "api_call_audits",
    } <= tables


def test_migration_is_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.db")
    database.migrate()

    result = database.migrate()

    assert result.applied_versions == ()
    assert result.current_version == "0007"


def test_current_version_is_read_only_for_missing_database(tmp_path: Path) -> None:
    path = tmp_path / "missing" / "state.db"

    assert Database(path).current_version() is None
    assert not path.parent.exists()


def test_0006_to_0007_preserves_active_workflow_and_requires_revalidation(
    tmp_path: Path,
) -> None:
    packaged = Database(tmp_path / "unused.db").migrations_dir
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    for source in sorted(packaged.glob("000[1-6]_*.sql")):
        shutil.copy2(source, migration_dir / source.name)
    database = Database(tmp_path / "state" / "state.db", migrations_dir=migration_dir)
    assert database.migrate().current_version == "0006"
    _register_project(database)
    with database.connection() as connection:
        connection.execute(
            """
            INSERT INTO workflow_runs(
                id, project_id, workflow_name, goal, workflow_phase, run_status,
                risk_level, checkpoint_json, config_hash, created_at, updated_at,
                migration_revalidation_required
            ) VALUES (
                'RUN-LEGACY', 'PROJECT-TEST', 'new-project', 'legacy goal',
                'implementation', 'running', 'high', '{}', 'hash', 'now', 'now', 0
            )
            """
        )
        connection.commit()
    shutil.copy2(
        packaged / "0007_release_closure.sql",
        migration_dir / "0007_release_closure.sql",
    )

    result = database.migrate()

    assert result.applied_versions == ("0007",)
    assert result.current_version == "0007"
    assert result.backup_path is not None
    with database.read_connection() as connection:
        run = connection.execute(
            "SELECT migration_revalidation_required FROM workflow_runs "
            "WHERE id = 'RUN-LEGACY'"
        ).fetchone()
        group_count = connection.execute("SELECT COUNT(*) FROM task_groups").fetchone()[0]
    assert run is not None and run[0] == 1
    assert group_count == 0


def test_0007_adds_release_closure_columns_and_irreversible_acceptance(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.db")
    database.migrate()
    _register_project(database)
    with database.connection() as connection:
        connection.execute(
            """
            INSERT INTO workflow_runs(
                id, project_id, workflow_name, goal, workflow_phase, run_status,
                risk_level, checkpoint_json, config_hash, created_at, updated_at
            ) VALUES (
                'RUN-1', 'PROJECT-TEST', 'new-project', 'goal', 'implementation',
                'running', 'high', '{}', 'hash', 'now', 'now'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO tasks(
                id, run_id, agent, status, input_hash, producer, created_at, updated_at
            ) VALUES ('TASK-1', 'RUN-1', 'backend-engineer', 'completed', 'input',
                      'backend-engineer', 'now', 'now')
            """
        )
        connection.execute(
            """
            INSERT INTO handoffs(
                id, run_id, task_id, producer, consumer, status, artifact_refs_json,
                commit_refs_json, tests_json, risks_json, open_questions_json,
                created_at, updated_at
            ) VALUES ('HANDOFF-1', 'RUN-1', 'TASK-1', 'backend-engineer', 'reviewer',
                      'ready', '[]', '[]', '[]', '[]', '[]', 'now', 'now')
            """
        )
        connection.execute(
            """
            UPDATE handoffs SET status = 'accepted', reviewer = 'reviewer',
                reviewed_commit = ?, state_version = state_version + 1
            WHERE id = 'HANDOFF-1'
            """,
            ("a" * 40,),
        )
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError, match="invalid handoff"):
            connection.execute(
                "UPDATE handoffs SET status = 'blocked' WHERE id = 'HANDOFF-1'"
            )

        memory_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(memory_records)")
        }
        release_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(release_records)")
        }
        check_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(check_evidence)")
        }
    assert "state_version" in memory_columns
    assert {
        "integration_source_commit",
        "candidate_commit",
        "final_manifest_hash",
        "external_reconciliation_json",
        "publish_operation_id",
    } <= release_columns
    assert {"started_at", "ended_at"} <= check_columns


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


def test_failed_pending_migration_restores_verified_database_atomically(
    tmp_path: Path,
) -> None:
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    (migration_dir / "0001_sample.sql").write_text(
        "CREATE TABLE sample(id INTEGER PRIMARY KEY, value TEXT);\n",
        encoding="utf-8",
    )
    database = Database(tmp_path / "state" / "state.db", migrations_dir=migration_dir)
    database.migrate()
    with database.connection() as connection:
        connection.execute("INSERT INTO sample(id, value) VALUES (1, 'preserved')")
        connection.commit()
    (migration_dir / "0002_broken.sql").write_text(
        "ALTER TABLE sample ADD COLUMN transient TEXT;\nBROKEN SQL;\n",
        encoding="utf-8",
    )

    with pytest.raises(MigrationError, match="0002_broken"):
        database.migrate()

    database.integrity_check()
    with database.read_connection() as connection:
        row = connection.execute("SELECT value FROM sample WHERE id = 1").fetchone()
        columns = {str(item[1]) for item in connection.execute("PRAGMA table_info(sample)")}
    assert row is not None and row[0] == "preserved"
    assert "transient" not in columns
    assert list((tmp_path / "state").glob("*-failed-migration.db"))


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
