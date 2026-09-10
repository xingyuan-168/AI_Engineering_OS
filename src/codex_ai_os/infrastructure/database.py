"""SQLite connection, single lightweight migration, and integrity (ADR-0016).

The governance-core runtime keeps at most one numbered migration. A database
written by the pre-refactor runtime (schema 0001~0008) is exported to a
timestamped backup file and rebuilt from scratch; no data migration is
performed because the old runtime tables are superseded.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

_EXPECTED_TABLES: Final[frozenset[str]] = frozenset(
    {"schema_migrations", "tasks", "approvals", "worktrees", "memory_index"}
)
_EXPECTED_MIGRATION_COLUMNS: Final[frozenset[str]] = frozenset(
    {"version", "name", "checksum", "applied_at"}
)


class MigrationError(RuntimeError):
    """Raised when schema migration or validation fails."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: str
    name: str
    path: Path
    sql: str
    checksum: str


@dataclass(frozen=True, slots=True)
class MigrationResult:
    applied_versions: tuple[str, ...]
    current_version: str | None
    legacy_backup_path: Path | None


class Database:
    """Own the local SQLite runtime state and the lightweight migration."""

    def __init__(self, path: Path, *, migrations_dir: Path | None = None) -> None:
        self.path = path.resolve()
        self.migrations_dir = migrations_dir or Path(__file__).with_name("migrations")

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
        finally:
            connection.close()

    def migrate(self) -> MigrationResult:
        """Apply pending migrations, exporting a legacy database first."""

        legacy_backup = self._export_and_reset_if_legacy()
        migrations = self._discover_migrations()
        applied_now: list[str] = []
        with self.connection() as connection:
            self._bootstrap_migration_table(connection)
            applied = self._applied_migrations(connection)
            self._validate_applied_checksums(applied, migrations)
            for migration in migrations:
                if migration.version in applied:
                    continue
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    for statement in _split_sql(migration.sql):
                        connection.execute(statement)
                    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
                    if violations:
                        raise sqlite3.IntegrityError("migration foreign-key check failed")
                    connection.execute(
                        "INSERT INTO schema_migrations(version, name, checksum, applied_at) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            migration.version,
                            migration.name,
                            migration.checksum,
                            _utc_now(),
                        ),
                    )
                    connection.commit()
                    applied_now.append(migration.version)
                except sqlite3.Error as exc:
                    connection.rollback()
                    raise MigrationError(
                        f"migration {migration.version}_{migration.name} failed: {exc}"
                    ) from exc
            self._integrity_check_connection(connection)
        current = migrations[-1].version if migrations else None
        return MigrationResult(tuple(applied_now), current, legacy_backup)

    def current_version(self) -> str | None:
        if not self.path.is_file():
            return None
        connection = sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True, timeout=5.0)
        connection.row_factory = sqlite3.Row
        try:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'schema_migrations'"
            ).fetchone()
            if table is None:
                return None
            row = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
            ).fetchone()
            return str(row[0]) if row is not None else None
        except sqlite3.Error:
            return None
        finally:
            connection.close()

    def integrity_check(self) -> None:
        with self.connection() as connection:
            self._integrity_check_connection(connection)

    def _export_and_reset_if_legacy(self) -> Path | None:
        """Export a pre-refactor database to a backup file and reset it.

        Legacy detection is observational: any unexpected table, an
        incompatible schema_migrations layout, or a checksum mismatch marks
        the database as pre-refactor. The export is a plain file copy plus a
        SHA-256 sidecar; the runtime database is then rebuilt empty.
        """

        if not self.path.exists() or self.path.stat().st_size == 0:
            return None
        if not self._is_legacy():
            return None
        backup_dir = self.path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = backup_dir / f"{self.path.stem}-legacy-{stamp}.db"
        shutil.copy2(self.path, backup_path)
        digest = hashlib.sha256(backup_path.read_bytes()).hexdigest()
        backup_path.with_suffix(".db.sha256").write_text(
            f"{digest}  {backup_path.name}\n", encoding="utf-8"
        )
        for suffix in ("", "-wal", "-shm"):
            Path(f"{self.path}{suffix}").unlink(missing_ok=True)
        return backup_path

    def _is_legacy(self) -> bool:
        try:
            connection = sqlite3.connect(
                f"{self.path.as_uri()}?mode=ro", uri=True, timeout=5.0
            )
            connection.row_factory = sqlite3.Row
        except sqlite3.Error:
            return True
        try:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            if not tables:
                return False
            if not tables <= _EXPECTED_TABLES:
                return True
            if "schema_migrations" not in tables:
                return True
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(schema_migrations)").fetchall()
            }
            if columns != _EXPECTED_MIGRATION_COLUMNS:
                return True
            migrations = {migration.version: migration for migration in self._discover_migrations()}
            for row in connection.execute(
                "SELECT version, checksum FROM schema_migrations"
            ).fetchall():
                migration = migrations.get(str(row["version"]))
                if migration is None or migration.checksum != str(row["checksum"]):
                    return True
            return False
        except (sqlite3.Error, MigrationError):
            return True
        finally:
            connection.close()

    def _discover_migrations(self) -> list[Migration]:
        if not self.migrations_dir.is_dir():
            raise MigrationError(f"migration directory not found: {self.migrations_dir}")
        migrations: list[Migration] = []
        for path in sorted(self.migrations_dir.glob("[0-9][0-9][0-9][0-9]_*.sql")):
            version, name_with_suffix = path.name.split("_", 1)
            sql = path.read_text(encoding="utf-8")
            migrations.append(
                Migration(
                    version=version,
                    name=name_with_suffix.removesuffix(".sql"),
                    path=path,
                    sql=sql,
                    checksum=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
                )
            )
        versions = [migration.version for migration in migrations]
        if len(versions) != len(set(versions)):
            raise MigrationError("migration versions must be unique")
        return migrations

    @staticmethod
    def _bootstrap_migration_table(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        connection.commit()

    @staticmethod
    def _applied_migrations(connection: sqlite3.Connection) -> dict[str, str]:
        rows = connection.execute("SELECT version, checksum FROM schema_migrations").fetchall()
        return {str(row["version"]): str(row["checksum"]) for row in rows}

    @staticmethod
    def _validate_applied_checksums(
        applied: dict[str, str], migrations: list[Migration]
    ) -> None:
        available = {migration.version: migration for migration in migrations}
        for version, checksum in applied.items():
            migration = available.get(version)
            if migration is None:
                raise MigrationError(f"applied migration {version} is missing from the package")
            if migration.checksum != checksum:
                raise MigrationError(f"checksum mismatch for applied migration {version}")

    @staticmethod
    def _integrity_check_connection(connection: sqlite3.Connection) -> None:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            raise MigrationError(f"SQLite integrity check failed: {result}")
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise MigrationError(f"SQLite foreign-key violations: {len(violations)}")


def _split_sql(script: str) -> list[str]:
    statements: list[str] = []
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                statements.append(statement)
            buffer = ""
    if buffer.strip():
        raise MigrationError("migration SQL ends with an incomplete statement")
    return statements


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "Database",
    "Migration",
    "MigrationError",
    "MigrationResult",
]
