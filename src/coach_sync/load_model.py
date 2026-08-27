from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from .context import load_context
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


BIKE_SPECIFIC_CATEGORIES = {"bike_indoor", "bike_outdoor", "mtb"}


def _bike_window_summary(rows: list[dict], start: date, end: date) -> dict:
    bike_rows = []
    for row in rows:
        row_date = parse_date(row.get("date"))
        if (
            row_date is not None
            and start <= row_date <= end
            and row.get("category") in BIKE_SPECIFIC_CATEGORIES
            and counts_for_training_load(row)
        ):
            bike_rows.append(row)
    bike_dates = sorted({str(row.get("date")) for row in bike_rows})
    mtb_dates = sorted(
        {
            str(row.get("date"))
            for row in bike_rows
            if row.get("category") == "mtb"
        }
    )
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "unique_bike_days": len(bike_dates),
        "bike_dates": bike_dates,
        "bike_activity_files": len(bike_rows),
        "bike_duration_min": round(
            sum(as_number(row.get("duration_min")) or 0 for row in bike_rows), 1
        ),
        "garmin_bike_load": round(
            sum(as_number(row.get("training_load")) or 0 for row in bike_rows), 1
        ),
        "mtb_unique_days": len(mtb_dates),
        "mtb_dates": mtb_dates,
    }


def build_bike_continuity_accountability(
    root: str | Path | None = None,
    for_date: str | date | None = None,
    *,
    activities: list[dict] | None = None,
    context: dict | None = None,
) -> dict:
    """Materialize the canonical bike-frequency guardrail as live evidence.

    This is deliberately an accountability surface, not an automatic permission
    to train. Physical readiness, CNS consequence, Sabbath and real calendar
    constraints still resolve the actual session.
    """
    target = parse_date(for_date) or today_local(DEFAULT_TIMEZONE)
    context = context if isinstance(context, dict) else load_context(root)
    rows = activities if activities is not None else load_activities(root)
    continuity = (
        (context.get("training_rules") or {}).get("bike_specific_continuity")
        or {}
    )

    maintenance_floor = int(continuity.get("minimum_bike_touches_per_week") or 2)
    preferred = int(
        continuity.get("preferred_rebuild_bike_touches_per_week")
        or max(5, maintenance_floor)
    )
    maximum = int(
        continuity.get("maximum_normal_build_bike_touches_per_week")
        or max(preferred, 6)
    )
    protected_mtb = int(continuity.get("protect_mtb_exposures_per_week") or 2)

    week_start = target - timedelta(days=target.weekday())
    week_end = week_start + timedelta(days=6)
    current_week = _bike_window_summary(rows, week_start, target)
    rolling_7 = _bike_window_summary(rows, target - timedelta(days=6), target)
    previous_7 = _bike_window_summary(
        rows,
        target - timedelta(days=13),
        target - timedelta(days=7),
    )

    rest_weekdays = {
        int(item.get("weekday"))
        for item in ((context.get("training_rules") or {}).get("weekly_rest_days") or [])
        if isinstance(item, dict) and item.get("weekday") is not None
    }
    remaining_non_rest_dates = [
        day.isoformat()
        for offset in range(1, (week_end - target).days + 1)
        if (day := target + timedelta(days=offset)).weekday() not in rest_weekdays
    ]
    gap = max(0, preferred - current_week["unique_bike_days"])
    if gap == 0:
        status = "preferred_target_met"
    elif target == week_end:
        status = "completed_week_below_preferred"
    elif gap > len(remaining_non_rest_dates):
        status = "preferred_target_at_risk"
    else:
        status = "building_toward_preferred"

    dose_anchors = continuity.get("indoor_endurance_dose_anchors") or {}
    routine_contract = dose_anchors.get("routine_low_cost_continuity_contract") or {}
    return {
        "date": target.isoformat(),
        "status": status,
        "targets": {
            "maintenance_floor_unique_bike_days": maintenance_floor,
            "preferred_unique_bike_days": preferred,
            "maximum_normal_unique_bike_days": maximum,
            "protected_mtb_unique_days": protected_mtb,
            "counting_rule": continuity.get("counting_rule")
            or "Count unique bike days, not split activity files.",
        },
        "current_calendar_week": current_week,
        "rolling_last_7_days": rolling_7,
        "previous_7_days": previous_7,
        "preferred_gap_unique_days": gap,
        "remaining_non_rest_calendar_dates": remaining_non_rest_dates,
        "routine_low_cost_continuity_dose": dose_anchors.get(
            "routine_low_cost_continuity"
        )
        or "60 minutes at 120-130 W and global RPE 2-3 when mechanically clean and fully resolving.",
        "routine_low_cost_continuity_contract": {
            "total_duration_min": int(routine_contract.get("total_duration_min") or 60),
            "main_power_w_range": routine_contract.get("main_power_w_range")
            or [120, 130],
            "global_rpe_range": routine_contract.get("global_rpe_range") or [2, 3],
            "density_cost": routine_contract.get("density_cost") or "low",
        },
        "progressive_overload_guardrail": continuity.get(
            "progressive_overload_guardrail"
        ),
        "weekly_frequency_audit_rule": continuity.get("weekly_frequency_audit"),
        "decision_use": (
            "Expose underdosing and prevent the two-touch maintenance floor or repeated short primers "
            "from being mistaken for a successful build. This block cannot override physical "
            "readiness, CNS ceiling, Sabbath, symptoms, environmental safety or a named calendar constraint."
        ),
        "provenance": {
            "targets": "config/athlete_context.json:training_rules.bike_specific_continuity",
            "actuals": "normalized Garmin activity summaries",
            "window_semantics": "Monday-through-target calendar week plus rolling seven-day comparisons",
        },
    }
