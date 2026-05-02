from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from statistics import mean
from typing import Any

from .evidence import as_number, dated_snapshot_files
from .io import read_json, write_json
from .paths import snapshots_dir
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local


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

    return {
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
        "body_battery_current": as_number(stats.get("bodyBatteryMostRecentValue")),
        "body_battery_charge": as_number(stats.get("bodyBatteryChargedValue")),
        "body_battery_drain": as_number(stats.get("bodyBatteryDrainedValue")),
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
        "body_weight": as_number(_nested(body_comp, "totalAverage", "weight")),
    }


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


def build_wellness_trends(
    root: str | Path | None = None,
    for_date: str | date | None = None,
) -> dict:
    rows = build_wellness_daily(root)
    target = parse_date(for_date) or today_local(DEFAULT_TIMEZONE)
    rows = [row for row in rows if parse_date(row.get("date")) and parse_date(row.get("date")) <= target]
    latest = rows[-1] if rows else None
    last_7 = rows[-7:]
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

    trends = {
        "date": target.isoformat(),
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "days_available": len(rows),
        "latest": latest,
        "last_7": {
            "avg_sleep_score": _avg(last_7, "sleep_score"),
            "avg_sleep_hours": _avg(last_7, "sleep_hours"),
            "avg_resting_hr": _avg(last_7, "resting_hr"),
            "avg_overnight_hrv": _avg(last_7, "overnight_hrv"),
            "avg_stress": _avg(last_7, "avg_stress"),
            "avg_body_battery_wake": _avg(last_7, "body_battery_wake"),
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
        },
        "flags": flags,
    }
    write_json(snapshots_dir(root) / "wellness_trends.json", trends)
    return trends
