from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

from .briefing import build_daily_brief
from .cleanup import cleanup_derived
from .coach_packet import build_coach_packet
from .data_quality import build_data_quality_report
from .device_audit import build_device_audit, summarize_activity_devices
from .data_inventory import build_data_inventory
from .evidence import (
    TRAINING_LOAD_CATEGORIES,
    as_number,
    find_value,
    summarize_activity,
    wellness_snapshot_is_usable,
)
from .gear_audit import BIKE_GEAR_CATEGORIES, build_gear_audit, summarize_gear_items
from .io import read_json, write_json
from .paths import activities_dir, ensure_layout, snapshots_dir
from .planning import build_today_plan, load_weekly_session
from .predictive_training import build_predictive_training
from .reports import review_block
from .self_evaluation import build_self_evaluation_report, summarize_activity_self_evaluation
from .state import build_current_state
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local
from .weekly_planning import build_weekly_plan


def _safe_call(label: str, func: Callable[..., Any], *args: Any) -> dict:
    attempted_at = iso_now(DEFAULT_TIMEZONE)
    try:
        data = func(*args)
        nonempty = bool(data) if isinstance(data, (dict, list, tuple, str, bytes, bytearray)) else data is not None
        return {
            "label": label,
            "ok": True,
            "status": "success" if nonempty else "success_empty",
            "attempted_at": attempted_at,
            "data": data,
        }
    except Exception as exc:  # Live Garmin API methods differ across versions.
        return {
            "label": label,
            "ok": False,
            "status": "failed",
            "attempted_at": attempted_at,
            "error": str(exc),
        }


def _unsupported_call(label: str) -> dict:
    return {
        "label": label,
        "ok": False,
        "status": "unsupported",
        "attempted_at": iso_now(DEFAULT_TIMEZONE),
        "error": "client_method_unavailable",
    }


def _not_attempted_call(label: str, reason: str) -> dict:
    return {
        "label": label,
        "ok": False,
        "status": "not_attempted",
        "attempted_at": None,
        "error": reason,
    }


def _fetch_record(result: dict) -> dict:
    return {
        "status": result.get("status") or ("success" if result.get("ok") else "failed"),
        "fetched_at": result.get("attempted_at"),
        "error": result.get("error"),
    }


def _device_metadata_usable(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    metadata = payload.get("metadataDTO")
    return isinstance(metadata, dict) and isinstance(metadata.get("sensors"), list)


def _device_fetch_record(result: dict, payload: Any) -> dict:
    record = _fetch_record(result)
    if result.get("status") == "success" and not _device_metadata_usable(payload):
        record.update(
            {
                "status": "success_empty",
                "endpoint_status": "success",
                "semantic_error": "metadataDTO.sensors_missing",
            }
        )
    return record


def _training_readiness_capability(result: dict) -> tuple[bool | None, int | None]:
    if result.get("status") != "success" or not isinstance(result.get("data"), list):
        return None, None
    devices = [item for item in result["data"] if isinstance(item, dict)]
    explicit = [
        item.get("trainingReadinessCapable")
        for item in devices
        if isinstance(item.get("trainingReadinessCapable"), bool)
    ]
    return (any(explicit) if explicit else None), len(devices)


def _training_readiness_sync_failure(
    capable: bool | None,
    payloads: list[dict],
) -> dict | None:
    if capable is not True or any(item.get("status") == "success" for item in payloads):
        return None
    return {
        "source": "training_readiness",
        "message": "Training Readiness-capable device has no nonempty readiness response.",
        "endpoints": [
            {"label": item.get("label"), **_fetch_record(item)}
            for item in payloads
        ],
    }


def _activity_success_empty_gap(
    root: str | Path | None,
    activity_limit: int,
    result: dict,
) -> dict | None:
    if (
        activity_limit <= 0
        or result.get("status") != "success_empty"
        or not isinstance(result.get("data"), list)
        or not any(activities_dir(root).glob("*.json"))
    ):
        return None
    return {
        "source": "activities",
        "message": (
            "Garmin returned an empty activity page despite an existing local activity corpus; "
            "treat this live fetch as a data gap."
        ),
        "attempt": _fetch_record(result),
    }


def _unit_system_sync_warning(result: dict) -> dict | None:
    if result.get("status") == "success":
        return None
    return {
        "source": "unit_system",
        "severity": "warning",
        "message": "Garmin unit system is unavailable; weather temperature units remain unverified.",
        "attempt": _fetch_record(result),
    }


def _cycling_ftp_summary(payload: Any) -> dict | None:
    ftp_w = as_number(
        find_value(
            payload,
            (
                "functionalThresholdPower",
                "functional_threshold_power",
                "ftpWatts",
                "ftp",
                "maxFtp",
                "thresholdPower",
            ),
        )
    )
    if ftp_w is None or ftp_w <= 0:
        return None
    effective_raw = find_value(
        payload,
        (
            "calendarDate",
            "effectiveDate",
            "measurementDate",
            "date",
        ),
    )
    try:
        effective = parse_date(effective_raw)
    except (TypeError, ValueError):
        effective = None
    detected_by = find_value(
        payload,
        (
            "functionalThresholdPowerSource",
            "ftpSource",
            "measurementSource",
        ),
    )
    biometric_source_type = find_value(
        payload,
        (
            "biometricSourceType",
            "sourceType",
        ),
    )
    sport = find_value(payload, ("sport", "sportType"))
    return {
        "ftp_w": round(ftp_w, 1),
        "effective_date": effective.isoformat() if effective else None,
        "effective_at": str(effective_raw) if effective_raw not in (None, "") else None,
        "detection_source": str(detected_by) if detected_by not in (None, "") else None,
        "biometric_source_type": (
            str(biometric_source_type)
            if biometric_source_type not in (None, "")
            else None
        ),
        "sport": str(sport).upper() if sport not in (None, "") else "CYCLING",
    }


def _write_cycling_ftp_current(
    root: str | Path | None,
    result: dict,
) -> dict:
    """Write the latest Garmin cycling FTP while retaining a prior usable response."""
    path = snapshots_dir(root) / "garmin_cycling_ftp_current.json"
    existing = read_json(path, {})
    previous = (
        existing.get("last_known_good")
        if isinstance(existing, dict) and isinstance(existing.get("last_known_good"), dict)
        else None
    )
    summary = (
        _cycling_ftp_summary(result.get("data"))
        if result.get("status") == "success"
        else None
    )
    attempt_status = str(
        result.get("status") or ("success" if result.get("ok") else "failed")
    )
    semantic_error = None
    if attempt_status == "success" and summary is None:
        attempt_status = "success_empty"
        semantic_error = "functional_threshold_power_missing_or_invalid"

    latest_attempt = {
        "label": result.get("label") or "get_cycling_ftp",
        "status": attempt_status,
        "endpoint_ok": bool(result.get("ok")),
        "attempted_at": result.get("attempted_at"),
        "error": result.get("error"),
        "semantic_error": semantic_error,
        **(summary or {}),
    }
    if summary is None and "data" in result:
        latest_attempt["raw_payload"] = result.get("data")

    if summary is not None:
        last_known_good = {
            **summary,
            "fetched_at": result.get("attempted_at"),
            "raw_payload": result.get("data"),
        }
    else:
        last_known_good = previous

    available = isinstance(last_known_good, dict)
    artifact = {
        "artifact_type": "garmin_cycling_ftp_current",
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "source": "garminconnect.get_cycling_ftp",
        "privacy": "raw_private_local_only",
        "sport": (last_known_good or {}).get("sport") or "CYCLING",
        "status": (
            "available_current"
            if summary is not None
            else ("available_retained" if available else "unavailable")
        ),
        "ftp_w": (last_known_good or {}).get("ftp_w"),
        "effective_date": (last_known_good or {}).get("effective_date"),
        "effective_at": (last_known_good or {}).get("effective_at"),
        "detection_source": (last_known_good or {}).get("detection_source"),
        "biometric_source_type": (last_known_good or {}).get(
            "biometric_source_type"
        ),
        "latest_attempt": latest_attempt,
        "last_attempt_ok": attempt_status == "success",
        "last_attempt_status": attempt_status,
        "last_attempt_error": result.get("error") or semantic_error,
        "last_success_at": (last_known_good or {}).get("fetched_at"),
        "last_known_good": last_known_good,
        "retention_policy": "preserve_last_nonempty_success",
        "decision_use": "current_garmin_operational_ftp_context",
    }
    write_json(path, artifact)
    return artifact


def _preserve_last_good_wellness_call(
    previous: Any,
    current: dict,
    expected_date: str | None = None,
    required_series_key: str | tuple[str, ...] = "stressValuesArray",
    required_value_keys: tuple[str, ...] = (),
    require_response_date: bool = False,
) -> dict:
    """Keep raw nonempty evidence while exposing the exact latest degraded attempt."""
    previous = previous if isinstance(previous, dict) else {}
    current = dict(current)

    def has_required_series(result: dict) -> bool:
        data = result.get("data")
        if not isinstance(data, dict):
            return False
        series_keys = (
            (required_series_key,)
            if isinstance(required_series_key, str)
            else required_series_key
        )
        has_series = any(
            isinstance(data.get(key), list) and bool(data.get(key))
            for key in series_keys
        )
        has_summary_value = any(
            (value := as_number(data.get(key))) is not None and value > 0
            for key in required_value_keys
        )
        if not has_series and not has_summary_value:
            return False
        response_date = parse_date(data.get("calendarDate") or data.get("date"))
        expected = parse_date(expected_date)
        if require_response_date and expected is not None and response_date is None:
            return False
        return not (expected is not None and response_date is not None and response_date != expected)

    previous_status = previous.get("status") or (
        "success" if previous.get("ok") and previous.get("data") else None
    )
    if previous_status == "success" and not has_required_series(previous):
        previous_status = "success_empty"
    current_status = current.get("status") or (
        "success" if current.get("ok") and current.get("data") else "failed"
    )
    current_data = current.get("data") if isinstance(current.get("data"), dict) else {}
    response_date = parse_date(current_data.get("calendarDate") or current_data.get("date"))
    expected = parse_date(expected_date)
    response_date_mismatch = (
        expected is not None and response_date is not None and response_date != expected
    )
    response_date_missing = (
        require_response_date
        and expected is not None
        and response_date is None
    )
    if current_status == "success" and (response_date_mismatch or response_date_missing):
        current_status = "failed"
        current["status"] = current_status
        current["ok"] = False
        current["error"] = (
            "response_date_missing" if response_date_missing else "response_date_mismatch"
        )
        current["expected_date"] = expected.isoformat()
        current["response_date"] = response_date.isoformat() if response_date else None
    elif current_status == "success" and not has_required_series(current):
        current_status = "success_empty"
        current["status"] = current_status
    degraded = current_status in {
        "failed",
        "unsupported",
        "success_empty",
        "not_attempted",
    }
    latest_attempt = (
        {key: value for key, value in current.items() if key != "data"}
        if current_status == "success"
        else dict(current)
    )
    if previous_status == "success" and degraded:
        return {
            **previous,
            "latest_attempt": latest_attempt,
            "last_attempt_ok": current_status == "success_empty",
            "last_attempt_status": current_status,
            "last_attempt_error": current.get("error"),
            "last_success_at": previous.get("last_success_at")
            or previous.get("attempted_at"),
            "retention_policy": "preserve_last_nonempty_success",
        }
    return {
        **current,
        "latest_attempt": latest_attempt,
        "last_attempt_ok": current_status in {"success", "success_empty"},
        "last_attempt_status": current_status,
        "last_attempt_error": current.get("error"),
        **(
            {"last_success_at": current.get("attempted_at")}
            if current_status == "success"
            else {}
        ),
        "retention_policy": "preserve_last_nonempty_success",
    }


_WELLNESS_SERIES_RETENTION = {
    "get_spo2_data": {
        "scope": "single Garmin calendar-date endpoint response",
        "fields": {
            "spO2HourlyAverages": {
                "nominal_cadence": "hourly",
                "nominal_daily_max_samples": 24,
            },
            "spO2SingleValues": {
                "nominal_cadence": "event_driven",
                "nominal_daily_max_samples": None,
            },
            "continuousReadingDTOList": {
                "nominal_cadence": "device_defined",
                "nominal_daily_max_samples": None,
            },
        },
    },
    "get_respiration_data": {
        "scope": "single Garmin calendar-date endpoint response",
        "fields": {
            "respirationValuesArray": {
                "nominal_cadence": "two_minutes",
                "nominal_daily_max_samples": 720,
            },
            "respirationAveragesValuesArray": {
                "nominal_cadence": "hourly",
                "nominal_daily_max_samples": 24,
            },
        },
    },
}


def _annotate_wellness_series_retention(item: dict) -> dict:
    """Describe the daily raw-series bound without deleting Garmin evidence."""
    contract = _WELLNESS_SERIES_RETENTION.get(str(item.get("label") or ""))
    if contract is None:
        return item
    data = item.get("data") if isinstance(item.get("data"), dict) else {}
    fields = []
    for field, metadata in contract["fields"].items():
        raw = data.get(field)
        fields.append(
            {
                "field": field,
                "retained_count": len(raw) if isinstance(raw, list) else 0,
                **metadata,
            }
        )
    return {
        **item,
        "series_retention": {
            "scope": contract["scope"],
            "policy": (
                "Preserve the complete endpoint response for the requested day; "
                "do not aggregate it into an unbounded longitudinal raw array."
            ),
            "series": fields,
        },
    }


def _write_wellness_payload(root: str | Path | None, day: str, payloads: list[dict]) -> dict:
    path = snapshots_dir(root) / f"garmin_wellness_{day}.json"
    existing = read_json(path, {})
    previous_payloads = {
        item.get("label"): item
        for item in (existing.get("payloads") or [])
        if isinstance(item, dict) and item.get("label")
    } if isinstance(existing, dict) else {}
    retained_series = {
        "get_all_day_stress": {
            "series": "stressValuesArray",
            "values": (),
            "require_date": False,
        },
        "get_heart_rates": {
            "series": "heartRateValues",
            "values": (),
            "require_date": True,
        },
        "get_spo2_data": {
            "series": (
                "spO2HourlyAverages",
                "spO2SingleValues",
                "continuousReadingDTOList",
            ),
            "values": ("averageSpO2", "avgSleepSpO2", "latestSpO2"),
            "require_date": True,
        },
        "get_respiration_data": {
            "series": ("respirationValuesArray", "respirationAveragesValuesArray"),
            "values": ("avgWakingRespirationValue", "avgSleepRespirationValue"),
            "require_date": True,
        },
    }
    retained_payloads = []
    for raw_item in payloads:
        item = _annotate_wellness_series_retention(raw_item)
        label = item.get("label")
        retention = retained_series.get(label)
        retained_payloads.append(
            _preserve_last_good_wellness_call(
                previous_payloads.get(label),
                item,
                day,
                required_series_key=retention["series"],
                required_value_keys=retention["values"],
                require_response_date=retention["require_date"],
            )
            if retention
            else item
        )
    combined = {
        "artifact_type": "raw_garmin_wellness",
        "date": day,
        "fetched_at": iso_now(DEFAULT_TIMEZONE),
        "source": "garminconnect",
        "privacy": "raw_private_local_only",
        "payloads": retained_payloads,
    }
    write_json(path, combined)
    return combined


def _write_merged_activity_index(
    root: str | Path | None,
    filename: str,
    source: str,
    rows: list[dict],
    **metadata: Any,
) -> dict:
    path = snapshots_dir(root) / filename
    existing = read_json(path, {})
    merged: dict[str, dict] = {}
    if isinstance(existing, dict):
        for row in existing.get("activities") or []:
            if not isinstance(row, dict):
                continue
            activity_id = str(row.get("activity_id") or row.get("id") or "")
            if activity_id:
                merged[activity_id] = row
    current_ids: set[str] = set()
    for row in rows:
        activity_id = str(row.get("activity_id") or row.get("id") or "")
        if activity_id:
            current_ids.add(activity_id)
            previous = merged.get(activity_id) or {}
            fetch = row.get("fetch") if isinstance(row.get("fetch"), dict) else {}
            fetch_status = str(fetch.get("status") or "unknown")
            previous_fetch = previous.get("fetch") if isinstance(previous.get("fetch"), dict) else {}
            previous_succeeded = str(previous_fetch.get("status") or "") in {
                "success",
                "success_empty",
            } or any(
                previous.get(key) is True
                for key in ("gear_fetch_ok", "device_fetch_ok", "detail_fetch_ok")
            )
            degraded_attempt = fetch_status in {"failed", "unsupported"} or (
                fetch_status == "success_empty" and row.get("preserve_last_success_on_empty")
            )
            if degraded_attempt and previous and previous_succeeded:
                preserved = dict(previous)
                for key in ("date", "name", "type", "category"):
                    if row.get(key) is not None:
                        preserved[key] = row.get(key)
                preserved["latest_attempt"] = fetch
                preserved["last_attempt_ok"] = False
                preserved["last_attempt_error"] = fetch.get("error")
                merged[activity_id] = preserved
            else:
                combined = {**previous, **row}
                combined["latest_attempt"] = fetch
                combined["last_attempt_ok"] = fetch_status in {"success", "success_empty"} and not degraded_attempt
                combined["last_attempt_error"] = fetch.get("error")
                if combined["last_attempt_ok"]:
                    combined["last_success_at"] = fetch.get("fetched_at")
                merged[activity_id] = combined
    status_counts: dict[str, int] = {}
    for row in rows:
        fetch = row.get("fetch") if isinstance(row.get("fetch"), dict) else {}
        status = str(fetch.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    artifact = {
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "source": source,
        "last_sync_rows": len(rows),
        "last_sync_summary": {
            "attempted": len(rows),
            "status_counts": dict(sorted(status_counts.items())),
            "succeeded": sum(status_counts.get(key, 0) for key in ("success", "success_empty")),
            "nonempty_success": status_counts.get("success", 0),
            "empty_success": status_counts.get("success_empty", 0),
            "failed": status_counts.get("failed", 0),
            "unsupported": status_counts.get("unsupported", 0),
        },
        "retained_rows": max(0, len(merged) - len(current_ids)),
        **metadata,
        "activities": sorted(
            merged.values(),
            key=lambda item: (item.get("date") or "", str(item.get("activity_id") or "")),
            reverse=True,
        ),
    }
    write_json(path, artifact)
    return artifact


def _write_activity_gear_index(
    root: str | Path | None,
    client: Any,
    activities: list[dict],
) -> dict:
    rows = []
    for activity in activities:
        summary = summarize_activity(activity)
        if summary.get("category") not in BIKE_GEAR_CATEGORIES:
            continue
        activity_id = summary.get("id")
        if not activity_id:
            continue
        method = getattr(client, "get_activity_gear", None)
        result = (
            _safe_call("get_activity_gear", method, activity_id)
            if method
            else _unsupported_call("get_activity_gear")
        )
        gear_payload = result.get("data") if result.get("ok") else []
        rows.append(
            {
                "activity_id": activity_id,
                "date": summary.get("date"),
                "name": summary.get("name"),
                "type": summary.get("type"),
                "category": summary.get("category"),
                "gear_fetch_ok": result.get("ok"),
                "gear_fetch_error": result.get("error"),
                "fetch": _fetch_record(result),
                "gear": summarize_gear_items(gear_payload),
            }
        )
    return _write_merged_activity_index(
        root,
        "activity_gear_index.json",
        "garminconnect.get_activity_gear",
        rows,
    )


def _fetch_activity_detail_results(
    client: Any,
    activities: list[dict],
    max_self_evaluation_fetches: int = 20,
) -> dict[str, dict]:
    wanted: list[str] = []
    for activity in activities:
        summary = summarize_activity(activity)
        activity_id = summary.get("id")
        if activity_id and summary.get("category") in BIKE_GEAR_CATEGORIES:
            wanted.append(str(activity_id))
    recent_meaningful = _ordered_training_activities(activities)[
        : max(1, max_self_evaluation_fetches)
    ]
    for activity, summary in recent_meaningful:
        activity_id = summary.get("id")
        if activity_id:
            wanted.append(str(activity_id))

    method = getattr(client, "get_activity", None)
    results: dict[str, dict] = {}
    for activity_id in dict.fromkeys(wanted):
        results[activity_id] = (
            _safe_call("get_activity", method, activity_id)
            if method
            else _unsupported_call("get_activity")
        )
    return results


def _write_activity_device_index(
    root: str | Path | None,
    client: Any,
    activities: list[dict],
    detail_results: dict[str, dict] | None = None,
    max_recent_non_bike: int = 5,
) -> dict:
    ordered_non_bike_ids = [
        str(summary.get("id"))
        for _, summary in _ordered_training_activities(activities)
        if summary.get("category") not in BIKE_GEAR_CATEGORIES
    ]
    recent_non_bike_ids = set(ordered_non_bike_ids[: max(0, max_recent_non_bike)])
    rows = []
    for activity in activities:
        summary = summarize_activity(activity)
        activity_id = summary.get("id")
        if summary.get("category") not in BIKE_GEAR_CATEGORIES and str(
            activity_id or ""
        ) not in recent_non_bike_ids:
            continue
        if not activity_id:
            continue
        result = (detail_results or {}).get(str(activity_id))
        if result is None:
            method = getattr(client, "get_activity", None)
            result = (
                _safe_call("get_activity", method, activity_id)
                if method
                else _unsupported_call("get_activity")
            )
        device_payload = result.get("data") if result.get("ok") and isinstance(result.get("data"), dict) else {}
        device_fetch = _device_fetch_record(result, device_payload)
        device_metadata_usable = _device_metadata_usable(device_payload)
        device_summary = summarize_activity_devices(device_payload)
        rows.append(
            {
                "activity_id": activity_id,
                "date": summary.get("date"),
                "name": summary.get("name"),
                "type": summary.get("type"),
                "category": summary.get("category"),
                "device_fetch_ok": device_metadata_usable,
                "device_fetch_error": result.get("error") or device_fetch.get("semantic_error"),
                "fetch": device_fetch,
                "preserve_last_success_on_empty": True,
                **device_summary,
            }
        )
    return _write_merged_activity_index(
        root,
        "activity_device_index.json",
        "garminconnect.get_activity.metadataDTO",
        rows,
    )


def _write_activity_self_evaluation_index(
    root: str | Path | None,
    client: Any,
    activities: list[dict],
    max_detail_fetches: int = 20,
    detail_results: dict[str, dict] | None = None,
) -> dict:
    rows = []
    for activity in activities[:max(1, max_detail_fetches)]:
        summary = summarize_activity(activity)
        activity_id = summary.get("id")
        if not activity_id:
            continue
        result = (detail_results or {}).get(str(activity_id))
        if result is None:
            method = getattr(client, "get_activity", None)
            result = (
                _safe_call("get_activity", method, activity_id)
                if method
                else _unsupported_call("get_activity")
            )
        detail_payload = result.get("data") if result.get("ok") and isinstance(result.get("data"), dict) else {}
        rows.append(
            {
                "activity_id": activity_id,
                "date": summary.get("date"),
                "name": summary.get("name"),
                "type": summary.get("type"),
                "category": summary.get("category"),
                "detail_fetch_ok": result.get("status") == "success",
                "detail_fetch_error": result.get("error"),
                "fetch": _fetch_record(result),
                "preserve_last_success_on_empty": True,
                **summarize_activity_self_evaluation(detail_payload),
            }
        )
    return _write_merged_activity_index(
        root,
        "activity_self_evaluation_index.json",
        "garminconnect.get_activity.summaryDTO.directWorkoutFeel/directWorkoutRpe",
        rows,
        max_detail_fetches=max_detail_fetches,
    )


KEY_DETAIL_CATEGORIES = tuple(sorted(TRAINING_LOAD_CATEGORIES))


def _activity_recency_key(activity: dict, summary: dict) -> tuple[str, str, str]:
    start = find_value(
        activity,
        ("startTimeLocal", "start_time_local", "startTimeGMT", "start_time"),
    )
    return (
        str(summary.get("date") or ""),
        str(start or ""),
        str(summary.get("id") or ""),
    )


def _ordered_training_activities(activities: list[dict]) -> list[tuple[dict, dict]]:
    pairs = [
        (activity, summarize_activity(activity))
        for activity in activities
    ]
    pairs = [
        pair
        for pair in pairs
        if pair[1].get("id") and pair[1].get("counts_for_training_load")
    ]
    return sorted(
        pairs,
        key=lambda pair: _activity_recency_key(pair[0], pair[1]),
        reverse=True,
    )


def _select_key_activities(activities: list[dict], limit: int) -> list[tuple[dict, dict]]:
    if limit <= 0:
        return []
    candidates = _ordered_training_activities(activities)
    selected: list[tuple[dict, dict]] = []
    selected_ids: set[str] = set()
    selected_categories: set[str] = set()

    # The latest meaningful session is the primary coaching surface even when
    # it is a hike or run. Subsequent slots maximize modality diversity.
    if candidates:
        activity, summary = candidates[0]
        activity_id = str(summary.get("id") or "")
        selected.append((activity, summary))
        selected_ids.add(activity_id)
        selected_categories.add(str(summary.get("category") or "other"))
    for activity, summary in candidates[1:]:
        activity_id = str(summary.get("id") or "")
        category = str(summary.get("category") or "other")
        if activity_id in selected_ids or category in selected_categories:
            continue
        selected.append((activity, summary))
        selected_ids.add(activity_id)
        selected_categories.add(category)
        if len(selected) >= limit:
            return selected[:limit]
    for activity, summary in candidates:
        activity_id = str(summary.get("id") or "")
        if activity_id and activity_id not in selected_ids:
            selected.append((activity, summary))
            selected_ids.add(activity_id)
            if len(selected) >= limit:
                break
    return selected[:limit]


def _method_call(client: Any, method_name: str, *args: Any) -> dict:
    method = getattr(client, method_name, None)
    return (
        _safe_call(method_name, method, *args)
        if method
        else _unsupported_call(method_name)
    )


def _write_original_activity_file(
    root: str | Path | None,
    activity_id: str,
    result: dict,
) -> dict:
    data = result.get("data")
    summary = {key: value for key, value in result.items() if key != "data"}
    if not result.get("ok") or not isinstance(data, (bytes, bytearray)) or not data:
        return summary
    extension = ".zip" if bytes(data[:2]) == b"PK" else ".fit"
    destination = activities_dir(root) / "fit" / f"garmin_{activity_id}_original{extension}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(destination.suffix + ".tmp")
    tmp.write_bytes(bytes(data))
    tmp.replace(destination)
    return {
        **summary,
        "stored_path": str(destination),
        "bytes": len(data),
        "format": "zip" if extension == ".zip" else "fit",
    }


def _preserve_last_good_detail_call(previous: Any, current: dict) -> dict:
    previous = previous if isinstance(previous, dict) else {}
    previous_status = previous.get("status") or (
        "success" if previous.get("ok") and previous.get("data") else None
    )
    current_status = current.get("status")
    degraded = current_status in {
        "failed",
        "unsupported",
        "success_empty",
        "not_attempted",
    }
    attempt = {key: value for key, value in current.items() if key != "data"}
    if previous_status == "success" and degraded:
        return {
            **previous,
            "latest_attempt": attempt,
            "last_attempt_ok": False,
            "last_attempt_error": current.get("error"),
        }
    return {
        **current,
        "latest_attempt": attempt,
        "last_attempt_ok": current_status in {"success", "success_empty"},
        "last_attempt_error": current.get("error"),
        **(
            {"last_success_at": current.get("attempted_at")}
            if current_status == "success"
            else {}
        ),
    }


def _write_key_activity_details(
    root: str | Path | None,
    client: Any,
    activities: list[dict],
    detail_results: dict[str, dict],
    limit: int,
    original_download_format: Any = None,
) -> dict:
    selected = _select_key_activities(activities, limit)
    rows = []
    for _raw_activity, summary in selected:
        activity_id = str(summary.get("id") or "")
        category = str(summary.get("category") or "")
        calls = {
            "activity": detail_results.get(activity_id)
            or _method_call(client, "get_activity", activity_id),
            "details": _method_call(client, "get_activity_details", activity_id),
            "splits": _method_call(client, "get_activity_splits", activity_id),
            "hr_zones": _method_call(client, "get_activity_hr_in_timezones", activity_id),
            "power_zones": (
                _method_call(client, "get_activity_power_in_timezones", activity_id)
                if category in {"mtb", "bike_indoor", "bike_outdoor"}
                else _not_attempted_call("get_activity_power_in_timezones", "not_applicable")
            ),
            "weather": (
                _method_call(client, "get_activity_weather", activity_id)
                if category in {"mtb", "bike_outdoor"}
                else _not_attempted_call("get_activity_weather", "not_applicable")
            ),
            "exercise_sets": (
                _method_call(client, "get_activity_exercise_sets", activity_id)
                if category == "gym"
                else _not_attempted_call("get_activity_exercise_sets", "not_applicable")
            ),
        }
        if hasattr(client, "download_activity") and original_download_format is not None:
            original_result = _safe_call(
                "download_activity_original",
                client.download_activity,
                activity_id,
                original_download_format,
            )
        elif hasattr(client, "download_activity"):
            original_result = _not_attempted_call(
                "download_activity_original",
                "original_download_format_unavailable",
            )
        else:
            original_result = _unsupported_call("download_activity_original")
        calls["original_download"] = _write_original_activity_file(
            root,
            activity_id,
            original_result,
        )

        path = activities_dir(root) / "details" / f"garmin_{activity_id}_detail.json"
        existing = read_json(path, {})
        previous_calls = (
            existing.get("calls")
            if isinstance(existing, dict) and isinstance(existing.get("calls"), dict)
            else {}
        )
        retained_calls = {
            name: _preserve_last_good_detail_call(previous_calls.get(name), result)
            for name, result in calls.items()
        }
        artifact = {
            "artifact_type": "raw_garmin_key_activity_detail",
            "activity_id": activity_id,
            "date": summary.get("date"),
            "category": category,
            "fetched_at": iso_now(DEFAULT_TIMEZONE),
            "source": "garminconnect",
            "privacy": "raw_private_local_only",
            "calls": retained_calls,
            "latest_refresh": {
                name: {key: value for key, value in result.items() if key != "data"}
                for name, result in calls.items()
            },
        }
        write_json(path, artifact)
        failed_calls = [
            name
            for name, result in calls.items()
            if isinstance(result, dict)
            and result.get("status") in {"failed", "unsupported"}
        ]
        empty_required_calls = [
            name
            for name in ("activity", "details", "splits")
            if isinstance(calls.get(name), dict)
            and calls[name].get("status") == "success_empty"
        ]
        rows.append(
            {
                "activity_id": activity_id,
                "date": summary.get("date"),
                "category": category,
                "path": str(path),
                "failed_calls": failed_calls,
                "empty_required_calls": empty_required_calls,
                "status": "partial" if failed_calls or empty_required_calls else "ok",
            }
        )
    return {
        "attempted": len(selected),
        "succeeded": sum(1 for row in rows if row.get("status") == "ok"),
        "partial": sum(1 for row in rows if row.get("status") == "partial"),
        "activities": rows,
    }


def _fetch_live(
    root: str | Path | None,
    wellness_days: int,
    activity_limit: int,
    key_detail_limit: int = 0,
) -> dict:
    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    if not email or not password:
        return {
            "status": "skipped",
            "reason": "GARMIN_EMAIL and GARMIN_PASSWORD are not both set.",
        }
    try:
        from garminconnect import Garmin
    except Exception as exc:
        return {"status": "skipped", "reason": f"garminconnect import failed: {exc}"}

    tokenstore = os.getenv("GARMINTOKENS")
    default_tokenstore = Path.home() / ".garminconnect"
    if not tokenstore and default_tokenstore.exists():
        tokenstore = str(default_tokenstore)

    try:
        client = Garmin(email, password)
        if tokenstore:
            client.login(tokenstore=tokenstore)
            auth_method = "tokenstore"
        else:
            client.login()
            auth_method = "credentials"
    except Exception as exc:
        if tokenstore:
            return {
                "status": "failed",
                "reason": f"Garmin cached-token login failed from {tokenstore}: {exc}",
                "auth_method": "tokenstore",
            }
        return {
            "status": "failed",
            "reason": f"Garmin credential login failed: {exc}",
            "auth_method": "credentials",
        }
    today = today_local(DEFAULT_TIMEZONE)
    wellness_written = []
    wellness_unusable = []
    wellness_endpoint_failures: list[dict] = []
    for offset in range(max(1, wellness_days)):
        day = (today - timedelta(days=offset)).isoformat()
        payloads = [
            _method_call(client, "get_stats", day),
            _method_call(client, "get_user_summary", day),
            _method_call(client, "get_all_day_stress", day),
            _method_call(client, "get_heart_rates", day),
            _method_call(client, "get_spo2_data", day),
            _method_call(client, "get_respiration_data", day),
            _method_call(client, "get_body_battery", day, day),
            _method_call(client, "get_body_battery_events", day),
            _method_call(client, "get_sleep_data", day),
            _method_call(client, "get_hrv_data", day),
            _method_call(client, "get_body_composition", day),
        ]
        snapshot = _write_wellness_payload(root, day, payloads)
        wellness_written.append(day)
        if not wellness_snapshot_is_usable(snapshot):
            wellness_unusable.append(day)
        failed = [
            {"label": item.get("label"), **_fetch_record(item)}
            for item in payloads
            if item.get("status") in {"failed", "unsupported"}
        ]
        if failed:
            wellness_endpoint_failures.append({"date": day, "endpoints": failed})

    training_status = _method_call(client, "get_training_status", today.isoformat())
    write_json(
        snapshots_dir(root) / f"garmin_training_status_{today.isoformat()}.json",
        {
            "date": today.isoformat(),
            "fetched_at": iso_now(DEFAULT_TIMEZONE),
            "source": "garminconnect",
            "payload": training_status,
        },
    )
    training_status_usable = bool(
        training_status
        and training_status.get("ok")
        and isinstance(training_status.get("data"), dict)
        and training_status.get("data")
    )

    cycling_ftp_result = _method_call(client, "get_cycling_ftp")
    cycling_ftp = _write_cycling_ftp_current(root, cycling_ftp_result)

    training_readiness_payloads = [
        _method_call(client, "get_training_readiness", today.isoformat()),
        _method_call(client, "get_morning_training_readiness", today.isoformat()),
    ]
    write_json(
        snapshots_dir(root) / f"garmin_training_readiness_{today.isoformat()}.json",
        {
            "date": today.isoformat(),
            "fetched_at": iso_now(DEFAULT_TIMEZONE),
            "source": "garminconnect",
            "payloads": training_readiness_payloads,
        },
    )
    training_readiness_nonempty = any(
        item.get("status") == "success" for item in training_readiness_payloads
    )
    device_capabilities = _method_call(client, "get_devices")
    unit_system = _method_call(client, "get_unit_system")
    training_readiness_capable, registered_device_count = _training_readiness_capability(
        device_capabilities
    )
    write_json(
        snapshots_dir(root) / f"garmin_device_capabilities_{today.isoformat()}.json",
        {
            "date": today.isoformat(),
            "fetched_at": iso_now(DEFAULT_TIMEZONE),
            "source": "garminconnect",
            "privacy": "raw_private_local_only",
            "training_readiness_capable": training_readiness_capable,
            "registered_device_count": registered_device_count,
            "calls": {
                "devices": device_capabilities,
                "unit_system": unit_system,
            },
        },
    )

    activities_written = 0
    activity_gear_index = None
    activity_device_index = None
    activity_self_evaluation_index = None
    key_activity_details = {"attempted": 0, "succeeded": 0, "partial": 0, "activities": []}
    activity_fetch_failure = None
    activity_result = _not_attempted_call("get_activities", "activity_limit_zero")
    if activity_limit > 0:
        activity_result = _method_call(client, "get_activities", 0, activity_limit)
        result = activity_result
        if result.get("ok") and isinstance(result.get("data"), list):
            for activity in result["data"]:
                activity_id = activity.get("activityId") or activity.get("id") or activities_written
                write_json(activities_dir(root) / f"garmin_{activity_id}.json", activity)
                activities_written += 1
            detail_results = _fetch_activity_detail_results(client, result["data"])
            activity_gear_index = _write_activity_gear_index(root, client, result["data"])
            activity_device_index = _write_activity_device_index(
                root,
                client,
                result["data"],
                detail_results=detail_results,
            )
            activity_self_evaluation_index = _write_activity_self_evaluation_index(
                root,
                client,
                result["data"],
                detail_results=detail_results,
            )
            original_format = None
            try:
                original_format = Garmin.ActivityDownloadFormat.ORIGINAL
            except (AttributeError, TypeError):
                pass
            key_activity_details = _write_key_activity_details(
                root,
                client,
                result["data"],
                detail_results,
                key_detail_limit,
                original_download_format=original_format,
            )
        else:
            activity_fetch_failure = result.get("error") or "Garmin activities response was not a list."

    failures = []
    warnings = []
    if wellness_unusable:
        failures.append({"source": "wellness", "dates": wellness_unusable})
    if wellness_endpoint_failures:
        failures.append({"source": "wellness_endpoints", "details": wellness_endpoint_failures})
    if not training_status_usable:
        failures.append(
            {
                "source": "training_status",
                "attempt": _fetch_record(training_status),
            }
        )
    if cycling_ftp_result.get("status") in {"failed", "unsupported"}:
        failures.append(
            {
                "source": "cycling_ftp",
                "attempt": _fetch_record(cycling_ftp_result),
                "retained_last_known_good": cycling_ftp.get("status")
                == "available_retained",
            }
        )
    if device_capabilities.get("status") in {"failed", "unsupported"}:
        failures.append(
            {"source": "device_capabilities", "attempt": _fetch_record(device_capabilities)}
        )
    training_readiness_failure = _training_readiness_sync_failure(
        training_readiness_capable,
        training_readiness_payloads,
    )
    if training_readiness_failure:
        failures.append(training_readiness_failure)
    activity_empty_gap = _activity_success_empty_gap(root, activity_limit, activity_result)
    if activity_empty_gap:
        failures.append(activity_empty_gap)
    unit_system_warning = _unit_system_sync_warning(unit_system)
    if unit_system_warning:
        warnings.append(unit_system_warning)
    if activity_fetch_failure:
        failures.append({"source": "activities", "message": activity_fetch_failure})
    for source, artifact in (
        ("activity_gear", activity_gear_index),
        ("activity_device", activity_device_index),
        ("activity_self_evaluation", activity_self_evaluation_index),
    ):
        summary = (artifact or {}).get("last_sync_summary") or {}
        unusable_empty = source in {"activity_device", "activity_self_evaluation"} and summary.get(
            "empty_success"
        )
        if summary.get("failed") or summary.get("unsupported") or unusable_empty:
            failures.append({"source": source, "summary": summary})
    if key_activity_details.get("partial"):
        failures.append(
            {
                "source": "key_activity_details",
                "partial": key_activity_details.get("partial"),
                "activities": key_activity_details.get("activities"),
            }
        )

    return {
        "status": "partial" if failures else "ok",
        "auth_method": auth_method,
        "fetched_at": iso_now(DEFAULT_TIMEZONE),
        "wellness_written": wellness_written,
        "wellness_unusable_dates": wellness_unusable,
        "wellness_endpoint_failures": wellness_endpoint_failures,
        "training_status_written": training_status_usable,
        "training_status_endpoint": _fetch_record(training_status),
        "cycling_ftp_written": True,
        "cycling_ftp_available": cycling_ftp.get("status")
        in {"available_current", "available_retained"},
        "cycling_ftp_status": cycling_ftp.get("status"),
        "cycling_ftp_endpoint": _fetch_record(cycling_ftp_result),
        "training_readiness_written": True,
        "training_readiness_nonempty": training_readiness_nonempty,
        "training_readiness_capable": training_readiness_capable,
        "registered_device_count": registered_device_count,
        "training_readiness_endpoints": [
            {"label": item.get("label"), **_fetch_record(item)}
            for item in training_readiness_payloads
        ],
        "device_capabilities_endpoint": _fetch_record(device_capabilities),
        "unit_system_endpoint": _fetch_record(unit_system),
        "activities_written": activities_written,
        "activities_endpoint": _fetch_record(activity_result),
        "activity_gear_sync": (activity_gear_index or {}).get("last_sync_summary"),
        "activity_device_sync": (activity_device_index or {}).get("last_sync_summary"),
        "activity_self_evaluation_sync": (activity_self_evaluation_index or {}).get("last_sync_summary"),
        "activity_gear_checked": ((activity_gear_index or {}).get("last_sync_summary") or {}).get(
            "attempted", 0
        ),
        "activity_device_checked": ((activity_device_index or {}).get("last_sync_summary") or {}).get(
            "attempted", 0
        ),
        "activity_self_evaluation_checked": (
            (activity_self_evaluation_index or {}).get("last_sync_summary") or {}
        ).get("attempted", 0),
        "key_activity_details": key_activity_details,
        "failures": failures,
        "warnings": warnings,
    }


def _record_sync_run(
    root: str | Path | None,
    live: dict,
    rebuild_only: bool,
    decision_only: bool,
) -> None:
    entry = {
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "run_type": "rebuild" if rebuild_only else "live_sync",
        "mode": "decision_only" if decision_only else "full",
        "live_status": live.get("status"),
        "live_fetched_at": live.get("fetched_at"),
        "failure_count": len(live.get("failures") or []),
    }
    ledger_path = snapshots_dir(root) / "sync_run_ledger.json"
    ledger = read_json(ledger_path, {})
    entries = ledger.get("entries") if isinstance(ledger, dict) else []
    if not isinstance(entries, list):
        entries = []
    entries.append(entry)
    write_json(
        ledger_path,
        {
            "generated_at": entry["generated_at"],
            "retention": 50,
            "entries": entries[-50:],
        },
    )
    if rebuild_only:
        return
    live_path = snapshots_dir(root) / "last_live_sync_status.json"
    previous = read_json(live_path, {})
    artifact = {
        "generated_at": entry["generated_at"],
        "last_attempt": {"run": entry, "live_sync": live},
        "last_successful_contact": (
            {"run": entry, "live_sync": live}
            if live.get("status") in {"ok", "partial"} and live.get("auth_method")
            else (previous.get("last_successful_contact") if isinstance(previous, dict) else None)
        ),
    }
    write_json(live_path, artifact)


def sync_connect(
    root: str | Path | None = None,
    wellness_days: int = 30,
    activity_limit: int = 200,
    rebuild_only: bool = False,
    cleanup_after: bool = False,
    decision_only: bool = False,
    key_detail_limit: int = 3,
) -> dict:
    ensure_layout(root)
    live = (
        {"status": "skipped", "reason": "rebuild_only"}
        if rebuild_only
        else _fetch_live(
            root,
            wellness_days,
            activity_limit,
            key_detail_limit=0 if decision_only else max(0, key_detail_limit),
        )
    )
    _record_sync_run(root, live, rebuild_only=rebuild_only, decision_only=decision_only)
    state = build_current_state(root, refresh_models=not decision_only)
    state_date = parse_date(state.get("date"))
    weekly_session = load_weekly_session(root, state_date) if state_date else None
    weekly_plan = (
        build_weekly_plan(root, state=state)
        if state_date and (state_date.weekday() == 0 or weekly_session is None)
        else None
    )
    weekly_plan_available = weekly_plan is not None or weekly_session is not None
    plan = build_today_plan(root, state=state)
    predictive = build_predictive_training(root, state=state, plan=plan)
    if decision_only:
        coach_packet = build_coach_packet(root, state=state, plan=plan)
        status = {
            "generated_at": iso_now(DEFAULT_TIMEZONE),
            "mode": "decision_only",
            "live_sync": live,
            "state_file": str(snapshots_dir(root) / "current_state.json"),
            "weekly_plan_file": str(snapshots_dir(root) / "weekly_plan.txt") if weekly_plan_available else None,
            "coach_packet_file": str(snapshots_dir(root) / "coach_packet.txt"),
            "readiness_level": state.get("readiness", {}).get("readiness_level"),
            "session_title": plan.get("session", {}).get("title"),
            "predictive_training_file": str(snapshots_dir(root) / "predictive_training.json"),
            "predictive_model_confidence": (
                predictive.get("today_prescription", {}).get("model_confidence", {}).get("status")
            ),
            "predictive_latest_review": (
                predictive.get("latest_review", {}).get("comparison", {}).get("interpretation")
            ),
            "coach_packet_confidence": coach_packet.get("today_call", {}).get("coach_confidence"),
            "coach_packet_stance": coach_packet.get("today_call", {}).get("stance"),
            "skipped_full_rebuild_artifacts": [
                "daily_brief",
                "review_block",
                "garmin_surface_manifest",
                "data_inventory",
                "data_quality",
            ],
        }
        write_json(snapshots_dir(root) / "sync_status.json", status)
        return status
    brief = build_daily_brief(root, state=state, plan=plan)
    review = review_block(root, state=state)
    inventory = build_data_inventory(root)
    quality = build_data_quality_report(root)
    gear_audit = build_gear_audit(root)
    device_audit = build_device_audit(root)
    self_evaluation = build_self_evaluation_report(root)
    battery_model = state.get("body_battery_model", {})
    baselines = state.get("historical_baselines", {})
    training_predictor = state.get("training_predictor", {})
    coach_packet = build_coach_packet(root, state=state, plan=plan)
    cleanup = cleanup_derived(root, apply=True) if cleanup_after else None
    status = {
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "live_sync": live,
        "state_file": str(snapshots_dir(root) / "current_state.json"),
        "brief_file": str(snapshots_dir(root) / "daily_brief.txt"),
        "weekly_plan_file": str(snapshots_dir(root) / "weekly_plan.txt") if weekly_plan_available else None,
        "coach_packet_file": str(snapshots_dir(root) / "coach_packet.txt"),
        "review_block_file": str(snapshots_dir(root) / "review_block.txt"),
        "garmin_surface_manifest_file": str(
            snapshots_dir(root) / "garmin_surface_manifest.json"
        ),
        "readiness_level": state.get("readiness", {}).get("readiness_level"),
        "session_title": brief.get("session", {}).get("title"),
        "predictive_training_file": str(snapshots_dir(root) / "predictive_training.json"),
        "predictive_model_confidence": (
            predictive.get("today_prescription", {}).get("model_confidence", {}).get("status")
        ),
        "predictive_latest_review": (
            predictive.get("latest_review", {}).get("comparison", {}).get("interpretation")
        ),
        "next_review_date": review.get("next_review_date"),
        "wellness_days_available": inventory.get("wellness_days_available"),
        "activity_count": inventory.get("activity_count"),
        "garmin_endpoint_state_counts": inventory.get("endpoint_state_counts", {}),
        "data_quality_flags": quality.get("flags", []),
        "gear_audit_flags": gear_audit.get("flags", []),
        "device_audit_flags": device_audit.get("flags", []),
        "self_evaluation_checked": self_evaluation.get("checked_activities"),
        "self_evaluation_logged": self_evaluation.get("evaluated_activities"),
        "body_battery_model_samples": battery_model.get("samples"),
        "body_battery_model_accuracy": battery_model.get("leave_one_out_accuracy"),
        "historical_activity_count": baselines.get("activity_count"),
        "training_predictor_samples": training_predictor.get("samples"),
        "training_predictor_validation": training_predictor.get("validation"),
        "coach_packet_confidence": coach_packet.get("today_call", {}).get("coach_confidence"),
        "coach_packet_stance": coach_packet.get("today_call", {}).get("stance"),
        "cleanup": cleanup,
    }
    write_json(snapshots_dir(root) / "sync_status.json", status)
    return status
