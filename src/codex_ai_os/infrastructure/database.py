"""SQLite connection, migration, backup, and integrity management."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


class MigrationError(RuntimeError):
    """Raised when schema migration or checksum validation fails."""


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
    backup_path: Path | None


class Database:
    """Own the local SQLite runtime state and numbered SQL migrations."""

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

    def migrate(
        self,
        *,
        app_version: str = "0.1.0",
        applied_by: str = "codex-os",
    ) -> MigrationResult:
        existed_before = self.path.exists() and self.path.stat().st_size > 0
        migrations = self._discover_migrations()

        with self.connection() as connection:
            self._bootstrap_migration_table(connection)
            applied = self._applied_migrations(connection)
            self._validate_applied_checksums(applied, migrations)
            pending = [migration for migration in migrations if migration.version not in applied]
            backup_path = self._backup(connection) if existed_before and pending else None
            applied_now: list[str] = []

            for migration in pending:
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    for statement in _split_sql(migration.sql):
                        connection.execute(statement)
                    connection.execute(
                        """
                        INSERT INTO schema_migrations(
                            version, name, app_version, checksum, applied_by, applied_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            migration.version,
                            migration.name,
                            app_version,
                            migration.checksum,
                            applied_by,
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

            current = migrations[-1].version if migrations else None
            return MigrationResult(tuple(applied_now), current, backup_path)

    def integrity_check(self) -> None:
        with self.connection() as connection:
            result = connection.execute("PRAGMA integrity_check").fetchone()
            if result is None or result[0] != "ok":
                raise MigrationError(f"SQLite integrity check failed: {result}")
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise MigrationError(f"SQLite foreign-key violations: {len(violations)}")

    def current_version(self) -> str | None:
        with self.connection() as connection:
            self._bootstrap_migration_table(connection)
            row = connection.execute(
                "SELECT version FROM schema_migrations "
                "ORDER BY applied_at DESC, version DESC LIMIT 1"
            ).fetchone()
            return str(row[0]) if row is not None else None

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
                app_version TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_by TEXT NOT NULL,
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

    def _backup(self, connection: sqlite3.Connection) -> Path:
        backup_dir = self.path.parent.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = backup_dir / f"{self.path.stem}-{stamp}.db"
        with sqlite3.connect(backup_path) as backup_connection:
            connection.backup(backup_connection)
        digest = hashlib.sha256(backup_path.read_bytes()).hexdigest()
        backup_path.with_suffix(".db.sha256").write_text(
            f"{digest}  {backup_path.name}\n",
            encoding="utf-8",
        )
        return backup_path


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
