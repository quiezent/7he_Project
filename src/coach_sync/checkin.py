from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .io import write_json
from .paths import daily_checkin_path, input_dir
from .time_utils import today_local


BOOL_TRUE = {"yes", "y", "true", "1", "present"}
BOOL_FALSE = {"no", "n", "false", "0", "none", "absent"}


def normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.strip().lower()).strip("_")


def parse_value(value: str) -> Any:
    value = value.strip()
    lowered = value.lower()
    if lowered in BOOL_TRUE:
        return True
    if lowered in BOOL_FALSE:
        return False
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def parse_checkin_text(text: str) -> dict:
    data: dict[str, Any] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip().strip("-* ")
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        if not value.strip():
            continue
        data[normalize_key(key)] = parse_value(value)
    return data


def load_daily_checkin(root: str | Path | None = None, default_date: str | None = None) -> dict:
    path = daily_checkin_path(root)
    if not path.exists():
        return {}
    data = parse_checkin_text(path.read_text(encoding="utf-8"))
    if data and "date" not in data:
        data["date"] = default_date or today_local().isoformat()
    return data


def write_checkin_template(root: str | Path | None = None) -> Path:
    path = daily_checkin_path(root)
    if path.exists():
        return path
    template = """# Daily Check-in

date:
next_morning_response:
ride_purpose:
trail_condition:
skill_quality:
technical_quality_notes:
late_session_skill_fade:
stop_rule_outcome:
actual_rpe:
workout_feel:
fueling:
heat_feel:
notes:
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(template, encoding="utf-8")
    return path


def import_checkin(path: str | Path, root: str | Path | None = None) -> dict:
    source = Path(path)
    entry = parse_checkin_text(source.read_text(encoding="utf-8"))
    if "date" not in entry:
        entry["date"] = today_local().isoformat()
    log_dir = input_dir(root)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"feedback_{entry['date']}.json"
    write_json(log_path, entry)
    return entry
