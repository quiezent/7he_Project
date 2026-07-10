from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
import hashlib
from pathlib import Path
import re
from statistics import median
from typing import Any

from .evidence import as_number
from .io import read_json, write_json, write_text
from .load_model import build_activity_summary_index
from .paths import input_dir, snapshots_dir
from .planning import SESSION_CONTRACT_FIELDS, build_today_plan
from .state import build_current_state
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local
from .training_predictor import (
    _activity_by_date,
    _activity_empty,
    _features_for_day,
    _predict,
    _readiness_level,
    _response_class,
    _target_score,
    build_training_predictor,
)
from .wellness import build_wellness_daily


LOAD_PER_HOUR = {
    "recovery": 15.0,
    "easy": 35.0,
    "moderate": 55.0,
    "moderate_hard": 70.0,
    "skill": 45.0,
    "hard": 85.0,
}

RPE_RANGES = {
    "recovery": [10, 20],
    "easy": [20, 40],
    "moderate": [30, 50],
    "moderate_hard": [40, 65],
    "skill": [25, 45],
    "hard": [50, 80],
}

MTB_SESSION_TYPES = {
    "endurance_skills",
    "mtb_durability_enduro",
    "mtb_quality_skill",
    "mtb_repeatability_controlled",
    "mtb_skill_transfer_optional",
    "outdoor_bike_optional",
    "outdoor_mtb",
}
INDOOR_BIKE_SESSION_TYPES = {
    "bike_quality",
    "easy_bike_continuity",
    "indoor_tempo_torque",
    "garmin_aerobic_continuity",
}
MIN_EXECUTION_PROFILE_SAMPLES = 3

CONTRACT_REVIEW_KEYS = (
    "session_contract_review",
    "contract_review",
    "post_session_review",
    "review",
)
FUELING_REVIEW_FIELDS = {
    "fueling_carbs_g_per_hour",
    "fluid_ml_per_hour",
    "sodium_mg_per_hour",
}
TECHNICAL_REVIEW_FIELDS = {
    "technical_quality_notes",
    "late_session_skill_fade",
}
REVIEW_FIELD_ALIASES = {
    "actual_rpe": ("actual_rpe", "rpe", "rpe_score", "direct_workout_rpe"),
    "workout_feel": ("workout_feel", "feel", "feel_score", "direct_workout_feel"),
    "technical_quality_notes": (
        "technical_quality_notes",
        "technical_notes",
        "skill_quality",
        "technical_quality",
    ),
    "late_session_skill_fade": (
        "late_session_skill_fade",
        "late_skill_fade",
        "skill_fade",
        "late_ride_skill_fade",
    ),
}
TECHNICAL_NOTE_KEYS = {
    "technical_quality_notes",
    "technical_notes",
    "skill_quality",
    "technical_focus",
    "session_notes",
    "rider_report",
    "subjective_report",
    "coaching_interpretation",
}
TECHNICAL_NOTE_MARKERS = (
    "brak",
    "line",
    "corner",
    "jump",
    "drop",
    "root",
    "traction",
    "clipless",
    "pedal",
    "suspension",
    "fork",
    "shock",
    "chute",
    "descent",
    "berm",
    "body position",
    "arm pump",
    "technical",
    "skill",
)
MISSING_REVIEW_VALUES = {"", "unknown", "not_logged", "missing", "not_recorded", "n_a", "na", "null"}


def _planned_session_path(target: date) -> str:
    return f"input/planned_session_{target.isoformat()}.json"


def _load_planned_session_plan(root: str | Path | None, target: date) -> dict | None:
    path = input_dir(root) / f"planned_session_{target.isoformat()}.json"
    payload = read_json(path, {})
    if not isinstance(payload, dict):
        return None
    session = payload.get("session")
    if not isinstance(session, dict):
        return None
    payload_date = parse_date(payload.get("date"))
    if payload_date and payload_date != target:
        return None
    plan = dict(payload)
    plan["date"] = target.isoformat()
    plan.setdefault("coaching_status", "coach_authored_planned_session")
    plan["plan_source"] = {
        "type": "input_planned_session",
        "path": _planned_session_path(target),
    }
    return plan


def _round(value: float | None, digits: int = 1) -> float | None:
    return round(value, digits) if value is not None else None


def _number(value: Any, default: float = 0.0) -> float:
    parsed = as_number(value)
    return parsed if parsed is not None else default


def _model_report(root: str | Path | None, target: date) -> dict:
    report = read_json(snapshots_dir(root) / "training_response_model_report.json", {})
    if not isinstance(report, dict) or not report.get("tree"):
        report = build_training_predictor(root, target)
    return report


def _model_confidence(report: dict) -> dict:
    validation = report.get("validation") or {}
    utility = validation.get("utility")
    lift = validation.get("accuracy_lift_vs_baseline")
    if utility == "useful":
        status = "supporting"
        message = "Validation beats the majority baseline enough to support coaching judgment."
    elif validation.get("status") == "insufficient_samples":
        status = "insufficient"
        message = "Not enough personal samples to treat this as a stable model."
    else:
        status = "experimental"
        message = "Validation does not beat a simple baseline yet; use this for expectation tracking, not automatic prescription."
    return {
        "status": status,
        "validation_utility": utility,
        "accuracy_lift_vs_baseline": lift,
        "message": message,
    }


def _median(values: list[float]) -> float | None:
    return median(values) if values else None


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = round((len(ordered) - 1) * q)
    return ordered[int(index)]


def _session_family(session_type: str) -> str:
    if session_type in MTB_SESSION_TYPES:
        return "outdoor_mtb_or_skill"
    if session_type in INDOOR_BIKE_SESSION_TYPES or "bike" in session_type or "cycling" in session_type:
        return "bike_indoor_or_quality"
    if "gym" in session_type:
        return "gym"
    if session_type == "scheduled_rest":
        return "rest"
    return "other"


def _execution_profile_rows(root: str | Path | None, expected: dict) -> list[dict]:
    artifact = read_json(snapshots_dir(root) / "predictive_backtest_10_dates.json", {})
    rows = artifact.get("rows") if isinstance(artifact, dict) else []
    if not isinstance(rows, list):
        return []
    expected_family = _session_family(str(expected.get("type") or ""))
    matches = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_expected = ((row.get("prediction") or {}).get("expected_session") or {})
        if _session_family(str(row_expected.get("type") or "")) == expected_family:
            matches.append(row)
    return matches


def _risk_adjusted_expected_session(expected: dict, profile: dict) -> dict | None:
    if not profile.get("likely_harder_than_plan"):
        return None
    expected_load = _number(expected.get("expected_training_load"))
    expected_duration = _number(expected.get("duration_min"))
    expected_high_intensity = _number(expected.get("expected_high_intensity_min"))
    stress_load = max(expected_load, _number(profile.get("median_actual_training_load"), expected_load))
    stress_duration = max(expected_duration, _number(profile.get("median_actual_duration_min"), expected_duration))
    stress_high_intensity = max(
        expected_high_intensity,
        _number(profile.get("median_actual_high_intensity_min"), expected_high_intensity),
    )
    categories = dict(expected.get("categories") or {})
    category_counts = profile.get("actual_category_counts") or {}
    if str(expected.get("type") or "") in MTB_SESSION_TYPES:
        categories = {"mtb": 1}
    elif category_counts:
        primary = max(category_counts.items(), key=lambda item: item[1])[0]
        if primary in {"bike_indoor", "bike_outdoor"}:
            categories = {"bike_indoor": 1}
        elif primary == "mtb":
            categories = {"mtb": 1}
    mtb_sessions = 1 if categories.get("mtb") else expected.get("mtb_sessions") or 0
    return {
        **expected,
        "title": f"{expected.get('title')} - execution drift stress test",
        "duration_min": int(round(stress_duration)),
        "expected_training_load": _round(stress_load),
        "expected_training_load_range": [
            expected.get("expected_training_load"),
            _round(max(stress_load, _number(profile.get("p75_actual_training_load"), stress_load))),
        ],
        "expected_high_intensity_min": _round(stress_high_intensity),
        "mtb_sessions": mtb_sessions,
        "categories": categories,
        "assumptions": [
            "Stress-test load is based on Clayton's historical tendency for outdoor/bike prescriptions to become longer or harder than the written plan.",
            "Use this only as an execution-risk branch; the main prescription remains the session to actually follow.",
        ],
    }


def _fallback_execution_profile(expected: dict) -> dict:
    session_type = str(expected.get("type") or "")
    expected_load = _number(expected.get("expected_training_load"))
    expected_duration = _number(expected.get("duration_min"))
    if session_type in MTB_SESSION_TYPES:
        duration = max(expected_duration, 150.0 if expected_duration <= 60 else expected_duration * 1.75)
        load = max(expected_load, expected_load * (4.5 if expected_load < 50 else 2.0))
        high_intensity = max(_number(expected.get("expected_high_intensity_min")), duration * 0.08)
        profile = {
            "status": "fallback",
            "level": "elevated",
            "samples": 0,
            "family": _session_family(session_type),
            "harder_than_plan_rate": None,
            "median_actual_to_expected_load_ratio": None,
            "median_actual_training_load": _round(load),
            "p75_actual_training_load": _round(load),
            "median_actual_duration_min": _round(duration),
            "median_actual_high_intensity_min": _round(high_intensity),
            "actual_category_counts": {"mtb": 1},
            "likely_harder_than_plan": True,
            "coaching_action": (
                "Outdoor MTB and skill prescriptions need a hard duration/load cap; otherwise they can turn into a "
                "durability day and invalidate the planned recovery prediction."
            ),
        }
        profile["stress_test_expected_session"] = _risk_adjusted_expected_session(expected, profile)
        return profile
    return {
        "status": "unremarkable",
        "level": "low",
        "samples": 0,
        "family": _session_family(session_type),
        "harder_than_plan_rate": None,
        "likely_harder_than_plan": False,
        "coaching_action": "No historical execution-risk adjustment applied.",
        "stress_test_expected_session": None,
    }


def _execution_risk_profile(root: str | Path | None, expected: dict) -> dict:
    if not expected.get("sessions"):
        return {
            "status": "not_applicable",
            "level": "low",
            "samples": 0,
            "family": _session_family(str(expected.get("type") or "")),
            "likely_harder_than_plan": False,
            "coaching_action": "No session is prescribed.",
            "stress_test_expected_session": None,
        }
    rows = _execution_profile_rows(root, expected)
    if len(rows) < MIN_EXECUTION_PROFILE_SAMPLES:
        return _fallback_execution_profile(expected)

    actual_loads = []
    actual_durations = []
    actual_high_intensity = []
    load_ratios = []
    category_counts: Counter[str] = Counter()
    harder = 0
    matched = 0
    for row in rows:
        actual = row.get("actual_session") or {}
        comparison = row.get("comparison") or {}
        row_expected = ((row.get("prediction") or {}).get("expected_session") or {})
        actual_load = as_number(actual.get("training_load"))
        actual_duration = as_number(actual.get("duration_min"))
        high_intensity = as_number(actual.get("high_intensity_min"))
        expected_load = as_number(row_expected.get("expected_training_load"))
        if actual_load is not None:
            actual_loads.append(actual_load)
            if expected_load:
                load_ratios.append(actual_load / expected_load)
        if actual_duration is not None:
            actual_durations.append(actual_duration)
        if high_intensity is not None:
            actual_high_intensity.append(high_intensity)
        for category, count in (actual.get("categories") or {}).items():
            category_counts[str(category)] += int(count or 0)
        if comparison.get("adherence_status") == "harder_than_predicted":
            harder += 1
        if comparison.get("adherence_status") == "matched_expected_load":
            matched += 1

    harder_rate = harder / len(rows)
    median_ratio = _median(load_ratios)
    likely_harder = harder_rate >= 0.5 or (median_ratio is not None and median_ratio >= 1.5)
    level = "high" if harder_rate >= 0.7 or (median_ratio is not None and median_ratio >= 3.0) else "elevated"
    if not likely_harder:
        level = "low"
    profile = {
        "status": "calibrated",
        "level": level,
        "samples": len(rows),
        "family": _session_family(str(expected.get("type") or "")),
        "harder_than_plan_rate": _round(harder_rate, 2),
        "matched_plan_rate": _round(matched / len(rows), 2),
        "median_actual_to_expected_load_ratio": _round(median_ratio, 2) if median_ratio is not None else None,
        "median_actual_training_load": _round(_median(actual_loads)),
        "p75_actual_training_load": _round(_quantile(actual_loads, 0.75)),
        "median_actual_duration_min": _round(_median(actual_durations)),
        "median_actual_high_intensity_min": _round(_median(actual_high_intensity)),
        "actual_category_counts": dict(sorted(category_counts.items())),
        "likely_harder_than_plan": likely_harder,
        "coaching_action": (
            "Give this session an explicit cap and route purpose; if Clayton intends a long MTB durability day, "
            "prescribe it as that instead of calling it easy continuity."
        )
        if likely_harder
        else "Historical execution mostly matches this prescription family.",
    }
    profile["stress_test_expected_session"] = _risk_adjusted_expected_session(expected, profile)
    return profile


def _leaf_response_payload(leaf: dict) -> dict:
    expected_score = leaf.get("avg_next_day_response_score")
    return {
        "score": expected_score,
        "readiness_level": _readiness_level(expected_score) if expected_score is not None else None,
        "response_class": _response_class(expected_score) if expected_score is not None else None,
        "prob_next_day_ready": leaf.get("prob_next_day_ready"),
        "leaf_samples": leaf.get("samples"),
        "leaf_date_span": leaf.get("date_span"),
        "leaf_readiness_level_distribution": leaf.get("readiness_level_distribution"),
    }


def _coaching_adjusted_response(raw_response: dict, features: dict, expected: dict) -> dict:
    raw_score = raw_response.get("score")
    if raw_score is None:
        return {
            "status": "unavailable",
            "score": None,
            "adjustment": 0.0,
            "drivers": [],
            "message": "No raw model score is available to adjust.",
        }
    training_load = _number(features.get("today_training_load"))
    duration = _number(features.get("today_duration_min"))
    high_intensity = _number(features.get("today_high_intensity_min"))
    body_battery = _number(features.get("today_body_battery_anchor"), 70.0)
    sleep_hours = _number(features.get("today_sleep_hours"), 7.0)
    avg_stress = _number(features.get("today_avg_stress"), 25.0)
    acwr = _number(features.get("acute_to_chronic_load_ratio"))
    mtb = _number(features.get("mtb_sessions_today")) > 0
    session_type = str(expected.get("type") or "")
    adjustment = 0.0
    drivers = []

    def penalize(points: float, reason: str) -> None:
        nonlocal adjustment
        adjustment -= points
        drivers.append({"points": -points, "reason": reason})

    if training_load >= 200:
        penalize(12, "planned_or_stress_test_load_at_or_above_200")
    elif training_load >= 120:
        penalize(7, "planned_or_stress_test_load_at_or_above_120")
    elif training_load >= 90 and session_type in MTB_SESSION_TYPES:
        penalize(3, "mtb_load_above_easy_continuity_band")

    if high_intensity >= 30:
        penalize(8, "high_intensity_minutes_at_or_above_30")
    elif high_intensity >= 15:
        penalize(4, "high_intensity_minutes_at_or_above_15")

    if mtb and duration >= 150:
        penalize(7, "mtb_duration_at_or_above_150_minutes")
    elif mtb and duration >= 120:
        penalize(4, "mtb_duration_at_or_above_120_minutes")

    if body_battery < 55 and training_load >= 90:
        penalize(6, "modest_body_battery_with_meaningful_load")
    if sleep_hours < 6 and training_load >= 120:
        penalize(4, "sub_6h_sleep_with_meaningful_load")
    if avg_stress > 35 and training_load >= 120:
        penalize(4, "high_day_stress_with_meaningful_load")
    if acwr >= 1.4 and training_load >= 90:
        penalize(4, "acute_chronic_load_already_high")

    adjusted_score = max(0.0, min(100.0, raw_score + adjustment))
    return {
        "status": "adjusted" if drivers else "same_as_raw",
        "raw_score": raw_score,
        "score": _round(adjusted_score),
        "adjustment": _round(adjustment),
        "readiness_level": _readiness_level(adjusted_score),
        "response_class": _response_class(adjusted_score),
        "drivers": drivers,
        "message": (
            "Transparent coaching overlay on top of the tree model; used because session load and MTB duration can be underweighted by the current small-data tree."
            if drivers
            else "No additional strain adjustment applied."
        ),
    }


def _response_status_from_delta(delta: float | None) -> str:
    if delta is None:
        return "no_expected_response"
    if delta <= -10:
        return "worse_than_expected"
    if delta >= 10:
        return "better_than_expected"
    return "within_expected_band"


def _session_expectation(plan: dict) -> dict:
    session = plan.get("session") or {}
    session_type = str(session.get("type") or "unknown")
    intensity = str(session.get("intensity") or "easy")
    duration = int(session.get("duration_min") or 0)
    modality = str(session.get("modality") or "").lower()
    if session_type == "scheduled_rest":
        duration = 0
    session_count = 1 if duration > 0 else 0
    base_load_per_hour = LOAD_PER_HOUR.get(intensity, LOAD_PER_HOUR["easy"])
    is_mtb = modality == "mtb" or session_type in MTB_SESSION_TYPES
    is_bike = (
        is_mtb
        or modality in {"bike", "bike_indoor", "bike_outdoor", "cycling"}
        or session_type in INDOOR_BIKE_SESSION_TYPES
        or "bike" in session_type
        or "cycling" in session_type
    )
    if is_mtb:
        base_load_per_hour += 10
    elif session_type == "bike_quality":
        base_load_per_hour += 15
    training_load = (duration / 60.0) * base_load_per_hour if duration > 0 else 0.0
    if intensity == "hard":
        high_intensity_min = duration * 0.25
    elif intensity == "moderate_hard":
        high_intensity_min = duration * 0.16
    elif intensity == "moderate":
        high_intensity_min = duration * 0.08
    else:
        high_intensity_min = 0.0
    mtb_sessions = 1 if session_count and is_mtb else 0
    gym_sessions = 1 if session_count and "gym" in session_type else 0
    categories = {}
    if session_count:
        if mtb_sessions:
            categories["mtb"] = 1
        elif gym_sessions:
            categories["gym"] = 1
        elif is_bike:
            categories["bike_outdoor" if modality in {"bike_outdoor", "outdoor_bike"} else "bike_indoor"] = 1
        else:
            categories["other"] = 1
    load_low = training_load * 0.7
    load_high = training_load * 1.35
    expected = {
        "title": session.get("title"),
        "type": session_type,
        "intensity": intensity,
        "modality": modality or ("mtb" if is_mtb else "bike" if is_bike else "other"),
        "duration_min": duration,
        "sessions": session_count,
        "expected_training_load": _round(training_load),
        "expected_training_load_range": [_round(load_low), _round(load_high)],
        "expected_high_intensity_min": _round(high_intensity_min),
        "expected_rpe_score_range": RPE_RANGES.get(intensity, RPE_RANGES["easy"]),
        "expected_feel": "normal",
        "mtb_sessions": mtb_sessions,
        "gym_sessions": gym_sessions,
        "categories": categories,
        "assumptions": [
            "Expected load is a deterministic estimate from planned duration, intensity, and modality.",
            "Trail heat, stops, technical terrain, wrist-HR error, and group riding can move actual Garmin load away from this estimate.",
        ],
    }
    for field in SESSION_CONTRACT_FIELDS:
        if field in session:
            expected[field] = session[field]
    if session.get("schema_version"):
        expected["schema_version"] = session.get("schema_version")
    if session.get("contract_fields"):
        expected["contract_fields"] = session.get("contract_fields")
    return expected


def _simulate_activity_day(activity_by_day: dict[str, dict], target: date, expected: dict) -> dict[str, dict]:
    simulated = {day: dict(row) for day, row in activity_by_day.items()}
    row = _activity_empty()
    row.update(
        {
            "sessions": expected.get("sessions") or 0,
            "duration_min": float(expected.get("duration_min") or 0),
            "training_load": float(expected.get("expected_training_load") or 0),
            "distance_km": 0.0,
            "high_intensity_min": float(expected.get("expected_high_intensity_min") or 0),
            "mtb_sessions": expected.get("mtb_sessions") or 0,
            "gym_sessions": expected.get("gym_sessions") or 0,
            "categories": dict(expected.get("categories") or {}),
        }
    )
    simulated[target.isoformat()] = row
    return simulated


def _wellness_basis(root: str | Path | None, target: date) -> tuple[date | None, list[dict], dict[str, dict]]:
    wellness_rows = [
        row
        for row in build_wellness_daily(root)
        if parse_date(row.get("date")) and parse_date(row.get("date")) <= target
    ]
    wellness_by_date = {row["date"]: row for row in wellness_rows if row.get("date")}
    basis_dates = [
        parse_date(row.get("date"))
        for row in wellness_rows
        if row.get("date") and parse_date(row.get("date")) and parse_date(row.get("date")) <= target
    ]
    basis_dates = [item for item in basis_dates if item is not None]
    return (max(basis_dates) if basis_dates else None), wellness_rows, wellness_by_date


def _prediction_from_plan(
    root: str | Path | None,
    target: date,
    plan: dict,
    model: dict,
) -> dict:
    expected = _session_expectation(plan)
    basis_date, wellness_rows, wellness_by_date = _wellness_basis(root, target)
    warnings = []
    if basis_date is None:
        return {
            "status": "unavailable",
            "expected_session": expected,
            "warnings": ["No wellness row is available for a pre-session prediction."],
        }
    if basis_date != target:
        warnings.append(
            f"Prediction uses wellness from {basis_date.isoformat()} as the state basis for action on {target.isoformat()}."
        )
    activity_by_day = _activity_by_date(build_activity_summary_index(root, target))
    feature_wellness_by_date = dict(wellness_by_date)
    feature_wellness_rows = list(wellness_rows)
    if target.isoformat() not in feature_wellness_by_date:
        basis_row = wellness_by_date.get(basis_date.isoformat())
        if basis_row is None:
            return {
                "status": "unavailable",
                "basis_date": basis_date.isoformat(),
                "expected_session": expected,
                "warnings": ["The latest wellness basis row could not be loaded for the planned action date."],
            }
        surrogate = {**basis_row, "date": target.isoformat(), "prediction_state_basis_date": basis_date.isoformat()}
        feature_wellness_by_date[target.isoformat()] = surrogate
        feature_wellness_rows.append(surrogate)
    simulated = _simulate_activity_day(activity_by_day, target, expected)
    features = _features_for_day(target, feature_wellness_by_date, feature_wellness_rows, simulated)
    if features is None:
        return {
            "status": "unavailable",
            "basis_date": basis_date.isoformat(),
            "expected_session": expected,
            "warnings": ["Could not build digital-twin feature vector."],
        }
    tree = model.get("tree") or {}
    prediction = _predict(tree, features)
    leaf = prediction["leaf"]
    raw_response = _leaf_response_payload(leaf)
    adjusted_response = _coaching_adjusted_response(raw_response, features, expected)
    execution_risk = _execution_risk_profile(root, expected)
    stress_expected = execution_risk.get("stress_test_expected_session")
    if stress_expected:
        stress_simulated = _simulate_activity_day(activity_by_day, target, stress_expected)
        stress_features = _features_for_day(target, feature_wellness_by_date, feature_wellness_rows, stress_simulated)
        if stress_features is not None:
            stress_prediction = _predict(tree, stress_features)
            stress_raw_response = _leaf_response_payload(stress_prediction["leaf"])
            execution_risk["stress_test_next_day_response"] = stress_raw_response
            execution_risk["stress_test_coaching_adjusted_next_day_response"] = (
                _coaching_adjusted_response(stress_raw_response, stress_features, stress_expected)
            )
            execution_risk["stress_test_prediction_path"] = stress_prediction.get("path")
        if execution_risk.get("level") in {"high", "elevated"}:
            warnings.append(
                "Execution risk: Clayton's similar prescriptions have often become longer or harder than the written plan."
            )
    return {
        "status": "ok" if not warnings else "caution",
        "basis_date": basis_date.isoformat(),
        "action_date": target.isoformat(),
        "predicts_date": (target + timedelta(days=1)).isoformat(),
        "expected_session": expected,
        "simulated_features": features,
        "prediction_path": prediction.get("path"),
        "expected_next_day_response": raw_response,
        "coaching_adjusted_next_day_response": adjusted_response,
        "execution_risk": execution_risk,
        "warnings": warnings,
    }


def _activity_rows_for_date(root: str | Path | None, target: date) -> list[dict]:
    rows = []
    for row in build_activity_summary_index(root, target):
        try:
            row_date = parse_date(row.get("date"))
        except (TypeError, ValueError):
            row_date = None
        if row_date == target and row.get("counts_for_training_load"):
            rows.append(row)
    return rows


def _actual_activity_summary(root: str | Path | None, target: date) -> dict:
    rows = _activity_rows_for_date(root, target)
    categories: dict[str, int] = defaultdict(int)
    duration = 0.0
    training_load = 0.0
    high_intensity = 0.0
    mtb_sessions = 0
    gym_sessions = 0
    for row in rows:
        category = row.get("category") or "other"
        categories[category] += 1
        duration += _number(row.get("duration_min"))
        training_load += _number(row.get("training_load"))
        zones = row.get("hr_zone_min") or {}
        high_intensity += _number(zones.get("z4")) + _number(zones.get("z5"))
        mtb_sessions += int(category == "mtb")
        gym_sessions += int(category == "gym")
    return {
        "date": target.isoformat(),
        "sessions": len(rows),
        "duration_min": _round(duration),
        "training_load": _round(training_load),
        "high_intensity_min": _round(high_intensity),
        "mtb_sessions": mtb_sessions,
        "gym_sessions": gym_sessions,
        "categories": dict(sorted(categories.items())),
        "activities": rows,
    }


def _self_evaluation_for_date(root: str | Path | None, target: date) -> dict:
    index = read_json(snapshots_dir(root) / "activity_self_evaluation_index.json", {})
    rows = []
    for row in (index.get("activities") if isinstance(index, dict) else []) or []:
        if not isinstance(row, dict) or not row.get("has_self_evaluation"):
            continue
        try:
            row_date = parse_date(row.get("date"))
        except (TypeError, ValueError):
            row_date = None
        if row_date == target:
            rows.append(row)
    rpes = [_number(row.get("rpe_score"), default=0.0) for row in rows if row.get("rpe_score") is not None]
    feels = [_number(row.get("feel_score"), default=0.0) for row in rows if row.get("feel_score") is not None]
    return {
        "count": len(rows),
        "max_rpe_score": max(rpes) if rpes else None,
        "avg_rpe_score": _round(sum(rpes) / len(rpes), 1) if rpes else None,
        "avg_feel_score": _round(sum(feels) / len(feels), 1) if feels else None,
        "rows": rows,
    }


def _actual_next_day_response(root: str | Path | None, target: date) -> dict:
    next_day = target + timedelta(days=1)
    wellness_rows = [
        row
        for row in build_wellness_daily(root)
        if parse_date(row.get("date")) and parse_date(row.get("date")) <= next_day
    ]
    wellness_by_date = {row["date"]: row for row in wellness_rows if row.get("date")}
    next_row = wellness_by_date.get(next_day.isoformat())
    if next_row is None:
        return {
            "status": "pending",
            "target_date": next_day.isoformat(),
            "message": "Next-day wellness is not available yet.",
        }
    payload = _target_score(next_row, wellness_rows, next_day)
    if not payload.get("usable"):
        return {
            "status": "unusable",
            "target_date": next_day.isoformat(),
            "message": "Next-day wellness exists but does not have enough signals for response scoring.",
            "raw": payload,
        }
    return {
        "status": "available",
        "target_date": next_day.isoformat(),
        "score": payload.get("next_day_response_score"),
        "readiness_level": payload.get("next_day_readiness_level"),
        "response_class": payload.get("next_day_response_class"),
        "signals": payload.get("signals"),
        "missing_signals": payload.get("missing_signals"),
    }


def _normalized_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")


def _review_value_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return _normalized_key(value) not in MISSING_REVIEW_VALUES
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return True


def _activity_ref_for_feedback_id(activity_id: Any) -> str | None:
    if not _review_value_present(activity_id):
        return None
    return hashlib.sha256(str(activity_id).encode("utf-8")).hexdigest()[:12]


def _text_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for value_item in value for item in _text_values(value_item)]
    if isinstance(value, dict):
        return [item for value_item in value.values() for item in _text_values(value_item)]
    return []


def _technical_note_sources(blocks: list[dict]) -> list[str]:
    sources = []
    direct_keys = {
        "technical_quality_notes",
        "technical_notes",
        "skill_quality",
        "technical_quality",
    }
    for block in blocks:
        payload = block.get("payload") or {}
        source = block.get("source") or "feedback"
        for key, value in payload.items():
            normalized = _normalized_key(key)
            if normalized not in TECHNICAL_NOTE_KEYS:
                continue
            text = " ".join(_text_values(value)).lower()
            if normalized in direct_keys and _review_value_present(value):
                sources.append(f"{source}:{normalized}")
            elif any(marker in text for marker in TECHNICAL_NOTE_MARKERS):
                sources.append(f"{source}:{normalized}")
    return sorted(set(sources))


def _feedback_evidence_for_actual(
    root: str | Path | None,
    target: date,
    actual: dict,
) -> tuple[dict, list[dict]]:
    path = input_dir(root) / f"feedback_{target.isoformat()}.json"
    payload = read_json(path, {})
    if not isinstance(payload, dict):
        payload = {}
    actual_refs = {
        str(row.get("activity_ref"))
        for row in actual.get("activities") or []
        if isinstance(row, dict) and row.get("activity_ref")
    }
    blocks: list[dict] = []
    matched_entries = 0
    unscoped_entries = 0
    ignored_entries = 0
    root_included = False

    def add_block(container: dict, source: str) -> None:
        blocks.append({"source": source, "payload": container})
        for key in CONTRACT_REVIEW_KEYS:
            nested = container.get(key)
            if isinstance(nested, dict):
                blocks.append({"source": f"{source}.{key}", "payload": nested})

    root_activity_ref = _activity_ref_for_feedback_id(payload.get("activity_id"))
    if not root_activity_ref or not actual_refs or root_activity_ref in actual_refs:
        add_block(payload, "feedback")
        root_included = True

    entries = payload.get("entries") or []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        entry_activity_ref = _activity_ref_for_feedback_id(entry.get("activity_id"))
        if entry_activity_ref:
            if actual_refs and entry_activity_ref not in actual_refs:
                ignored_entries += 1
                continue
            if not actual_refs:
                ignored_entries += 1
                continue
            matched_entries += 1
        else:
            unscoped_entries += 1
        add_block(entry, f"feedback.entries[{index}]")

    scope = "none"
    if matched_entries:
        scope = "activity_matched"
    elif root_included or unscoped_entries:
        scope = "date_scoped"
    evidence = {
        "available": bool(payload),
        "file": f"input/feedback_{target.isoformat()}.json" if payload else None,
        "scope": scope,
        "root_included": root_included,
        "matched_activity_entries": matched_entries,
        "unscoped_entries": unscoped_entries,
        "ignored_other_activity_entries": ignored_entries,
        "technical_note_sources": _technical_note_sources(blocks),
    }
    return evidence, blocks


def _manual_review_value(blocks: list[dict], field: str) -> tuple[Any, str | None]:
    aliases = REVIEW_FIELD_ALIASES.get(field, (field,))
    normalized_aliases = {_normalized_key(item) for item in aliases}
    for block in reversed(blocks):
        payload = block.get("payload") or {}
        for key, value in payload.items():
            if _normalized_key(key) in normalized_aliases and _review_value_present(value):
                return value, str(block.get("source") or "feedback")
    return None, None


def _expected_contract_status(expected: dict) -> dict:
    declared = expected.get("contract_fields")
    declared_fields = set(declared) if isinstance(declared, list) else set()
    missing = [
        field
        for field in SESSION_CONTRACT_FIELDS
        if field not in declared_fields or not _review_value_present(expected.get(field))
    ]
    schema_version = _number(expected.get("schema_version"))
    valid = schema_version >= 3 and not missing
    return {
        "schema_version": int(schema_version) if schema_version else None,
        "declared_fields": sorted(declared_fields),
        "missing_fields": missing,
        "status": "complete" if valid else "missing_or_incomplete",
        "valid": valid,
    }


def _is_technical_session(expected: dict) -> bool:
    categories = expected.get("categories") or {}
    session_type = str(expected.get("type") or "").lower()
    modality = str(expected.get("modality") or "").lower()
    return bool(
        categories.get("mtb")
        or _number(expected.get("mtb_sessions")) > 0
        or modality == "mtb"
        or "mtb" in session_type
        or "enduro" in session_type
    )


def _review_field_is_applicable(field: str, expected: dict) -> bool:
    normalized = _normalized_key(field)
    technical = _is_technical_session(expected)
    duration = _number(expected.get("duration_min"))
    if normalized in TECHNICAL_REVIEW_FIELDS:
        return technical
    if normalized in FUELING_REVIEW_FIELDS:
        return technical or duration >= 60
    return True


def _automatic_review_source(field: str, actual: dict, self_eval: dict, response: dict) -> str | None:
    normalized = _normalized_key(field)
    if normalized == "actual_duration_min":
        return "garmin_activity" if actual.get("sessions", 0) and actual.get("duration_min") is not None else None
    if normalized == "actual_training_load":
        return "garmin_activity" if actual.get("sessions", 0) and actual.get("training_load") is not None else None
    if normalized == "actual_rpe":
        return "garmin_self_evaluation" if self_eval.get("avg_rpe_score") is not None else None
    if normalized == "workout_feel":
        return "garmin_self_evaluation" if self_eval.get("avg_feel_score") is not None else None
    if normalized == "next_morning_response":
        return "garmin_next_day_response" if response.get("status") == "available" else None
    return None


def _review_field_rows(
    expected: dict,
    actual: dict,
    self_eval: dict,
    response: dict,
    feedback: dict,
    blocks: list[dict],
) -> list[dict]:
    fields = []
    seen = set()
    for value in expected.get("post_session_review_fields") or []:
        if not isinstance(value, str) or not value.strip():
            continue
        normalized = _normalized_key(value)
        if normalized in seen:
            continue
        seen.add(normalized)
        fields.append(normalized)

    rows = []
    technical_sources = feedback.get("technical_note_sources") or []
    for field in fields:
        applicable = _review_field_is_applicable(field, expected)
        automatic_source = _automatic_review_source(field, actual, self_eval, response)
        manual_value, manual_source = _manual_review_value(blocks, field)
        source = automatic_source or (f"{manual_source}" if manual_source else None)
        if automatic_source:
            status = "completed"
        elif manual_source:
            status = "completed"
        elif field == "technical_quality_notes" and applicable and technical_sources:
            status = "completed"
            source = "feedback_technical_note"
        elif not applicable:
            status = "not_applicable"
        else:
            status = "missing"
        rows.append(
            {
                "field": field,
                "required_for_calibration": applicable,
                "status": status,
                "source": source,
                "manual_value_logged": manual_value is not None,
            }
        )
    return rows


def _stop_rule_review(expected: dict, blocks: list[dict]) -> dict:
    stop_rules = expected.get("stop_rules") or []
    if not stop_rules or not expected.get("sessions"):
        return {
            "required": False,
            "status": "not_applicable",
            "source": None,
            "calibration_effect": "not_applicable",
        }
    value, source = _manual_review_value(blocks, "stop_rule_outcome")
    if value is None:
        triggered, triggered_source = _manual_review_value(blocks, "stop_rule_triggered")
        triggered_status = _normalized_key(triggered) if triggered is not None else ""
        if isinstance(triggered, bool) or triggered_status in {"yes", "no", "true", "false", "0", "1"}:
            triggered_now = triggered is True or triggered_status in {"yes", "true", "1"}
            if not triggered_now:
                return {
                    "required": True,
                    "status": "not_triggered",
                    "source": triggered_source,
                    "calibration_effect": "compatible",
                }
            action, action_source = _manual_review_value(blocks, "stop_rule_action")
            value = action
            source = action_source or triggered_source
        if value is None:
            return {
                "required": True,
                "status": "not_logged",
                "source": None,
                "calibration_effect": "missing",
            }
    normalized = _normalized_key(value)
    if normalized in {"not_triggered", "no", "false", "0", "none", "no_stop_rule_triggered"}:
        status = "not_triggered"
        effect = "compatible"
    elif normalized in {"triggered_and_stopped", "stopped", "stopped_when_triggered", "triggered_stop"}:
        status = "triggered_and_stopped"
        effect = "dose_changed"
    elif normalized in {"triggered_and_downshifted", "downshifted", "modified_after_trigger"}:
        status = "triggered_and_downshifted"
        effect = "dose_changed"
    elif normalized in {"triggered_but_continued", "continued", "ignored", "overrode_stop_rule"}:
        status = "triggered_but_continued"
        effect = "unsafe"
    else:
        status = "unknown"
        effect = "unclassified"
    return {
        "required": True,
        "status": status,
        "source": source,
        "calibration_effect": effect,
    }


def _technical_quality_review(expected: dict, blocks: list[dict]) -> dict:
    if not _is_technical_session(expected):
        return {
            "required": False,
            "status": "not_applicable",
            "source": None,
            "calibration_effect": "not_applicable",
        }
    value, source = _manual_review_value(blocks, "technical_quality")
    source_field = "technical_quality"
    if value is None:
        value, source = _manual_review_value(blocks, "late_session_skill_fade")
        source_field = "late_session_skill_fade"
    if value is None:
        return {
            "required": True,
            "status": "not_logged",
            "source": None,
            "calibration_effect": "missing",
        }
    normalized = _normalized_key(value)
    clean_values = {"clean", "good", "stable", "controlled", "acceptable", "pass", "passed"}
    if source_field == "late_session_skill_fade":
        clean_values.update({"none", "no", "false", "0", "not_present", "no_fade", "no_skill_fade"})
    degraded_values = {
        "degraded",
        "faded",
        "poor",
        "reactive",
        "sloppy",
        "aborted",
        "unsafe",
        "yes",
        "true",
        "1",
        "present",
    }
    if normalized in clean_values:
        status = "clean"
        effect = "compatible"
    elif normalized in degraded_values:
        status = "degraded"
        effect = "quality_limited"
    else:
        status = "documented_unclassified"
        effect = "needs_interpretation"
    return {
        "required": True,
        "status": status,
        "source": source,
        "calibration_effect": effect,
    }


def _action_alignment(expected: dict, actual: dict) -> dict:
    expected_sessions = int(_number(expected.get("sessions")))
    actual_sessions = int(_number(actual.get("sessions")))
    expected_categories = sorted(
        str(category)
        for category, count in (expected.get("categories") or {}).items()
        if _number(count) > 0
    )
    actual_categories = sorted(
        str(category)
        for category, count in (actual.get("categories") or {}).items()
        if _number(count) > 0
    )
    if expected_sessions == 0:
        modality_status = "not_applicable" if actual_sessions == 0 else "mismatched"
        session_count_status = "not_applicable" if actual_sessions == 0 else "mismatched"
    elif actual_sessions == 0:
        modality_status = "missing"
        session_count_status = "missing"
    else:
        modality_status = "matched" if expected_categories == actual_categories else "mismatched"
        session_count_status = "matched" if expected_sessions == actual_sessions else "mismatched"

    expected_duration = _number(expected.get("duration_min"))
    actual_duration = _number(actual.get("duration_min"))
    duration_ratio = actual_duration / expected_duration if expected_duration else None
    if expected_duration <= 0:
        duration_status = "not_applicable" if actual_sessions == 0 else "mismatched"
    elif actual_sessions == 0:
        duration_status = "missing"
    elif duration_ratio is None:
        duration_status = "unknown"
    elif 0.7 <= duration_ratio <= 1.35:
        duration_status = "matched"
    else:
        duration_status = "drifted"
    component_statuses = (modality_status, session_count_status, duration_status)
    if expected_sessions == 0:
        overall = "not_applicable" if actual_sessions == 0 else "mismatched"
    elif all(status == "matched" for status in component_statuses):
        overall = "matched"
    elif "missing" in component_statuses:
        overall = "missing"
    else:
        overall = "mismatched"
    return {
        "status": overall,
        "expected_categories": expected_categories,
        "actual_categories": actual_categories,
        "modality_status": modality_status,
        "expected_sessions": expected_sessions,
        "actual_sessions": actual_sessions,
        "session_count_status": session_count_status,
        "expected_duration_min": _round(expected_duration),
        "actual_duration_min": _round(actual_duration),
        "duration_ratio": _round(duration_ratio, 2),
        "duration_status": duration_status,
    }


def _contract_quality_review(
    root: str | Path | None,
    target: date,
    expected: dict,
    actual: dict,
    self_eval: dict,
    response: dict,
) -> dict:
    contract = _expected_contract_status(expected)
    feedback, blocks = _feedback_evidence_for_actual(root, target, actual)
    field_rows = _review_field_rows(expected, actual, self_eval, response, feedback, blocks)
    required_rows = [row for row in field_rows if row.get("required_for_calibration")]
    completed = [row["field"] for row in required_rows if row.get("status") == "completed"]
    missing = [row["field"] for row in required_rows if row.get("status") == "missing"]
    not_applicable = [row["field"] for row in field_rows if row.get("status") == "not_applicable"]
    completion_ratio = len(completed) / len(required_rows) if required_rows else 1.0
    action = _action_alignment(expected, actual)
    stop_rule = _stop_rule_review(expected, blocks)
    technical_quality = _technical_quality_review(expected, blocks)
    reasons = []
    if not expected.get("sessions"):
        status = "not_applicable"
        reasons.append("No trainable session was prescribed.")
    elif not contract.get("valid"):
        status = "contract_missing"
        reasons.append("The stored prediction does not contain a complete schema v3 session contract.")
    elif action.get("status") != "matched":
        status = "action_mismatch"
        reasons.append("Actual modality, session count, or duration does not match the stored action closely enough.")
    elif stop_rule.get("status") == "triggered_but_continued":
        status = "unsafe_stop_rule_continued"
        reasons.append("A stop rule was triggered but the session continued, so the nominal action is not trustworthy.")
    elif stop_rule.get("status") in {"triggered_and_stopped", "triggered_and_downshifted"}:
        status = "execution_dose_stopped"
        reasons.append("A stop rule changed the delivered dose; retain this as safety evidence, not a nominal calibration row.")
    elif stop_rule.get("status") in {"not_logged", "unknown"}:
        status = "incomplete"
        reasons.append("Stop-rule outcome was not logged explicitly.")
    elif technical_quality.get("status") == "degraded":
        status = "technical_quality_degraded"
        reasons.append("Technical quality degraded, so the session did not represent the intended action cleanly.")
    elif technical_quality.get("status") in {"not_logged", "documented_unclassified"}:
        status = "incomplete"
        reasons.append("Technical quality outcome was not recorded in a calibratable form.")
    elif missing:
        status = "incomplete"
        reasons.append(f"Required review fields are missing: {', '.join(missing)}.")
    else:
        status = "complete"
        reasons.append("Action, review fields, technical outcome, and stop-rule outcome are complete enough for calibration.")
    return {
        "status": status,
        "calibration_eligible": status == "complete",
        "contract": contract,
        "action_alignment": action,
        "stop_rule_outcome": stop_rule,
        "technical_quality": technical_quality,
        "review_field_completion": {
            "required": [row["field"] for row in required_rows],
            "completed": completed,
            "missing": missing,
            "not_applicable": not_applicable,
            "completion_ratio": _round(completion_ratio, 2),
            "fields": field_rows,
        },
        "feedback": feedback,
        "reasons": reasons,
    }


def _stress_test_comparison(prediction: dict, actual_response: float | None) -> dict:
    risk = prediction.get("execution_risk") or {}
    stress_adjusted = risk.get("stress_test_coaching_adjusted_next_day_response") or {}
    stress_raw = risk.get("stress_test_next_day_response") or {}
    stress_score = stress_adjusted.get("score")
    source = "coaching_adjusted_stress_test"
    if stress_score is None:
        stress_score = stress_raw.get("score")
        source = "raw_stress_test" if stress_score is not None else None
    if actual_response is None or stress_score is None:
        return {
            "available": False,
            "expected_response_source": source,
            "score": stress_score,
            "response_delta": None,
            "response_status": "pending_or_unavailable",
        }
    delta = actual_response - stress_score
    return {
        "available": True,
        "expected_response_source": source,
        "score": stress_score,
        "response_delta": _round(delta),
        "response_status": _response_status_from_delta(delta),
    }


def _compare_prediction(
    prediction: dict,
    actual: dict,
    self_eval: dict,
    response: dict,
    contract_quality: dict | None = None,
) -> dict:
    expected = prediction.get("expected_session") or {}
    expected_range = expected.get("expected_training_load_range") or [None, None]
    expected_load = expected.get("expected_training_load")
    actual_load = actual.get("training_load")
    load_delta = actual_load - expected_load if actual_load is not None and expected_load is not None else None
    load_delta_pct = (load_delta / expected_load * 100) if load_delta is not None and expected_load else None
    lower, upper = expected_range if len(expected_range) == 2 else (None, None)
    if actual.get("sessions", 0) == 0 and expected.get("sessions", 0) > 0:
        adherence = "missed_prescribed_session"
    elif actual.get("sessions", 0) > 0 and expected.get("sessions", 0) == 0:
        adherence = "trained_on_planned_rest"
    elif lower is not None and actual_load is not None and actual_load < lower:
        adherence = "easier_than_predicted"
    elif upper is not None and actual_load is not None and actual_load > upper:
        adherence = "harder_than_predicted"
    else:
        adherence = "matched_expected_load"
    load_ratio = actual_load / expected_load if actual_load is not None and expected_load else None
    duration = actual.get("duration_min") or 0
    expected_duration = expected.get("duration_min") or 0
    duration_ratio = duration / expected_duration if expected_duration else None

    adjusted_response = prediction.get("coaching_adjusted_next_day_response") or {}
    raw_expected_response = prediction.get("expected_next_day_response") or {}
    expected_response = adjusted_response.get("score")
    if expected_response is None:
        expected_response = raw_expected_response.get("score")
    actual_response = response.get("score") if response.get("status") == "available" else None
    response_delta = (
        actual_response - expected_response
        if actual_response is not None and expected_response is not None
        else None
    )
    if actual_response is None:
        response_status = "pending_next_day"
    else:
        response_status = _response_status_from_delta(response_delta)

    if response_status == "pending_next_day":
        physiology_calibration_status = "pending_next_day"
        physiology_calibration_weight = 0.0
        physiology_calibration_eligible = False
    elif response_status == "no_expected_response":
        physiology_calibration_status = "not_calibratable"
        physiology_calibration_weight = 0.0
        physiology_calibration_eligible = False
    elif adherence != "matched_expected_load":
        physiology_calibration_status = "execution_changed_input"
        physiology_calibration_weight = 0.0
        physiology_calibration_eligible = False
    elif response_status == "within_expected_band":
        physiology_calibration_status = "calibrated"
        physiology_calibration_weight = 1.0
        physiology_calibration_eligible = True
    else:
        physiology_calibration_status = "model_miss"
        physiology_calibration_weight = 1.0
        physiology_calibration_eligible = True

    quality = contract_quality or {
        "status": "not_reviewed",
        "calibration_eligible": False,
        "reasons": ["Contract-quality evidence was not built for this review."],
    }
    if not physiology_calibration_eligible:
        calibration_status = physiology_calibration_status
        calibration_weight = 0.0
        calibration_eligible = False
    elif quality.get("calibration_eligible"):
        calibration_status = physiology_calibration_status
        calibration_weight = physiology_calibration_weight
        calibration_eligible = True
    else:
        quality_status = quality.get("status")
        calibration_status = {
            "contract_missing": "contract_missing",
            "action_mismatch": "contract_action_mismatch",
            "execution_dose_stopped": "contract_dose_stopped",
            "unsafe_stop_rule_continued": "contract_unreliable",
            "technical_quality_degraded": "technical_quality_degraded",
        }.get(quality_status, "contract_incomplete")
        calibration_weight = 0.0
        calibration_eligible = False

    if response_status == "pending_next_day":
        interpretation = "Await next-day Garmin wellness before judging model calibration."
    elif response_status == "no_expected_response":
        interpretation = "Actual next-day response exists, but the model did not produce a comparable expectation."
    elif adherence != "matched_expected_load":
        interpretation = "The actual session changed the input; judge prescription execution before judging the model."
    elif not calibration_eligible:
        reason = next(iter(quality.get("reasons") or []), "The session contract review is incomplete.")
        interpretation = (
            "The physiological load-response pair is comparable, but this is not a full digital-twin calibration sample: "
            f"{reason}"
        )
    elif response_status == "within_expected_band":
        interpretation = "The model was acceptably calibrated for this complete session-response pair."
    elif response_status == "worse_than_expected":
        interpretation = "The model overestimated recovery, or non-training stress, heat, fueling, sleep, or trail cost was higher than represented."
    else:
        interpretation = "The model was conservative or adaptation/recovery was better than expected."

    return {
        "adherence_status": adherence,
        "training_load_delta": _round(load_delta),
        "training_load_delta_pct": _round(load_delta_pct),
        "duration_delta_min": _round((actual.get("duration_min") or 0) - (expected.get("duration_min") or 0)),
        "execution_drift": {
            "training_load_ratio": _round(load_ratio, 2),
            "duration_ratio": _round(duration_ratio, 2),
            "actual_changed_model_input": adherence != "matched_expected_load",
        },
        "self_evaluation": self_eval,
        "expected_response_source": "coaching_adjusted"
        if adjusted_response.get("score") is not None
        else "raw_model",
        "response_status": response_status,
        "response_delta": _round(response_delta),
        "execution_risk_stress_test": _stress_test_comparison(prediction, actual_response),
        "physiology_calibration_status": physiology_calibration_status,
        "physiology_calibration_eligible": physiology_calibration_eligible,
        "physiology_calibration_weight": physiology_calibration_weight,
        "contract_quality": quality,
        "calibration_status": calibration_status,
        "calibration_eligible": calibration_eligible,
        "calibration_weight": calibration_weight,
        "interpretation": interpretation,
    }


def _prediction_text(artifact: dict) -> str:
    expected = artifact["prediction"].get("expected_session") or {}
    response = artifact["prediction"].get("expected_next_day_response") or {}
    adjusted = artifact["prediction"].get("coaching_adjusted_next_day_response") or {}
    risk = artifact["prediction"].get("execution_risk") or {}
    stress = risk.get("stress_test_next_day_response") or {}
    stress_adjusted = risk.get("stress_test_coaching_adjusted_next_day_response") or {}
    return "\n".join(
        [
            f"Predictive Session Plan - {artifact['date']}",
            "",
            f"Model confidence: {artifact['model_confidence']['status']} - {artifact['model_confidence']['message']}",
            f"Planned session: {expected.get('title')} ({expected.get('intensity')}, {expected.get('duration_min')} min)",
            f"Expected load: {expected.get('expected_training_load')} ({expected.get('expected_training_load_range')})",
            f"Expected RPE score range: {expected.get('expected_rpe_score_range')}",
            f"Expected next-day response: {response.get('score')} / {response.get('readiness_level')} / {response.get('response_class')}",
            f"Coaching-adjusted next-day response: {adjusted.get('score')} / {adjusted.get('readiness_level')} / {adjusted.get('response_class')}",
            f"Execution risk: {risk.get('level')} / {risk.get('status')} - {risk.get('coaching_action')}",
            f"Stress-test next-day response if execution drifts: {stress.get('score')} / {stress.get('readiness_level')} / {stress.get('response_class')}",
            f"Stress-test coaching-adjusted response: {stress_adjusted.get('score')} / {stress_adjusted.get('readiness_level')} / {stress_adjusted.get('response_class')}",
            "",
        ]
    )


def _review_text(review: dict) -> str:
    comparison = review.get("comparison") or {}
    response = review.get("actual_next_day_response") or {}
    quality = comparison.get("contract_quality") or {}
    review_fields = quality.get("review_field_completion") or {}
    stop_rule = quality.get("stop_rule_outcome") or {}
    technical = quality.get("technical_quality") or {}
    return "\n".join(
        [
            f"Predictive Session Review - {review['date']}",
            "",
            f"Adherence: {comparison.get('adherence_status')}",
            f"Load delta: {comparison.get('training_load_delta')} ({comparison.get('training_load_delta_pct')}%)",
            f"Next-day response: {response.get('score')} / {response.get('readiness_level')} / {comparison.get('response_status')}",
            f"Physiology calibration: {comparison.get('physiology_calibration_status')} (eligible: {comparison.get('physiology_calibration_eligible')})",
            f"Contract quality: {quality.get('status')} (review fields: {len(review_fields.get('completed') or [])}/{len(review_fields.get('required') or [])})",
            f"Stop-rule outcome: {stop_rule.get('status')}; technical quality: {technical.get('status')}",
            f"Calibration: {comparison.get('calibration_status')} (eligible: {comparison.get('calibration_eligible')})",
            f"Interpretation: {comparison.get('interpretation')}",
            "",
        ]
    )


def build_predictive_prescription(
    root: str | Path | None = None,
    for_date: str | date | None = None,
    state: dict | None = None,
    plan: dict | None = None,
) -> dict:
    target = parse_date(for_date) or parse_date((state or {}).get("date")) or today_local(DEFAULT_TIMEZONE)
    state = state or build_current_state(root, target)
    plan = plan or _load_planned_session_plan(root, target) or build_today_plan(root, target, state=state)
    plan_source = plan.get("plan_source") or {
        "type": "today_plan",
        "path": "snapshots/today_plan.json",
    }
    model = _model_report(root, target)
    artifact = {
        "date": target.isoformat(),
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "artifact_type": "predictive_session_plan",
        "plan_source": plan_source,
        "model": {
            "source": "snapshots/training_response_model_report.json",
            "model_type": model.get("model_type"),
            "samples": model.get("samples"),
            "target": model.get("target"),
            "report_date": model.get("date"),
            "prediction_date": target.isoformat(),
            "reuse_policy": "reuse_valid_historical_tree_until_explicit_training-predictor rebuild",
            "validation": model.get("validation"),
        },
        "model_confidence": _model_confidence(model),
        "prediction": _prediction_from_plan(root, target, plan, model),
        "artifacts": {
            "current": "snapshots/predictive_session_plan.json",
            "dated": f"snapshots/predictive_session_{target.isoformat()}.json",
        },
    }
    dated_path = snapshots_dir(root) / f"predictive_session_{target.isoformat()}.json"
    existing_dated = read_json(dated_path, {})
    completed_same_day_activity = (
        target == today_local(DEFAULT_TIMEZONE) and bool(_activity_rows_for_date(root, target))
    )
    dated_write_status = "written"
    if completed_same_day_activity:
        if existing_dated:
            dated_write_status = "preserved_existing_after_activity"
        else:
            dated_write_status = "skipped_after_activity"
    artifact["artifacts"]["dated_write_status"] = dated_write_status
    if existing_dated and dated_write_status == "preserved_existing_after_activity":
        artifact["artifacts"]["preserved_dated_generated_at"] = existing_dated.get("generated_at")
    write_json(snapshots_dir(root) / "predictive_session_plan.json", artifact)
    if dated_write_status == "written":
        write_json(dated_path, artifact)
    write_text(snapshots_dir(root) / "predictive_session_plan.txt", _prediction_text(artifact))
    return artifact


def build_predictive_review(
    root: str | Path | None = None,
    for_date: str | date | None = None,
    prescription: dict | None = None,
) -> dict:
    target = parse_date(for_date) or parse_date((prescription or {}).get("date")) or today_local(DEFAULT_TIMEZONE)
    if prescription is None:
        prescription = read_json(snapshots_dir(root) / f"predictive_session_{target.isoformat()}.json", {})
        if not prescription:
            current = read_json(snapshots_dir(root) / "predictive_session_plan.json", {})
            prescription = current if current.get("date") == target.isoformat() else {}
    prediction = (prescription or {}).get("prediction") or {}
    actual = _actual_activity_summary(root, target)
    self_eval = _self_evaluation_for_date(root, target)
    response = _actual_next_day_response(root, target)
    expected = prediction.get("expected_session") or {}
    contract_quality = (
        _contract_quality_review(root, target, expected, actual, self_eval, response)
        if prediction
        else {
            "status": "not_applicable",
            "calibration_eligible": False,
            "reasons": ["No dated pre-session prescription was stored for this date."],
        }
    )
    comparison = (
        _compare_prediction(prediction, actual, self_eval, response, contract_quality)
        if prediction
        else {
            "adherence_status": "no_stored_prescription",
            "response_status": "not_reviewed",
            "physiology_calibration_status": "not_calibratable",
            "physiology_calibration_eligible": False,
            "physiology_calibration_weight": 0.0,
            "contract_quality": contract_quality,
            "calibration_status": "not_calibratable",
            "calibration_eligible": False,
            "calibration_weight": 0.0,
            "interpretation": "No dated pre-session prescription was stored for this date, so this session cannot calibrate the digital twin.",
        }
    )
    review = {
        "date": target.isoformat(),
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "artifact_type": "predictive_session_review",
        "prescription_available": bool(prescription),
        "prescription_source_date": (prescription or {}).get("date"),
        "expected": prediction,
        "actual_activity": actual,
        "actual_next_day_response": response,
        "comparison": comparison,
        "artifacts": {
            "current": "snapshots/predictive_session_review.json",
            "dated": f"snapshots/predictive_session_review_{target.isoformat()}.json",
        },
    }
    write_json(snapshots_dir(root) / "predictive_session_review.json", review)
    write_json(snapshots_dir(root) / f"predictive_session_review_{target.isoformat()}.json", review)
    write_text(snapshots_dir(root) / "predictive_session_review.txt", _review_text(review))
    return review


def build_predictive_training(
    root: str | Path | None = None,
    for_date: str | date | None = None,
    state: dict | None = None,
    plan: dict | None = None,
) -> dict:
    target = parse_date(for_date) or parse_date((state or {}).get("date")) or today_local(DEFAULT_TIMEZONE)
    prescription = build_predictive_prescription(root, target, state=state, plan=plan)
    review_target = target - timedelta(days=1)
    review = build_predictive_review(root, review_target)
    artifact = {
        "date": target.isoformat(),
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "artifact_type": "predictive_training_loop",
        "today_prescription": prescription,
        "latest_review": review,
        "workflow": [
            "Before training: build predictive_session_plan and store a dated prescription.",
            "Read the execution-risk branch: it estimates what happens if the session drifts beyond the written cap.",
            "After Garmin sync next day: build predictive_session_review for that date.",
            "If actual load differs materially, classify execution/adherence first.",
            "Only matched-load, action-aligned sessions with complete review fields, explicit stop-rule outcome, and clean technical evidence are eligible for digital-twin calibration.",
        ],
        "artifacts": {
            "prescription": "snapshots/predictive_session_plan.json",
            "review": "snapshots/predictive_session_review.json",
            "loop": "snapshots/predictive_training.json",
        },
    }
    write_json(snapshots_dir(root) / "predictive_training.json", artifact)
    return artifact
