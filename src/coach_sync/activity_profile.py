from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from .evidence import as_number, load_activities, redacted_activity_summary
from .io import write_json
from .paths import snapshots_dir
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local


def _in_window(activities: list[dict], target_date: date, days: int) -> list[dict]:
    start = target_date - timedelta(days=days - 1)
    return [
        activity
        for activity in activities
        if parse_date(activity.get("date")) and start <= parse_date(activity.get("date")) <= target_date
    ]


def _totals(items: list[dict]) -> dict:
    zone_totals = {f"z{zone}": 0.0 for zone in range(1, 6)}
    for item in items:
        for zone, minutes in (item.get("hr_zone_min") or {}).items():
            zone_totals[zone] = round(zone_totals.get(zone, 0) + (minutes or 0), 1)
    return {
        "sessions": len(items),
        "duration_min": round(sum(item.get("duration_min") or 0 for item in items), 1),
        "training_load": round(sum(item.get("training_load") or 0 for item in items), 1),
        "distance_km": round(sum(item.get("distance_km") or 0 for item in items), 1),
        "hr_zone_min": zone_totals,
        "with_power": sum(1 for item in items if item.get("avg_power") is not None),
    }


def _latest_by_category(activities: list[dict]) -> dict:
    latest = {}
    for activity in activities:
        category = activity.get("category") or "other"
        latest[category] = redacted_activity_summary(activity)
    return latest


def build_activity_profile(
    root: str | Path | None = None,
    for_date: str | date | None = None,
) -> dict:
    target = parse_date(for_date) or today_local(DEFAULT_TIMEZONE)
    activities = [item for item in load_activities(root) if parse_date(item.get("date"))]
    activities = [item for item in activities if parse_date(item.get("date")) <= target]
    categories = Counter(item.get("category") or "other" for item in activities)
    types = Counter(item.get("type") or "unknown" for item in activities)
    names = Counter()
    for item in activities:
        for word in (item.get("name") or "").lower().replace("-", " ").split():
            names[word] += 1

    profile = {
        "date": target.isoformat(),
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "activity_count": len(activities),
        "date_span": {
            "first": activities[0].get("date") if activities else None,
            "latest": activities[-1].get("date") if activities else None,
        },
        "categories": dict(categories.most_common()),
        "types": dict(types.most_common()),
        "last_7_days": _totals(_in_window(activities, target, 7)),
        "last_28_days": _totals(_in_window(activities, target, 28)),
        "latest_by_category": _latest_by_category(activities),
        "power_sessions": sum(1 for item in activities if item.get("avg_power") is not None),
        "common_name_terms": dict(names.most_common(20)),
    }
    write_json(snapshots_dir(root) / "activity_profile.json", profile)
    return profile
