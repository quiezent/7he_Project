from __future__ import annotations

import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .context import load_context
from .device_audit import EXTERNAL_HR_TYPES
from .evidence import (
    dated_snapshot_files,
    load_activities,
    load_latest_training_status,
    load_latest_wellness,
    summarize_recent_training,
)
from .gear_audit import ELITE_SUITO_TERMS, gear_matches
from .io import read_json, write_json, write_text
from .paths import activities_dir, context_path, ensure_layout, snapshots_dir
from .planning import build_today_plan
from .predictive_training import (
    _actual_activity_summary,
    _actual_next_day_response,
    _compare_prediction,
    _contract_quality_review,
    _self_evaluation_for_date,
    build_predictive_prescription,
)
from .readiness import build_readiness
from .state import determine_phase
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local
from .training_status import normalize_training_status_payload
from .wellness import build_wellness_daily, build_wellness_trends


DEFAULT_BACKTEST_DATES = [
    "2021-07-29",
    "2021-12-09",
    "2022-01-29",
    "2026-04-11",
    "2026-04-23",
    "2026-04-29",
    "2026-05-09",
    "2026-05-15",
    "2026-05-25",
    "2026-05-27",
]

DATE_RATIONALES = {
    "2021-07-29": "Extreme mixed-load day to test overload detection.",
    "2021-12-09": "Long MTB day followed by strong recovery; useful positive MTB contrast.",
    "2022-01-29": "Hard MTB stress case with high z4/z5 time.",
    "2026-04-11": "Hard elliptical day with good following-day response.",
    "2026-04-23": "Hard elliptical day with poor following-day response.",
    "2026-04-29": "Controlled indoor bike day with modern wellness coverage.",
    "2026-05-09": "Long low-intensity mixed aerobic day with self-evaluation.",
    "2026-05-15": "Modern hard MTB day with poor following-day response.",
    "2026-05-25": "Structured indoor tempo case for current rebuild progression.",
    "2026-05-27": "Long MTB durability case; device confidence caveat applies.",
}


def _round(value: float | None, digits: int = 1) -> float | None:
    return round(value, digits) if value is not None else None


def _copy_context(source_root: str | Path | None, dest_root: Path) -> None:
    source = context_path(source_root)
    if source.exists():
        destination = context_path(dest_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    else:
        load_context(dest_root)


def _copy_dated_snapshots(
    source_root: str | Path | None,
    dest_root: Path,
    prefix: str,
    cutoff: date,
) -> None:
    destination = snapshots_dir(dest_root)
    destination.mkdir(parents=True, exist_ok=True)
    for snapshot_date, path in dated_snapshot_files(source_root, prefix):
        if snapshot_date <= cutoff:
            shutil.copy2(path, destination / path.name)


def _copy_pre_session_activities(
    source_root: str | Path | None,
    dest_root: Path,
    target: date,
) -> None:
    source_base = activities_dir(source_root)
    dest_base = activities_dir(dest_root)
    dest_base.mkdir(parents=True, exist_ok=True)
    for activity in load_activities(source_root):
        activity_date = parse_date(activity.get("date"))
        if activity_date is None or activity_date >= target:
            continue
        source_file = activity.get("source_file")
        if not source_file:
            continue
        source_path = Path(source_file)
        try:
            relative = source_path.relative_to(source_base)
        except ValueError:
            relative = Path(source_path.name)
        destination = dest_base / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination)


def _prepare_pre_session_root(source_root: str | Path | None, dest_root: Path, target: date) -> None:
    ensure_layout(dest_root)
    _copy_context(source_root, dest_root)
    _copy_dated_snapshots(source_root, dest_root, "garmin_wellness", target)
    _copy_dated_snapshots(source_root, dest_root, "garmin_training_status", target)
    _copy_pre_session_activities(source_root, dest_root, target)


def _data_freshness(root: str | Path | None, target: date, training: dict) -> dict:
    wellness_date, _ = load_latest_wellness(root, target)
    if wellness_date is None:
        wellness_freshness = {"status": "missing", "message": "No Garmin wellness data has been synced."}
    else:
        age = (target - wellness_date).days
        if age < 0:
            wellness_freshness = {
                "status": "future",
                "age_days": age,
                "message": "Latest wellness snapshot is dated after the target date.",
            }
        elif age == 0:
            wellness_freshness = {
                "status": "current",
                "age_days": age,
                "message": "Garmin wellness data is current.",
            }
        else:
            wellness_freshness = {
                "status": "stale",
                "age_days": age,
                "message": f"Garmin wellness data is {age} day(s) behind the decision date.",
            }

    training_status_date, _ = load_latest_training_status(root, target)
    if training_status_date is None:
        training_status_freshness = {
            "status": "missing",
            "message": "No Garmin training status snapshot is available.",
        }
    else:
        status_age = (target - training_status_date).days
        if status_age < 0:
            training_status_freshness = {
                "status": "future",
                "age_days": status_age,
                "message": "Latest training status is dated after the target date.",
            }
        elif status_age == 0:
            training_status_freshness = {
                "status": "current",
                "age_days": status_age,
                "message": "Garmin training status is current.",
            }
        else:
            training_status_freshness = {
                "status": "stale",
                "age_days": status_age,
                "message": f"Latest training status is {status_age} day(s) old.",
            }

    latest_activity = training.get("latest_training_activity") or training.get("latest_activity")
    hard_limiters = []
    if wellness_freshness.get("status") != "current":
        hard_limiters.append(wellness_freshness.get("message"))
    if training_status_freshness.get("status") != "current":
        hard_limiters.append(training_status_freshness.get("message"))
    if latest_activity is None:
        activity_freshness = {
            "status": "missing",
            "message": "No Garmin activity data is available to verify recent load.",
        }
        hard_limiters.append(activity_freshness["message"])
    else:
        latest_activity_date = parse_date(latest_activity.get("date"))
        activity_age = (target - latest_activity_date).days if latest_activity_date else None
        if activity_age is None:
            activity_freshness = {
                "status": "unknown",
                "message": "Latest activity has no usable date.",
            }
            hard_limiters.append(activity_freshness["message"])
        elif activity_age > 7:
            activity_freshness = {
                "status": "stale",
                "age_days": activity_age,
                "latest_activity_date": latest_activity_date.isoformat(),
                "message": f"Latest activity is {activity_age} day(s) old.",
            }
            hard_limiters.append(activity_freshness["message"])
        else:
            activity_freshness = {
                "status": "current",
                "age_days": activity_age,
                "latest_activity_date": latest_activity_date.isoformat(),
                "message": "Recent pre-session activity data is available.",
            }
    freshness_statuses = (
        wellness_freshness.get("status"),
        training_status_freshness.get("status"),
        activity_freshness.get("status"),
    )
    if "missing" in freshness_statuses:
        overall_status = "missing"
    elif "stale" in freshness_statuses or "future" in freshness_statuses or "unknown" in freshness_statuses:
        overall_status = "stale"
    else:
        overall_status = "current"
    freshness = {
        "status": overall_status,
        "age_days": wellness_freshness.get("age_days"),
        "latest_wellness_date": wellness_date.isoformat() if wellness_date else None,
        "message": (
            "Garmin readiness data is current."
            if overall_status == "current"
            else next((item for item in hard_limiters if item), "Readiness data is not current; limit hard-session confidence.")
        ),
        "wellness_data": wellness_freshness,
        "training_status_data": training_status_freshness,
        "activity_data": activity_freshness,
        "hard_session_confidence": "normal" if not hard_limiters else "limited",
        "hard_session_limiters": [item for item in hard_limiters if item],
    }
    return freshness


def _pre_session_state(root: str | Path | None, target: date) -> dict:
    context = load_context(root)
    tz = context.get("athlete", {}).get("timezone", DEFAULT_TIMEZONE)
    readiness = build_readiness(root, target)
    activities = load_activities(root)
    training = summarize_recent_training(activities, target)
    wellness_date, wellness = load_latest_wellness(root, target)
    training_status_date, training_status = load_latest_training_status(root, target)
    wellness_trends = build_wellness_trends(root, target)
    latest_body_composition = wellness_trends.get("latest_body_composition") or {}
    athlete = dict(context.get("athlete", {}))
    if athlete.get("body_weight_kg") is None and latest_body_composition.get("body_weight_kg") is not None:
        athlete["body_weight_kg"] = latest_body_composition.get("body_weight_kg")
        athlete["body_weight_source"] = {
            "source": "garmin_body_composition",
            "date": latest_body_composition.get("date"),
            "age_days": latest_body_composition.get("age_days"),
        }
    return {
        "date": target.isoformat(),
        "generated_at": iso_now(tz),
        "athlete": athlete,
        "goal": context.get("athlete", {}).get("goal"),
        "phase": determine_phase(context, target),
        "readiness": readiness,
        "data_freshness": _data_freshness(root, target, training),
        "training_load": training,
        "body_composition": latest_body_composition or None,
        "latest_activity": training.get("latest_activity"),
        "latest_training_activity": training.get("latest_training_activity"),
        "evidence_sources": {
            "wellness_date": wellness_date.isoformat() if wellness_date else None,
            "training_status_date": training_status_date.isoformat() if training_status_date else None,
            "wellness_available": wellness is not None,
            "training_status_available": training_status is not None,
            "activity_count": len(activities),
        },
    }


def _build_pre_session_prescription(source_root: str | Path | None, target: date) -> dict:
    with tempfile.TemporaryDirectory(prefix=f"coach_predictive_{target.isoformat()}_") as temp:
        pre_root = Path(temp)
        _prepare_pre_session_root(source_root, pre_root, target)
        state = _pre_session_state(pre_root, target)
        plan = build_today_plan(pre_root, target, state=state)
        prescription = build_predictive_prescription(pre_root, target, state=state, plan=plan)
        prescription["backtest_context"] = {
            "mode": "pre_session_replay",
            "activity_cutoff": (target - timedelta(days=1)).isoformat(),
            "wellness_cutoff": target.isoformat(),
            "training_status_cutoff": target.isoformat(),
            "pre_session_readiness": {
                "score": state.get("readiness", {}).get("readiness_score"),
                "level": state.get("readiness", {}).get("readiness_level"),
                "hard_session_guidance": state.get("readiness", {}).get("hard_session_guidance"),
            },
            "data_freshness": state.get("data_freshness"),
        }
        return prescription


def _wellness_row(wellness_by_date: dict[str, dict], target: date) -> dict:
    row = wellness_by_date.get(target.isoformat()) or {}
    return {
        "body_battery_wake": row.get("body_battery_wake"),
        "body_battery_current": row.get("body_battery_current"),
        "body_battery_drain": row.get("body_battery_drain"),
        "overnight_hrv": row.get("overnight_hrv"),
        "hrv_status": row.get("hrv_status"),
        "sleep_score": row.get("sleep_score"),
        "sleep_hours": row.get("sleep_hours"),
        "sleep_stress": row.get("sleep_stress"),
        "resting_hr": row.get("resting_hr"),
        "avg_stress": row.get("avg_stress"),
    }


def _training_status_next_day(root: str | Path | None, target: date) -> dict:
    next_day = target + timedelta(days=1)
    snapshot_date, snapshot = load_latest_training_status(root, next_day)
    exact = snapshot_date == next_day
    normalized = normalize_training_status_payload(
        snapshot if exact else None,
        snapshot_date.isoformat() if exact and snapshot_date else None,
    )
    return {
        "available": exact,
        "target_date": next_day.isoformat(),
        "source_date": snapshot_date.isoformat() if snapshot_date else None,
        "training_status_feedback": normalized.get("training_status_feedback") if exact else None,
        "acwr": normalized.get("acute_chronic") if exact else None,
        "load_focus_feedback": (normalized.get("load_focus") or {}).get("feedback") if exact else None,
    }


def _read_index(root: str | Path | None, name: str) -> list[dict]:
    payload = read_json(snapshots_dir(root) / name, {})
    if isinstance(payload, dict):
        rows = payload.get("activities") or []
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    return [row for row in rows if isinstance(row, dict)]


def _device_confidence(root: str | Path | None, target: date) -> dict:
    rows = [row for row in _read_index(root, "activity_device_index.json") if row.get("date") == target.isoformat()]
    sensor_types = sorted(
        {
            str(sensor.get("sensor_type"))
            for row in rows
            for sensor in (row.get("sensors") if isinstance(row.get("sensors"), list) else [])
            if sensor.get("sensor_type")
        }
    )
    flags = []
    activity_rows = []
    confidences = []
    for row in rows:
        has_external_hr = bool(row.get("external_hr_sensor")) or any(
            sensor_type in EXTERNAL_HR_TYPES for sensor_type in sensor_types
        )
        battery_statuses = [str(item).upper() for item in row.get("external_hr_battery_statuses") or []]
        if has_external_hr and "LOW" in battery_statuses:
            hr_confidence = "external_hr_low_battery"
        elif has_external_hr:
            hr_confidence = "external_hr"
        else:
            hr_confidence = "wrist_hr_likely"
        confidences.append(hr_confidence)
        activity_rows.append(
            {
                "activity_id": row.get("activity_id"),
                "date": row.get("date"),
                "name": row.get("name"),
                "category": row.get("category"),
                "hr_confidence": hr_confidence,
                "sensor_types": sorted(
                    {
                        item.get("sensor_type")
                        for item in (row.get("sensors") if isinstance(row.get("sensors"), list) else [])
                        if item.get("sensor_type")
                    }
                ),
            }
        )
        if row.get("category") == "mtb" and hr_confidence == "wrist_hr_likely":
            flags.append("MTB activity has no external HEART_RATE sensor; HR/load confidence is lower.")
        if hr_confidence == "external_hr_low_battery":
            flags.append("External HR sensor battery was low.")
    if "wrist_hr_likely" in confidences:
        aggregate = "wrist_hr_likely"
    elif "external_hr_low_battery" in confidences:
        aggregate = "external_hr_low_battery"
    elif "external_hr" in confidences:
        aggregate = "external_hr"
    else:
        aggregate = "unknown"
    return {
        "available": bool(rows),
        "hr_confidence": aggregate,
        "sensor_types": sensor_types,
        "flags": sorted(set(flags)),
        "activities": activity_rows,
    }


def _gear_context(root: str | Path | None, target: date) -> dict:
    rows = [row for row in _read_index(root, "activity_gear_index.json") if row.get("date") == target.isoformat()]
    flags = []
    activity_rows = []
    for row in rows:
        gear = row.get("gear") if isinstance(row.get("gear"), list) else []
        labels = [item.get("label") for item in gear if item.get("label")]
        if row.get("category") == "mtb" and gear_matches(gear, ELITE_SUITO_TERMS):
            flags.append("MTB activity is tagged with Elite Suito gear; Garmin Gear needs correction.")
        activity_rows.append(
            {
                "activity_id": row.get("activity_id"),
                "date": row.get("date"),
                "name": row.get("name"),
                "category": row.get("category"),
                "gear_labels": labels,
            }
        )
    return {
        "available": bool(rows),
        "flags": sorted(set(flags)),
        "activities": activity_rows,
    }


def _row_for_date(
    root: str | Path | None,
    target: date,
    wellness_by_date: dict[str, dict],
) -> dict:
    prescription = _build_pre_session_prescription(root, target)
    prediction = prescription.get("prediction") or {}
    actual = _actual_activity_summary(root, target)
    self_eval = _self_evaluation_for_date(root, target)
    response = _actual_next_day_response(root, target)
    contract_quality = _contract_quality_review(root, target, prediction.get("expected_session") or {}, actual, self_eval, response)
    comparison = _compare_prediction(prediction, actual, self_eval, response, contract_quality)
    comparison["calibration_note"] = comparison.get("interpretation")
    next_day = target + timedelta(days=1)
    recovery = {
        **response,
        **_wellness_row(wellness_by_date, next_day),
    }
    return {
        "date": target.isoformat(),
        "target_date": next_day.isoformat(),
        "rationale": DATE_RATIONALES.get(target.isoformat()),
        "pre_session_context": prescription.get("backtest_context"),
        "prediction": {
            "source": "pre_session_replay",
            "model_confidence": prescription.get("model_confidence"),
            "warnings": prediction.get("warnings") or [],
            "expected_session": prediction.get("expected_session"),
            "expected_next_day_response": prediction.get("expected_next_day_response"),
            "coaching_adjusted_next_day_response": prediction.get("coaching_adjusted_next_day_response"),
            "execution_risk": prediction.get("execution_risk"),
            "prediction_path": prediction.get("prediction_path"),
        },
        "actual_session": actual,
        "self_evaluation": {
            "available": bool(self_eval.get("count")),
            **self_eval,
        },
        "next_day_recovery": recovery,
        "training_status_next_day": _training_status_next_day(root, target),
        "device_confidence": _device_confidence(root, target),
        "gear_context": _gear_context(root, target),
        "comparison": comparison,
    }


def _coverage(rows: list[dict]) -> dict:
    return {
        "dates": len(rows),
        "actual_load_duration_dates": sum(1 for row in rows if row.get("actual_session", {}).get("sessions", 0) > 0),
        "self_eval_dates": sum(1 for row in rows if row.get("self_evaluation", {}).get("available")),
        "next_day_wellness_dates": sum(
            1 for row in rows if row.get("next_day_recovery", {}).get("status") == "available"
        ),
        "next_day_training_status_dates": sum(
            1 for row in rows if row.get("training_status_next_day", {}).get("available")
        ),
        "device_confidence_dates": sum(1 for row in rows if row.get("device_confidence", {}).get("available")),
        "physiology_calibratable_dates": sum(
            1 for row in rows if row.get("comparison", {}).get("physiology_calibration_eligible")
        ),
        "contract_calibratable_dates": sum(
            1 for row in rows if row.get("comparison", {}).get("calibration_eligible")
        ),
    }


def _calibration_summary(rows: list[dict]) -> dict:
    adherence_counts: dict[str, int] = {}
    response_counts: dict[str, int] = {}
    contract_quality_counts: dict[str, int] = {}
    matched_errors = []
    contract_calibratable_errors = []
    all_errors = []
    stress_errors = []
    for row in rows:
        comparison = row.get("comparison") or {}
        adherence = comparison.get("adherence_status")
        response_status = comparison.get("response_status")
        if adherence:
            adherence_counts[adherence] = adherence_counts.get(adherence, 0) + 1
        if response_status:
            response_counts[response_status] = response_counts.get(response_status, 0) + 1
        contract_status = (comparison.get("contract_quality") or {}).get("status")
        if contract_status:
            contract_quality_counts[contract_status] = contract_quality_counts.get(contract_status, 0) + 1
        delta = comparison.get("response_delta")
        if delta is not None:
            all_errors.append(abs(delta))
            if adherence == "matched_expected_load":
                matched_errors.append(abs(delta))
            if comparison.get("calibration_eligible"):
                contract_calibratable_errors.append(abs(delta))
        stress = comparison.get("execution_risk_stress_test") or {}
        stress_delta = stress.get("response_delta")
        if stress.get("available") and adherence == "harder_than_predicted" and stress_delta is not None:
            stress_errors.append(abs(stress_delta))
    return {
        "adherence_counts": dict(sorted(adherence_counts.items())),
        "response_status_counts": dict(sorted(response_counts.items())),
        "contract_quality_status_counts": dict(sorted(contract_quality_counts.items())),
        "mean_abs_response_error_all": _round(sum(all_errors) / len(all_errors), 1) if all_errors else None,
        "mean_abs_response_error_matched_load": (
            _round(sum(matched_errors) / len(matched_errors), 1) if matched_errors else None
        ),
        "mean_abs_response_error_contract_calibratable": (
            _round(sum(contract_calibratable_errors) / len(contract_calibratable_errors), 1)
            if contract_calibratable_errors
            else None
        ),
        "mean_abs_stress_test_error_for_drifted_sessions": (
            _round(sum(stress_errors) / len(stress_errors), 1) if stress_errors else None
        ),
        "stress_test_drifted_count": len(stress_errors),
        "matched_load_count": len(matched_errors),
        "contract_calibratable_count": len(contract_calibratable_errors),
    }


def _text_report(artifact: dict) -> str:
    lines = [
        f"Predictive Backtest - {artifact['date']}",
        "",
        "Method: pre-session replay with activities visible only through the prior day, then actual day and next-day wellness revealed for review.",
        "",
        "Rows:",
    ]
    for row in artifact["rows"]:
        expected = (row.get("prediction") or {}).get("expected_session") or {}
        expected_response = (row.get("prediction") or {}).get("expected_next_day_response") or {}
        adjusted_response = (row.get("prediction") or {}).get("coaching_adjusted_next_day_response") or {}
        actual = row.get("actual_session") or {}
        comparison = row.get("comparison") or {}
        stress = comparison.get("execution_risk_stress_test") or {}
        quality = comparison.get("contract_quality") or {}
        recovery = row.get("next_day_recovery") or {}
        lines.append(
            "- {date}: prescribed {title} ({intensity}, {duration} min, load {expected_load}); "
            "actual {actual_categories}, {actual_duration} min, load {actual_load}; "
            "next-day {actual_score} vs expected {expected_score} / adjusted {adjusted_score}; "
            "stress-test {stress_score} ({stress_status}); {response_status}; {adherence}; contract {contract_status}".format(
                date=row.get("date"),
                title=expected.get("title"),
                intensity=expected.get("intensity"),
                duration=expected.get("duration_min"),
                expected_load=expected.get("expected_training_load"),
                actual_categories=actual.get("categories"),
                actual_duration=actual.get("duration_min"),
                actual_load=actual.get("training_load"),
                actual_score=recovery.get("score"),
                expected_score=expected_response.get("score"),
                adjusted_score=adjusted_response.get("score"),
                stress_score=stress.get("score"),
                stress_status=stress.get("response_status"),
                response_status=comparison.get("response_status"),
                adherence=comparison.get("adherence_status"),
                contract_status=quality.get("status"),
            )
        )
    lines.extend(
        [
            "",
            f"Coverage: {artifact['coverage']}",
            f"Calibration summary: {artifact['calibration_summary']}",
            "",
        ]
    )
    return "\n".join(lines)


def build_predictive_backtest(
    root: str | Path | None = None,
    dates: list[str | date] | None = None,
) -> dict:
    target_dates = [parse_date(item) for item in (dates or DEFAULT_BACKTEST_DATES)]
    if any(item is None for item in target_dates):
        raise ValueError("All backtest dates must be parseable YYYY-MM-DD values.")
    wellness_by_date = {
        row["date"]: row
        for row in build_wellness_daily(root)
        if isinstance(row, dict) and row.get("date")
    }
    rows = [_row_for_date(root, target, wellness_by_date) for target in target_dates if target is not None]
    artifact = {
        "date": today_local(DEFAULT_TIMEZONE).isoformat(),
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "artifact_type": "predictive_session_backtest_10_dates",
        "date_span": {
            "start": min(row["date"] for row in rows) if rows else None,
            "end": max(row["date"] for row in rows) if rows else None,
        },
        "selection": {
            "n": len(rows),
            "method": "hand_selected_varied_training_days_with_next_day_wellness",
            "dates": [row["date"] for row in rows],
        },
        "sources": {
            "prescription": "pre-session replay using dated Garmin snapshots and activities through D-1",
            "activities": "activities/ raw Garmin activity JSON via activity_summary_index builder",
            "self_evaluation": "snapshots/activity_self_evaluation_index.json",
            "wellness": "snapshots/garmin_wellness_<date>.json normalized by wellness_daily",
            "training_status": "snapshots/garmin_training_status_<date>.json when exact next-day snapshot exists",
            "device_confidence": "snapshots/activity_device_index.json",
            "gear": "snapshots/activity_gear_index.json",
        },
        "coverage": _coverage(rows),
        "calibration_summary": _calibration_summary(rows),
        "caveats": [
            "This is a replay with current code/config, not a recreation of older coaching rules.",
            "Prescription generation is pre-session bounded: activity data is copied only through the day before the target date.",
            "Review uses actual target-day activities plus following-day Garmin wellness.",
            "The response target is Garmin-derived recovery/readiness, not direct trail skill execution.",
            "If actual load differs from prescribed load, adherence is judged before model calibration.",
            "Only rows with a complete session contract, action alignment, explicit stop-rule outcome, and complete relevant review fields are full digital-twin calibration samples.",
        ],
        "rows": rows,
    }
    write_json(snapshots_dir(root) / "predictive_backtest_10_dates.json", artifact)
    write_json(snapshots_dir(root) / "predictive_backtest.json", artifact)
    write_text(snapshots_dir(root) / "predictive_backtest_10_dates.txt", _text_report(artifact))
    return artifact
