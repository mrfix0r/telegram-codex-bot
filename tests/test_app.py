from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

from internal_bot.actions import ActionRegistry
from internal_bot.app import BotApplication, parse_command, resolve_timezone
from internal_bot.config import AppConfig
from internal_bot.storage import Storage


class FakeClient:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    def send_message(self, chat_id: int, text: str) -> None:
        self.messages.append((chat_id, text))


class AppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.config = AppConfig(
            token="test",
            owner_ids=frozenset({7}),
            allowed_chat_ids=frozenset(),
            data_dir=root,
            actions_file=root / "actions.json",
            workspace_root=root,
            poll_timeout=1,
            log_level="INFO",
            timezone="UTC",
        )
        self.client = FakeClient()
        self.app = BotApplication(
            self.config,
            self.client,  # type: ignore[arg-type]
            Storage(root / "bot.db"),
            ActionRegistry(root / "actions.json", root),
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_parse_group_command(self) -> None:
        self.assertEqual(parse_command("/todo@my_bot Купить молоко"), ("todo", "Купить молоко"))

    def test_moscow_timezone_works_without_tzdata(self) -> None:
        with patch("internal_bot.app.ZoneInfo", side_effect=ZoneInfoNotFoundError):
            value = resolve_timezone("Europe/Moscow")
        self.assertEqual(value.utcoffset(None), timedelta(hours=3))

    def test_unknown_timezone_falls_back_to_utc_without_tzdata(self) -> None:
        with patch("internal_bot.app.ZoneInfo", side_effect=ZoneInfoNotFoundError):
            value = resolve_timezone("Unknown/Timezone")
        self.assertEqual(value.utcoffset(None), timedelta(0))

    def test_command_flow(self) -> None:
        response = self.app.dispatch("/todo Подготовить план")
        self.assertIn("#1", response)
        self.assertIn("Подготовить план", self.app.dispatch("/todos"))
        self.assertIn("завершена", self.app.dispatch("/done 1"))

    def test_rejects_unknown_user(self) -> None:
        self.app.handle_update(
            {"message": {"text": "/status", "chat": {"id": 10}, "from": {"id": 99}}}
        )
        self.assertEqual(self.client.messages, [(10, "Доступ запрещён.")])


if __name__ == "__main__":
    unittest.main()
