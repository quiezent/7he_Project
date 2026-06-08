from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from statistics import mean
from typing import Any

from .evidence import as_number, dated_snapshot_files
from .io import read_json, write_json
from .paths import snapshots_dir
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local
from .wellness_verification import verify_wellness_payload


def _payloads_by_label(snapshot: dict) -> dict[str, Any]:
    out = {}
    for payload in snapshot.get("payloads", []):
        if payload.get("ok"):
            out[payload.get("label")] = payload.get("data")
    return out


def _nested(data: dict | None, *keys: str) -> Any:
    current: Any = data or {}
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _minutes(seconds: Any) -> float | None:
    value = as_number(seconds)
    return round(value / 60, 1) if value is not None else None


def _hours(seconds: Any) -> float | None:
    value = as_number(seconds)
    return round(value / 3600, 2) if value is not None else None


def _mass_kg(value: Any) -> float | None:
    raw = as_number(value)
    if raw is None:
        return None
    # Garmin Index body-composition mass values arrive in grams.
    kg = raw / 1000 if raw > 300 else raw
    return round(kg, 2)


def _pct(value: Any) -> float | None:
    raw = as_number(value)
    return round(raw, 1) if raw is not None else None


def _body_composition(body_comp: dict | None) -> dict:
    body_comp = body_comp or {}
    rows = [
        item
        for item in body_comp.get("dateWeightList", [])
        if isinstance(item, dict)
    ]
    latest_sample = rows[-1] if rows else {}
    total = body_comp.get("totalAverage") if isinstance(body_comp.get("totalAverage"), dict) else {}

    def pick(key: str) -> Any:
        value = total.get(key)
        return value if value is not None else latest_sample.get(key)

    weight_g = as_number(pick("weight"))
    return {
        "body_weight": weight_g,
        "body_weight_kg": _mass_kg(weight_g),
        "bmi": as_number(pick("bmi")),
        "body_fat_pct": _pct(pick("bodyFat")),
        "body_water_pct": _pct(pick("bodyWater")),
        "muscle_mass_kg": _mass_kg(pick("muscleMass")),
        "bone_mass_kg": _mass_kg(pick("boneMass")),
        "metabolic_age": as_number(pick("metabolicAge")),
        "physique_rating": as_number(pick("physiqueRating")),
        "visceral_fat": as_number(pick("visceralFat")),
        "body_composition_source": latest_sample.get("sourceType"),
        "body_composition_sample_time_gmt": latest_sample.get("timestampGMT"),
    }


def _body_battery_from_endpoint(payload: Any, target_date: str | None) -> dict:
    if not isinstance(payload, list):
        return {}
    selected = None
    for row in payload:
        if not isinstance(row, dict):
            continue
        if target_date is None or row.get("date") == target_date:
            selected = row
    if not selected:
        return {}
    values = selected.get("bodyBatteryValuesArray") or []
    latest_time = None
    latest_value = None
    for item in values:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        value = as_number(item[1])
        if value is None:
            continue
        latest_time = item[0]
        latest_value = value
    return {
        "current": latest_value,
        "charge": as_number(selected.get("charged")),
        "drain": as_number(selected.get("drained")),
        "latest_timestamp": latest_time,
        "start_time_local": selected.get("startTimestampLocal"),
        "end_time_local": selected.get("endTimestampLocal"),
    }


def normalize_wellness_payload(snapshot: dict) -> dict:
    labels = _payloads_by_label(snapshot)
    stats = labels.get("get_stats") or labels.get("get_user_summary") or {}
    sleep = labels.get("get_sleep_data") or {}
    sleep_dto = sleep.get("dailySleepDTO") or {}
    hrv = labels.get("get_hrv_data") or {}
    hrv_summary = hrv.get("hrvSummary") or {}
    body_comp = labels.get("get_body_composition") or {}
    sleep_scores = sleep_dto.get("sleepScores") or {}

    raw_date = snapshot.get("date") or stats.get("calendarDate") or sleep_dto.get("calendarDate")
    body_battery = _body_battery_from_endpoint(labels.get("get_body_battery"), raw_date)
    verification = verify_wellness_payload(snapshot, raw_date, DEFAULT_TIMEZONE)
    body_battery_interpretation = verification.get("body_battery_interpretation") or {}
    body_battery_current = body_battery.get("current")
    body_battery_source = "get_body_battery" if body_battery_current is not None else "daily_summary"
    sleep_seconds = as_number(sleep_dto.get("sleepTimeSeconds"))
    awake_seconds = as_number(sleep_dto.get("awakeSleepSeconds")) or 0
    time_in_bed = sleep_seconds + awake_seconds if sleep_seconds is not None else None
    sleep_efficiency = (
        round((sleep_seconds / time_in_bed) * 100, 1)
        if sleep_seconds is not None and time_in_bed
        else None
    )
    moderate = as_number(stats.get("moderateIntensityMinutes")) or 0
    vigorous = as_number(stats.get("vigorousIntensityMinutes")) or 0
    composition = _body_composition(body_comp)

    row = {
        "date": raw_date,
        "available_payloads": sorted(labels),
        "steps": as_number(stats.get("totalSteps")),
        "step_goal": as_number(stats.get("dailyStepGoal")),
        "active_kcal": as_number(stats.get("activeKilocalories")),
        "bmr_kcal": as_number(stats.get("bmrKilocalories")),
        "wellness_kcal": as_number(stats.get("wellnessKilocalories")),
        "resting_hr": as_number(stats.get("restingHeartRate") or sleep.get("restingHeartRate")),
        "rhr_7d_avg": as_number(stats.get("lastSevenDaysAvgRestingHeartRate")),
        "min_hr": as_number(stats.get("minHeartRate")),
        "max_hr": as_number(stats.get("maxHeartRate")),
        "avg_stress": as_number(stats.get("averageStressLevel")),
        "max_stress": as_number(stats.get("maxStressLevel")),
        "rest_stress_min": _minutes(stats.get("restStressDuration")),
        "low_stress_min": _minutes(stats.get("lowStressDuration")),
        "medium_stress_min": _minutes(stats.get("mediumStressDuration")),
        "high_stress_min": _minutes(stats.get("highStressDuration")),
        "body_battery_wake": as_number(stats.get("bodyBatteryAtWakeTime")),
        "body_battery_current": body_battery_current
        if body_battery_current is not None
        else as_number(stats.get("bodyBatteryMostRecentValue")),
        "body_battery_charge": body_battery.get("charge")
        if body_battery.get("charge") is not None
        else as_number(stats.get("bodyBatteryChargedValue")),
        "body_battery_drain": body_battery.get("drain")
        if body_battery.get("drain") is not None
        else as_number(stats.get("bodyBatteryDrainedValue")),
        "body_battery_source": body_battery_source,
        "body_battery_latest_timestamp": body_battery.get("latest_timestamp"),
        "body_battery_start_time_local": body_battery.get("start_time_local"),
        "body_battery_end_time_local": body_battery.get("end_time_local"),
        "body_battery_verified_morning_anchor": as_number(
            body_battery_interpretation.get("recommended_morning_anchor")
        ),
        "body_battery_verified_anchor_source": body_battery_interpretation.get(
            "recommended_anchor_source"
        ),
        "body_battery_post_wake_recharge": (
            body_battery_interpretation.get("post_wake_recharge") or {}
        ).get("detected"),
        "body_battery_verification_status": verification.get("verification_status"),
        "body_battery_verification_confidence": verification.get("confidence"),
        "sleep_score": as_number(_nested(sleep_scores, "overall", "value")),
        "sleep_quality": _nested(sleep_scores, "overall", "qualifierKey"),
        "sleep_hours": _hours(sleep_seconds),
        "sleep_efficiency_pct": sleep_efficiency,
        "deep_sleep_hours": _hours(sleep_dto.get("deepSleepSeconds")),
        "light_sleep_hours": _hours(sleep_dto.get("lightSleepSeconds")),
        "rem_sleep_hours": _hours(sleep_dto.get("remSleepSeconds")),
        "awake_sleep_hours": _hours(sleep_dto.get("awakeSleepSeconds")),
        "sleep_stress": as_number(sleep_dto.get("avgSleepStress")),
        "restless_moments": as_number(sleep.get("restlessMomentsCount")),
        "overnight_hrv": as_number(sleep.get("avgOvernightHrv") or hrv_summary.get("lastNightAvg")),
        "hrv_status": sleep.get("hrvStatus") or hrv_summary.get("status"),
        "hrv_weekly_avg": as_number(hrv_summary.get("weeklyAvg")),
        "hrv_balanced_low": as_number(_nested(hrv_summary, "baseline", "balancedLow")),
        "hrv_balanced_upper": as_number(_nested(hrv_summary, "baseline", "balancedUpper")),
        "avg_spo2": as_number(stats.get("averageSpo2") or sleep_dto.get("averageSpO2Value")),
        "sleep_spo2": as_number(sleep_dto.get("averageSpO2Value")),
        "avg_respiration": as_number(
            stats.get("avgWakingRespirationValue") or sleep_dto.get("averageRespirationValue")
        ),
        "moderate_intensity_min": moderate,
        "vigorous_intensity_min": vigorous,
        "weighted_intensity_min": moderate + 2 * vigorous,
        "intensity_goal_min": as_number(stats.get("intensityMinutesGoal")),
    }
    row.update(composition)
    return row


def normalize_wellness_snapshot(path: Path) -> dict:
    return normalize_wellness_payload(read_json(path, {}))


def build_wellness_daily(root: str | Path | None = None) -> list[dict]:
    rows = [
        normalize_wellness_snapshot(path)
        for _, path in dated_snapshot_files(root, "garmin_wellness")
    ]
    rows = sorted(rows, key=lambda item: item.get("date") or "")
    for row in rows:
        if row.get("date"):
            write_json(snapshots_dir(root) / f"garmin_daily_recovery_{row['date']}.json", row)
    write_json(snapshots_dir(root) / "wellness_daily.json", rows)
    return rows


def _avg(rows: list[dict], key: str) -> float | None:
    values = [as_number(row.get(key)) for row in rows]
    values = [value for value in values if value is not None]
    return round(mean(values), 2) if values else None


def _sum(rows: list[dict], key: str) -> float:
    return round(sum(as_number(row.get(key)) or 0 for row in rows), 1)


def _trend(rows: list[dict], key: str, days: int = 7) -> dict:
    current = rows[-days:]
    previous = rows[-2 * days : -days]
    current_avg = _avg(current, key)
    previous_avg = _avg(previous, key)
    delta = (
        round(current_avg - previous_avg, 2)
        if current_avg is not None and previous_avg is not None
        else None
    )
    return {"current_avg": current_avg, "previous_avg": previous_avg, "delta": delta}


def _latest_body_composition(rows: list[dict], target: date) -> dict | None:
    for row in reversed(rows):
        body_weight_kg = as_number(row.get("body_weight_kg"))
        if body_weight_kg is None:
            continue
        row_date = parse_date(row.get("date"))
        if not row_date:
            continue
        return {
            "date": row_date.isoformat(),
            "age_days": (target - row_date).days,
            "body_weight_kg": body_weight_kg,
            "bmi": as_number(row.get("bmi")),
            "body_fat_pct": as_number(row.get("body_fat_pct")),
            "body_water_pct": as_number(row.get("body_water_pct")),
            "muscle_mass_kg": as_number(row.get("muscle_mass_kg")),
            "bone_mass_kg": as_number(row.get("bone_mass_kg")),
            "metabolic_age": as_number(row.get("metabolic_age")),
            "physique_rating": as_number(row.get("physique_rating")),
            "visceral_fat": as_number(row.get("visceral_fat")),
            "source": row.get("body_composition_source") or "garmin_body_composition",
            "sample_time_gmt": row.get("body_composition_sample_time_gmt"),
        }
    return None


def build_wellness_trends(
    root: str | Path | None = None,
    for_date: str | date | None = None,
) -> dict:
    rows = build_wellness_daily(root)
    target = parse_date(for_date) or today_local(DEFAULT_TIMEZONE)
    rows = [row for row in rows if parse_date(row.get("date")) and parse_date(row.get("date")) <= target]
    latest = rows[-1] if rows else None
    last_7 = rows[-7:]
    latest_composition = _latest_body_composition(rows, target)
    flags = []
    if latest:
        if (latest.get("body_battery_current") or 100) < 35:
            flags.append(
                {
                    "type": "low_body_battery",
                    "message": "Current Body Battery is low enough to temper hard-session confidence.",
                }
            )
        if (latest.get("sleep_score") or 100) < 60:
            flags.append({"type": "low_sleep", "message": "Sleep score is below 60."})
        hrv_status = str(latest.get("hrv_status") or "").lower()
        if any(term in hrv_status for term in ("low", "poor", "unbalanced")):
            flags.append({"type": "hrv_status", "message": f"HRV status is {latest.get('hrv_status')}."})
    if latest_composition and latest_composition.get("age_days", 0) > 14:
        flags.append(
            {
                "type": "body_composition_stale",
                "message": f"Latest Garmin scale body-composition sample is {latest_composition['age_days']} day(s) old.",
            }
        )

    trends = {
        "date": target.isoformat(),
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "days_available": len(rows),
        "latest": latest,
        "latest_body_composition": latest_composition,
        "last_7": {
            "avg_sleep_score": _avg(last_7, "sleep_score"),
            "avg_sleep_hours": _avg(last_7, "sleep_hours"),
            "avg_resting_hr": _avg(last_7, "resting_hr"),
            "avg_overnight_hrv": _avg(last_7, "overnight_hrv"),
            "avg_stress": _avg(last_7, "avg_stress"),
            "avg_body_battery_wake": _avg(last_7, "body_battery_wake"),
            "avg_body_weight_kg": _avg(last_7, "body_weight_kg"),
            "weighted_intensity_min": _sum(last_7, "weighted_intensity_min"),
            "steps": _sum(last_7, "steps"),
        },
        "trends_7_vs_prior_7": {
            "sleep_score": _trend(rows, "sleep_score"),
            "sleep_hours": _trend(rows, "sleep_hours"),
            "resting_hr": _trend(rows, "resting_hr"),
            "overnight_hrv": _trend(rows, "overnight_hrv"),
            "avg_stress": _trend(rows, "avg_stress"),
            "body_battery_wake": _trend(rows, "body_battery_wake"),
            "body_weight_kg": _trend(rows, "body_weight_kg"),
        },
        "flags": flags,
    }
    write_json(snapshots_dir(root) / "wellness_trends.json", trends)
    return trends
