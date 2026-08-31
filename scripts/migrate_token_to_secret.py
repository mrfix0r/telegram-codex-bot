"""Однократно переносит TELEGRAM_BOT_TOKEN из .env в .secrets/telegram.env."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile


TOKEN_KEY = "TELEGRAM_BOT_TOKEN"


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def token_line(lines: list[str]) -> str | None:
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(f"{TOKEN_KEY}="):
            return stripped.rstrip("\r\n")
    return None


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    source = root / ".env"
    destination = root / ".secrets" / "telegram.env"
    if not source.is_file():
        raise SystemExit("Файл .env не найден")

    source_lines = source.read_text(encoding="utf-8-sig").splitlines(keepends=True)
    value_line = token_line(source_lines)
    if value_line is None:
        if destination.is_file() and token_line(
            destination.read_text(encoding="utf-8-sig").splitlines(keepends=True)
        ):
            print("Токен уже находится в .secrets/telegram.env")
            return
        raise SystemExit("TELEGRAM_BOT_TOKEN не найден в .env")

    if destination.exists():
        existing_line = token_line(
            destination.read_text(encoding="utf-8-sig").splitlines(keepends=True)
        )
        if existing_line != value_line:
            raise SystemExit(
                "Секрет .secrets/telegram.env уже существует с другим значением; "
                "перенос остановлен"
            )
    else:
        atomic_write(
            destination,
            "# Локальный секрет Telegram-бота. Не добавлять в Git.\n" + value_line + "\n",
        )

    remaining = [
        line
        for line in source_lines
        if not line.lstrip().startswith(f"{TOKEN_KEY}=")
    ]
    atomic_write(source, "".join(remaining))
    print("Токен перенесён в .secrets/telegram.env и удалён из .env")


if __name__ == "__main__":
    main()
