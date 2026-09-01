import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from internal_bot.config import AppConfig


class ConfigTests(unittest.TestCase):
    def test_loads_token_from_separate_secret_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".secrets").mkdir()
            (root / ".env").write_text(
                "TELEGRAM_OWNER_IDS=7\nBOT_SECRET_FILE=./.secrets/telegram.env\n",
                encoding="utf-8",
            )
            (root / ".secrets" / "telegram.env").write_text(
                "TELEGRAM_BOT_TOKEN=test:secret-token\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                config = AppConfig.from_env(root)

            self.assertEqual(config.token, "test:secret-token")
            self.assertEqual(config.owner_ids, frozenset({7}))
            self.assertTrue(config.codex_enabled)
            self.assertEqual(config.codex_approval_policy, "unlessTrusted")

    def test_process_environment_has_priority_over_secret_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".env").write_text("TELEGRAM_OWNER_IDS=7\n", encoding="utf-8")

            with patch.dict(
                os.environ,
                {"TELEGRAM_BOT_TOKEN": "process:token"},
                clear=True,
            ):
                config = AppConfig.from_env(root)

            self.assertEqual(config.token, "process:token")

    def test_rejects_invalid_codex_boolean(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".env").write_text("TELEGRAM_OWNER_IDS=7\n", encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "TELEGRAM_BOT_TOKEN": "process:token",
                    "BOT_CODEX_ENABLED": "sometimes",
                },
                clear=True,
            ):
                with self.assertRaisesRegex(ValueError, "BOT_CODEX_ENABLED"):
                    AppConfig.from_env(root)


if __name__ == "__main__":
    unittest.main()
