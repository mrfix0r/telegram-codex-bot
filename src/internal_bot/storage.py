from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


@dataclass(frozen=True, slots=True)
class CodexSessionRecord:
    chat_id: int
    thread_id: str
    status: str
    last_turn_id: str | None
    last_response: str
    last_error: str
    updated_at: str


class Storage:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS codex_sessions (
                    chat_id INTEGER PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_turn_id TEXT,
                    last_response TEXT NOT NULL DEFAULT '',
                    last_error TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def save_codex_session(
        self,
        *,
        chat_id: int,
        thread_id: str,
        status: str,
        last_turn_id: str | None,
        last_response: str,
        last_error: str,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO codex_sessions(
                       chat_id, thread_id, status, last_turn_id,
                       last_response, last_error, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(chat_id) DO UPDATE SET
                       thread_id = excluded.thread_id,
                       status = excluded.status,
                       last_turn_id = excluded.last_turn_id,
                       last_response = excluded.last_response,
                       last_error = excluded.last_error,
                       updated_at = excluded.updated_at""",
                (
                    chat_id,
                    thread_id,
                    status,
                    last_turn_id,
                    last_response,
                    last_error,
                    self._now(),
                ),
            )

    def list_codex_sessions(self) -> list[CodexSessionRecord]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT chat_id, thread_id, status, last_turn_id,
                          last_response, last_error, updated_at
                   FROM codex_sessions ORDER BY updated_at DESC"""
            ).fetchall()
        return [
            CodexSessionRecord(
                chat_id=int(row["chat_id"]),
                thread_id=row["thread_id"],
                status=row["status"],
                last_turn_id=row["last_turn_id"],
                last_response=row["last_response"],
                last_error=row["last_error"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]
