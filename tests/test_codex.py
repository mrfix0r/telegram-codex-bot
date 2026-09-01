from __future__ import annotations

import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from typing import Any, Callable

from internal_bot.codex import (
    CodexBusyError,
    CodexError,
    CodexManager,
    resolve_codex_executable,
)
from internal_bot.storage import Storage


class CodexExecutableTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Проверка пути Codex Desktop нужна на Windows")
    def test_finds_codex_desktop_when_cli_is_missing_from_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable = root / "OpenAI" / "Codex" / "bin" / "version" / "codex.exe"
            executable.parent.mkdir(parents=True)
            executable.touch()

            with (
                patch("internal_bot.codex.shutil.which", return_value=None),
                patch.dict(os.environ, {"LOCALAPPDATA": str(root)}, clear=True),
            ):
                result = resolve_codex_executable("codex")

            self.assertEqual(result, str(executable.resolve()))


class FakeRpc:
    def __init__(self) -> None:
        self.handler: Callable[[dict[str, Any]], None] = lambda message: None
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.responses: list[tuple[Any, dict[str, Any]]] = []
        self.errors: list[tuple[Any, int, str]] = []
        self.started = False
        self.closed = False
        self.close_count = 0
        self.close_event = threading.Event()
        self.thread_number = 0
        self.turn_number = 0
        self.complete_during_turn_start = False
        self.resume_error: CodexError | None = None

    def set_handler(self, handler: Callable[[dict[str, Any]], None]) -> None:
        self.handler = handler

    def start(self) -> None:
        self.started = True
        self.closed = False
        self.close_event.clear()

    def request(
        self, method: str, params: dict[str, Any], timeout: int | None = None
    ) -> Any:
        self.started = True
        self.requests.append((method, params))
        if method == "thread/start":
            self.thread_number += 1
            thread_id = (
                "thr_12345678901234567890"
                if self.thread_number == 1
                else f"thr_1234567890123456789{self.thread_number}"
            )
            return {"thread": {"id": thread_id}}
        if method == "thread/resume":
            if self.resume_error is not None:
                raise self.resume_error
            return {"thread": {"id": params["threadId"]}}
        if method == "turn/start":
            self.turn_number += 1
            turn_id = f"turn_{self.turn_number}"
            if self.complete_during_turn_start:
                self.handler(
                    {
                        "method": "turn/started",
                        "params": {
                            "threadId": params["threadId"],
                            "turn": {"id": turn_id, "status": "inProgress", "items": []},
                        },
                    }
                )
                self.handler(
                    {
                        "method": "item/agentMessage/delta",
                        "params": {
                            "threadId": params["threadId"],
                            "turnId": turn_id,
                            "itemId": "item_fast",
                            "delta": "Быстрый ответ",
                        },
                    }
                )
                self.handler(
                    {
                        "method": "turn/completed",
                        "params": {
                            "threadId": params["threadId"],
                            "turn": {"id": turn_id, "status": "completed", "items": []},
                        },
                    }
                )
            return {
                "turn": {
                    "id": turn_id,
                    "status": "inProgress",
                }
            }
        if method == "turn/steer":
            return {"turnId": params["expectedTurnId"]}
        if method == "turn/interrupt":
            return {}
        if method == "thread/list":
            return {
                "data": [
                    {
                        "id": "thr_12345678901234567890",
                        "name": "Telegram bot",
                        "preview": "Добавить управление",
                        "status": {"type": "idle"},
                        "cwd": str(Path.cwd()),
                    }
                ]
            }
        raise AssertionError(f"Unexpected method: {method}")

    def respond(self, request_id: Any, result: dict[str, Any]) -> None:
        self.responses.append((request_id, result))

    def respond_error(self, request_id: Any, code: int, message: str) -> None:
        self.errors.append((request_id, code, message))

    def emit(self, message: dict[str, Any]) -> None:
        self.handler(message)

    def close(self) -> None:
        self.closed = True
        self.close_count += 1
        self.close_event.set()


class CodexManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.storage = Storage(self.root / "bot.db")
        self.rpc = FakeRpc()
        self.notifications: list[tuple[int, str]] = []
        self.manager = CodexManager(
            storage=self.storage,
            notify_user=lambda chat_id, text: self.notifications.append((chat_id, text)),
            enabled=True,
            executable="codex",
            workspace=self.root,
            model=None,
            approval_policy="on-request",
            request_timeout=5,
            rpc=self.rpc,
        )

    def tearDown(self) -> None:
        self.manager.close()
        self.temp.cleanup()

    def wait_for_release_worker(self) -> None:
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            with self.manager._release_lock:
                if not self.manager._release_scheduled:
                    return
            time.sleep(0.01)
        self.fail("Поток освобождения Codex App Server не завершился")

    def test_starts_turn_and_delivers_final_answer(self) -> None:
        status = self.manager.start_task(10, "Проверь проект")
        self.assertEqual(status.status, "inProgress")
        self.assertEqual(status.turn_id, "turn_1")

        self.rpc.emit(
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": status.thread_id,
                    "turnId": "turn_1",
                    "itemId": "item_1",
                    "delta": "Проверка завершена.",
                },
            }
        )
        self.rpc.emit(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": status.thread_id,
                    "turn": {"id": "turn_1", "status": "completed", "items": []},
                },
            }
        )

        completed = self.manager.status(10)
        self.assertEqual(completed.status, "completed")
        self.assertEqual(completed.last_response, "Проверка завершена.")
        self.assertIn("Codex завершил", self.notifications[-1][1])
        self.assertEqual(self.storage.list_codex_sessions()[0].status, "completed")

    def test_reply_steers_active_turn(self) -> None:
        self.manager.start_task(10, "Начни")
        self.manager.continue_task(10, "Сначала запусти тесты")
        method, params = self.rpc.requests[-1]
        self.assertEqual(method, "turn/steer")
        self.assertEqual(params["expectedTurnId"], "turn_1")

    def test_fast_completion_is_not_overwritten_by_start_response(self) -> None:
        self.rpc.complete_during_turn_start = True

        status = self.manager.start_task(10, "Ответь быстро")

        self.assertEqual(status.status, "completed")
        self.assertEqual(status.last_response, "Быстрый ответ")

    def test_releases_app_server_and_resumes_thread_after_completion(self) -> None:
        status = self.manager.start_task(10, "Проверь проект")
        self.rpc.emit(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": status.thread_id,
                    "turn": {"id": "turn_1", "status": "completed", "items": []},
                },
            }
        )

        self.assertTrue(self.rpc.close_event.wait(1))
        self.assertEqual(self.rpc.close_count, 1)

        self.manager.continue_task(10, "Продолжай")

        methods = [method for method, _ in self.rpc.requests]
        self.assertEqual(methods[-2:], ["thread/resume", "turn/start"])

    def test_keeps_app_server_while_another_turn_is_active(self) -> None:
        first = self.manager.start_task(10, "Первая задача")
        second = self.manager.start_task(11, "Вторая задача")

        self.rpc.emit(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": first.thread_id,
                    "turn": {"id": "turn_1", "status": "completed", "items": []},
                },
            }
        )
        self.wait_for_release_worker()
        self.assertEqual(self.rpc.close_count, 0)

        self.rpc.emit(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": second.thread_id,
                    "turn": {"id": "turn_2", "status": "completed", "items": []},
                },
            }
        )

        self.assertTrue(self.rpc.close_event.wait(1))
        self.assertEqual(self.rpc.close_count, 1)

    def test_stop_interrupts_active_turn(self) -> None:
        self.manager.start_task(10, "Начни")
        self.manager.stop_task(10)
        self.assertEqual(self.rpc.requests[-1][0], "turn/interrupt")

    def test_command_approval_requires_explicit_response(self) -> None:
        status = self.manager.start_task(10, "Запусти тесты")
        self.rpc.emit(
            {
                "id": 77,
                "method": "item/commandExecution/requestApproval",
                "params": {
                    "threadId": status.thread_id,
                    "turnId": "turn_1",
                    "itemId": "item_1",
                    "command": ["python", "-m", "unittest"],
                    "reason": "Нужно проверить проект",
                },
            }
        )
        self.assertEqual(self.manager.status(10).pending_requests, 1)
        self.assertEqual(self.rpc.responses, [])

        self.manager.approve(10)

        self.assertEqual(self.rpc.responses, [(77, {"decision": "accept"})])

    def test_answers_single_user_input_question(self) -> None:
        status = self.manager.start_task(10, "Спроси")
        self.rpc.emit(
            {
                "id": "question-1",
                "method": "item/tool/requestUserInput",
                "params": {
                    "threadId": status.thread_id,
                    "turnId": "turn_1",
                    "itemId": "item_2",
                    "isBlocking": True,
                    "questions": [
                        {"id": "format", "header": "Формат", "question": "JSON или CSV?"}
                    ],
                },
            }
        )

        self.manager.answer(10, "JSON")

        self.assertEqual(
            self.rpc.responses,
            [("question-1", {"answers": {"format": {"answers": ["JSON"]}}})],
        )

    def test_lists_and_uses_existing_thread(self) -> None:
        threads = self.manager.list_threads()
        self.assertEqual(threads[0].name, "Telegram bot")

        requests_before_selection = len(self.rpc.requests)
        status = self.manager.use_thread(10, "thr_123")

        self.assertEqual(status.thread_id, "thr_12345678901234567890")
        self.assertEqual(
            [method for method, _ in self.rpc.requests[requests_before_selection:]],
            ["thread/list"],
        )

        self.manager.continue_task(10, "Продолжай")

        resume_requests = [
            params for method, params in self.rpc.requests if method == "thread/resume"
        ]
        self.assertEqual(resume_requests[-1]["approvalPolicy"], "on-request")
        self.assertEqual(resume_requests[-1]["sandbox"], "workspace-write")

    def test_active_writer_has_friendly_error(self) -> None:
        self.rpc.resume_error = CodexError(
            "Codex App Server (thread/resume): thread already has an active writer"
        )
        self.manager.use_thread(10, "thr_12345678901234567890")

        with self.assertRaisesRegex(CodexBusyError, "другом экземпляре Codex"):
            self.manager.continue_task(10, "Продолжай")


if __name__ == "__main__":
    unittest.main()
