from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from coach_sync.surface_manifest import build_garmin_surface_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inventory persisted Garmin collection, normalization, and decision-use surfaces."
    )
    parser.add_argument("--root", default=None, help="Repository root. Defaults to this project.")
    parser.add_argument("--date", default=None, help="Optional YYYY-MM-DD evidence cutoff.")
    args = parser.parse_args(argv)
    print(json.dumps(build_garmin_surface_manifest(args.root, args.date), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
