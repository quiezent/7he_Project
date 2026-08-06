from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from .context import load_context
from .evidence import as_number
from .io import read_json, write_json
from .paths import snapshots_dir
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local


ARTIFACT_VERSION = "wearable_coverage_v3"
MATERIAL_UNAVAILABLE_MINUTES = 15.0
FALLBACK_CADENCE_SECONDS = 180.0
MAX_CADENCE_SECONDS = 30 * 60.0
MAX_POSITIVE_CADENCE_SECONDS = 5 * 60.0
MIN_POSITIVE_VALID_DENSITY_PCT = 90.0
MIN_POSITIVE_CUTOFF_HOUR = 18
CONFIRMED_STATUSES = {"confirmed", "completed", "complete", "done", "occurred", "reported"}
ALLOWED_REASON_CATEGORIES = {
    "church_dress_watch",
    "dress_watch",
    "charging",
    "skin_break",
    "device_maintenance",
    "medical",
    "travel_security",
    "other_reported",
}
ALLOWED_REPLACEMENT_CATEGORIES = {"dress_watch", "none", "other", "unknown"}
ALLOWED_FREQUENCY_CATEGORIES = {"always", "usually", "occasional", "reported_pattern"}
ALLOWED_ATTEMPT_STATUSES = {
    "success",
    "failed",
    "unsupported",
    "success_empty",
    "not_attempted",
}
ALLOWED_ATTEMPT_ERROR_CATEGORIES = {
    "response_date_mismatch",
    "missing_required_series",
    "empty_required_series",
    "unsupported_endpoint",
    "authentication_failed",
    "rate_limited",
    "timeout",
    "network_error",
}


def _payload_record(snapshot: dict, label: str = "get_all_day_stress") -> dict:
    for payload in snapshot.get("payloads") or []:
        if isinstance(payload, dict) and payload.get("label") == label:
            return payload
    return {}


def _descriptor_index(descriptors: Any, keys: Iterable[str], default: int) -> int:
    wanted = {str(key).lower() for key in keys}
    for descriptor in descriptors or []:
        if not isinstance(descriptor, dict):
            continue
        key = next(
            (
                descriptor.get(name)
                for name in (
                    "key",
                    "stressValueDescriptorKey",
                    "bodyBatteryValueDescriptorKey",
                )
                if descriptor.get(name) is not None
            ),
            None,
        )
        if str(key).lower() not in wanted:
            continue
        index = next(
            (
                as_number(descriptor.get(name))
                for name in (
                    "index",
                    "stressValueDescriptorIndex",
                    "bodyBatteryValueDescriptorIndex",
                )
                if descriptor.get(name) is not None
            ),
            None,
        )
        if index is not None:
            return int(index)
    return default


def _epoch_local(value: Any, tz: ZoneInfo) -> datetime | None:
    numeric = as_number(value)
    if numeric is None:
        return None
    seconds = numeric / 1000.0 if abs(numeric) > 10_000_000_000 else numeric
    try:
        return datetime.fromtimestamp(seconds, timezone.utc).astimezone(tz)
    except (OSError, OverflowError, ValueError):
        return None


def _parse_local_datetime(value: Any, target: date, tz: ZoneInfo) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) or str(value).strip().isdigit():
        return _epoch_local(value, tz)
    text = str(value).strip().replace("Z", "+00:00")
    if len(text) in {5, 8} and len(text) >= 3 and text[2] == ":":
        try:
            parsed_time = datetime.strptime(
                text,
                "%H:%M" if len(text) == 5 else "%H:%M:%S",
            ).time()
        except ValueError:
            return None
        return datetime.combine(target, parsed_time, tzinfo=tz)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def _parse_gmt_datetime(value: Any, tz: ZoneInfo) -> datetime | None:
    """Parse Garmin's timezone-less `*GMT` timestamps as UTC, not local time."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) or str(value).strip().isdigit():
        return _epoch_local(value, tz)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(tz)


def _cadence_seconds(timestamps: list[datetime]) -> float:
    gaps = [
        (current - previous).total_seconds()
        for previous, current in zip(timestamps, timestamps[1:])
        if 0 < (current - previous).total_seconds() <= MAX_CADENCE_SECONDS
    ]
    return round(float(median(gaps)), 1) if gaps else FALLBACK_CADENCE_SECONDS


def _merge_intervals(
    intervals: list[tuple[datetime, datetime, set[str]]],
    tolerance_seconds: float = 0.0,
) -> list[tuple[datetime, datetime, set[str]]]:
    merged: list[tuple[datetime, datetime, set[str]]] = []
    for start, end, causes in sorted(intervals, key=lambda item: item[0]):
        if end <= start:
            continue
        if not merged:
            merged.append((start, end, set(causes)))
            continue
        prior_start, prior_end, prior_causes = merged[-1]
        if start <= prior_end + timedelta(seconds=tolerance_seconds):
            merged[-1] = (prior_start, max(prior_end, end), prior_causes | set(causes))
        else:
            merged.append((start, end, set(causes)))
    return merged


def _interval_minutes(start: datetime, end: datetime) -> float:
    return max(0.0, (end - start).total_seconds() / 60.0)


def _bounded_run(start: datetime, end: datetime, causes: set[str]) -> dict:
    return {
        "start_local": start.isoformat(timespec="minutes"),
        "end_local": end.isoformat(timespec="minutes"),
        "duration_minutes": round(_interval_minutes(start, end), 1),
        "evidence": sorted(causes),
    }


def _stress_coverage(
    payload: dict,
    tz: ZoneInfo,
    target: date,
    latest_allowed: datetime | None,
    expected_cutoff: datetime | None,
) -> dict:
    descriptors = payload.get("stressValueDescriptorsDTOList") or payload.get(
        "stressValueDescriptorDTOList"
    )
    timestamp_index = _descriptor_index(descriptors, ("timestamp",), 0)
    value_index = _descriptor_index(descriptors, ("stresslevel", "stress"), 1)
    rows: dict[datetime, float] = {}
    malformed = 0
    outside_target_date = 0
    after_cutoff = 0
    for row in payload.get("stressValuesArray") or []:
        if not isinstance(row, (list, tuple)) or len(row) <= max(timestamp_index, value_index):
            malformed += 1
            continue
        stamp = _epoch_local(row[timestamp_index], tz)
        value = as_number(row[value_index])
        if stamp is None or value is None:
            malformed += 1
            continue
        if stamp.date() != target:
            outside_target_date += 1
            continue
        if latest_allowed is not None and stamp > latest_allowed:
            after_cutoff += 1
            continue
        rows[stamp] = value

    ordered = sorted(rows.items())
    timestamps = [stamp for stamp, _ in ordered]
    cadence = _cadence_seconds(timestamps)
    unavailable: list[tuple[datetime, datetime, set[str]]] = []
    for stamp, value in ordered:
        if value < 0:
            unavailable.append(
                (stamp, stamp + timedelta(seconds=cadence), {"negative_stress_sentinel"})
            )
    for (previous, _), (current, _) in zip(ordered, ordered[1:]):
        gap = (current - previous).total_seconds()
        if gap > cadence * 1.5:
            unavailable.append(
                (
                    previous + timedelta(seconds=cadence),
                    current,
                    {"missing_timestamp_slots"},
                )
            )
    merged = _merge_intervals(unavailable, tolerance_seconds=cadence * 0.25)
    material = [
        item
        for item in merged
        if _interval_minutes(item[0], item[1]) >= MATERIAL_UNAVAILABLE_MINUTES
    ]
    midnight = datetime.combine(target, datetime.min.time(), tzinfo=tz)
    cutoff_on_target = bool(
        expected_cutoff is not None and expected_cutoff.date() == target
    )
    expected_slots = (
        int(
            max(0.0, (expected_cutoff - midnight).total_seconds())
            / FALLBACK_CADENCE_SECONDS
        )
        + 1
        if cutoff_on_target
        else 0
    )
    valid_count = sum(value >= 0 for _, value in ordered)
    parsed_density_pct = (
        min(100.0, len(ordered) / expected_slots * 100.0)
        if expected_slots
        else 0.0
    )
    valid_density_pct = (
        min(100.0, valid_count / expected_slots * 100.0)
        if expected_slots
        else 0.0
    )
    start_gap_minutes = (
        max(0.0, _interval_minutes(midnight, timestamps[0]))
        if timestamps
        else None
    )
    tail_gap_minutes = (
        max(
            0.0,
            _interval_minutes(
                timestamps[-1] + timedelta(seconds=cadence),
                expected_cutoff,
            ),
        )
        if timestamps and cutoff_on_target
        else None
    )
    boundary_tolerance_minutes = max(5.0, cadence / 60.0 * 1.5)
    start_boundary_complete = bool(
        start_gap_minutes is not None
        and start_gap_minutes <= boundary_tolerance_minutes
    )
    end_boundary_complete = bool(
        tail_gap_minutes is not None
        and tail_gap_minutes <= boundary_tolerance_minutes
    )
    cadence_credible = bool(
        len(ordered) >= 2 and cadence <= MAX_POSITIVE_CADENCE_SECONDS
    )
    cutoff_reaches_decision_day = bool(
        cutoff_on_target and expected_cutoff.hour >= MIN_POSITIVE_CUTOFF_HOUR
    )
    series_sufficient = bool(
        cutoff_reaches_decision_day
        and start_boundary_complete
        and end_boundary_complete
        and cadence_credible
        and valid_density_pct >= MIN_POSITIVE_VALID_DENSITY_PCT
    )
    return {
        "sample_count": len(ordered),
        "valid_sample_count": valid_count,
        "sentinel_sample_count": sum(value < 0 for _, value in ordered),
        "malformed_sample_count": malformed,
        "discarded_outside_target_date_count": outside_target_date,
        "discarded_after_cutoff_count": after_cutoff,
        "cadence_seconds": cadence,
        "first_sample_local": timestamps[0].isoformat(timespec="minutes") if timestamps else None,
        "last_sample_local": timestamps[-1].isoformat(timespec="minutes") if timestamps else None,
        "material_unavailable_threshold_minutes": MATERIAL_UNAVAILABLE_MINUTES,
        "material_unavailable_run_count": len(material),
        "material_unavailable_minutes": round(
            sum(_interval_minutes(start, end) for start, end, _ in material),
            1,
        ),
        "material_unavailable_runs": [
            _bounded_run(start, end, causes) for start, end, causes in material[:24]
        ],
        "positive_use_coverage": {
            "expected_start_local": midnight.isoformat(timespec="minutes"),
            "expected_cutoff_local": expected_cutoff.isoformat(timespec="minutes")
            if cutoff_on_target
            else None,
            "minimum_cutoff_hour": MIN_POSITIVE_CUTOFF_HOUR,
            "expected_sample_slots_at_3min": expected_slots,
            "parsed_density_pct": round(parsed_density_pct, 1),
            "valid_density_pct": round(valid_density_pct, 1),
            "minimum_valid_density_pct": MIN_POSITIVE_VALID_DENSITY_PCT,
            "start_gap_minutes": round(start_gap_minutes, 1)
            if start_gap_minutes is not None
            else None,
            "tail_gap_minutes": round(tail_gap_minutes, 1)
            if tail_gap_minutes is not None
            else None,
            "boundary_tolerance_minutes": round(boundary_tolerance_minutes, 1),
            "start_boundary_complete": start_boundary_complete,
            "end_boundary_complete": end_boundary_complete,
            "cadence_credible": cadence_credible,
            "cutoff_reaches_decision_day": cutoff_reaches_decision_day,
            "series_sufficient_for_low_stress_reward": series_sufficient,
        },
        "_ordered": ordered,
        "_material_intervals": material,
    }


def _body_battery_coverage(
    payload: dict,
    tz: ZoneInfo,
    target: date,
    latest_allowed: datetime | None,
) -> dict:
    descriptors = payload.get("bodyBatteryValueDescriptorsDTOList") or payload.get(
        "bodyBatteryValueDescriptorDTOList"
    )
    rows = payload.get("bodyBatteryValuesArray") or []
    fallback_level_index = 2 if any(
        isinstance(row, (list, tuple)) and len(row) >= 3 for row in rows
    ) else 1
    timestamp_index = _descriptor_index(descriptors, ("timestamp",), 0)
    status_index = _descriptor_index(descriptors, ("bodybatterystatus", "status"), 1)
    level_index = _descriptor_index(
        descriptors,
        ("bodybatterylevel", "bodybattery", "level"),
        fallback_level_index,
    )
    samples: dict[datetime, tuple[str | None, float | None]] = {}
    malformed = 0
    outside_target_date = 0
    after_cutoff = 0
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) <= max(timestamp_index, level_index):
            malformed += 1
            continue
        stamp = _epoch_local(row[timestamp_index], tz)
        if stamp is None:
            malformed += 1
            continue
        if stamp.date() != target:
            outside_target_date += 1
            continue
        if latest_allowed is not None and stamp > latest_allowed:
            after_cutoff += 1
            continue
        status = str(row[status_index]) if len(row) > status_index and row[status_index] else None
        samples[stamp] = (status, as_number(row[level_index]))

    ordered = sorted(samples.items())
    timestamps = [stamp for stamp, _ in ordered]
    cadence = _cadence_seconds(timestamps)
    unavailable: list[tuple[datetime, datetime, set[str]]] = []
    for stamp, (_, level) in ordered:
        if level is None:
            unavailable.append(
                (stamp, stamp + timedelta(seconds=cadence), {"body_battery_value_missing"})
            )
    for (previous, _), (current, _) in zip(ordered, ordered[1:]):
        gap = (current - previous).total_seconds()
        if gap > cadence * 1.5:
            unavailable.append(
                (
                    previous + timedelta(seconds=cadence),
                    current,
                    {"missing_timestamp_slots"},
                )
            )
    merged = _merge_intervals(unavailable, tolerance_seconds=cadence * 0.25)
    material = [
        item
        for item in merged
        if _interval_minutes(item[0], item[1]) >= MATERIAL_UNAVAILABLE_MINUTES
    ]
    return {
        "sample_count": len(ordered),
        "valid_level_sample_count": sum(level is not None for _, (_, level) in ordered),
        "missing_level_sample_count": sum(level is None for _, (_, level) in ordered),
        "malformed_sample_count": malformed,
        "discarded_outside_target_date_count": outside_target_date,
        "discarded_after_cutoff_count": after_cutoff,
        "cadence_seconds": cadence,
        "first_sample_local": timestamps[0].isoformat(timespec="minutes") if timestamps else None,
        "last_sample_local": timestamps[-1].isoformat(timespec="minutes") if timestamps else None,
        "material_unavailable_run_count": len(material),
        "material_unavailable_minutes": round(
            sum(_interval_minutes(start, end) for start, end, _ in material),
            1,
        ),
        "material_unavailable_runs": [
            _bounded_run(start, end, causes) for start, end, causes in material[:24]
        ],
    }


def _heart_rate_coverage(
    record: dict,
    tz: ZoneInfo,
    target: date,
    as_of: datetime,
) -> dict:
    payload = record.get("data") if isinstance(record.get("data"), dict) else {}
    payload_date = _safe_date(payload.get("calendarDate") or payload.get("date"))
    endpoint_usable = bool(
        payload
        and record.get("status") == "success"
        and record.get("ok") is True
        and payload_date == target
        and isinstance(payload.get("heartRateValues"), list)
        and payload.get("heartRateValues")
    )
    descriptors = payload.get("heartRateValueDescriptors") or payload.get(
        "heartRateValueDescriptorsDTOList"
    )
    timestamp_index = _descriptor_index(descriptors, ("timestamp",), 0)
    value_index = _descriptor_index(
        descriptors,
        ("heartrate", "heart_rate", "heartRate", "bpm"),
        1,
    )
    gmt_cutoff = _parse_gmt_datetime(payload.get("endTimestampGMT"), tz)
    local_cutoff = _parse_local_datetime(
        payload.get("endTimestampLocal"),
        target,
        tz,
    )
    gmt_start = _parse_gmt_datetime(payload.get("startTimestampGMT"), tz)
    local_start = _parse_local_datetime(
        payload.get("startTimestampLocal"),
        target,
        tz,
    )
    credible_cutoffs = [
        item
        for item in (gmt_cutoff, local_cutoff, as_of)
        if item is not None and item.date() == target and item <= as_of
    ]
    endpoint_cutoff = (
        min(credible_cutoffs)
        if credible_cutoffs
        else (as_of if as_of.date() == target else None)
    )
    credible_starts = [
        item
        for item in (gmt_start, local_start)
        if item is not None
        and item.date() == target
        and (endpoint_cutoff is None or item <= endpoint_cutoff)
    ]
    endpoint_start = min(credible_starts) if credible_starts else None

    rows: dict[datetime, float | None] = {}
    malformed = 0
    outside_target_date = 0
    after_cutoff = 0
    duplicate_timestamps = 0
    if endpoint_usable:
        for row in payload.get("heartRateValues") or []:
            if not isinstance(row, (list, tuple)) or len(row) <= max(
                timestamp_index,
                value_index,
            ):
                malformed += 1
                continue
            stamp = _epoch_local(row[timestamp_index], tz)
            if stamp is None:
                malformed += 1
                continue
            if stamp.date() != target:
                outside_target_date += 1
                continue
            if endpoint_cutoff is not None and stamp > endpoint_cutoff:
                after_cutoff += 1
                continue
            if stamp in rows:
                duplicate_timestamps += 1
            value = as_number(row[value_index])
            rows[stamp] = value if value is not None and value >= 0 else None

    ordered = sorted(rows.items())
    timestamps = [stamp for stamp, _ in ordered]
    cadence = _cadence_seconds(timestamps)
    unavailable: list[tuple[datetime, datetime, set[str]]] = []
    if timestamps and endpoint_start is not None:
        if timestamps[0] > endpoint_start + timedelta(seconds=cadence * 1.5):
            unavailable.append(
                (
                    endpoint_start,
                    timestamps[0],
                    {"heart_rate_uncovered_prefix_after_endpoint_start"},
                )
            )
    for stamp, value in ordered:
        if value is None:
            unavailable.append(
                (
                    stamp,
                    min(stamp + timedelta(seconds=cadence), endpoint_cutoff)
                    if endpoint_cutoff is not None
                    else stamp + timedelta(seconds=cadence),
                    {"heart_rate_value_missing"},
                )
            )
    for (previous, _), (current, _) in zip(ordered, ordered[1:]):
        gap = (current - previous).total_seconds()
        if gap > cadence * 1.5:
            unavailable.append(
                (
                    previous + timedelta(seconds=cadence),
                    current,
                    {"heart_rate_timestamp_slots_missing"},
                )
            )
    if timestamps and endpoint_cutoff is not None:
        tail_start = timestamps[-1] + timedelta(seconds=cadence)
        if endpoint_cutoff > tail_start + timedelta(seconds=cadence * 0.5):
            unavailable.append(
                (
                    tail_start,
                    endpoint_cutoff,
                    {"heart_rate_uncovered_tail_before_endpoint_cutoff"},
                )
            )
    merged = _merge_intervals(unavailable, tolerance_seconds=cadence * 0.25)
    material = [
        item
        for item in merged
        if _interval_minutes(item[0], item[1]) >= MATERIAL_UNAVAILABLE_MINUTES
    ]
    public_runs = []
    for start, end, causes in material[:24]:
        row = _bounded_run(start, end, causes)
        row.update(
            {
                "boundary_basis": "heart_rate_sample_transition",
                "boundary_resolution_seconds": cadence,
                "start_boundary_observed": (
                    "heart_rate_uncovered_prefix_after_endpoint_start" not in causes
                ),
                "end_boundary_observed": (
                    "heart_rate_uncovered_tail_before_endpoint_cutoff" not in causes
                ),
            }
        )
        public_runs.append(row)
    return {
        "endpoint_status": _safe_attempt_status(record.get("status")),
        "endpoint_usable": endpoint_usable,
        "payload_date": payload_date.isoformat() if payload_date else None,
        "sample_count": len(ordered),
        "measured_sample_count": sum(value is not None for _, value in ordered),
        "unavailable_sample_count": sum(value is None for _, value in ordered),
        "malformed_sample_count": malformed,
        "discarded_outside_target_date_count": outside_target_date,
        "discarded_after_cutoff_count": after_cutoff,
        "duplicate_timestamp_count": duplicate_timestamps,
        "cadence_seconds": cadence,
        "first_sample_local": timestamps[0].isoformat(timespec="minutes")
        if timestamps
        else None,
        "last_sample_local": timestamps[-1].isoformat(timespec="minutes")
        if timestamps
        else None,
        "endpoint_cutoff_local": endpoint_cutoff.isoformat(timespec="minutes")
        if endpoint_cutoff
        else None,
        "endpoint_start_local": endpoint_start.isoformat(timespec="minutes")
        if endpoint_start
        else None,
        "material_unavailable_run_count": len(material),
        "material_unavailable_minutes": round(
            sum(_interval_minutes(start, end) for start, end, _ in material),
            1,
        ),
        "material_unavailable_runs": public_runs,
        "interpretation": (
            "A direct Fenix wrist-HR gap confirms optical-HR measurement unavailability at sample-transition resolution. It does not identify physical watch removal or distinguish dress-watch use, a loose strap, showering, charging, or another contact interruption."
        ),
        "_ordered": ordered,
        "_material_intervals": material,
    }


def _candidate_windows(feedback: dict) -> list[dict]:
    candidates: list[dict] = []

    def add(value: Any) -> None:
        if isinstance(value, dict):
            candidates.append(value)
        elif isinstance(value, list):
            candidates.extend(item for item in value if isinstance(item, dict))

    wearable = feedback.get("wearable_context")
    if isinstance(wearable, dict):
        add(wearable.get("off_wrist_window"))
        add(wearable.get("off_wrist_windows"))
    for key in ("wearable_off_window", "wearable_off_windows", "off_wrist_window", "off_wrist_windows"):
        add(feedback.get(key))
    for entry in feedback.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        nested = entry.get("wearable_context")
        if isinstance(nested, dict):
            add(nested.get("off_wrist_window"))
            add(nested.get("off_wrist_windows"))
        for key in ("wearable_off_window", "wearable_off_windows", "off_wrist_window", "off_wrist_windows"):
            add(entry.get(key))
    return candidates


def _safe_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    try:
        return parse_date(value)
    except (TypeError, ValueError):
        return None


def _reason_category(value: Any) -> str:
    normalized = str(value or "other_reported").strip().lower().replace(" ", "_")
    return normalized if normalized in ALLOWED_REASON_CATEGORIES else "other_reported"


def _replacement_category(value: Any) -> str:
    normalized = str(value or "unknown").strip().lower().replace(" ", "_")
    return normalized if normalized in ALLOWED_REPLACEMENT_CATEGORIES else "other"


def _manual_windows(
    feedback: dict,
    target: date,
    tz: ZoneInfo,
    evidence_cutoff: datetime | None,
    as_of: datetime | None,
) -> list[dict]:
    outer_date_raw = feedback.get("date")
    outer_date = _safe_date(outer_date_raw)
    if outer_date_raw not in (None, "") and outer_date != target:
        return []
    results: list[dict] = []
    for index, item in enumerate(_candidate_windows(feedback)):
        embedded_raw = item.get("date") or item.get("window_date")
        embedded_date = _safe_date(embedded_raw) if embedded_raw else target
        status = str(item.get("status") or "").strip().lower()
        result = {
            "window_index": index,
            "status": "ignored_unconfirmed_status",
            "occurrence_confirmed": False,
            "date": embedded_date.isoformat() if embedded_date else None,
            "start_local": None,
            "end_local": None,
            "duration_minutes": None,
            "reason_category": _reason_category(
                item.get("reason_category") or item.get("reason")
            ),
            "replacement_device_category": _replacement_category(
                item.get("replacement_device_category")
                or item.get("replacement_device")
                or "unknown"
            ),
            "source": "athlete_report",
        }
        if status not in CONFIRMED_STATUSES:
            results.append(result)
            continue
        result["occurrence_confirmed"] = True
        if embedded_date != target:
            result["status"] = "ignored_wrong_date"
            results.append(result)
            continue
        start = _parse_local_datetime(
            item.get("start_local") or item.get("off_wrist_start_local"),
            target,
            tz,
        )
        end = _parse_local_datetime(
            item.get("end_local") or item.get("off_wrist_end_local"),
            target,
            tz,
        )
        if start is None or end is None:
            result["status"] = "reported_without_complete_timing"
            results.append(result)
            continue
        result["start_local"] = start.isoformat(timespec="minutes")
        result["end_local"] = end.isoformat(timespec="minutes")
        if start.date() != target or end.date() != target:
            result["status"] = "invalid_boundary_date"
            results.append(result)
            continue
        if end <= start:
            result["status"] = "invalid_nonpositive_duration"
            results.append(result)
            continue
        duration = _interval_minutes(start, end)
        result["duration_minutes"] = round(duration, 1)
        if duration > 18 * 60:
            result["status"] = "invalid_excessive_duration"
            results.append(result)
            continue
        if as_of is not None and end > as_of:
            result["status"] = "future_relative_to_wall_clock"
            results.append(result)
            continue
        if evidence_cutoff is not None and start > evidence_cutoff:
            result["status"] = "future_relative_to_endpoint_cutoff"
            results.append(result)
            continue
        result["status"] = "confirmed_valid"
        result["_start"] = start
        result["_end"] = end
        results.append(result)
    return results


def _recurring_context(context: dict, target: date) -> list[dict]:
    wearable = (context.get("athlete") or {}).get("wearable_context") or {}
    results: list[dict] = []
    for item in wearable.get("reported_recurring_exceptions") or []:
        if not isinstance(item, dict):
            continue
        weekday = item.get("weekday")
        try:
            normalized_weekday = int(weekday) if weekday is not None else None
        except (TypeError, ValueError):
            normalized_weekday = None
        if normalized_weekday is not None and normalized_weekday != target.weekday():
            continue
        frequency = str(item.get("frequency") or "reported_pattern").strip().lower()
        if frequency not in ALLOWED_FREQUENCY_CATEGORIES:
            frequency = "reported_pattern"
        results.append(
            {
                "label": "reported recurring wearable exception",
                "weekday": normalized_weekday,
                "frequency": frequency,
                "reason_category": _reason_category(item.get("reason_category")),
                "replacement_device_category": _replacement_category(
                    item.get("replacement_device_category")
                ),
                "occurrence_confirmed_for_date": False,
                "exact_timing_known": False,
                "interpretation": (
                    "Recurring context may explain a matching gap, but it cannot instantiate or time a dated off-wrist window."
                ),
            }
        )
    return results


def _overlap_minutes(
    start: datetime,
    end: datetime,
    intervals: list[tuple[datetime, datetime, set[str]]],
) -> float:
    overlaps = []
    for interval_start, interval_end, _ in intervals:
        overlap_start = max(start, interval_start)
        overlap_end = min(end, interval_end)
        if overlap_end > overlap_start:
            overlaps.append((overlap_start, overlap_end, {"overlap"}))
    merged = _merge_intervals(overlaps)
    return sum(_interval_minutes(item[0], item[1]) for item in merged)


def _manual_alignment(manual_windows: list[dict], stress: dict) -> list[dict]:
    alignments: list[dict] = []
    ordered = stress.get("_ordered") or []
    unavailable = stress.get("_material_intervals") or []
    for item in manual_windows:
        if item.get("status") != "confirmed_valid":
            continue
        start = item["_start"]
        end = item["_end"]
        duration = _interval_minutes(start, end)
        overlap = _overlap_minutes(start, end, unavailable)
        valid_samples = sum(start <= stamp < end and value >= 0 for stamp, value in ordered)
        unavailable_share = overlap / duration * 100 if duration else 0.0
        if valid_samples:
            agreement = "discordant_valid_stress_samples_present"
        elif overlap > 0:
            agreement = "consistent_with_unavailable_series"
        else:
            agreement = "insufficient_series_evidence"
        alignments.append(
            {
                "window_index": item.get("window_index"),
                "sensor_agreement": agreement,
                "unavailable_overlap_minutes": round(overlap, 1),
                "unavailable_share_pct": round(unavailable_share, 1),
                "valid_stress_samples_inside": valid_samples,
            }
        )
    return alignments


def _material_run_attribution(
    stress: dict,
    manual_windows: list[dict],
    optical_hr: dict | None = None,
) -> dict:
    """Separate measurement confirmation, physical wear state, and root cause."""
    windows = [
        (item["_start"], item["_end"], {str(item.get("window_index"))})
        for item in manual_windows
        if item.get("status") == "confirmed_valid"
    ]
    hr_intervals = (
        (optical_hr or {}).get("_material_intervals") or []
        if (optical_hr or {}).get("endpoint_usable")
        else []
    )
    hr_samples = (
        (optical_hr or {}).get("_ordered") or []
        if (optical_hr or {}).get("endpoint_usable")
        else []
    )
    alignment_tolerance_seconds = max(
        float(stress.get("cadence_seconds") or FALLBACK_CADENCE_SECONDS),
        float((optical_hr or {}).get("cadence_seconds") or 0),
    ) * 1.5
    rows: list[dict] = []
    total_manual_confirmed = 0.0
    total_hr_direct_overlap = 0.0
    total_measurement_confirmed = 0.0
    total_unconfirmed_measurement = 0.0
    total_unknown_wear_state = 0.0
    total_cause_attributed = 0.0
    total_cause_unattributed = 0.0
    for run_start, run_end, causes in stress.get("_material_intervals") or []:
        overlaps: list[tuple[datetime, datetime, set[str]]] = []
        matched_indices: set[int] = set()
        for window_start, window_end, index_values in windows:
            overlap_start = max(run_start, window_start)
            overlap_end = min(run_end, window_end)
            if overlap_end <= overlap_start:
                continue
            overlaps.append((overlap_start, overlap_end, set(index_values)))
            matched_indices.update(int(value) for value in index_values)
        merged_manual = _merge_intervals(overlaps)
        manual_confirmed_minutes = sum(
            _interval_minutes(start, end) for start, end, _ in merged_manual
        )
        duration = _interval_minutes(run_start, run_end)

        hr_overlaps: list[tuple[datetime, datetime, set[str]]] = []
        hr_supported: list[tuple[datetime, datetime, set[str]]] = []
        matched_hr_causes: set[str] = set()
        for hr_start, hr_end, hr_causes in hr_intervals:
            overlap_start = max(run_start, hr_start)
            overlap_end = min(run_end, hr_end)
            if overlap_end > overlap_start:
                hr_overlaps.append((overlap_start, overlap_end, set(hr_causes)))
            supported_start = max(
                run_start,
                hr_start - timedelta(seconds=alignment_tolerance_seconds),
            )
            supported_end = min(
                run_end,
                hr_end + timedelta(seconds=alignment_tolerance_seconds),
            )
            if supported_end > supported_start:
                matched_hr_causes.update(hr_causes)
                hr_supported.append(
                    (supported_start, supported_end, {"sample_resolution_alignment"})
                )
        hr_direct_overlap_minutes = sum(
            _interval_minutes(start, end)
            for start, end, _ in _merge_intervals(hr_overlaps)
        )
        merged_hr_direct = _merge_intervals(hr_overlaps)
        measurement_support = _merge_intervals([*merged_manual, *merged_hr_direct])
        measurement_confirmed_minutes = min(
            duration,
            sum(
                _interval_minutes(start, end)
                for start, end, _ in measurement_support
            ),
        )
        unconfirmed_measurement_minutes = max(
            0.0,
            duration - measurement_confirmed_minutes,
        )
        hr_corroborated = bool(merged_hr_direct)
        hr_measured_samples_inside = sum(
            run_start <= stamp < run_end and value is not None
            for stamp, value in hr_samples
        )
        if hr_corroborated and hr_measured_samples_inside:
            optical_hr_state = "mixed"
        elif hr_corroborated:
            optical_hr_state = "unavailable"
        elif (optical_hr or {}).get("endpoint_usable") and hr_measured_samples_inside:
            optical_hr_state = "available"
        else:
            optical_hr_state = "unknown"

        if manual_confirmed_minutes >= duration - 0.1 and duration > 0:
            attribution = "confirmed_intentional_off_wrist"
            device_wear_state = "not_worn"
            wear_confirmation = "athlete_confirmed_exact_window"
        elif unconfirmed_measurement_minutes <= 0.1 and hr_corroborated:
            attribution = "confirmed_optical_hr_measurement_unavailability"
            device_wear_state = (
                "mixed_or_unknown" if manual_confirmed_minutes > 0 else "unknown"
            )
            wear_confirmation = "unconfirmed"
            unconfirmed_measurement_minutes = 0.0
        elif measurement_confirmed_minutes > 0:
            attribution = (
                "partially_confirmed_with_unexplained_remainder"
                if manual_confirmed_minutes > 0
                else "partially_corroborated_optical_hr_with_unexplained_remainder"
            )
            device_wear_state = (
                "mixed_or_unknown" if manual_confirmed_minutes > 0 else "unknown"
            )
            wear_confirmation = (
                "partially_athlete_confirmed"
                if manual_confirmed_minutes > 0
                else "unconfirmed"
            )
        else:
            attribution = "unexplained_internal_unavailability"
            device_wear_state = "unknown"
            wear_confirmation = "unconfirmed"

        cause_attributed_minutes = manual_confirmed_minutes
        cause_unattributed_minutes = max(0.0, duration - cause_attributed_minutes)
        row = _bounded_run(run_start, run_end, causes)
        row.update(
            {
                "attribution": attribution,
                "measurement_availability": {
                    "signal": (
                        "fenix_wrist_optical_hr"
                        if hr_corroborated or manual_confirmed_minutes > 0
                        else "garmin_all_day_stress"
                    ),
                    "state": (
                        optical_hr_state if hr_corroborated else "unavailable"
                    ),
                    "confirmation": (
                        "direct_garmin_heart_rate_series"
                        if hr_corroborated
                        else (
                            "athlete_confirmed_off_wrist_window"
                            if manual_confirmed_minutes > 0
                            else "direct_garmin_stress_series"
                        )
                    ),
                    "boundary_basis": (
                        (
                            "heart_rate_stop_with_open_tail_at_endpoint_cutoff"
                            if "heart_rate_uncovered_tail_before_endpoint_cutoff"
                            in matched_hr_causes
                            else "open_endpoint_prefix_until_heart_rate_start"
                            if "heart_rate_uncovered_prefix_after_endpoint_start"
                            in matched_hr_causes
                            else "heart_rate_sample_transition"
                        )
                        if hr_corroborated
                        else (
                            "athlete_reported_clock_time"
                            if manual_confirmed_minutes > 0
                            else "stress_series_only"
                        )
                    ),
                    "boundary_resolution_seconds": (
                        (optical_hr or {}).get("cadence_seconds")
                        if hr_corroborated
                        else stress.get("cadence_seconds")
                    ),
                    "end_boundary_observed": (
                        "heart_rate_uncovered_tail_before_endpoint_cutoff"
                        not in matched_hr_causes
                        if hr_corroborated
                        else None
                    ),
                    "start_boundary_observed": (
                        "heart_rate_uncovered_prefix_after_endpoint_start"
                        not in matched_hr_causes
                        if hr_corroborated
                        else None
                    ),
                },
                "optical_hr_measurement": {
                    "signal": "fenix_wrist_optical_hr",
                    "state": optical_hr_state,
                    "confirmation": (
                        "direct_garmin_heart_rate_series"
                        if (optical_hr or {}).get("endpoint_usable")
                        else "unknown_endpoint_unavailable"
                    ),
                    "measured_sample_count_inside_stress_gap": hr_measured_samples_inside,
                    "interpretation": (
                        "Wrist HR continued through this stress-only gap; do not classify it as optical-contact loss."
                        if optical_hr_state == "available"
                        else "Wrist-HR loss corroborates only part of this stress gap; valid HR samples keep the remaining portion stress-specific."
                        if optical_hr_state == "mixed"
                        else "Wrist-HR measurement unavailability corroborates this stress gap."
                        if optical_hr_state == "unavailable"
                        else "The optical-HR state cannot be established for this stress gap."
                    ),
                },
                "device_wear_state": {
                    "state": device_wear_state,
                    "confirmation": wear_confirmation,
                },
                "cause_attribution": {
                    "status": (
                        "complete"
                        if cause_attributed_minutes >= duration - 0.1
                        else "partial"
                        if cause_attributed_minutes > 0
                        else "none"
                    ),
                    "category": (
                        "athlete_reported_off_wrist_reason"
                        if cause_attributed_minutes > 0
                        else "unknown"
                    ),
                },
                "confirmed_overlap_minutes": round(manual_confirmed_minutes, 1),
                "optical_hr_direct_overlap_minutes": round(
                    hr_direct_overlap_minutes,
                    1,
                ),
                "measurement_confirmed_minutes": round(
                    measurement_confirmed_minutes,
                    1,
                ),
                "unexplained_minutes": round(unconfirmed_measurement_minutes, 1),
                "matched_window_indices": sorted(matched_indices),
            }
        )
        rows.append(row)
        total_manual_confirmed += manual_confirmed_minutes
        total_hr_direct_overlap += hr_direct_overlap_minutes
        total_measurement_confirmed += measurement_confirmed_minutes
        total_unconfirmed_measurement += unconfirmed_measurement_minutes
        total_unknown_wear_state += max(0.0, duration - manual_confirmed_minutes)
        total_cause_attributed += cause_attributed_minutes
        total_cause_unattributed += cause_unattributed_minutes
    return {
        "runs": rows,
        "summary": {
            "material_run_count": len(rows),
            "fully_attributed_run_count": sum(
                row["attribution"] == "confirmed_intentional_off_wrist"
                for row in rows
            ),
            "partially_attributed_run_count": sum(
                row["attribution"]
                == "partially_confirmed_with_unexplained_remainder"
                for row in rows
            ),
            "unattributed_run_count": sum(
                row["attribution"] == "unexplained_internal_unavailability"
                for row in rows
            ),
            "measurement_confirmed_run_count": sum(
                row.get("unexplained_minutes", 0) <= 0.1 for row in rows
            ),
            "optical_hr_corroborated_run_count": sum(
                (row.get("measurement_availability") or {}).get("confirmation")
                == "direct_garmin_heart_rate_series"
                for row in rows
            ),
            "stress_only_run_count_with_hr_available": sum(
                (row.get("optical_hr_measurement") or {}).get("state")
                == "available"
                for row in rows
            ),
            "mixed_optical_hr_run_count": sum(
                (row.get("optical_hr_measurement") or {}).get("state") == "mixed"
                for row in rows
            ),
            "confirmed_overlap_minutes": round(total_manual_confirmed, 1),
            "confirmed_off_wrist_minutes": round(total_manual_confirmed, 1),
            "optical_hr_direct_overlap_minutes": round(total_hr_direct_overlap, 1),
            "confirmed_measurement_unavailable_minutes": round(
                total_measurement_confirmed,
                1,
            ),
            "unconfirmed_measurement_unavailable_minutes": round(
                total_unconfirmed_measurement,
                1,
            ),
            "unknown_physical_wear_state_minutes": round(
                total_unknown_wear_state,
                1,
            ),
            "cause_attributed_minutes": round(total_cause_attributed, 1),
            "cause_unattributed_minutes": round(total_cause_unattributed, 1),
            "unexplained_material_minutes": round(
                total_unconfirmed_measurement,
                1,
            ),
            "recurring_context_is_not_attribution": True,
        },
    }


def _manual_window_summary(manual_windows: list[dict]) -> dict:
    valid = [item for item in manual_windows if item.get("status") == "confirmed_valid"]
    intervals = [
        (item["_start"], item["_end"], {"athlete_confirmed"}) for item in valid
    ]
    merged = _merge_intervals(intervals)
    raw_minutes = sum(_interval_minutes(item["_start"], item["_end"]) for item in valid)
    union_minutes = sum(_interval_minutes(start, end) for start, end, _ in merged)
    return {
        "confirmed_valid_window_count": len(valid),
        "union_window_count": len(merged),
        "raw_duration_minutes": round(raw_minutes, 1),
        "union_duration_minutes": round(union_minutes, 1),
        "overlap_deduplicated_minutes": round(max(0.0, raw_minutes - union_minutes), 1),
    }


def _strip_private_fields(item: dict) -> dict:
    return {key: value for key, value in item.items() if not key.startswith("_")}


def _safe_iso_timestamp(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.isoformat(timespec="seconds")


def _safe_attempt_status(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    return normalized if normalized in ALLOWED_ATTEMPT_STATUSES else "unknown"


def _safe_attempt_summary(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, Any] = {}
    status = _safe_attempt_status(value.get("status"))
    if status:
        result["status"] = status
    if isinstance(value.get("ok"), bool):
        result["ok"] = value["ok"]
    attempted_at = _safe_iso_timestamp(value.get("attempted_at"))
    if attempted_at is not None:
        result["attempted_at"] = attempted_at
    error = str(value.get("error") or "").strip().lower().replace("-", "_")
    if error:
        result["error_category"] = (
            error
            if error in ALLOWED_ATTEMPT_ERROR_CATEGORIES
            else "connector_error_redacted"
        )
    for key in ("expected_date", "response_date"):
        parsed_date = _safe_date(value.get(key))
        if parsed_date is not None:
            result[key] = parsed_date.isoformat()
    return result or None


def analyze_wearable_coverage(
    *,
    target_date: date,
    wellness_snapshot: dict,
    feedback: dict,
    context: dict,
    timezone_name: str = DEFAULT_TIMEZONE,
    as_of: datetime | None = None,
) -> dict:
    tz = ZoneInfo(timezone_name)
    record = _payload_record(wellness_snapshot, "get_all_day_stress")
    heart_rate_record = _payload_record(wellness_snapshot, "get_heart_rates")
    payload = record.get("data") if isinstance(record.get("data"), dict) else {}
    snapshot_date_raw = wellness_snapshot.get("date")
    snapshot_date = _safe_date(snapshot_date_raw)
    snapshot_date_matches = snapshot_date_raw in (None, "") or snapshot_date == target_date
    payload_date = _safe_date(payload.get("calendarDate") or payload.get("date")) if payload else None
    endpoint_record_usable = bool(
        payload
        and snapshot_date_matches
        and record.get("status") == "success"
        and record.get("ok") is True
        and payload_date == target_date
        and isinstance(payload.get("stressValuesArray"), list)
        and payload.get("stressValuesArray")
    )
    endpoint_cutoff = _parse_local_datetime(
        payload.get("endTimestampLocal") if payload else None,
        target_date,
        tz,
    )
    effective_as_of = as_of
    if effective_as_of is None:
        effective_as_of = datetime.now(tz)
    else:
        effective_as_of = (
            effective_as_of.replace(tzinfo=tz)
            if effective_as_of.tzinfo is None
            else effective_as_of.astimezone(tz)
        )
    cutoff_candidates = [
        item for item in (endpoint_cutoff, effective_as_of) if item is not None
    ]
    latest_allowed = min(cutoff_candidates) if cutoff_candidates else None
    effective_cutoff = (
        min(endpoint_cutoff, effective_as_of)
        if endpoint_cutoff is not None and effective_as_of is not None
        else endpoint_cutoff
    )
    stress = _stress_coverage(
        payload if endpoint_record_usable else {},
        tz,
        target_date,
        latest_allowed,
        effective_cutoff,
    )
    battery = _body_battery_coverage(
        payload if endpoint_record_usable else {},
        tz,
        target_date,
        latest_allowed,
    )
    optical_hr = _heart_rate_coverage(
        heart_rate_record,
        tz,
        target_date,
        effective_as_of,
    )
    endpoint_usable = bool(endpoint_record_usable and stress.get("sample_count"))
    manual = _manual_windows(
        feedback,
        target_date,
        tz,
        endpoint_cutoff,
        effective_as_of,
    )
    recurring = _recurring_context(context, target_date)
    alignments = _manual_alignment(manual, stress)
    manual_summary = _manual_window_summary(manual)
    material_gap = bool(stress.get("material_unavailable_run_count"))
    series_sufficient = bool(
        (stress.get("positive_use_coverage") or {}).get(
            "series_sufficient_for_low_stress_reward"
        )
    )
    exact_valid = [item for item in manual if item.get("status") == "confirmed_valid"]
    confirmed_union = _merge_intervals(
        [
            (item["_start"], item["_end"], {"athlete_confirmed"})
            for item in exact_valid
        ]
    )
    material_confirmed_window = any(
        _interval_minutes(start, end) >= MATERIAL_UNAVAILABLE_MINUTES
        for start, end, _ in confirmed_union
    )
    attribution = _material_run_attribution(stress, exact_valid, optical_hr)
    attribution_summary = attribution["summary"]
    confirmed_overlap_minutes = attribution_summary["confirmed_overlap_minutes"]
    unexplained_material_minutes = attribution_summary["unexplained_material_minutes"]
    confirmed_measurement_minutes = attribution_summary[
        "confirmed_measurement_unavailable_minutes"
    ]
    discordant = any(
        item.get("sensor_agreement") == "discordant_valid_stress_samples_present"
        for item in alignments
    )

    if not endpoint_usable:
        label = "endpoint_unavailable"
        confidence = "low"
        reasons = [
            "No target-date nonempty all-day stress series is available; manual context does not rewrite endpoint state."
        ]
    elif discordant:
        label = "discordant"
        confidence = "medium"
        reasons = [
            "An athlete-confirmed window contains valid Garmin stress samples; keep both sources and verify timing rather than overriding either."
        ]
    elif material_gap and unexplained_material_minutes > 0.1:
        if confirmed_overlap_minutes > 0:
            label = "partially_confirmed_with_unexplained_internal_unavailability"
            confidence = "high"
            reasons = [
                "An exact dated report overlaps part of the material unavailability, but unmatched run time remains unexplained and cannot inherit that cause."
            ]
        elif confirmed_measurement_minutes > 0:
            label = (
                "partially_confirmed_optical_hr_with_unexplained_internal_unavailability"
            )
            confidence = "high"
            reasons = [
                "Direct wrist-HR transitions corroborate portions of the material stress gaps, while wrist HR remains available through other portions or runs. Keep unmatched stress unavailability separate and do not infer optical-contact loss."
            ]
        elif exact_valid:
            label = "confirmed_interval_plus_unexplained_internal_unavailability"
            confidence = "high"
            reasons = [
                "The athlete confirmed an exact dated off-wrist interval, but it does not overlap the detected material run; the run remains unexplained."
            ]
        elif recurring:
            label = "unexplained_internal_unavailability_with_recurring_context"
            confidence = "high"
            reasons = [
                "The target-date series contains material internal unavailability. A recurring wearable routine is relevant context only and does not attribute any run without exact dated overlap."
            ]
        else:
            label = "unexplained_internal_unavailability"
            confidence = "high"
            reasons = [
                "The target-date series contains a material internal unusable interval; its cause is not inferred from missing samples alone."
            ]
    elif material_gap and confirmed_overlap_minutes >= (
        (stress.get("material_unavailable_minutes") or 0) - 0.1
    ):
        label = "confirmed_intentional_off_wrist"
        confidence = "high"
        reasons = [
            "Exact dated athlete reports account for the material unavailable run. Physical watch removal is confirmed only for those reported intervals; physiology remains missing and is not imputed."
        ]
    elif material_gap and confirmed_measurement_minutes > 0:
        label = "confirmed_optical_hr_measurement_unavailability"
        confidence = "high"
        reasons = [
            "Direct target-date Fenix wrist-HR sample transitions corroborate the material stress gap at sensor-cadence resolution. Optical-HR measurement was unavailable, but physical wear state and the cause remain unassigned."
        ]
    elif exact_valid:
        label = "confirmed_intentional_off_wrist"
        confidence = "high" if confirmed_overlap_minutes > 0 else "medium"
        reasons = [
            "The athlete supplied a target-date completed off-wrist interval; only exact overlap is attributed, and missing samples remain missing rather than being imputed."
        ]
    elif not series_sufficient:
        label = "insufficient_series_coverage"
        confidence = "high"
        reasons = [
            "The endpoint has target-date observations but lacks sufficient valid density, credible cadence, midnight coverage, or a tail reaching a declared cutoff at or after 18:00; low stress cannot receive positive credit."
        ]
    elif material_gap:
        label = "unexplained_internal_unavailability"
        confidence = "high"
        reasons = [
            "The target-date series contains a material internal unusable interval; its cause is not inferred from missing samples alone."
        ]
    elif recurring:
        label = "reported_routine_context_only"
        confidence = "medium"
        reasons = [
            "A recurring wearable exception applies to this weekday, but no material internal gap or exact dated report confirms occurrence."
        ]
    else:
        label = "no_material_internal_unavailability"
        confidence = "high" if endpoint_usable else "low"
        reasons = [
            "No material internal unusable run was detected between the first and last observed all-day stress samples."
        ]

    low_stress_reward_eligible = bool(
        endpoint_usable
        and series_sufficient
        and not material_gap
        and not material_confirmed_window
        and not discordant
    )
    latest_attempt = _safe_attempt_summary(record.get("latest_attempt"))
    latest_attempt_status = (latest_attempt or {}).get("status") or _safe_attempt_status(
        record.get("last_attempt_status")
    )
    retention_policy = (
        "preserve_last_nonempty_success"
        if record.get("retention_policy") == "preserve_last_nonempty_success"
        else ("unknown" if record.get("retention_policy") not in (None, "") else None)
    )
    retained_after_degraded = bool(
        endpoint_usable
        and latest_attempt_status
        in {"failed", "unsupported", "success_empty", "not_attempted"}
        and retention_policy == "preserve_last_nonempty_success"
    )
    stress_public = _strip_private_fields(stress)
    battery_public = _strip_private_fields(battery)
    optical_hr_public = _strip_private_fields(optical_hr)
    heart_rate_latest_attempt = _safe_attempt_summary(
        heart_rate_record.get("latest_attempt")
    )
    return {
        "artifact_type": "wearable_coverage",
        "schema_version": ARTIFACT_VERSION,
        "date": target_date.isoformat(),
        "generated_at": iso_now(timezone_name),
        "status": "available" if endpoint_usable else "insufficient_evidence",
        "classification": {
            "label": label,
            "confidence": confidence,
            "reasons": reasons,
            "cause_inference_rule": (
                "Stress or Body Battery gaps alone do not prove optical-contact loss. A direct wrist-HR gap confirms measurement unavailability only; it never proves physical watch removal or assigns dress-watch use, loose fit, showering, charging, or another cause."
            ),
        },
        "observed_coverage": {
            "stress": stress_public,
            "body_battery": battery_public,
            "optical_heart_rate": optical_hr_public,
            "boundary_guard": (
                "Material-run detection, optical-HR measurement confirmation, physical wear state, and cause attribution remain separate. Positive low-stress eligibility separately tests valid density, the midnight start boundary, and the declared-cutoff tail; boundary failures withhold credit without inventing or imputing physiology."
            ),
        },
        "athlete_reported_windows": [_strip_private_fields(item) for item in manual],
        "athlete_reported_window_summary": manual_summary,
        "recurring_context": recurring,
        "sensor_alignment": alignments,
        "material_run_attribution": attribution["runs"],
        "attribution_summary": attribution_summary,
        "decision_use": {
            "role": "coverage_interpretation_only_never_readiness_clearance",
            "low_stress_positive_reward_eligible": low_stress_reward_eligible,
            "high_observed_stress_may_still_downshift": True,
            "reason": (
                "The target-date series has sufficient valid density and boundary coverage, with no material internal unavailability, confirmed off-wrist interval, or discordance."
                if low_stress_reward_eligible
                else "Low average stress cannot earn positive readiness credit while target-date all-day coverage is missing, measurement-unavailable, explicitly off-wrist, or discordant."
            ),
        },
        "safety_contract": {
            "may_impute_stress": False,
            "may_impute_body_battery": False,
            "may_impute_sleep_hrv_steps": False,
            "may_treat_missing_as_rest": False,
            "may_infer_physical_watch_removal_from_hr_gap": False,
            "may_assign_contact_loss_cause_from_hr_gap": False,
            "may_raise_physical_readiness": False,
            "may_raise_cns_ceiling": False,
            "may_raise_technical_ceiling": False,
            "may_change_written_session": False,
            "may_explain_confirmed_interval_only": True,
            "endpoint_failure_remains_endpoint_failure": True,
        },
        "provenance": {
            "source_endpoint": "get_all_day_stress",
            "wellness_file": f"snapshots/garmin_wellness_{target_date.isoformat()}.json",
            "feedback_file": (
                f"input/feedback_{target_date.isoformat()}.json" if feedback else None
            ),
            "endpoint_payload_date": payload_date.isoformat() if payload_date else None,
            "wellness_snapshot_date": snapshot_date.isoformat() if snapshot_date else None,
            "wellness_snapshot_date_matches_target": snapshot_date_matches,
            "endpoint_status": _safe_attempt_status(record.get("status")),
            "endpoint_record_usable": endpoint_record_usable,
            "effective_sample_cutoff_local": latest_allowed.isoformat(timespec="minutes")
            if latest_allowed is not None
            else None,
            "latest_attempt": latest_attempt,
            "last_success_at": _safe_iso_timestamp(record.get("last_success_at")),
            "retained_after_degraded_attempt": retained_after_degraded,
            "retention_policy": retention_policy,
            "optical_hr_endpoint": {
                "source_endpoint": "get_heart_rates",
                "endpoint_status": _safe_attempt_status(
                    heart_rate_record.get("status")
                ),
                "endpoint_record_usable": optical_hr.get("endpoint_usable"),
                "endpoint_payload_date": optical_hr.get("payload_date"),
                "effective_sample_cutoff_local": optical_hr.get(
                    "endpoint_cutoff_local"
                ),
                "latest_attempt": heart_rate_latest_attempt,
                "last_success_at": _safe_iso_timestamp(
                    heart_rate_record.get("last_success_at")
                ),
                "retention_policy": (
                    "preserve_last_nonempty_success"
                    if heart_rate_record.get("retention_policy")
                    == "preserve_last_nonempty_success"
                    else None
                ),
            },
            "raw_privacy": "raw_private_local_only",
            "derived_privacy": (
                "Bounded reason categories and timing only; raw notes, locations, profile identifiers, and sample values are not copied."
            ),
        },
    }


def build_wearable_coverage(
    root: str | Path | None = None,
    for_date: str | date | None = None,
    *,
    wellness_snapshot: dict | None = None,
    feedback: dict | None = None,
) -> dict:
    base = Path(root or ".").resolve()
    context = load_context(root)
    timezone_name = (context.get("athlete") or {}).get("timezone", DEFAULT_TIMEZONE)
    target = parse_date(for_date) or today_local(timezone_name)
    snapshot = wellness_snapshot if isinstance(wellness_snapshot, dict) else read_json(
        snapshots_dir(root) / f"garmin_wellness_{target.isoformat()}.json",
        {},
    )
    feedback_payload = feedback if isinstance(feedback, dict) else read_json(
        base / "input" / f"feedback_{target.isoformat()}.json",
        {},
    )
    artifact = analyze_wearable_coverage(
        target_date=target,
        wellness_snapshot=snapshot,
        feedback=feedback_payload,
        context=context,
        timezone_name=timezone_name,
    )
    write_json(snapshots_dir(root) / "wearable_coverage.json", artifact)
    write_json(
        snapshots_dir(root) / f"wearable_coverage_{target.isoformat()}.json",
        artifact,
    )
    return artifact
