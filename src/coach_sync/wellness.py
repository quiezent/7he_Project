from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any

from .evidence import as_number, dated_snapshot_files, wellness_snapshot_is_usable
from .io import read_json, write_json
from .paths import snapshots_dir
from .time_utils import DEFAULT_TIMEZONE, get_zoneinfo, iso_now, parse_date, today_local
from .wellness_verification import verify_wellness_payload


_ALLOWED_ALL_DAY_STRESS_STATUSES = {
    "success",
    "failed",
    "unsupported",
    "success_empty",
    "not_attempted",
}


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


def _merge_daily_summaries(primary: Any, fallback: Any) -> dict:
    """Merge duplicate Garmin daily summaries without letting primary nulls hide fallback data."""
    primary = primary if isinstance(primary, dict) else {}
    fallback = fallback if isinstance(fallback, dict) else {}
    merged = dict(fallback)
    merged.update({key: value for key, value in primary.items() if value is not None})
    return merged


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


def _descriptor_index(descriptors: Any, keys: tuple[str, ...], default: int) -> int:
    wanted = {key.lower() for key in keys}
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
                    "spo2ValueDescriptorKey",
                    "respirationAveragesValueDescriptionKey",
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
                    "spo2ValueDescriptorIndex",
                    "respirationAveragesValueDescriptorIndex",
                )
                if descriptor.get(name) is not None
            ),
            None,
        )
        if index is not None:
            return int(index)
    return default


def _epoch_to_local_iso(value: Any) -> str | None:
    parsed = _epoch_to_local_datetime(value)
    return parsed.isoformat(timespec="seconds") if parsed is not None else None


def _epoch_to_local_datetime(value: Any) -> datetime | None:
    numeric = as_number(value)
    if numeric is None:
        return None
    seconds = numeric / 1000.0 if abs(numeric) > 10_000_000_000 else numeric
    tz = get_zoneinfo(DEFAULT_TIMEZONE) or datetime.now().astimezone().tzinfo
    try:
        return datetime.fromtimestamp(seconds, timezone.utc).astimezone(tz)
    except (OSError, OverflowError, ValueError):
        return None


def _as_local_datetime(value: datetime | str | None) -> datetime:
    tz = get_zoneinfo(DEFAULT_TIMEZONE) or datetime.now().astimezone().tzinfo
    if value is None:
        return datetime.now(tz)
    if isinstance(value, datetime):
        return value.replace(tzinfo=tz) if value.tzinfo is None else value.astimezone(tz)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(tz)
    return parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed.astimezone(tz)


def _safe_parse_date(value: Any) -> date | None:
    try:
        parsed = parse_date(value)
    except (TypeError, ValueError):
        return None
    return parsed.date() if isinstance(parsed, datetime) else parsed


def _safe_all_day_stress_status(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    return (
        normalized
        if normalized in _ALLOWED_ALL_DAY_STRESS_STATUSES
        else "unknown"
    )


def _safe_iso_timestamp(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.isoformat(timespec="seconds")


def _internal_stress_unavailability(rows: list[tuple[float, float]]) -> dict:
    ordered = sorted(rows, key=lambda item: item[0])
    timestamps_seconds = [
        stamp / 1000.0 if abs(stamp) > 10_000_000_000 else stamp
        for stamp, _ in ordered
    ]
    gaps = [
        current - previous
        for previous, current in zip(timestamps_seconds, timestamps_seconds[1:])
        if 0 < current - previous <= 30 * 60
    ]
    cadence = float(median(gaps)) if gaps else 180.0
    intervals: list[tuple[float, float]] = []
    current_start: float | None = None
    current_end: float | None = None
    for (stamp, value), stamp_seconds in zip(ordered, timestamps_seconds):
        if value < 0:
            slot_end = stamp_seconds + cadence
            if current_start is None:
                current_start, current_end = stamp_seconds, slot_end
            elif stamp_seconds <= (current_end or stamp_seconds) + cadence * 0.25:
                current_end = max(current_end or slot_end, slot_end)
            else:
                intervals.append((current_start, current_end or current_start))
                current_start, current_end = stamp_seconds, slot_end
        elif current_start is not None:
            intervals.append((current_start, current_end or current_start))
            current_start = current_end = None
    if current_start is not None:
        intervals.append((current_start, current_end or current_start))
    for previous, current in zip(timestamps_seconds, timestamps_seconds[1:]):
        if current - previous > cadence * 1.5:
            intervals.append((previous + cadence, current))
    material = [
        (start, end)
        for start, end in intervals
        if (end - start) / 60.0 >= 15.0
    ]
    durations = [(end - start) / 60.0 for start, end in material]
    return {
        "cadence_seconds": round(cadence, 1),
        "material_run_count": len(material),
        "material_unavailable_minutes": round(sum(durations), 1),
        "longest_material_unavailable_minutes": round(max(durations), 1)
        if durations
        else 0.0,
    }


def _empty_all_day_stress_summary() -> dict:
    return {
        "all_day_stress_sample_count": 0,
        "all_day_stress_parsed_sample_count": 0,
        "all_day_stress_target_date_sample_count": 0,
        "all_day_stress_valid_sample_count": 0,
        "all_day_stress_sentinel_sample_count": 0,
        "all_day_stress_malformed_sample_count": 0,
        "all_day_stress_other_date_sample_count": 0,
        "all_day_stress_after_cutoff_sample_count": 0,
        "all_day_stress_duplicate_timestamp_count": 0,
        "all_day_stress_start_time_local": None,
        "all_day_stress_end_time_local": None,
        "all_day_stress_declared_cutoff_local": None,
        "all_day_stress_sample_cutoff_local": None,
        "all_day_stress_effective_cutoff_local": None,
        "all_day_stress_start_lag_minutes": None,
        "all_day_stress_tail_lag_minutes": None,
        "all_day_stress_density_ratio": None,
        "all_day_stress_parsed_density_ratio": None,
        "all_day_stress_coverage_sufficient": None,
        "all_day_stress_sufficiency_issues": [],
        "all_day_stress_cadence_seconds": None,
        "all_day_stress_material_unavailable_run_count": None,
        "all_day_stress_material_unavailable_minutes": None,
        "all_day_stress_longest_material_unavailable_minutes": None,
        "all_day_stress_low_positive_reward_eligible": None,
        "all_day_body_battery_sample_count": 0,
        "all_day_body_battery_latest_value": None,
        "all_day_body_battery_latest_timestamp_local": None,
    }


def _all_day_stress_summary(
    record: Any,
    target_date: str | None,
    outer_date: str | None,
    as_of: datetime | str | None = None,
) -> dict:
    """Expose bounded all-day-series coverage without replacing readiness anchors."""
    empty = _empty_all_day_stress_summary()
    if not isinstance(record, dict):
        return empty
    payload = record.get("data")
    if not isinstance(payload, dict):
        return empty

    target = _safe_parse_date(target_date)
    outer = _safe_parse_date(outer_date)
    nested = _safe_parse_date(payload.get("calendarDate") or payload.get("date"))
    endpoint_status = str(record.get("status") or "").strip().lower()
    endpoint_success = record.get("ok") is True and endpoint_status == "success"

    declared_cutoff = _local_source_cutoff(
        payload.get("endTimestampLocal"),
        "all_day_stress.endTimestampLocal",
    )
    declared_cutoff_local = (
        (declared_cutoff or {}).get("normalized_local")
        if (declared_cutoff or {}).get("_sort_timestamp") is not None
        else None
    )
    declared_cutoff_dt = (
        datetime.fromisoformat(declared_cutoff_local)
        if declared_cutoff_local is not None
        else None
    )
    wall_as_of = _as_local_datetime(as_of)
    target_start = None
    wall_cutoff = None
    if target is not None:
        tz = get_zoneinfo(DEFAULT_TIMEZONE) or wall_as_of.tzinfo
        target_start = datetime.combine(target, datetime.min.time(), tzinfo=tz)
        if wall_as_of.date() == target:
            wall_cutoff = wall_as_of
        elif wall_as_of.date() > target:
            wall_cutoff = target_start + timedelta(days=1) - timedelta(microseconds=1)
        else:
            # A future target cannot borrow a future-declared endpoint cutoff.
            # Clamping to the actual wall instant keeps every future sample out.
            wall_cutoff = wall_as_of
    cutoff_candidates = [item for item in (declared_cutoff_dt, wall_cutoff) if item is not None]
    sample_cutoff = min(cutoff_candidates) if cutoff_candidates else None
    effective_cutoff = (
        min(declared_cutoff_dt, wall_cutoff)
        if declared_cutoff_dt is not None and wall_cutoff is not None
        else declared_cutoff_dt
    )

    issues: list[str] = []
    if not endpoint_success:
        issues.append("endpoint_not_success")
    if target is None or outer is None:
        issues.append("outer_target_date_missing_or_invalid")
    elif outer != target:
        issues.append("outer_target_date_mismatch")
    if nested is None:
        issues.append("nested_target_date_missing_or_invalid")
    elif target is None or nested != target:
        issues.append("nested_target_date_mismatch")
    if declared_cutoff_dt is None:
        issues.append("declared_cutoff_unavailable")
    elif (
        target is None
        or (
            declared_cutoff_dt.date() != target
            and not _completed_day_boundary(declared_cutoff_dt, target)
        )
    ):
        issues.append("declared_cutoff_date_mismatch")
    if effective_cutoff is None or target_start is None:
        issues.append("effective_cutoff_unavailable")

    stress_descriptors = payload.get("stressValueDescriptorsDTOList") or payload.get(
        "stressValueDescriptorDTOList"
    )
    stress_time_index = _descriptor_index(stress_descriptors, ("timestamp",), 0)
    stress_value_index = _descriptor_index(
        stress_descriptors,
        ("stresslevel", "stress"),
        1,
    )
    raw_stress_rows = payload.get("stressValuesArray")
    series_is_list = isinstance(raw_stress_rows, list)
    stress_rows = raw_stress_rows if series_is_list else []
    malformed_count = 0 if series_is_list else 1
    parsed_count = 0
    other_date_count = 0
    after_cutoff_count = 0
    target_rows_by_stamp: dict[float, float] = {}
    duplicate_count = 0
    for row in stress_rows:
        if not isinstance(row, (list, tuple)) or len(row) <= max(
            stress_time_index, stress_value_index
        ):
            malformed_count += 1
            continue
        stamp = as_number(row[stress_time_index])
        value = as_number(row[stress_value_index])
        local_dt = _epoch_to_local_datetime(stamp)
        if stamp is None or value is None or local_dt is None:
            malformed_count += 1
            continue
        parsed_count += 1
        if target is None or local_dt.date() != target:
            other_date_count += 1
            continue
        if sample_cutoff is None or local_dt > sample_cutoff:
            after_cutoff_count += 1
            continue
        if stamp in target_rows_by_stamp:
            duplicate_count += 1
        target_rows_by_stamp[stamp] = value

    parsed_stress_rows = sorted(target_rows_by_stamp.items())
    valid_stress_rows = [item for item in parsed_stress_rows if item[1] >= 0]
    sentinel_count = sum(1 for _, value in parsed_stress_rows if value < 0)
    internal_unavailability = _internal_stress_unavailability(parsed_stress_rows)

    cadence = as_number(internal_unavailability.get("cadence_seconds"))
    first_dt = (
        _epoch_to_local_datetime(parsed_stress_rows[0][0]) if parsed_stress_rows else None
    )
    last_dt = (
        _epoch_to_local_datetime(parsed_stress_rows[-1][0]) if parsed_stress_rows else None
    )
    start_lag = (
        max(0.0, (first_dt - target_start).total_seconds() / 60.0)
        if first_dt is not None and target_start is not None
        else None
    )
    tail_lag = (
        max(0.0, (effective_cutoff - last_dt).total_seconds() / 60.0)
        if last_dt is not None and effective_cutoff is not None
        else None
    )
    cutoff_elapsed_minutes = (
        (effective_cutoff - target_start).total_seconds() / 60.0
        if effective_cutoff is not None and target_start is not None
        else None
    )
    cadence_credible = cadence is not None and 120.0 <= cadence <= 300.0
    # Garmin's all-day stress surface is nominally three-minute data. Density
    # must use that fixed contract; deriving it from an already-thinned series
    # would let uniform five-minute sampling look complete.
    expected_count = (
        int((cutoff_elapsed_minutes * 60.0) // 180.0) + 1
        if cutoff_elapsed_minutes is not None and cutoff_elapsed_minutes >= 0
        else None
    )
    parsed_density_ratio = (
        min(1.0, len(parsed_stress_rows) / expected_count)
        if expected_count
        else None
    )
    density_ratio = (
        min(1.0, len(valid_stress_rows) / expected_count)
        if expected_count
        else None
    )

    if not series_is_list or not stress_rows:
        issues.append("series_missing_or_empty")
    if malformed_count:
        issues.append("malformed_samples")
    if other_date_count:
        issues.append("samples_outside_target_date")
    if after_cutoff_count:
        issues.append("samples_after_effective_cutoff")
    if not parsed_stress_rows:
        issues.append("no_target_date_samples_before_cutoff")
    if duplicate_count:
        issues.append("duplicate_timestamps")
    if not cadence_credible:
        issues.append("cadence_not_credible")
    if len(parsed_stress_rows) < 100:
        issues.append("sample_count_too_low")
    if density_ratio is None or density_ratio < 0.90:
        issues.append("series_density_insufficient")
    if start_lag is None or start_lag > 5.0:
        issues.append("series_does_not_start_near_midnight")
    if cutoff_elapsed_minutes is None or cutoff_elapsed_minutes < 18 * 60:
        issues.append("effective_cutoff_before_18_local")
    if tail_lag is None or tail_lag > 5.0:
        issues.append("series_tail_does_not_reach_cutoff")
    if internal_unavailability["material_run_count"]:
        issues.append("material_internal_unavailability")

    # Preserve insertion order while bounding this diagnostic surface.
    issues = list(dict.fromkeys(issues))[:12]
    coverage_sufficient = not issues

    body_battery_descriptors = payload.get("bodyBatteryValueDescriptorsDTOList") or payload.get(
        "bodyBatteryValueDescriptorDTOList"
    )
    raw_body_battery_rows = payload.get("bodyBatteryValuesArray")
    body_battery_rows = raw_body_battery_rows if isinstance(raw_body_battery_rows, list) else []
    default_battery_index = (
        2
        if any(
            isinstance(row, (list, tuple)) and len(row) >= 3
            for row in body_battery_rows
        )
        else 1
    )
    battery_time_index = _descriptor_index(body_battery_descriptors, ("timestamp",), 0)
    battery_value_index = _descriptor_index(
        body_battery_descriptors,
        ("bodybatterylevel", "bodybattery", "level"),
        default_battery_index,
    )
    battery_samples: list[tuple[float, float]] = []
    for row in body_battery_rows:
        if not isinstance(row, (list, tuple)) or len(row) <= max(
            battery_time_index, battery_value_index
        ):
            continue
        stamp = as_number(row[battery_time_index])
        value = as_number(row[battery_value_index])
        local_dt = _epoch_to_local_datetime(stamp)
        if (
            stamp is not None
            and value is not None
            and local_dt is not None
            and target is not None
            and local_dt.date() == target
            and sample_cutoff is not None
            and local_dt <= sample_cutoff
        ):
            battery_samples.append((stamp, value))
    battery_samples.sort(key=lambda item: item[0])

    return {
        "all_day_stress_sample_count": len(stress_rows),
        "all_day_stress_parsed_sample_count": parsed_count,
        "all_day_stress_target_date_sample_count": len(parsed_stress_rows),
        "all_day_stress_valid_sample_count": len(valid_stress_rows),
        "all_day_stress_sentinel_sample_count": sentinel_count,
        "all_day_stress_malformed_sample_count": malformed_count,
        "all_day_stress_other_date_sample_count": other_date_count,
        "all_day_stress_after_cutoff_sample_count": after_cutoff_count,
        "all_day_stress_duplicate_timestamp_count": duplicate_count,
        "all_day_stress_start_time_local": _epoch_to_local_iso(valid_stress_rows[0][0])
        if valid_stress_rows
        else None,
        "all_day_stress_end_time_local": _epoch_to_local_iso(valid_stress_rows[-1][0])
        if valid_stress_rows
        else None,
        "all_day_stress_declared_cutoff_local": declared_cutoff_local,
        "all_day_stress_sample_cutoff_local": sample_cutoff.isoformat(
            timespec="seconds"
        )
        if sample_cutoff is not None
        else None,
        "all_day_stress_effective_cutoff_local": effective_cutoff.isoformat(
            timespec="seconds"
        )
        if effective_cutoff is not None
        else None,
        "all_day_stress_start_lag_minutes": round(start_lag, 1)
        if start_lag is not None
        else None,
        "all_day_stress_tail_lag_minutes": round(tail_lag, 1)
        if tail_lag is not None
        else None,
        "all_day_stress_density_ratio": round(density_ratio, 3)
        if density_ratio is not None
        else None,
        "all_day_stress_parsed_density_ratio": round(parsed_density_ratio, 3)
        if parsed_density_ratio is not None
        else None,
        "all_day_stress_coverage_sufficient": coverage_sufficient,
        "all_day_stress_sufficiency_issues": issues,
        "all_day_stress_cadence_seconds": internal_unavailability["cadence_seconds"],
        "all_day_stress_material_unavailable_run_count": internal_unavailability[
            "material_run_count"
        ],
        "all_day_stress_material_unavailable_minutes": internal_unavailability[
            "material_unavailable_minutes"
        ],
        "all_day_stress_longest_material_unavailable_minutes": internal_unavailability[
            "longest_material_unavailable_minutes"
        ],
        "all_day_stress_low_positive_reward_eligible": coverage_sufficient,
        "all_day_body_battery_sample_count": len(battery_samples),
        "all_day_body_battery_latest_value": battery_samples[-1][1]
        if battery_samples
        else None,
        "all_day_body_battery_latest_timestamp_local": _epoch_to_local_iso(
            battery_samples[-1][0]
        )
        if battery_samples
        else None,
    }


def _payload_record(snapshot: dict, label: str) -> dict:
    return next(
        (
            item
            for item in snapshot.get("payloads") or []
            if isinstance(item, dict) and item.get("label") == label
        ),
        {},
    )


def _endpoint_status(record: dict) -> str:
    status = str(record.get("status") or "").strip().lower()
    if status in _ALLOWED_ALL_DAY_STRESS_STATUSES:
        return status
    if record.get("ok") is True:
        data = record.get("data")
        return "success" if isinstance(data, dict) and data else "success_empty"
    return "failed" if record else "not_attempted"


def _endpoint_provenance(
    record: dict,
    target_date: str | None,
    endpoint_name: str,
) -> dict:
    payload = record.get("data") if isinstance(record.get("data"), dict) else {}
    response = _safe_parse_date(payload.get("calendarDate") or payload.get("date"))
    target = _safe_parse_date(target_date)
    status = _endpoint_status(record)
    latest_attempt = (
        record.get("latest_attempt")
        if isinstance(record.get("latest_attempt"), dict)
        else record
    )
    latest_status = _endpoint_status(latest_attempt)
    cutoff = _local_source_cutoff(
        payload.get("endTimestampLocal"),
        f"{endpoint_name}.endTimestampLocal",
    )
    date_matches = (
        response == target if response is not None and target is not None else None
    )
    usable = status == "success" and date_matches is True
    return {
        "endpoint": f"garminconnect.{endpoint_name}",
        "endpoint_status": status,
        "latest_attempt_status": latest_status,
        "latest_attempt_at": _safe_iso_timestamp(
            latest_attempt.get("attempted_at") or latest_attempt.get("fetched_at")
        ),
        "last_success_at": _safe_iso_timestamp(
            record.get("last_success_at")
            or (record.get("attempted_at") if status == "success" else None)
        ),
        "response_date": response.isoformat() if response is not None else None,
        "response_date_matches_target": date_matches,
        "data_cutoff_local": (
            (cutoff or {}).get("normalized_local")
            if usable and (cutoff or {}).get("_sort_timestamp") is not None
            else None
        ),
        "data_retained_after_degraded_attempt": (
            usable and latest_status in {"failed", "unsupported", "success_empty"}
        ),
        "retention_policy": (
            "preserve_last_nonempty_success"
            if record.get("retention_policy") == "preserve_last_nonempty_success"
            else None
        ),
        "data_usable": usable,
        "freshness": (
            "target_date"
            if usable
            else "wrong_or_missing_response_date"
            if status == "success"
            else "endpoint_unavailable"
        ),
    }


def _completed_day_boundary(moment: datetime | None, target: date | None) -> bool:
    if moment is None or target is None:
        return False
    tz = moment.tzinfo
    boundary = datetime.combine(
        target + timedelta(days=1),
        datetime.min.time(),
        tzinfo=tz,
    )
    return moment == boundary


def _bounded_series_summary(
    rows: Any,
    target_date: str | None,
    *,
    time_index: int,
    value_index: int,
    cadence_seconds: int | None = None,
    endpoint_cutoff_local: str | None = None,
    value_max: float | None = None,
    activity_sentinel: float | None = None,
) -> dict:
    target = _safe_parse_date(target_date)
    series = rows if isinstance(rows, list) else []
    malformed = 0 if rows is None or isinstance(rows, list) else 1
    other_date = 0
    sentinel = 0
    activity_unavailable = 0
    invalid_non_sentinel = 0
    parsed: list[tuple[float, float]] = []
    valid: list[tuple[float, float]] = []
    for row in series:
        if not isinstance(row, (list, tuple)) or len(row) <= max(time_index, value_index):
            malformed += 1
            continue
        stamp = as_number(row[time_index])
        value = as_number(row[value_index])
        local_dt = _epoch_to_local_datetime(stamp)
        if stamp is None or value is None or local_dt is None:
            malformed += 1
            continue
        if target is None or (
            local_dt.date() != target
            and not _completed_day_boundary(local_dt, target)
        ):
            other_date += 1
            continue
        parsed.append((stamp, value))
        if value < 0:
            sentinel += 1
            if activity_sentinel is not None and value == activity_sentinel:
                activity_unavailable += 1
            continue
        if value <= 0 or (value_max is not None and value > value_max):
            invalid_non_sentinel += 1
            continue
        valid.append((stamp, value))
    parsed.sort(key=lambda item: item[0])
    valid.sort(key=lambda item: item[0])

    expected = None
    if cadence_seconds and endpoint_cutoff_local and target is not None:
        try:
            cutoff = datetime.fromisoformat(endpoint_cutoff_local)
        except (TypeError, ValueError):
            cutoff = None
        if cutoff is not None:
            tz = cutoff.tzinfo
            start = datetime.combine(target, datetime.min.time(), tzinfo=tz)
            elapsed = max(0.0, (cutoff - start).total_seconds())
            expected = int(elapsed // cadence_seconds)
    coverage_ratio = (
        min(1.0, len(parsed) / expected) if expected and expected > 0 else None
    )
    valid_ratio = (
        min(1.0, len(valid) / expected) if expected and expected > 0 else None
    )
    return {
        "reported_count": len(series),
        "parsed_target_count": len(parsed),
        "valid_count": len(valid),
        "sentinel_count": sentinel,
        "activity_sentinel_count": activity_unavailable,
        "malformed_count": malformed,
        "other_date_count": other_date,
        "invalid_non_sentinel_count": invalid_non_sentinel,
        "first_timestamp_local": _epoch_to_local_iso(parsed[0][0]) if parsed else None,
        "last_timestamp_local": _epoch_to_local_iso(parsed[-1][0]) if parsed else None,
        "lowest_valid": min((value for _, value in valid), default=None),
        "highest_valid": max((value for _, value in valid), default=None),
        "expected_count_through_cutoff": expected,
        "series_coverage_ratio": round(coverage_ratio, 3)
        if coverage_ratio is not None
        else None,
        "valid_measurement_ratio": round(valid_ratio, 3)
        if valid_ratio is not None
        else None,
    }


def _object_series_count(rows: Any, value_keys: tuple[str, ...]) -> dict:
    series = rows if isinstance(rows, list) else []
    valid = 0
    for row in series:
        if isinstance(row, dict):
            value = next(
                (as_number(row.get(key)) for key in value_keys if row.get(key) is not None),
                None,
            )
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            value = as_number(row[1])
        else:
            value = None
        if value is not None and 0 < value <= 100:
            valid += 1
    return {"reported_count": len(series), "valid_count": valid}


def _spo2_summary(record: dict, target_date: str | None) -> dict:
    provenance = _endpoint_provenance(record, target_date, "get_spo2_data")
    payload = record.get("data") if provenance["data_usable"] else {}
    descriptors = payload.get("spO2ValueDescriptorsDTOList") or []
    time_index = _descriptor_index(descriptors, ("timestamp",), 0)
    value_index = _descriptor_index(descriptors, ("spo2reading", "spo2", "value"), 1)
    hourly = _bounded_series_summary(
        payload.get("spO2HourlyAverages"),
        target_date,
        time_index=time_index,
        value_index=value_index,
        value_max=100,
    )
    single = _object_series_count(
        payload.get("spO2SingleValues"),
        ("spo2", "spO2", "spo2Value", "value", "reading"),
    )
    continuous = _object_series_count(
        payload.get("continuousReadingDTOList"),
        ("spo2", "spO2", "spo2Value", "value", "reading"),
    )
    daily_average = as_number(payload.get("averageSpO2"))
    sleep_average = as_number(payload.get("avgSleepSpO2"))
    lowest = as_number(payload.get("lowestSpO2"))
    latest = as_number(payload.get("latestSpO2"))
    return {
        "spo2_endpoint_status": provenance["endpoint_status"],
        "spo2_latest_attempt_status": provenance["latest_attempt_status"],
        "spo2_last_success_at": provenance["last_success_at"],
        "spo2_response_date": provenance["response_date"],
        "spo2_response_date_matches_target": provenance[
            "response_date_matches_target"
        ],
        "spo2_data_cutoff_local": provenance["data_cutoff_local"],
        "spo2_data_retained_after_degraded_attempt": provenance[
            "data_retained_after_degraded_attempt"
        ],
        "spo2_freshness": provenance["freshness"],
        "spo2_daily_average_pct": daily_average,
        "spo2_sleep_average_pct": sleep_average,
        "spo2_lowest_pct": lowest,
        "spo2_latest_pct": latest,
        "spo2_latest_timestamp_local": payload.get("latestSpO2TimestampLocal"),
        "spo2_7d_average_pct": as_number(payload.get("lastSevenDaysAvgSpO2")),
        "spo2_hourly_aggregate_count": hourly["reported_count"],
        "spo2_hourly_valid_count": hourly["valid_count"],
        "spo2_hourly_sentinel_count": hourly["sentinel_count"],
        "spo2_hourly_malformed_count": hourly["malformed_count"],
        "spo2_hourly_lowest_pct": hourly["lowest_valid"],
        "spo2_hourly_highest_pct": hourly["highest_valid"],
        "spo2_hourly_start_local": hourly["first_timestamp_local"],
        "spo2_hourly_end_local": hourly["last_timestamp_local"],
        "spo2_single_reading_count": single["reported_count"],
        "spo2_single_valid_count": single["valid_count"],
        "spo2_continuous_reading_count": continuous["reported_count"],
        "spo2_continuous_valid_count": continuous["valid_count"],
        "spo2_in_activity_interpretation": (
            "The Garmin daily Pulse Ox endpoint does not establish activity-time SpO2; "
            "do not align hourly sleep/inactivity aggregates to exercise physiology."
        ),
        "spo2_decision_use": (
            "context_only_negative_or_missing_values_never_promote_readiness"
        ),
        "spo2_provenance": provenance,
    }


def _respiration_summary(record: dict, target_date: str | None) -> dict:
    provenance = _endpoint_provenance(record, target_date, "get_respiration_data")
    payload = record.get("data") if provenance["data_usable"] else {}
    descriptors = payload.get("respirationValueDescriptorsDTOList") or []
    time_index = _descriptor_index(descriptors, ("timestamp",), 0)
    value_index = _descriptor_index(descriptors, ("respiration",), 1)
    two_min = _bounded_series_summary(
        payload.get("respirationValuesArray"),
        target_date,
        time_index=time_index,
        value_index=value_index,
        cadence_seconds=120,
        endpoint_cutoff_local=provenance["data_cutoff_local"],
        activity_sentinel=-2.0,
    )
    average_descriptors = payload.get("respirationAveragesValueDescriptorDTOList") or []
    average_time_index = _descriptor_index(average_descriptors, ("timestamp",), 0)
    average_value_index = _descriptor_index(
        average_descriptors,
        ("averagerespirationvalue", "respiration"),
        1,
    )
    hourly = _bounded_series_summary(
        payload.get("respirationAveragesValuesArray"),
        target_date,
        time_index=average_time_index,
        value_index=average_value_index,
    )
    return {
        "respiration_endpoint_status": provenance["endpoint_status"],
        "respiration_latest_attempt_status": provenance["latest_attempt_status"],
        "respiration_last_success_at": provenance["last_success_at"],
        "respiration_response_date": provenance["response_date"],
        "respiration_response_date_matches_target": provenance[
            "response_date_matches_target"
        ],
        "respiration_data_cutoff_local": provenance["data_cutoff_local"],
        "respiration_data_retained_after_degraded_attempt": provenance[
            "data_retained_after_degraded_attempt"
        ],
        "respiration_freshness": provenance["freshness"],
        "respiration_waking_average_brpm": as_number(
            payload.get("avgWakingRespirationValue")
        ),
        "respiration_sleep_average_brpm": as_number(
            payload.get("avgSleepRespirationValue")
        ),
        "respiration_lowest_brpm": as_number(payload.get("lowestRespirationValue")),
        "respiration_highest_brpm": as_number(payload.get("highestRespirationValue")),
        "respiration_two_min_reported_count": two_min["reported_count"],
        "respiration_two_min_parsed_target_count": two_min["parsed_target_count"],
        "respiration_two_min_valid_count": two_min["valid_count"],
        "respiration_two_min_sentinel_count": two_min["sentinel_count"],
        "respiration_two_min_activity_sentinel_count": two_min[
            "activity_sentinel_count"
        ],
        "respiration_two_min_malformed_count": two_min["malformed_count"],
        "respiration_two_min_other_date_count": two_min["other_date_count"],
        "respiration_two_min_expected_count_through_cutoff": two_min[
            "expected_count_through_cutoff"
        ],
        "respiration_two_min_series_coverage_ratio": two_min[
            "series_coverage_ratio"
        ],
        "respiration_two_min_valid_measurement_ratio": two_min[
            "valid_measurement_ratio"
        ],
        "respiration_two_min_start_local": two_min["first_timestamp_local"],
        "respiration_two_min_end_local": two_min["last_timestamp_local"],
        "respiration_hourly_aggregate_count": hourly["reported_count"],
        "respiration_hourly_valid_count": hourly["valid_count"],
        "respiration_exercise_aligned_unavailable_interpretation": (
            "Garmin -2 activity sentinels are unavailable samples, not respiration "
            "measurements; no in-activity value is imputed."
        ),
        "respiration_decision_use": (
            "context_only_negative_or_missing_values_never_promote_readiness"
        ),
        "respiration_provenance": provenance,
    }


def _local_source_cutoff(value: Any, source: str) -> dict | None:
    """Normalize Garmin's mixed ISO and local-epoch cutoff values for comparison."""
    if value in (None, ""):
        return None
    tz = get_zoneinfo(DEFAULT_TIMEZONE) or datetime.now().astimezone().tzinfo
    numeric = as_number(value)
    numeric_text = isinstance(value, str) and value.strip().lstrip("-").isdigit()
    parsed: datetime | None = None
    encoding = "iso_local"
    if numeric is not None and (not isinstance(value, str) or numeric_text):
        seconds = numeric / 1000.0 if abs(numeric) > 10_000_000_000 else numeric
        try:
            # Garmin fields named *Local can encode wall-clock local time as an
            # epoch value. Preserve that wall-clock interpretation rather than
            # applying Kuala Lumpur's offset twice.
            parsed = datetime.fromtimestamp(seconds, timezone.utc).replace(tzinfo=tz)
            encoding = "local_epoch_ms" if abs(numeric) > 10_000_000_000 else "local_epoch_s"
        except (OSError, OverflowError, ValueError):
            parsed = None
    elif isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=tz)
            else:
                parsed = parsed.astimezone(tz)
                encoding = "iso_offset"
    if parsed is None:
        return {
            "source": source,
            "raw": value,
            "normalized_local": None,
            "encoding": "unparsed",
        }
    return {
        "source": source,
        "raw": value,
        "normalized_local": parsed.isoformat(timespec="seconds"),
        "encoding": encoding,
        "_sort_timestamp": parsed.timestamp(),
    }


def _source_cutoff_summary(
    stats: dict,
    body_battery: dict,
    sleep_dto: dict,
    spo2_payload: dict | None = None,
    respiration_payload: dict | None = None,
) -> dict:
    spo2_payload = spo2_payload or {}
    respiration_payload = respiration_payload or {}
    candidates = [
        _local_source_cutoff(stats.get("wellnessEndTimeLocal"), "daily_summary.wellnessEndTimeLocal"),
        _local_source_cutoff(body_battery.get("end_time_local"), "body_battery.endTimestampLocal"),
        _local_source_cutoff(sleep_dto.get("sleepEndTimestampLocal"), "sleep.dailySleepDTO.sleepEndTimestampLocal"),
        _local_source_cutoff(
            spo2_payload.get("endTimestampLocal"),
            "spo2.endTimestampLocal",
        ),
        _local_source_cutoff(
            respiration_payload.get("endTimestampLocal"),
            "respiration.endTimestampLocal",
        ),
    ]
    candidates = [item for item in candidates if item is not None]
    parsed = [item for item in candidates if item.get("_sort_timestamp") is not None]
    chosen = max(parsed, key=lambda item: item["_sort_timestamp"]) if parsed else None
    public_candidates = [
        {key: value for key, value in item.items() if key != "_sort_timestamp"}
        for item in candidates
    ]
    return {
        "selected_local": chosen.get("normalized_local") if chosen else None,
        "selected_source": chosen.get("source") if chosen else None,
        "candidates": public_candidates,
    }


def normalize_wellness_payload(
    snapshot: dict,
    as_of: datetime | str | None = None,
) -> dict:
    labels = _payloads_by_label(snapshot)
    stats = _merge_daily_summaries(
        labels.get("get_stats"),
        labels.get("get_user_summary"),
    )
    sleep = labels.get("get_sleep_data") or {}
    sleep_dto = sleep.get("dailySleepDTO") or {}
    hrv = labels.get("get_hrv_data") or {}
    hrv_summary = hrv.get("hrvSummary") or {}
    body_comp = labels.get("get_body_composition") or {}
    all_day_stress_record = next(
        (
            payload
            for payload in snapshot.get("payloads") or []
            if isinstance(payload, dict) and payload.get("label") == "get_all_day_stress"
        ),
        {},
    )
    spo2_record = _payload_record(snapshot, "get_spo2_data")
    respiration_record = _payload_record(snapshot, "get_respiration_data")
    sleep_scores = sleep_dto.get("sleepScores") or {}

    raw_date_value = (
        snapshot.get("date") or stats.get("calendarDate") or sleep_dto.get("calendarDate")
    )
    parsed_raw_date = _safe_parse_date(raw_date_value)
    raw_date = parsed_raw_date.isoformat() if parsed_raw_date is not None else None
    all_day_stress = _all_day_stress_summary(
        all_day_stress_record,
        raw_date,
        snapshot.get("date"),
        as_of,
    )
    spo2 = _spo2_summary(spo2_record, raw_date)
    respiration = _respiration_summary(respiration_record, raw_date)
    body_battery = _body_battery_from_endpoint(labels.get("get_body_battery"), raw_date)
    verification = (
        verify_wellness_payload(snapshot, raw_date, DEFAULT_TIMEZONE)
        if raw_date is not None
        else {}
    )
    sleep_window = verification.get("sleep_window") or {}
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
    source_cutoffs = _source_cutoff_summary(
        stats,
        body_battery,
        sleep_dto,
        (
            spo2_record.get("data")
            if (spo2.get("spo2_provenance") or {}).get("data_usable")
            and isinstance(spo2_record.get("data"), dict)
            else None
        ),
        (
            respiration_record.get("data")
            if (respiration.get("respiration_provenance") or {}).get("data_usable")
            and isinstance(respiration_record.get("data"), dict)
            else None
        ),
    )

    row = {
        "date": raw_date,
        "source_fetched_at": snapshot.get("fetched_at"),
        "source_last_sync_timestamp_gmt": stats.get("lastSyncTimestampGMT"),
        "source_data_cutoff_local": source_cutoffs.get("selected_local"),
        "source_data_cutoff_source": source_cutoffs.get("selected_source"),
        "source_data_cutoffs": source_cutoffs.get("candidates"),
        "daily_summary_payloads_used": [
            label
            for label in ("get_stats", "get_user_summary")
            if isinstance(labels.get(label), dict) and labels.get(label)
        ],
        "available_payloads": sorted(labels),
        "all_day_stress_endpoint_status": _safe_all_day_stress_status(
            all_day_stress_record.get("status")
        ),
        "all_day_stress_latest_attempt_status": _safe_all_day_stress_status(
            all_day_stress_record.get("last_attempt_status")
            or (
                all_day_stress_record.get("latest_attempt")
                if isinstance(all_day_stress_record.get("latest_attempt"), dict)
                else {}
            ).get("status")
        ),
        "all_day_stress_last_success_at": _safe_iso_timestamp(
            all_day_stress_record.get("last_success_at")
        ),
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
        # ``sleep_hours`` remains the compatibility alias for Garmin's primary
        # sleep duration. The explicit fields retain timing and nap semantics
        # from the shared verification surface.
        "sleep_hours": sleep_window.get("primary_sleep_hours"),
        "sleep_start_local": sleep_window.get("start_local"),
        "sleep_end_local": sleep_window.get("end_local"),
        "sleep_window_hours": sleep_window.get("window_hours"),
        "primary_sleep_hours": sleep_window.get("primary_sleep_hours"),
        "nap_hours_reported": sleep_window.get("nap_hours_reported"),
        "total_sleep_hours_reported": sleep_window.get("total_sleep_hours_reported"),
        "nap_reporting_status": sleep_window.get("nap_reporting_status"),
        "total_sleep_reporting_status": sleep_window.get("total_sleep_reporting_status"),
        "sleep_duration_provenance": sleep_window.get("sleep_duration_provenance"),
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
        "avg_spo2": (
            spo2.get("spo2_daily_average_pct")
            if spo2.get("spo2_daily_average_pct") is not None
            else as_number(stats.get("averageSpo2") or sleep_dto.get("averageSpO2Value"))
        ),
        "sleep_spo2": (
            spo2.get("spo2_sleep_average_pct")
            if spo2.get("spo2_sleep_average_pct") is not None
            else as_number(sleep_dto.get("averageSpO2Value"))
        ),
        "avg_respiration": (
            respiration.get("respiration_waking_average_brpm")
            if respiration.get("respiration_waking_average_brpm") is not None
            else as_number(
                stats.get("avgWakingRespirationValue")
                or sleep_dto.get("averageRespirationValue")
            )
        ),
        "monitoring_altitude_m": as_number(
            stats.get("averageMonitoringEnvironmentAltitude")
            if stats.get("averageMonitoringEnvironmentAltitude") is not None
            else stats.get("monitoringEnvironmentAltitude")
        ),
        "monitoring_altitude_source": (
            "daily_summary.averageMonitoringEnvironmentAltitude"
            if stats.get("averageMonitoringEnvironmentAltitude") is not None
            else "daily_summary.monitoringEnvironmentAltitude"
            if stats.get("monitoringEnvironmentAltitude") is not None
            else None
        ),
        "moderate_intensity_min": moderate,
        "vigorous_intensity_min": vigorous,
        "weighted_intensity_min": moderate + 2 * vigorous,
        "intensity_goal_min": as_number(stats.get("intensityMinutesGoal")),
    }
    row.update(all_day_stress)
    row.update(spo2)
    row.update(respiration)
    row.update(composition)
    return row


def normalize_wellness_snapshot(path: Path) -> dict:
    return normalize_wellness_payload(read_json(path, {}))


def build_wellness_daily(root: str | Path | None = None) -> list[dict]:
    rows = []
    for _, path in dated_snapshot_files(root, "garmin_wellness"):
        snapshot = read_json(path, {})
        if wellness_snapshot_is_usable(snapshot):
            rows.append(normalize_wellness_payload(snapshot))
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
            "avg_spo2_daily_pct": _avg(last_7, "spo2_daily_average_pct"),
            "avg_spo2_sleep_pct": _avg(last_7, "spo2_sleep_average_pct"),
            "avg_respiration_waking_brpm": _avg(
                last_7, "respiration_waking_average_brpm"
            ),
            "avg_respiration_sleep_brpm": _avg(
                last_7, "respiration_sleep_average_brpm"
            ),
            "avg_monitoring_altitude_m": _avg(last_7, "monitoring_altitude_m"),
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
