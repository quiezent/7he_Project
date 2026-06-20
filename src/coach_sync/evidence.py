from __future__ import annotations

import re
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from .io import read_json
from .paths import activities_dir, snapshots_dir
from .time_utils import parse_date


def normalize_key(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def find_value(payload: Any, names: Iterable[str]) -> Any:
    wanted = {normalize_key(name) for name in names}
    if isinstance(payload, dict):
        for key, value in payload.items():
            if normalize_key(str(key)) in wanted:
                return value
        for value in payload.values():
            found = find_value(value, wanted)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = find_value(value, wanted)
            if found is not None:
                return found
    return None


def as_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        match = re.search(r"-?\d+(?:\.\d+)?", str(value))
        return float(match.group(0)) if match else None


def dated_snapshot_files(root: str | Path | None, prefix: str) -> list[tuple[date, Path]]:
    files: list[tuple[date, Path]] = []
    for path in snapshots_dir(root).glob(f"{prefix}_*.json"):
        raw_date = path.stem.removeprefix(prefix + "_")
        try:
            files.append((date.fromisoformat(raw_date[:10]), path))
        except ValueError:
            continue
    return sorted(files, key=lambda item: item[0])


def latest_snapshot(
    root: str | Path | None,
    prefix: str,
    on_or_before: str | date | None = None,
) -> tuple[date, dict] | tuple[None, None]:
    files = dated_snapshot_files(root, prefix)
    cutoff = parse_date(on_or_before)
    if cutoff is not None:
        files = [item for item in files if item[0] <= cutoff]
    if not files:
        return None, None
    snap_date, path = files[-1]
    return snap_date, read_json(path, {})


def load_latest_wellness(
    root: str | Path | None = None,
    on_or_before: str | date | None = None,
) -> tuple[date | None, dict | None]:
    return latest_snapshot(root, "garmin_wellness", on_or_before)


def load_latest_training_status(
    root: str | Path | None = None,
    on_or_before: str | date | None = None,
) -> tuple[date | None, dict | None]:
    return latest_snapshot(root, "garmin_training_status", on_or_before)


def activity_date(payload: dict) -> date | None:
    candidates = [
        find_value(payload, ("startTimeLocal", "start_time_local", "startTimeGMT", "start_time")),
        find_value(payload, ("date", "startDate", "activityDate")),
    ]
    for value in candidates:
        if not value:
            continue
        try:
            return parse_date(str(value))
        except ValueError:
            continue
    return None


def activity_type(payload: dict) -> str:
    direct = find_value(payload, ("activityType", "type", "sport", "activity_type"))
    if isinstance(direct, dict):
        for key in ("typeKey", "type_key", "parentTypeId", "name"):
            if key in direct:
                return str(direct[key])
    if direct is not None:
        return str(direct)
    return "unknown"


def classify_activity_type(activity_type_value: str, name: str = "") -> str:
    text = f"{activity_type_value} {name}".lower()
    if "auto_racing" in text or "motorsport" in text:
        return "motorsport"
    if "mountain" in text or "mtb" in text:
        return "mtb"
    if "indoor_cycling" in text or "virtual_ride" in text:
        return "bike_indoor"
    if "cycling" in text or "biking" in text:
        return "bike_outdoor"
    if "strength" in text or "gym" in text:
        return "gym"
    if "treadmill" in text:
        return "run_treadmill"
    if "running" in text:
        return "run"
    if "elliptical" in text:
        return "elliptical"
    if "hiking" in text:
        return "hike"
    return "other"


TRAINING_LOAD_CATEGORIES = {
    "bike_indoor",
    "bike_outdoor",
    "mtb",
    "gym",
    "run_treadmill",
    "run",
    "elliptical",
    "hike",
}


def counts_for_training_load(activity: dict) -> bool:
    return activity.get("category") in TRAINING_LOAD_CATEGORIES


def summarize_activity(payload: dict, path: Path | None = None) -> dict:
    started = activity_date(payload)
    duration_seconds = as_number(
        find_value(payload, ("duration", "durationSeconds", "elapsedDuration", "movingDuration"))
    )
    distance_m = as_number(find_value(payload, ("distance", "distanceMeters")))
    act_type = activity_type(payload)
    name = str(find_value(payload, ("activityName", "name")) or "")
    training_load = as_number(
        find_value(payload, ("activityTrainingLoad", "trainingLoad", "training_load"))
    )
    return {
        "id": str(find_value(payload, ("activityId", "id")) or (path.stem if path else "")),
        "date": started.isoformat() if started else None,
        "name": name,
        "type": act_type,
        "category": classify_activity_type(act_type, name),
        "counts_for_training_load": classify_activity_type(act_type, name)
        in TRAINING_LOAD_CATEGORIES,
        "duration_min": round(duration_seconds / 60, 1) if duration_seconds else None,
        "distance_km": round(distance_m / 1000, 2) if distance_m else None,
        "training_load": training_load,
        "avg_hr": as_number(find_value(payload, ("averageHR", "avgHr", "averageHeartRate"))),
        "max_hr": as_number(find_value(payload, ("maxHR", "maxHr", "maxHeartRate"))),
        "avg_power": as_number(find_value(payload, ("avgPower", "averagePower", "avgWatts"))),
        "normalized_power": as_number(find_value(payload, ("normPower", "normalizedPower"))),
        "intensity_factor": as_number(find_value(payload, ("intensityFactor", "intensity_factor"))),
        "aerobic_te": as_number(find_value(payload, ("aerobicTrainingEffect", "aerobic_te"))),
        "anaerobic_te": as_number(find_value(payload, ("anaerobicTrainingEffect", "anaerobic_te"))),
        "hr_zone_min": {
            f"z{zone}": round(
                (as_number(find_value(payload, (f"hrTimeInZone_{zone}", f"hr_zone_{zone}"))) or 0)
                / 60,
                1,
            )
            for zone in range(1, 6)
        },
        "source_file": str(path) if path else None,
    }


def _activity_fingerprint(base: Path) -> tuple[int, int, int]:
    count = 0
    latest_mtime_ns = 0
    total_size = 0
    for path in base.glob("**/*.json"):
        try:
            stat = path.stat()
        except OSError:
            continue
        count += 1
        latest_mtime_ns = max(latest_mtime_ns, stat.st_mtime_ns)
        total_size += stat.st_size
    return count, latest_mtime_ns, total_size


@lru_cache(maxsize=8)
def _load_activities_cached(
    base_dir: str,
    file_count: int,
    latest_mtime_ns: int,
    total_size: int,
) -> tuple[tuple[tuple[str, Any], ...], ...]:
    summaries: list[dict] = []
    # file_count/latest_mtime_ns/total_size are part of the cache key.
    _ = (file_count, latest_mtime_ns, total_size)
    for path in Path(base_dir).glob("**/*.json"):
        payload = read_json(path, {})
        if isinstance(payload, dict):
            summaries.append(summarize_activity(payload, path))
    ordered = sorted(summaries, key=lambda item: item.get("date") or "")
    return tuple(tuple(sorted(item.items())) for item in ordered)


def load_activities(root: str | Path | None = None) -> list[dict]:
    base = activities_dir(root).resolve()
    file_count, latest_mtime_ns, total_size = _activity_fingerprint(base)
    cached = _load_activities_cached(
        str(base),
        file_count,
        latest_mtime_ns,
        total_size,
    )
    return [dict(items) for items in cached]


def redacted_activity_summary(activity: dict | None) -> dict | None:
    if not activity:
        return None
    return {
        "date": activity.get("date"),
        "type": activity.get("type"),
        "category": activity.get("category"),
        "duration_min": activity.get("duration_min"),
        "distance_km": activity.get("distance_km"),
        "training_load": activity.get("training_load"),
        "avg_hr": activity.get("avg_hr"),
        "max_hr": activity.get("max_hr"),
        "avg_power": activity.get("avg_power"),
        "normalized_power": activity.get("normalized_power"),
        "intensity_factor": activity.get("intensity_factor"),
        "aerobic_te": activity.get("aerobic_te"),
        "anaerobic_te": activity.get("anaerobic_te"),
        "hr_zone_min": activity.get("hr_zone_min"),
    }


def summarize_recent_training(activities: list[dict], today: date) -> dict:
    def in_window(days: int, offset: int = 0) -> list[dict]:
        end = today - timedelta(days=offset)
        start = end - timedelta(days=days - 1)
        out = []
        for activity in activities:
            act_date = parse_date(activity.get("date"))
            if act_date and start <= act_date <= end:
                out.append(activity)
        return out

    last_7 = in_window(7)
    previous_7 = in_window(7, offset=7)
    last_28 = in_window(28)

    def totals(items: list[dict]) -> dict:
        training_items = [item for item in items if counts_for_training_load(item)]
        duration = sum(item.get("duration_min") or 0 for item in training_items)
        load = sum(item.get("training_load") or 0 for item in training_items)
        return {
            "sessions": len(training_items),
            "duration_min": round(duration, 1),
            "training_load": round(load, 1),
            "outdoor_mtb_sessions": sum(
                1 for item in training_items if item.get("category") == "mtb"
            ),
            "excluded_sessions": len(items) - len(training_items),
        }

    last_7_totals = totals(last_7)
    previous_7_totals = totals(previous_7)
    prev_load = previous_7_totals["training_load"]
    spike_ratio = None
    if prev_load > 0:
        spike_ratio = round(last_7_totals["training_load"] / prev_load, 2)
    latest_activity = activities[-1] if activities else None
    latest_training_activity = None
    for row in reversed(activities):
        if row.get("counts_for_training_load"):
            latest_training_activity = row
            break
    return {
        "last_7_days": last_7_totals,
        "previous_7_days": previous_7_totals,
        "last_28_days": totals(last_28),
        "acute_load_spike_ratio": spike_ratio,
        "latest_activity": redacted_activity_summary(latest_activity),
        "latest_training_activity": redacted_activity_summary(latest_training_activity),
    }
