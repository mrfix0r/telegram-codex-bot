from __future__ import annotations

from typing import Any


ReplyMarkup = dict[str, Any]


def button(
    text: str,
    callback_data: str,
    *,
    style: str | None = None,
) -> dict[str, str]:
    result = {"text": text, "callback_data": callback_data}
    if style is not None:
        result["style"] = style
    return result


def main_keyboard() -> ReplyMarkup:
    return {
        "inline_keyboard": [
            [
                button("▶️ Запустить / продолжить", "prompt:codex", style="primary"),
                button("🆕 Новая задача", "prompt:codex_new", style="primary"),
            ],
            [
                button("💬 Направить задачу", "prompt:codex_reply"),
                button("📊 Статус", "show:status"),
            ],
            [
                button("🗂 Выбрать задачу", "show:threads"),
                button("⏹ Остановить", "action:stop"),
            ],
            [button("ℹ️ Помощь", "show:help")],
        ]
    }


def cancel_keyboard() -> ReplyMarkup:
    return {
        "inline_keyboard": [
            [button("Отмена", "action:cancel"), button("Главное меню", "show:menu")]
        ],
        "force_reply": True,
    }


def approval_keyboard() -> ReplyMarkup:
    return {
        "inline_keyboard": [
            [
                button("✅ Разрешить один раз", "approve:once", style="success"),
                button("🛡 До конца сессии", "approve:session", style="success"),
            ],
            [button("❌ Отклонить", "approve:decline", style="danger")],
            [button("Главное меню", "show:menu")],
        ]
    }


def answer_keyboard() -> ReplyMarkup:
    return {
        "inline_keyboard": [
            [button("✍️ Ответить Codex", "prompt:codex_answer")],
            [button("Главное меню", "show:menu")],
        ]
    }


def thread_keyboard(labels: list[str]) -> ReplyMarkup:
    rows = [
        [button(f"{index + 1}. {label[:48]}", f"thread:{index}")]
        for index, label in enumerate(labels)
    ]
    rows.append([button("Главное меню", "show:menu")])
    return {"inline_keyboard": rows}


def notification_keyboard(text: str) -> ReplyMarkup:
    if "/codex_approve" in text:
        return approval_keyboard()
    if "/codex_answer" in text:
        return answer_keyboard()
    return main_keyboard()
