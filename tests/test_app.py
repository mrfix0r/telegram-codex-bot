from pathlib import Path
import tempfile
import unittest
from typing import Any

from internal_bot.app import BotApplication, parse_command
from internal_bot.codex import CodexStatus, CodexThreadSummary
from internal_bot.config import AppConfig


class FakeClient:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str, dict[str, Any] | None]] = []
        self.callback_answers: list[tuple[str, str | None, bool]] = []

    def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        self.messages.append((chat_id, text, reply_markup))

    def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
        *,
        show_alert: bool = False,
    ) -> None:
        self.callback_answers.append((callback_query_id, text, show_alert))


class FakeCodex:
    def __init__(self) -> None:
        self.started: list[tuple[int, str, bool]] = []
        self.used: list[tuple[int, str]] = []

    def start_task(
        self,
        chat_id: int,
        prompt: str,
        *,
        new_thread: bool = False,
    ) -> CodexStatus:
        self.started.append((chat_id, prompt, new_thread))
        return CodexStatus("thr_new", "turn_1", "inProgress", "", "", 0)

    def list_threads(self, limit: int) -> list[CodexThreadSummary]:
        return [
            CodexThreadSummary(
                "thr_old",
                "Проверка проекта",
                "Исправить ошибки",
                "idle",
                "C:/project",
            ),
            CodexThreadSummary(
                "thr_without_name",
                "",
                "Описание задачи без отдельного заголовка",
                "idle",
                "C:/project",
            ),
            CodexThreadSummary(
                "thr_google_drive",
                "Без названия",
                "[@Google Drive](plugin://google-drive@openai-curated-remote): "
                "проанализируй последние документы и подготовь краткий отчёт",
                "notLoaded",
                "C:/project",
            ),
        ][:limit]

    def use_thread(self, chat_id: int, thread_id: str) -> CodexStatus:
        self.used.append((chat_id, thread_id))
        return CodexStatus(thread_id, None, "idle", "", "", 0)


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
            codex_approval_policy="on-request",
            codex_request_timeout=5,
        )
        self.client = FakeClient()
        self.codex = FakeCodex()
        self.app = BotApplication(
            self.config,
            self.client,  # type: ignore[arg-type]
            self.codex,  # type: ignore[arg-type]
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
        self.assertEqual(self.client.messages, [(10, "Доступ запрещён.", None)])

    def test_start_message_contains_button_menu(self) -> None:
        self.app.handle_update(
            {"message": {"text": "/start", "chat": {"id": 10}, "from": {"id": 7}}}
        )

        markup = self.client.messages[-1][2]
        self.assertIsNotNone(markup)
        callbacks = {
            button["callback_data"]
            for row in markup["inline_keyboard"]  # type: ignore[index]
            for button in row
        }
        self.assertIn("prompt:codex_new", callbacks)
        self.assertIn("show:threads", callbacks)

    def test_button_prompts_for_new_task_text(self) -> None:
        self.app.handle_update(
            {
                "callback_query": {
                    "id": "cb_1",
                    "data": "prompt:codex_new",
                    "from": {"id": 7},
                    "message": {"chat": {"id": 10}},
                }
            }
        )
        prompt_markup = self.client.messages[-1][2]
        self.assertIsNotNone(prompt_markup)
        self.assertTrue(prompt_markup["force_reply"])  # type: ignore[index]
        self.app.handle_update(
            {"message": {"text": "Проверь проект", "chat": {"id": 10}, "from": {"id": 7}}}
        )

        self.assertEqual(self.client.callback_answers, [("cb_1", None, False)])
        self.assertEqual(self.codex.started, [(10, "Проверь проект", True)])
        self.assertIn("Задача Codex запущена", self.client.messages[-1][1])

    def test_thread_can_be_selected_with_buttons(self) -> None:
        self.app.handle_update(
            {
                "callback_query": {
                    "id": "cb_list",
                    "data": "show:threads",
                    "from": {"id": 7},
                    "message": {"chat": {"id": 10}},
                }
            }
        )
        self.app.handle_update(
            {
                "callback_query": {
                    "id": "cb_use",
                    "data": "thread:0",
                    "from": {"id": 7},
                    "message": {"chat": {"id": 10}},
                }
            }
        )

        self.assertEqual(self.codex.used, [(10, "thr_old")])
        self.assertEqual(self.client.callback_answers[-1], ("cb_use", "Задача выбрана.", False))
        self.assertIn("выбрана", self.client.messages[-1][1])

    def test_overall_status_button_runs_codex_threads(self) -> None:
        response, _ = self.app.dispatch_callback("show:overall_status", 10)

        self.assertIn("Последние задачи Codex", response)
        self.assertIn("thr_old", response)
        self.assertIn("Описание задачи без отдельного заголовка", response)
        self.assertIn("Google Drive — проанализируй последние…", response)
        self.assertIn("Статус: не подключена", response)
        self.assertNotIn("plugin://", response)
        self.assertNotIn("Без названия", response)

    def test_thread_picker_uses_preview_when_name_is_missing(self) -> None:
        response, markup = self.app.dispatch_callback("show:threads", 10)

        self.assertIn("Описание задачи без отдельного заголовка", response)
        self.assertEqual(
            markup["inline_keyboard"][1][0]["text"],
            "2. Описание задачи без отдельного заголовка",
        )
        self.assertIn(
            "3. Google Drive — проанализируй последние… — не подключена",
            response,
        )
        self.assertEqual(
            markup["inline_keyboard"][2][0]["text"],
            "3. Google Drive — проанализируй последние…",
        )

    def test_rejects_button_from_unknown_user(self) -> None:
        self.app.handle_update(
            {
                "callback_query": {
                    "id": "cb_denied",
                    "data": "show:status",
                    "from": {"id": 99},
                    "message": {"chat": {"id": 10}},
                }
            }
        )

        self.assertEqual(
            self.client.callback_answers,
            [("cb_denied", "Доступ запрещён.", True)],
        )


if __name__ == "__main__":
    unittest.main()
