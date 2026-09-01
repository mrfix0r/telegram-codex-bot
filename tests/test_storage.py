from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from internal_bot.storage import Storage


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.temp.name) / "test.db")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_new_database_only_contains_codex_sessions(self) -> None:
        with closing(sqlite3.connect(self.storage.db_path)) as connection:
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertEqual(names, {"codex_sessions"})

    def test_codex_session_is_upserted(self) -> None:
        self.storage.save_codex_session(
            chat_id=10,
            thread_id="thr_1",
            status="inProgress",
            last_turn_id="turn_1",
            last_response="",
            last_error="",
        )
        self.storage.save_codex_session(
            chat_id=10,
            thread_id="thr_1",
            status="completed",
            last_turn_id=None,
            last_response="Готово",
            last_error="",
        )

        records = self.storage.list_codex_sessions()

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "completed")
        self.assertEqual(records[0].last_response, "Готово")


if __name__ == "__main__":
    unittest.main()
