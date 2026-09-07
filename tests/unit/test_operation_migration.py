from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from codex_ai_os.domain.operations import HostOperationKind
from codex_ai_os.infrastructure.database import Database, MigrationError
from codex_ai_os.infrastructure.operations import HostOperationStore


def legacy_database(tmp_path: Path) -> tuple[Database, Path]:
    packaged = Database(tmp_path / "unused.db").migrations_dir
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for path in packaged.glob("000[1-7]_*.sql"):
        shutil.copy2(path, migrations / path.name)
    database = Database(tmp_path / "state.db", migrations_dir=migrations)
    assert database.migrate().current_version == "0007"
    with database.connection() as connection:
        connection.execute(
            "INSERT INTO projects(id,name,root,config_hash,created_at,updated_at) "
            "VALUES ('PROJECT-MIGRATION','test','/fixture','hash','now','now')"
        )
        connection.commit()
    source = packaged / "0008_operation_reliability.sql"
    return database, source


@pytest.mark.parametrize("operation_type", ["environment_adopt", "environment_verify", "environment_unknown"])
def test_0008_preserves_references_and_revalidates_old_environment_operations(
    tmp_path: Path, operation_type: str,
) -> None:
    database, source = legacy_database(tmp_path)
    store = HostOperationStore(database)
    operation = store.ensure_pending(
        project_id="PROJECT-MIGRATION", idempotency_key="old-environment",
        kind=(HostOperationKind.INTEGRATION_PREPARE if operation_type == "environment_adopt"
              else HostOperationKind.VERIFICATION_PREPARE),
        request={"operation_type": operation_type},
    )
    with database.connection() as connection:
        connection.execute(
            "INSERT INTO api_call_audits(call_id,request_id,correlation_id,principal,operation,"
            "host_operation_id,status,duration_ms,request_summary_json,response_summary_json,created_at) "
            "VALUES ('CALL-OLD','REQ-OLD','CORR-OLD','fixture','prepare',?,'succeeded',1,'{}','{}','now')",
            (operation.operation_id,),
        )
        connection.commit()
    shutil.copy2(source, database.migrations_dir / source.name)
    result = database.migrate()
    assert result.current_version == "0008"
    assert result.backup_path is not None
    database.integrity_check()
    migrated = store.get(operation.operation_id)
    assert migrated.status.value == "reconcile_required"
    assert migrated.request == operation.request
    assert migrated.kind.value == (
        operation_type if operation_type != "environment_unknown" else "verification_prepare"
    )
    with database.read_connection() as connection:
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
        snapshot = connection.execute("SELECT * FROM host_operation_legacy_snapshots").fetchone()
        assert snapshot is not None and snapshot["original_status"] == "pending"
        assert connection.execute("SELECT host_operation_id FROM api_call_audits").fetchone()[0] == (
            operation.operation_id
        )
    assert database.migrate().applied_versions == ()


def test_0008_rebuild_failure_restores_previous_schema_and_data(tmp_path: Path) -> None:
    database, source = legacy_database(tmp_path)
    target = database.migrations_dir / source.name
    target.write_text(source.read_text(encoding="utf-8") + "\nBROKEN SQL;\n", encoding="utf-8")
    with pytest.raises(MigrationError, match="0008"):
        database.migrate()
    assert database.current_version() == "0007"
    database.integrity_check()
    with database.connection() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO host_operations(operation_id,project_id,kind,idempotency_key,request_hash,"
                "status,request_json,created_at,updated_at) "
                "VALUES ('BAD','missing','integration_prepare','key',?,'pending','{}','now','now')",
                ("a" * 64,),
            )
