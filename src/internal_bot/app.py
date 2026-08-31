from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
import logging
from pathlib import Path
import platform
import time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .actions import ActionRegistry
from .config import AppConfig
from .storage import Storage
from .telegram import TelegramClient, TelegramError


LOG = logging.getLogger(__name__)

HELP_TEXT = """Внутренний инструмент готов к работе.

/todo текст — добавить задачу
/todos — показать открытые задачи
/todos all — показать все задачи
/done ID — завершить задачу
/note текст — сохранить заметку
/notes [число] — последние заметки
/actions — разрешённые действия
/run имя [аргументы] — запустить действие
/reload — перечитать actions.json
/status — состояние инструмента
/ping — проверка связи
/help — эта справка"""


def resolve_timezone(name: str) -> tzinfo:
    """Возвращает часовой пояс даже на Windows без установленного пакета tzdata."""
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        normalized = name.strip().lower()
        if normalized in {"europe/moscow", "msk"}:
            LOG.warning(
                "База часовых поясов не найдена; для %s используется фиксированный UTC+03:00",
                name,
            )
            return timezone(timedelta(hours=3), name="MSK")
        LOG.warning("Часовой пояс %s не найден, используется UTC", name)
        return timezone.utc


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
        storage: Storage,
        actions: ActionRegistry,
    ) -> None:
        self.config = config
        self.client = client
        self.storage = storage
        self.actions = actions
        self.started_at = time.monotonic()
        self.timezone = resolve_timezone(config.timezone)

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
            response = self.dispatch(text)
        except Exception:
            LOG.exception("Ошибка обработки команды")
            response = "Команда завершилась внутренней ошибкой. Подробности записаны в лог."
        self.client.send_message(chat_id, response)

    def dispatch(self, text: str) -> str:
        command, args = parse_command(text)
        if not command:
            return "Команды начинаются с /. Отправьте /help для списка."
        if command in {"start", "help"}:
            return HELP_TEXT
        if command == "ping":
            return "pong"
        if command == "todo":
            if not args:
                return "Использование: /todo текст задачи"
            todo_id = self.storage.add_todo(args)
            return f"Задача #{todo_id} добавлена."
        if command == "todos":
            items = self.storage.list_todos(include_done=args.lower() == "all")
            if not items:
                return "Задач пока нет."
            lines = ["Задачи:"]
            for item in items:
                mark = "✓" if item.done else "○"
                lines.append(f"{mark} #{item.id} {item.text}")
            return "\n".join(lines)
        if command == "done":
            try:
                todo_id = int(args)
            except ValueError:
                return "Использование: /done ID"
            return (
                f"Задача #{todo_id} завершена."
                if self.storage.complete_todo(todo_id)
                else f"Открытая задача #{todo_id} не найдена."
            )
        if command == "note":
            if not args:
                return "Использование: /note текст заметки"
            note_id = self.storage.add_note(args)
            return f"Заметка #{note_id} сохранена."
        if command == "notes":
            try:
                limit = int(args) if args else 10
            except ValueError:
                return "Использование: /notes [число от 1 до 50]"
            items = self.storage.list_notes(limit)
            if not items:
                return "Заметок пока нет."
            return "Последние заметки:\n" + "\n".join(
                f"#{item.id} {item.text}" for item in items
            )
        if command == "actions":
            actions = self.actions.list()
            if not actions:
                return "Действия не настроены. Скопируйте actions.json.example в actions.json."
            return "Доступные действия:\n" + "\n".join(
                f"/run {item.name} — {item.description}" for item in actions
            )
        if command == "reload":
            self.actions.reload()
            return f"Конфигурация перечитана. Действий: {len(self.actions.list())}."
        if command == "run":
            name, _, action_args = args.partition(" ")
            if not name:
                return "Использование: /run имя [аргументы]"
            try:
                result = self.actions.run(name, action_args)
            except KeyError:
                return f"Действие {name!r} не найдено. Отправьте /actions."
            except ValueError as exc:
                return str(exc)
            output = result.output or "(действие не вернуло текст)"
            if result.timed_out:
                return f"Действие {result.name} остановлено по таймауту.\n\n{output}"
            state = "успешно" if result.ok else f"с кодом {result.exit_code}"
            return f"Действие {result.name} завершено {state}.\n\n{output}"
        if command == "status":
            stats = self.storage.stats()
            uptime = int(time.monotonic() - self.started_at)
            now = datetime.now(self.timezone).strftime("%Y-%m-%d %H:%M:%S %Z")
            return (
                "Инструмент работает.\n"
                f"Время: {now}\n"
                f"Uptime: {uptime // 3600}ч {(uptime % 3600) // 60}м\n"
                f"Заметок: {stats['notes']}\n"
                f"Открытых задач: {stats['open_todos']}\n"
                f"Завершённых задач: {stats['done_todos']}\n"
                f"Действий: {len(self.actions.list())}\n"
                f"Python: {platform.python_version()}"
            )
        return "Неизвестная команда. Отправьте /help."

    def run_forever(self) -> None:
        self.client.prepare()
        LOG.info("Бот запущен; владельцы: %s", sorted(self.config.owner_ids))
        offset: int | None = None
        retry_delay = 1
        while True:
            try:
                updates = self.client.get_updates(offset, self.config.poll_timeout)
                retry_delay = 1
                for update in updates:
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        offset = update_id + 1
                    self.handle_update(update)
            except TelegramError as exc:
                LOG.error("%s; повтор через %s сек.", exc, retry_delay)
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30)


def build_application(config: AppConfig) -> BotApplication:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    return BotApplication(
        config=config,
        client=TelegramClient(config.token),
        storage=Storage(config.data_dir / "bot.db"),
        actions=ActionRegistry(config.actions_file, config.workspace_root),
    )
