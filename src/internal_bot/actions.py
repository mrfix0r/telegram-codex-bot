from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shlex
import subprocess
from typing import Any


class ActionConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Action:
    name: str
    description: str
    command: tuple[str, ...]
    cwd: Path
    timeout_seconds: int
    accepts_arguments: bool


@dataclass(frozen=True, slots=True)
class ActionResult:
    name: str
    exit_code: int | None
    output: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.exit_code == 0


class ActionRegistry:
    def __init__(self, actions_file: Path, workspace_root: Path) -> None:
        self.actions_file = actions_file
        self.workspace_root = workspace_root.resolve()
        self._actions: dict[str, Action] = {}
        self.reload()

    def reload(self) -> None:
        if not self.actions_file.is_file():
            self._actions = {}
            return
        try:
            raw: Any = json.loads(self.actions_file.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ActionConfigError(f"Не удалось прочитать {self.actions_file}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ActionConfigError("Корень actions.json должен быть JSON-объектом")

        parsed: dict[str, Action] = {}
        for name, item in raw.items():
            if not isinstance(name, str) or not name or not name.replace("-", "").replace("_", "").isalnum():
                raise ActionConfigError(f"Недопустимое имя действия: {name!r}")
            if not isinstance(item, dict):
                raise ActionConfigError(f"Действие {name!r} должно быть объектом")
            command = item.get("command")
            if (
                not isinstance(command, list)
                or not command
                or not all(isinstance(part, str) and part for part in command)
            ):
                raise ActionConfigError(f"У действия {name!r} command должен быть массивом строк")

            cwd_value = item.get("cwd", ".")
            if not isinstance(cwd_value, str):
                raise ActionConfigError(f"У действия {name!r} cwd должен быть строкой")
            cwd = (self.workspace_root / cwd_value).resolve()
            try:
                cwd.relative_to(self.workspace_root)
            except ValueError as exc:
                raise ActionConfigError(
                    f"Действие {name!r} пытается выйти за пределы BOT_WORKSPACE_ROOT"
                ) from exc
            if not cwd.is_dir():
                raise ActionConfigError(f"Рабочая папка действия {name!r} не существует: {cwd}")

            timeout = item.get("timeout_seconds", 30)
            if not isinstance(timeout, int) or not 1 <= timeout <= 300:
                raise ActionConfigError(
                    f"У действия {name!r} timeout_seconds должен быть от 1 до 300"
                )
            description = item.get("description", name)
            if not isinstance(description, str):
                raise ActionConfigError(f"У действия {name!r} description должен быть строкой")
            accepts_arguments = item.get("accepts_arguments", False)
            if not isinstance(accepts_arguments, bool):
                raise ActionConfigError(
                    f"У действия {name!r} accepts_arguments должен быть true/false"
                )

            parsed[name.lower()] = Action(
                name=name.lower(),
                description=description.strip() or name,
                command=tuple(command),
                cwd=cwd,
                timeout_seconds=timeout,
                accepts_arguments=accepts_arguments,
            )
        self._actions = parsed

    def list(self) -> list[Action]:
        return sorted(self._actions.values(), key=lambda item: item.name)

    def run(self, name: str, arguments: str = "") -> ActionResult:
        action = self._actions.get(name.lower())
        if action is None:
            raise KeyError(name)
        extra: list[str] = []
        if arguments.strip():
            if not action.accepts_arguments:
                raise ValueError(f"Действие {action.name} не принимает аргументы")
            try:
                extra = shlex.split(arguments)
            except ValueError as exc:
                raise ValueError(f"Не удалось разобрать аргументы: {exc}") from exc
            if len(extra) > 20 or any(len(part) > 500 for part in extra):
                raise ValueError("Слишком много или слишком длинные аргументы")

        try:
            completed = subprocess.run(
                [*action.command, *extra],
                cwd=action.cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=action.timeout_seconds,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            partial = "\n".join(
                part for part in ((exc.stdout or ""), (exc.stderr or "")) if part
            )
            return ActionResult(action.name, None, partial.strip(), timed_out=True)
        except OSError as exc:
            return ActionResult(action.name, None, f"Не удалось запустить: {exc}")

        output = "\n".join(
            part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
        )
        if len(output) > 12000:
            output = output[:12000] + "\n… вывод обрезан"
        return ActionResult(action.name, completed.returncode, output)
