from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from .context import load_context
from .evidence import as_number, find_value, load_activities
from .io import read_json, write_json
from .paths import snapshots_dir
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local
from .wellness import normalize_wellness_payload


CLASSIFICATION_VERSION = "rest_recharge_v1"
PRE_WINDOW_MIN = 20
POST_WINDOW_MIN = 30
MIN_STRESS_SAMPLES = 6
MIN_STRESS_COVERAGE_PCT = 70.0
QUIET_STRESS_MAX = 25.0
QUIET_REST_SHARE_MIN_PCT = 80.0
BODY_BATTERY_RECHARGE_MIN = 2.0
BODY_BATTERY_BOUNDARY_MAX_GAP_MIN = 15.0
POSITIVE_CLARITY_MIN = 7.0
POSITIVE_INERTIA_MAX_MIN = 20.0
NEGATIVE_CLARITY_MAX = 4.0
NEGATIVE_INERTIA_MIN = 45.0
ADEQUATE_PRIMARY_SLEEP_H = 6.5


def _payload_record(snapshot: dict, *labels: str) -> dict:
    wanted = set(labels)
    for payload in snapshot.get("payloads") or []:
        if isinstance(payload, dict) and payload.get("label") in wanted:
            return payload
    return {}


def _payload_data(snapshot: dict, *labels: str) -> Any:
    record = _payload_record(snapshot, *labels)
    data = record.get("data")
    return data if isinstance(data, (dict, list)) else None


def _descriptor_index(
    descriptors: Any,
    keys: Iterable[str],
    default: int,
) -> int:
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
    text = str(value).strip().replace("Z", "+00:00")
    if len(text) in {5, 8} and text[2] == ":":
        try:
            parsed_time = datetime.strptime(text, "%H:%M" if len(text) == 5 else "%H:%M:%S").time()
        except ValueError:
            return None
        return datetime.combine(target, parsed_time, tzinfo=tz)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    else:
        parsed = parsed.astimezone(tz)
    return parsed


def _clarity_range(value: Any) -> tuple[float | None, float | None]:
    if isinstance(value, (list, tuple)):
        numbers = [as_number(item) for item in value]
        numbers = [item for item in numbers if item is not None]
        return (min(numbers), max(numbers)) if numbers else (None, None)
    number = as_number(value)
    return (number, number) if number is not None else (None, None)


def _candidate_reviews(feedback: dict) -> list[dict]:
    candidates: list[dict] = []

    def add(value: Any) -> None:
        if isinstance(value, dict):
            candidates.append(value)
        elif isinstance(value, list):
            candidates.extend(item for item in value if isinstance(item, dict))

    for key in (
        "sleep_work_timing_review",
        "sleep_work_timing_reviews",
        "rest_recharge_window",
        "rest_recharge_windows",
    ):
        add(feedback.get(key))
    for entry in feedback.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        for key in (
            "sleep_work_timing_review",
            "sleep_work_timing_reviews",
            "rest_recharge_window",
            "rest_recharge_windows",
        ):
            add(entry.get(key))
    return candidates


def _manual_window(feedback: dict, target: date, tz: ZoneInfo) -> dict:
    selected: dict | None = None
    for review in _candidate_reviews(feedback):
        review_date = parse_date(review.get("date") or review.get("window_date"))
        if review_date is not None and review_date != target:
            continue
        status = str(
            review.get("nap_status")
            or review.get("rest_status")
            or review.get("status")
            or ""
        ).strip().lower()
        if status in {"completed", "complete", "done"}:
            selected = review
    if selected is None:
        text = str(feedback).lower()
        return {
            "status": "missing_completed_manual_window",
            "legacy_manual_report_present": "nap" in text,
        }

    start_raw = (
        selected.get("nap_start_local")
        or selected.get("rest_start_local")
        or selected.get("start_local")
    )
    end_raw = (
        selected.get("nap_end_local")
        or selected.get("rest_end_local")
        or selected.get("end_local")
    )
    start = _parse_local_datetime(start_raw, target, tz)
    end = _parse_local_datetime(end_raw, target, tz)
    if start is not None and end is not None and end <= start:
        end += timedelta(days=1)
    duration_min = (
        round((end - start).total_seconds() / 60, 1)
        if start is not None and end is not None
        else None
    )
    valid = (
        start is not None
        and end is not None
        and start.date() == target
        and duration_min is not None
        and 0 < duration_min <= 12 * 60
    )
    clarity_value = next(
        (
            selected.get(key)
            for key in (
                "post_nap_clarity_10_range",
                "post_rest_clarity_10_range",
                "post_clarity_10_range",
                "post_nap_clarity_10",
                "post_rest_clarity_10",
                "post_clarity_10",
            )
            if selected.get(key) is not None
        ),
        None,
    )
    pre_clarity_value = next(
        (
            selected.get(key)
            for key in (
                "pre_nap_clarity_10_range",
                "pre_rest_clarity_10_range",
                "pre_clarity_10_range",
                "pre_nap_clarity_10",
                "pre_rest_clarity_10",
                "pre_clarity_10",
            )
            if selected.get(key) is not None
        ),
        None,
    )
    clarity_low, clarity_high = _clarity_range(clarity_value)
    pre_clarity_low, pre_clarity_high = _clarity_range(pre_clarity_value)
    window_type = "nap" if any(str(key).startswith("nap_") for key in selected) else "rest"
    return {
        "status": "completed_valid" if valid else "completed_invalid_timing",
        "type": window_type,
        "occurrence_source": selected.get("nap_source")
        or selected.get("rest_source")
        or selected.get("source")
        or "athlete_report",
        "start_local": start.isoformat(timespec="minutes") if start else None,
        "end_local": end.isoformat(timespec="minutes") if end else None,
        "_start": start,
        "_end": end,
        "reported_duration_minutes": duration_min,
        "time_in_bed_minutes": as_number(selected.get("time_in_bed_minutes"))
        if selected.get("time_in_bed_minutes") is not None
        else duration_min,
        "estimated_sleep_minutes": as_number(selected.get("estimated_sleep_minutes")),
        "sleep_inertia_minutes": as_number(
            selected.get("sleep_inertia_minutes")
            if selected.get("sleep_inertia_minutes") is not None
            else selected.get("inertia_minutes")
        ),
        "pre_clarity_low_10": pre_clarity_low,
        "pre_clarity_high_10": pre_clarity_high,
        "post_clarity_low_10": clarity_low,
        "post_clarity_high_10": clarity_high,
        "illness_status": selected.get("illness_status"),
        "health_status": selected.get("health_status"),
        "systemic_illness_status": selected.get("systemic_illness_status"),
    }


def _stress_samples(payload: dict, tz: ZoneInfo) -> tuple[list[tuple[datetime, float]], int]:
    descriptors = payload.get("stressValueDescriptorsDTOList") or payload.get(
        "stressValueDescriptorDTOList"
    )
    timestamp_index = _descriptor_index(descriptors, ("timestamp",), 0)
    value_index = _descriptor_index(descriptors, ("stresslevel", "stress"), 1)
    samples: list[tuple[datetime, float]] = []
    sentinel_count = 0
    for row in payload.get("stressValuesArray") or []:
        if not isinstance(row, (list, tuple)) or len(row) <= max(timestamp_index, value_index):
            continue
        stamp = _epoch_local(row[timestamp_index], tz)
        value = as_number(row[value_index])
        if stamp is None or value is None:
            continue
        if value < 0:
            sentinel_count += 1
            continue
        samples.append((stamp, value))
    return sorted(samples), sentinel_count


def _body_battery_samples(payload: dict, tz: ZoneInfo) -> list[tuple[datetime, float]]:
    descriptors = payload.get("bodyBatteryValueDescriptorsDTOList") or payload.get(
        "bodyBatteryValueDescriptorDTOList"
    )
    rows = payload.get("bodyBatteryValuesArray") or []
    fallback_value_index = 2 if any(isinstance(row, (list, tuple)) and len(row) >= 3 for row in rows) else 1
    timestamp_index = _descriptor_index(descriptors, ("timestamp",), 0)
    value_index = _descriptor_index(
        descriptors,
        ("bodybatterylevel", "bodybattery", "level"),
        fallback_value_index,
    )
    samples: dict[datetime, float] = {}
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) <= max(timestamp_index, value_index):
            continue
        stamp = _epoch_local(row[timestamp_index], tz)
        value = as_number(row[value_index])
        if stamp is not None and value is not None:
            samples[stamp] = value
    return sorted(samples.items())


def _cadence_seconds(samples: list[tuple[datetime, float]]) -> float | None:
    gaps = [
        (current[0] - previous[0]).total_seconds()
        for previous, current in zip(samples, samples[1:])
        if 0 < (current[0] - previous[0]).total_seconds() <= 30 * 60
    ]
    return round(float(median(gaps)), 1) if gaps else None


def _slice(
    samples: list[tuple[datetime, float]],
    start: datetime,
    end: datetime,
    *,
    include_start: bool = True,
    include_end: bool = False,
) -> list[tuple[datetime, float]]:
    return [
        sample
        for sample in samples
        if (sample[0] >= start if include_start else sample[0] > start)
        and (sample[0] <= end if include_end else sample[0] < end)
    ]


def _coverage(
    rows: list[tuple[datetime, float]],
    start: datetime,
    end: datetime,
    cadence_seconds: float | None,
) -> dict:
    cadence = cadence_seconds or 180.0
    expected = max(1, int((end - start).total_seconds() // cadence))
    return {
        "expected_samples": expected,
        "valid_samples": len(rows),
        "coverage_pct": round(min(100.0, len(rows) / expected * 100), 1),
        "cadence_seconds": cadence_seconds,
    }


def _stress_summary(
    rows: list[tuple[datetime, float]],
    start: datetime,
    end: datetime,
    cadence_seconds: float | None,
) -> dict:
    coverage = _coverage(rows, start, end, cadence_seconds)
    values = [value for _, value in rows]
    return {
        **coverage,
        "first_sample_local": rows[0][0].isoformat(timespec="minutes") if rows else None,
        "last_sample_local": rows[-1][0].isoformat(timespec="minutes") if rows else None,
        "mean": round(mean(values), 2) if values else None,
        "median": round(float(median(values)), 2) if values else None,
        "minimum": min(values) if values else None,
        "maximum": max(values) if values else None,
        "rest_range_pct_le_25": round(sum(value <= 25 for value in values) / len(values) * 100, 1)
        if values
        else None,
    }


def _body_battery_summary(
    rows: list[tuple[datetime, float]],
    start: datetime,
    end: datetime,
    cadence_seconds: float | None,
) -> dict:
    coverage = _coverage(rows, start, end, cadence_seconds)
    values = [value for _, value in rows]
    first_gap = round((rows[0][0] - start).total_seconds() / 60, 1) if rows else None
    last_gap = round((end - rows[-1][0]).total_seconds() / 60, 1) if rows else None
    return {
        **coverage,
        "first_sample_local": rows[0][0].isoformat(timespec="minutes") if rows else None,
        "last_sample_local": rows[-1][0].isoformat(timespec="minutes") if rows else None,
        "start_level": values[0] if values else None,
        "end_level": values[-1] if values else None,
        "delta": round(values[-1] - values[0], 1) if len(values) >= 2 else None,
        "first_sample_gap_from_window_start_minutes": first_gap,
        "last_sample_gap_to_window_end_minutes": last_gap,
        "boundary_pair_valid": bool(
            len(rows) >= 2
            and first_gap is not None
            and 0 <= first_gap <= BODY_BATTERY_BOUNDARY_MAX_GAP_MIN
            and last_gap is not None
            and 0 <= last_gap <= BODY_BATTERY_BOUNDARY_MAX_GAP_MIN
        ),
        "boundary_max_gap_minutes": BODY_BATTERY_BOUNDARY_MAX_GAP_MIN,
        "minimum": min(values) if values else None,
        "maximum": max(values) if values else None,
    }


def _recharge_onset(
    rows: list[tuple[datetime, float]],
    start: datetime,
) -> dict:
    if len(rows) < 2:
        return {"detected": False, "timestamp_local": None, "latency_minutes": None}
    minimum = min(value for _, value in rows)
    minimum_index = next(index for index, (_, value) in enumerate(rows) if value == minimum)
    threshold = minimum + 1
    for index in range(minimum_index + 1, len(rows) - 1):
        if rows[index][1] >= threshold and rows[index + 1][1] >= threshold:
            return {
                "detected": True,
                "timestamp_local": rows[index][0].isoformat(timespec="minutes"),
                "latency_minutes": round((rows[index][0] - start).total_seconds() / 60, 1),
                "method": "first_two_consecutive_samples_at_least_one_point_above_window_minimum",
            }
    return {
        "detected": False,
        "timestamp_local": None,
        "latency_minutes": None,
        "method": "first_two_consecutive_samples_at_least_one_point_above_window_minimum",
    }


def _garmin_nap_evidence(snapshot: dict) -> dict:
    sleep = _payload_data(snapshot, "get_sleep_data") or {}
    sleep_dto = sleep.get("dailySleepDTO") if isinstance(sleep, dict) else {}
    sleep_dto = sleep_dto if isinstance(sleep_dto, dict) else {}
    nap_seconds = as_number(sleep_dto.get("napTimeSeconds"))
    events = _payload_data(snapshot, "get_body_battery_events")
    events = events if isinstance(events, list) else []
    nap_events = [
        event
        for event in events
        if isinstance(event, dict) and "nap" in str(event).lower()
    ]
    if nap_seconds is not None and nap_seconds > 0:
        nap_status = "explicit_positive"
    elif nap_seconds == 0:
        nap_status = "reported_zero_non_exclusionary"
    else:
        nap_status = "missing_non_exclusionary"
    return {
        "nap_time_seconds": nap_seconds,
        "nap_time_status": nap_status,
        "matching_body_battery_event_count": len(nap_events),
        "interpretation": (
            "A positive Garmin label can support occurrence, but zero, empty, or missing data never "
            "disproves an athlete-confirmed nap or rest window."
        ),
    }


def _health_context(feedback: dict, review: dict) -> dict:
    values = []
    for source in (review, feedback):
        if not isinstance(source, dict):
            continue
        for key in ("illness_status", "health_status", "systemic_illness_status"):
            if source.get(key) is not None:
                values.append((key, str(source.get(key)).strip().lower()))
    if not values:
        return {"status": "unknown", "source": None, "interpretation": "No structured illness status was reported."}
    mapping = {
        "well": "none",
        "healthy": "none",
        "none": "none",
        "no_symptoms": "none",
        "suspected": "suspected",
        "symptomatic": "active",
        "sick": "active",
        "active": "active",
        "recovering": "recovering",
        "resolved": "recovering",
    }
    mapped = [
        {"source": key, "reported_value": raw, "status": mapping.get(raw, "unknown")}
        for key, raw in values
    ]
    priority = {"unknown": 0, "none": 1, "recovering": 2, "suspected": 3, "active": 4}
    selected = max(mapped, key=lambda item: priority[item["status"]])
    known_statuses = {item["status"] for item in mapped if item["status"] != "unknown"}
    return {
        "status": selected["status"],
        "reported_value": selected["reported_value"],
        "source": selected["source"],
        "reported_evidence": mapped,
        "conflict": len(known_statuses) > 1,
        "resolution_rule": "conservative_precedence_active_then_suspected_then_recovering_then_none",
        "interpretation": (
            "Structured athlete health context; Garmin physiology alone is not an illness diagnosis. "
            "Conflicting structured reports resolve to the more conservative status."
        ),
    }


def _activity_datetime(value: Any, tz: ZoneInfo) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def _activity_rows_with_times(
    activities: list[dict],
    tz: ZoneInfo,
    target: date | None = None,
) -> list[dict]:
    rows = []
    for activity in activities:
        activity_day = parse_date(activity.get("date"))
        if target is not None and (
            activity_day is None
            or activity_day < target - timedelta(days=3)
            or activity_day > target
        ):
            continue
        row = dict(activity)
        start = _activity_datetime(row.get("start_time_local"), tz)
        end = _activity_datetime(row.get("end_time_local"), tz)
        source_file = row.get("source_file")
        raw = (
            read_json(Path(source_file), {})
            if source_file and (start is None or end is None)
            else {}
        )
        if start is None:
            start = _activity_datetime(
                find_value(raw, ("startTimeLocal", "start_time_local")),
                tz,
            )
        if end is None:
            end = _activity_datetime(
                find_value(raw, ("endTimeLocal", "end_time_local")),
                tz,
            )
        end_derivation = "explicit_end_time_local" if end is not None else None
        elapsed_seconds = as_number(
            find_value(raw, ("elapsedDuration", "elapsedDurationSeconds", "elapsed_duration"))
        )
        elapsed_minutes = (
            elapsed_seconds / 60
            if elapsed_seconds is not None
            else as_number(row.get("elapsed_duration_min"))
        )
        active_duration = as_number(row.get("duration_min"))
        if start is not None and end is None and elapsed_minutes is not None:
            end = start + timedelta(minutes=elapsed_minutes)
            end_derivation = "start_plus_elapsed_duration"
        elif start is not None and end is None and active_duration is not None:
            end = start + timedelta(minutes=active_duration)
            end_derivation = "start_plus_active_duration_fallback"
        row["start_time_local"] = start.isoformat() if start else None
        row["end_time_local"] = end.isoformat() if end else None
        row["end_time_derivation"] = end_derivation
        rows.append(row)
    return rows


def _preceding_48h_load(
    activity_rows: list[dict],
    window_start: datetime,
    tz: ZoneInfo,
) -> dict:
    period_start = window_start - timedelta(hours=48)
    included = []
    missing_exact_time = 0
    for row in activity_rows:
        start = _activity_datetime(row.get("start_time_local"), tz)
        end = _activity_datetime(row.get("end_time_local"), tz)
        if start is None or end is None:
            if parse_date(row.get("date")) in {period_start.date(), window_start.date()}:
                missing_exact_time += 1
            continue
        if end < period_start or end > window_start:
            continue
        included.append((end, row))
    included.sort(key=lambda item: item[0])
    training_rows = [row for _, row in included if row.get("counts_for_training_load", True)]
    mtb_rows = [(end, row) for end, row in included if row.get("category") == "mtb"]
    latest_mtb_end = mtb_rows[-1][0] if mtb_rows else None
    derivations: dict[str, int] = {}
    for _, row in included:
        derivation = str(row.get("end_time_derivation") or "provided_by_caller")
        derivations[derivation] = derivations.get(derivation, 0) + 1
    return {
        "period_start_local": period_start.isoformat(timespec="minutes"),
        "period_end_local": window_start.isoformat(timespec="minutes"),
        "session_count": len(training_rows),
        "duration_minutes": round(sum(as_number(row.get("duration_min")) or 0 for row in training_rows), 1),
        "training_load": round(sum(as_number(row.get("training_load")) or 0 for row in training_rows), 1),
        "mtb_session_count": len(mtb_rows),
        "mtb_duration_minutes": round(sum(as_number(row.get("duration_min")) or 0 for _, row in mtb_rows), 1),
        "mtb_training_load": round(sum(as_number(row.get("training_load")) or 0 for _, row in mtb_rows), 1),
        "mtb_z4_z5_minutes": round(
            sum(
                (as_number((row.get("hr_zone_min") or {}).get("z4")) or 0)
                + (as_number((row.get("hr_zone_min") or {}).get("z5")) or 0)
                for _, row in mtb_rows
            ),
            1,
        ),
        "hours_since_latest_mtb": round((window_start - latest_mtb_end).total_seconds() / 3600, 1)
        if latest_mtb_end
        else None,
        "exact_time_missing_rows": missing_exact_time,
        "end_time_derivations": dict(sorted(derivations.items())),
        "active_duration_fallback_rows": derivations.get(
            "start_plus_active_duration_fallback",
            0,
        ),
        "sessions": [
            {
                "date": row.get("date"),
                "end_time_local": end.isoformat(timespec="minutes"),
                "end_time_derivation": row.get("end_time_derivation")
                or "provided_by_caller",
                "category": row.get("category"),
                "duration_min": row.get("duration_min"),
                "training_load": row.get("training_load"),
            }
            for end, row in included
        ],
        "method": "activity_end_within_preceding_48_hours_using_explicit_end_then_elapsed_duration",
        "future_leakage_guard": "Activities ending after the reported rest-window start are excluded.",
    }


def _classification(
    manual: dict,
    stress_during: dict,
    body_during: dict,
) -> dict:
    missing = []
    if manual.get("status") != "completed_valid":
        missing.extend(["completed_manual_window", "valid_start_end"])
    stress_coverage = as_number(stress_during.get("coverage_pct"))
    stress_mean = as_number(stress_during.get("mean"))
    rest_pct = as_number(stress_during.get("rest_range_pct_le_25"))
    adequate_stress = (
        (stress_during.get("valid_samples") or 0) >= MIN_STRESS_SAMPLES
        and stress_coverage is not None
        and stress_coverage >= MIN_STRESS_COVERAGE_PCT
    )
    if not adequate_stress:
        missing.append("adequate_during_stress_coverage")
    quiet = (
        adequate_stress
        and stress_mean is not None
        and stress_mean <= QUIET_STRESS_MAX
        and rest_pct is not None
        and rest_pct >= QUIET_REST_SHARE_MIN_PCT
    )
    bb_delta = as_number(body_during.get("delta"))
    paired_bb = body_during.get("boundary_pair_valid") is True and bb_delta is not None
    if not paired_bb:
        missing.append("paired_body_battery_boundary_samples")
    recharge = paired_bb and bb_delta >= BODY_BATTERY_RECHARGE_MIN
    inertia = as_number(manual.get("sleep_inertia_minutes"))
    clarity_low = as_number(manual.get("post_clarity_low_10"))
    pre_clarity_high = as_number(manual.get("pre_clarity_high_10"))
    clarity_improvement = (
        clarity_low - pre_clarity_high
        if clarity_low is not None and pre_clarity_high is not None
        else None
    )
    subjective_positive = (
        inertia is not None
        and inertia <= POSITIVE_INERTIA_MAX_MIN
        and (
            (clarity_low is not None and clarity_low >= POSITIVE_CLARITY_MIN)
            or (clarity_improvement is not None and clarity_improvement >= 1)
        )
    )
    subjective_negative = (
        (inertia is not None and inertia >= NEGATIVE_INERTIA_MIN)
        or (clarity_low is not None and clarity_low <= NEGATIVE_CLARITY_MAX)
        or (clarity_improvement is not None and clarity_improvement <= -2)
    )
    if inertia is None:
        missing.append("sleep_inertia_minutes")
    if clarity_low is None:
        missing.append("post_clarity")
    objective_strain = adequate_stress and (
        (stress_mean is not None and stress_mean >= 40)
        or (rest_pct is not None and rest_pct < 40)
    )
    if paired_bb and bb_delta <= -2:
        objective_strain = True

    reasons = []
    if quiet:
        reasons.append("During-window stress was adequately sampled and predominantly in Garmin's rest range.")
    if recharge:
        reasons.append(f"Body Battery rose {bb_delta:.1f} points across paired during-window samples.")
    if subjective_positive:
        reasons.append("Post-window clarity was at least 7/10 with sleep inertia cleared within 20 minutes.")
    if subjective_negative:
        reasons.append("The athlete-reported cognitive response remained impaired after the window.")
    if objective_strain:
        reasons.append("Objective physiology remained strained or Body Battery declined materially.")

    if manual.get("status") != "completed_valid":
        label = "insufficient_evidence"
    elif (
        objective_strain
        or (subjective_positive and subjective_negative)
        or (subjective_negative and (quiet or recharge))
    ):
        label = "discordant"
    elif sum(bool(item) for item in (quiet, recharge, subjective_positive)) >= 2 and (quiet or recharge):
        label = "restorative"
    elif quiet and paired_bb and not recharge and not subjective_positive and not subjective_negative:
        label = "quiet_but_non_restorative"
    else:
        label = "insufficient_evidence"

    complete_channels = sum(
        (
            adequate_stress,
            paired_bb,
            inertia is not None and clarity_low is not None,
        )
    )
    confidence = "high" if complete_channels == 3 else "medium" if complete_channels == 2 else "low"
    if label == "insufficient_evidence":
        confidence = "low"
    return {
        "label": label,
        "confidence": confidence,
        "rule_version": CLASSIFICATION_VERSION,
        "signals": {
            "quiet_stress": bool(quiet),
            "body_battery_recharge": bool(recharge),
            "subjective_positive": bool(subjective_positive),
            "subjective_negative": bool(subjective_negative),
            "objective_strain": bool(objective_strain),
        },
        "reasons": reasons,
        "missing_fields": sorted(set(missing)),
        "thresholds": {
            "minimum_stress_samples": MIN_STRESS_SAMPLES,
            "minimum_stress_coverage_pct": MIN_STRESS_COVERAGE_PCT,
            "quiet_mean_stress_max": QUIET_STRESS_MAX,
            "quiet_rest_range_share_min_pct": QUIET_REST_SHARE_MIN_PCT,
            "body_battery_recharge_min_points": BODY_BATTERY_RECHARGE_MIN,
            "body_battery_boundary_max_gap_minutes": BODY_BATTERY_BOUNDARY_MAX_GAP_MIN,
            "positive_post_clarity_min_10": POSITIVE_CLARITY_MIN,
            "positive_inertia_max_minutes": POSITIVE_INERTIA_MAX_MIN,
        },
    }


def _safety_contract() -> dict:
    return {
        "upward_training_clearance": "none",
        "may_raise_physical_readiness": False,
        "may_raise_cns_ceiling": False,
        "may_raise_technical_ceiling": False,
        "may_remove_primary_sleep_gate": False,
        "may_authorize_high_consequence": False,
        "body_battery_only_promotion_allowed": False,
        "primary_sleep_gate_preserved": True,
        "cns_ceiling_preserved": True,
        "interpretation": (
            "Rest/recharge evidence can confirm an intraday recovery response or support a downshift. "
            "It cannot upgrade the written session or override readiness, CNS, sleep, freshness, density, "
            "Sabbath, illness, or technical-consequence constraints."
        ),
    }


def analyze_rest_recharge_window(
    *,
    target_date: date,
    timezone_name: str,
    wellness_snapshot: dict,
    feedback: dict,
    normalized_wellness: dict,
    wellness_history: list[dict] | None = None,
    activity_rows: list[dict] | None = None,
    adequate_sleep_hours: float = ADEQUATE_PRIMARY_SLEEP_H,
) -> dict:
    tz = ZoneInfo(timezone_name)
    manual = _manual_window(feedback, target_date, tz)
    public_manual = {key: value for key, value in manual.items() if not key.startswith("_")}
    wellness_snapshot_date = parse_date(wellness_snapshot.get("date"))
    wellness_snapshot_current = wellness_snapshot_date == target_date
    all_day_record = (
        _payload_record(wellness_snapshot, "get_all_day_stress", "get_stress_data")
        if wellness_snapshot_current
        else {}
    )
    all_day_candidate = (
        all_day_record.get("data") if isinstance(all_day_record.get("data"), dict) else {}
    )
    endpoint_calendar_date = parse_date(
        all_day_candidate.get("calendarDate") or all_day_candidate.get("date")
    )
    endpoint_date_mismatch = (
        endpoint_calendar_date is not None and endpoint_calendar_date != target_date
    )
    all_day = {} if endpoint_date_mismatch else all_day_candidate
    stress, sentinel_count = _stress_samples(all_day, tz)
    body_battery = _body_battery_samples(all_day, tz)
    stress_cadence = _cadence_seconds(stress)
    body_cadence = _cadence_seconds(body_battery)
    start = manual.get("_start")
    end = manual.get("_end")

    empty_summary = {
        "expected_samples": 0,
        "valid_samples": 0,
        "coverage_pct": 0.0,
        "cadence_seconds": None,
        "first_sample_local": None,
        "last_sample_local": None,
    }
    if start is not None and end is not None:
        pre_start = start - timedelta(minutes=PRE_WINDOW_MIN)
        post_end = end + timedelta(minutes=POST_WINDOW_MIN)
        stress_pre_rows = _slice(stress, pre_start, start)
        stress_during_rows = _slice(stress, start, end)
        stress_post_rows = _slice(stress, end, post_end)
        body_pre_rows = _slice(body_battery, pre_start, start)
        body_during_rows = _slice(body_battery, start, end)
        body_post_rows = _slice(body_battery, end, post_end, include_end=True)
        stress_pre = _stress_summary(stress_pre_rows, pre_start, start, stress_cadence)
        stress_during = _stress_summary(stress_during_rows, start, end, stress_cadence)
        stress_post = _stress_summary(stress_post_rows, end, post_end, stress_cadence)
        body_pre = _body_battery_summary(body_pre_rows, pre_start, start, body_cadence)
        body_during = _body_battery_summary(body_during_rows, start, end, body_cadence)
        body_post = _body_battery_summary(body_post_rows, end, post_end, body_cadence)
        onset = _recharge_onset(
            _slice(body_battery, start, post_end, include_end=True),
            start,
        )
        load_48h = _preceding_48h_load(activity_rows or [], start, tz)
    else:
        stress_pre = {**empty_summary, "mean": None, "rest_range_pct_le_25": None}
        stress_during = dict(stress_pre)
        stress_post = dict(stress_pre)
        body_pre = {**empty_summary, "start_level": None, "end_level": None, "delta": None}
        body_during = dict(body_pre)
        body_post = dict(body_pre)
        onset = {"detected": False, "timestamp_local": None, "latency_minutes": None}
        load_48h = None

    classification = _classification(manual, stress_during, body_during)
    primary_sleep = as_number(
        normalized_wellness.get("primary_sleep_hours")
        if normalized_wellness.get("primary_sleep_hours") is not None
        else normalized_wellness.get("sleep_hours")
    )
    shortfall = max(0.0, adequate_sleep_hours - primary_sleep) if primary_sleep is not None else None
    history = [
        row
        for row in wellness_history or []
        if isinstance(row, dict)
        and parse_date(row.get("date")) is not None
        and target_date - timedelta(days=6) <= parse_date(row.get("date")) <= target_date
    ]
    recent_primary = [
        as_number(row.get("primary_sleep_hours") if row.get("primary_sleep_hours") is not None else row.get("sleep_hours"))
        for row in history
    ]
    recent_primary = [value for value in recent_primary if value is not None]
    avg_primary_7d = round(mean(recent_primary), 2) if recent_primary else None
    rolling_shortfalls = [max(0.0, adequate_sleep_hours - value) for value in recent_primary]
    sleep_debt_proxy = {
        "window_days": 7,
        "observed_days": len(recent_primary),
        "coverage_pct": round(len(recent_primary) / 7 * 100, 1),
        "threshold_hours_per_night": adequate_sleep_hours,
        "cumulative_shortfall_hours": round(sum(rolling_shortfalls), 2)
        if rolling_shortfalls
        else None,
        "average_shortfall_hours_per_observed_night": round(mean(rolling_shortfalls), 2)
        if rolling_shortfalls
        else None,
        "label": "sleep_opportunity_shortfall_proxy_not_clinical_sleep_debt",
        "interpretation": (
            "Sum of observed primary-sleep shortfalls versus the architecture threshold; missing days "
            "are not imputed and this is not a biological or clinical sleep-debt estimate."
        ),
    }
    latest_attempt = all_day_record.get("latest_attempt") if isinstance(all_day_record.get("latest_attempt"), dict) else None
    endpoint_status = all_day_record.get("last_attempt_status") or all_day_record.get("status")
    data_cutoff = all_day.get("endTimestampLocal")
    status = "available" if manual.get("status") == "completed_valid" else "insufficient_evidence"
    if not all_day:
        status = "insufficient_evidence"

    return {
        "artifact_type": "rest_recharge_window",
        "date": target_date.isoformat(),
        "generated_at": iso_now(timezone_name),
        "status": status,
        "classification": classification,
        "window": public_manual,
        "garmin_nap_evidence": _garmin_nap_evidence(
            wellness_snapshot if wellness_snapshot_current else {}
        ),
        "objective_response": {
            "analysis_windows": {
                "pre_minutes": PRE_WINDOW_MIN,
                "during": "reported_start_to_reported_end",
                "post_minutes": POST_WINDOW_MIN,
            },
            "stress": {
                "pre": stress_pre,
                "during": stress_during,
                "post": stress_post,
                "negative_sentinel_samples_excluded": sentinel_count,
                "stress_mean_change_during_minus_pre": round(
                    stress_during["mean"] - stress_pre["mean"], 2
                )
                if stress_during.get("mean") is not None and stress_pre.get("mean") is not None
                else None,
            },
            "body_battery": {
                "pre": body_pre,
                "during": body_during,
                "post": body_post,
                "delta_during": body_during.get("delta"),
                "delta_pre_end_to_post_end": round(
                    body_post["end_level"] - body_pre["end_level"], 1
                )
                if body_post.get("end_level") is not None and body_pre.get("end_level") is not None
                else None,
                "recharge_onset": onset,
            },
        },
        "context": {
            "primary_sleep_hours": primary_sleep,
            "rolling_7d_primary_sleep_hours": avg_primary_7d,
            "sleep_opportunity_debt_proxy": sleep_debt_proxy,
            "shortfall_to_architecture_threshold": {
                "threshold_hours": adequate_sleep_hours,
                "shortfall_hours": round(shortfall, 2) if shortfall is not None else None,
                "label": "architecture_sleep_opportunity_shortfall_not_clinical_sleep_debt",
                "primary_sleep_gate_preserved": True,
            },
            "illness": _health_context(feedback, manual),
            "preceding_48h_load": load_48h,
        },
        "provenance": {
            "feedback_file": f"input/feedback_{target_date.isoformat()}.json",
            "wellness_file": (
                f"snapshots/garmin_wellness_{wellness_snapshot_date.isoformat()}.json"
                if wellness_snapshot_date
                else None
            ),
            "source_endpoint": "get_all_day_stress",
            "endpoint_data_status": (
                "available"
                if all_day
                else "response_date_mismatch"
                if endpoint_date_mismatch
                else "wrong_date"
                if wellness_snapshot_date and not wellness_snapshot_current
                else "missing"
            ),
            "endpoint_last_attempt_status": endpoint_status,
            "endpoint_last_attempt_at": (latest_attempt or {}).get("attempted_at")
            or all_day_record.get("attempted_at"),
            "endpoint_last_success_at": all_day_record.get("last_success_at")
            or (
                all_day_record.get("attempted_at")
                if all_day_record.get("status") == "success"
                else None
            ),
            "endpoint_data_retained_after_degraded_attempt": bool(
                all_day
                and endpoint_status
                in {"failed", "unsupported", "success_empty", "not_attempted"}
                and all_day_record.get("retention_policy")
                == "preserve_last_nonempty_success"
            ),
            "endpoint_data_cutoff_local": data_cutoff,
            "retention_policy": all_day_record.get("retention_policy"),
            "raw_privacy": wellness_snapshot.get("privacy") or "raw_private_local_only",
            "derived_privacy_check": "No raw profile identifier is copied into this artifact.",
        },
        "safety_contract": _safety_contract(),
    }


def build_rest_recharge_window(
    root: str | Path | None = None,
    for_date: str | date | None = None,
    *,
    wellness_snapshot: dict | None = None,
    feedback: dict | None = None,
    normalized_wellness: dict | None = None,
    wellness_history: list[dict] | None = None,
    activity_rows: list[dict] | None = None,
) -> dict:
    context = load_context(root)
    timezone_name = context.get("athlete", {}).get("timezone", DEFAULT_TIMEZONE)
    target = parse_date(for_date) or today_local(timezone_name)
    base = Path(root or ".").resolve()
    wellness_snapshot = wellness_snapshot or read_json(
        snapshots_dir(root) / f"garmin_wellness_{target.isoformat()}.json",
        {},
    )
    feedback_path = base / "input" / f"feedback_{target.isoformat()}.json"
    feedback = feedback if feedback is not None else read_json(feedback_path, {})
    if not isinstance(feedback, dict) or parse_date(feedback.get("date")) != target:
        feedback = {}
    if normalized_wellness is None:
        normalized_wellness = (
            normalize_wellness_payload(wellness_snapshot)
            if parse_date(wellness_snapshot.get("date")) == target
            else {}
        )
    elif parse_date(normalized_wellness.get("date")) != target:
        normalized_wellness = {}
    if wellness_history is None:
        wellness_history = read_json(snapshots_dir(root) / "wellness_daily.json", [])
        wellness_history = [
            row
            for row in wellness_history
            if isinstance(row, dict)
            and parse_date(row.get("date")) is not None
            and parse_date(row.get("date")) <= target
        ]
    if activity_rows is None:
        activity_rows = _activity_rows_with_times(
            load_activities(root),
            ZoneInfo(timezone_name),
            target,
        )
    artifact = analyze_rest_recharge_window(
        target_date=target,
        timezone_name=timezone_name,
        wellness_snapshot=wellness_snapshot,
        feedback=feedback,
        normalized_wellness=normalized_wellness,
        wellness_history=wellness_history,
        activity_rows=activity_rows,
    )
    write_json(snapshots_dir(root) / "rest_recharge_window.json", artifact)
    write_json(
        snapshots_dir(root) / f"rest_recharge_window_{target.isoformat()}.json",
        artifact,
    )
    return artifact
