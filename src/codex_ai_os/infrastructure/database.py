"""SQLite connection, migration, backup, and integrity management."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from collections.abc import Generator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from codex_ai_os.domain.versions import RUNTIME_VERSIONS


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

    @contextmanager
    def read_connection(self) -> Generator[sqlite3.Connection]:
        """Open an existing database without creating files or changing journal mode."""

        if not self.path.is_file():
            raise MigrationError(f"database does not exist: {self.path}")
        connection = sqlite3.connect(
            f"{self.path.as_uri()}?mode=ro",
            uri=True,
            timeout=5.0,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA query_only = ON")
        try:
            yield connection
        finally:
            connection.close()

    def migrate(
        self,
        *,
        app_version: str = RUNTIME_VERSIONS.software,
        applied_by: str = "codex-os",
    ) -> MigrationResult:
        existed_before = self.path.exists() and self.path.stat().st_size > 0
        migrations = self._discover_migrations()
        backup_path: Path | None = None
        failure: MigrationError | None = None
        applied_now: list[str] = []

        with self.connection() as connection:
            self._bootstrap_migration_table(connection)
            applied = self._applied_migrations(connection)
            self._validate_applied_checksums(applied, migrations)
            pending = [migration for migration in migrations if migration.version not in applied]
            backup_path = self._backup(connection) if existed_before and pending else None

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
                    failure = MigrationError(
                        f"migration {migration.version}_{migration.name} failed: {exc}"
                    )
                    failure.__cause__ = exc
                    break

            if failure is None:
                try:
                    self._integrity_check_connection(connection)
                    self._fts_check_connection(connection)
                except MigrationError as exc:
                    failure = exc

        if failure is not None:
            if backup_path is not None:
                self._restore_backup(backup_path)
            raise failure

        current = migrations[-1].version if migrations else None
        return MigrationResult(tuple(applied_now), current, backup_path)

    def integrity_check(self) -> None:
        with self.connection() as connection:
            self._integrity_check_connection(connection)

    def current_version(self) -> str | None:
        if not self.path.is_file():
            return None
        with self.read_connection() as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'schema_migrations'"
            ).fetchone()
            if table is None:
                return None
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
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        backup_dir = self.path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = backup_dir / f"{self.path.stem}-{stamp}-pre-migration.db"
        with closing(sqlite3.connect(backup_path)) as backup_connection:
            connection.backup(backup_connection)
            backup_connection.commit()
        digest = hashlib.sha256(backup_path.read_bytes()).hexdigest()
        backup_path.with_suffix(".db.sha256").write_text(
            f"{digest}  {backup_path.name}\n",
            encoding="utf-8",
        )
        self._verify_backup(backup_path)
        return backup_path

    def _verify_backup(self, backup_path: Path) -> None:
        checksum_path = backup_path.with_suffix(".db.sha256")
        try:
            expected = checksum_path.read_text(encoding="utf-8").split()[0]
        except (OSError, IndexError) as exc:
            raise MigrationError(f"backup checksum cannot be read: {checksum_path}") from exc
        actual = hashlib.sha256(backup_path.read_bytes()).hexdigest()
        if actual != expected:
            raise MigrationError(f"backup checksum mismatch: {backup_path}")
        with closing(sqlite3.connect(backup_path)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            self._integrity_check_connection(connection)
            self._fts_check_connection(connection)

    def _restore_backup(self, backup_path: Path) -> None:
        self._verify_backup(backup_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        failed_path: Path | None = None
        with tempfile.NamedTemporaryFile(
            delete=False,
            dir=self.path.parent,
            prefix=f".{self.path.stem}.restore-",
            suffix=".db",
        ) as handle:
            temporary = Path(handle.name)
        try:
            source_uri = f"{backup_path.resolve().as_uri()}?mode=ro"
            with closing(sqlite3.connect(source_uri, uri=True)) as source, closing(
                sqlite3.connect(temporary)
            ) as target:
                source.backup(target)
                target.commit()
            with closing(sqlite3.connect(temporary)) as restored:
                restored.execute("PRAGMA foreign_keys = ON")
                self._integrity_check_connection(restored)
                self._fts_check_connection(restored)

            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            if self.path.exists():
                failed_path = self.path.with_name(
                    f"{self.path.stem}-{stamp}-failed-migration.db"
                )
                os.replace(self.path, failed_path)
                self._preserve_sidecars(failed_path)
            os.replace(temporary, self.path)
            temporary = None
            with self.read_connection() as restored:
                self._integrity_check_connection(restored)
                self._fts_check_connection(restored)
        except (OSError, sqlite3.Error, MigrationError) as exc:
            if failed_path is not None and failed_path.exists():
                if self.path.exists():
                    broken_restore = failed_path.with_name(
                        f"{failed_path.stem}-restore-attempt.db"
                    )
                    os.replace(self.path, broken_restore)
                os.replace(failed_path, self.path)
            if isinstance(exc, MigrationError):
                raise
            raise MigrationError(f"atomic database restore failed: {exc}") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _preserve_sidecars(self, failed_path: Path) -> None:
        for suffix in ("-wal", "-shm"):
            source = Path(f"{self.path}{suffix}")
            if source.exists():
                os.replace(source, Path(f"{failed_path}{suffix}"))

    @staticmethod
    def _integrity_check_connection(connection: sqlite3.Connection) -> None:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            raise MigrationError(f"SQLite integrity check failed: {result}")
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise MigrationError(f"SQLite foreign-key violations: {len(violations)}")

    @staticmethod
    def _fts_check_connection(connection: sqlite3.Connection) -> None:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memory_fts'"
        ).fetchone()
        if table is None:
            return
        try:
            connection.execute("SELECT count(*) FROM memory_fts").fetchone()
        except sqlite3.Error as exc:
            raise MigrationError(f"SQLite FTS validation failed: {exc}") from exc


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
