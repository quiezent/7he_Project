from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from .evidence import as_number, counts_for_training_load, load_activities
from .io import read_json, write_json
from .paths import snapshots_dir
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local


def _redacted_id(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _local_datetime(value: Any) -> datetime | None:
    """Parse an explicit Garmin local timestamp without applying a timezone."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if len(text) <= 10:
        return None
    try:
        return datetime.fromisoformat(text.replace(" ", "T", 1))
    except ValueError:
        return None


def _local_field(payload: dict, *names: str) -> Any:
    for name in names:
        value = payload.get(name)
        if value not in (None, ""):
            return value
    # Garmin summary exports are normally flat. These bounded wrappers cover the
    # known alternate shapes without accidentally selecting a lap timestamp.
    for wrapper_name in ("summaryDTO", "activitySummary", "activitySummaryDTO"):
        wrapper = payload.get(wrapper_name)
        if not isinstance(wrapper, dict):
            continue
        for name in names:
            value = wrapper.get(name)
            if value not in (None, ""):
                return value
    return None


def _start_time_bucket(start: datetime | None) -> str | None:
    if start is None:
        return None
    hour = start.hour
    if hour < 5:
        return "overnight"
    if hour < 8:
        return "early_morning"
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    if hour < 21:
        return "evening"
    return "late_evening"


@lru_cache(maxsize=4096)
def _raw_local_timing(
    source_file: str,
    modified_ns: int,
    size_bytes: int,
) -> tuple[str | None, str | None, str | None]:
    # modified_ns/size_bytes keep this process-local cache coherent after a sync.
    _ = (modified_ns, size_bytes)
    payload = read_json(Path(source_file), {})
    if not isinstance(payload, dict):
        return None, None, None

    start = _local_datetime(
        _local_field(payload, "startTimeLocal", "start_time_local")
    )
    end = _local_datetime(_local_field(payload, "endTimeLocal", "end_time_local"))
    if end is None and start is not None:
        elapsed_seconds = as_number(
            _local_field(
                payload,
                "elapsedDuration",
                "elapsed_duration",
                "elapsedDurationSeconds",
                "duration",
                "durationSeconds",
            )
        )
        if elapsed_seconds is not None and elapsed_seconds >= 0:
            end = start + timedelta(seconds=elapsed_seconds)

    start_text = start.isoformat(timespec="seconds") if start is not None else None
    end_text = end.isoformat(timespec="seconds") if end is not None else None
    return start_text, end_text, _start_time_bucket(start)


def _activity_local_timing(activity: dict) -> tuple[str | None, str | None, str | None]:
    source_file = activity.get("source_file")
    if not source_file:
        return None, None, None
    try:
        stat = Path(source_file).stat()
    except OSError:
        return None, None, None
    return _raw_local_timing(str(source_file), stat.st_mtime_ns, stat.st_size)


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
        start_time_local, end_time_local, start_time_bucket = _activity_local_timing(activity)
        rows.append(
            {
                "activity_ref": _redacted_id(activity.get("id") or f"{activity.get('date')}-{len(rows)}"),
                "date": activity.get("date"),
                "start_time_local": start_time_local,
                "end_time_local": end_time_local,
                "start_time_bucket": start_time_bucket,
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
