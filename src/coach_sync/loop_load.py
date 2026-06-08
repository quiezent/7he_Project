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
ZONE_WEIGHTS = {
    0: 0.35,
    1: 0.70,
    2: 1.10,
    3: 1.70,
    4: 2.60,
    5: 3.60,
}


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
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "activity_id": str(activity_id),
        "auth": auth,
        "calls": calls,
    }
    write_json(snapshots_dir(root) / f"activity_detail_{activity_id}.json", artifact)
    return artifact


def _load_activity_detail(root: str | Path | None, activity_id: str, fetch_live: bool) -> dict[str, Any]:
    path = snapshots_dir(root) / f"activity_detail_{activity_id}.json"
    existing = read_json(path, None)
    if existing:
        return existing
    if not fetch_live:
        return {
            "status": "failed",
            "reason": f"Missing {path}; rerun with live fetch enabled.",
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
