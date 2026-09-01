from __future__ import annotations

from http.client import BadStatusLine, IncompleteRead, RemoteDisconnected
import json
import logging
import socket
import ssl
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


LOG = logging.getLogger(__name__)


class TelegramError(RuntimeError):
    pass


class TelegramConnectionError(TelegramError):
    """Временная ошибка транспорта, после которой запрос можно повторить."""


class TelegramClient:
    def __init__(self, token: str) -> None:
        self.base_url = f"https://api.telegram.org/bot{token}/"

    def call(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
        timeout: int = 40,
        retries: int = 0,
    ) -> Any:
        for attempt in range(retries + 1):
            try:
                return self._call_once(method, payload, timeout)
            except TelegramConnectionError:
                if attempt >= retries:
                    raise
                delay = min(2**attempt, 4)
                LOG.debug(
                    "Временный сбой Telegram при %s; внутренняя попытка %s/%s через %s сек.",
                    method,
                    attempt + 2,
                    retries + 1,
                    delay,
                )
                time.sleep(delay)
        raise AssertionError("Недостижимая ветка повторов Telegram")

    def _call_once(
        self,
        method: str,
        payload: dict[str, Any] | None,
        timeout: int,
    ) -> Any:
        body = json.dumps(payload or {}).encode("utf-8")
        request = Request(
            self.base_url + method,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise TelegramError(f"Telegram HTTP {exc.code}: {detail}") from exc
        except (
            URLError,
            socket.timeout,
            TimeoutError,
            ConnectionError,
            ssl.SSLError,
            RemoteDisconnected,
            IncompleteRead,
            BadStatusLine,
            json.JSONDecodeError,
        ) as exc:
            raise TelegramConnectionError(
                f"Ошибка соединения с Telegram: {exc}"
            ) from exc
        if not data.get("ok"):
            raise TelegramError(data.get("description", "Неизвестная ошибка Telegram API"))
        return data.get("result")

    def get_updates(self, offset: int | None, poll_timeout: int) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": poll_timeout,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = self.call(
            "getUpdates",
            payload,
            timeout=poll_timeout + 10,
            retries=2,
        )
        return result if isinstance(result, list) else []

    def send_message(self, chat_id: int, text: str) -> None:
        for chunk in split_message(text):
            self.call(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": chunk,
                    "disable_web_page_preview": True,
                },
            )

    def prepare(self) -> None:
        self.call(
            "deleteWebhook",
            {"drop_pending_updates": False},
            retries=2,
        )
        self.call(
            "setMyCommands",
            {
                "commands": [
                    {"command": "help", "description": "Список команд"},
                    {"command": "codex", "description": "Запустить задачу Codex"},
                    {"command": "codex_new", "description": "Новая задача Codex"},
                    {"command": "codex_reply", "description": "Продолжить задачу Codex"},
                    {"command": "codex_status", "description": "Статус задачи Codex"},
                    {"command": "codex_threads", "description": "Последние задачи Codex"},
                    {"command": "codex_use", "description": "Подключить задачу Codex"},
                    {"command": "codex_stop", "description": "Остановить задачу Codex"},
                    {"command": "codex_approve", "description": "Разрешить действие Codex"},
                    {"command": "codex_decline", "description": "Отклонить действие Codex"},
                    {"command": "codex_answer", "description": "Ответить Codex"},
                ]
            },
            retries=2,
        )


def split_message(text: str, limit: int = 3900) -> list[str]:
    value = text or "(пустой ответ)"
    chunks: list[str] = []
    while len(value) > limit:
        split_at = value.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = limit
        chunks.append(value[:split_at])
        value = value[split_at:].lstrip("\n")
    chunks.append(value)
    return chunks
