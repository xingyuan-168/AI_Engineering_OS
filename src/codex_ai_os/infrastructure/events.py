"""Append-only runtime event storage."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from codex_ai_os.domain.ids import new_id
from codex_ai_os.infrastructure.database import Database


@dataclass(frozen=True, slots=True)
class EventRecord:
    event_id: str
    project_id: str
    run_id: str | None
    task_id: str | None
    event_type: str
    idempotency_key: str | None
    payload: dict[str, Any]
    approval_required: bool
    created_at: str


class EventStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def append(
        self,
        *,
        project_id: str,
        event_type: str,
        payload: dict[str, Any],
        run_id: str | None = None,
        task_id: str | None = None,
        idempotency_key: str | None = None,
        approval_required: bool = False,
    ) -> EventRecord:
        if idempotency_key is not None:
            existing = self.by_idempotency_key(idempotency_key)
            if existing is not None:
                return existing

        record = EventRecord(
            event_id=new_id("EVENT"),
            project_id=project_id,
            run_id=run_id,
            task_id=task_id,
            event_type=event_type,
            idempotency_key=idempotency_key,
            payload=payload,
            approval_required=approval_required,
            created_at=datetime.now(UTC).isoformat(),
        )
        payload_json = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        with self.database.connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO events(
                        event_id, project_id, run_id, task_id, event_type,
                        idempotency_key, payload_json, approval_required, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.event_id,
                        record.project_id,
                        record.run_id,
                        record.task_id,
                        record.event_type,
                        record.idempotency_key,
                        payload_json,
                        int(record.approval_required),
                        record.created_at,
                    ),
                )
                connection.commit()
            except sqlite3.IntegrityError:
                connection.rollback()
                if idempotency_key is not None:
                    existing = self.by_idempotency_key(idempotency_key)
                    if existing is not None:
                        return existing
                raise
        return record

    def by_idempotency_key(self, key: str) -> EventRecord | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM events WHERE idempotency_key = ?", (key,)
            ).fetchone()
            return _event_from_row(row) if row is not None else None

    def list_for_project(self, project_id: str) -> list[EventRecord]:
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE project_id = ? ORDER BY sequence", (project_id,)
            ).fetchall()
            return [_event_from_row(row) for row in rows]


def _event_from_row(row: sqlite3.Row) -> EventRecord:
    raw_payload: object = json.loads(str(row["payload_json"]))
    if not isinstance(raw_payload, dict):
        raise ValueError("event payload must be a JSON object")
    object_payload = cast(dict[object, object], raw_payload)
    if not all(isinstance(key, str) for key in object_payload):
        raise ValueError("event payload keys must be strings")
    payload = cast(dict[str, Any], object_payload)
    return EventRecord(
        event_id=str(row["event_id"]),
        project_id=str(row["project_id"]),
        run_id=str(row["run_id"]) if row["run_id"] is not None else None,
        task_id=str(row["task_id"]) if row["task_id"] is not None else None,
        event_type=str(row["event_type"]),
        idempotency_key=(
            str(row["idempotency_key"]) if row["idempotency_key"] is not None else None
        ),
        payload=payload,
        approval_required=bool(row["approval_required"]),
        created_at=str(row["created_at"]),
    )
