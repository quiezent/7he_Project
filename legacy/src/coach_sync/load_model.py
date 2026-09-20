from __future__ import annotations

import hashlib
from datetime import date, timedelta
from pathlib import Path

from .evidence import counts_for_training_load, load_activities
from .io import write_json
from .paths import snapshots_dir
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local


def _redacted_id(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def build_activity_summary_index(
    root: str | Path | None = None,
    for_date: str | date | None = None,
) -> list[dict]:
    target = parse_date(for_date) or today_local(DEFAULT_TIMEZONE)
    rows = []
    for activity in load_activities(root):
        act_date = parse_date(activity.get("date"))
        if not act_date or act_date > target:
            continue
        rows.append(
            {
                "activity_ref": _redacted_id(activity.get("id") or f"{activity.get('date')}-{len(rows)}"),
                "date": activity.get("date"),
                "type": activity.get("type"),
                "category": activity.get("category"),
                "counts_for_training_load": activity.get("counts_for_training_load"),
                "duration_min": activity.get("duration_min"),
                "distance_km": activity.get("distance_km"),
                "training_load": activity.get("training_load"),
                "avg_hr": activity.get("avg_hr"),
                "max_hr": activity.get("max_hr"),
                "has_power": activity.get("avg_power") is not None,
                "avg_power": activity.get("avg_power"),
                "normalized_power": activity.get("normalized_power"),
                "intensity_factor": activity.get("intensity_factor"),
                "aerobic_te": activity.get("aerobic_te"),
                "anaerobic_te": activity.get("anaerobic_te"),
                "hr_zone_min": activity.get("hr_zone_min"),
            }
        )
    write_json(snapshots_dir(root) / "activity_summary_index.json", rows)
    return rows


def _window(rows: list[dict], target: date, days: int) -> list[dict]:
    start = target - timedelta(days=days - 1)
    return [
        row
        for row in rows
        if parse_date(row.get("date")) and start <= parse_date(row.get("date")) <= target
    ]


def _category_totals(rows: list[dict]) -> dict:
    totals: dict[str, dict] = {}
    for row in rows:
        category = row.get("category") or "other"
        bucket = totals.setdefault(
            category,
            {
                "sessions": 0,
                "duration_min": 0.0,
                "training_load": 0.0,
                "distance_km": 0.0,
                "excluded_sessions": 0,
            },
        )
        if row.get("counts_for_training_load"):
            bucket["sessions"] += 1
            bucket["duration_min"] += row.get("duration_min") or 0
            bucket["training_load"] += row.get("training_load") or 0
            bucket["distance_km"] += row.get("distance_km") or 0
        else:
            bucket["excluded_sessions"] += 1
    for bucket in totals.values():
        bucket["duration_min"] = round(bucket["duration_min"], 1)
        bucket["training_load"] = round(bucket["training_load"], 1)
        bucket["distance_km"] = round(bucket["distance_km"], 1)
    return totals


def build_modality_load_rollups(
    root: str | Path | None = None,
    for_date: str | date | None = None,
) -> dict:
    target = parse_date(for_date) or today_local(DEFAULT_TIMEZONE)
    rows = build_activity_summary_index(root, target)
    artifact = {
        "date": target.isoformat(),
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "windows": {
            f"last_{days}_days": _category_totals(_window(rows, target, days))
            for days in (7, 14, 28, 42)
        },
    }
    write_json(snapshots_dir(root) / "modality_load_rollups.json", artifact)
    return artifact

