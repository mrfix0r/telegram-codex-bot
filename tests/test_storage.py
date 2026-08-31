from pathlib import Path
import tempfile
import unittest

from internal_bot.storage import Storage


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.temp.name) / "test.db")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_note_lifecycle(self) -> None:
        note_id = self.storage.add_note("Важная мысль")
        self.assertGreater(note_id, 0)
        self.assertEqual(self.storage.list_notes()[0].text, "Важная мысль")

    def test_todo_lifecycle_and_stats(self) -> None:
        todo_id = self.storage.add_todo("Проверить отчёт")
        self.assertEqual([item.id for item in self.storage.list_todos()], [todo_id])
        self.assertTrue(self.storage.complete_todo(todo_id))
        self.assertFalse(self.storage.complete_todo(todo_id))
        self.assertEqual(self.storage.list_todos(), [])
        self.assertEqual(self.storage.stats()["done_todos"], 1)

    def test_empty_text_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.storage.add_note("   ")


if __name__ == "__main__":
    unittest.main()
