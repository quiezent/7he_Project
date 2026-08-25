from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
import hashlib
import math
from pathlib import Path
import re
from statistics import median
from typing import Any

from .evidence import as_number, find_value, load_activities
from .io import read_json, write_json, write_text
from .load_model import build_activity_summary_index
from .paths import activities_dir, input_dir, snapshots_dir
from .planning import SESSION_CONTRACT_FIELDS, _nutrition_block, build_today_plan
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

_PREDICTION_PRIVATE_IDENTIFIER_KEYS = {
    "deviceid",
    "devicetypepk",
    "deviceversionpk",
    "serial",
    "serialnumber",
    "unitid",
    "applicationid",
    "userprofilepk",
    "profilepk",
    "userpk",
}


def _privacy_safe_prediction(value: Any, path: str = "expected") -> tuple[Any, list[str]]:
    """Copy a prediction while omitting device/profile identifiers from review surfaces."""
    if isinstance(value, dict):
        cleaned = {}
        removed = []
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            child_path = f"{path}.{key}"
            if normalized in _PREDICTION_PRIVATE_IDENTIFIER_KEYS:
                removed.append(child_path)
                continue
            safe_child, child_removed = _privacy_safe_prediction(child, child_path)
            cleaned[key] = safe_child
            removed.extend(child_removed)
        return cleaned, removed
    if isinstance(value, list):
        cleaned_list = []
        removed = []
        for index, child in enumerate(value):
            safe_child, child_removed = _privacy_safe_prediction(
                child, f"{path}[{index}]"
            )
            cleaned_list.append(safe_child)
            removed.extend(child_removed)
        return cleaned_list, removed
    return value, []
INTENSITY_ALIASES = {
    "easy_skill": "skill",
    "skill_easy": "skill",
    "skill_moderate": "moderate",
    "skill_moderate_hard": "moderate_hard",
}

MTB_SESSION_TYPES = {
    "endurance_skills",
    "mtb_durability_enduro",
    "mtb_quality_skill",
    "mtb_repeatability_controlled",
    "mtb_skill_transfer_optional",
    "mtb_skill_familiar_capped",
    "outdoor_bike_optional",
    "outdoor_mtb",
}
HIKING_MODALITIES = {"hike", "hiking"}
INDOOR_BIKE_SESSION_TYPES = {
    "bike_quality",
    "easy_bike_continuity",
    "indoor_tempo_torque",
    "garmin_aerobic_continuity",
}
MIN_EXECUTION_PROFILE_SAMPLES = 3
MIN_MATCHED_ROUTE_REPEAT_LOAD_SAMPLES = 2
MAX_DETAILED_REJECTED_ACTION_CANDIDATES = 25
LOOP_ARTIFACT_LOAD_TOLERANCE = 0.1
MATCHED_ACTION_DURATION_RATIO_RANGE = (0.8, 1.25)
OUT_OF_POLICY_DELIVERED_ACTION_WEIGHT = 0.35
MATCHED_2K_SESSION_TYPE = "mtb_skill_familiar_capped"
MATCHED_2K_ACTION_IDENTITY = {
    "schema_version": 1,
    "venue_key": "bukit_kiara",
    "route_key": "full_2k",
    "bike_key": "stumpjumper_expert_my25",
    "access_key": "self_pedaled",
}

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
OBJECTIVE_ACTIVITY_REVIEW_FIELDS = {
    "average_power_w": "avg_power",
    "normalized_power_w": "normalized_power",
    "average_hr": "avg_hr",
    "aerobic_training_effect": "aerobic_te",
    "anaerobic_training_effect": "anaerobic_te",
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
    stress_load_range = [
        expected.get("expected_training_load"),
        _round(max(stress_load, _number(profile.get("p75_actual_training_load"), stress_load))),
    ]
    return {
        **expected,
        "title": f"{expected.get('title')} - execution drift stress test",
        "duration_min": int(round(stress_duration)),
        "expected_training_load": _round(stress_load),
        "expected_training_load_range": stress_load_range,
        "execution_risk_training_load_expectation": {
            "source": "execution_risk_stress_test",
            "expected_value": _round(stress_load),
            "expected_range": stress_load_range,
        },
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


def _training_load_range(value: Any) -> tuple[list[float] | None, float | None, str | None]:
    explicit_value = None
    range_values = None
    if isinstance(value, dict):
        for key in ("value", "expected", "target"):
            parsed = as_number(value.get(key))
            if parsed is not None:
                explicit_value = parsed
                break
        for key in ("range", "expected_range", "target_range"):
            candidate = value.get(key)
            if isinstance(candidate, (list, tuple)) and len(candidate) == 2:
                range_values = [as_number(candidate[0]), as_number(candidate[1])]
                break
        if range_values is None:
            low = next(
                (
                    as_number(value.get(key))
                    for key in ("min", "minimum", "low", "lower")
                    if as_number(value.get(key)) is not None
                ),
                None,
            )
            high = next(
                (
                    as_number(value.get(key))
                    for key in ("max", "maximum", "high", "upper")
                    if as_number(value.get(key)) is not None
                ),
                None,
            )
            if low is not None and high is not None:
                range_values = [low, high]
    elif isinstance(value, (list, tuple)):
        if len(value) == 2:
            range_values = [as_number(value[0]), as_number(value[1])]
        elif len(value) == 1:
            explicit_value = as_number(value[0])
    elif isinstance(value, str):
        normalized_text = value.replace(",", "")
        range_match = re.search(
            r"(?<![\w.])(\d+(?:\.\d+)?)\s*(?:-|–|\bto\b)\s*(\d+(?:\.\d+)?)",
            normalized_text,
            flags=re.IGNORECASE,
        )
        if range_match:
            range_values = [float(range_match.group(1)), float(range_match.group(2))]
        else:
            numbers = [
                float(match)
                for match in re.findall(r"(?<![\w.])\d+(?:\.\d+)?", normalized_text)
            ]
            if numbers:
                explicit_value = numbers[0]
    else:
        explicit_value = as_number(value)

    if range_values is not None:
        low, high = range_values
        if low is None or high is None or low < 0 or high < low:
            return None, None, None
        selected_value = (
            explicit_value
            if explicit_value is not None and low <= explicit_value <= high
            else (low + high) / 2.0
        )
        if selected_value < 0:
            return None, None, None
        return [_round(low), _round(high)], _round(selected_value), "explicit_range"
    if explicit_value is None or explicit_value < 0:
        return None, None, None
    return (
        [_round(explicit_value * 0.7), _round(explicit_value * 1.35)],
        _round(explicit_value),
        "contract_value_with_default_tolerance",
    )


def _contract_training_load_expectation(expected: dict) -> dict | None:
    if _number(expected.get("schema_version")) < 3:
        return None
    expected_result = expected.get("expected_result")
    if not isinstance(expected_result, dict):
        return None
    for key in ("garmin_training_load", "garmin_load"):
        if key not in expected_result:
            continue
        raw_value = expected_result.get(key)
        expected_range, expected_value, range_basis = _training_load_range(raw_value)
        if expected_range is None or expected_value is None:
            continue
        return {
            "source": f"schema_v3_contract.expected_result.{key}",
            "raw_value": raw_value,
            "expected_value": expected_value,
            "expected_range": expected_range,
            "range_basis": range_basis,
        }
    return None


def _selected_training_load_expectation(expected: dict) -> dict:
    generic_audit = expected.get("generic_training_load_expectation")
    generic_source = (
        generic_audit if isinstance(generic_audit, dict) else expected
    )
    generic_range = generic_source.get("expected_range")
    if generic_range is None:
        generic_range = generic_source.get("expected_training_load_range")
    if not isinstance(generic_range, (list, tuple)) or len(generic_range) != 2:
        generic_range = [None, None]
    else:
        generic_range = [as_number(generic_range[0]), as_number(generic_range[1])]
    generic = {
        "source": "generic_deterministic_duration_intensity",
        "expected_value": as_number(
            generic_source.get("expected_value")
            if generic_source.get("expected_value") is not None
            else generic_source.get("expected_training_load")
        ),
        "expected_range": generic_range,
    }
    stress_override = expected.get("execution_risk_training_load_expectation")
    if not isinstance(stress_override, dict):
        stress_override = None
    contract = _contract_training_load_expectation(expected)
    action = expected.get("action_matched_training_load_expectation")
    if not isinstance(action, dict):
        action = None
    if stress_override:
        selected = stress_override
    elif contract:
        selected = contract
    elif action:
        selected = {
            "source": (
                action.get("source")
                if action.get("status") == "matched_route_repeat_baseline"
                else "historical_matched_route_repeat_load_prior_fail_closed"
            ),
            "expected_value": as_number(action.get("expected_value")),
            "expected_range": action.get("expected_range") or [None, None],
        }
    else:
        selected = generic
    result = {
        "selected_source": selected.get("source"),
        "expected_value": selected.get("expected_value"),
        "expected_range": selected.get("expected_range"),
        "contract": contract,
        "generic_deterministic": generic,
    }
    if stress_override:
        result["execution_risk_stress_test"] = stress_override
    if action:
        result["action_matched"] = action
    return result


def _rpe_score_range(
    value: Any,
    *,
    require_rpe_label: bool = False,
) -> list[float] | None:
    range_values: list[float | None] | None = None
    if isinstance(value, dict):
        for key in (
            "range",
            "expected_range",
            "target_range",
            "rpe_range",
            "rpe_range_out_of_10",
            "whole_session_rpe_range",
        ):
            candidate = value.get(key)
            if isinstance(candidate, (list, tuple)) and len(candidate) == 2:
                range_values = [as_number(candidate[0]), as_number(candidate[1])]
                break
        if range_values is None:
            low = next(
                (
                    as_number(value.get(key))
                    for key in ("min", "minimum", "low", "lower")
                    if as_number(value.get(key)) is not None
                ),
                None,
            )
            high = next(
                (
                    as_number(value.get(key))
                    for key in ("max", "maximum", "high", "upper")
                    if as_number(value.get(key)) is not None
                ),
                None,
            )
            if low is not None and high is not None:
                range_values = [low, high]
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        range_values = [as_number(value[0]), as_number(value[1])]
    elif isinstance(value, str):
        if require_rpe_label:
            matches = list(
                re.finditer(
                    (
                        r"(?P<label>(?:whole[\s-]*session[\s-]*)?rpe)\s*"
                        r"(?:(?:approximately|about|around)\s*)?"
                        r"(?P<low>\d+(?:\.\d+)?)\s*(?:-|–|—|\bto\b)\s*"
                        r"(?P<high>\d+(?:\.\d+)?)"
                    ),
                    value,
                    flags=re.IGNORECASE,
                )
            )
            if matches:
                match = next(
                    (
                        candidate
                        for candidate in matches
                        if "whole" in candidate.group("label").lower()
                    ),
                    matches[0],
                )
                range_values = [
                    float(match.group("low")),
                    float(match.group("high")),
                ]
        else:
            match = re.search(
                r"(?<![\w.])(\d+(?:\.\d+)?)\s*(?:-|–|—|\bto\b)\s*(\d+(?:\.\d+)?)",
                value,
                flags=re.IGNORECASE,
            )
            if match:
                range_values = [float(match.group(1)), float(match.group(2))]

    if range_values is None:
        return None
    low, high = range_values
    if low is None or high is None or low < 0 or high < low or high > 100:
        return None
    scale = 10.0 if high <= 10 else 1.0
    return [_round(low * scale), _round(high * scale)]


def _contract_rpe_expectation(expected: dict) -> dict | None:
    if _number(expected.get("schema_version")) < 3:
        return None
    expected_result = expected.get("expected_result")
    if not isinstance(expected_result, dict):
        return None

    direct_keys = (
        "rpe_range_out_of_10",
        "whole_session_rpe_range",
        "session_rpe_range",
        "rpe_range",
        "whole_session_rpe",
        "session_rpe",
    )
    for key in direct_keys:
        if key not in expected_result:
            continue
        raw_value = expected_result.get(key)
        expected_range = _rpe_score_range(raw_value)
        if expected_range is not None:
            return {
                "source": f"schema_v3_contract.expected_result.{key}",
                "raw_value": raw_value,
                "expected_score_range": expected_range,
                "scale": "garmin_rpe_score_0_to_100",
            }

    physiology = expected_result.get("physiology")
    if isinstance(physiology, dict):
        for key in direct_keys:
            if key not in physiology:
                continue
            raw_value = physiology.get(key)
            expected_range = _rpe_score_range(raw_value)
            if expected_range is not None:
                return {
                    "source": f"schema_v3_contract.expected_result.physiology.{key}",
                    "raw_value": raw_value,
                    "expected_score_range": expected_range,
                    "scale": "garmin_rpe_score_0_to_100",
                }
    elif isinstance(physiology, str):
        expected_range = _rpe_score_range(
            physiology,
            require_rpe_label=True,
        )
        if expected_range is not None:
            return {
                "source": "schema_v3_contract.expected_result.physiology",
                "raw_value": physiology,
                "expected_score_range": expected_range,
                "scale": "garmin_rpe_score_0_to_100",
            }
    return None


def _gear_bike_key(value: Any) -> str | None:
    normalized = _normalized_key(value)
    if "stumpjumper" in normalized and "expert" in normalized and "my25" in normalized:
        return "stumpjumper_expert_my25"
    return None


def _positive_int(value: Any) -> int | None:
    parsed = as_number(value)
    if parsed is None or parsed <= 0 or not float(parsed).is_integer():
        return None
    return int(parsed)


def _normalized_matched_2k_action_identity(value: Any) -> tuple[dict | None, list[str]]:
    if not isinstance(value, dict):
        return None, ["action_identity_missing"]
    normalized = {
        "schema_version": _positive_int(value.get("schema_version")),
        "venue_key": _normalized_key(value.get("venue_key")),
        "route_key": _normalized_key(value.get("route_key")),
        "bike_key": _normalized_key(value.get("bike_key")),
        "access_key": _normalized_key(value.get("access_key")),
        "quality_descent_count": _positive_int(value.get("quality_descent_count")),
    }
    reasons = [
        f"{key}_mismatch"
        for key, expected_value in MATCHED_2K_ACTION_IDENTITY.items()
        if normalized.get(key) != expected_value
    ]
    if normalized["quality_descent_count"] not in {2, 3}:
        reasons.append("quality_descent_count_must_be_2_or_3")
    if reasons:
        return None, reasons
    return normalized, []


def _planned_matched_2k_identity(session: dict) -> tuple[dict | None, dict]:
    """Require an explicit versioned action identity; never infer it from prose."""
    if str(session.get("type") or "") != MATCHED_2K_SESSION_TYPE:
        return None, {
            "status": "not_applicable_session_type",
            "missing_fields": [],
        }
    identity, reasons = _normalized_matched_2k_action_identity(
        session.get("action_identity")
    )
    if identity is None:
        return None, {
            "status": "missing_action_identity",
            "reasons": reasons,
            "required_path": "session.action_identity",
            "required_schema": {
                **MATCHED_2K_ACTION_IDENTITY,
                "quality_descent_count": "2_or_3",
            },
        }
    return identity, {
        "status": "complete",
        "source": "session.action_identity",
        "sensor_comparability_requirement": "external_hr_confirmed_separately",
    }


def _rows_from_index(root: str | Path | None, name: str) -> list[dict]:
    payload = read_json(snapshots_dir(root) / name, {})
    if isinstance(payload, dict):
        rows = payload.get("activities") or []
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    return [row for row in rows if isinstance(row, dict)]


def _feedback_containers_for_activity(
    root: str | Path | None,
    sample_date: date,
    activity_id: str,
) -> list[dict]:
    payload = read_json(input_dir(root) / f"feedback_{sample_date.isoformat()}.json", {})
    if not isinstance(payload, dict):
        return []
    containers: list[dict] = []

    def add(container: dict) -> None:
        containers.append(container)
        for key in CONTRACT_REVIEW_KEYS:
            nested = container.get(key)
            if isinstance(nested, dict):
                containers.append(nested)

    root_id = payload.get("activity_id")
    if root_id is not None and str(root_id) == activity_id:
        add(payload)
    for entry in payload.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        entry_id = entry.get("activity_id")
        if entry_id is not None and str(entry_id) == activity_id:
            add(entry)
    return containers


def _structured_feedback_identity(containers: list[dict]) -> dict:
    for index, container in enumerate(containers):
        identity, reasons = _normalized_matched_2k_action_identity(
            container.get("action_identity")
        )
        if identity is not None:
            return {
                "identity": identity,
                "source": f"activity_scoped_feedback_container[{index}].action_identity",
                "recorded_at_local": container.get(
                    "action_identity_recorded_at_local"
                ),
                "reasons": [],
            }
        if container.get("action_identity") is not None:
            return {
                "identity": None,
                "source": f"activity_scoped_feedback_container[{index}].action_identity",
                "recorded_at_local": container.get(
                    "action_identity_recorded_at_local"
                ),
                "reasons": reasons,
            }
    return {
        "identity": None,
        "source": None,
        "recorded_at_local": None,
        "reasons": ["structured_activity_action_identity_missing"],
    }


def _loop_artifact_corroboration(
    root: str | Path | None,
    sample_date: date,
    activity_id: str,
    raw_training_load: float,
    expected_quality_descent_count: int,
) -> tuple[list[str], dict]:
    path = (
        snapshots_dir(root)
        / f"activity_loop_load_{sample_date.isoformat()}_{activity_id}.json"
    )
    payload = read_json(path, None)
    provenance = {
        "source": "dated_activity_loop_load_artifact",
        "artifact_ref": _activity_ref_for_feedback_id(activity_id),
        "official_load_tolerance": LOOP_ARTIFACT_LOAD_TOLERANCE,
        "route_authority": "none_structure_corroboration_only",
    }
    if not isinstance(payload, dict):
        return ["dated_loop_artifact_missing"], provenance

    reasons = []
    if str(payload.get("activity_id") or "") != activity_id:
        reasons.append("loop_artifact_activity_id_mismatch")
    if parse_date(payload.get("date")) != sample_date:
        reasons.append("loop_artifact_date_mismatch")

    official_load = as_number(payload.get("official_activity_training_load"))
    if official_load is None:
        load_delta = None
        reasons.append("loop_artifact_official_load_missing")
    else:
        load_delta = abs(float(official_load) - float(raw_training_load))
        if load_delta > LOOP_ARTIFACT_LOAD_TOLERANCE:
            reasons.append("loop_artifact_official_load_mismatch")

    loops = [row for row in payload.get("loops") or [] if isinstance(row, dict)]
    paired = [
        row
        for row in loops
        if {"climb", "descent"}.issubset(
            {_normalized_key(value) for value in row.get("lap_kinds") or []}
        )
    ]
    legacy_full_2k = []
    for row in loops:
        raw_label = str(row.get("label") or "")
        label = _normalized_key(raw_label)
        tokens = set(label.split("_"))
        kinds = {_normalized_key(value) for value in row.get("lap_kinds") or []}
        is_2k_plus = bool(
            re.search(r"2\s*k\s*\+", raw_label, flags=re.IGNORECASE)
            or "2k_plus" in label
            or "2kplus" in label
        )
        if "descent" in kinds and "2k" in tokens and not is_2k_plus:
            legacy_full_2k.append(row)

    if paired:
        structure_mode = "paired_climb_descent_loops"
        corroborated_count = len(paired)
    elif legacy_full_2k:
        structure_mode = "legacy_full_2k_descent_labels"
        corroborated_count = len(legacy_full_2k)
    else:
        structure_mode = "unrecognized"
        corroborated_count = None
        reasons.append("loop_artifact_repeat_structure_unrecognized")
    if (
        corroborated_count is not None
        and corroborated_count != expected_quality_descent_count
    ):
        reasons.append("loop_artifact_quality_descent_count_mismatch")

    provenance.update(
        {
            "artifact_date": payload.get("date"),
            "artifact_activity_ref": _activity_ref_for_feedback_id(
                payload.get("activity_id")
            ),
            "raw_activity_training_load": _round(raw_training_load),
            "official_activity_training_load": _round(official_load),
            "official_load_absolute_delta": _round(load_delta, 4),
            "repeat_structure_mode": structure_mode,
            "corroborated_quality_descent_count": corroborated_count,
        }
    )
    return reasons, provenance


def _matched_2k_sample_identity(
    root: str | Path | None,
    activity: dict,
    gear_rows: list[dict],
    device_rows: list[dict],
    feedback_containers: list[dict] | None = None,
) -> tuple[dict | None, list[str], dict]:
    activity_id = str(activity.get("id") or "")
    sample_date = parse_date(activity.get("date"))
    if not activity_id or sample_date is None:
        return None, ["activity_identity_missing"], {}

    gear = next(
        (row for row in gear_rows if str(row.get("activity_id") or "") == activity_id),
        None,
    )
    device = next(
        (row for row in device_rows if str(row.get("activity_id") or "") == activity_id),
        None,
    )
    gear_labels = [
        item.get("label") or item.get("custom_make_model")
        for item in (gear or {}).get("gear") or []
        if isinstance(item, dict)
    ]
    gear_bike_key = next(
        (_gear_bike_key(value) for value in gear_labels if _gear_bike_key(value)),
        None,
    )
    gear_fetch_confirmed = bool(
        isinstance(gear, dict)
        and gear.get("gear_fetch_ok") is True
        and parse_date(gear.get("date")) == sample_date
        and _normalized_key(gear.get("category")) == "mtb"
    )
    external_hr_battery_statuses = {
        _normalized_key(value)
        for value in (device or {}).get("external_hr_battery_statuses") or []
    }
    external_hr_confirmed = bool(
        isinstance(device, dict)
        and device.get("device_fetch_ok") is True
        and device.get("external_hr_sensor") is True
        and parse_date(device.get("date")) == sample_date
        and _normalized_key(device.get("category")) == "mtb"
        and device.get("hr_source_classification")
        == "external_standard_metadata"
        and bool(
            external_hr_battery_statuses.intersection({"good", "new", "ok"})
        )
    )

    containers = (
        feedback_containers
        if feedback_containers is not None
        else _feedback_containers_for_activity(root, sample_date, activity_id)
    )
    feedback_identity = _structured_feedback_identity(containers)
    identity = feedback_identity.get("identity")
    reasons = list(feedback_identity.get("reasons") or [])
    if not gear_fetch_confirmed or gear_bike_key is None:
        reasons.append("stumpjumper_gear_provenance_not_confirmed")
    if not external_hr_confirmed:
        reasons.append("external_hr_provenance_not_confirmed")
    if identity and gear_bike_key != identity.get("bike_key"):
        reasons.append("feedback_gear_bike_key_conflict")
    loop_provenance = None
    if identity:
        loop_reasons, loop_provenance = _loop_artifact_corroboration(
            root,
            sample_date,
            activity_id,
            float(as_number(activity.get("training_load")) or 0.0),
            identity["quality_descent_count"],
        )
        reasons.extend(loop_reasons)
    if reasons:
        return None, reasons, {
            "feedback_available": bool(containers),
            "action_identity_recorded_at_local": feedback_identity.get(
                "recorded_at_local"
            ),
            "loop_artifact": loop_provenance,
        }
    return {
        **identity,
        "sensor_key": "external_hr_confirmed",
    }, [], {
        "identity_sources": {
            "action_identity": feedback_identity.get("source"),
            "bike_key_corroboration": "snapshots/activity_gear_index.json",
            "sensor_key": "snapshots/activity_device_index.json",
            "loop_structure_and_load": "dated_activity_loop_load_artifact",
        },
        "action_identity_recorded_at_local": feedback_identity.get(
            "recorded_at_local"
        ),
        "loop_artifact": loop_provenance,
    }


def _matched_action_load_range(point: float, loads: list[float]) -> list[float]:
    """Conservative coaching envelope, not a statistical confidence interval."""
    lower = min(min(loads), point * 0.82)
    upper = max(max(loads), point * 1.12)
    return [float(math.floor(lower / 10.0) * 10), float(math.ceil(upper / 10.0) * 10)]


def _apply_matched_2k_load_baseline(
    root: str | Path | None,
    target: date,
    plan: dict,
    expected: dict,
) -> dict:
    session = plan.get("session") if isinstance(plan.get("session"), dict) else {}
    if str(session.get("type") or "") != MATCHED_2K_SESSION_TYPE:
        return expected
    # This existing session type covers several familiar-skill prescriptions.
    # The narrow route-repeat prior is opt-in through an explicit identity so
    # unrelated sessions retain the generic predictor.
    if "action_identity" not in session:
        return expected

    identity, identity_audit = _planned_matched_2k_identity(session)
    planned_duration_min = as_number(session.get("duration_min"))
    base = {
        "source": "historical_matched_route_repeat_load_prior",
        "target_date": target.isoformat(),
        "strictly_prior_samples_only": True,
        "minimum_matched_route_repeat_samples": MIN_MATCHED_ROUTE_REPEAT_LOAD_SAMPLES,
        "action_identity": identity,
        "identity_audit": identity_audit,
        "planned_duration_min": _round(planned_duration_min),
        "sample_duration_ratio_range": list(MATCHED_ACTION_DURATION_RATIO_RANGE),
        "calibration_role": "load_expectation_only_not_digital_twin_calibration",
        "range_interpretation": "conservative_coaching_envelope_not_confidence_interval",
        "sample_count": 0,
        "sample_dates": [],
        "samples": [],
        "unannotated_mtb_rows_ignored": 0,
        "rejected_candidate_count": 0,
        "rejected_reason_counts": {},
        "rejected_candidates_limit": MAX_DETAILED_REJECTED_ACTION_CANDIDATES,
        "rejected_candidates_truncated": 0,
        "rejected_candidates": [],
        "expected_value": None,
        "expected_range": [None, None],
    }
    generic = {
        "expected_value": expected.get("expected_training_load"),
        "expected_range": expected.get("expected_training_load_range"),
        "source": "generic_deterministic_duration_intensity_audit_only",
    }
    if planned_duration_min is None or planned_duration_min <= 0:
        identity_audit = {
            **identity_audit,
            "status": "invalid_planned_duration",
            "reason": "A positive planned duration is required for route-repeat load matching.",
        }
        identity = None
    if identity is None:
        baseline = {
            **base,
            "action_identity": identity,
            "identity_audit": identity_audit,
            "status": identity_audit.get("status"),
        }
        return {
            **expected,
            "generic_training_load_expectation": generic,
            "expected_training_load": None,
            "expected_training_load_range": [None, None],
            "action_matched_training_load_expectation": baseline,
        }

    gear_rows = _rows_from_index(root, "activity_gear_index.json")
    device_rows = _rows_from_index(root, "activity_device_index.json")
    accepted = []
    rejected = []
    accepted_activity_refs: set[str] = set()
    accepted_dates: set[str] = set()
    unannotated_mtb_rows_ignored = 0
    for activity in load_activities(root):
        sample_date = parse_date(activity.get("date"))
        if (
            sample_date is None
            or sample_date >= target
            or activity.get("category") != "mtb"
            or as_number(activity.get("training_load")) is None
        ):
            continue
        activity_id = str(activity.get("id") or "")
        feedback_containers = _feedback_containers_for_activity(
            root,
            sample_date,
            activity_id,
        )
        if not any(
            "action_identity" in container for container in feedback_containers
        ):
            unannotated_mtb_rows_ignored += 1
            continue
        sample_identity, reasons, provenance = _matched_2k_sample_identity(
            root,
            activity,
            gear_rows,
            device_rows,
            feedback_containers,
        )
        try:
            identity_recorded_date = parse_date(
                provenance.get("action_identity_recorded_at_local")
            )
        except (TypeError, ValueError):
            identity_recorded_date = None
            reasons.append("action_identity_recorded_at_invalid")
        if identity_recorded_date is None and "action_identity_recorded_at_invalid" not in reasons:
            reasons.append("action_identity_recorded_at_missing")
        elif identity_recorded_date is not None and identity_recorded_date >= target:
            reasons.append("action_identity_not_available_before_prediction_date")
        activity_ref = _activity_ref_for_feedback_id(activity.get("id"))
        sample_duration_min = as_number(activity.get("duration_min"))
        duration_ratio = (
            sample_duration_min / planned_duration_min
            if sample_duration_min is not None and planned_duration_min
            else None
        )
        provenance["duration_comparability"] = {
            "planned_duration_min": _round(planned_duration_min),
            "sample_duration_min": _round(sample_duration_min),
            "sample_to_planned_ratio": _round(duration_ratio, 3),
            "accepted_ratio_range": list(MATCHED_ACTION_DURATION_RATIO_RANGE),
        }
        if duration_ratio is None:
            reasons.append("sample_duration_missing")
        elif not (
            MATCHED_ACTION_DURATION_RATIO_RANGE[0]
            <= duration_ratio
            <= MATCHED_ACTION_DURATION_RATIO_RANGE[1]
        ):
            reasons.append("sample_duration_not_comparable")
        sample_date_key = sample_date.isoformat()
        if activity_ref in accepted_activity_refs:
            reasons.append("duplicate_activity_ref")
        if sample_date_key in accepted_dates:
            reasons.append("non_independent_sample_date")
        expected_sample_identity = {
            **identity,
            "sensor_key": "external_hr_confirmed",
        }
        if reasons or sample_identity != expected_sample_identity:
            mismatch_reasons = list(reasons)
            if sample_identity and not mismatch_reasons:
                mismatch_reasons = [
                    f"{field}_mismatch"
                    for field, expected_value in expected_sample_identity.items()
                    if sample_identity.get(field) != expected_value
                ]
            rejected.append(
                {
                    "date": sample_date.isoformat(),
                    "activity_ref": activity_ref,
                    "reasons": mismatch_reasons or ["action_identity_mismatch"],
                    "evidence": provenance,
                }
            )
            continue
        accepted.append(
            {
                "date": sample_date.isoformat(),
                "activity_ref": activity_ref,
                "training_load": _round(as_number(activity.get("training_load"))),
                "action_identity": sample_identity,
                **provenance,
            }
        )
        if activity_ref:
            accepted_activity_refs.add(activity_ref)
        accepted_dates.add(sample_date_key)
    accepted.sort(key=lambda row: (row["date"], row.get("activity_ref") or ""))
    rejected.sort(key=lambda row: (row["date"], row.get("activity_ref") or ""))
    rejected_candidate_count = len(rejected)
    rejected_reason_counts = dict(
        sorted(
            Counter(
                reason
                for row in rejected
                for reason in row.get("reasons") or []
            ).items()
        )
    )
    detailed_rejected = rejected[-MAX_DETAILED_REJECTED_ACTION_CANDIDATES:]
    rejection_audit = {
        "unannotated_mtb_rows_ignored": unannotated_mtb_rows_ignored,
        "rejected_candidate_count": rejected_candidate_count,
        "rejected_reason_counts": rejected_reason_counts,
        "rejected_candidates_limit": MAX_DETAILED_REJECTED_ACTION_CANDIDATES,
        "rejected_candidates_truncated": max(
            0,
            rejected_candidate_count - MAX_DETAILED_REJECTED_ACTION_CANDIDATES,
        ),
        "rejected_candidates": detailed_rejected,
    }
    if len(accepted) < MIN_MATCHED_ROUTE_REPEAT_LOAD_SAMPLES:
        baseline = {
            **base,
            "status": "insufficient_matched_route_repeat_samples",
            "sample_count": len(accepted),
            "sample_dates": [row["date"] for row in accepted],
            "samples": accepted,
            **rejection_audit,
        }
        return {
            **expected,
            "generic_training_load_expectation": generic,
            "expected_training_load": None,
            "expected_training_load_range": [None, None],
            "action_matched_training_load_expectation": baseline,
        }

    loads = [float(row["training_load"]) for row in accepted]
    point = float(median(loads))
    expected_range = _matched_action_load_range(point, loads)
    baseline = {
        **base,
        "status": "matched_route_repeat_baseline",
        "sample_count": len(accepted),
        "sample_dates": [row["date"] for row in accepted],
        "samples": accepted,
        **rejection_audit,
        "estimate_method": "median_matched_route_repeat_training_load",
        "range_method": "observed_values_with_18pct_lower_and_12pct_upper_median_envelope_rounded_outward_to_10",
        "expected_value": _round(point),
        "expected_range": expected_range,
    }
    return {
        **expected,
        "generic_training_load_expectation": generic,
        "expected_training_load": _round(point),
        "expected_training_load_range": expected_range,
        "action_matched_training_load_expectation": baseline,
    }


def _session_expectation(plan: dict) -> dict:
    session = plan.get("session") or {}
    session_type = str(session.get("type") or "unknown")
    intensity = str(session.get("intensity") or "easy")
    generic_intensity = INTENSITY_ALIASES.get(intensity, intensity)
    duration = int(session.get("duration_min") or 0)
    modality = str(session.get("modality") or "").lower()
    if session_type == "scheduled_rest":
        duration = 0
    session_count = 1 if duration > 0 else 0
    base_load_per_hour = LOAD_PER_HOUR.get(generic_intensity, LOAD_PER_HOUR["easy"])
    is_mtb = modality == "mtb" or session_type in MTB_SESSION_TYPES
    is_bike = (
        is_mtb
        or modality in {"bike", "bike_indoor", "bike_outdoor", "cycling"}
        or session_type in INDOOR_BIKE_SESSION_TYPES
        or "bike" in session_type
        or "cycling" in session_type
    )
    is_hike = modality in HIKING_MODALITIES or session_type.lower() in HIKING_MODALITIES
    if is_mtb:
        base_load_per_hour += 10
    elif session_type == "bike_quality":
        base_load_per_hour += 15
    training_load = (duration / 60.0) * base_load_per_hour if duration > 0 else 0.0
    if generic_intensity == "hard":
        high_intensity_min = duration * 0.25
    elif generic_intensity == "moderate_hard":
        high_intensity_min = duration * 0.16
    elif generic_intensity == "moderate":
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
        elif is_hike:
            categories["hike"] = 1
        else:
            categories["other"] = 1
    load_low = training_load * 0.7
    load_high = training_load * 1.35
    expected = {
        "title": session.get("title"),
        "type": session_type,
        "intensity": intensity,
        "modality": (
            "hike"
            if is_hike
            else modality or ("mtb" if is_mtb else "bike" if is_bike else "other")
        ),
        "duration_min": duration,
        "sessions": session_count,
        "expected_training_load": _round(training_load),
        "expected_training_load_range": [_round(load_low), _round(load_high)],
        "expected_high_intensity_min": _round(high_intensity_min),
        "expected_rpe_score_range": RPE_RANGES.get(
            generic_intensity,
            RPE_RANGES["easy"],
        ),
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
    if "optional" in session:
        expected["optional"] = session.get("optional")
    if session.get("schema_version"):
        expected["schema_version"] = session.get("schema_version")
    if session.get("contract_fields"):
        expected["contract_fields"] = session.get("contract_fields")
    if generic_intensity != intensity:
        expected["generic_intensity_alias"] = {
            "raw": intensity,
            "normalized": generic_intensity,
        }
    if isinstance(plan.get("nutrition"), dict):
        expected["planned_nutrition"] = plan.get("nutrition")
    contract_load = _contract_training_load_expectation(expected)
    if contract_load:
        expected["contract_training_load_expectation"] = contract_load
    contract_rpe = _contract_rpe_expectation(expected)
    if contract_rpe:
        expected["generic_expected_rpe_score_range"] = expected[
            "expected_rpe_score_range"
        ]
        expected["expected_rpe_score_range"] = contract_rpe[
            "expected_score_range"
        ]
        expected["contract_rpe_expectation"] = contract_rpe
    return expected


def _review_time_optionality_resolution(
    root: str | Path | None,
    target: date,
    prescription: dict,
    expected: dict,
) -> tuple[dict, dict]:
    """Recover omitted legacy optionality only from an exact dated-plan match."""
    match_fields = (
        "title",
        "type",
        "modality",
        "duration_min",
        "intensity",
        "schema_version",
        "contract_fields",
        *SESSION_CONTRACT_FIELDS,
    )
    plan_source = prescription.get("plan_source") or {}
    source_path = str(plan_source.get("path") or "").replace("\\", "/")
    expected_path = _planned_session_path(target)
    base = {
        "applied": False,
        "status": "not_applicable",
        "source": {
            "type": plan_source.get("type"),
            "path": source_path or None,
            "date": target.isoformat(),
        },
        "stored_optional_present": "optional" in expected,
        "stored_optional_value": expected.get("optional"),
        "resolved_optional_value": expected.get("optional"),
        "required_match_fields": list(match_fields),
        "matched_fields": [],
        "mismatched_fields": [],
        "contract_validation": None,
        "stored_prediction_mutated": False,
        "reason": None,
    }
    if "optional" in expected:
        return expected, {
            **base,
            "status": "stored_explicit_optionality",
            "reason": "The immutable stored prediction already contains explicit optionality.",
        }
    if plan_source.get("type") != "input_planned_session":
        return expected, {
            **base,
            "status": "not_applicable_plan_source",
            "reason": "Legacy recovery is limited to an explicitly recorded input_planned_session source.",
        }
    try:
        prescription_date = parse_date(prescription.get("date"))
    except (TypeError, ValueError):
        prescription_date = None
    if source_path != expected_path or prescription_date != target:
        return expected, {
            **base,
            "status": "rejected_invalid_source_identity",
            "reason": "The prescription date or canonical dated input path does not match the review date.",
        }
    source_payload = read_json(
        input_dir(root) / f"planned_session_{target.isoformat()}.json",
        {},
    )
    if not isinstance(source_payload, dict) or not isinstance(
        source_payload.get("session"), dict
    ):
        return expected, {
            **base,
            "status": "rejected_source_missing",
            "reason": "The recorded dated input plan is missing or does not contain a session object.",
        }
    try:
        source_date = parse_date(source_payload.get("date"))
    except (TypeError, ValueError):
        source_date = None
    if source_date != target:
        return expected, {
            **base,
            "status": "rejected_invalid_source_date",
            "reason": "The dated input plan payload does not explicitly match the review date.",
        }
    source_session = source_payload["session"]
    if source_session.get("optional") is not True:
        return expected, {
            **base,
            "status": "rejected_source_not_optional",
            "reason": "The dated input plan does not explicitly set session.optional to true.",
        }
    source_expected = _session_expectation({"session": source_session})
    contract_validation = {
        "stored": _expected_contract_status(expected),
        "source": _expected_contract_status(source_expected),
    }
    if not all(item.get("valid") for item in contract_validation.values()):
        return expected, {
            **base,
            "status": "rejected_incomplete_schema_v3_contract",
            "contract_validation": contract_validation,
            "reason": (
                "Legacy optionality recovery requires complete schema-v3 contracts in both the "
                "immutable prediction and its recorded dated input plan."
            ),
        }
    matched_fields = [
        field for field in match_fields if expected.get(field) == source_expected.get(field)
    ]
    mismatched_fields = [field for field in match_fields if field not in matched_fields]
    if mismatched_fields:
        return expected, {
            **base,
            "status": "rejected_stored_action_mismatch",
            "matched_fields": matched_fields,
            "mismatched_fields": mismatched_fields,
            "contract_validation": contract_validation,
            "reason": (
                "The current dated input plan does not exactly reproduce the immutable stored "
                "action and schema-v3 contract."
            ),
        }
    resolved = {**expected, "optional": True}
    return resolved, {
        **base,
        "applied": True,
        "status": "recovered_from_exact_dated_input_plan",
        "resolved_optional_value": True,
        "matched_fields": matched_fields,
        "contract_validation": contract_validation,
        "reason": (
            "The legacy prediction omitted optionality, but its recorded same-date input plan still "
            "exactly matches the stored action and schema-v3 contract and explicitly marks it optional."
        ),
    }


def _simulate_activity_day(activity_by_day: dict[str, dict], target: date, expected: dict) -> dict[str, dict]:
    simulated = {day: dict(row) for day, row in activity_by_day.items()}
    selected_load = _selected_training_load_expectation(expected)
    simulated_training_load = selected_load.get("expected_value")
    if simulated_training_load is None:
        simulated_training_load = expected.get("expected_training_load") or 0
    row = _activity_empty()
    row.update(
        {
            "sessions": expected.get("sessions") or 0,
            "duration_min": float(expected.get("duration_min") or 0),
            "training_load": float(simulated_training_load),
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
    expected = _apply_matched_2k_load_baseline(root, target, plan, expected)
    selected_load = _selected_training_load_expectation(expected)
    expected = {
        **expected,
        "selected_training_load_expectation": selected_load,
    }
    if selected_load.get("expected_value") is not None:
        expected["expected_training_load"] = _round(
            selected_load.get("expected_value")
        )
        expected["expected_training_load_range"] = list(
            selected_load.get("expected_range") or [None, None]
        )
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
    if selected_load.get("expected_value") is None:
        matched = expected.get("action_matched_training_load_expectation") or {}
        return {
            "status": "unavailable",
            "basis_date": basis_date.isoformat(),
            "action_date": target.isoformat(),
            "expected_session": expected,
            "training_load_expectation": selected_load,
            "warnings": warnings
            + [
                "Matched route-repeat MTB load expectation failed closed; generic duration/intensity load was retained for audit only and was not simulated.",
                f"Matched-baseline status: {matched.get('status') or 'unavailable'}.",
            ],
        }
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
        feature_row = feature_wellness_by_date.get(target.isoformat()) or {}
        raw_avg_stress = as_number(feature_row.get("avg_stress"))
        coverage_eligible = feature_row.get(
            "all_day_stress_low_positive_reward_eligible"
        )
        if raw_avg_stress is not None and raw_avg_stress <= 30 and coverage_eligible is not True:
            issues = feature_row.get("all_day_stress_sufficiency_issues") or []
            issue_text = ", ".join(str(item) for item in issues) or "coverage gate not passed"
            stress_basis_label = (
                "Target-date"
                if basis_date == target
                else f"State-basis {basis_date.isoformat()}"
            )
            reason = (
                f"{stress_basis_label} low average stress was withheld from the digital twin because "
                f"all-day coverage was not sufficient for positive use ({issue_text}). "
                "No stale or partial low-stress value was imputed."
            )
        elif as_number(feature_row.get("sleep_stress")) is None:
            reason = (
                "Target-date observed sleep stress is unavailable; the digital twin does "
                "not manufacture a sleep-stress proxy."
            )
        else:
            reason = (
                "One or more required target-date digital-twin features are unavailable."
            )
        return {
            "status": "unavailable",
            "basis_date": basis_date.isoformat(),
            "expected_session": expected,
            "warnings": [
                "Could not build digital-twin feature vector.",
                reason,
            ],
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


def _planned_fueling_ranges(expected: dict) -> tuple[dict[str, list[float]], dict]:
    nutrition = expected.get("planned_nutrition") or {}
    target_block = nutrition.get("during_session_targets") or {}
    ranges: dict[str, list[float]] = {}
    for field in ("carbs_g_per_hour", "fluid_ml_per_hour", "sodium_mg_per_hour"):
        values = target_block.get(field)
        if not isinstance(values, (list, tuple)) or len(values) != 2:
            continue
        low = as_number(values[0])
        high = as_number(values[1])
        if low is None or high is None or low > high:
            continue
        ranges[field] = [low, high]
    return ranges, target_block if isinstance(target_block, dict) else {}


def _fueling_adequacy_review(expected: dict, blocks: list[dict]) -> dict:
    ranges, target_block = _planned_fueling_ranges(expected)
    if not ranges:
        return {
            "applicable": False,
            "status": "planned_ranges_unavailable",
            "metrics": {},
            "calibration_effect": "audit_only",
            "affects_calibration_eligibility": False,
            "interpretation": "No explicit numeric fueling ranges were stored with the prescription.",
        }

    field_map = {
        "carbs_g_per_hour": "fueling_carbs_g_per_hour",
        "fluid_ml_per_hour": "fluid_ml_per_hour",
        "sodium_mg_per_hour": "sodium_mg_per_hour",
    }
    metrics = {}
    for target_field, feedback_field in field_map.items():
        planned = ranges.get(target_field)
        if planned is None:
            continue
        raw_value, source = _manual_review_value(blocks, feedback_field)
        actual_value = as_number(raw_value)
        if raw_value is None:
            status = "not_logged"
        elif actual_value is None or actual_value < 0:
            status = "invalid"
        elif actual_value < planned[0]:
            status = "below_planned_range"
        elif actual_value > planned[1]:
            status = "above_planned_range"
        else:
            status = "within_planned_range"
        metrics[target_field] = {
            "planned_range": planned,
            "actual": actual_value,
            "status": status,
            "source": source,
        }

    statuses = {row.get("status") for row in metrics.values()}
    if statuses <= {"within_planned_range"}:
        status = "within_planned_ranges"
        interpretation = "Logged carbohydrate, fluid, and sodium rates sit within the stored prescription ranges."
    elif statuses & {"not_logged", "invalid"}:
        status = "incomplete"
        interpretation = "At least one planned fueling rate is missing or invalid; retain fueling as an unresolved response confounder."
    else:
        status = "outside_planned_range"
        interpretation = "At least one logged fueling rate sits outside the stored prescription range; inspect it as a response confounder."
    return {
        "applicable": True,
        "status": status,
        "target_profile": target_block.get("profile"),
        "target_source": target_block.get("source"),
        "selection_reason": target_block.get("selection_reason"),
        "metrics": metrics,
        "calibration_effect": "audit_only",
        "affects_calibration_eligibility": False,
        "interpretation": interpretation,
    }


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
    activity_field = OBJECTIVE_ACTIVITY_REVIEW_FIELDS.get(normalized)
    activities = actual.get("activities") or []
    if (
        activity_field
        and actual.get("sessions") == 1
        and len(activities) == 1
        and isinstance(activities[0], dict)
        and activities[0].get(activity_field) is not None
    ):
        return f"garmin_activity.activities[0].{activity_field}"
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


def _nested_review_value(
    blocks: list[dict],
    aliases: tuple[str, ...],
) -> tuple[Any, str | None]:
    """Return a structured feedback value without copying its whole parent block."""
    normalized_aliases = {_normalized_key(alias) for alias in aliases}

    def find_nested(value: Any, path: str) -> tuple[Any, str | None]:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}"
                if _normalized_key(key) in normalized_aliases and _review_value_present(child):
                    return child, child_path
            for key, child in value.items():
                found, found_path = find_nested(child, f"{path}.{key}")
                if found_path:
                    return found, found_path
        elif isinstance(value, list):
            for index, child in enumerate(value):
                found, found_path = find_nested(child, f"{path}[{index}]")
                if found_path:
                    return found, found_path
        return None, None

    for block in reversed(blocks):
        source = str(block.get("source") or "feedback")
        found, found_path = find_nested(block.get("payload") or {}, source)
        if found_path:
            return found, found_path
    return None, None


def _trigger_repetition(value: Any) -> int | None:
    numeric = as_number(value)
    if numeric is not None and float(numeric).is_integer() and numeric > 0:
        return int(numeric)
    match = re.search(r"(?:rep(?:etition)?)[_\s-]*(\d+)", str(value or ""), re.IGNORECASE)
    return int(match.group(1)) if match else None


def _bounded_rpe_values(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)):
        return []
    values = []
    for item in value:
        number = as_number(item)
        if number is None or number < 0 or number > 10:
            return []
        values.append(float(number))
    return values


def _sanitized_repetition_rows(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    allowed = (
        "duration_min",
        "average_power_w",
        "normalized_power_w",
        "average_hr_bpm",
        "max_hr_bpm",
        "average_cadence_rpm",
    )
    rows = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue
        number = as_number(item.get("number"))
        repetition = int(number) if number is not None and number > 0 else index
        row = {"number": repetition}
        for field in allowed:
            metric = as_number(item.get(field))
            if metric is not None:
                row[field] = _round(metric, 1)
        if len(row) > 1:
            rows.append(row)
    return rows


def _feedback_source_scope(source: str | None) -> str | None:
    if not source:
        return None
    match = re.match(r"(feedback\.entries\[\d+\])", source)
    if match:
        return match.group(1)
    return "feedback" if source.startswith("feedback") else None


def _execution_learning_evidence(
    expected: dict,
    blocks: list[dict],
    stop_rule: dict,
) -> dict:
    """Build bounded evidence for action-boundary and stop-rule learning."""
    trigger_raw, trigger_source = _nested_review_value(
        blocks,
        ("stop_trigger_timing", "stop_rule_trigger_timing", "trigger_timing"),
    )
    trigger_repetition = _trigger_repetition(trigger_raw)
    rep_rpe_raw, rep_rpe_source = _nested_review_value(
        blocks,
        (
            "repetition_reported_rpe_0_to_10",
            "rep_by_rep_reported_rpe_0_to_10",
            "repetition_rpe_0_to_10",
        ),
    )
    repetition_rpe = _bounded_rpe_values(rep_rpe_raw)
    component_aliases = ["trigger_repetition_rpe_components_0_to_10"]
    if trigger_repetition:
        component_aliases.extend(
            (
                f"repetition_{trigger_repetition}_rpe_components_0_to_10",
                f"rep_{trigger_repetition}_rpe_components_0_to_10",
            )
        )
    component_raw, component_source = _nested_review_value(
        blocks,
        tuple(component_aliases),
    )
    components = {}
    if isinstance(component_raw, dict):
        for key, value in component_raw.items():
            number = as_number(value)
            if number is not None and 0 <= number <= 10:
                components[_normalized_key(key)] = _round(number, 1)

    objective_raw, objective_source = _nested_review_value(
        blocks,
        ("objective_interval_evidence",),
    )
    repetitions = _sanitized_repetition_rows(
        objective_raw.get("repetitions") if isinstance(objective_raw, dict) else None
    )
    continuation_reason, continuation_source = _nested_review_value(
        blocks,
        ("continuation_reason", "stop_rule_continuation_reason"),
    )

    rpe_range = expected.get("expected_rpe_score_range") or []
    rpe_ceiling = as_number(rpe_range[1]) if isinstance(rpe_range, (list, tuple)) and len(rpe_range) == 2 else None
    if rpe_ceiling is not None and rpe_ceiling > 10:
        rpe_ceiling /= 10.0
    last_within_rpe_ceiling = None
    if trigger_repetition and repetition_rpe and rpe_ceiling is not None:
        for repetition, value in enumerate(repetition_rpe[: trigger_repetition - 1], start=1):
            if value <= rpe_ceiling:
                last_within_rpe_ceiling = repetition
            else:
                break

    trigger_dimension = "reported_repetition_rpe"
    if components:
        maximum = max(components.values())
        leaders = sorted(key for key, value in components.items() if value == maximum)
        if len(leaders) == 1:
            trigger_dimension = leaders[0]

    power_values = [
        row["average_power_w"]
        for row in repetitions
        if row.get("average_power_w") is not None
    ]
    external_work_stable = None
    if len(power_values) >= 2:
        mean_power = sum(power_values) / len(power_values)
        external_work_stable = max(power_values) - min(power_values) <= max(5.0, mean_power * 0.03)

    explicitly_triggered = stop_rule.get("status") in {
        "triggered_and_stopped",
        "triggered_and_downshifted",
        "triggered_but_continued",
    }
    source_scope_aligned = bool(
        _feedback_source_scope(trigger_source)
        and _feedback_source_scope(trigger_source)
        == _feedback_source_scope(rep_rpe_source)
    )
    boundary_eligible = bool(
        explicitly_triggered
        and trigger_repetition
        and repetition_rpe
        and len(repetition_rpe) >= trigger_repetition
        and source_scope_aligned
    )
    delivered_action_characterized = bool(
        boundary_eligible
        and repetitions
        and any(row.get("number") == trigger_repetition for row in repetitions)
        and _feedback_source_scope(objective_source)
        == _feedback_source_scope(trigger_source)
    )
    return {
        "structured_boundary_evidence": boundary_eligible,
        "structured_delivered_action_evidence": delivered_action_characterized,
        "feedback_source_scope_aligned": source_scope_aligned,
        "trigger": {
            "timing": trigger_raw,
            "repetition": trigger_repetition,
            "dimension": trigger_dimension if boundary_eligible else None,
            "rpe_components_0_to_10": components,
            "source": trigger_source,
            "component_source": component_source,
        },
        "repetition_reported_rpe_0_to_10": repetition_rpe,
        "repetition_rpe_source": rep_rpe_source,
        "expected_rpe_ceiling_0_to_10": _round(rpe_ceiling, 1),
        "last_within_rpe_ceiling_repetition": last_within_rpe_ceiling,
        "objective_repetitions": repetitions,
        "objective_repetitions_source": objective_source,
        "external_work_stable": external_work_stable,
        "continuation_reason": continuation_reason if isinstance(continuation_reason, str) else None,
        "continuation_reason_source": continuation_source,
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


def _structured_feedback_action_alignment(blocks: list[dict]) -> dict:
    field_name = "action_alignment"
    container_names = {"coach_contract_audit", "contract_audit"}
    for block in reversed(blocks):
        payload = block.get("payload") or {}
        source = str(block.get("source") or "feedback")
        candidates: list[tuple[Any, str]] = []
        for key, value in payload.items():
            normalized_key = _normalized_key(key)
            if normalized_key == field_name:
                candidates.append((value, f"{source}.{key}"))
            elif normalized_key in container_names and isinstance(value, dict):
                for nested_key, nested_value in value.items():
                    if _normalized_key(nested_key) == field_name:
                        candidates.append(
                            (
                                nested_value,
                                f"{source}.{key}.{nested_key}",
                            )
                        )
        for value, value_source in candidates:
            if not _review_value_present(value):
                continue
            if isinstance(value, bool):
                status = "matched" if value else "mismatched"
                normalized = str(value).lower()
            elif isinstance(value, str):
                normalized = _normalized_key(value)
                tokens = set(normalized.split("_"))
                if tokens & {"mismatch", "mismatched", "drift", "drifted"}:
                    status = "mismatched"
                elif tokens & {"match", "matched", "align", "aligned"}:
                    status = "matched"
                else:
                    status = "unknown"
            else:
                normalized = _normalized_key(value)
                status = "unknown"
            return {
                "available": True,
                "status": status,
                "value": value,
                "normalized_value": normalized,
                "source": value_source,
            }
    return {
        "available": False,
        "status": "not_logged",
        "value": None,
        "normalized_value": None,
        "source": None,
    }


def _expected_categories_for_action_alignment(expected: dict) -> tuple[list[str], dict]:
    stored_categories = sorted(
        str(category)
        for category, count in (expected.get("categories") or {}).items()
        if _number(count) > 0
    )
    session_type = _normalized_key(expected.get("type"))
    modality = _normalized_key(expected.get("modality"))
    explicit_hike = session_type in HIKING_MODALITIES or modality in HIKING_MODALITIES
    if stored_categories == ["other"] and explicit_hike:
        comparison_categories = ["hike"]
        return comparison_categories, {
            "applied": True,
            "source": "review_time_legacy_expected_category_normalization",
            "reason": (
                "The immutable stored expected_session explicitly identifies a hike/hiking "
                "action but was created before hike had a canonical predictive category, so "
                "its legacy ['other'] category is compared as ['hike']."
            ),
            "stored_categories": stored_categories,
            "comparison_categories": comparison_categories,
            "evidence": {
                "expected_session.type": expected.get("type"),
                "expected_session.modality": expected.get("modality"),
                "expected_session.categories": expected.get("categories"),
            },
            "stored_prediction_mutated": False,
        }
    return stored_categories, {
        "applied": False,
        "source": None,
        "reason": None,
        "stored_categories": stored_categories,
        "comparison_categories": stored_categories,
        "stored_prediction_mutated": False,
    }


def _action_alignment(expected: dict, actual: dict, blocks: list[dict] | None = None) -> dict:
    expected_sessions = int(_number(expected.get("sessions")))
    actual_sessions = int(_number(actual.get("sessions")))
    allowed_optional_skip = (
        expected.get("optional") is True
        and expected_sessions > 0
        and actual_sessions == 0
    )
    expected_categories, expected_category_normalization = (
        _expected_categories_for_action_alignment(expected)
    )
    actual_categories = sorted(
        str(category)
        for category, count in (actual.get("categories") or {}).items()
        if _number(count) > 0
    )
    if allowed_optional_skip:
        modality_status = "not_applicable_optional_skip"
        session_count_status = "allowed_optional_skip"
    elif expected_sessions == 0:
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
    if allowed_optional_skip:
        duration_status = "not_applicable_optional_skip"
    elif expected_duration <= 0:
        duration_status = "not_applicable" if actual_sessions == 0 else "mismatched"
    elif actual_sessions == 0:
        duration_status = "missing"
    elif duration_ratio is None:
        duration_status = "unknown"
    elif 0.7 <= duration_ratio <= 1.35:
        duration_status = "matched"
    else:
        duration_status = "drifted"
    structured_feedback = _structured_feedback_action_alignment(blocks or [])
    component_statuses = (modality_status, session_count_status, duration_status)
    if allowed_optional_skip:
        overall = "allowed_optional_skip"
    elif expected_sessions == 0:
        overall = "not_applicable" if actual_sessions == 0 else "mismatched"
    elif structured_feedback.get("status") == "mismatched":
        overall = "mismatched"
    elif all(status == "matched" for status in component_statuses):
        overall = "matched"
    elif "missing" in component_statuses:
        overall = "missing"
    else:
        overall = "mismatched"
    return {
        "status": overall,
        "expected_categories": expected_categories,
        "stored_expected_categories": expected_category_normalization.get(
            "stored_categories"
        ),
        "expected_category_normalization": expected_category_normalization,
        "actual_categories": actual_categories,
        "modality_status": modality_status,
        "expected_sessions": expected_sessions,
        "actual_sessions": actual_sessions,
        "session_count_status": session_count_status,
        "expected_duration_min": _round(expected_duration),
        "actual_duration_min": _round(actual_duration),
        "duration_ratio": _round(duration_ratio, 2),
        "duration_status": duration_status,
        "structured_feedback_alignment": structured_feedback,
        "within_written_optionality": allowed_optional_skip,
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
    action = _action_alignment(expected, actual, blocks)
    stop_rule = _stop_rule_review(expected, blocks)
    execution_learning_evidence = _execution_learning_evidence(expected, blocks, stop_rule)
    technical_quality = _technical_quality_review(expected, blocks)
    fueling_adequacy = _fueling_adequacy_review(expected, blocks)
    reasons = []
    if not expected.get("sessions"):
        status = "not_applicable"
        reasons.append("No trainable session was prescribed.")
    elif action.get("status") == "allowed_optional_skip":
        status = "optional_skip"
        reasons.append(
            "The explicitly optional session was not performed; this is allowed by the written plan "
            "but provides no delivered session for calibration."
        )
    elif not contract.get("valid"):
        status = "contract_missing"
        reasons.append("The stored prediction does not contain a complete schema v3 session contract.")
    elif action.get("status") != "matched":
        status = "action_mismatch"
        reasons.append(
            "Actual modality, session count, duration, or explicitly logged route/stage execution "
            "does not match the stored action closely enough."
        )
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
        "execution_learning_evidence": execution_learning_evidence,
        "technical_quality": technical_quality,
        "fueling_adequacy": fueling_adequacy,
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


def _index_activities(root: str | Path | None, name: str) -> list[dict]:
    payload = read_json(snapshots_dir(root) / name, {})
    if isinstance(payload, dict):
        rows = payload.get("activities") or []
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    return [row for row in rows if isinstance(row, dict)]


def _activity_metadata_context(
    activity_id: str,
    device_rows: list[dict],
    gear_rows: list[dict],
) -> dict:
    device = next((row for row in device_rows if str(row.get("activity_id")) == activity_id), None)
    gear = next((row for row in gear_rows if str(row.get("activity_id")) == activity_id), None)
    def cached_after_refresh_failure(row: dict | None) -> bool:
        if not isinstance(row, dict) or row.get("last_attempt_ok") is not False:
            return False
        attempt = row.get("latest_attempt") if isinstance(row.get("latest_attempt"), dict) else {}
        return attempt.get("status") in {"failed", "unsupported", "success_empty"}

    device_cached = cached_after_refresh_failure(device)
    gear_cached = cached_after_refresh_failure(gear)
    if device is None:
        hr_confidence = "unknown_not_indexed"
    elif not device.get("device_fetch_ok"):
        hr_confidence = "unknown_fetch_failed"
    elif device.get("external_hr_sensor"):
        hr_confidence = "external_hr_confirmed"
    else:
        hr_confidence = "no_external_hr_reported"
    if gear is None:
        gear_confidence = "unknown_not_indexed"
        gear_labels = []
    elif not gear.get("gear_fetch_ok"):
        gear_confidence = "unknown_fetch_failed"
        gear_labels = []
    else:
        gear_confidence = "garmin_gear_confirmed"
        gear_labels = [
            item.get("label")
            for item in gear.get("gear") or []
            if isinstance(item, dict) and item.get("label")
        ]
    return {
        "device_fetch_status": "not_indexed"
        if device is None
        else "cached_after_refresh_failure"
        if device_cached
        else "ok"
        if device.get("device_fetch_ok")
        else "failed",
        "hr_source_confidence": hr_confidence,
        "sensor_types": sorted(
            {
                str(item.get("sensor_type"))
                for item in (device or {}).get("sensors") or []
                if isinstance(item, dict) and item.get("sensor_type")
            }
        ),
        "gear_fetch_status": "not_indexed"
        if gear is None
        else "cached_after_refresh_failure"
        if gear_cached
        else "ok"
        if gear.get("gear_fetch_ok")
        else "failed",
        "gear_confidence": gear_confidence,
        "gear_labels": gear_labels,
    }


def _raw_activity_context(root: str | Path | None, target: date, actual: dict) -> tuple[list[dict], dict[str, str]]:
    actual_refs = {
        str(row.get("activity_ref"))
        for row in actual.get("activities") or []
        if isinstance(row, dict) and row.get("activity_ref")
    }
    device_rows = _index_activities(root, "activity_device_index.json")
    gear_rows = _index_activities(root, "activity_gear_index.json")
    rows = []
    id_to_ref: dict[str, str] = {}
    for activity in load_activities(root):
        if parse_date(activity.get("date")) != target or not activity.get("counts_for_training_load"):
            continue
        activity_id = str(activity.get("id") or "")
        activity_ref = _activity_ref_for_feedback_id(activity_id)
        if not activity_ref or (actual_refs and activity_ref not in actual_refs):
            continue
        payload = read_json(activity.get("source_file"), {}) if activity.get("source_file") else {}
        if not isinstance(payload, dict):
            payload = {}
        elapsed_sec = as_number(find_value(payload, ("elapsedDuration", "duration")))
        moving_sec = as_number(find_value(payload, ("movingDuration",)))
        stopped_sec = (
            max(0.0, elapsed_sec - moving_sec)
            if elapsed_sec is not None and moving_sec is not None
            else None
        )
        entry = {
            "activity_ref": activity_ref,
            "category": activity.get("category"),
            "duration_min": activity.get("duration_min"),
            "moving_duration_min": _round(moving_sec / 60.0) if moving_sec is not None else None,
            "stopped_duration_min": _round(stopped_sec / 60.0) if stopped_sec is not None else None,
            "elevation_gain_m": _round(as_number(find_value(payload, ("elevationGain",))), 1),
            "elevation_loss_m": _round(as_number(find_value(payload, ("elevationLoss",))), 1),
            "temperature": {
                "device_min_c": _round(as_number(find_value(payload, ("minTemperature",))), 1),
                "device_max_c": _round(as_number(find_value(payload, ("maxTemperature",))), 1),
                "source": "garmin_activity_summary_device_temperature",
                "decision_use": "historical_device_exposure_context_not_air_temperature_or_forecast",
            },
            "water_estimated_ml": _round(as_number(find_value(payload, ("waterEstimated",))), 0),
            "water_estimate_guardrail": "Garmin water estimate is modeled loss, not measured sweat or logged intake.",
            "power": {
                "average_w": _round(as_number(find_value(payload, ("avgPower", "averagePower"))), 1),
                "normalized_w": _round(as_number(find_value(payload, ("normPower", "normalizedPower"))), 1),
                "max_w": _round(as_number(find_value(payload, ("maxPower",))), 1),
                "max_20_min_w": _round(as_number(find_value(payload, ("max20MinPower",))), 1),
                "garmin_detected_ftp_w": _round(as_number(find_value(payload, ("maxFtp",))), 0),
                "decision_use": (
                    "Session power and P20 are context only. A non-null maxFtp is Garmin's sparse "
                    "FTP-detection surface and may update the current operational FTP when dated and sensor-backed."
                ),
            },
            "training_effect": {
                "aerobic": _round(as_number(find_value(payload, ("aerobicTrainingEffect",))), 1),
                "anaerobic": _round(as_number(find_value(payload, ("anaerobicTrainingEffect",))), 1),
                "label": find_value(payload, ("trainingEffectLabel",)),
            },
            "training_stress_score": _round(as_number(find_value(payload, ("trainingStressScore",))), 1),
            "metadata_confidence": _activity_metadata_context(activity_id, device_rows, gear_rows),
        }
        rows.append(entry)
        id_to_ref[activity_id] = activity_ref
    return rows, id_to_ref


def _detail_context(root: str | Path | None, id_to_ref: dict[str, str]) -> list[dict]:
    rows = []
    for activity_id, activity_ref in id_to_ref.items():
        preserved_path = activities_dir(root) / "details" / f"garmin_{activity_id}_detail.json"
        legacy_path = snapshots_dir(root) / f"activity_detail_{activity_id}.json"
        detail = read_json(preserved_path, {})
        source = f"activities/details/garmin_{activity_id}_detail.json"
        if not isinstance(detail, dict) or not detail:
            detail = read_json(legacy_path, {})
            source = f"snapshots/activity_detail_{activity_id}.json"
        if not isinstance(detail, dict) or not detail:
            continue
        calls = detail.get("calls") or {}
        details_call = calls.get("details") or {}
        details_data = details_call.get("data") or {}
        weather_call = calls.get("weather") or {}
        weather = weather_call.get("data") or {}
        rows.append(
            {
                "activity_ref": activity_ref,
                "source": source,
                "details_fetch_ok": bool(details_call.get("ok")),
                "metric_descriptor_count": details_data.get("measurementCount"),
                "sample_count": details_data.get("metricsCount"),
                "total_metric_values": details_data.get("totalMetricsCount"),
                "weather": {
                    "temperature_raw": weather.get("temp") if isinstance(weather, dict) else None,
                    "apparent_temperature_raw": weather.get("apparentTemp") if isinstance(weather, dict) else None,
                    "relative_humidity_pct": weather.get("relativeHumidity") if isinstance(weather, dict) else None,
                    "unit_status": "unknown_do_not_use_quantitatively",
                    "coaching_rule": "Historical activity weather is context only, not a forecast.",
                }
                if weather_call.get("ok")
                else None,
            }
        )
    return rows


def _loop_context(
    root: str | Path | None,
    target: date,
    id_to_ref: dict[str, str],
    allowed_activity_refs: set[str] | None = None,
) -> dict:
    artifacts = []
    for path in snapshots_dir(root).glob(f"activity_loop_load_{target.isoformat()}_*.json"):
        payload = read_json(path, {})
        if not isinstance(payload, dict):
            continue
        activity_id = str(payload.get("activity_id") or (payload.get("activity") or {}).get("activity_id") or "")
        activity_ref = id_to_ref.get(activity_id)
        if not activity_ref or (
            allowed_activity_refs is not None
            and activity_ref not in allowed_activity_refs
        ):
            continue
        action_seconds: dict[str, float] = defaultdict(float)
        timeline_flags = set()
        for lap in payload.get("laps") or []:
            if not isinstance(lap, dict):
                continue
            timeline = lap.get("timeline") or {}
            timeline_flags.update(str(value) for value in timeline.get("flags") or [])
            action_summary = timeline.get("action_terrain_summary") or {}
            for name, section in (action_summary.get("sections") or {}).items():
                if isinstance(section, dict):
                    action_seconds[str(name)] += _number(section.get("duration_s"))
        loops = []
        for loop in (payload.get("loops") or [])[:24]:
            if not isinstance(loop, dict):
                continue
            estimated = loop.get("estimated_load") or {}
            loops.append(
                {
                    "loop": loop.get("loop"),
                    "label": loop.get("label"),
                    "lap_kinds": loop.get("lap_kinds") or [],
                    "elapsed_min": loop.get("elapsed_min"),
                    "moving_min": loop.get("moving_min"),
                    "stop_min": loop.get("stop_min"),
                    "elevation_gain_m": loop.get("elevation_gain_m"),
                    "elevation_loss_m": loop.get("elevation_loss_m"),
                    "estimated_primary_load": estimated.get("primary_continuous_hr"),
                }
            )
        artifacts.append(
            {
                "activity_ref": activity_ref,
                "official_activity_training_load": payload.get("official_activity_training_load"),
                "loop_count": len(payload.get("loops") or []),
                "lap_count": len(payload.get("laps") or []),
                "loops": loops,
                "action_terrain_minutes": {
                    name: _round(seconds / 60.0)
                    for name, seconds in sorted(action_seconds.items())
                },
                "timeline_flags": sorted(timeline_flags),
                "method_limits": (payload.get("method") or {}).get("limits") or [],
            }
        )
    return {
        "available": bool(artifacts),
        "matched_artifacts": len(artifacts),
        "activities": artifacts,
    }


def _latest_session_evidence_context(root: str | Path | None, target: date) -> dict:
    state = read_json(snapshots_dir(root) / "current_state.json", {})
    evidence = state.get("latest_session_evidence") if isinstance(state, dict) else None
    if not isinstance(evidence, dict):
        return {"available": False, "reason": "latest_session_evidence_not_available"}
    evidence_date = None
    activity = evidence.get("activity") or {}
    date_candidates = [
        activity.get("date") if isinstance(activity, dict) else None,
        evidence.get("activity_date"),
        evidence.get("session_date"),
        evidence.get("date"),
    ]
    for value in date_candidates:
        try:
            evidence_date = parse_date(value)
        except (TypeError, ValueError):
            evidence_date = None
        if evidence_date:
            break
    if evidence_date != target:
        return {
            "available": False,
            "reason": "latest_session_evidence_date_does_not_match_review",
            "evidence_date": evidence_date.isoformat() if evidence_date else None,
        }
    return {
        "available": True,
        "source": "snapshots/current_state.json:latest_session_evidence",
        "evidence": evidence,
    }


def _reconcile_raw_timing_with_latest_session(
    raw_rows: list[dict],
    id_to_ref: dict[str, str],
    latest_session: dict,
) -> None:
    """Apply a qualified trace correction without erasing Garmin's raw timing."""
    if not latest_session.get("available"):
        return
    evidence = latest_session.get("evidence") or {}
    activity = evidence.get("activity") or {}
    timing = evidence.get("timing") or {}
    plausibility = timing.get("plausibility") or {}
    if plausibility.get("status") != "garmin_moving_duration_replaced_by_trace_estimate":
        return
    activity_ref = id_to_ref.get(str(activity.get("activity_id") or ""))
    if not activity_ref:
        return
    for row in raw_rows:
        if row.get("activity_ref") != activity_ref:
            continue
        row["garmin_reported_timing"] = {
            "moving_duration_min": row.get("moving_duration_min"),
            "implied_stopped_duration_min": row.get("stopped_duration_min"),
        }
        row["moving_duration_min"] = timing.get("moving_min")
        row["stopped_duration_min"] = None
        row["nonmoving_or_stopped_estimate_min"] = timing.get(
            "nonmoving_or_stopped_estimate_min"
        )
        row["timing_source"] = (
            "snapshots/current_state.json:latest_session_evidence.timing"
        )
        row["timing_interpretation_guardrail"] = timing.get(
            "stopped_interpretation"
        )
        break


def _cns_payload(root: str | Path | None, target: date) -> dict | None:
    dated_name = f"cns_readiness_{target.isoformat()}.json"
    payload = read_json(snapshots_dir(root) / dated_name, {})
    source = f"snapshots/{dated_name}"
    if not isinstance(payload, dict) or parse_date(payload.get("date")) != target:
        payload = read_json(snapshots_dir(root) / "cns_readiness.json", {})
        source = "snapshots/cns_readiness.json"
        if not isinstance(payload, dict) or parse_date(payload.get("date")) != target:
            return None
    return {
        "date": target.isoformat(),
        "status": payload.get("status"),
        "score": payload.get("score"),
        "confidence": payload.get("confidence"),
        "session_ceiling": payload.get("session_ceiling"),
        "interpretation": payload.get("interpretation"),
        "source": source,
    }


def _cns_outcome_context(root: str | Path | None, target: date) -> dict:
    session_day = _cns_payload(root, target)
    next_day = _cns_payload(root, target + timedelta(days=1))
    return {
        "session_day": session_day,
        "next_day": next_day,
        "available": bool(session_day or next_day),
        "decision_role": "Audit context only; CNS evidence does not change calibration eligibility automatically.",
    }


def _coaching_evidence_audit(
    root: str | Path | None,
    target: date,
    expected: dict,
    actual: dict,
    contract_quality: dict,
) -> dict:
    raw_rows, id_to_ref = _raw_activity_context(root, target, actual)
    detail_rows = _detail_context(root, id_to_ref)
    mtb_activity_refs = {
        str(row.get("activity_ref"))
        for row in raw_rows
        if row.get("category") == "mtb" and row.get("activity_ref")
    }
    loop_context = _loop_context(
        root,
        target,
        id_to_ref,
        allowed_activity_refs=mtb_activity_refs,
    )
    latest_session = _latest_session_evidence_context(root, target)
    _reconcile_raw_timing_with_latest_session(raw_rows, id_to_ref, latest_session)
    cns = _cns_outcome_context(root, target)
    fueling = contract_quality.get("fueling_adequacy") or {}
    limiters = []
    if actual.get("sessions") and len(raw_rows) < int(_number(actual.get("sessions"))):
        limiters.append("raw_activity_summary_coverage_is_partial")
    if _is_technical_session(expected):
        if any(
            (row.get("metadata_confidence") or {}).get("hr_source_confidence")
            in {"unknown_not_indexed", "unknown_fetch_failed", "no_external_hr_reported"}
            for row in raw_rows
        ):
            limiters.append("mtb_hr_source_confidence_is_limited")
        if any(
            (row.get("metadata_confidence") or {}).get("device_fetch_status")
            == "cached_after_refresh_failure"
            for row in raw_rows
        ):
            limiters.append("mtb_device_metadata_is_cached_after_refresh_failure")
        if not loop_context.get("available"):
            limiters.append("matching_loop_context_not_available")
    if fueling.get("applicable") and fueling.get("status") != "within_planned_ranges":
        limiters.append(f"fueling_audit_{fueling.get('status')}")
    if not cns.get("next_day"):
        limiters.append("next_day_cns_outcome_not_available")
    confidence = "high" if not limiters else "medium" if len(limiters) <= 2 else "limited"
    return {
        "status": "available" if raw_rows or latest_session.get("available") else "partial",
        "confidence": confidence,
        "confidence_limiters": limiters,
        "usage": "Coaching interpretation and confounder audit only; these fields are not automatic model authority.",
        "calibration_eligibility_effect": "none",
        "actual_session_evidence": {
            "raw_summary_coverage": {
                "matched": len(raw_rows),
                "expected_from_actual_index": int(_number(actual.get("sessions"))),
            },
            "activities": raw_rows,
            "activity_detail": detail_rows,
            "latest_session_evidence": latest_session,
            "loop_context": loop_context,
        },
        "fueling_adequacy": fueling,
        "cns_outcome": cns,
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


def _learning_disposition(
    quality: dict,
    adherence: str,
    response_status: str,
    response_delta: float | None,
    physiology_status: str,
    physiology_eligible: bool,
    physiology_weight: float,
    calibration_status: str,
    calibration_eligible: bool,
    calibration_weight: float,
) -> dict:
    """Separate nominal validation from observations of the delivered action."""
    quality_status = str(quality.get("status") or "not_reviewed")
    stop_rule = quality.get("stop_rule_outcome") or {}
    stop_status = str(stop_rule.get("status") or "unknown")
    learning_evidence = quality.get("execution_learning_evidence") or {}
    unsafe_continuation = stop_status == "triggered_but_continued"

    if unsafe_continuation:
        nominal = {
            "status": "rejected_unsafe_stop_rule_continued",
            "eligible": False,
            "weight": 0.0,
            "permanent_exclusion": True,
            "target": "nominal_prescription",
            "reason": (
                "The stop rule was overridden, so this session can never validate the nominal prescription, "
                "even if next-day recovery is favorable."
            ),
        }
    else:
        nominal = {
            "status": calibration_status,
            "eligible": calibration_eligible,
            "weight": calibration_weight,
            "permanent_exclusion": False,
            "target": "nominal_prescription",
            "reason": next(iter(quality.get("reasons") or []), None),
        }

    action_matched = (quality.get("action_alignment") or {}).get("status") == "matched"
    structured_boundary = bool(learning_evidence.get("structured_boundary_evidence"))
    structured_delivered_action = bool(
        learning_evidence.get("structured_delivered_action_evidence")
    )
    if unsafe_continuation:
        if response_status == "pending_next_day":
            delivered_status = "pending_next_day"
            delivered_eligible = False
            delivered_weight = 0.0
        elif response_status == "no_expected_response":
            delivered_status = "not_comparable_no_expected_response"
            delivered_eligible = False
            delivered_weight = 0.0
        elif not action_matched:
            delivered_status = "not_eligible_action_mismatch"
            delivered_eligible = False
            delivered_weight = 0.0
        elif adherence != "matched_expected_load":
            delivered_status = "not_eligible_load_changed_model_input"
            delivered_eligible = False
            delivered_weight = 0.0
        elif not structured_delivered_action:
            delivered_status = "not_eligible_insufficient_characterization"
            delivered_eligible = False
            delivered_weight = 0.0
        elif response_status == "within_expected_band":
            delivered_status = "observed_within_expected_band"
            delivered_eligible = True
            delivered_weight = OUT_OF_POLICY_DELIVERED_ACTION_WEIGHT
        else:
            delivered_status = "observed_model_miss"
            delivered_eligible = True
            delivered_weight = OUT_OF_POLICY_DELIVERED_ACTION_WEIGHT
        delivered = {
            "status": delivered_status,
            "eligible": delivered_eligible,
            "weight": delivered_weight,
            "target": "executed_action_only",
            "policy_status": "out_of_policy_stop_rule_override",
            "nominal_prescription_validation": False,
            "response_status": response_status,
            "response_delta": _round(response_delta),
            "characterization_status": (
                "structured"
                if structured_delivered_action
                else "insufficient_structured_evidence"
            ),
            "evidence_limitations": (
                (quality.get("review_field_completion") or {}).get("missing") or []
            ),
        }
    else:
        delivered = {
            "status": physiology_status,
            "eligible": physiology_eligible,
            "weight": physiology_weight,
            "target": "delivered_action",
            "policy_status": "in_policy" if quality_status == "complete" else "not_fully_validated",
            "nominal_prescription_validation": calibration_eligible,
            "response_status": response_status,
            "response_delta": _round(response_delta),
            "characterization_status": quality_status,
            "evidence_limitations": (
                (quality.get("review_field_completion") or {}).get("missing") or []
            ),
        }

    trigger = learning_evidence.get("trigger") or {}
    if stop_status in {
        "triggered_and_stopped",
        "triggered_and_downshifted",
        "triggered_but_continued",
    }:
        boundary = {
            "status": "eligible" if structured_boundary else "insufficient_structured_evidence",
            "eligible": structured_boundary,
            "weight": 1.0 if structured_boundary else 0.0,
            "target": "execution_boundary",
            "stop_rule_outcome": stop_status,
            "trigger": trigger,
            "repetition_reported_rpe_0_to_10": learning_evidence.get(
                "repetition_reported_rpe_0_to_10"
            )
            or [],
            "expected_rpe_ceiling_0_to_10": learning_evidence.get(
                "expected_rpe_ceiling_0_to_10"
            ),
            "last_within_rpe_ceiling_repetition": learning_evidence.get(
                "last_within_rpe_ceiling_repetition"
            ),
            "objective_repetitions": learning_evidence.get("objective_repetitions") or [],
            "external_work_stable": learning_evidence.get("external_work_stable"),
            "interpretation_guardrail": (
                "This lane estimates the observed execution boundary; it does not validate the nominal dose, "
                "FTP, or VO2 physiology."
            ),
        }
    else:
        boundary = {
            "status": "not_applicable",
            "eligible": False,
            "weight": 0.0,
            "target": "execution_boundary",
        }

    if unsafe_continuation:
        continuation_reason = learning_evidence.get("continuation_reason")
        safety = {
            "status": "eligible_stop_rule_override",
            "eligible": True,
            "weight": 1.0,
            "target": "stop_rule_execution_behavior",
            "event": "stop_rule_overridden",
            "continuation_reason": continuation_reason,
            "characterization_status": "complete" if continuation_reason else "partial",
            "reason_source": learning_evidence.get("continuation_reason_source"),
        }
        counterfactual = {
            "status": "unidentifiable",
            "eligible": False,
            "weight": 0.0,
            "target": "rule_compliant_nominal_response",
            "reason": (
                "No response was observed for the counterfactual action in which the athlete stopped or "
                "downshifted when the rule triggered."
            ),
        }
    else:
        safety = {
            "status": "not_applicable",
            "eligible": False,
            "weight": 0.0,
            "target": "stop_rule_execution_behavior",
        }
        counterfactual = {
            "status": "not_applicable",
            "eligible": False,
            "weight": 0.0,
            "target": "rule_compliant_nominal_response",
        }

    return {
        "schema_version": 1,
        "backward_compatible_field_mapping": {
            "physiology_calibration_status_eligible_weight": "delivered_action_response",
            "calibration_status_eligible_weight": "nominal_contract_validation",
        },
        "nominal_contract_validation": nominal,
        "delivered_action_response": delivered,
        "execution_boundary_learning": boundary,
        "safety_adherence_learning": safety,
        "counterfactual_nominal_response": counterfactual,
    }


def _compare_prediction(
    prediction: dict,
    actual: dict,
    self_eval: dict,
    response: dict,
    contract_quality: dict | None = None,
) -> dict:
    expected = prediction.get("expected_session") or {}
    load_expectation = _selected_training_load_expectation(expected)
    expected_range = load_expectation.get("expected_range") or [None, None]
    expected_load = load_expectation.get("expected_value")
    actual_load = actual.get("training_load")
    load_delta = actual_load - expected_load if actual_load is not None and expected_load is not None else None
    load_delta_pct = (load_delta / expected_load * 100) if load_delta is not None and expected_load else None
    lower, upper = expected_range if len(expected_range) == 2 else (None, None)
    allowed_optional_skip = (
        expected.get("optional") is True
        and actual.get("sessions", 0) == 0
        and expected.get("sessions", 0) > 0
    )
    if allowed_optional_skip:
        adherence = "allowed_optional_skip"
    elif actual.get("sessions", 0) == 0 and expected.get("sessions", 0) > 0:
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
        if not allowed_optional_skip
        and actual_response is not None
        and expected_response is not None
        else None
    )
    if allowed_optional_skip:
        response_status = "not_applicable_optional_skip"
    elif actual_response is None:
        response_status = "pending_next_day"
    else:
        response_status = _response_status_from_delta(response_delta)

    if allowed_optional_skip:
        physiology_calibration_status = "not_calibratable_optional_skip"
        physiology_calibration_weight = 0.0
        physiology_calibration_eligible = False
    elif response_status == "pending_next_day":
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
    quality_status = quality.get("status")
    unsafe_stop_rule_continued = quality_status == "unsafe_stop_rule_continued"
    if unsafe_stop_rule_continued and physiology_calibration_eligible:
        physiology_calibration_status = (
            "out_of_policy_observation"
            if response_status == "within_expected_band"
            else "out_of_policy_model_miss"
        )
        physiology_calibration_weight = OUT_OF_POLICY_DELIVERED_ACTION_WEIGHT

    if unsafe_stop_rule_continued:
        calibration_status = "contract_unreliable"
        calibration_weight = 0.0
        calibration_eligible = False
    elif not physiology_calibration_eligible:
        calibration_status = physiology_calibration_status
        calibration_weight = 0.0
        calibration_eligible = False
    elif quality.get("calibration_eligible"):
        calibration_status = physiology_calibration_status
        calibration_weight = physiology_calibration_weight
        calibration_eligible = True
    else:
        calibration_status = {
            "contract_missing": "contract_missing",
            "action_mismatch": "contract_action_mismatch",
            "execution_dose_stopped": "contract_dose_stopped",
            "unsafe_stop_rule_continued": "contract_unreliable",
            "technical_quality_degraded": "technical_quality_degraded",
        }.get(quality_status, "contract_incomplete")
        calibration_weight = 0.0
        calibration_eligible = False

    if unsafe_stop_rule_continued:
        interpretation = (
            "The stop rule was triggered but the session continued. This permanently rejects nominal-contract "
            "validation; retain the characterized execution boundary and, after next-day evidence arrives, the "
            "lower-weight delivered-action response observation."
        )
    elif allowed_optional_skip:
        interpretation = (
            "The optional session was not performed; this was allowed by the written plan and is not "
            "adherence drift. No delivered session-response pair exists for calibration."
        )
    elif response_status == "pending_next_day":
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

    execution_risk_stress_test = _stress_test_comparison(
        prediction,
        None if allowed_optional_skip else actual_response,
    )
    if allowed_optional_skip:
        execution_risk_stress_test["response_status"] = (
            "not_applicable_optional_skip"
        )

    learning_disposition = _learning_disposition(
        quality,
        adherence,
        response_status,
        response_delta,
        physiology_calibration_status,
        physiology_calibration_eligible,
        physiology_calibration_weight,
        calibration_status,
        calibration_eligible,
        calibration_weight,
    )

    return {
        "adherence_status": adherence,
        "training_load_expectation": load_expectation,
        "training_load_delta": _round(load_delta),
        "training_load_delta_pct": _round(load_delta_pct),
        "duration_delta_min": _round((actual.get("duration_min") or 0) - (expected.get("duration_min") or 0)),
        "execution_drift": {
            "training_load_ratio": _round(load_ratio, 2),
            "duration_ratio": _round(duration_ratio, 2),
            "actual_changed_model_input": adherence != "matched_expected_load",
            "adherence_drift": adherence not in {
                "matched_expected_load",
                "allowed_optional_skip",
            },
            "within_written_optionality": allowed_optional_skip,
        },
        "self_evaluation": self_eval,
        "expected_response_source": "coaching_adjusted"
        if adjusted_response.get("score") is not None
        else "raw_model",
        "response_status": response_status,
        "response_delta": _round(response_delta),
        "execution_risk_stress_test": execution_risk_stress_test,
        "physiology_calibration_status": physiology_calibration_status,
        "physiology_calibration_eligible": physiology_calibration_eligible,
        "physiology_calibration_weight": physiology_calibration_weight,
        "contract_quality": quality,
        "calibration_status": calibration_status,
        "calibration_eligible": calibration_eligible,
        "calibration_weight": calibration_weight,
        "learning_disposition": learning_disposition,
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
    audit = review.get("coaching_evidence_audit") or {}
    fueling = audit.get("fueling_adequacy") or {}
    cns = audit.get("cns_outcome") or {}
    next_day_cns = cns.get("next_day") or {}
    optionality = review.get("optionality_resolution") or {}
    learning = comparison.get("learning_disposition") or {}
    nominal = learning.get("nominal_contract_validation") or {}
    delivered = learning.get("delivered_action_response") or {}
    boundary = learning.get("execution_boundary_learning") or {}
    safety = learning.get("safety_adherence_learning") or {}
    return "\n".join(
        [
            f"Predictive Session Review - {review['date']}",
            "",
            f"Adherence: {comparison.get('adherence_status')}",
            f"Optionality resolution: {optionality.get('status')} (applied: {optionality.get('applied')})",
            f"Load delta: {comparison.get('training_load_delta')} ({comparison.get('training_load_delta_pct')}%)",
            f"Next-day response: {response.get('score')} / {response.get('readiness_level')} / {comparison.get('response_status')}",
            f"Nominal contract validation: {nominal.get('status')} (eligible: {nominal.get('eligible')}, weight: {nominal.get('weight')})",
            f"Delivered-action response: {delivered.get('status')} ({delivered.get('policy_status')}; eligible: {delivered.get('eligible')}, weight: {delivered.get('weight')})",
            f"Execution-boundary learning: {boundary.get('status')}; safety learning: {safety.get('status')}",
            f"Legacy physiology/delivered-action field: {comparison.get('physiology_calibration_status')} (eligible: {comparison.get('physiology_calibration_eligible')})",
            f"Contract quality: {quality.get('status')} (review fields: {len(review_fields.get('completed') or [])}/{len(review_fields.get('required') or [])})",
            f"Stop-rule outcome: {stop_rule.get('status')}; technical quality: {technical.get('status')}",
            f"Fueling audit: {fueling.get('status')}; next-day CNS: {next_day_cns.get('status')}",
            f"Coaching evidence confidence: {audit.get('confidence')} (calibration effect: {audit.get('calibration_eligibility_effect')})",
            f"Legacy full/nominal calibration field: {comparison.get('calibration_status')} (eligible: {comparison.get('calibration_eligible')})",
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
    if not isinstance(plan.get("nutrition"), dict):
        from .context import load_context

        context = load_context(root)
        nutrition_context = {
            **context,
            "athlete": {
                **context.get("athlete", {}),
                **state.get("athlete", {}),
            },
        }
        session = plan.get("session") or {}
        plan = {
            **plan,
            "nutrition": _nutrition_block(
                nutrition_context,
                str(session.get("intensity") or "easy"),
                int(session.get("duration_min") or 0),
                session=session,
                state=state,
            ),
        }
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
    dated_exists = dated_path.exists()
    existing_dated: dict = {}
    dated_integrity = "missing"
    if dated_exists:
        try:
            raw_existing_dated = read_json(dated_path, None)
        except (OSError, UnicodeError, ValueError):
            raw_existing_dated = None
            dated_integrity = "corrupt"
        else:
            if (
                isinstance(raw_existing_dated, dict)
                and raw_existing_dated.get("date") == target.isoformat()
                and isinstance(raw_existing_dated.get("prediction"), dict)
            ):
                existing_dated = raw_existing_dated
                dated_integrity = "valid"
            else:
                dated_integrity = "invalid_or_empty"
    completed_same_day_activity = (
        target == today_local(DEFAULT_TIMEZONE) and bool(_activity_rows_for_date(root, target))
    )
    dated_write_status = "written"
    if dated_exists:
        if completed_same_day_activity:
            dated_write_status = "preserved_existing_after_activity"
        elif dated_integrity == "valid":
            dated_write_status = "preserved_existing_immutable"
        else:
            dated_write_status = f"preserved_existing_{dated_integrity}"
    elif completed_same_day_activity:
        dated_write_status = "skipped_after_activity"
    artifact["artifacts"]["dated_write_status"] = dated_write_status
    artifact["artifacts"]["existing_dated_integrity"] = dated_integrity
    if existing_dated and dated_write_status.startswith("preserved_existing"):
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
    prediction = (prescription or {}).get("prediction") or {}
    actual = _actual_activity_summary(root, target)
    self_eval = _self_evaluation_for_date(root, target)
    response = _actual_next_day_response(root, target)
    stored_expected = prediction.get("expected_session") or {}
    expected, optionality_resolution = _review_time_optionality_resolution(
        root,
        target,
        prescription or {},
        stored_expected,
    )
    review_prediction = (
        {**prediction, "expected_session": expected}
        if expected is not stored_expected
        else prediction
    )
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
        _compare_prediction(review_prediction, actual, self_eval, response, contract_quality)
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
    if not prediction:
        comparison["learning_disposition"] = _learning_disposition(
            contract_quality,
            "no_stored_prescription",
            "not_reviewed",
            None,
            "not_calibratable",
            False,
            0.0,
            "not_calibratable",
            False,
            0.0,
        )
    coaching_evidence_audit = _coaching_evidence_audit(
        root,
        target,
        expected,
        actual,
        contract_quality,
    )
    comparison["coaching_evidence_confidence"] = {
        "status": coaching_evidence_audit.get("confidence"),
        "limiters": coaching_evidence_audit.get("confidence_limiters") or [],
        "calibration_eligibility_effect": "none",
    }
    privacy_safe_expected, redacted_identifier_paths = _privacy_safe_prediction(
        review_prediction
    )
    review = {
        "date": target.isoformat(),
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "artifact_type": "predictive_session_review",
        "prescription_available": bool(prescription),
        "prescription_source_date": (prescription or {}).get("date"),
        "expected": privacy_safe_expected,
        "optionality_resolution": optionality_resolution,
        "actual_activity": actual,
        "actual_next_day_response": response,
        "comparison": comparison,
        "coaching_evidence_audit": coaching_evidence_audit,
        "privacy": {
            "stored_prediction_mutated": False,
            "review_surface_identifier_redaction_applied": bool(
                redacted_identifier_paths
            ),
            "redacted_identifier_field_count": len(redacted_identifier_paths),
            "redacted_identifier_paths": redacted_identifier_paths,
        },
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
