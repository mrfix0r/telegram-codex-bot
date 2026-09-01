import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from internal_bot.desktop import DesktopRefreshError, refresh_codex_desktop


@unittest.skipUnless(os.name == "nt", "Codex Desktop доступен только в Windows")
class DesktopRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.script = Path(self.temp.name) / "refresh.ps1"
        self.script.touch()

    def tearDown(self) -> None:
        self.temp.cleanup()

    @patch("internal_bot.desktop.subprocess.run")
    def test_maps_restart_result_to_user_message(self, mocked_run) -> None:
        mocked_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="RESTARTED\n", stderr=""
        )

        result = refresh_codex_desktop(self.script)

        self.assertIn("перезапущен", result)
        command = mocked_run.call_args.args[0]
        self.assertIn("-NonInteractive", command)
        self.assertEqual(command[-1], str(self.script))

    @patch("internal_bot.desktop.subprocess.run")
    def test_reports_script_failure(self, mocked_run) -> None:
        mocked_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="restart failed"
        )

        with self.assertRaisesRegex(DesktopRefreshError, "restart failed"):
            refresh_codex_desktop(self.script)


if __name__ == "__main__":
    unittest.main()
