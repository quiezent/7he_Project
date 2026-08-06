from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import mean
from typing import Any

from .evidence import as_number, counts_for_training_load, summarize_activity
from .io import read_json, write_json, write_text
from .paths import activities_dir, input_dir, snapshots_dir
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local


BIKE_CATEGORIES = {"bike_indoor", "bike_outdoor", "mtb"}
SUBSTITUTION_MONTHS = ("2025-12", "2026-01", "2026-02", "2026-04")
WEIRD_MONTHS = ("2022-03", "2022-04", "2023-06", "2023-12", "2024-03", "2024-05", "2025-05")


def _round(value: float | None, digits: int = 1) -> float | None:
    return round(value, digits) if value is not None else None


def _avg(values: list[float | None]) -> float | None:
    usable = [value for value in values if value is not None]
    return mean(usable) if usable else None


def _raw_rows(root: str | Path | None) -> list[dict]:
    rows = []
    # Only top-level Garmin summaries belong in longitudinal activity analysis.
    # Rich key-session detail under activities/details is preserved raw evidence,
    # not an additional synthetic activity.
    for path in activities_dir(root).glob("*.json"):
        payload = read_json(path, {})
        if not isinstance(payload, dict):
            continue
        summary = summarize_activity(payload, path)
        day = parse_date(summary.get("date"))
        if day is None:
            continue
        zones = summary.get("hr_zone_min") or {}
        rows.append(
            {
                **summary,
                "date_obj": day,
                "start_time_local": payload.get("startTimeLocal"),
                "activity_name": payload.get("activityName") or summary.get("name"),
                "activity_type": (payload.get("activityType") or {}).get("typeKey") or summary.get("type"),
                "manufacturer": payload.get("manufacturer"),
                "device_id": payload.get("deviceId"),
                "duration_min": _round(summary.get("duration_min")),
                "elapsed_min": _round((as_number(payload.get("elapsedDuration")) or 0) / 60),
                "moving_min": _round((as_number(payload.get("movingDuration")) or 0) / 60),
                "elevation_gain_m": _round(as_number(payload.get("elevationGain"))),
                "calories": _round(as_number(payload.get("calories")), 0),
                "training_load": _round(as_number(summary.get("training_load")) or 0),
                "training_stress_score": _round(as_number(payload.get("trainingStressScore"))),
                "garmin_detected_ftp_w": _round(as_number(payload.get("maxFtp")), 0),
                "p20_w": _round(
                    as_number(payload.get("maxAvgPower_1200")) or as_number(payload.get("max20MinPower")),
                    0,
                ),
                "p10_w": _round(as_number(payload.get("maxAvgPower_600")), 0),
                "p5_w": _round(as_number(payload.get("maxAvgPower_300")), 0),
                "p1_w": _round(as_number(payload.get("maxAvgPower_60")), 0),
                "max_power_w": _round(as_number(payload.get("maxPower")), 0),
                "vo2max": _round(as_number(payload.get("vO2MaxValue"))),
                "avg_cadence": _round(as_number(payload.get("averageBikingCadenceInRevPerMinute"))),
                "max_cadence": _round(as_number(payload.get("maxBikingCadenceInRevPerMinute"))),
                "min_temp_c": _round(as_number(payload.get("minTemperature"))),
                "max_temp_c": _round(as_number(payload.get("maxTemperature"))),
                "flow": _round(as_number(payload.get("avgFlow")), 2),
                "grit": _round(as_number(payload.get("grit")), 1),
                "has_intensity_intervals": bool(payload.get("hasIntensityIntervals")),
                "split_summaries": payload.get("splitSummaries") or [],
                "high_hr_min": _round((as_number(zones.get("z4")) or 0) + (as_number(zones.get("z5")) or 0)),
            }
        )
    return sorted(rows, key=lambda row: (row["date"], row.get("start_time_local") or ""))


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _weekly_bike_minimum(rows: list[dict], start: date, end: date) -> list[dict]:
    first_week = _week_start(start)
    weeks = []
    cursor = first_week
    while cursor <= end:
        weeks.append(cursor)
        cursor += timedelta(days=7)
    out = []
    for week in weeks:
        week_rows = [row for row in rows if week <= row["date_obj"] <= week + timedelta(days=6)]
        counted = [row for row in week_rows if counts_for_training_load(row)]
        bike = [row for row in counted if row.get("category") in BIKE_CATEGORIES]
        cat_counts = Counter(row.get("category") for row in bike)
        cat_load = Counter()
        for row in counted:
            cat_load[row.get("category") or "other"] += row.get("training_load") or 0
        bike_load = sum(row.get("training_load") or 0 for row in bike)
        total_load = sum(row.get("training_load") or 0 for row in counted)
        bike_days = len({row["date"] for row in bike})
        missed_reason = None
        if not bike:
            nonbike = cat_load.most_common(1)
            missed_reason = f"no bike-specific session; load mostly {nonbike[0][0]}" if nonbike else "no counted training"
        elif len(bike) == 1:
            missed_reason = "only one bike touch"
        elif bike_load < 100:
            missed_reason = "bike touches were very light"
        out.append(
            {
                "week_start": week.isoformat(),
                "week_end": (week + timedelta(days=6)).isoformat(),
                "bike_sessions": len(bike),
                "indoor_bike_sessions": cat_counts.get("bike_indoor", 0),
                "mtb_sessions": cat_counts.get("mtb", 0),
                "outdoor_easy_or_commute_sessions": cat_counts.get("bike_outdoor", 0),
                "bike_days": bike_days,
                "bike_load": _round(bike_load),
                "total_load": _round(total_load),
                "bike_load_ratio": _round(bike_load / total_load, 2) if total_load else None,
                "missed_or_low_bike_reason_from_data": missed_reason,
            }
        )
    return out


def _month_rows(rows: list[dict], month: str) -> list[dict]:
    return [row for row in rows if row["date"].startswith(month)]


def _monthly_summary(rows: list[dict], month: str) -> dict:
    items = _month_rows(rows, month)
    counted = [row for row in items if counts_for_training_load(row)]
    cat_load = Counter()
    cat_sessions = Counter()
    for row in counted:
        cat = row.get("category") or "other"
        cat_load[cat] += row.get("training_load") or 0
        cat_sessions[cat] += 1
    cycling = [row for row in counted if row.get("category") in BIKE_CATEGORIES]
    return {
        "month": month,
        "sessions": len(counted),
        "training_load": _round(sum(row.get("training_load") or 0 for row in counted)),
        "category_sessions": dict(sorted(cat_sessions.items())),
        "category_load": {cat: _round(value) for cat, value in sorted(cat_load.items())},
        "bike_specific_load": _round(sum(row.get("training_load") or 0 for row in cycling)),
        "bike_sessions": len(cycling),
        "mtb_sessions": sum(1 for row in cycling if row.get("category") == "mtb"),
        "best_p20_w": max([row.get("p20_w") for row in cycling if row.get("p20_w") is not None], default=None),
        "best_vo2max": max([row.get("vo2max") for row in cycling if row.get("vo2max") is not None], default=None),
    }


def _substitution_blocks(rows: list[dict]) -> list[dict]:
    context = {
        "2025-12": "Known context: left pinky fracture era; MTB was absent after July 2025. Garmin cannot distinguish injury-history caution from convenience.",
        "2026-01": "Known context: historical injury-era low-bike block; high elliptical load with almost no bike continuity.",
        "2026-02": "Known context: historical injury-era low-bike block; bike work returned only late in the month.",
        "2026-04": "Known context: late injury-history block; treat as historical context rather than a current training gate.",
    }
    out = []
    for month in SUBSTITUTION_MONTHS:
        summary = _monthly_summary(rows, month)
        out.append(
            {
                **summary,
                "known_context": context[month],
                "best_read_from_data": _substitution_read(summary),
                "still_needs_clayton_note": [
                    "bike mechanical/access",
                    "weather/trail closures",
                    "work stress/travel",
                    "motivation/fear of crashing",
                    "planned cross-training versus fallback convenience",
                ],
            }
        )
    return out


def _substitution_read(summary: dict) -> str:
    bike_load = summary.get("bike_specific_load") or 0
    elliptical = (summary.get("category_load") or {}).get("elliptical", 0)
    if bike_load < 400 and elliptical >= 1000:
        return "non-bike aerobic work became the backbone; bike force/frequency likely under-maintained"
    if bike_load < 700:
        return "bike continuity was below rebuild level"
    return "bike continuity partly protected but still not MTB-specific"


def _power_test_audit(rows: list[dict], target: date) -> dict:
    all_cycling = [row for row in rows if row.get("category") in BIKE_CATEGORIES]
    cycling = [row for row in all_cycling if row.get("p20_w") is not None]
    formal = [
        row
        for row in cycling
        if any(token in (row.get("activity_name") or "").lower() for token in ("ftp test", "fitness test"))
    ]
    structured = [
        row
        for row in cycling
        if any(token in (row.get("activity_name") or "").lower() for token in ("threshold", "ftp training", "tempo", "vo2 max"))
    ]
    recent_60 = [row for row in cycling if row["date_obj"] >= target - timedelta(days=59)]
    detected_ftp_rows = [
        row for row in all_cycling if row.get("garmin_detected_ftp_w") is not None
    ]
    latest_detected_ftp = detected_ftp_rows[-1] if detected_ftp_rows else None
    recent_detected_ftp = [
        row for row in detected_ftp_rows if row["date_obj"] >= target - timedelta(days=59)
    ]
    if latest_detected_ftp is not None:
        age_days = (target - latest_detected_ftp["date_obj"]).days
        current_ftp_call = (
            f"Current Garmin operational FTP is {int(latest_detected_ftp['garmin_detected_ftp_w'])} W "
            f"from the sparse activity maxFtp detection surface on {latest_detected_ftp['date']}. "
            "Use it for current FTP-relative prescription with RPE/HR validation; it is Garmin-estimated, "
            "not equivalent to a clean laboratory or steady-state field test."
        )
    else:
        age_days = None
        current_ftp_call = (
            "No current Garmin FTP-detection surface found. Treat 222 W as historical P20, not current FTP, "
            "and prescribe from current controlled efforts until a valid current FTP source exists."
        )
    return {
        "last_formal_test_candidate": _power_row(formal[-1]) if formal else None,
        "best_formal_20_min_candidate": _power_row(max(formal, key=lambda row: row.get("p20_w") or 0)) if formal else None,
        "historical_best_p20": _power_row(max(cycling, key=lambda row: row.get("p20_w") or 0)) if cycling else None,
        "last_structured_power_candidate": _power_row(structured[-1]) if structured else None,
        "recent_60d_best_p20": _power_row(max(recent_60, key=lambda row: row.get("p20_w") or 0)) if recent_60 else None,
        "latest_garmin_detected_ftp": _ftp_row(latest_detected_ftp, age_days),
        "recent_60d_garmin_detected_ftp": [
            _ftp_row(row, (target - row["date_obj"]).days) for row in recent_detected_ftp
        ],
        "current_ftp_call": current_ftp_call,
        "garmin_ftp_interpretation": (
            "Top-level maxFtp is a sparse Garmin detection/max-metric surface, unlike the "
            "functionalThresholdPower setting carried into ordinary IF/TSS calculations. Preserve the "
            "detection provenance separately from clean-test validity."
        ),
        "power_source_note": "Raw Garmin summaries identify indoor/outdoor activity, Garmin device/manufacturer, and power values, but not the exact power source such as Elite Suito versus bike power meter.",
    }


def _ftp_row(row: dict | None, age_days: int | None = None) -> dict | None:
    if not row:
        return None
    return {
        "date": row.get("date"),
        "activity_id": row.get("id"),
        "name": row.get("activity_name"),
        "category": row.get("category"),
        "ftp_w": row.get("garmin_detected_ftp_w"),
        "age_days": age_days,
        "source_field": "maxFtp",
    }


def _power_row(row: dict | None) -> dict | None:
    if not row:
        return None
    return {
        "date": row.get("date"),
        "name": row.get("activity_name"),
        "category": row.get("category"),
        "duration_min": row.get("duration_min"),
        "p20_w": row.get("p20_w"),
        "p10_w": row.get("p10_w"),
        "avg_hr": row.get("avg_hr"),
        "max_hr": row.get("max_hr"),
        "avg_power": row.get("avg_power"),
        "normalized_power": row.get("normalized_power"),
        "intensity_factor": row.get("intensity_factor"),
        "training_load": row.get("training_load"),
        "manufacturer": row.get("manufacturer"),
        "device_id": row.get("device_id"),
    }


def _weird_months(rows: list[dict]) -> list[dict]:
    out = []
    for month in WEIRD_MONTHS:
        summary = _monthly_summary(rows, month)
        items = _month_rows(rows, month)
        cycling = [row for row in items if row.get("category") in BIKE_CATEGORIES]
        testish = [
            row
            for row in cycling
            if any(token in (row.get("activity_name") or "").lower() for token in ("test", "threshold", "tempo", "vo2", "ftp"))
        ]
        best = max(cycling, key=lambda row: row.get("p20_w") or -1, default=None)
        out.append(
            {
                **summary,
                "structured_power_opportunities": len(testish),
                "best_power_activity": _power_row(best),
                "coach_read": _weird_month_read(summary, testish, best),
            }
        )
    return out


def _weird_month_read(summary: dict, testish: list[dict], best: dict | None) -> str:
    mtb = summary.get("mtb_sessions") or 0
    load = summary.get("training_load") or 0
    p20 = summary.get("best_p20_w")
    if mtb >= 8 and len(testish) <= 2:
        return "large MTB/stochastic trail block; likely built durability but did not create a clean sustained power expression"
    if load >= 2800 and p20 and p20 < 170:
        return "high load with suppressed/hidden P20; accumulated fatigue or lack of fresh test is likely"
    if best and best.get("category") == "mtb":
        return "best P20 came from trail, so terrain/coasting/descents make the metric noisy"
    return "needs subjective note: fatigue, illness, heat, racing, bike issue, or missed test opportunity"


def _classify_mtb_ride(row: dict) -> dict:
    load = row.get("training_load") or 0
    duration = row.get("duration_min") or 0
    high_hr = row.get("high_hr_min") or 0
    grit = row.get("grit") or 0
    if duration >= 140:
        ride_type = "long Z2/trail durability with technical fatigue"
    elif load >= 170 or high_hr >= 18:
        ride_type = "enduro laps / race-simulation fitness load"
    elif grit >= 55:
        ride_type = "technical trail session"
    else:
        ride_type = "controlled trail exposure / social-durability ride"
    skill = "braking and line choice under heat/fatigue"
    if row.get("locationName") == "Putrajaya" or "putrajaya" in (row.get("activity_name") or "").lower():
        skill = "jump timing and curated trail speed"
    elif grit >= 60:
        skill = "rough terrain composure and braking durability"
    elif (row.get("normalized_power") or 0) > (row.get("avg_power") or 0) * 1.7:
        skill = "punchy climbs and corner-exit accelerations"
    return {
        "date": row.get("date"),
        "start_time_local": row.get("start_time_local"),
        "name": row.get("activity_name"),
        "duration_min": row.get("duration_min"),
        "distance_km": row.get("distance_km"),
        "elevation_gain_m": row.get("elevation_gain_m"),
        "training_load": row.get("training_load"),
        "avg_hr": row.get("avg_hr"),
        "max_hr": row.get("max_hr"),
        "avg_power": row.get("avg_power"),
        "normalized_power": row.get("normalized_power"),
        "p20_w": row.get("p20_w"),
        "high_hr_min": row.get("high_hr_min"),
        "temperature_c": {"min": row.get("min_temp_c"), "max": row.get("max_temp_c")},
        "flow": row.get("flow"),
        "grit": row.get("grit"),
        "ride_type_inferred": ride_type,
        "skill_focus_inferred": skill,
        "confidence": "medium" if row.get("activity_name") else "low",
    }


def _last_mtb(rows: list[dict], limit: int = 10) -> list[dict]:
    return [_classify_mtb_ride(row) for row in [r for r in rows if r.get("category") == "mtb"][-limit:]][::-1]


def _wellness_by_date(root: str | Path | None) -> dict[str, dict]:
    rows = read_json(snapshots_dir(root) / "wellness_daily.json", [])
    return {row.get("date"): row for row in rows if isinstance(row, dict) and row.get("date")}


def _readiness_by_date(root: str | Path | None) -> dict[str, dict]:
    out = {}
    for path in snapshots_dir(root).glob("readiness_*.json"):
        row = read_json(path, {})
        if isinstance(row, dict) and row.get("date"):
            out[row["date"]] = row
    return out


def _subjective_notes(root: str | Path | None) -> dict[str, list[str]]:
    notes: dict[str, list[str]] = defaultdict(list)
    for path in input_dir(root).glob("**/*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        day = None
        if path.suffix == ".json":
            payload = read_json(path, {})
            day = payload.get("date") if isinstance(payload, dict) else None
            if isinstance(payload, dict):
                for key in ("next_morning_response", "notes"):
                    if payload.get(key):
                        notes[day].append(str(payload[key]))
        else:
            for line in text.splitlines():
                if line.lower().startswith("date:"):
                    day = line.split(":", 1)[1].strip()
                if any(line.lower().startswith(prefix) for prefix in ("notes:", "next_morning_response:", "ride_purpose:", "trail_condition:", "skill_quality:", "fueling:", "heat_feel:")):
                    if day:
                        notes[day].append(line.strip())
    return notes


def _recovery_30d(root: str | Path | None, rows: list[dict], target: date) -> list[dict]:
    wellness = _wellness_by_date(root)
    readiness = _readiness_by_date(root)
    notes = _subjective_notes(root)
    start = target - timedelta(days=29)
    out = []
    for row in rows:
        if row["date_obj"] < start or row["date_obj"] > target:
            continue
        if row.get("category") != "mtb" and (row.get("training_load") or 0) < 75:
            continue
        w = wellness.get(row["date"]) or {}
        r = readiness.get(row["date"]) or {}
        out.append(
            {
                "date": row.get("date"),
                "activity": row.get("activity_name"),
                "category": row.get("category"),
                "duration_min": row.get("duration_min"),
                "training_load": row.get("training_load"),
                "avg_hr": row.get("avg_hr"),
                "max_hr": row.get("max_hr"),
                "readiness_score": r.get("readiness_score"),
                "readiness_level": r.get("readiness_level"),
                "wake_body_battery": w.get("body_battery_wake"),
                "current_body_battery": w.get("body_battery_current"),
                "sleep_hours": w.get("sleep_hours"),
                "sleep_score": w.get("sleep_score"),
                "overnight_hrv": w.get("overnight_hrv"),
                "hrv_status": w.get("hrv_status"),
                "resting_hr": w.get("resting_hr"),
                "subjective_notes_available": notes.get(row["date"], []),
            }
        )
    return out


def _heat_last_mtb(rows: list[dict], limit: int = 6) -> list[dict]:
    rides = [row for row in rows if row.get("category") == "mtb"][-limit:][::-1]
    return [
        {
            "date": row.get("date"),
            "start_time_local": row.get("start_time_local"),
            "name": row.get("activity_name"),
            "duration_min": row.get("duration_min"),
            "training_load": row.get("training_load"),
            "avg_hr": row.get("avg_hr"),
            "max_hr": row.get("max_hr"),
            "temperature_c": {"min": row.get("min_temp_c"), "max": row.get("max_temp_c")},
            "humidity": None,
            "trail_condition": None,
            "hydration_logged": None,
            "coach_read": "temperature present; humidity, wet/muddy/slippery condition, and hydration were not logged in Garmin summary",
        }
        for row in rides
    ]


def _nutrition_targets(root: str | Path | None) -> dict:
    wellness = read_json(snapshots_dir(root) / "wellness_daily.json", [])
    weights = [row for row in wellness if isinstance(row, dict) and row.get("body_weight_kg") is not None]
    latest = weights[-1] if weights else {}
    kg = as_number(latest.get("body_weight_kg"))
    return {
        "latest_weight_kg": _round(kg, 2),
        "latest_weight_date": latest.get("date"),
        "mtb_90_to_150_min": {
            "carbs_g_per_hour": [45, 75],
            "sodium_mg_per_hour": [600, 1000],
            "fluid_ml_per_hour": [500, 900],
        },
        "mtb_over_150_min_or_race_practice": {
            "carbs_g_per_hour": [60, 90],
            "sodium_mg_per_hour": [800, 1200],
            "fluid_ml_per_hour": [650, 1000],
        },
        "note": "Use the upper end in Kuala Lumpur heat or when skill quality drops late; gut-train before race day.",
    }


def _gym_audit(rows: list[dict], target: date) -> dict:
    recent = [
        row
        for row in rows
        if row.get("category") == "gym" and row["date_obj"] >= target - timedelta(days=59)
    ]
    return {
        "recent_garmin_gym_sessions": [
            {
                "date": row.get("date"),
                "duration_min": row.get("duration_min"),
                "training_load": row.get("training_load"),
                "avg_hr": row.get("avg_hr"),
                "max_hr": row.get("max_hr"),
                "aerobic_te": row.get("aerobic_te"),
                "anaerobic_te": row.get("anaerobic_te"),
            }
            for row in recent
        ],
        "missing_fields": ["exercises", "sets", "reps", "external_load", "RPE", "24h soreness", "48h soreness"],
        "coach_read": "Useful as a small durability dose only if it does not reduce Friday/Saturday trail quality.",
    }


def _indoor_structure(rows: list[dict], target: date) -> list[dict]:
    recent = [
        row
        for row in rows
        if row.get("category") == "bike_indoor" and row["date_obj"] >= target - timedelta(days=13)
    ]
    return [
        {
            "date": row.get("date"),
            "name": row.get("activity_name"),
            "duration_min": row.get("duration_min"),
            "training_load": row.get("training_load"),
            "avg_power": row.get("avg_power"),
            "normalized_power": row.get("normalized_power"),
            "p20_w": row.get("p20_w"),
            "avg_hr": row.get("avg_hr"),
            "max_hr": row.get("max_hr"),
            "avg_cadence": row.get("avg_cadence"),
            "intensity_factor": row.get("intensity_factor"),
            "has_intensity_intervals": row.get("has_intensity_intervals"),
            "split_types": [item.get("splitType") for item in row.get("split_summaries") or []],
            "coach_read": _indoor_read(row),
        }
        for row in recent
    ]


def _indoor_read(row: dict) -> str:
    name = (row.get("activity_name") or "").lower()
    if "tempo" in name:
        return "structured tempo/torque stimulus; currently useful for rebuild"
    if "base" in name:
        return "aerobic bike touch; useful for maintenance but not enough alone for enduro force"
    return "bike-specific touch; needs cadence/RPE note for better interpretation"


def _limiter_ranking() -> list[dict]:
    return [
        {"rank": 1, "limiter": "Upper-body durability on long descents", "confidence": "medium", "why": "long MTB gap plus enduro goal; Garmin cannot measure arm pump/braking fatigue directly"},
        {"rank": 2, "limiter": "Heavy braking fatigue", "confidence": "medium", "why": "specific enduro demand absent from indoor work and only recently reintroduced"},
        {"rank": 3, "limiter": "Repeated hard efforts after descents", "confidence": "medium_high", "why": "recent MTB loads are meaningful, but repeatability after trail fatigue is still rebuilding"},
        {"rank": 4, "limiter": "Sustained climbing power", "confidence": "high", "why": "current P20/VO2 markers are below historical high"},
        {"rank": 5, "limiter": "Repeat 2-5 minute punch power", "confidence": "medium", "why": "recent data shows some punch power, but no dedicated repeatability block yet"},
        {"rank": 6, "limiter": "Corner exit acceleration", "confidence": "low_medium", "why": "power/skill overlap; needs trail notes"},
        {"rank": 7, "limiter": "Grip confidence in wet roots/rocks", "confidence": "low", "why": "wet-tech confidence is subjective and needs trail notes"},
        {"rank": 8, "limiter": "Jump speed/scrub timing", "confidence": "low", "why": "PCP/jump-specific data is not yet in the current synced MTB set"},
    ]


def build_athlete_question_audit(
    root: str | Path | None = None,
    for_date: str | date | None = None,
) -> dict:
    target = parse_date(for_date) or today_local(DEFAULT_TIMEZONE)
    rows = _raw_rows(root)
    window_start = date(2025, 8, 1)
    artifact = {
        "date": target.isoformat(),
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "analysis_type": "athlete_profile_question_audit",
        "scope": {
            "activity_rows": len(rows),
            "activity_start": rows[0]["date"] if rows else None,
            "activity_end": rows[-1]["date"] if rows else None,
            "weekly_bike_window": {"start": window_start.isoformat(), "end": target.isoformat()},
        },
        "bike_specific_minimum_weeks": _weekly_bike_minimum(rows, window_start, target),
        "nonbike_substitution_blocks": _substitution_blocks(rows),
        "power_test_and_ftp": _power_test_audit(rows, target),
        "weird_high_load_low_p20_months": _weird_months(rows),
        "last_10_mtb_ride_labels": _last_mtb(rows, 10),
        "limiter_ranking_provisional": _limiter_ranking(),
        "last_30d_recovery_vs_hard_days": _recovery_30d(root, rows, target),
        "last_6_mtb_heat_audit": _heat_last_mtb(rows, 6),
        "nutrition_targets": _nutrition_targets(root),
        "strength_training_audit": _gym_audit(rows, target),
        "recent_indoor_trainer_structure": _indoor_structure(rows, target),
        "trail_frequency_read": {
            "best_current_target": "protect one quality MTB day and one durability/enduro-volume MTB day each week; use Saturday as optional PCP/jump/skills only when driving/family load allows",
            "sabbath": "Sunday remains hard rest",
            "needs_calendar_input": ["fixed weekly outdoor windows", "Sepang/driving commitments", "family day constraints"],
        },
        "data_gaps": [
            "reason bike was missed is mostly not in Garmin",
            "exact gym exercises/sets/reps/load and soreness are not in Garmin",
            "carbs, sodium, fluid, caffeine, and late-ride skill fade are not in Garmin",
            "humidity, rain, mud, slippery roots/rocks, and trail closure state are not in Garmin summaries",
            "actual ride feel and aggression/confidence need post-ride notes",
        ],
        "artifacts": {
            "json": "snapshots/athlete_question_audit.json",
            "text": "snapshots/athlete_question_audit.txt",
        },
    }
    write_json(snapshots_dir(root) / "athlete_question_audit.json", artifact)
    write_text(snapshots_dir(root) / "athlete_question_audit.txt", _text_report(artifact))
    return artifact


def _text_report(artifact: dict) -> str:
    lines = [
        f"Athlete Question Audit - {artifact['date']}",
        "",
        "Bike Minimum Weekly Table:",
    ]
    for week in artifact["bike_specific_minimum_weeks"]:
        note = week["missed_or_low_bike_reason_from_data"] or "ok"
        lines.append(
            f"- {week['week_start']}: bike {week['bike_sessions']} "
            f"(indoor {week['indoor_bike_sessions']}, MTB {week['mtb_sessions']}, outdoor/easy {week['outdoor_easy_or_commute_sessions']}), "
            f"bike load {week['bike_load']}, total load {week['total_load']}, note {note}"
        )
    lines.extend(["", "Substitution Blocks:"])
    for month in artifact["nonbike_substitution_blocks"]:
        lines.append(
            f"- {month['month']}: bike load {month['bike_specific_load']}, total {month['training_load']}, "
            f"sessions {month['category_sessions']}. {month['best_read_from_data']}"
        )
    lines.extend(["", "Power Test / FTP:"])
    ftp = artifact["power_test_and_ftp"]
    lines.append(f"- Historical best: {ftp['historical_best_p20']}")
    lines.append(f"- Last formal test candidate: {ftp['last_formal_test_candidate']}")
    lines.append(f"- Recent 60d best P20: {ftp['recent_60d_best_p20']}")
    lines.append(f"- Latest Garmin detected FTP: {ftp['latest_garmin_detected_ftp']}")
    lines.append(f"- Current call: {ftp['current_ftp_call']}")
    lines.extend(["", "Last 10 MTB Labels:"])
    for ride in artifact["last_10_mtb_ride_labels"]:
        lines.append(
            f"- {ride['date']}: {ride['ride_type_inferred']}; skill {ride['skill_focus_inferred']}; "
            f"load {ride['training_load']}, temp {ride['temperature_c']}"
        )
    lines.extend(["", "Limiter Ranking:"])
    for item in artifact["limiter_ranking_provisional"]:
        lines.append(f"- {item['rank']}. {item['limiter']} ({item['confidence']}): {item['why']}")
    lines.extend(["", "Data Gaps:"])
    lines.extend(f"- {gap}" for gap in artifact["data_gaps"])
    return "\n".join(lines) + "\n"
