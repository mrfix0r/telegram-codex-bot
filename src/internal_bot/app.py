from __future__ import annotations

import logging
import time
from typing import Any

from .codex import CodexError, CodexManager, CodexStatus
from .config import AppConfig
from .storage import Storage
from .telegram import TelegramClient, TelegramConnectionError, TelegramError


LOG = logging.getLogger(__name__)

HELP_TEXT = """Управление Codex через Telegram.

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

    def authorized(self, user_id: int, chat_id: int) -> bool:
        return user_id in self.config.owner_ids and (
            not self.config.allowed_chat_ids or chat_id in self.config.allowed_chat_ids
        )

    def handle_update(self, update: dict[str, Any]) -> None:
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
            response = self.dispatch(text, chat_id=chat_id)
        except Exception:
            LOG.exception("Ошибка обработки команды")
            response = "Команда завершилась внутренней ошибкой. Подробности записаны в лог."
        self.client.send_message(chat_id, response)

    def dispatch(self, text: str, chat_id: int | None = None) -> str:
        command, args = parse_command(text)
        if not command:
            return "Команды начинаются с /. Отправьте /help для списка."
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
            return f"Задача Codex подключена.\nThread ID: {status.thread_id}"
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
    return BotApplication(
        config=config,
        client=client,
        codex=codex,
    )
