from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

from .evidence import load_activities
from .io import write_json
from .paths import snapshots_dir
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local


def _valid_activities(root: str | Path | None, target: date) -> list[dict]:
    rows = []
    for activity in load_activities(root):
        act_date = parse_date(activity.get("date"))
        if act_date and act_date <= target:
            rows.append(activity)
    return rows


def _totals(rows: list[dict]) -> dict:
    return {
        "sessions": len(rows),
        "duration_min": round(sum(row.get("duration_min") or 0 for row in rows), 1),
        "training_load": round(sum(row.get("training_load") or 0 for row in rows), 1),
        "distance_km": round(sum(row.get("distance_km") or 0 for row in rows), 1),
    }


def _by_category(rows: list[dict]) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row.get("category") or "other"].append(row)
    return {category: _totals(items) for category, items in sorted(grouped.items())}


def _yearly(rows: list[dict]) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        act_date = parse_date(row.get("date"))
        if act_date:
            grouped[str(act_date.year)].append(row)
    return {year: {"all": _totals(items), "by_category": _by_category(items)} for year, items in grouped.items()}


def _window_rows(rows: list[dict], end: date, days: int) -> list[dict]:
    start = end - timedelta(days=days - 1)
    return [
        row
        for row in rows
        if parse_date(row.get("date")) and start <= parse_date(row.get("date")) <= end
    ]


def _best_windows(rows: list[dict], days: int, category: str | None = None) -> dict | None:
    if category:
        rows = [row for row in rows if row.get("category") == category]
    dates = sorted({parse_date(row.get("date")) for row in rows if parse_date(row.get("date"))})
    if not dates:
        return None
    best_load = None
    best_duration = None
    for end in dates:
        assert end is not None
        window = _window_rows(rows, end, days)
        totals = _totals(window)
        candidate = {"end_date": end.isoformat(), "days": days, **totals}
        if best_load is None or candidate["training_load"] > best_load["training_load"]:
            best_load = candidate
        if best_duration is None or candidate["duration_min"] > best_duration["duration_min"]:
            best_duration = candidate
    return {"best_by_load": best_load, "best_by_duration": best_duration}


def build_historical_baselines(
    root: str | Path | None = None,
    for_date: str | date | None = None,
) -> dict:
    target = parse_date(for_date) or today_local(DEFAULT_TIMEZONE)
    rows = _valid_activities(root, target)
    categories = Counter(row.get("category") or "other" for row in rows)
    mtb_rows = [row for row in rows if row.get("category") == "mtb"]
    pre_injury_mtb = [
        row
        for row in mtb_rows
        if parse_date(row.get("date")) and parse_date(row.get("date")) <= date(2025, 7, 31)
    ]
    artifact = {
        "date": target.isoformat(),
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "activity_count": len(rows),
        "date_span": {
            "first": rows[0].get("date") if rows else None,
            "latest": rows[-1].get("date") if rows else None,
        },
        "category_counts": dict(categories.most_common()),
        "all_time": {
            "all": _totals(rows),
            "by_category": _by_category(rows),
        },
        "yearly": _yearly(rows),
        "peak_windows": {
            "all_7_day": _best_windows(rows, 7),
            "all_28_day": _best_windows(rows, 28),
            "mtb_7_day": _best_windows(rows, 7, "mtb"),
            "mtb_28_day": _best_windows(rows, 28, "mtb"),
        },
        "pre_injury_mtb_baseline": {
            "through_date": "2025-07-31",
            "totals": _totals(pre_injury_mtb),
            "peak_7_day": _best_windows(pre_injury_mtb, 7),
            "peak_28_day": _best_windows(pre_injury_mtb, 28),
        },
        "historical_mtb_reference": {
            "message": "Use historical MTB windows as destination context, not as immediate targets.",
        },
    }
    write_json(snapshots_dir(root) / "historical_activity_baselines.json", artifact)
    return artifact
