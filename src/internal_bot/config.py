from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


class ConfigError(ValueError):
    pass


def load_env_file(path: Path) -> None:
    """Загружает простой .env без сторонней зависимости и не затирает env процесса."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def _parse_ids(value: str, name: str, *, required: bool = False) -> frozenset[int]:
    result: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            result.add(int(item))
        except ValueError as exc:
            raise ConfigError(f"{name} должен содержать числовые ID через запятую") from exc
    if required and not result:
        raise ConfigError(f"Переменная {name} обязательна")
    return frozenset(result)


def _positive_int(value: str, name: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} должен быть целым числом") from exc
    if result <= 0:
        raise ConfigError(f"{name} должен быть больше нуля")
    return result


def _parse_bool(value: str, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} должен быть true или false")


@dataclass(frozen=True, slots=True)
class AppConfig:
    token: str
    owner_ids: frozenset[int]
    allowed_chat_ids: frozenset[int]
    data_dir: Path
    poll_timeout: int
    log_level: str
    codex_enabled: bool
    codex_executable: str
    codex_workspace: Path
    codex_model: str | None
    codex_approval_policy: str
    codex_request_timeout: int

    @classmethod
    def from_env(cls, base_dir: Path | None = None) -> "AppConfig":
        base = (base_dir or Path.cwd()).resolve()
        load_env_file(base / ".env")

        secret_file = Path(os.environ.get("BOT_SECRET_FILE", "./.secrets/telegram.env"))
        if not secret_file.is_absolute():
            secret_file = (base / secret_file).resolve()
        load_env_file(secret_file)

        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not token or token.endswith("replace_me"):
            raise ConfigError(
                f"Укажите TELEGRAM_BOT_TOKEN в секрете {secret_file} "
                "или в переменной окружения"
            )

        def resolve_path(env_name: str, default: str) -> Path:
            value = Path(os.environ.get(env_name, default)).expanduser()
            return (base / value).resolve() if not value.is_absolute() else value.resolve()

        codex_workspace = resolve_path("BOT_CODEX_WORKSPACE", ".")
        if not codex_workspace.is_dir():
            raise ConfigError(f"BOT_CODEX_WORKSPACE не существует: {codex_workspace}")
        codex_executable = os.environ.get("BOT_CODEX_EXECUTABLE", "codex").strip()
        if not codex_executable:
            raise ConfigError("BOT_CODEX_EXECUTABLE не может быть пустым")
        approval_policy = os.environ.get(
            "BOT_CODEX_APPROVAL_POLICY", "unlessTrusted"
        ).strip()
        allowed_approval_policies = {
            "unlessTrusted",
            "on-request",
            "on-failure",
            "never",
            "untrusted",
        }
        if approval_policy not in allowed_approval_policies:
            raise ConfigError(
                "BOT_CODEX_APPROVAL_POLICY имеет недопустимое значение: "
                f"{approval_policy}"
            )

        return cls(
            token=token,
            owner_ids=_parse_ids(
                os.environ.get("TELEGRAM_OWNER_IDS", ""),
                "TELEGRAM_OWNER_IDS",
                required=True,
            ),
            allowed_chat_ids=_parse_ids(
                os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", ""),
                "TELEGRAM_ALLOWED_CHAT_IDS",
            ),
            data_dir=resolve_path("BOT_DATA_DIR", "./data"),
            poll_timeout=_positive_int(
                os.environ.get("BOT_POLL_TIMEOUT", "30"), "BOT_POLL_TIMEOUT"
            ),
            log_level=os.environ.get("BOT_LOG_LEVEL", "INFO").upper(),
            codex_enabled=_parse_bool(
                os.environ.get("BOT_CODEX_ENABLED", "true"), "BOT_CODEX_ENABLED"
            ),
            codex_executable=codex_executable,
            codex_workspace=codex_workspace,
            codex_model=os.environ.get("BOT_CODEX_MODEL", "").strip() or None,
            codex_approval_policy=approval_policy,
            codex_request_timeout=_positive_int(
                os.environ.get("BOT_CODEX_REQUEST_TIMEOUT", "30"),
                "BOT_CODEX_REQUEST_TIMEOUT",
            ),
        )
