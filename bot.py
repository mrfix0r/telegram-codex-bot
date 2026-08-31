"""Удобная точка входа: python bot.py."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent / "src"))

from internal_bot.main import main


if __name__ == "__main__":
    main()
