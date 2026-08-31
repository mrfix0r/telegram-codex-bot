from __future__ import annotations

import json
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    def __init__(self, token: str) -> None:
        self.base_url = f"https://api.telegram.org/bot{token}/"

    def call(self, method: str, payload: dict[str, Any] | None = None, timeout: int = 40) -> Any:
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
        except (URLError, socket.timeout, json.JSONDecodeError) as exc:
            raise TelegramError(f"Ошибка соединения с Telegram: {exc}") from exc
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
        result = self.call("getUpdates", payload, timeout=poll_timeout + 10)
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
        self.call("deleteWebhook", {"drop_pending_updates": False})
        self.call(
            "setMyCommands",
            {
                "commands": [
                    {"command": "help", "description": "Список команд"},
                    {"command": "todo", "description": "Добавить задачу"},
                    {"command": "todos", "description": "Открытые задачи"},
                    {"command": "note", "description": "Сохранить заметку"},
                    {"command": "actions", "description": "Доступные действия"},
                    {"command": "status", "description": "Статус инструмента"},
                ]
            },
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
