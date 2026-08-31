from __future__ import annotations

import logging
from pathlib import Path
import sys

from .app import build_application
from .config import AppConfig, ConfigError


def main() -> None:
    try:
        config = AppConfig.from_env(Path.cwd())
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        build_application(config).run_forever()
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Бот остановлен")
