from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .backfill import _login
from .garmin_sync import _safe_call
from .io import read_json, write_json
from .paths import activities_dir, snapshots_dir
from .time_utils import DEFAULT_TIMEZONE, iso_now


DEFAULT_ZONE_BOUNDARIES = {
    1: 118,
    2: 132,
    3: 146,
    4: 160,
    5: 174,
}
PEDALING_POWER_W = 40.0
PEDALING_CADENCE_RPM = 20.0
CLIMB_GRADE_PCT = 3.0
DESCENT_GRADE_PCT = -3.0
ZONE_WEIGHTS = {
    0: 0.35,
    1: 0.70,
    2: 1.10,
    3: 1.70,
    4: 2.60,
    5: 3.60,
}


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_lap_groups(values: list[str] | None) -> list[list[int]]:
    groups: list[list[int]] = []
    for value in values or []:
        laps = [int(part.strip()) for part in value.split(",") if part.strip()]
        if laps:
            groups.append(laps)
    return groups


def _parse_gmt_millis(value: str) -> float:
    if value.endswith(".0"):
        fmt = "%Y-%m-%dT%H:%M:%S.0"
    else:
        fmt = "%Y-%m-%dT%H:%M:%S"
    return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc).timestamp() * 1000.0


def _activity_summary(summary: dict[str, Any], activity_detail: dict[str, Any]) -> dict[str, Any]:
    detail_summary = ((activity_detail.get("calls") or {}).get("activity") or {}).get("data") or {}
    summary_dto = detail_summary.get("summaryDTO") or {}
    return {
        "activity_id": summary.get("activityId") or summary_dto.get("activityId"),
        "activity_name": summary.get("activityName") or detail_summary.get("activityName"),
        "activity_type": ((summary.get("activityType") or {}).get("typeKey"))
        or ((detail_summary.get("activityTypeDTO") or {}).get("typeKey")),
        "start_time_local": summary.get("startTimeLocal") or summary_dto.get("startTimeLocal"),
        "duration_sec": summary.get("duration") or summary_dto.get("duration"),
        "moving_duration_sec": summary.get("movingDuration") or summary_dto.get("movingDuration"),
        "distance_m": summary.get("distance") or summary_dto.get("distance"),
        "elevation_gain_m": summary.get("elevationGain") or summary_dto.get("elevationGain"),
        "average_hr": summary.get("averageHR") or summary_dto.get("averageHR"),
        "max_hr": summary.get("maxHR") or summary_dto.get("maxHR"),
        "activity_training_load": summary.get("activityTrainingLoad") or summary_dto.get("activityTrainingLoad"),
        "calories": summary.get("calories") or summary_dto.get("calories"),
    }


def _fetch_activity_detail(root: str | Path | None, activity_id: str) -> dict[str, Any]:
    client, auth = _login()
    if not client:
        return {
            "status": "failed",
            "reason": auth.get("reason", "Garmin login failed."),
            "auth": auth,
        }

    calls: dict[str, dict[str, Any]] = {}
    for label, method_name in (
        ("splits", "get_activity_splits"),
        ("details", "get_activity_details"),
        ("hr_zones", "get_activity_hr_in_timezones"),
        ("weather", "get_activity_weather"),
        ("activity", "get_activity"),
    ):
        method = getattr(client, method_name, None)
        if not method:
            calls[label] = {"label": method_name, "ok": False, "error": "missing_method"}
            continue
        calls[label] = _safe_call(method_name, method, activity_id)

    artifact = {
        "artifact_type": "raw_garmin_key_activity_detail",
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "fetched_at": iso_now(DEFAULT_TIMEZONE),
        "activity_id": str(activity_id),
        "auth": auth,
        "privacy": "raw_private_local_only",
        "calls": calls,
    }
    write_json(
        activities_dir(root) / "details" / f"garmin_{activity_id}_detail.json",
        artifact,
    )
    return artifact


def _load_activity_detail(root: str | Path | None, activity_id: str, fetch_live: bool) -> dict[str, Any]:
    paths = (
        activities_dir(root) / "details" / f"garmin_{activity_id}_detail.json",
        snapshots_dir(root) / f"activity_detail_{activity_id}.json",
    )
    for path in paths:
        existing = read_json(path, None)
        if existing:
            return existing
    if not fetch_live:
        return {
            "status": "failed",
            "reason": (
                "Missing preserved or legacy Garmin activity detail; "
                "rerun with live fetch enabled."
            ),
        }
    return _fetch_activity_detail(root, activity_id)


def _zone_boundaries(hr_zone_payload: Any) -> dict[int, float]:
    boundaries = dict(DEFAULT_ZONE_BOUNDARIES)
    if isinstance(hr_zone_payload, list):
        for row in hr_zone_payload:
            try:
                boundaries[int(row["zoneNumber"])] = float(row["zoneLowBoundary"])
            except (KeyError, TypeError, ValueError):
                continue
    return boundaries


def _zone_for_hr(hr: float | None, boundaries: dict[int, float]) -> int:
    if hr is None:
        return 0
    if hr < boundaries[1]:
        return 0
    if hr < boundaries[2]:
        return 1
    if hr < boundaries[3]:
        return 2
    if hr < boundaries[4]:
        return 3
    if hr < boundaries[5]:
        return 4
    return 5


def _continuous_hr_score(hr: float | None, seconds: float, boundaries: dict[int, float]) -> float:
    if hr is None or seconds <= 0:
        return 0.0
    zone = _zone_for_hr(hr, boundaries)
    if zone >= 5:
        next_weight = ZONE_WEIGHTS[5] + 0.7
        span = max(1.0, boundaries[5] - boundaries[4])
        fraction = min(1.0, max(0.0, (hr - boundaries[5]) / span))
        weight = ZONE_WEIGHTS[5] + (next_weight - ZONE_WEIGHTS[5]) * fraction
    else:
        low = boundaries.get(zone, 0.0) if zone else 0.0
        high = boundaries.get(zone + 1, boundaries[1])
        span = max(1.0, high - low)
        fraction = min(1.0, max(0.0, (hr - low) / span))
        weight = ZONE_WEIGHTS[zone] + (ZONE_WEIGHTS[zone + 1] - ZONE_WEIGHTS[zone]) * fraction
    return seconds / 60.0 * weight


def _zone_weighted_score(hr: float | None, seconds: float, boundaries: dict[int, float]) -> float:
    if seconds <= 0:
        return 0.0
    return seconds / 60.0 * ZONE_WEIGHTS[_zone_for_hr(hr, boundaries)]


def _trimp_score(hr: float | None, seconds: float, resting_hr: float = 45.0, max_hr: float = 185.0) -> float:
    if hr is None or seconds <= 0:
        return 0.0
    hrr = min(1.0, max(0.0, (hr - resting_hr) / max(1.0, max_hr - resting_hr)))
    return seconds / 60.0 * hrr * math.exp(1.92 * hrr)


def _metric_rows(detail_payload: dict[str, Any]) -> list[dict[str, Any]]:
    descriptors = {
        row["key"]: row["metricsIndex"]
        for row in detail_payload.get("metricDescriptors", [])
        if "key" in row and "metricsIndex" in row
    }
    rows: list[dict[str, Any]] = []
    for raw in detail_payload.get("activityDetailMetrics", []):
        metrics = raw.get("metrics") or []
        row = {key: metrics[index] if index < len(metrics) else None for key, index in descriptors.items()}
        if row.get("directTimestamp") is not None:
            rows.append(row)
    rows.sort(key=lambda row: row["directTimestamp"])
    return rows


def _lap_intervals(laps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    intervals = []
    for lap in laps:
        start = _parse_gmt_millis(lap["startTimeGMT"])
        end = start + float(lap.get("elapsedDuration") or lap.get("duration") or 0.0) * 1000.0
        intervals.append({"lap": lap, "start": start, "end": end})
    return intervals


def _kind_for_lap(lap: dict[str, Any]) -> str:
    gain = float(lap.get("elevationGain") or 0.0)
    loss = float(lap.get("elevationLoss") or 0.0)
    if gain >= 50.0 and gain > loss:
        return "climb"
    if loss >= 50.0 and loss > gain:
        return "descent"
    return "transition"


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _weighted_average(total: float, seconds: float) -> float | None:
    if seconds <= 0:
        return None
    return total / seconds


def _segment_is_moving(row: dict[str, Any], next_row: dict[str, Any] | None) -> bool | None:
    speed = _as_float(row.get("directSpeed"))
    if speed is not None and speed >= 0.5:
        return True

    distance = _as_float(row.get("sumDistance"))
    next_distance = _as_float(next_row.get("sumDistance")) if next_row else None
    if distance is not None and next_distance is not None:
        if next_distance - distance >= 1.0:
            return True
        return False

    if speed is not None:
        return False
    return None


def _classify_action_terrain(moving: bool | None, power: float | None, cadence: float | None, grade_pct: float | None) -> str:
    if moving is False:
        return "stopped"
    if moving is None:
        return "unknown"

    pedaling = (power is not None and power >= PEDALING_POWER_W) or (
        cadence is not None and cadence >= PEDALING_CADENCE_RPM
    )
    if grade_pct is None:
        return "pedaling_unknown_grade" if pedaling else "coasting_unknown_grade"
    if grade_pct >= CLIMB_GRADE_PCT:
        return "punchy_climb_pedaling" if pedaling else "uphill_low_power"
    if grade_pct <= DESCENT_GRADE_PCT:
        return "downhill_pedaling" if pedaling else "downhill_coasting"
    return "flat_pedaling" if pedaling else "flat_coasting"


def _empty_action_bucket() -> dict[str, Any]:
    return {
        "seconds": 0.0,
        "distance_m": 0.0,
        "elevation_delta_m": 0.0,
        "hr_weight": 0.0,
        "hr_seconds": 0.0,
        "power_weight": 0.0,
        "power_seconds": 0.0,
        "max_power": None,
    }


def _summarize_action_terrain(segments: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, dict[str, Any]] = {}
    pedaling_seconds = 0.0
    coasting_seconds = 0.0

    for item in segments:
        category = str(item.get("action_terrain") or "unknown")
        bucket = buckets.setdefault(category, _empty_action_bucket())
        seconds = float(item.get("duration_s") or 0.0)
        distance = float(item.get("distance_delta_m") or 0.0)
        elevation = float(item.get("elevation_delta_m") or 0.0)
        bucket["seconds"] += seconds
        bucket["distance_m"] += max(0.0, distance)
        bucket["elevation_delta_m"] += elevation

        hr = item.get("hr")
        if hr is not None:
            bucket["hr_weight"] += float(hr) * seconds
            bucket["hr_seconds"] += seconds
        power = item.get("power")
        if power is not None:
            bucket["power_weight"] += float(power) * seconds
            bucket["power_seconds"] += seconds
            bucket["max_power"] = float(power) if bucket["max_power"] is None else max(float(power), bucket["max_power"])

        if "pedaling" in category:
            pedaling_seconds += seconds
        elif "coasting" in category or category in {"uphill_low_power", "stopped"}:
            coasting_seconds += seconds

    sections = {}
    for category, bucket in sorted(buckets.items()):
        avg_hr = _weighted_average(bucket["hr_weight"], bucket["hr_seconds"])
        avg_power = _weighted_average(bucket["power_weight"], bucket["power_seconds"])
        grade = _safe_ratio(bucket["elevation_delta_m"] * 100.0, bucket["distance_m"])
        speed = _safe_ratio(bucket["distance_m"], bucket["seconds"])
        sections[category] = {
            "duration_s": round(bucket["seconds"], 1),
            "distance_m": round(bucket["distance_m"], 1),
            "avg_hr": round(avg_hr, 1) if avg_hr is not None else None,
            "avg_power": round(avg_power, 1) if avg_power is not None else None,
            "max_power": round(bucket["max_power"], 1) if bucket["max_power"] is not None else None,
            "elevation_delta_m": round(bucket["elevation_delta_m"], 1),
            "avg_grade_pct": round(grade, 1) if grade is not None else None,
            "avg_speed_kmh": round(speed * 3.6, 1) if speed is not None else None,
        }

    active_sections = {
        key: value
        for key, value in sections.items()
        if key != "stopped" and value.get("duration_s", 0.0) > 0
    }
    dominant_active_category = None
    if active_sections:
        dominant_active_category = max(active_sections.items(), key=lambda item: item[1]["duration_s"])[0]

    return {
        "thresholds": {
            "pedaling_power_w": PEDALING_POWER_W,
            "pedaling_cadence_rpm": PEDALING_CADENCE_RPM,
            "climb_grade_pct": CLIMB_GRADE_PCT,
            "descent_grade_pct": DESCENT_GRADE_PCT,
        },
        "pedaling_sec": round(pedaling_seconds, 1),
        "coasting_or_stopped_sec": round(coasting_seconds, 1),
        "dominant_active_category": dominant_active_category,
        "sections": sections,
    }


def _summarize_run(run: dict[str, Any]) -> dict[str, Any]:
    hr_avg = _weighted_average(run["hr_weight"], run["hr_seconds"])
    power_avg = _weighted_average(run["power_weight"], run["power_seconds"])
    return {
        "start_offset_s": round(run["start_offset_s"], 1),
        "end_offset_s": round(run["end_offset_s"], 1),
        "duration_s": round(run["duration_s"], 1),
        "avg_hr": round(hr_avg, 1) if hr_avg is not None else None,
        "start_hr": run.get("start_hr"),
        "end_hr": run.get("end_hr"),
        "max_hr": run.get("max_hr"),
        "avg_power": round(power_avg, 1) if power_avg is not None else None,
        "max_power": run.get("max_power"),
    }


def _summarize_lap_timeline(
    rows: list[dict[str, Any]],
    interval: dict[str, Any],
    boundaries: dict[int, float],
) -> dict[str, Any]:
    lap_start = float(interval["start"])
    lap_end = float(interval["end"])
    if lap_end <= lap_start:
        return {"samples": 0, "flags": ["invalid_lap_interval"]}

    segments: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        timestamp = _as_float(row.get("directTimestamp"))
        if timestamp is None:
            continue
        next_row = rows[index + 1] if index < len(rows) - 1 else None
        next_timestamp = _as_float(next_row.get("directTimestamp")) if next_row else lap_end
        if next_timestamp is None:
            next_timestamp = lap_end
        start = max(timestamp, lap_start)
        end = min(next_timestamp, lap_end)
        if end <= start:
            continue
        moving = _segment_is_moving(row, next_row)
        duration_s = (end - start) / 1000.0
        full_duration_s = max(0.0, (next_timestamp - timestamp) / 1000.0)
        scale = duration_s / full_duration_s if full_duration_s > 0 else 0.0
        distance = _as_float(row.get("sumDistance"))
        next_distance = _as_float(next_row.get("sumDistance")) if next_row else None
        elevation = _as_float(row.get("directElevation"))
        next_elevation = _as_float(next_row.get("directElevation")) if next_row else None
        distance_delta_m = None
        elevation_delta_m = None
        if distance is not None and next_distance is not None:
            distance_delta_m = max(0.0, next_distance - distance) * scale
        if elevation is not None and next_elevation is not None:
            elevation_delta_m = (next_elevation - elevation) * scale
        grade_pct = None
        if distance is not None and next_distance is not None and elevation is not None and next_elevation is not None:
            raw_distance_delta = next_distance - distance
            if raw_distance_delta > 1.0:
                grade_pct = (next_elevation - elevation) * 100.0 / raw_distance_delta
        power = _as_float(row.get("directPower"))
        cadence = _as_float(row.get("directBikeCadence"))
        segments.append(
            {
                "offset_s": (start - lap_start) / 1000.0,
                "end_offset_s": (end - lap_start) / 1000.0,
                "duration_s": duration_s,
                "moving": moving,
                "hr": _as_float(row.get("directHeartRate")),
                "power": power,
                "cadence": cadence,
                "distance_delta_m": distance_delta_m,
                "elevation_delta_m": elevation_delta_m,
                "grade_pct": grade_pct,
                "action_terrain": _classify_action_terrain(moving, power, cadence, grade_pct),
            }
        )

    if not segments:
        return {"samples": 0, "flags": ["missing_lap_timeline_samples"]}

    moving_seconds = sum(item["duration_s"] for item in segments if item["moving"] is True)
    stopped_seconds = sum(item["duration_s"] for item in segments if item["moving"] is False)
    unknown_seconds = sum(item["duration_s"] for item in segments if item["moving"] is None)
    hr_segments = [item for item in segments if item["hr"] is not None]
    first_hr = hr_segments[0]["hr"] if hr_segments else None
    last_hr = hr_segments[-1]["hr"] if hr_segments else None
    max_hr = max((item["hr"] for item in hr_segments), default=None)

    moving_hr_weight = sum(item["hr"] * item["duration_s"] for item in segments if item["moving"] is True and item["hr"] is not None)
    moving_hr_seconds = sum(item["duration_s"] for item in segments if item["moving"] is True and item["hr"] is not None)
    stopped_hr_weight = sum(item["hr"] * item["duration_s"] for item in segments if item["moving"] is False and item["hr"] is not None)
    stopped_hr_seconds = sum(item["duration_s"] for item in segments if item["moving"] is False and item["hr"] is not None)

    runs: list[dict[str, Any]] = []
    for item in segments:
        if item["moving"] is None:
            continue
        if not runs or runs[-1]["moving"] != item["moving"]:
            runs.append(
                {
                    "moving": item["moving"],
                    "start_offset_s": item["offset_s"],
                    "end_offset_s": item["end_offset_s"],
                    "duration_s": item["duration_s"],
                    "hr_weight": 0.0,
                    "hr_seconds": 0.0,
                    "power_weight": 0.0,
                    "power_seconds": 0.0,
                    "start_hr": item["hr"],
                    "end_hr": item["hr"],
                    "max_hr": item["hr"],
                    "max_power": item["power"],
                }
            )
        else:
            runs[-1]["end_offset_s"] = item["end_offset_s"]
            runs[-1]["duration_s"] += item["duration_s"]
            runs[-1]["end_hr"] = item["hr"] if item["hr"] is not None else runs[-1]["end_hr"]
            if item["hr"] is not None:
                current_max_hr = runs[-1].get("max_hr")
                runs[-1]["max_hr"] = item["hr"] if current_max_hr is None else max(current_max_hr, item["hr"])
            if item["power"] is not None:
                current_max_power = runs[-1].get("max_power")
                runs[-1]["max_power"] = item["power"] if current_max_power is None else max(current_max_power, item["power"])

        if item["hr"] is not None:
            runs[-1]["hr_weight"] += item["hr"] * item["duration_s"]
            runs[-1]["hr_seconds"] += item["duration_s"]
        if item["power"] is not None:
            runs[-1]["power_weight"] += item["power"] * item["duration_s"]
            runs[-1]["power_seconds"] += item["duration_s"]

    stop_runs = [run for run in runs if run["moving"] is False]
    longest_stop = max(stop_runs, key=lambda row: row["duration_s"], default=None)
    moving_after_longest_stop = None
    if longest_stop:
        for run in runs:
            if run["moving"] is True and run["start_offset_s"] >= longest_stop["end_offset_s"]:
                moving_after_longest_stop = run
                break

    flags: list[str] = []
    high_start_boundary = boundaries.get(4, 160.0)
    if longest_stop and first_hr is not None:
        hr_drop = None
        if longest_stop.get("start_hr") is not None and longest_stop.get("end_hr") is not None:
            hr_drop = float(longest_stop["start_hr"]) - float(longest_stop["end_hr"])
        if (
            first_hr >= high_start_boundary
            and longest_stop["start_offset_s"] <= 60.0
            and longest_stop["duration_s"] >= 180.0
            and (hr_drop is None or hr_drop >= 25.0)
        ):
            flags.append("max_hr_likely_boundary_carryover")
    if stopped_seconds >= 180.0:
        flags.append("contains_long_rest")
    if unknown_seconds > 0 and moving_seconds == 0 and stopped_seconds == 0:
        flags.append("movement_timeline_unavailable")

    moving_hr_avg = _weighted_average(moving_hr_weight, moving_hr_seconds)
    stopped_hr_avg = _weighted_average(stopped_hr_weight, stopped_hr_seconds)
    return {
        "samples": len(segments),
        "first_hr": round(first_hr, 1) if first_hr is not None else None,
        "last_hr": round(last_hr, 1) if last_hr is not None else None,
        "max_hr": round(max_hr, 1) if max_hr is not None else None,
        "moving_sec": round(moving_seconds, 1),
        "stopped_sec": round(stopped_seconds, 1),
        "movement_unknown_sec": round(unknown_seconds, 1),
        "moving_avg_hr": round(moving_hr_avg, 1) if moving_hr_avg is not None else None,
        "stopped_avg_hr": round(stopped_hr_avg, 1) if stopped_hr_avg is not None else None,
        "longest_stop": _summarize_run(longest_stop) if longest_stop else None,
        "first_moving_after_longest_stop": _summarize_run(moving_after_longest_stop)
        if moving_after_longest_stop
        else None,
        "action_terrain_summary": _summarize_action_terrain(segments),
        "flags": flags,
    }


def build_loop_load(
    root: str | Path | None,
    activity_id: str,
    lap_groups: list[list[int]],
    date: str | None = None,
    labels: list[str] | None = None,
    fetch_live: bool = True,
) -> dict[str, Any]:
    detail_artifact = _load_activity_detail(root, str(activity_id), fetch_live)
    if detail_artifact.get("status") == "failed":
        return detail_artifact

    calls = detail_artifact.get("calls") or {}
    splits = ((calls.get("splits") or {}).get("data") or {}).get("lapDTOs") or []
    details = (calls.get("details") or {}).get("data") or {}
    hr_zones = (calls.get("hr_zones") or {}).get("data")
    weather = (calls.get("weather") or {}).get("data")
    if not splits:
        return {"status": "failed", "reason": "No Garmin lapDTOs available for this activity."}

    summary = read_json(activities_dir(root) / f"garmin_{activity_id}.json", {}) or {}
    activity = _activity_summary(summary, detail_artifact)
    official_load = activity.get("activity_training_load")
    if official_load is None:
        return {"status": "failed", "reason": "Activity has no official activityTrainingLoad to calibrate against."}

    boundaries = _zone_boundaries(hr_zones)
    intervals = _lap_intervals(splits)
    rows = _metric_rows(details)
    activity_end = max((item["end"] for item in intervals), default=None)

    lap_scores: dict[int, dict[str, float]] = {
        int(item["lap"].get("lapIndex")): {
            "continuous_hr": 0.0,
            "zone_weighted": 0.0,
            "trimp": 0.0,
        }
        for item in intervals
    }
    lap_zone_seconds: dict[int, dict[str, float]] = {
        int(item["lap"].get("lapIndex")): {f"z{zone}": 0.0 for zone in range(0, 6)}
        for item in intervals
    }
    lap_hr_weighted_sum: dict[int, float] = {int(item["lap"].get("lapIndex")): 0.0 for item in intervals}
    lap_hr_seconds: dict[int, float] = {int(item["lap"].get("lapIndex")): 0.0 for item in intervals}

    for index, row in enumerate(rows):
        start = float(row["directTimestamp"])
        if index < len(rows) - 1:
            end = float(rows[index + 1]["directTimestamp"])
        else:
            end = activity_end or start
        if end <= start:
            continue
        hr = row.get("directHeartRate")
        for interval in intervals:
            overlap = max(0.0, min(end, interval["end"]) - max(start, interval["start"])) / 1000.0
            if overlap <= 0:
                continue
            lap_index = int(interval["lap"].get("lapIndex"))
            lap_scores[lap_index]["continuous_hr"] += _continuous_hr_score(hr, overlap, boundaries)
            lap_scores[lap_index]["zone_weighted"] += _zone_weighted_score(hr, overlap, boundaries)
            lap_scores[lap_index]["trimp"] += _trimp_score(hr, overlap)
            lap_zone_seconds[lap_index][f"z{_zone_for_hr(hr, boundaries)}"] += overlap
            if hr is not None:
                lap_hr_weighted_sum[lap_index] += float(hr) * overlap
                lap_hr_seconds[lap_index] += overlap

    model_totals = {
        model: sum(scores[model] for scores in lap_scores.values())
        for model in ("continuous_hr", "zone_weighted", "trimp")
    }
    model_scales = {
        model: float(official_load) / total if total > 0 else 0.0
        for model, total in model_totals.items()
    }
    calories_total = sum(float(lap.get("calories") or 0.0) for lap in splits)

    lap_rows = []
    for lap in splits:
        lap_index = int(lap.get("lapIndex"))
        calories = float(lap.get("calories") or 0.0)
        elapsed_min = float(lap.get("elapsedDuration") or 0.0) / 60.0
        moving_min = float(lap.get("movingDuration") or 0.0) / 60.0
        interval = next((item for item in intervals if int(item["lap"].get("lapIndex")) == lap_index), None)
        lap_rows.append(
            {
                "lap": lap_index,
                "kind": _kind_for_lap(lap),
                "distance_km": round(float(lap.get("distance") or 0.0) / 1000.0, 3),
                "elapsed_min": round(elapsed_min, 2),
                "moving_min": round(moving_min, 2),
                "stop_min": round(max(0.0, elapsed_min - moving_min), 2),
                "elevation_gain_m": round(float(lap.get("elevationGain") or 0.0), 1),
                "elevation_loss_m": round(float(lap.get("elevationLoss") or 0.0), 1),
                "average_hr": lap.get("averageHR"),
                "stream_average_hr": round(
                    lap_hr_weighted_sum[lap_index] / lap_hr_seconds[lap_index],
                    1,
                )
                if lap_hr_seconds[lap_index] > 0
                else None,
                "max_hr": lap.get("maxHR"),
                "calories": calories,
                "average_temperature_c": lap.get("averageTemperature"),
                "grit": lap.get("grit"),
                "flow": lap.get("avgFlow"),
                "average_speed_kmh": round(float(lap.get("averageSpeed") or 0.0) * 3.6, 2),
                "max_speed_kmh": round(float(lap.get("maxSpeed") or 0.0) * 3.6, 2),
                "estimated_load": {
                    "primary_continuous_hr": round(
                        lap_scores[lap_index]["continuous_hr"] * model_scales["continuous_hr"],
                        1,
                    ),
                    "zone_weighted": round(lap_scores[lap_index]["zone_weighted"] * model_scales["zone_weighted"], 1),
                    "trimp": round(lap_scores[lap_index]["trimp"] * model_scales["trimp"], 1),
                    "calorie_scaled": round(float(official_load) * calories / calories_total, 1)
                    if calories_total > 0
                    else None,
                },
                "stream_time_in_zone_sec": {
                    key: round(value, 1)
                    for key, value in lap_zone_seconds[lap_index].items()
                    if value > 0
                },
                "timeline": _summarize_lap_timeline(rows, interval, boundaries) if interval else {"samples": 0},
            }
        )

    by_lap = {row["lap"]: row for row in lap_rows}
    loop_rows = []
    assigned_laps: set[int] = set()
    for index, laps in enumerate(lap_groups, start=1):
        rows_for_loop = [by_lap[lap] for lap in laps if lap in by_lap]
        assigned_laps.update(row["lap"] for row in rows_for_loop)
        elapsed = sum(row["elapsed_min"] for row in rows_for_loop)
        moving = sum(row["moving_min"] for row in rows_for_loop)
        label = labels[index - 1] if labels and index - 1 < len(labels) else f"Loop {index}"
        loop_rows.append(
            {
                "loop": index,
                "label": label,
                "laps": [row["lap"] for row in rows_for_loop],
                "lap_kinds": [row["kind"] for row in rows_for_loop],
                "distance_km": round(sum(row["distance_km"] for row in rows_for_loop), 3),
                "elapsed_min": round(elapsed, 2),
                "moving_min": round(moving, 2),
                "stop_min": round(max(0.0, elapsed - moving), 2),
                "elevation_gain_m": round(sum(row["elevation_gain_m"] for row in rows_for_loop), 1),
                "elevation_loss_m": round(sum(row["elevation_loss_m"] for row in rows_for_loop), 1),
                "average_hr_est": round(
                    sum(float(row.get("average_hr") or 0.0) * row["elapsed_min"] for row in rows_for_loop) / elapsed,
                    1,
                )
                if elapsed > 0
                else None,
                "max_hr": max((row.get("max_hr") or 0) for row in rows_for_loop) if rows_for_loop else None,
                "estimated_load": {
                    "primary_continuous_hr": round(
                        sum(row["estimated_load"]["primary_continuous_hr"] for row in rows_for_loop),
                        1,
                    ),
                    "zone_weighted": round(sum(row["estimated_load"]["zone_weighted"] for row in rows_for_loop), 1),
                    "trimp": round(sum(row["estimated_load"]["trimp"] for row in rows_for_loop), 1),
                    "calorie_scaled": round(
                        sum(row["estimated_load"]["calorie_scaled"] or 0.0 for row in rows_for_loop),
                        1,
                    ),
                },
                "load_per_elapsed_hour": round(
                    60.0
                    * sum(row["estimated_load"]["primary_continuous_hr"] for row in rows_for_loop)
                    / elapsed,
                    1,
                )
                if elapsed > 0
                else None,
            }
        )

    unassigned = [row for row in lap_rows if row["lap"] not in assigned_laps]
    output = {
        "artifact_type": "activity_loop_load",
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "date": date,
        "activity_id": str(activity_id),
        "activity": activity,
        "official_activity_training_load": round(float(official_load), 1),
        "method": {
            "primary_estimate": "primary_continuous_hr",
            "summary": (
                "Garmin does not expose official training load by lap. "
                "This artifact redistributes the official activity training load across manual laps using "
                "Garmin HR samples, elapsed time, and Clayton's Garmin HR-zone boundaries."
            ),
            "zone_boundaries_bpm": boundaries,
            "secondary_checks": ["zone_weighted", "trimp", "calorie_scaled"],
            "model_scales": {key: round(value, 4) for key, value in model_scales.items()},
            "limits": [
                "Downhill arm pump, braking fatigue, impacts, heat skill-cost, and grip confidence are not fully captured by HR-based load.",
                "Loop estimates are best used for repeatability tracking within similar routes and sensor conditions.",
                "Manual lap boundaries can inherit HR from the previous trail; use the timeline flags before interpreting max HR or lap load.",
            ],
        },
        "weather": weather,
        "laps": lap_rows,
        "loops": loop_rows,
        "unassigned_laps": unassigned,
        "sanity_checks": {
            "lap_primary_load_sum": round(sum(row["estimated_load"]["primary_continuous_hr"] for row in lap_rows), 1),
            "official_load": round(float(official_load), 1),
            "primary_sum_to_official_ratio": _safe_ratio(
                round(sum(row["estimated_load"]["primary_continuous_hr"] for row in lap_rows), 1),
                round(float(official_load), 1),
            ),
        },
    }

    dated = snapshots_dir(root) / f"activity_loop_load_{date or 'undated'}_{activity_id}.json"
    current = snapshots_dir(root) / "activity_loop_load_current.json"
    output["artifacts"] = {
        "dated": str(dated.relative_to(Path(root).resolve()) if root else dated),
        "current": str(current.relative_to(Path(root).resolve()) if root else current),
    }
    write_json(dated, output)
    write_json(current, output)
    return output
