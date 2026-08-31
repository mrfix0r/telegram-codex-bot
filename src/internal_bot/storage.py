from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


@dataclass(frozen=True, slots=True)
class Note:
    id: int
    text: str
    created_at: str


@dataclass(frozen=True, slots=True)
class Todo:
    id: int
    text: str
    done: bool
    created_at: str
    completed_at: str | None


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
                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS todos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT NOT NULL,
                    done INTEGER NOT NULL DEFAULT 0 CHECK (done IN (0, 1)),
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _clean_text(text: str) -> str:
        value = text.strip()
        if not value:
            raise ValueError("Текст не может быть пустым")
        if len(value) > 4000:
            raise ValueError("Текст слишком длинный (максимум 4000 символов)")
        return value

    def add_note(self, text: str) -> int:
        value = self._clean_text(text)
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO notes(text, created_at) VALUES (?, ?)",
                (value, self._now()),
            )
            return int(cursor.lastrowid)

    def list_notes(self, limit: int = 10) -> list[Note]:
        limit = max(1, min(limit, 50))
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, text, created_at FROM notes ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [Note(int(row["id"]), row["text"], row["created_at"]) for row in rows]

    def add_todo(self, text: str) -> int:
        value = self._clean_text(text)
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO todos(text, created_at) VALUES (?, ?)",
                (value, self._now()),
            )
            return int(cursor.lastrowid)

    def list_todos(self, include_done: bool = False, limit: int = 50) -> list[Todo]:
        condition = "" if include_done else "WHERE done = 0"
        limit = max(1, min(limit, 100))
        with self._connect() as db:
            rows = db.execute(
                f"""SELECT id, text, done, created_at, completed_at
                    FROM todos {condition}
                    ORDER BY done ASC, id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            Todo(
                id=int(row["id"]),
                text=row["text"],
                done=bool(row["done"]),
                created_at=row["created_at"],
                completed_at=row["completed_at"],
            )
            for row in rows
        ]

    def complete_todo(self, todo_id: int) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                """UPDATE todos SET done = 1, completed_at = ?
                   WHERE id = ? AND done = 0""",
                (self._now(), todo_id),
            )
            return cursor.rowcount > 0

    def stats(self) -> dict[str, int]:
        with self._connect() as db:
            note_count = int(db.execute("SELECT COUNT(*) FROM notes").fetchone()[0])
            open_count = int(
                db.execute("SELECT COUNT(*) FROM todos WHERE done = 0").fetchone()[0]
            )
            done_count = int(
                db.execute("SELECT COUNT(*) FROM todos WHERE done = 1").fetchone()[0]
            )
        return {"notes": note_count, "open_todos": open_count, "done_todos": done_count}
