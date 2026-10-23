from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .models import Event, IncidentState, utc_now


class SQLiteStore:
    """Small durable store for the MVP.

    The persistence boundary is intentionally isolated so SQLite can later be
    replaced by PostgreSQL without changing the orchestration layer.
    """

    def __init__(self, path: str | Path = "data/agent.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS incidents (
                    id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    incident_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def save_incident(self, state: IncidentState) -> IncidentState:
        state.updated_at = utc_now()
        encoded = state.model_dump_json()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO incidents(id, state_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    state_json=excluded.state_json,
                    updated_at=excluded.updated_at
                """,
                (state.id, encoded, state.updated_at.isoformat()),
            )
        return state

    def get_incident(self, incident_id: str) -> IncidentState | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state_json FROM incidents WHERE id = ?", (incident_id,)
            ).fetchone()
        if not row:
            return None
        return IncidentState.model_validate_json(row["state_json"])

    def append_event(self, event: Event) -> Event:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO events(incident_id, type, message, data_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event.incident_id,
                    event.type,
                    event.message,
                    json.dumps(event.data, ensure_ascii=False, default=str),
                    event.created_at.isoformat(),
                ),
            )
            event.id = int(cursor.lastrowid)
        return event

    def list_events(self, incident_id: str) -> list[Event]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, incident_id, type, message, data_json, created_at
                FROM events WHERE incident_id = ? ORDER BY id ASC
                """,
                (incident_id,),
            ).fetchall()
        return [
            Event(
                id=row["id"],
                incident_id=row["incident_id"],
                type=row["type"],
                message=row["message"],
                data=json.loads(row["data_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def clear(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM events")
            connection.execute("DELETE FROM incidents")

