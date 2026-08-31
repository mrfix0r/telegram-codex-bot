import json
from pathlib import Path
import sys
import tempfile
import unittest

from internal_bot.actions import ActionConfigError, ActionRegistry


class ActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.actions_file = self.root / "actions.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_actions(self, value: object) -> None:
        self.actions_file.write_text(json.dumps(value), encoding="utf-8")

    def test_runs_allowlisted_command_without_shell(self) -> None:
        self.write_actions(
            {
                "echo": {
                    "description": "test",
                    "command": [sys.executable, "-c", "print('ok')"],
                }
            }
        )
        result = ActionRegistry(self.actions_file, self.root).run("echo")
        self.assertTrue(result.ok)
        self.assertEqual(result.output, "ok")

    def test_arguments_are_rejected_by_default(self) -> None:
        self.write_actions({"echo": {"command": [sys.executable, "-c", "print('ok')"]}})
        registry = ActionRegistry(self.actions_file, self.root)
        with self.assertRaises(ValueError):
            registry.run("echo", "unexpected")

    def test_cwd_cannot_escape_workspace(self) -> None:
        self.write_actions({"bad": {"command": ["noop"], "cwd": ".."}})
        with self.assertRaises(ActionConfigError):
            ActionRegistry(self.actions_file, self.root)


if __name__ == "__main__":
    unittest.main()
