import unittest

from internal_bot.ui import (
    approval_keyboard,
    desktop_refresh_keyboard,
    main_keyboard,
    notification_keyboard,
    thread_keyboard,
)


def callbacks(markup: dict[str, object]) -> list[str]:
    rows = markup["inline_keyboard"]
    return [
        button["callback_data"]
        for row in rows  # type: ignore[union-attr]
        for button in row
    ]


class UiTests(unittest.TestCase):
    def test_status_buttons_have_expected_labels(self) -> None:
        labels = {
            button["callback_data"]: button["text"]
            for row in main_keyboard()["inline_keyboard"]
            for button in row
        }

        self.assertEqual(labels["show:status"], "📊 Статус по задаче")
        self.assertEqual(labels["show:overall_status"], "🩺 Общий статус")

    def test_all_callback_data_fits_telegram_limit(self) -> None:
        markups = [
            main_keyboard(),
            approval_keyboard(),
            desktop_refresh_keyboard(),
            thread_keyboard(["Очень длинное название задачи " * 10 for _ in range(20)]),
        ]

        for markup in markups:
            for value in callbacks(markup):
                self.assertLessEqual(len(value.encode("utf-8")), 64)

    def test_approval_notification_uses_approval_buttons(self) -> None:
        markup = notification_keyboard("Ответьте /codex_approve или /codex_decline")

        self.assertIn("approve:once", callbacks(markup))
        self.assertIn("approve:decline", callbacks(markup))

    def test_main_menu_has_desktop_refresh_button(self) -> None:
        self.assertIn("desktop:refresh", callbacks(main_keyboard()))
        self.assertIn(
            "desktop:refresh_confirm",
            callbacks(desktop_refresh_keyboard()),
        )


if __name__ == "__main__":
    unittest.main()
