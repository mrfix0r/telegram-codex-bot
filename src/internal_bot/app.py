from __future__ import annotations

import logging
import time
from typing import Any

from .codex import CodexError, CodexManager, CodexStatus
from .config import AppConfig
from .storage import Storage
from .telegram import TelegramClient, TelegramConnectionError, TelegramError
from .ui import (
    ReplyMarkup,
    cancel_keyboard,
    main_keyboard,
    notification_keyboard,
    thread_keyboard,
)


LOG = logging.getLogger(__name__)

HELP_TEXT = """Управление Codex через Telegram.

Выберите действие кнопкой под сообщением. Текст задачи бот запросит следующим сообщением.

/codex запрос — создать или продолжить задачу Codex
/codex_new запрос — создать новую задачу Codex
/codex_reply текст — направить выполняемую задачу
/codex_status — статус и последний результат Codex
/codex_threads — последние задачи Codex
/codex_use ID — подключить существующую задачу
/codex_stop — остановить текущую задачу Codex
/codex_approve — подтвердить действие Codex один раз
/codex_decline — отклонить действие Codex
/codex_answer текст — ответить на вопрос Codex
/help — эта справка"""

PROMPT_COMMANDS = {
    "prompt:codex": (
        "codex",
        "Напишите, что нужно сделать. Codex продолжит выбранную задачу или создаст новую.",
    ),
    "prompt:codex_new": ("codex_new", "Напишите текст новой задачи Codex."),
    "prompt:codex_reply": (
        "codex_reply",
        "Напишите уточнение или следующую инструкцию для выбранной задачи.",
    ),
    "prompt:codex_answer": ("codex_answer", "Напишите ответ на вопрос Codex."),
}


def parse_command(text: str) -> tuple[str, str]:
    value = text.strip()
    if not value.startswith("/"):
        return "", value
    first, _, rest = value.partition(" ")
    command = first[1:].split("@", 1)[0].lower()
    return command, rest.strip()


class BotApplication:
    def __init__(
        self,
        config: AppConfig,
        client: TelegramClient,
        codex: CodexManager | None = None,
    ) -> None:
        self.config = config
        self.client = client
        self.codex = codex
        self._pending_inputs: dict[int, str] = {}
        self._thread_choices: dict[int, list[str]] = {}

    def authorized(self, user_id: int, chat_id: int) -> bool:
        return user_id in self.config.owner_ids and (
            not self.config.allowed_chat_ids or chat_id in self.config.allowed_chat_ids
        )

    def handle_update(self, update: dict[str, Any]) -> None:
        callback_query = update.get("callback_query")
        if isinstance(callback_query, dict):
            self._handle_callback_query(callback_query)
            return

        message = update.get("message")
        if not isinstance(message, dict):
            return
        text = message.get("text")
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        if not isinstance(text, str) or not isinstance(chat.get("id"), int):
            return
        chat_id = int(chat["id"])
        user_id = sender.get("id")
        if not isinstance(user_id, int) or not self.authorized(user_id, chat_id):
            LOG.warning("Отклонён запрос user_id=%r chat_id=%r", user_id, chat_id)
            self.client.send_message(chat_id, "Доступ запрещён.")
            return

        try:
            command, _ = parse_command(text)
            if command:
                self._pending_inputs.pop(chat_id, None)
                response = self.dispatch(text, chat_id=chat_id)
            else:
                pending_command = self._pending_inputs.pop(chat_id, None)
                if pending_command:
                    response = self.dispatch(
                        f"/{pending_command} {text}",
                        chat_id=chat_id,
                    )
                else:
                    response = "Выберите действие кнопкой под сообщением."
        except Exception:
            LOG.exception("Ошибка обработки команды")
            response = "Команда завершилась внутренней ошибкой. Подробности записаны в лог."
        self.client.send_message(chat_id, response, reply_markup=main_keyboard())

    def _handle_callback_query(self, query: dict[str, Any]) -> None:
        callback_id = query.get("id")
        data = query.get("data")
        sender = query.get("from") or {}
        message = query.get("message") or {}
        chat = message.get("chat") or {}
        if not isinstance(callback_id, str):
            return
        if not isinstance(data, str) or not isinstance(chat.get("id"), int):
            self.client.answer_callback_query(
                callback_id,
                "Кнопка больше недоступна.",
                show_alert=True,
            )
            return

        chat_id = int(chat["id"])
        user_id = sender.get("id")
        if not isinstance(user_id, int) or not self.authorized(user_id, chat_id):
            LOG.warning("Отклонена кнопка user_id=%r chat_id=%r", user_id, chat_id)
            self.client.answer_callback_query(
                callback_id,
                "Доступ запрещён.",
                show_alert=True,
            )
            return

        is_thread_selection = data.startswith("thread:")
        if not is_thread_selection:
            self.client.answer_callback_query(callback_id)
        try:
            response, reply_markup = self.dispatch_callback(data, chat_id)
        except CodexError as exc:
            response, reply_markup = f"Codex: {exc}", main_keyboard()
        except Exception:
            LOG.exception("Ошибка обработки кнопки")
            response = "Действие завершилось внутренней ошибкой. Подробности записаны в лог."
            reply_markup = main_keyboard()
        if is_thread_selection:
            failed = response.startswith("Codex:") or response.startswith("Действие завершилось")
            self.client.answer_callback_query(
                callback_id,
                response[:200] if failed else "Задача выбрана.",
                show_alert=failed,
            )
        self.client.send_message(chat_id, response, reply_markup=reply_markup)

    def dispatch_callback(self, data: str, chat_id: int) -> tuple[str, ReplyMarkup]:
        prompt = PROMPT_COMMANDS.get(data)
        if prompt is not None:
            command, text = prompt
            self._pending_inputs[chat_id] = command
            return text, cancel_keyboard()

        self._pending_inputs.pop(chat_id, None)
        if data == "show:menu":
            return "Что сделать с Codex?", main_keyboard()
        if data == "show:help":
            return HELP_TEXT, main_keyboard()
        if data == "show:status":
            return self.dispatch("/codex_status", chat_id), main_keyboard()
        if data == "show:overall_status":
            return self.dispatch("/codex_threads", chat_id), main_keyboard()
        if data == "show:threads":
            return self._show_thread_picker(chat_id)
        if data == "action:stop":
            return self.dispatch("/codex_stop", chat_id), main_keyboard()
        if data == "action:cancel":
            return "Ввод отменён.", main_keyboard()
        if data == "approve:once":
            return self.dispatch("/codex_approve", chat_id), main_keyboard()
        if data == "approve:session":
            return self.dispatch("/codex_approve session", chat_id), main_keyboard()
        if data == "approve:decline":
            return self.dispatch("/codex_decline", chat_id), main_keyboard()
        if data.startswith("thread:"):
            try:
                index = int(data.partition(":")[2])
                thread_id = self._thread_choices[chat_id][index]
            except (KeyError, IndexError, ValueError):
                return (
                    "Этот список задач устарел. Откройте список ещё раз.",
                    main_keyboard(),
                )
            response = self.dispatch(f"/codex_use {thread_id}", chat_id)
            return response, main_keyboard()
        return "Неизвестная кнопка. Откройте главное меню.", main_keyboard()

    def _show_thread_picker(self, chat_id: int) -> tuple[str, ReplyMarkup]:
        if self.codex is None:
            raise CodexError("интеграция не настроена")
        threads = self.codex.list_threads(10)
        if not threads:
            return "Задачи Codex не найдены.", main_keyboard()
        self._thread_choices[chat_id] = [item.thread_id for item in threads]
        labels: list[str] = []
        lines = ["Выберите задачу:"]
        for index, item in enumerate(threads, 1):
            label = (item.name or item.preview or item.thread_id).replace("\n", " ").strip()
            labels.append(label)
            lines.append(f"{index}. {label[:80]} — {item.status}")
        return "\n".join(lines), thread_keyboard(labels)

    def send_codex_notification(self, chat_id: int, text: str) -> None:
        self.client.send_message(
            chat_id,
            text,
            reply_markup=notification_keyboard(text),
        )

    def dispatch(self, text: str, chat_id: int | None = None) -> str:
        command, args = parse_command(text)
        if not command:
            return "Выберите действие кнопкой или отправьте /help."
        if command in {"start", "help"}:
            return HELP_TEXT
        if command in {
            "codex",
            "codex_new",
            "codex_reply",
            "codex_status",
            "codex_threads",
            "codex_use",
            "codex_stop",
            "codex_approve",
            "codex_decline",
            "codex_answer",
        }:
            if chat_id is None:
                return "Команда Codex доступна только из Telegram-чата."
            try:
                return self._dispatch_codex(command, args, chat_id)
            except CodexError as exc:
                return f"Codex: {exc}"
        return "Неизвестная команда. Отправьте /help."

    def _dispatch_codex(self, command: str, args: str, chat_id: int) -> str:
        if self.codex is None:
            raise CodexError("интеграция не настроена")
        if command in {"codex", "codex_new"}:
            if not args:
                return f"Использование: /{command} текст задачи"
            status = self.codex.start_task(
                chat_id, args, new_thread=command == "codex_new"
            )
            return (
                "Задача Codex запущена. Итог придёт отдельным сообщением.\n"
                f"Thread ID: {status.thread_id}"
            )
        if command == "codex_reply":
            if not args:
                return "Использование: /codex_reply текст"
            status = self.codex.continue_task(chat_id, args)
            return (
                "Сообщение передано Codex.\n"
                f"Thread ID: {status.thread_id}"
            )
        if command == "codex_status":
            return self._format_codex_status(self.codex.status(chat_id))
        if command == "codex_stop":
            self.codex.stop_task(chat_id)
            return "Запрос на остановку задачи Codex отправлен."
        if command == "codex_approve":
            mode = args.lower() or "once"
            if mode not in {"once", "session"}:
                return "Использование: /codex_approve [once|session]"
            self.codex.approve(chat_id, for_session=mode == "session")
            return (
                "Действие Codex разрешено для текущей сессии."
                if mode == "session"
                else "Действие Codex разрешено один раз."
            )
        if command == "codex_decline":
            self.codex.decline(chat_id)
            return "Действие Codex отклонено."
        if command == "codex_answer":
            if not args:
                return "Использование: /codex_answer текст ответа"
            self.codex.answer(chat_id, args)
            return "Ответ передан Codex."
        if command == "codex_threads":
            try:
                limit = int(args) if args else 10
            except ValueError:
                return "Использование: /codex_threads [число от 1 до 20]"
            threads = self.codex.list_threads(limit)
            if not threads:
                return "Задачи Codex не найдены."
            lines = ["Последние задачи Codex:"]
            for item in threads:
                preview = item.preview.replace("\n", " ").strip()
                if len(preview) > 120:
                    preview = preview[:117] + "…"
                lines.append(
                    f"\n{item.name}\n{item.thread_id}\n"
                    f"Статус: {item.status}"
                    + (f"\n{preview}" if preview else "")
                )
            lines.append("\nПодключение: /codex_use ID")
            return "\n".join(lines)
        if command == "codex_use":
            if not args:
                return "Использование: /codex_use ID"
            status = self.codex.use_thread(chat_id, args)
            return (
                "Задача Codex выбрана. Теперь можно направить ей новый запрос.\n"
                f"Thread ID: {status.thread_id}"
            )
        raise CodexError("неизвестная команда")

    @staticmethod
    def _format_codex_status(status: CodexStatus) -> str:
        if status.thread_id is None:
            return "Задача Codex ещё не выбрана. Используйте /codex или /codex_threads."
        names = {
            "idle": "ожидает нового запроса",
            "inProgress": "выполняется",
            "completed": "завершена",
            "interrupted": "остановлена",
            "failed": "ошибка",
        }
        lines = [
            f"Codex: {names.get(status.status, status.status)}",
            f"Thread ID: {status.thread_id}",
        ]
        if status.turn_id:
            lines.append(f"Turn ID: {status.turn_id}")
        if status.pending_requests:
            lines.append(f"Ожидает ответов/подтверждений: {status.pending_requests}")
        if status.last_error:
            lines.append(f"Ошибка: {status.last_error}")
        if status.last_response:
            preview = status.last_response[-1500:]
            lines.append("Последний ответ:\n" + preview)
        return "\n".join(lines)

    def run_forever(self) -> None:
        self.client.prepare()
        LOG.info("Бот запущен; владельцы: %s", sorted(self.config.owner_ids))
        offset: int | None = None
        retry_delay = 1
        try:
            while True:
                try:
                    updates = self.client.get_updates(offset, self.config.poll_timeout)
                    retry_delay = 1
                    for update in updates:
                        update_id = update.get("update_id")
                        if isinstance(update_id, int):
                            offset = update_id + 1
                        self.handle_update(update)
                except TelegramConnectionError as exc:
                    LOG.warning(
                        "%s; это временный сетевой сбой, повтор через %s сек.",
                        exc,
                        retry_delay,
                    )
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 30)
                except TelegramError as exc:
                    LOG.error("%s; повтор через %s сек.", exc, retry_delay)
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 30)
        finally:
            if self.codex is not None:
                self.codex.close()


def build_application(config: AppConfig) -> BotApplication:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(config.token)
    storage = Storage(config.data_dir / "bot.db")
    codex = CodexManager(
        storage=storage,
        notify_user=client.send_message,
        enabled=config.codex_enabled,
        executable=config.codex_executable,
        workspace=config.codex_workspace,
        model=config.codex_model,
        approval_policy=config.codex_approval_policy,
        request_timeout=config.codex_request_timeout,
    )
    application = BotApplication(
        config=config,
        client=client,
        codex=codex,
    )
    codex.notify_user = application.send_codex_notification
    return application
