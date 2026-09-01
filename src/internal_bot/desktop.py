from __future__ import annotations

import os
from pathlib import Path
import subprocess


class DesktopRefreshError(RuntimeError):
    pass


def refresh_codex_desktop(script_path: Path | None = None) -> str:
    if os.name != "nt":
        raise DesktopRefreshError("перезапуск Codex Desktop поддерживается только в Windows")

    script = script_path or (
        Path(__file__).resolve().parents[2] / "scripts" / "refresh_codex_desktop.ps1"
    )
    if not script.is_file():
        raise DesktopRefreshError(f"не найден сценарий перезапуска: {script}")

    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    powershell = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not powershell.is_file():
        raise DesktopRefreshError("Windows PowerShell не найден")

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            [
                str(powershell),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
            ],
            cwd=script.parent.parent,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=creationflags,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DesktopRefreshError(f"не удалось запустить сценарий: {exc}") from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise DesktopRefreshError(detail or f"сценарий завершился с кодом {completed.returncode}")
    result = completed.stdout.strip()
    messages = {
        "RESTARTED": (
            "Codex Desktop перезапущен. Список задач обновится после открытия приложения."
        ),
        "STARTED": "Codex Desktop был закрыт и теперь запущен.",
    }
    return messages.get(result, result or "Codex Desktop перезапущен.")
