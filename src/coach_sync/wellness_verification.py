from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .context import load_context
from .evidence import as_number, load_latest_wellness
from .io import write_json, write_text
from .paths import snapshots_dir
from .time_utils import DEFAULT_TIMEZONE, get_zoneinfo, iso_now, parse_date, today_local


POST_WAKE_RECHARGE_THRESHOLD = 5.0


def _payloads_by_label(snapshot: dict) -> dict[str, Any]:
    out = {}
    for payload in snapshot.get("payloads", []):
        if payload.get("ok"):
            out[payload.get("label")] = payload.get("data")
    return out


def _to_epoch_seconds(value: float) -> float:
    return value / 1000.0 if abs(value) > 10_000_000_000 else value


def _parse_datetime(
    value: Any,
    tz_name: str,
    *,
    gmt: bool = False,
    local_epoch: bool = False,
) -> datetime | None:
    if value in (None, ""):
        return None
    tz = get_zoneinfo(tz_name)
    if tz is None:
        tz = datetime.now().astimezone().tzinfo
    numeric = as_number(value)
    numeric_string = isinstance(value, str) and value.strip().lstrip("-").isdigit()
    if numeric is not None and (not isinstance(value, str) or numeric_string):
        seconds = _to_epoch_seconds(numeric)
        if local_epoch:
            return datetime.fromtimestamp(seconds, timezone.utc).replace(tzinfo=tz)
        return datetime.fromtimestamp(seconds, timezone.utc).astimezone(tz)
    if isinstance(value, str):
        raw = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc if gmt else tz)
        return parsed.astimezone(tz)
    return None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="minutes") if value else None


def _rounded(value: float | None, digits: int = 1) -> float | None:
    return round(value, digits) if value is not None else None


def _selected_body_battery_day(payload: Any, target: str | None) -> dict:
    if not isinstance(payload, list):
        return {}
    selected = {}
    for row in payload:
        if not isinstance(row, dict):
            continue
        selected = row
        if target is None or row.get("date") == target:
            break
    return selected


def _body_battery_points(row: dict, tz_name: str) -> list[dict]:
    points = []
    for item in row.get("bodyBatteryValuesArray") or []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        at = _parse_datetime(item[0], tz_name, gmt=True)
        value = as_number(item[1])
        if at is None or value is None:
            continue
        points.append({"at": at, "value": value})
    return sorted(points, key=lambda item: item["at"])


def _public_point(point: dict | None) -> dict | None:
    if not point:
        return None
    return {
        "time_local": _iso(point.get("at")),
        "value": _rounded(point.get("value"), 1),
    }


def _nearest_point(points: list[dict], target: datetime | None) -> dict | None:
    if not points or target is None:
        return None
    return min(points, key=lambda item: abs((item["at"] - target).total_seconds()))


def _sleep_window(sleep: dict, tz_name: str) -> dict:
    dto = sleep.get("dailySleepDTO") if isinstance(sleep, dict) else {}
    dto = dto or {}
    start = _parse_datetime(dto.get("sleepStartTimestampGMT"), tz_name, gmt=True)
    end = _parse_datetime(dto.get("sleepEndTimestampGMT"), tz_name, gmt=True)
    if start is None:
        start = _parse_datetime(
            dto.get("sleepStartTimestampLocal"),
            tz_name,
            local_epoch=True,
        )
    if end is None:
        end = _parse_datetime(
            dto.get("sleepEndTimestampLocal"),
            tz_name,
            local_epoch=True,
        )
    sleep_seconds = as_number(dto.get("sleepTimeSeconds"))
    awake_seconds = as_number(dto.get("awakeSleepSeconds"))
    nap_seconds = as_number(dto.get("napTimeSeconds"))
    window_hours = None
    if start and end:
        window_hours = (end - start).total_seconds() / 3600.0
    return {
        "start_local": _iso(start),
        "end_local": _iso(end),
        "sleep_hours": _rounded(sleep_seconds / 3600.0, 2) if sleep_seconds is not None else None,
        "awake_sleep_hours": _rounded(awake_seconds / 3600.0, 2) if awake_seconds is not None else None,
        "nap_hours_reported": _rounded(nap_seconds / 3600.0, 2) if nap_seconds is not None else None,
        "window_hours": _rounded(window_hours, 2),
        "_end": end,
    }


def _point_summary(points: list[dict]) -> dict:
    if not points:
        return {"points": 0}
    latest = points[-1]
    highest = max(points, key=lambda item: item["value"])
    lowest = min(points, key=lambda item: item["value"])
    return {
        "points": len(points),
        "first": _public_point(points[0]),
        "latest": _public_point(latest),
        "highest": _public_point(highest),
        "lowest": _public_point(lowest),
    }


def verify_wellness_payload(
    snapshot: dict,
    for_date: str | date | None = None,
    tz_name: str = DEFAULT_TIMEZONE,
) -> dict:
    target = parse_date(for_date) or parse_date(snapshot.get("date")) or today_local(tz_name)
    target_key = target.isoformat()
    labels = _payloads_by_label(snapshot)
    stats = labels.get("get_stats") or labels.get("get_user_summary") or {}
    sleep = labels.get("get_sleep_data") or {}
    body_battery_row = _selected_body_battery_day(labels.get("get_body_battery"), target_key)
    sleep_info = _sleep_window(sleep, tz_name)
    points = _body_battery_points(body_battery_row, tz_name)
    point_summary = _point_summary(points)

    reported_wake = as_number(stats.get("bodyBatteryAtWakeTime"))
    reported_current = as_number(stats.get("bodyBatteryMostRecentValue"))
    sleep_end = sleep_info.get("_end")
    near_sleep_end = _nearest_point(points, sleep_end)
    post_wake_points = [
        point for point in points if sleep_end is None or point["at"] >= sleep_end
    ]
    peak_after_wake = max(post_wake_points, key=lambda item: item["value"]) if post_wake_points else None

    delta_from_reported = (
        peak_after_wake["value"] - reported_wake
        if peak_after_wake and reported_wake is not None
        else None
    )
    delta_from_series_wake = (
        peak_after_wake["value"] - near_sleep_end["value"]
        if peak_after_wake and near_sleep_end
        else None
    )
    wake_series_delta = (
        near_sleep_end["value"] - reported_wake
        if near_sleep_end and reported_wake is not None
        else None
    )
    post_wake_recharge_detected = any(
        delta is not None and delta >= POST_WAKE_RECHARGE_THRESHOLD
        for delta in (delta_from_reported, delta_from_series_wake)
    )

    recommended_anchor = reported_wake
    recommended_anchor_source = "garmin_reported_wake"
    if recommended_anchor is None and near_sleep_end:
        recommended_anchor = near_sleep_end["value"]
        recommended_anchor_source = "series_near_sleep_end"
    if post_wake_recharge_detected and peak_after_wake:
        recommended_anchor = max(recommended_anchor or peak_after_wake["value"], peak_after_wake["value"])
        recommended_anchor_source = "post_wake_recharge_peak"
    elif recommended_anchor is None and points:
        recommended_anchor = points[-1]["value"]
        recommended_anchor_source = "latest_body_battery_point"

    if not points:
        verification_status = "insufficient_body_battery_series"
        confidence = "low"
    elif post_wake_recharge_detected:
        verification_status = "garmin_sleep_summary_understates_later_recovery"
        confidence = "medium"
    elif near_sleep_end and wake_series_delta is not None and abs(wake_series_delta) <= 3:
        verification_status = "garmin_wake_body_battery_matches_series"
        confidence = "medium"
    else:
        verification_status = "body_battery_series_available"
        confidence = "medium"

    if post_wake_recharge_detected:
        second_sleep_interpretation = (
            "Garmin did not need to log a nap for this to matter; Body Battery shows later "
            "recharge after the reported sleep end, so wake Body Battery alone is incomplete."
        )
    else:
        second_sleep_interpretation = (
            "No meaningful post-wake Body Battery recharge was detected in the available series."
        )

    public_sleep = {key: value for key, value in sleep_info.items() if not key.startswith("_")}
    return {
        "date": target_key,
        "source_date": snapshot.get("date"),
        "timezone": tz_name,
        "verification_status": verification_status,
        "confidence": confidence,
        "reported": {
            "body_battery_wake": _rounded(reported_wake, 1),
            "body_battery_current": _rounded(reported_current, 1),
            "body_battery_highest": _rounded(as_number(stats.get("bodyBatteryHighestValue")), 1),
            "body_battery_lowest": _rounded(as_number(stats.get("bodyBatteryLowestValue")), 1),
            "body_battery_charge": _rounded(as_number(stats.get("bodyBatteryChargedValue")), 1),
            "body_battery_drain": _rounded(as_number(stats.get("bodyBatteryDrainedValue")), 1),
        },
        "sleep_window": public_sleep,
        "body_battery_series": point_summary,
        "body_battery_interpretation": {
            "series_near_reported_sleep_end": _public_point(near_sleep_end),
            "wake_series_delta": _rounded(wake_series_delta, 1),
            "peak_after_reported_wake": _public_point(peak_after_wake),
            "post_wake_recharge": {
                "detected": post_wake_recharge_detected,
                "threshold_points": POST_WAKE_RECHARGE_THRESHOLD,
                "delta_from_reported_wake": _rounded(delta_from_reported, 1),
                "delta_from_series_wake": _rounded(delta_from_series_wake, 1),
            },
            "recommended_morning_anchor": _rounded(recommended_anchor, 1),
            "recommended_anchor_source": recommended_anchor_source,
            "second_sleep_or_rest_interpretation": second_sleep_interpretation,
        },
        "coaching_use": {
            "readiness": (
                "Use recommended_morning_anchor for morning readiness when it is higher than "
                "Garmin's reported wake Body Battery due to verified post-wake recharge."
            ),
            "intraday": (
                "Keep current Body Battery as an intraday limiter if it has since fallen."
            ),
            "sleep_duration": (
                "Do not inflate sleep hours unless Garmin sleep or nap data confirms it."
            ),
        },
    }


def _verification_text(report: dict) -> str:
    reported = report.get("reported") or {}
    sleep = report.get("sleep_window") or {}
    interpretation = report.get("body_battery_interpretation") or {}
    recharge = interpretation.get("post_wake_recharge") or {}
    peak = interpretation.get("peak_after_reported_wake") or {}
    near = interpretation.get("series_near_reported_sleep_end") or {}
    lines = [
        f"Wellness Verification - {report.get('date')}",
        "",
        f"Status: {report.get('verification_status')} ({report.get('confidence')})",
        f"Garmin wake Body Battery: {reported.get('body_battery_wake')}",
        f"Garmin current Body Battery: {reported.get('body_battery_current')}",
        f"Reported sleep: {sleep.get('sleep_hours')}h, window end: {sleep.get('end_local')}",
        f"Series near sleep end: {near.get('value')} at {near.get('time_local')}",
        f"Post-wake peak: {peak.get('value')} at {peak.get('time_local')}",
        f"Post-wake recharge: {recharge.get('detected')} "
        f"(delta from wake: {recharge.get('delta_from_reported_wake')})",
        f"Recommended morning anchor: {interpretation.get('recommended_morning_anchor')} "
        f"from {interpretation.get('recommended_anchor_source')}",
        "",
        interpretation.get("second_sleep_or_rest_interpretation") or "",
    ]
    return "\n".join(lines).strip() + "\n"


def build_wellness_verification(
    root: str | Path | None = None,
    for_date: str | date | None = None,
) -> dict:
    context = load_context(root)
    tz = context.get("athlete", {}).get("timezone", DEFAULT_TIMEZONE)
    target = parse_date(for_date) or today_local(tz)
    wellness_date, wellness = load_latest_wellness(root, target)
    if wellness is None:
        report = {
            "date": target.isoformat(),
            "generated_at": iso_now(tz),
            "timezone": tz,
            "verification_status": "missing_wellness_snapshot",
            "confidence": "low",
            "reported": {},
            "sleep_window": {},
            "body_battery_series": {"points": 0},
            "body_battery_interpretation": {
                "recommended_morning_anchor": None,
                "recommended_anchor_source": "none",
            },
            "coaching_use": {
                "readiness": "No raw Garmin wellness snapshot is available to verify.",
            },
        }
    else:
        report = verify_wellness_payload(wellness, target, tz)
        report["generated_at"] = iso_now(tz)
        report["source_date"] = wellness_date.isoformat() if wellness_date else report.get("source_date")
        report["data_age_days"] = (target - wellness_date).days if wellness_date else None
        if wellness_date and wellness_date != target:
            report["verification_status"] = "stale_wellness_snapshot"
            report["confidence"] = "low"

    out_dir = snapshots_dir(root)
    write_json(out_dir / "wellness_verification.json", report)
    write_json(out_dir / f"wellness_verification_{target.isoformat()}.json", report)
    write_text(out_dir / "wellness_verification.txt", _verification_text(report))
    return report
