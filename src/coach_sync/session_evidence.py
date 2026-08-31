from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
from math import isfinite
from pathlib import Path
from typing import Any

from .device_audit import (
    is_external_standard_source,
    public_recording_device,
    read_standard_fit_device_sources,
    summarize_activity_devices,
)
from .evidence import as_number, counts_for_training_load, load_activities
from .io import read_json
from .paths import repo_root, snapshots_dir
from .time_utils import DEFAULT_TIMEZONE, parse_date, today_local


POWER_CURVE_POINTS = {
    "1s": 1,
    "5s": 5,
    "1min": 60,
    "5min": 300,
    "20min": 1200,
}
DETAIL_TRACE_METRIC_LIMIT = 32
BIKE_SESSION_CATEGORIES = {"mtb", "bike_indoor", "bike_outdoor"}
TRACE_TIMING_TOLERANCE_SECONDS = 60.0
TRACE_TIMING_TOLERANCE_FRACTION = 0.02
MAX_PLAUSIBLE_SESSION_DURATION_SECONDS = 7 * 24 * 60 * 60


def _round(value: Any, digits: int = 1) -> float | None:
    number = as_number(value)
    return round(number, digits) if number is not None else None


def _minutes(value: Any) -> float | None:
    seconds = as_number(value)
    return round(seconds / 60, 1) if seconds is not None else None


def _relative_path(root: str | Path | None, path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(repo_root(root)).as_posix()
    except (OSError, ValueError):
        return path.name


def _activity_start_key(activity: dict, raw: dict) -> tuple[str, float, str]:
    local_start = raw.get("startTimeLocal") or raw.get("start_time_local")
    if local_start:
        start_text = str(local_start).strip().replace(" ", "T")
    else:
        start_text = f"{activity.get('date') or ''}T00:00:00"
    begin_timestamp = as_number(raw.get("beginTimestamp")) or 0.0
    return start_text, begin_timestamp, str(activity.get("id") or "")


def _session_category(activity: dict) -> str | None:
    activity_type = str(activity.get("type") or "").lower()
    if "hiking" in activity_type:
        return "hike"
    return activity.get("category")


def _latest_training_activity(
    root: str | Path | None,
    target: date,
    activities: list[dict] | None,
) -> tuple[dict, dict, Path] | None:
    rows = activities if activities is not None else load_activities(root)
    eligible = []
    for activity in rows:
        activity_day = parse_date(activity.get("date"))
        if activity_day is None or activity_day > target or not counts_for_training_load(activity):
            continue
        eligible.append((activity_day, activity))
    if not eligible:
        return None

    latest_day = max(item[0] for item in eligible)
    candidates = []
    for _, activity in eligible:
        if parse_date(activity.get("date")) != latest_day:
            continue
        source_path = Path(activity.get("source_file") or "")
        if not source_path.is_file():
            continue
        raw = read_json(source_path, {})
        if isinstance(raw, dict) and raw:
            candidates.append((_activity_start_key(activity, raw), activity, raw, source_path))
    if not candidates:
        return None
    _, activity, raw, source_path = max(candidates, key=lambda item: item[0])
    return activity, raw, source_path


def _index_rows(root: str | Path | None, filename: str) -> list[dict]:
    payload = read_json(snapshots_dir(root) / filename, {})
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("activities") or []
    else:
        rows = []
    return [row for row in rows if isinstance(row, dict)]


def _matching_index_row(root: str | Path | None, filename: str, activity_id: str) -> dict | None:
    for row in reversed(_index_rows(root, filename)):
        if str(row.get("activity_id") or row.get("id") or "") == activity_id:
            return row
    return None


def _cached_after_refresh_failure(row: dict) -> bool:
    attempt = row.get("latest_attempt") if isinstance(row.get("latest_attempt"), dict) else {}
    return row.get("last_attempt_ok") is False and attempt.get("status") in {
        "failed",
        "unsupported",
        "success_empty",
    }


def _gear_evidence(root: str | Path | None, activity_id: str) -> dict:
    row = _matching_index_row(root, "activity_gear_index.json", activity_id)
    source = "snapshots/activity_gear_index.json"
    if row is None:
        return {"status": "not_indexed", "labels": [], "source": source}
    if row.get("gear_fetch_ok") is not True:
        return {
            "status": "fetch_failed" if row.get("gear_fetch_ok") is False else "unknown",
            "labels": [],
            "source": source,
            "fetch_error": row.get("gear_fetch_error"),
        }
    gear = row.get("gear") if isinstance(row.get("gear"), list) else []
    labels = [item.get("label") for item in gear if isinstance(item, dict) and item.get("label")]
    return {
        "status": (
            "available_cached_after_refresh_failure"
            if _cached_after_refresh_failure(row)
            else "available"
            if labels
            else "no_gear_linked"
        ),
        "labels": labels,
        "latest_attempt": row.get("latest_attempt"),
        "source": source,
    }


def _original_fit_path(root: str | Path | None, activity_id: str) -> Path | None:
    base = repo_root(root) / "activities" / "fit"
    for suffix in (".fit", ".zip"):
        candidate = base / f"garmin_{activity_id}_original{suffix}"
        if candidate.is_file():
            return candidate
    return None


def _device_hr_confidence(summary: dict, fit_sources: dict | None = None) -> str:
    external_hr = summary.get("external_hr_sensor") is True
    if external_hr:
        statuses = [str(item).upper() for item in summary.get("external_hr_battery_statuses") or []]
        return "external_hr_low_battery" if "LOW" in statuses else "external_hr"
    if summary.get("local_or_onboard_hr_sensor") is True:
        return "local_or_onboard_hr"
    if fit_sources and fit_sources.get("local_or_onboard_only") is True:
        return "wrist_or_onboard_likely"
    if fit_sources and fit_sources.get("external_device_source_present") is True:
        return "external_device_present_hr_source_unresolved"
    if summary.get("hr_source_classification") == "unknown_standard_metadata_source":
        return "unknown_standard_metadata_source"
    return "unknown_standard_metadata_no_hr_source"


def _normalized_device_summary(
    summary: dict,
    *,
    status: str,
    source: str,
    fit_sources: dict | None = None,
    fit_source_path: str | None = None,
    latest_attempt: dict | None = None,
) -> dict:
    sensors = summary.get("sensors") if isinstance(summary.get("sensors"), list) else []
    sensor_types = sorted(
        {
            str(item.get("sensor_type"))
            for item in sensors
            if isinstance(item, dict) and item.get("sensor_type")
        }
        | {
            str(item)
            for item in (fit_sources or {}).get("sensor_types") or []
            if item
        }
    )
    external_speed_sensor_rows = [
        item
        for item in sensors
        if isinstance(item, dict)
        and item.get("sensor_type") == "BIKE_SPEED"
        and is_external_standard_source(item.get("source_type"))
    ]
    metadata_external_speed_sensor = bool(external_speed_sensor_rows)
    fit_external_speed_sensor = bool(
        fit_sources and fit_sources.get("external_speed_sensor") is True
    )
    external_speed_sensor = metadata_external_speed_sensor or fit_external_speed_sensor
    external_power_sensor_rows = [
        item
        for item in sensors
        if isinstance(item, dict)
        and item.get("sensor_type") == "BIKE_POWER"
        and is_external_standard_source(item.get("source_type"))
    ]
    fit_external_power_sensor = "BIKE_POWER" in (
        (fit_sources or {}).get("external_sensor_types") or []
    )
    external_power_sensor = bool(external_power_sensor_rows) or fit_external_power_sensor
    speed_battery_statuses = sorted(
        {
            str(value).upper()
            for value in (
                [row.get("battery_status") for row in external_speed_sensor_rows]
                + list(
                    (fit_sources or {}).get(
                        "external_speed_sensor_battery_statuses"
                    )
                    or []
                )
            )
            if value
        }
    )
    return {
        "status": status,
        "recording_device": public_recording_device(summary.get("recording_device")),
        "sensor_types": sensor_types,
        "external_hr_sensor": summary.get("external_hr_sensor") is True,
        "local_or_onboard_hr_sensor": summary.get("local_or_onboard_hr_sensor") is True,
        "external_hr_battery_statuses": [
            str(item).upper() for item in summary.get("external_hr_battery_statuses") or []
        ],
        "speed_measurement": {
            "status": (
                "external_bike_speed_sensor_in_standard_metadata"
                if metadata_external_speed_sensor
                else "external_bike_speed_sensor_in_preserved_standard_fit"
                if fit_external_speed_sensor
                else "no_external_bike_speed_sensor_in_standard_metadata"
            ),
            "external_speed_sensor": external_speed_sensor,
            "source_surface": (
                "garmin_devices_and_apps_standard_metadata"
                if metadata_external_speed_sensor
                else "preserved_standard_fit_device_info"
                if fit_external_speed_sensor
                else None
            ),
            "battery_statuses": speed_battery_statuses,
            "ontology_entity": "measurement_provenance",
            "interpretation_guardrail": (
                "Standard metadata establishes that a BIKE_SPEED sensor contributed to the activity "
                "device context. It improves speed/distance confidence under canopy but does not prove "
                "wheel location, calibration, wheel circumference, per-sample accuracy, route identity, "
                "technical execution, or manual lap-boundary accuracy."
            ),
        },
        "power_measurement": {
            "status": (
                "external_bike_power_sensor_in_standard_metadata"
                if external_power_sensor_rows
                else "external_bike_power_sensor_in_preserved_standard_fit"
                if fit_external_power_sensor
                else "no_external_bike_power_sensor_in_standard_metadata"
            ),
            "external_power_sensor": external_power_sensor,
            "ontology_entity": "measurement_provenance",
        },
        "hr_source_classification": summary.get("hr_source_classification"),
        "hr_confidence": _device_hr_confidence(summary, fit_sources),
        "standard_fit_device_sources": fit_sources,
        "latest_attempt": latest_attempt,
        "source": source,
        "sources": [item for item in (source, fit_source_path) if item],
        "interpretation_guardrail": (
            "An external heart-rate source is asserted only from standard Garmin sensor metadata. "
            "When the preserved FIT contains local/onboard sources only, wrist or onboard HR is "
            "likely but not proven; proprietary paired-device strings are never used as active-source evidence."
        ),
    }


def _preserved_device_evidence(root: str | Path | None, activity_id: str) -> dict | None:
    detail = _matching_detail_call(root, activity_id, "activity")
    summary = summarize_activity_devices(detail[0]) if detail and isinstance(detail[0], dict) else {}
    fit_path = _original_fit_path(root, activity_id)
    fit_sources = read_standard_fit_device_sources(fit_path) if fit_path else None
    usable_fit = fit_sources if fit_sources and fit_sources.get("status") == "available" else None
    if not detail and usable_fit is None:
        return None
    source = detail[1] if detail else (_relative_path(root, fit_path) or fit_path.name)
    return _normalized_device_summary(
        summary,
        status="available_from_preserved_detail",
        source=source,
        fit_sources=usable_fit,
        fit_source_path=_relative_path(root, fit_path) if fit_path else None,
    )


def _device_evidence(root: str | Path | None, activity_id: str) -> dict:
    row = _matching_index_row(root, "activity_device_index.json", activity_id)
    source = "snapshots/activity_device_index.json"
    fit_path = _original_fit_path(root, activity_id)
    fit_sources = read_standard_fit_device_sources(fit_path) if fit_path else None
    usable_fit = (
        fit_sources
        if fit_sources and fit_sources.get("status") == "available"
        else None
    )
    if row is None:
        preserved = _preserved_device_evidence(root, activity_id)
        if preserved:
            return preserved
        return {
            "status": "not_indexed",
            "hr_confidence": "unknown_not_indexed",
            "source": source,
        }
    if row.get("device_fetch_ok") is not True:
        preserved = _preserved_device_evidence(root, activity_id)
        if preserved:
            preserved["index_latest_attempt"] = row.get("latest_attempt")
            preserved["index_fetch_error"] = row.get("device_fetch_error")
            return preserved
        return {
            "status": "fetch_failed" if row.get("device_fetch_ok") is False else "unknown",
            "hr_confidence": "unknown_fetch_failed",
            "source": source,
            "fetch_error": row.get("device_fetch_error"),
        }

    return _normalized_device_summary(
        row,
        status=(
            "available_cached_after_refresh_failure"
            if _cached_after_refresh_failure(row)
            else "available"
        ),
        source=source,
        fit_sources=usable_fit,
        fit_source_path=_relative_path(root, fit_path) if fit_path else None,
        latest_attempt=row.get("latest_attempt"),
    )


def _self_evaluation(
    root: str | Path | None,
    activity_id: str,
    activity_date: str | date | None = None,
) -> dict:
    source = "snapshots/activity_self_evaluation_index.json"
    try:
        fallback_activity_date = parse_date(activity_date)
    except (TypeError, ValueError):
        fallback_activity_date = None
    candidates = [
        row
        for row in _index_rows(root, "activity_self_evaluation_index.json")
        if str(row.get("activity_id") or row.get("id") or "") == activity_id
    ]
    row = None
    if fallback_activity_date is not None:
        for candidate in reversed(candidates):
            try:
                candidate_date = parse_date(candidate.get("date"))
            except (TypeError, ValueError):
                candidate_date = None
            if candidate_date == fallback_activity_date:
                row = candidate
                break
    if row is None and candidates:
        row = candidates[-1]
    if row is None:
        return {
            "status": "not_indexed",
            "activity_id": activity_id,
            "date": None,
            "source": source,
        }
    raw_row_date = row.get("date")
    try:
        row_date = parse_date(raw_row_date)
    except (TypeError, ValueError):
        row_date = None
    matched_date = row_date or (fallback_activity_date if raw_row_date in (None, "") else None)
    identity = {
        "activity_id": str(row.get("activity_id") or row.get("id") or activity_id),
        "date": matched_date.isoformat() if matched_date is not None else None,
        "identity_status": (
            "exact_match"
            if matched_date is not None
            and fallback_activity_date is not None
            and matched_date == fallback_activity_date
            else "date_mismatch"
            if matched_date is not None and fallback_activity_date is not None
            else "date_unverified"
        ),
        "identity_provenance": {
            "activity_id": f"{source}.activities[].activity_id",
            "date": (
                f"{source}.activities[].date"
                if row_date is not None
                else "latest_session_evidence.activity.date"
                if matched_date is not None
                else None
            ),
            "raw_date": raw_row_date,
        },
    }
    if identity["identity_status"] == "date_mismatch":
        return {
            "status": "identity_mismatch",
            **identity,
            "source": source,
            "latest_attempt": row.get("latest_attempt"),
            "interpretation_guardrail": (
                "A self-evaluation row from another activity date is not attached to this session."
            ),
        }
    if row.get("detail_fetch_ok") is not True:
        return {
            "status": "fetch_failed" if row.get("detail_fetch_ok") is False else "unknown",
            **identity,
            "source": source,
            "fetch_error": row.get("detail_fetch_error"),
            "latest_attempt": row.get("latest_attempt"),
        }
    if not row.get("has_self_evaluation"):
        return {
            "status": "not_logged",
            **identity,
            "source": source,
            "latest_attempt": row.get("latest_attempt"),
        }
    feel_score = as_number(row.get("feel_score"))
    derived_feel = (
        int(feel_score / 25) + 1
        if feel_score in {0, 25, 50, 75, 100}
        else None
    )
    rpe_score = as_number(row.get("rpe_score"))
    derived_rpe = (
        rpe_score / 10.0
        if rpe_score in {10, 20, 30, 40, 50, 60, 70, 80, 90, 100}
        else None
    )
    if derived_feel is None and derived_rpe is None:
        return {
            "status": "unusable_invalid_categories",
            **identity,
            "feel_score": feel_score,
            "rpe_score": rpe_score,
            "invalid_category_fields": [
                field
                for field, invalid in (
                    ("feel_score", feel_score is not None),
                    ("rpe_score", rpe_score is not None),
                )
                if invalid
            ],
            "latest_attempt": row.get("latest_attempt"),
            "source": source,
            "interpretation_guardrail": (
                "Garmin Feel and Perceived Effort are categorical surfaces. Off-grid raw values "
                "are preserved for audit but cannot be treated as a valid subjective review."
            ),
        }
    return {
        "status": (
            "available_cached_after_refresh_failure"
            if _cached_after_refresh_failure(row)
            else "available"
        ),
        **identity,
        "feel_score": row.get("feel_score"),
        "feel_label": row.get("feel_label"),
        "feel_out_of_5": derived_feel,
        "feel_ordinal_display_out_of_10": (
            derived_feel * 2 if derived_feel is not None else None
        ),
        "feel_display_remap": "ordinal_1_to_5_mapped_to_even_labels_2_to_10_not_interval_equivalence",
        "feel_construct": row.get("feel_construct") or "athlete_state_composite",
        "rpe_score": rpe_score,
        "rpe_label": row.get("rpe_label") if derived_rpe is not None else None,
        "rpe_out_of_10": derived_rpe,
        "global_rpe_out_of_10": derived_rpe,
        "ontology": {
            "feel_entity": "athlete_state",
            "rpe_entity": "delivered_session_effort",
            "separation_rule": (
                "Neither Garmin Feel nor Perceived Effort establishes technical execution, illness "
                "absence, or a safety-contract outcome."
            ),
        },
        "latest_attempt": row.get("latest_attempt"),
        "source": source,
    }


def _matching_detail_call(
    root: str | Path | None,
    activity_id: str,
    call_name: str,
) -> tuple[Any, str] | None:
    result = _matching_detail_call_with_provenance(root, activity_id, call_name)
    return (result[0], result[1]) if result else None


def _matching_detail_call_with_provenance(
    root: str | Path | None,
    activity_id: str,
    call_name: str,
) -> tuple[Any, str, dict] | None:
    paths = (
        repo_root(root) / "activities" / "details" / f"garmin_{activity_id}_detail.json",
        snapshots_dir(root) / f"activity_detail_{activity_id}.json",
    )
    for path in paths:
        payload = _read_detail_artifact(path)
        if not isinstance(payload, dict) or str(payload.get("activity_id") or "") != activity_id:
            continue
        call = (payload.get("calls") or {}).get(call_name) or {}
        data = call.get("data") if call.get("ok") is True else None
        if isinstance(data, (dict, list)) and data:
            latest_attempt = (
                call.get("latest_attempt")
                if isinstance(call.get("latest_attempt"), dict)
                else None
            )
            bounded_attempt = (
                {
                    key: latest_attempt.get(key)
                    for key in ("status", "attempted_at", "error", "label")
                    if latest_attempt.get(key) is not None
                }
                if latest_attempt
                else None
            )
            cached_after_failure = call.get("last_attempt_ok") is False
            return (
                data,
                _relative_path(root, path) or path.name,
                {
                    "call_status": call.get("status"),
                    "attempted_at": call.get("attempted_at"),
                    "last_success_at": call.get("last_success_at"),
                    "last_attempt_ok": call.get("last_attempt_ok"),
                    "latest_attempt": bounded_attempt,
                    "using_cached_after_refresh_failure": cached_after_failure,
                },
            )
    return None


@lru_cache(maxsize=2)
def _read_detail_artifact_version(path_text: str, modified_ns: int) -> dict:
    payload = read_json(Path(path_text), {})
    return payload if isinstance(payload, dict) else {}


def _read_detail_artifact(path: Path) -> dict:
    try:
        modified_ns = path.stat().st_mtime_ns
    except OSError:
        return {}
    return _read_detail_artifact_version(str(path.resolve()), modified_ns)


def _matching_detail_weather(
    root: str | Path | None,
    activity_id: str,
) -> tuple[dict, str] | None:
    result = _matching_detail_call(root, activity_id, "weather")
    if result and isinstance(result[0], dict):
        return result[0], result[1]
    return None


def _detail_trace_evidence(
    root: str | Path | None,
    activity_id: str,
) -> dict:
    paths = (
        repo_root(root) / "activities" / "details" / f"garmin_{activity_id}_detail.json",
        snapshots_dir(root) / f"activity_detail_{activity_id}.json",
    )
    unavailable = None
    for path in paths:
        payload = _read_detail_artifact(path)
        if not isinstance(payload, dict) or str(payload.get("activity_id") or "") != activity_id:
            continue
        source = _relative_path(root, path) or path.name
        call = (payload.get("calls") or {}).get("details")
        if not isinstance(call, dict):
            unavailable = unavailable or {
                "status": "not_collected",
                "sample_count": 0,
                "metric_descriptors": [],
                "metric_descriptors_truncated": False,
                "source": source,
            }
            continue
        data = call.get("data")
        if call.get("ok") is not True or not isinstance(data, dict):
            unavailable = unavailable or {
                "status": call.get("status") or "fetch_failed",
                "sample_count": 0,
                "metric_descriptors": [],
                "metric_descriptors_truncated": False,
                "source": source,
            }
            continue

        samples = data.get("activityDetailMetrics")
        descriptors = data.get("metricDescriptors")
        sample_count = len(samples) if isinstance(samples, list) else 0
        descriptor_rows = descriptors if isinstance(descriptors, list) else []
        valid_descriptors = [
            descriptor
            for descriptor in descriptor_rows
            if isinstance(descriptor, dict) and descriptor.get("key")
        ]
        public_descriptors = []
        for descriptor in valid_descriptors[:DETAIL_TRACE_METRIC_LIMIT]:
            unit = descriptor.get("unit")
            public_descriptors.append(
                {
                    "key": str(descriptor["key"]),
                    "unit": (
                        str(unit.get("key"))
                        if isinstance(unit, dict) and unit.get("key") is not None
                        else str(unit)
                        if unit is not None
                        else None
                    ),
                }
            )
        return {
            "status": "available" if sample_count else "available_no_samples",
            "sample_count": sample_count,
            "metric_descriptors": public_descriptors,
            "metric_descriptors_truncated": (
                len(valid_descriptors) > DETAIL_TRACE_METRIC_LIMIT
            ),
            "source": source,
            "interpretation_guardrail": (
                "This block reports persisted trace availability only. Raw time-series values are "
                "deliberately excluded, and no heart-rate drift or interval physiology is inferred."
            ),
        }
    return unavailable or {
        "status": "not_available",
        "sample_count": 0,
        "metric_descriptors": [],
        "metric_descriptors_truncated": False,
        "source": None,
    }


def _detail_metric_rows(
    root: str | Path | None,
    activity_id: str,
) -> tuple[list[dict], str, dict[str, str | None], dict] | None:
    result = _matching_detail_call_with_provenance(root, activity_id, "details")
    if not result or not isinstance(result[0], dict):
        return None
    data, source, call_provenance = result
    descriptor_rows = data.get("metricDescriptors")
    sample_rows = data.get("activityDetailMetrics")
    if not isinstance(descriptor_rows, list) or not isinstance(sample_rows, list):
        return None
    descriptors = {}
    descriptor_units: dict[str, str | None] = {}
    for descriptor in descriptor_rows:
        if not isinstance(descriptor, dict) or descriptor.get("key") is None:
            continue
        index = descriptor.get("metricsIndex")
        if isinstance(index, int) and index >= 0:
            key = str(descriptor["key"])
            descriptors[key] = index
            unit = descriptor.get("unit")
            descriptor_units[key] = (
                str(unit.get("key"))
                if isinstance(unit, dict) and unit.get("key") is not None
                else None
            )
    timestamp_index = descriptors.get("directTimestamp")
    elapsed_index = descriptors.get("sumElapsedDuration")
    if timestamp_index is None and elapsed_index is None:
        return None
    rows = []
    for sequence_index, sample in enumerate(sample_rows):
        metrics = sample.get("metrics") if isinstance(sample, dict) else None
        if not isinstance(metrics, list):
            continue
        timestamp = (
            _finite_scalar_number(metrics[timestamp_index])
            if timestamp_index is not None and timestamp_index < len(metrics)
            else None
        )
        elapsed = (
            _finite_scalar_number(metrics[elapsed_index])
            if elapsed_index is not None and elapsed_index < len(metrics)
            else None
        )
        if timestamp is None and elapsed is None:
            continue
        row = {
            "directTimestamp": timestamp,
            "sumElapsedDuration": elapsed,
            "_sequence_index": sequence_index,
        }
        for key in (
            "sumDistance",
            "directSpeed",
            "directElevation",
            "directHeartRate",
            "directPower",
            "directBikeCadence",
            "directPerformanceCondition",
        ):
            index = descriptors.get(key)
            row[key] = metrics[index] if index is not None and index < len(metrics) else None
        rows.append(row)
    if rows and all(
        _finite_scalar_number(row.get("directTimestamp")) is not None for row in rows
    ):
        rows.sort(key=lambda row: row["directTimestamp"])
    return (rows, source, descriptor_units, call_provenance) if rows else None


def _finite_scalar_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value.strip()) if isinstance(value, str) else float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _positive_finite_seconds(value: Any) -> float | None:
    seconds = _finite_scalar_number(value)
    if seconds is None or seconds <= 0:
        return None
    return seconds


def _select_session_duration_seconds(raw: dict) -> float | None:
    candidates = [
        duration
        for duration in (
            _positive_finite_seconds(raw.get("elapsedDuration")),
            _positive_finite_seconds(raw.get("duration")),
        )
        if duration is not None
        and duration <= MAX_PLAUSIBLE_SESSION_DURATION_SECONDS
    ]
    return max(candidates) if candidates else None


def _trace_duration_ceiling(session_duration_sec: Any) -> float | None:
    duration = _positive_finite_seconds(session_duration_sec)
    if (
        duration is None
        or duration > MAX_PLAUSIBLE_SESSION_DURATION_SECONDS
    ):
        return None
    tolerance = min(
        TRACE_TIMING_TOLERANCE_SECONDS,
        max(5.0, duration * TRACE_TIMING_TOLERANCE_FRACTION),
    )
    return duration + tolerance


def _span_and_gaps_fit_session(
    values: list[float],
    *,
    units_per_second: float,
    session_duration_sec: Any,
) -> bool:
    ceiling = _trace_duration_ceiling(session_duration_sec)
    if not values or ceiling is None:
        return False
    ordered = sorted(values)
    gaps_sec = [
        (value - ordered[index - 1]) / units_per_second
        for index, value in enumerate(ordered[1:], start=1)
    ]
    span_sec = (ordered[-1] - ordered[0]) / units_per_second
    return span_sec <= ceiling and all(gap <= ceiling for gap in gaps_sec)


def _validated_elapsed_order(
    rows: list[dict],
    session_duration_sec: Any,
) -> list[dict] | None:
    ceiling = _trace_duration_ceiling(session_duration_sec)
    if not rows or ceiling is None:
        return None
    source_order = sorted(rows, key=lambda row: row.get("_sequence_index", 0))
    elapsed_values = [
        _finite_scalar_number(row.get("sumElapsedDuration")) for row in source_order
    ]
    if any(
        value is None or not isfinite(value) or value < 0
        for value in elapsed_values
    ):
        return None
    numeric = [float(value) for value in elapsed_values if value is not None]
    if any(
        value < numeric[index - 1]
        for index, value in enumerate(numeric[1:], start=1)
    ):
        return None
    if max(numeric) > ceiling or not _span_and_gaps_fit_session(
        numeric,
        units_per_second=1.0,
        session_duration_sec=session_duration_sec,
    ):
        return None
    return source_order


def _epoch_units_per_second(values: list[float]) -> float | None:
    if not values:
        return None
    minimum_magnitude = min(abs(value) for value in values)
    maximum_magnitude = max(abs(value) for value in values)
    if 100_000_000_000 <= minimum_magnitude and maximum_magnitude < 100_000_000_000_000:
        return 1000.0
    if 100_000_000 <= minimum_magnitude and maximum_magnitude < 100_000_000_000:
        return 1.0
    return None


def _aligned_activity_begin_timestamp(
    rows: list[dict],
    timestamp_units_per_second: float,
    activity_begin_timestamp: Any,
    session_duration_sec: Any,
) -> float | None:
    begin = _finite_scalar_number(activity_begin_timestamp)
    duration = _positive_finite_seconds(session_duration_sec)
    ceiling = _trace_duration_ceiling(duration)
    if begin is None or duration is None or ceiling is None:
        return None
    begin_units_per_second = _epoch_units_per_second([begin])
    if begin_units_per_second is None:
        return None
    timestamps = [
        _finite_scalar_number(row.get("directTimestamp")) for row in rows
    ]
    if any(value is None for value in timestamps):
        return None
    begin_epoch_sec = begin / begin_units_per_second
    offsets_sec = [
        float(value) / timestamp_units_per_second - begin_epoch_sec
        for value in timestamps
        if value is not None
    ]
    tolerance_sec = ceiling - duration
    if min(offsets_sec) < -tolerance_sec or max(offsets_sec) > ceiling:
        return None
    return begin_epoch_sec * timestamp_units_per_second


def _validated_gmt_units_per_second(
    rows: list[dict],
    timestamp_unit: str | None,
    session_duration_sec: Any = None,
    *,
    allow_missing_unit: bool = False,
) -> float | None:
    """Infer epoch scale, optionally admitting a duration-bounded legacy missing unit."""
    if timestamp_unit != "gmt" and not (
        allow_missing_unit and timestamp_unit is None
    ):
        return None
    if timestamp_unit is None and _positive_finite_seconds(session_duration_sec) is None:
        return None
    timestamps = [_finite_scalar_number(row.get("directTimestamp")) for row in rows]
    if not timestamps or any(
        value is None or not isfinite(value) for value in timestamps
    ):
        return None
    numeric = [float(value) for value in timestamps if value is not None]
    units_per_second = _epoch_units_per_second(numeric)
    if units_per_second is None:
        return None
    if session_duration_sec is not None and not _span_and_gaps_fit_session(
        numeric,
        units_per_second=units_per_second,
        session_duration_sec=session_duration_sec,
    ):
        return None
    return units_per_second


def _performance_condition_evidence(
    root: str | Path | None,
    activity_id: str,
    gear_labels: list[str] | None = None,
    *,
    category: str | None = None,
    power_context_basis: list[str] | None = None,
    session_duration_sec: Any = None,
    activity_begin_timestamp: Any = None,
) -> dict:
    if category not in BIKE_SESSION_CATEGORIES:
        return {
            "status": "not_applicable_non_cycling",
            "ontology_entity": "physiological_response_context",
            "source": None,
        }
    power_context_basis = list(power_context_basis or [])
    if not power_context_basis:
        return {
            "status": "not_available_power_context_missing",
            "ontology_entity": "physiological_response_context",
            "power_context_basis": [],
            "source": None,
            "interpretation_guardrail": (
                "Cycling Performance Condition is withheld without activity power or standard bike-power provenance."
            ),
        }
    result = _detail_metric_rows(root, activity_id)
    if not result:
        return {
            "status": "not_available",
            "ontology_entity": "physiological_response_context",
            "power_context_basis": power_context_basis,
            "source": None,
        }
    rows, source, units, call_provenance = result
    if units.get("directPerformanceCondition") != "dimensionless":
        return {
            "status": "not_available_invalid_metric_unit",
            "ontology_entity": "physiological_response_context",
            "power_context_basis": power_context_basis,
            "source": source,
            "metric_unit": units.get("directPerformanceCondition"),
        }
    elapsed_unit = units.get("sumElapsedDuration")
    timestamp_unit = units.get("directTimestamp")
    candidate_rows = []
    for row in rows:
        value = _finite_scalar_number(row.get("directPerformanceCondition"))
        if value is None or not -20 <= value <= 20:
            continue
        candidate_rows.append(row)
    if not candidate_rows:
        return {
            "status": "not_available",
            "ontology_entity": "physiological_response_context",
            "power_context_basis": power_context_basis,
            "source": source,
            "reason": "named_directPerformanceCondition_has_no_valid_values",
        }

    duration = _positive_finite_seconds(session_duration_sec)
    elapsed_order = (
        _validated_elapsed_order(candidate_rows, duration)
        if elapsed_unit == "second"
        else None
    )
    raw_timestamp_units_per_second = _validated_gmt_units_per_second(
        rows, timestamp_unit
    )
    duration_bounded_timestamp_units_per_second = (
        _validated_gmt_units_per_second(rows, timestamp_unit, duration)
        if duration is not None
        else None
    )
    aligned_begin_timestamp = (
        _aligned_activity_begin_timestamp(
            rows,
            duration_bounded_timestamp_units_per_second,
            activity_begin_timestamp,
            duration,
        )
        if duration_bounded_timestamp_units_per_second is not None
        else None
    )
    timestamp_units_per_second = (
        duration_bounded_timestamp_units_per_second
        if aligned_begin_timestamp is not None
        else None
    )
    first_timestamp = None
    if elapsed_order is not None:
        ordered_rows = elapsed_order
        timing_basis = "sumElapsedDuration_second"
    elif timestamp_units_per_second is not None:
        ordered_rows = sorted(
            candidate_rows,
            key=lambda row: (
                _finite_scalar_number(row.get("directTimestamp")),
                row.get("_sequence_index", 0),
            ),
        )
        first_timestamp = aligned_begin_timestamp
        timing_basis = (
            "directTimestamp_gmt_epoch_milliseconds"
            if timestamp_units_per_second == 1000.0
            else "directTimestamp_gmt_epoch_seconds"
        )
    else:
        ordered_rows = sorted(
            candidate_rows, key=lambda row: row.get("_sequence_index", 0)
        )
        if duration is None:
            timing_basis = "withheld_session_duration_unavailable"
        elif (
            duration_bounded_timestamp_units_per_second is not None
            and aligned_begin_timestamp is None
        ):
            timing_basis = "withheld_timestamp_not_aligned_to_activity_begin"
        elif raw_timestamp_units_per_second is not None:
            timing_basis = "withheld_timestamp_span_or_gap_exceeds_session_duration"
        else:
            timing_basis = "withheld_missing_or_non_monotonic_elapsed"

    observations = []
    for row in ordered_rows:
        value = _finite_scalar_number(row.get("directPerformanceCondition"))
        elapsed = None
        if elapsed_order is not None:
            elapsed = _finite_scalar_number(row.get("sumElapsedDuration"))
        elif timestamp_units_per_second is not None and first_timestamp is not None:
            timestamp = _finite_scalar_number(row.get("directTimestamp"))
            if timestamp is not None:
                elapsed = (timestamp - first_timestamp) / timestamp_units_per_second
        observations.append(
            {
                "elapsed_min": round(elapsed / 60.0, 2) if elapsed is not None else None,
                "value": round(value, 1),
            }
        )

    elapsed_values = [item["elapsed_min"] for item in observations]
    if any(value is None for value in elapsed_values) or any(
        value < 0 or (index > 0 and value < elapsed_values[index - 1])
        for index, value in enumerate(elapsed_values)
        if value is not None
    ):
        for item in observations:
            item["elapsed_min"] = None
        if timing_basis not in {
            "withheld_session_duration_unavailable",
            "withheld_timestamp_not_aligned_to_activity_begin",
            "withheld_timestamp_span_or_gap_exceeds_session_duration",
        }:
            timing_basis = "withheld_missing_or_non_monotonic_elapsed"

    state_points = []
    previous = None
    for item in observations:
        if item["value"] == previous:
            continue
        state_points.append(item)
        previous = item["value"]
    state_point_count = len(state_points)
    change_count = max(0, state_point_count - 1)
    state_points_truncated = state_point_count > 16
    state_points = state_points[:16]
    values = [item["value"] for item in observations]
    labels = [str(item).lower() for item in gear_labels or []]
    matched_stumpjumper = any("stumpjumper" in item for item in labels)
    return {
        "status": (
            "available_cached_after_refresh_failure"
            if call_provenance.get("using_cached_after_refresh_failure")
            else "available"
        ),
        "ontology_entity": "physiological_response_context",
        "power_context_basis": power_context_basis,
        "context_scope": (
            "matched_stumpjumper_fitness_context"
            if matched_stumpjumper
            else "generic_cycling_physiological_context"
        ),
        "held_trace_observation_count": len(observations),
        "state_point_count": state_point_count,
        "change_count": change_count,
        "first_value": observations[0]["value"],
        "first_elapsed_min": observations[0]["elapsed_min"],
        "final_value": observations[-1]["value"],
        "last_elapsed_min": observations[-1]["elapsed_min"],
        "minimum": min(values),
        "maximum": max(values),
        "change_final_minus_first": round(values[-1] - values[0], 1),
        "state_points": state_points,
        "state_points_truncated": state_points_truncated,
        "timing_basis": timing_basis,
        "descriptor_units": {
            "directPerformanceCondition": units.get("directPerformanceCondition"),
            "sumElapsedDuration": elapsed_unit,
            "directTimestamp": timestamp_unit,
        },
        "source": source,
        "provenance": call_provenance,
        "interpretation_guardrail": (
            "This bounded summary uses only Garmin's named directPerformanceCondition detail metric; "
            "unknown/proprietary FIT fields are not promoted. For cycling it is a power/heart-rate "
            "response relative to Garmin's fitness baseline. Repeated or held source-trace observations "
            "at the collected cadence are not independent Garmin fitness estimates; the bounded state "
            "points carry the useful changes. Compare "
            "it only within similar bike, route, "
            "heat, sensor and effort contexts; it does not establish coordination, technical execution, "
            "illness absence, or safety."
        ),
    }


def _trace_movement_timing(
    root: str | Path | None,
    activity_id: str,
    elapsed_min: float | None,
    session_duration_sec: Any = None,
) -> dict | None:
    result = _detail_metric_rows(root, activity_id)
    if not result or elapsed_min is None or elapsed_min <= 0:
        return None
    rows, source, units, call_provenance = result
    timestamped_rows = sorted(
        (
            row
            for row in rows
            if _finite_scalar_number(row.get("directTimestamp")) is not None
        ),
        key=lambda row: row["directTimestamp"],
    )
    if len(timestamped_rows) < 30:
        return None

    timestamp_units_per_second = _validated_gmt_units_per_second(
        timestamped_rows,
        units.get("directTimestamp"),
        session_duration_sec,
        allow_missing_unit=True,
    )
    if timestamp_units_per_second is None:
        return None

    moving_sec = 0.0
    stopped_sec = 0.0
    unknown_sec = 0.0
    max_gap_sec = 0.0
    for index, row in enumerate(timestamped_rows[:-1]):
        next_row = timestamped_rows[index + 1]
        duration_sec = (
            next_row["directTimestamp"] - row["directTimestamp"]
        ) / timestamp_units_per_second
        if duration_sec <= 0:
            continue
        max_gap_sec = max(max_gap_sec, duration_sec)
        speed = as_number(row.get("directSpeed"))
        distance = as_number(row.get("sumDistance"))
        next_distance = as_number(next_row.get("sumDistance"))
        if speed is not None and speed >= 0.5:
            moving_sec += duration_sec
        elif distance is not None and next_distance is not None:
            if next_distance - distance >= 1.0:
                moving_sec += duration_sec
            else:
                stopped_sec += duration_sec
        elif speed is not None:
            stopped_sec += duration_sec
        else:
            unknown_sec += duration_sec

    elapsed_sec = elapsed_min * 60.0
    covered_sec = moving_sec + stopped_sec + unknown_sec
    coverage_ratio = covered_sec / elapsed_sec if elapsed_sec > 0 else 0.0
    if (
        coverage_ratio < 0.9
        or coverage_ratio > 1.05
        or unknown_sec > elapsed_sec * 0.05
        or max_gap_sec > 300
    ):
        return None
    return {
        "moving_min": round(moving_sec / 60.0, 1),
        "stopped_min": round(stopped_sec / 60.0, 1),
        "nonmoving_or_stopped_estimate_min": round(stopped_sec / 60.0, 1),
        "unknown_min": round(unknown_sec / 60.0, 1),
        "sample_count": len(timestamped_rows),
        "coverage_ratio": round(coverage_ratio, 3),
        "max_sample_gap_sec": round(max_gap_sec, 1),
        "source": source,
        "provenance": call_provenance,
        "method": (
            "Derived from consecutive Garmin detail samples: moving when direct speed is at least "
            "0.5 m/s or cumulative distance advances at least 1 m."
        ),
        "interpretation_guardrail": (
            "The nonmoving value is a trace-derived classification estimate, not observed proof that "
            "the athlete was stationary for every classified second; very slow steep movement can remain ambiguous."
        ),
    }


def _phase_stats(
    rows: list[dict],
    start_timestamp: float,
    end_timestamp: float,
    timestamp_units_per_second: float,
) -> dict:
    samples = [
        row
        for row in rows
        if start_timestamp <= row["directTimestamp"] <= end_timestamp
    ]
    elevations = [as_number(row.get("directElevation")) for row in samples]
    elevations = [value for value in elevations if value is not None]
    heart_rates = [as_number(row.get("directHeartRate")) for row in samples]
    heart_rates = [value for value in heart_rates if value is not None]
    hr_weight = 0.0
    hr_seconds = 0.0
    for index, row in enumerate(rows[:-1]):
        segment_start = max(start_timestamp, row["directTimestamp"])
        segment_end = min(end_timestamp, rows[index + 1]["directTimestamp"])
        heart_rate = as_number(row.get("directHeartRate"))
        if segment_end > segment_start and heart_rate is not None:
            duration_sec = (segment_end - segment_start) / timestamp_units_per_second
            hr_weight += heart_rate * duration_sec
            hr_seconds += duration_sec
    return {
        "duration_min": round(
            max(0.0, end_timestamp - start_timestamp)
            / timestamp_units_per_second
            / 60.0,
            1,
        ),
        "sample_count": len(samples),
        "start_elevation_m": _round(samples[0].get("directElevation"), 1) if samples else None,
        "end_elevation_m": _round(samples[-1].get("directElevation"), 1) if samples else None,
        "min_elevation_m": round(min(elevations), 1) if elevations else None,
        "max_elevation_m": round(max(elevations), 1) if elevations else None,
        "average_hr_bpm": round(hr_weight / hr_seconds, 1) if hr_seconds else None,
        "max_hr_bpm": round(max(heart_rates), 1) if heart_rates else None,
    }


def _hike_phase_summary(
    root: str | Path | None,
    activity_id: str,
    session_duration_sec: Any = None,
) -> dict:
    result = _detail_metric_rows(root, activity_id)
    if not result:
        return {"status": "not_available", "source": None}
    rows, source, units, call_provenance = result
    timestamped_rows = sorted(
        (
            row
            for row in rows
            if _finite_scalar_number(row.get("directTimestamp")) is not None
        ),
        key=lambda row: row["directTimestamp"],
    )
    timestamp_units_per_second = _validated_gmt_units_per_second(
        timestamped_rows,
        units.get("directTimestamp"),
        session_duration_sec,
        allow_missing_unit=True,
    )
    if timestamp_units_per_second is None:
        return {
            "status": "invalid_or_unknown_timestamp_scale",
            "source": source,
            "provenance": call_provenance,
        }
    valid_rows = [
        row
        for row in timestamped_rows
        if as_number(row.get("directElevation")) is not None
        and as_number(row.get("directHeartRate")) is not None
    ]
    valid_ratio = len(valid_rows) / len(timestamped_rows) if timestamped_rows else 0.0
    max_valid_gap_sec = max(
        (
            (valid_rows[index + 1]["directTimestamp"] - row["directTimestamp"])
            / timestamp_units_per_second
            for index, row in enumerate(valid_rows[:-1])
        ),
        default=0.0,
    )
    if len(valid_rows) < 30 or valid_ratio < 0.8 or max_valid_gap_sec > 300:
        return {
            "status": "insufficient_trace_samples",
            "sample_count": len(valid_rows),
            "trace_sample_count": len(timestamped_rows),
            "valid_hr_elevation_sample_ratio": round(valid_ratio, 3),
            "max_valid_sample_gap_sec": round(max_valid_gap_sec, 1),
            "source": source,
        }
    elevations = [as_number(row["directElevation"]) for row in valid_rows]
    min_elevation = min(elevations)
    max_elevation = max(elevations)
    if max_elevation - min_elevation < 25:
        return {
            "status": "insufficient_elevation_range",
            "sample_count": len(valid_rows),
            "source": source,
        }
    top_band_floor = max_elevation - 8.0
    top_rows = [row for row in valid_rows if as_number(row["directElevation"]) >= top_band_floor]
    if len(top_rows) < 3:
        return {
            "status": "insufficient_top_band_samples",
            "sample_count": len(valid_rows),
            "source": source,
        }
    start_ms = valid_rows[0]["directTimestamp"]
    end_ms = valid_rows[-1]["directTimestamp"]
    first_top_ms = top_rows[0]["directTimestamp"]
    last_top_ms = top_rows[-1]["directTimestamp"]
    if first_top_ms <= start_ms or last_top_ms >= end_ms or last_top_ms < first_top_ms:
        return {
            "status": "phase_boundaries_unavailable",
            "sample_count": len(valid_rows),
            "source": source,
        }
    return {
        "status": "available_derived",
        "sample_count": len(valid_rows),
        "trace_sample_count": len(timestamped_rows),
        "valid_hr_elevation_sample_ratio": round(valid_ratio, 3),
        "max_valid_sample_gap_sec": round(max_valid_gap_sec, 1),
        "trace_min_elevation_m": round(min_elevation, 1),
        "trace_max_elevation_m": round(max_elevation, 1),
        "top_band": {
            "floor_elevation_m": round(top_band_floor, 1),
            "rule": "within_8_m_of_trace_max",
            "first_entry_offset_min": round(
                (first_top_ms - start_ms) / timestamp_units_per_second / 60.0, 1
            ),
            "last_exit_offset_min": round(
                (last_top_ms - start_ms) / timestamp_units_per_second / 60.0, 1
            ),
        },
        "phases": {
            "ascent_to_first_top_band_entry": _phase_stats(
                valid_rows, start_ms, first_top_ms, timestamp_units_per_second
            ),
            "top_band_dwell": _phase_stats(
                valid_rows, first_top_ms, last_top_ms, timestamp_units_per_second
            ),
            "descent_after_last_top_band_exit": _phase_stats(
                valid_rows, last_top_ms, end_ms, timestamp_units_per_second
            ),
        },
        "source": source,
        "provenance": call_provenance,
        "derivation": (
            "Coach-stack phase reconstruction from persisted Garmin directTimestamp, "
            "directElevation, and directHeartRate samples; this is not a Garmin-native phase label."
        ),
        "interpretation_guardrail": (
            "The phase summary does not infer SpO2, respiration, altitude symptoms, or terrain-specific "
            "technical load."
        ),
    }


def _timing_evidence(
    raw: dict,
    category: str | None,
    trace_timing: dict | None,
) -> dict:
    session_duration_sec = _select_session_duration_seconds(raw)
    elapsed_min = (
        round(session_duration_sec / 60.0, 1)
        if session_duration_sec is not None
        else None
    )
    selected_duration_field = next(
        (
            key
            for key in ("elapsedDuration", "duration")
            if _positive_finite_seconds(raw.get(key)) == session_duration_sec
            and session_duration_sec is not None
            and session_duration_sec <= MAX_PLAUSIBLE_SESSION_DURATION_SECONDS
        ),
        None,
    )
    raw_moving_min = _minutes(raw.get("movingDuration"))
    raw_stopped_min = (
        round(max(0.0, elapsed_min - raw_moving_min), 1)
        if elapsed_min is not None and raw_moving_min is not None
        else None
    )
    distance_km = (
        (as_number(raw.get("distance")) or 0.0) / 1000.0
        if raw.get("distance") is not None
        else None
    )
    ascent_m = as_number(raw.get("elevationGain"))
    vertical_density = (
        ascent_m / distance_km
        if ascent_m is not None and distance_km is not None and distance_km > 0
        else None
    )
    moving_ratio = (
        raw_moving_min / elapsed_min
        if raw_moving_min is not None and elapsed_min is not None and elapsed_min > 0
        else None
    )
    suspect_steep_hike = bool(
        category == "hike"
        and elapsed_min is not None
        and elapsed_min >= 60
        and raw_stopped_min is not None
        and raw_stopped_min >= 45
        and moving_ratio is not None
        and moving_ratio <= 0.4
        and ascent_m is not None
        and ascent_m >= 100
        and vertical_density is not None
        and vertical_density >= 50
    )
    timing = {
        "elapsed_min": elapsed_min,
        "moving_min": raw_moving_min,
        "stopped_min": raw_stopped_min,
        "stopped_derivation": "elapsed_minus_moving" if raw_stopped_min is not None else None,
        "garmin_reported": {
            "elapsed_min": elapsed_min,
            "moving_min": raw_moving_min,
            "implied_stopped_min": raw_stopped_min,
            "source_fields": {
                "elapsed": selected_duration_field,
                "moving": "movingDuration" if raw.get("movingDuration") is not None else None,
            },
        },
        "plausibility": {"status": "accepted_as_reported"},
    }
    if not suspect_steep_hike:
        return timing

    threshold_min = max(10.0, (elapsed_min or 0.0) * 0.1)
    if trace_timing and trace_timing.get("moving_min") is not None:
        trace_moving = as_number(trace_timing.get("moving_min"))
        trace_stopped = as_number(trace_timing.get("stopped_min"))
        if trace_moving is not None and raw_moving_min is not None and trace_moving - raw_moving_min >= threshold_min:
            timing.update(
                {
                    "moving_min": round(trace_moving, 1),
                    "stopped_min": round(trace_stopped, 1) if trace_stopped is not None else None,
                    "nonmoving_or_stopped_estimate_min": (
                        round(trace_stopped, 1) if trace_stopped is not None else None
                    ),
                    "stopped_derivation": "derived_detail_trace_movement_timeline",
                    "stopped_interpretation": (
                        "Trace-derived nonmoving-or-stopped estimate; not proof of continuous stationary time."
                    ),
                    "plausibility": {
                        "status": "garmin_moving_duration_replaced_by_trace_estimate",
                        "reason": (
                            "Garmin movingDuration was implausibly low for a steep hike and the "
                            "persisted movement trace supported materially more active time."
                        ),
                        "trace": trace_timing,
                    },
                }
            )
            return timing
        if trace_moving is not None and raw_moving_min is not None and abs(trace_moving - raw_moving_min) < threshold_min:
            timing["plausibility"] = {
                "status": "low_moving_duration_supported_by_trace",
                "trace": trace_timing,
            }
            return timing

    timing.update(
        {
            "moving_min": None,
            "stopped_min": None,
            "stopped_derivation": "withheld_implausible_garmin_moving_duration",
            "plausibility": {
                "status": "withheld_without_credible_trace_support",
                "reason": (
                    "Garmin movingDuration is implausibly low for the steep-hike profile, so its "
                    "implied stopped time is retained only as raw provenance and not surfaced as coaching fact."
                ),
                "trace": trace_timing,
            },
        }
    )
    return timing


def _loop_candidates(root: str | Path | None, target: date) -> list[tuple[date, str, dict, Path]]:
    candidates = []
    for path in snapshots_dir(root).glob("activity_loop_load_*.json"):
        payload = read_json(path, {})
        if not isinstance(payload, dict) or payload.get("artifact_type") != "activity_loop_load":
            continue
        artifact_date = parse_date(payload.get("date"))
        if artifact_date is None or artifact_date > target:
            continue
        activity_id = str(payload.get("activity_id") or "")
        if activity_id:
            candidates.append((artifact_date, str(payload.get("generated_at") or ""), payload, path))
    return candidates


def _matching_loop(
    root: str | Path | None,
    target: date,
    activity_id: str,
) -> tuple[dict, Path] | None:
    rows = [item for item in _loop_candidates(root, target) if item[2].get("activity_id") is not None and str(item[2].get("activity_id")) == activity_id]
    if not rows:
        return None
    _, _, payload, path = max(
        rows,
        key=lambda item: (
            item[0],
            item[1],
            item[3].name != "activity_loop_load_current.json",
            item[3].name,
        ),
    )
    return payload, path


def _latest_loop(root: str | Path | None, target: date) -> tuple[dict, Path] | None:
    rows = _loop_candidates(root, target)
    if not rows:
        return None
    _, _, payload, path = max(
        rows,
        key=lambda item: (
            item[0],
            item[1],
            item[3].name != "activity_loop_load_current.json",
            item[3].name,
        ),
    )
    return payload, path


def _weather_evidence(weather: dict, source: str) -> dict:
    explicit_unit = weather.get("temperatureUnit") or weather.get("tempUnit")
    if explicit_unit:
        unit = str(explicit_unit)
        unit_status = "reported_by_source"
        decision_use = "heat_context"
    else:
        unit = "unknown"
        unit_status = "not_reported_by_garmin_payload"
        decision_use = "context_only_until_unit_verified"
    weather_type = weather.get("weatherTypeDTO")
    description = weather_type.get("desc") if isinstance(weather_type, dict) else None
    station = weather.get("weatherStationDTO")
    station_name = station.get("name") if isinstance(station, dict) else None
    return {
        "temperature": {
            "value": _round(weather.get("temp"), 1),
            "apparent_value": _round(weather.get("apparentTemp"), 1),
            "unit": unit,
            "unit_status": unit_status,
        },
        "relative_humidity": {
            "value": _round(weather.get("relativeHumidity"), 1),
            "unit": "percent",
        },
        "description": description,
        "station": station_name,
        "issue_date": weather.get("issueDate"),
        "decision_use": decision_use,
        "source": source,
    }


def _loop_row(row: dict, primary_estimate: str) -> dict:
    estimated = row.get("estimated_load") if isinstance(row.get("estimated_load"), dict) else {}
    return {
        "loop": row.get("loop"),
        "label": row.get("label"),
        "elapsed_min": _round(row.get("elapsed_min"), 2),
        "moving_min": _round(row.get("moving_min"), 2),
        "rest_min": _round(row.get("stop_min"), 2),
        "average_hr": _round(row.get("average_hr_est"), 1),
        "estimated_load": _round(estimated.get(primary_estimate), 1),
        "load_per_elapsed_hour": _round(row.get("load_per_elapsed_hour"), 1),
    }


def _first_vs_final(loops: list[dict], primary_estimate: str) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for row in loops:
        kinds = row.get("lap_kinds") if isinstance(row.get("lap_kinds"), list) else []
        label = "+".join(str(kind) for kind in kinds if kind) or "unclassified"
        groups.setdefault(label, []).append(row)
    comparisons = []
    for kind, rows in groups.items():
        rows = sorted(rows, key=lambda item: as_number(item.get("loop")) or 0)
        if len(rows) < 2:
            continue
        first = _loop_row(rows[0], primary_estimate)
        final = _loop_row(rows[-1], primary_estimate)
        comparisons.append(
            {
                "kind": kind,
                "samples": len(rows),
                "first": first,
                "final": final,
                "change_final_minus_first": {
                    key: _round((final.get(key) or 0) - (first.get(key) or 0), 2)
                    if final.get(key) is not None and first.get(key) is not None
                    else None
                    for key in (
                        "elapsed_min",
                        "moving_min",
                        "rest_min",
                        "average_hr",
                        "estimated_load",
                        "load_per_elapsed_hour",
                    )
                },
            }
        )
    return comparisons


def _compact_loop(
    root: str | Path | None,
    payload: dict,
    path: Path,
    latest_id: str,
    target: date,
) -> dict:
    loops = [row for row in payload.get("loops") or [] if isinstance(row, dict)]
    method = payload.get("method") if isinstance(payload.get("method"), dict) else {}
    primary_estimate = str(method.get("primary_estimate") or "primary_continuous_hr")
    total_moving = sum(as_number(row.get("moving_min")) or 0 for row in loops)
    total_rest = sum(as_number(row.get("stop_min")) or 0 for row in loops)
    artifact_date = parse_date(payload.get("date"))
    source = _relative_path(root, path) or path.name
    weather_payload = payload.get("weather")
    return {
        "activity_id": str(payload.get("activity_id") or ""),
        "date": payload.get("date"),
        "age_days_at_target": (target - artifact_date).days if artifact_date else None,
        "matches_latest_session": str(payload.get("activity_id") or "") == latest_id,
        "manual_loop_groups": len(loops),
        "official_activity_training_load": _round(payload.get("official_activity_training_load"), 1),
        "redistributed_primary_load": _round(
            sum(
                as_number((row.get("estimated_load") or {}).get(primary_estimate)) or 0
                for row in loops
            ),
            1,
        ),
        "moving_min": round(total_moving, 1),
        "rest_min": round(total_rest, 1),
        "first_vs_final": _first_vs_final(loops, primary_estimate),
        "load_method": {
            "primary_estimate": primary_estimate,
            "status": "estimated_redistribution_of_official_activity_load",
            "sanity_check_ratio": (payload.get("sanity_checks") or {}).get(
                "primary_sum_to_official_ratio"
            ),
        },
        "interpretation_guardrail": (
            "First-versus-final values describe timing and estimated physiological load, not braking, "
            "line choice, arm pump, or technical quality. Compare only similar routes and sensor conditions."
        ),
        "weather": (
            _weather_evidence(weather_payload, source)
            if isinstance(weather_payload, dict) and weather_payload
            else None
        ),
        "source": source,
    }


def _power_evidence(raw: dict) -> dict | None:
    curve = {
        label: _round(raw.get(f"maxAvgPower_{seconds}"), 0)
        for label, seconds in POWER_CURVE_POINTS.items()
        if as_number(raw.get(f"maxAvgPower_{seconds}")) is not None
    }
    values = {
        "average_w": _round(raw.get("avgPower"), 0),
        "normalized_w": _round(raw.get("normPower"), 0),
        "max_w": _round(raw.get("maxPower"), 0),
        "intensity_factor": _round(raw.get("intensityFactor"), 3),
        "max_20_min_w": _round(raw.get("max20MinPower"), 1),
    }
    detected_ftp = _round(raw.get("maxFtp"), 0)
    if not curve and all(value is None for value in values.values()) and detected_ftp is None:
        return None
    return {
        **values,
        "garmin_detected_ftp": (
            {
                "value_w": detected_ftp,
                "source_field": "maxFtp",
                "status": "activity_level_garmin_ftp_detection_surface",
                "confidence": "high_for_garmin_operational_value_moderate_for_clean_test_validity",
            }
            if detected_ftp is not None
            else None
        ),
        "selected_best_average_w": curve,
        "interpretation_guardrail": (
            "A non-null activity maxFtp is Garmin's sparse FTP-detection surface and may establish the "
            "current Garmin operational FTP when recent and sensor-backed. Session power and the "
            "20-minute best do not independently establish FTP, and historical 222 W remains P20 only."
        ),
    }


def _detailed_gym_rollup(payload: Any, source: str | None) -> dict | None:
    rows = payload.get("exerciseSets") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return None
    groups: dict[str, dict] = {}
    active_sets = rest_intervals = 0
    active_seconds = rest_seconds = 0.0
    for row in rows:
        if not isinstance(row, dict):
            continue
        duration = as_number(row.get("duration")) or 0.0
        if str(row.get("setType") or "").upper() != "ACTIVE":
            rest_intervals += 1
            rest_seconds += duration
            continue
        active_sets += 1
        active_seconds += duration
        detected = [item for item in row.get("exercises") or [] if isinstance(item, dict)]
        best = max(detected, key=lambda item: as_number(item.get("probability")) or 0) if detected else {}
        label = str(best.get("name") or best.get("category") or "unknown")
        group = groups.setdefault(
            label,
            {
                "exercise": label,
                "sets": 0,
                "reps": 0,
                "max_weight_kg": None,
                "volume_kg_reps": 0.0,
                "active_duration_min": 0.0,
                "detection_probability_pct": [],
            },
        )
        reps = as_number(row.get("repetitionCount"))
        weight_g = as_number(row.get("weight"))
        weight_kg = weight_g / 1000.0 if weight_g is not None else None
        group["sets"] += 1
        group["reps"] += int(reps or 0)
        group["active_duration_min"] += duration / 60.0
        if weight_kg is not None:
            group["max_weight_kg"] = max(group["max_weight_kg"] or 0.0, weight_kg)
            group["volume_kg_reps"] += weight_kg * (reps or 0.0)
        probability = as_number(best.get("probability"))
        if probability is not None:
            group["detection_probability_pct"].append(probability)
    public_groups = []
    for group in groups.values():
        probabilities = group.pop("detection_probability_pct")
        group["max_weight_kg"] = _round(group["max_weight_kg"], 2)
        group["volume_kg_reps"] = _round(group["volume_kg_reps"], 1)
        group["active_duration_min"] = _round(group["active_duration_min"], 2)
        group["mean_detection_probability_pct"] = (
            _round(sum(probabilities) / len(probabilities), 1) if probabilities else None
        )
        public_groups.append(group)
    return {
        "records": len([row for row in rows if isinstance(row, dict)]),
        "active_sets": active_sets,
        "rest_intervals": rest_intervals,
        "active_duration_min": _round(active_seconds / 60.0, 2),
        "rest_duration_min": _round(rest_seconds / 60.0, 2),
        "exercise_groups": sorted(public_groups, key=lambda item: item["exercise"]),
        "source": source,
        "detection_guardrail": (
            "Exercise labels and probabilities are Garmin auto-detection context; verify them against the actual gym log before progression decisions."
        ),
    }


def _gym_evidence(
    raw: dict,
    detail_payload: Any = None,
    detail_source: str | None = None,
) -> dict:
    summaries = raw.get("summarizedExerciseSets")
    if not isinstance(summaries, list):
        summaries = []
    exercises = []
    for row in summaries:
        if not isinstance(row, dict):
            continue
        exercises.append(
            {
                "category": row.get("category"),
                "subcategory": row.get("subcategory") or row.get("subCategory"),
                "sets": row.get("sets"),
                "reps": row.get("reps"),
                "max_weight_kg": _round((as_number(row.get("maxWeight")) or 0) / 1000, 2)
                if row.get("maxWeight") is not None
                else None,
                "volume_kg_reps": _round((as_number(row.get("volume")) or 0) / 1000, 1)
                if row.get("volume") is not None
                else None,
                "active_duration_min": _round((as_number(row.get("duration")) or 0) / 60000, 2)
                if row.get("duration") is not None
                else None,
            }
        )
    return {
        "active_sets": raw.get("activeSets"),
        "total_sets": raw.get("totalSets"),
        "total_reps": raw.get("totalReps"),
        "total_volume_kg_reps": _round(
            sum(as_number(item.get("volume_kg_reps")) or 0 for item in exercises), 1
        )
        if exercises
        else None,
        "exercises": exercises,
        "detailed_sets": _detailed_gym_rollup(detail_payload, detail_source),
        "unit_note": "Garmin summary weight and volume values are converted from grams to kilograms.",
    }


def build_latest_session_evidence(
    root: str | Path | None = None,
    for_date: str | date | None = None,
    activities: list[dict] | None = None,
) -> dict:
    target = parse_date(for_date) or today_local(DEFAULT_TIMEZONE)
    selected = _latest_training_activity(root, target, activities)
    if selected is None:
        return {
            "status": "missing",
            "date": target.isoformat(),
            "confidence": {"status": "missing"},
            "cautions": [
                {
                    "type": "latest_training_activity_missing",
                    "severity": "yellow",
                    "message": "No raw Garmin training activity is available on or before the target date.",
                }
            ],
        }

    activity, raw, source_path = selected
    activity_id = str(raw.get("activityId") or activity.get("id") or "")
    category = _session_category(activity)

    # Manual loop-load artifacts model MTB lap/stage structure.  Do not attach a
    # coincidentally matching artifact to a hike (or another modality), because
    # its Garmin-reported moving/rest split can conflict with the hike-specific
    # trace plausibility correction below.
    matching_loop = (
        _matching_loop(root, target, activity_id) if category == "mtb" else None
    )
    loop_analysis = (
        _compact_loop(root, matching_loop[0], matching_loop[1], activity_id, target)
        if matching_loop
        else None
    )
    latest_loop = _latest_loop(root, target)
    recent_loop_analysis = None
    if latest_loop and str(latest_loop[0].get("activity_id") or "") != activity_id:
        recent_loop_analysis = _compact_loop(
            root,
            latest_loop[0],
            latest_loop[1],
            activity_id,
            target,
        )

    detail_trace = _detail_trace_evidence(root, activity_id)
    session_duration_sec = _select_session_duration_seconds(raw)
    raw_elapsed_min = (
        round(session_duration_sec / 60.0, 1)
        if session_duration_sec is not None
        else None
    )
    trace_timing = _trace_movement_timing(
        root,
        activity_id,
        raw_elapsed_min,
        session_duration_sec,
    )
    timing = _timing_evidence(raw, category, trace_timing)
    hike_phase_summary = (
        _hike_phase_summary(root, activity_id, session_duration_sec)
        if category == "hike"
        else None
    )
    detail_weather = _matching_detail_weather(root, activity_id)
    gym_detail = (
        _matching_detail_call(root, activity_id, "exercise_sets")
        if activity.get("category") == "gym"
        else None
    )
    loop_weather = None
    if matching_loop and isinstance(matching_loop[0].get("weather"), dict):
        loop_weather = (
            matching_loop[0]["weather"],
            _relative_path(root, matching_loop[1]) or matching_loop[1].name,
        )
    weather_source = detail_weather or loop_weather
    weather = _weather_evidence(*weather_source) if weather_source else None

    gear = (
        _gear_evidence(root, activity_id)
        if category in BIKE_SESSION_CATEGORIES
        else {
            "status": "not_applicable_non_bike",
            "labels": [],
            "source": "snapshots/activity_gear_index.json",
        }
    )
    device = _device_evidence(root, activity_id)
    self_evaluation = _self_evaluation(root, activity_id, activity.get("date"))
    power_context_basis = []
    for key in ("avgPower", "normPower", "normalizedPower", "maxPower"):
        power = _finite_scalar_number(raw.get(key))
        if power is not None and power > 0:
            power_context_basis.append(f"garmin_activity_summary.{key}")
    if (device.get("power_measurement") or {}).get("external_power_sensor") is True:
        power_context_basis.append("standard_external_bike_power_sensor_metadata")
    performance_condition = _performance_condition_evidence(
        root,
        activity_id,
        gear.get("labels") or [],
        category=category,
        power_context_basis=power_context_basis,
        session_duration_sec=session_duration_sec,
        activity_begin_timestamp=raw.get("beginTimestamp"),
    )
    device_temperature = None
    if raw.get("minTemperature") is not None or raw.get("maxTemperature") is not None:
        device_temperature = {
            "min": _round(raw.get("minTemperature"), 1),
            "max": _round(raw.get("maxTemperature"), 1),
            "unit": "celsius",
            "source": "garmin_activity_summary_device_temperature",
            "decision_use": "device_exposure_context_not_air_temperature",
        }

    cautions = []
    for evidence_name, evidence in (
        ("gear", gear),
        ("device", device),
        ("self_evaluation", self_evaluation),
    ):
        if evidence.get("status") in {
            "not_indexed",
            "fetch_failed",
            "unknown",
            "available_cached_after_refresh_failure",
        }:
            cautions.append(
                {
                    "type": f"latest_session_{evidence_name}_{evidence.get('status')}",
                    "severity": "yellow",
                    "message": (
                        f"Latest-session {evidence_name.replace('_', ' ')} metadata is "
                        f"{evidence.get('status').replace('_', ' ')}; do not infer missing values."
                    ),
                }
            )
    if weather and weather.get("decision_use") != "heat_context":
        cautions.append(
            {
                "type": "latest_session_weather_unit_unverified",
                "severity": "yellow",
                "message": "Garmin weather temperature has no explicit unit; withhold it from heat decisions until verified.",
            }
        )
    recent_loop_weather = (recent_loop_analysis or {}).get("weather") or {}
    if recent_loop_weather.get("decision_use") == "context_only_until_unit_verified":
        cautions.append(
            {
                "type": "recent_loop_weather_unit_unverified",
                "severity": "info",
                "message": (
                    "The latest available trail-loop artifact has Garmin weather temperature without "
                    "an explicit unit; it remains context-only."
                ),
            }
        )
    if activity.get("category") == "mtb" and loop_analysis is None:
        cautions.append(
            {
                "type": "latest_mtb_loop_analysis_missing",
                "severity": "info",
                "message": "No matching manual loop-load artifact is available for the latest MTB session.",
            }
        )
    timing_status = (timing.get("plausibility") or {}).get("status")
    if timing_status == "garmin_moving_duration_replaced_by_trace_estimate":
        cautions.append(
            {
                "type": "latest_session_garmin_moving_duration_replaced",
                "severity": "yellow",
                "message": (
                    "Garmin movingDuration materially undercounted this steep hike; coaching timing "
                    "uses the bounded trace-derived estimate while retaining Garmin's raw value."
                ),
            }
        )
    elif timing_status == "withheld_without_credible_trace_support":
        cautions.append(
            {
                "type": "latest_session_garmin_moving_duration_withheld",
                "severity": "yellow",
                "message": (
                    "Garmin movingDuration was implausibly low for this steep hike and no adequate "
                    "trace supported its implied stopped time; moving/stopped coaching values are withheld."
                ),
            }
        )
    if device.get("hr_confidence") in {
        "unknown_not_indexed",
        "unknown_fetch_failed",
        "unknown_standard_metadata_no_hr_source",
        "unknown_standard_metadata_source",
        "external_device_present_hr_source_unresolved",
    }:
        cautions.append(
            {
                "type": "latest_session_hr_source_unresolved",
                "severity": "yellow",
                "message": (
                    "Standard metadata does not identify the latest session's active heart-rate source; "
                    "do not infer it from paired-device names or proprietary FIT fields."
                ),
            }
        )

    confidence_status = "partial" if any(item.get("severity") == "yellow" for item in cautions) else "complete"
    technical_context = None
    if category in {"mtb", "bike_outdoor"} and (
        raw.get("avgFlow") is not None or raw.get("grit") is not None
    ):
        technical_context = {
            "flow": _round(raw.get("avgFlow"), 2),
            "grit": _round(raw.get("grit"), 2),
            "interpretation_guardrail": (
                "Garmin Flow and Grit are route- and device-dependent context, not direct scores of "
                "braking, cornering, line choice, or expert execution."
            ),
        }
    return {
        "status": "available",
        "date": target.isoformat(),
        "activity": {
            "activity_id": activity_id,
            "date": activity.get("date"),
            "started_at_local": raw.get("startTimeLocal"),
            "name": raw.get("activityName") or activity.get("name"),
            "type": activity.get("type"),
            "category": category,
        },
        "timing": timing,
        "terrain": {
            "distance_km": _round((as_number(raw.get("distance")) or 0) / 1000, 2)
            if raw.get("distance") is not None
            else None,
            "ascent_m": _round(raw.get("elevationGain"), 1),
            "descent_m": _round(raw.get("elevationLoss"), 1),
        },
        "workload": {
            "garmin_training_load": _round(raw.get("activityTrainingLoad"), 1),
            "training_stress_score": _round(raw.get("trainingStressScore"), 1),
            "aerobic_training_effect": _round(raw.get("aerobicTrainingEffect"), 1),
            "anaerobic_training_effect": _round(raw.get("anaerobicTrainingEffect"), 1),
            "training_effect_label": raw.get("trainingEffectLabel"),
        },
        "heart_rate": {
            "average_bpm": _round(raw.get("averageHR"), 0),
            "max_bpm": _round(raw.get("maxHR"), 0),
        },
        "cadence": {
            "average_rpm": _round(raw.get("averageBikingCadenceInRevPerMinute"), 1),
            "max_rpm": _round(raw.get("maxBikingCadenceInRevPerMinute"), 1),
        }
        if raw.get("averageBikingCadenceInRevPerMinute") is not None
        or raw.get("maxBikingCadenceInRevPerMinute") is not None
        else None,
        "power": _power_evidence(raw),
        "performance_condition": performance_condition,
        "detail_trace": detail_trace,
        "hike_phase_summary": hike_phase_summary,
        "environment": {
            "device_temperature": device_temperature,
            "weather": weather,
            "garmin_estimated_water_loss": {
                "value_ml": _round(raw.get("waterEstimated"), 0),
                "source": "garmin_activity_summary_model_estimate",
                "measurement_type": "estimated_not_measured",
                "interpretation_guardrail": "This is not logged fluid intake or a measured sweat test.",
            }
            if raw.get("waterEstimated") is not None
            else None,
        },
        "technical_context": technical_context,
        "gear": gear,
        "device": device,
        "self_evaluation": self_evaluation,
        "gym": (
            _gym_evidence(
                raw,
                gym_detail[0] if gym_detail else None,
                gym_detail[1] if gym_detail else None,
            )
            if category == "gym"
            else None
        ),
        "loop_analysis": loop_analysis,
        "recent_loop_analysis": recent_loop_analysis,
        "confidence": {
            "status": confidence_status,
            "hr_confidence": device.get("hr_confidence"),
        },
        "cautions": cautions,
        "provenance": {
            "raw_activity_summary": _relative_path(root, source_path),
            "activity_detail": (
                detail_trace.get("source")
                if detail_trace.get("status") in {"available", "available_no_samples"}
                else detail_weather[1]
                if detail_weather
                else gym_detail[1]
                if gym_detail
                else None
            ),
            "matching_loop_artifact": loop_analysis.get("source") if loop_analysis else None,
            "gear_index": gear.get("source"),
            "device_index": (
                device.get("source")
                if device.get("source") == "snapshots/activity_device_index.json"
                else None
            ),
            "device_sources": device.get("sources") or [device.get("source")],
            "self_evaluation_index": self_evaluation.get("source"),
        },
    }
