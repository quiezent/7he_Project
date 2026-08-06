from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

from .evidence import as_number, counts_for_training_load, load_activities, summarize_activity
from .io import read_json, write_json, write_text
from .paths import activities_dir, snapshots_dir
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local
from .wellness import build_wellness_daily


TRAINABLE_CATEGORIES = {
    "bike_indoor",
    "bike_outdoor",
    "mtb",
    "elliptical",
    "run",
    "run_treadmill",
    "gym",
    "hike",
}
CYCLING_CATEGORIES = {"bike_indoor", "bike_outdoor", "mtb"}
BIKE_SPECIFIC_CATEGORIES = {"bike_indoor", "bike_outdoor", "mtb"}
DEFAULT_DAYS = 365


def _redacted_id(raw: Any) -> str:
    return sha256(str(raw).encode("utf-8")).hexdigest()[:12]


def _round(value: float | None, digits: int = 1) -> float | None:
    return round(value, digits) if value is not None else None


def _sum(rows: list[dict], key: str) -> float:
    return sum(as_number(row.get(key)) or 0.0 for row in rows)


def _avg(values: list[float | None]) -> float | None:
    usable = [value for value in values if value is not None]
    return mean(usable) if usable else None


def _max(values: list[float | None]) -> float | None:
    usable = [value for value in values if value is not None]
    return max(usable) if usable else None


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 6 or len(xs) != len(ys):
        return None
    mx = mean(xs)
    my = mean(ys)
    sx = pstdev(xs)
    sy = pstdev(ys)
    if sx == 0 or sy == 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / len(xs)
    return cov / (sx * sy)


def _quantile(values: list[float], q: float) -> float | None:
    usable = sorted(values)
    if not usable:
        return None
    position = (len(usable) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(usable) - 1)
    if lower == upper:
        return usable[lower]
    fraction = position - lower
    return usable[lower] * (1 - fraction) + usable[upper] * fraction


def _activity_rows(root: str | Path | None, start: date, target: date) -> list[dict]:
    rows: list[dict] = []
    # Rich key-session detail is preserved under activities/details but must not
    # become a duplicate longitudinal session.
    for path in activities_dir(root).glob("*.json"):
        payload = read_json(path, {})
        if not isinstance(payload, dict):
            continue
        summary = summarize_activity(payload, path)
        act_date = parse_date(summary.get("date"))
        if act_date is None or act_date < start or act_date > target:
            continue
        category = summary.get("category") or "other"
        duration = as_number(summary.get("duration_min")) or 0.0
        load = as_number(summary.get("training_load")) or 0.0
        zones = summary.get("hr_zone_min") or {}
        p20 = as_number(payload.get("maxAvgPower_1200")) or as_number(payload.get("max20MinPower"))
        p10 = as_number(payload.get("maxAvgPower_600"))
        p5 = as_number(payload.get("maxAvgPower_300"))
        avg_hr = as_number(summary.get("avg_hr"))
        avg_power = as_number(summary.get("avg_power"))
        normalized_power = as_number(summary.get("normalized_power"))
        power_hr_efficiency = (
            round(avg_power / avg_hr, 3)
            if avg_power is not None and avg_hr and avg_hr > 0
            else None
        )
        rows.append(
            {
                "activity_ref": _redacted_id(summary.get("id") or path.stem),
                "date": act_date.isoformat(),
                "category": category,
                "type": summary.get("type"),
                "counts_for_training_load": counts_for_training_load(summary),
                "duration_min": round(duration, 1),
                "distance_km": summary.get("distance_km"),
                "training_load": round(load, 1),
                "avg_hr": avg_hr,
                "max_hr": as_number(summary.get("max_hr")),
                "avg_power": avg_power,
                "normalized_power": normalized_power,
                "intensity_factor": as_number(summary.get("intensity_factor")),
                "training_stress_score": as_number(payload.get("trainingStressScore")),
                "aerobic_te": as_number(summary.get("aerobic_te")),
                "anaerobic_te": as_number(summary.get("anaerobic_te")),
                "p20_w": p20,
                "p10_w": p10,
                "p5_w": p5,
                "p1_w": as_number(payload.get("maxAvgPower_60")),
                "max_power_w": as_number(payload.get("maxPower")),
                "vo2max": as_number(payload.get("vO2MaxValue")),
                "power_hr_efficiency": power_hr_efficiency,
                "hr_zone_min": zones,
                "low_intensity_min": (as_number(zones.get("z1")) or 0.0)
                + (as_number(zones.get("z2")) or 0.0),
                "tempo_min": as_number(zones.get("z3")) or 0.0,
                "high_intensity_min": (as_number(zones.get("z4")) or 0.0)
                + (as_number(zones.get("z5")) or 0.0),
                "hard_session": bool(
                    load >= 75
                    or (as_number(summary.get("aerobic_te")) or 0.0) >= 3.0
                    or (as_number(zones.get("z4")) or 0.0)
                    + (as_number(zones.get("z5")) or 0.0)
                    >= 10.0
                ),
            }
        )
    return sorted(rows, key=lambda row: (row["date"], row["activity_ref"]))


def _totals(rows: list[dict]) -> dict:
    counted = [row for row in rows if row.get("counts_for_training_load")]
    by_category: dict[str, dict] = {}
    for category in sorted({row.get("category") or "other" for row in rows}):
        items = [row for row in rows if (row.get("category") or "other") == category]
        counted_items = [row for row in items if row.get("counts_for_training_load")]
        by_category[category] = {
            "sessions": len(counted_items),
            "duration_min": _round(_sum(counted_items, "duration_min")),
            "training_load": _round(_sum(counted_items, "training_load")),
        }
    return {
        "sessions": len(counted),
        "duration_min": _round(_sum(counted, "duration_min")),
        "training_load": _round(_sum(counted, "training_load")),
        "hard_sessions": sum(1 for row in counted if row.get("hard_session")),
        "low_intensity_min": _round(_sum(counted, "low_intensity_min")),
        "tempo_min": _round(_sum(counted, "tempo_min")),
        "high_intensity_min": _round(_sum(counted, "high_intensity_min")),
        "by_category": by_category,
    }


def _monthly(rows: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[str(row["date"])[:7]].append(row)
    out = []
    previous = None
    for month in sorted(grouped):
        items = grouped[month]
        counted = [row for row in items if row.get("counts_for_training_load")]
        cycling = [row for row in counted if row.get("category") in CYCLING_CATEGORIES]
        summary = {
            "month": month,
            **_totals(items),
            "cycling_sessions": len(cycling),
            "bike_specific_load": _round(
                sum(
                    row.get("training_load") or 0.0
                    for row in counted
                    if row.get("category") in BIKE_SPECIFIC_CATEGORIES
                )
            ),
            "elliptical_load": _round(
                sum(row.get("training_load") or 0.0 for row in counted if row.get("category") == "elliptical")
            ),
            "mtb_sessions": sum(1 for row in counted if row.get("category") == "mtb"),
            "best_p20_w": _round(_max([as_number(row.get("p20_w")) for row in cycling]), 0),
            "best_p10_w": _round(_max([as_number(row.get("p10_w")) for row in cycling]), 0),
            "best_vo2max": _round(_max([as_number(row.get("vo2max")) for row in cycling]), 1),
            "avg_cycling_efficiency_w_per_bpm": _round(
                _avg(
                    [
                        as_number(row.get("power_hr_efficiency"))
                        for row in cycling
                        if row.get("avg_power") is not None
                    ]
                ),
                3,
            ),
        }
        if previous:
            summary["change_vs_prior_month"] = {
                "training_load": _round(summary["training_load"] - previous["training_load"]),
                "bike_specific_load": _round(
                    (summary["bike_specific_load"] or 0.0)
                    - (previous["bike_specific_load"] or 0.0)
                ),
                "best_p20_w": (
                    _round(summary["best_p20_w"] - previous["best_p20_w"], 0)
                    if summary["best_p20_w"] is not None and previous["best_p20_w"] is not None
                    else None
                ),
                "best_vo2max": (
                    _round(summary["best_vo2max"] - previous["best_vo2max"], 1)
                    if summary["best_vo2max"] is not None and previous["best_vo2max"] is not None
                    else None
                ),
            }
        previous = summary
        out.append(summary)
    return out


def _daily(rows: list[dict], start: date, target: date) -> dict[str, dict]:
    daily = {
        (start + timedelta(days=offset)).isoformat(): {
            "date": (start + timedelta(days=offset)).isoformat(),
            "sessions": 0,
            "duration_min": 0.0,
            "training_load": 0.0,
            "low_intensity_min": 0.0,
            "tempo_min": 0.0,
            "high_intensity_min": 0.0,
            "hard_sessions": 0,
            "category_load": {category: 0.0 for category in TRAINABLE_CATEGORIES},
            "category_sessions": {category: 0 for category in TRAINABLE_CATEGORIES},
        }
        for offset in range((target - start).days + 1)
    }
    for row in rows:
        if not row.get("counts_for_training_load"):
            continue
        bucket = daily[row["date"]]
        category = row.get("category") or "other"
        bucket["sessions"] += 1
        bucket["duration_min"] += as_number(row.get("duration_min")) or 0.0
        bucket["training_load"] += as_number(row.get("training_load")) or 0.0
        bucket["low_intensity_min"] += as_number(row.get("low_intensity_min")) or 0.0
        bucket["tempo_min"] += as_number(row.get("tempo_min")) or 0.0
        bucket["high_intensity_min"] += as_number(row.get("high_intensity_min")) or 0.0
        bucket["hard_sessions"] += int(bool(row.get("hard_session")))
        if category in TRAINABLE_CATEGORIES:
            bucket["category_load"][category] += as_number(row.get("training_load")) or 0.0
            bucket["category_sessions"][category] += 1
    for bucket in daily.values():
        for key in ("duration_min", "training_load", "low_intensity_min", "tempo_min", "high_intensity_min"):
            bucket[key] = round(bucket[key], 1)
        bucket["category_load"] = {
            category: round(value, 1)
            for category, value in sorted(bucket["category_load"].items())
            if value > 0
        }
        bucket["category_sessions"] = {
            category: value
            for category, value in sorted(bucket["category_sessions"].items())
            if value > 0
        }
    return daily


def _window_values(daily: dict[str, dict], end: date, days: int) -> list[dict]:
    start = end - timedelta(days=days - 1)
    return [
        daily[day.isoformat()]
        for offset in range(days)
        for day in [start + timedelta(days=offset)]
        if day.isoformat() in daily
    ]


def _window_summary(daily: dict[str, dict], end: date, days: int) -> dict:
    values = _window_values(daily, end, days)
    category_load: Counter[str] = Counter()
    category_sessions: Counter[str] = Counter()
    loads = []
    for row in values:
        loads.append(row["training_load"])
        category_load.update(row.get("category_load") or {})
        category_sessions.update(row.get("category_sessions") or {})
    total = sum(loads)
    chronic_week = sum(row["training_load"] for row in _window_values(daily, end, 28)) / 4
    acwr = total / chronic_week if days == 7 and chronic_week > 0 else None
    nonzero_days = sum(1 for value in loads if value > 0)
    load_std = pstdev(loads) if len(loads) > 1 else 0.0
    monotony = (mean(loads) / load_std) if load_std > 0 else None
    return {
        "end_date": end.isoformat(),
        "days": days,
        "sessions": sum(row["sessions"] for row in values),
        "training_days": nonzero_days,
        "rest_days": len(values) - nonzero_days,
        "duration_min": _round(sum(row["duration_min"] for row in values)),
        "training_load": _round(total),
        "low_intensity_min": _round(sum(row["low_intensity_min"] for row in values)),
        "tempo_min": _round(sum(row["tempo_min"] for row in values)),
        "high_intensity_min": _round(sum(row["high_intensity_min"] for row in values)),
        "hard_sessions": sum(row["hard_sessions"] for row in values),
        "category_load": {category: _round(value) for category, value in sorted(category_load.items())},
        "category_sessions": dict(sorted(category_sessions.items())),
        "bike_specific_load": _round(
            sum(value for category, value in category_load.items() if category in BIKE_SPECIFIC_CATEGORIES)
        ),
        "bike_specific_ratio": _round(
            sum(value for category, value in category_load.items() if category in BIKE_SPECIFIC_CATEGORIES) / total,
            3,
        )
        if total > 0
        else None,
        "elliptical_ratio": _round(category_load.get("elliptical", 0.0) / total, 3) if total > 0 else None,
        "acwr_7_to_28": _round(acwr, 2),
        "load_monotony": _round(monotony, 2),
    }


def _rolling_highlights(daily: dict[str, dict], start: date, target: date) -> dict:
    ends = [start + timedelta(days=offset) for offset in range((target - start).days + 1)]
    full_28 = [day for day in ends if (day - start).days >= 27]
    full_7 = [day for day in ends if (day - start).days >= 6]
    summaries_28 = [_window_summary(daily, day, 28) for day in full_28]
    summaries_7 = [_window_summary(daily, day, 7) for day in full_7]
    active_28 = [row for row in summaries_28 if row["training_load"] > 0]
    return {
        "peak_28d_load": max(summaries_28, key=lambda row: row["training_load"]) if summaries_28 else None,
        "lowest_active_28d_load": min(active_28, key=lambda row: row["training_load"]) if active_28 else None,
        "peak_7d_load": max(summaries_7, key=lambda row: row["training_load"]) if summaries_7 else None,
        "recent_7d": _window_summary(daily, target, 7),
        "recent_28d": _window_summary(daily, target, 28),
        "high_monotony_28d": sorted(
            [row for row in summaries_28 if row.get("load_monotony") is not None],
            key=lambda row: row["load_monotony"],
            reverse=True,
        )[:5],
    }


def _best_sessions(rows: list[dict]) -> dict:
    cycling = [row for row in rows if row.get("category") in CYCLING_CATEGORIES]
    mtb = [row for row in rows if row.get("category") == "mtb"]
    return {
        "top_p20_sessions": [
            {
                "date": row["date"],
                "category": row["category"],
                "activity_ref": row["activity_ref"],
                "duration_min": row["duration_min"],
                "training_load": row["training_load"],
                "p20_w": _round(as_number(row.get("p20_w")), 0),
                "p10_w": _round(as_number(row.get("p10_w")), 0),
                "avg_hr": row.get("avg_hr"),
                "max_hr": row.get("max_hr"),
                "vo2max": row.get("vo2max"),
            }
            for row in sorted(cycling, key=lambda item: as_number(item.get("p20_w")) or -1, reverse=True)
           [:12]
            if row.get("p20_w") is not None
        ],
        "top_mtb_load_sessions": [
            {
                "date": row["date"],
                "activity_ref": row["activity_ref"],
                "duration_min": row["duration_min"],
                "training_load": row["training_load"],
                "avg_hr": row.get("avg_hr"),
                "max_hr": row.get("max_hr"),
                "high_intensity_min": _round(as_number(row.get("high_intensity_min"))),
                "normalized_power": row.get("normalized_power"),
                "p20_w": _round(as_number(row.get("p20_w")), 0),
            }
            for row in sorted(mtb, key=lambda item: as_number(item.get("training_load")) or 0, reverse=True)
           [:10]
        ],
        "best_vo2max_by_month": [
            {"month": month["month"], "best_vo2max": month.get("best_vo2max")}
            for month in _monthly(rows)
            if month.get("best_vo2max") is not None
        ],
    }


def _future_best(rows: list[dict], day: date, days: int, key: str, categories: set[str]) -> float | None:
    end = day + timedelta(days=days)
    values = []
    for row in rows:
        row_date = parse_date(row.get("date"))
        if row_date and day < row_date <= end and row.get("category") in categories:
            value = as_number(row.get(key))
            if value is not None:
                values.append(value)
    return max(values) if values else None


def _adaptation_associations(rows: list[dict], daily: dict[str, dict], start: date, target: date) -> dict:
    samples = []
    first_end = start + timedelta(days=27)
    last_end = target - timedelta(days=14)
    if last_end < first_end:
        return {"samples": 0, "correlations": [], "top_vs_bottom_quartile": []}
    day = first_end
    while day <= last_end:
        prior_28 = _window_summary(daily, day, 28)
        prior_7 = _window_summary(daily, day, 7)
        target_p20 = _future_best(rows, day, 14, "p20_w", CYCLING_CATEGORIES)
        target_p10 = _future_best(rows, day, 14, "p10_w", CYCLING_CATEGORIES)
        if target_p20 is not None:
            load = prior_28["training_load"] or 0.0
            samples.append(
                {
                    "date": day.isoformat(),
                    "target_next_14d_best_p20_w": target_p20,
                    "target_next_14d_best_p10_w": target_p10,
                    "features": {
                        "prev_28d_training_load": load,
                        "prev_28d_duration_min": prior_28["duration_min"] or 0.0,
                        "prev_28d_bike_specific_load": prior_28["bike_specific_load"] or 0.0,
                        "prev_28d_bike_specific_ratio": prior_28["bike_specific_ratio"] or 0.0,
                        "prev_28d_elliptical_load": prior_28["category_load"].get("elliptical", 0.0),
                        "prev_28d_mtb_load": prior_28["category_load"].get("mtb", 0.0),
                        "prev_28d_indoor_bike_load": prior_28["category_load"].get("bike_indoor", 0.0),
                        "prev_28d_run_load": prior_28["category_load"].get("run", 0.0)
                        + prior_28["category_load"].get("run_treadmill", 0.0),
                        "prev_28d_gym_load": prior_28["category_load"].get("gym", 0.0),
                        "prev_28d_low_intensity_min": prior_28["low_intensity_min"] or 0.0,
                        "prev_28d_tempo_min": prior_28["tempo_min"] or 0.0,
                        "prev_28d_high_intensity_min": prior_28["high_intensity_min"] or 0.0,
                        "prev_28d_hard_sessions": float(prior_28["hard_sessions"] or 0),
                        "prev_28d_rest_days": float(prior_28["rest_days"] or 0),
                        "prev_7d_training_load": prior_7["training_load"] or 0.0,
                        "prev_7_to_28_acwr": prior_7["acwr_7_to_28"] or 0.0,
                    },
                }
            )
        day += timedelta(days=1)
    correlations = []
    feature_names = sorted(samples[0]["features"]) if samples else []
    targets = [sample["target_next_14d_best_p20_w"] for sample in samples]
    for feature in feature_names:
        xs = [sample["features"][feature] for sample in samples]
        corr = _pearson(xs, targets)
        if corr is not None:
            correlations.append({"feature": feature, "pearson_r": _round(corr, 3)})
    correlations.sort(key=lambda item: abs(item["pearson_r"]), reverse=True)
    top_vs_bottom = []
    if samples:
        low_cut = _quantile(targets, 0.25)
        high_cut = _quantile(targets, 0.75)
        low_samples = [sample for sample in samples if sample["target_next_14d_best_p20_w"] <= low_cut]
        high_samples = [sample for sample in samples if sample["target_next_14d_best_p20_w"] >= high_cut]
        for feature in feature_names:
            low_avg = _avg([sample["features"][feature] for sample in low_samples])
            high_avg = _avg([sample["features"][feature] for sample in high_samples])
            if low_avg is None or high_avg is None:
                continue
            top_vs_bottom.append(
                {
                    "feature": feature,
                    "bottom_quartile_avg": _round(low_avg, 1),
                    "top_quartile_avg": _round(high_avg, 1),
                    "difference_top_minus_bottom": _round(high_avg - low_avg, 1),
                }
            )
        top_vs_bottom.sort(key=lambda item: abs(item["difference_top_minus_bottom"]), reverse=True)
    return {
        "target": "next_14d_best_cycling_p20_w",
        "method_note": "Overlapping windows; use as personal association, not proof of causality.",
        "samples": len(samples),
        "date_span": {
            "start": samples[0]["date"] if samples else None,
            "end": samples[-1]["date"] if samples else None,
        },
        "correlations": correlations[:12],
        "top_vs_bottom_quartile": top_vs_bottom[:12],
    }


def _detraining_flags(months: list[dict], rolling: dict) -> list[dict]:
    flags = []
    for month in months:
        change = month.get("change_vs_prior_month") or {}
        p20_change = change.get("best_p20_w")
        vo2_change = change.get("best_vo2max")
        if p20_change is not None and p20_change <= -10:
            flags.append(
                {
                    "period": month["month"],
                    "type": "cycling_power_drop",
                    "message": f"Best observed 20-min cycling power fell {abs(p20_change):.0f} W versus prior month.",
                    "context": {
                        "training_load": month["training_load"],
                        "bike_specific_load": month.get("bike_specific_load"),
                        "elliptical_load": month.get("elliptical_load"),
                        "mtb_sessions": month.get("mtb_sessions"),
                    },
                }
            )
        if vo2_change is not None and vo2_change <= -1.0:
            flags.append(
                {
                    "period": month["month"],
                    "type": "vo2max_drop",
                    "message": f"Best observed Garmin cycling VO2max marker fell {abs(vo2_change):.1f} point(s) versus prior month.",
                    "context": {
                        "training_load": month["training_load"],
                        "bike_specific_load": month.get("bike_specific_load"),
                    },
                }
            )
        if month.get("training_load", 0) < 800 and month.get("sessions", 0) >= 6:
            flags.append(
                {
                    "period": month["month"],
                    "type": "low_load_month",
                    "message": "Training continued but total monthly load was low enough to risk detraining.",
                    "context": {
                        "training_load": month["training_load"],
                        "hard_sessions": month.get("hard_sessions"),
                        "bike_specific_load": month.get("bike_specific_load"),
                    },
                }
            )
    lowest = rolling.get("lowest_active_28d_load")
    if lowest:
        flags.append(
            {
                "period": f"28d ending {lowest['end_date']}",
                "type": "lowest_rolling_load",
                "message": "Lowest active rolling 28-day load in the one-year window.",
                "context": {
                    "training_load": lowest.get("training_load"),
                    "bike_specific_load": lowest.get("bike_specific_load"),
                    "rest_days": lowest.get("rest_days"),
                    "category_load": lowest.get("category_load"),
                },
            }
        )
    return flags


def _wellness_overlap(root: str | Path | None, start: date, target: date) -> dict:
    rows = [
        row
        for row in build_wellness_daily(root)
        if parse_date(row.get("date")) and start <= parse_date(row.get("date")) <= target
    ]
    if not rows:
        return {"samples": 0, "note": "No wellness rows in the one-year window."}
    modern = [
        row
        for row in rows
        if as_number(row.get("body_battery_wake")) is not None
        or as_number(row.get("overnight_hrv")) is not None
        or row.get("hrv_status") is not None
    ]
    return {
        "samples": len(rows),
        "modern_samples": len(modern),
        "date_span": {"start": rows[0].get("date"), "end": rows[-1].get("date")},
        "modern_date_span": {
            "start": modern[0].get("date") if modern else None,
            "end": modern[-1].get("date") if modern else None,
        },
        "averages": {
            "sleep_score": _round(_avg([as_number(row.get("sleep_score")) for row in modern])),
            "sleep_hours": _round(_avg([as_number(row.get("sleep_hours")) for row in modern]), 2),
            "wake_body_battery": _round(_avg([as_number(row.get("body_battery_wake")) for row in modern])),
            "overnight_hrv": _round(_avg([as_number(row.get("overnight_hrv")) for row in modern])),
            "resting_hr": _round(_avg([as_number(row.get("resting_hr")) for row in modern])),
            "avg_stress": _round(_avg([as_number(row.get("avg_stress")) for row in modern])),
        },
        "note": "Wellness is included only where modern Garmin recovery fields are present; older gaps are not interpreted as physiology.",
    }


def _interpretive_rules(months: list[dict], associations: dict) -> list[dict]:
    correlation_by_feature = {
        item["feature"]: item["pearson_r"] for item in associations.get("correlations", [])
    }
    return [
        {
            "rule": "Bike-specific load is the strongest durable fitness currency.",
            "evidence": {
                "correlation_prev_28d_bike_specific_load_to_next_p20": correlation_by_feature.get(
                    "prev_28d_bike_specific_load"
                ),
                "correlation_prev_28d_indoor_bike_load_to_next_p20": correlation_by_feature.get(
                    "prev_28d_indoor_bike_load"
                ),
            },
            "coaching_use": "Keep at least 2 bike-specific exposures most weeks when trying to build or preserve cycling fitness.",
        },
        {
            "rule": "Non-bike aerobic load helps the engine but does not fully preserve bike power.",
            "evidence": {
                "correlation_prev_28d_elliptical_load_to_next_p20": correlation_by_feature.get(
                    "prev_28d_elliptical_load"
                ),
                "months_with_high_nonbike_load": [
                    month["month"]
                    for month in months
                    if (month.get("elliptical_load") or 0) >= 500
                    and (month.get("bike_specific_load") or 0) < 500
                ],
            },
            "coaching_use": "Use elliptical for recovery and aerobic continuity, but pair it with bike torque/cadence work if the goal is MTB performance.",
        },
        {
            "rule": "You respond to structured tempo/threshold blocks, but they are expensive.",
            "evidence": {
                "months_with_best_p20_170_plus": [
                    month["month"] for month in months if (month.get("best_p20_w") or 0) >= 170
                ],
            },
            "coaching_use": "Build back via 3x8 -> 3x10 -> 3x12 before returning to 170-180 W work blocks.",
        },
        {
            "rule": "MTB durability needs MTB exposure, not just indoor power.",
            "evidence": {
                "months_with_mtb_sessions": [
                    {
                        "month": month["month"],
                        "mtb_sessions": month.get("mtb_sessions"),
                        "training_load": month.get("training_load"),
                    }
                    for month in months
                    if month.get("mtb_sessions")
                ],
            },
            "coaching_use": "For enduro, protect one quality trail day and one durability trail day before adding more gym or indoor intensity.",
        },
    ]


def _text_report(artifact: dict) -> str:
    months = artifact["monthly"]
    lines = [
        f"Adaptation Profile - {artifact['date']}",
        "",
        f"Window: {artifact['window']['start']} to {artifact['window']['end']} ({artifact['window']['days']} days)",
        f"Activities counted: {artifact['totals']['sessions']} sessions, {artifact['totals']['duration_min']} min, load {artifact['totals']['training_load']}",
        "",
        "Monthly Fitness Markers:",
    ]
    for month in months:
        lines.append(
            "- "
            f"{month['month']}: load {month['training_load']}, bike load {month.get('bike_specific_load')}, "
            f"MTB {month.get('mtb_sessions')}, hard {month.get('hard_sessions')}, "
            f"best P20 {month.get('best_p20_w')}, VO2 {month.get('best_vo2max')}"
        )
    lines.extend(["", "Key Associations:"])
    for item in artifact["adaptation_associations"].get("correlations", [])[:8]:
        lines.append(f"- {item['feature']}: r={item['pearson_r']}")
    lines.extend(["", "Interpretive Rules:"])
    for rule in artifact["personal_rules"]:
        lines.append(f"- {rule['rule']} {rule['coaching_use']}")
    lines.extend(["", "Detraining Flags:"])
    for flag in artifact["detraining_flags"][:10]:
        lines.append(f"- {flag['period']}: {flag['message']}")
    lines.extend(["", "Caveats:"])
    lines.extend(f"- {caveat}" for caveat in artifact["caveats"])
    return "\n".join(lines) + "\n"


def build_adaptation_profile(
    root: str | Path | None = None,
    for_date: str | date | None = None,
    days: int | None = DEFAULT_DAYS,
) -> dict:
    target = parse_date(for_date) or today_local(DEFAULT_TIMEZONE)
    requested_days = days
    start = date(1900, 1, 1) if days is None or days <= 0 else target - timedelta(days=days - 1)
    rows = _activity_rows(root, start, target)
    if days is None or days <= 0:
        start = parse_date(rows[0]["date"]) if rows else target
        days = (target - start).days + 1
    daily = _daily(rows, start, target)
    months = _monthly(rows)
    rolling = _rolling_highlights(daily, start, target)
    associations = _adaptation_associations(rows, daily, start, target)
    artifact = {
        "date": target.isoformat(),
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "analysis_type": (
            "full_range_n_of_1_adaptation_profile"
            if requested_days is None or requested_days <= 0
            else "one_year_n_of_1_adaptation_profile"
        ),
        "window": {
            "start": start.isoformat(),
            "end": target.isoformat(),
            "days": days,
            "requested_days": requested_days,
            "mode": "full_available_range" if requested_days is None or requested_days <= 0 else "fixed_day_window",
        },
        "data_coverage": {
            "activity_rows": len(rows),
            "activity_date_span": {
                "start": rows[0]["date"] if rows else None,
                "end": rows[-1]["date"] if rows else None,
            },
            "wellness_overlap": _wellness_overlap(root, start, target),
        },
        "totals": _totals(rows),
        "monthly": months,
        "rolling_highlights": rolling,
        "best_sessions": _best_sessions(rows),
        "adaptation_associations": associations,
        "detraining_flags": _detraining_flags(months, rolling),
        "personal_rules": _interpretive_rules(months, associations),
        "caveats": [
            "This is an observational N-of-1 analysis from Garmin-derived records, not a randomized experiment.",
            "Power peaks depend on whether a workout gave you a chance to express power; absence of a test can understate fitness.",
            "Garmin cycling VO2max is useful directionally but is device/model-derived and affected by modality mix.",
            "Modern HRV and wake Body Battery coverage is much stronger from March 2026 onward than across the full year.",
            "The historical finger injury is a real confounder and is interpreted as context, not a current training gate.",
        ],
        "artifacts": {
            "json": "snapshots/adaptation_profile.json",
            "text": "snapshots/adaptation_profile.txt",
        },
    }
    write_json(snapshots_dir(root) / "adaptation_profile.json", artifact)
    write_text(snapshots_dir(root) / "adaptation_profile.txt", _text_report(artifact))
    return artifact
