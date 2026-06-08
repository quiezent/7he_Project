from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from coach_sync.cli import main_for


if __name__ == "__main__":
    raise SystemExit(main_for("body-battery-model")(sys.argv[1:]))
