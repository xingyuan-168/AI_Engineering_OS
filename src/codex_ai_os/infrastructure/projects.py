"""Project metadata persistence."""

from __future__ import annotations

from datetime import UTC, datetime

from codex_ai_os.domain.config import ProjectConfig
from codex_ai_os.infrastructure.database import Database


class ProjectStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def register(self, config: ProjectConfig, config_hash: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self.database.connection() as connection:
            connection.execute(
                """
                INSERT INTO projects(id, name, root, config_hash, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    root = excluded.root,
                    config_hash = excluded.config_hash,
                    updated_at = excluded.updated_at
                """,
                (
                    config.project_id,
                    config.name,
                    config.root.as_posix(),
                    config_hash,
                    now,
                    now,
                ),
            )
            connection.commit()
