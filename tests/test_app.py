from pathlib import Path
import tempfile
import unittest

from internal_bot.app import BotApplication, parse_command
from internal_bot.config import AppConfig


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
            poll_timeout=1,
            log_level="INFO",
            codex_enabled=True,
            codex_executable="codex",
            codex_workspace=root,
            codex_model=None,
            codex_approval_policy="unlessTrusted",
            codex_request_timeout=5,
        )
        self.client = FakeClient()
        self.app = BotApplication(
            self.config,
            self.client,  # type: ignore[arg-type]
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_parse_group_command(self) -> None:
        self.assertEqual(
            parse_command("/codex@my_bot Проверить проект"),
            ("codex", "Проверить проект"),
        )

    def test_help_only_lists_codex_commands(self) -> None:
        response = self.app.dispatch("/help")
        self.assertIn("/codex", response)
        self.assertNotIn("/todo", response)

    def test_removed_command_is_unknown(self) -> None:
        self.assertIn("Неизвестная команда", self.app.dispatch("/todo Старое"))

    def test_codex_command_requires_telegram_chat(self) -> None:
        self.assertIn("только из Telegram", self.app.dispatch("/codex Проверить проект"))

    def test_rejects_unknown_user(self) -> None:
        self.app.handle_update(
            {"message": {"text": "/codex_status", "chat": {"id": 10}, "from": {"id": 99}}}
        )
        self.assertEqual(self.client.messages, [(10, "Доступ запрещён.")])


if __name__ == "__main__":
    unittest.main()
