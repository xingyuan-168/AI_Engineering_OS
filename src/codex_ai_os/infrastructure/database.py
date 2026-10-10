"""SQLite connection, single lightweight migration, and integrity (ADR-0016).

The runtime keeps one numbered migration. Ordinary access never rebuilds
existing state. Only an explicitly requested, fingerprinted predecessor
can be backed up consistently and replaced transactionally; unknown or
damaged databases are preserved for inspection.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from codex_ai_os.adapters.git import GitRunner

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

    def migrate(self, *, allow_legacy: bool = False) -> MigrationResult:
        """Create/validate current state; rebuilding a known predecessor is explicit."""

        legacy_backup = self._export_and_reset_if_legacy(allow_legacy=allow_legacy)
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
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
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

    def _export_and_reset_if_legacy(self, *, allow_legacy: bool) -> Path | None:
        if not self.path.exists() or self.path.stat().st_size == 0 or not self._is_legacy():
            return None
        if not allow_legacy:
            raise MigrationError(
                "MIGRATION_REQUIRED: run init --migrate-runtime explicitly, offline"
            )
        backup_dir = self.path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup = backup_dir / f"{self.path.stem}-legacy-{stamp}.db"
        guard = sqlite3.connect(self.path, timeout=0.2)
        try:
            # Reserve the writer before taking a consistent backup through a read connection.
            # Do not unlink a live database or WAL: schema replacement is one SQL transaction.
            guard.execute("BEGIN IMMEDIATE")
            if not self._is_legacy():
                raise MigrationError("MIGRATION_CHANGED: schema changed; retry inspection")
            project_root = self.path.parent.parent.parent
            if (
                self.path.parent.name == "state"
                and self.path.parent.parent.name == ".codex-os"
                and (project_root / ".git").exists()
            ):
                listing = GitRunner(project_root).run("worktree", "list", "--porcelain", timeout=5)
                if (
                    listing.returncode != 0
                    or sum(line.startswith("worktree ") for line in listing.stdout.splitlines())
                    != 1
                ):
                    raise MigrationError(
                        "MIGRATION_ACTIVE_WORKTREES: Git worktree list is not isolated"
                    )
            if guard.execute("SELECT count(*) FROM worktrees WHERE status != 'cleaned'").fetchone()[
                0
            ]:
                raise MigrationError("MIGRATION_ACTIVE_WORKTREES: finish/clean worktrees first")
            source = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
            destination = sqlite3.connect(backup)
            try:
                source.backup(destination)
                self._integrity_check_connection(destination)
            finally:
                destination.close()
                source.close()
            digest = hashlib.sha256(backup.read_bytes()).hexdigest()
            backup.with_suffix(".db.sha256").write_text(
                f"{digest}  {backup.name}\n", encoding="utf-8"
            )
            for table in ("worktrees", "approvals", "memory_index", "tasks"):
                guard.execute(f'DROP TABLE "{table}"')
            guard.execute("DELETE FROM schema_migrations")
            for migration in self._discover_migrations():
                for statement in _split_sql(migration.sql):
                    guard.execute(statement)
                guard.execute(
                    "INSERT INTO schema_migrations VALUES (?, ?, ?, ?)",
                    (migration.version, migration.name, migration.checksum, _utc_now()),
                )
            self._integrity_check_connection(guard)
            guard.commit()
        except (sqlite3.Error, OSError) as exc:
            guard.rollback()
            raise MigrationError(f"MIGRATION_FAILED: original database preserved: {exc}") from exc
        finally:
            guard.close()
        return backup

    def _is_legacy(self) -> bool:
        """Recognize only the audited lightweight predecessor, never guess from an error."""
        connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=0.2)
        try:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            if not tables:
                return False
            if tables != _EXPECTED_TABLES:
                raise MigrationError(
                    "MIGRATION_UNKNOWN_SCHEMA: unexpected tables; database preserved"
                )
            columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(schema_migrations)")
            }
            if columns != _EXPECTED_MIGRATION_COLUMNS:
                raise MigrationError("MIGRATION_UNKNOWN_SCHEMA: migration metadata differs")
            rows = dict(connection.execute("SELECT version, checksum FROM schema_migrations"))
            current = {item.version: item.checksum for item in self._discover_migrations()}
            self._integrity_check_connection(connection)
            if rows == current:
                reference = sqlite3.connect(":memory:")
                try:
                    self._bootstrap_migration_table(reference)
                    for migration in self._discover_migrations():
                        reference.executescript(migration.sql)
                    if _schema_fingerprint(reference) == _schema_fingerprint(connection):
                        return False
                finally:
                    reference.close()
                raise MigrationError("MIGRATION_UNKNOWN_SCHEMA: structure changed; preserved")
            # Git 12b723b: the known predecessor lacks target_branch and uses 'merged', not 'ready'.
            # Public migration checksum, not a credential.
            # pragma: allowlist nextline secret
            legacy = {"0001": "cf572399e7509516fc51e74bbe5ccfbae2a9c59026b613d15264bd5806c09482"}
            worktree_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(worktrees)")
            }
            if (
                rows == legacy
                and worktree_columns
                == {
                    "id",
                    "task_id",
                    "name",
                    "path",
                    "branch",
                    "disposable",
                    "status",
                    "created_at",
                    "updated_at",
                }
                and _schema_fingerprint(connection)
                # Public schema fingerprint, not a credential.
                # pragma: allowlist nextline secret
                == ("207e5cc52a4b3f5fde0fe32521ad802eebf5915c076cca4dd314c487bbb75845")
            ):
                return True
            raise MigrationError(
                "MIGRATION_UNKNOWN_SCHEMA: unrecognized checksum/structure; preserved"
            )
        except sqlite3.Error as exc:
            raise MigrationError(f"MIGRATION_INVALID_DATABASE: preserved: {exc}") from exc
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
    def _validate_applied_checksums(applied: dict[str, str], migrations: list[Migration]) -> None:
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


def _schema_fingerprint(connection: sqlite3.Connection) -> str:
    rows = [
        (kind, name, " ".join(sql.split()))
        for kind, name, sql in connection.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name"
        )
    ]
    return hashlib.sha256(json.dumps(rows, ensure_ascii=True).encode()).hexdigest()


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
