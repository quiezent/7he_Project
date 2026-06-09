from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from .evidence import as_number
from .io import read_json, write_json, write_text
from .load_model import build_activity_summary_index
from .paths import input_dir, repo_root, snapshots_dir
from .planning import build_today_plan
from .predictive_training import _session_expectation
from .state import build_current_state
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local


BRANCH_IDS = {
    "follow": "follow_intended_action",
    "mission_creep": "mission_creep_or_extension",
    "substitute": "substitute_or_change_modality",
    "defer": "defer_or_rest",
}


def _round(value: float | None, digits: int = 1) -> float | None:
    return round(value, digits) if value is not None else None


def _number(value: Any, default: float = 0.0) -> float:
    parsed = as_number(value)
    return parsed if parsed is not None else default


def _normalize_probabilities(branches: list[dict]) -> list[dict]:
    total = sum(float(branch.get("probability") or 0.0) for branch in branches)
    if total <= 0:
        return branches
    normalized = []
    for branch in branches:
        item = dict(branch)
        item["probability"] = round(float(item.get("probability") or 0.0) / total, 2)
        normalized.append(item)
    return normalized


def _planned_session_override(root: str | Path | None, target: date) -> dict | None:
    path = input_dir(root) / f"planned_session_{target.isoformat()}.json"
    payload = read_json(path, None)
    if not isinstance(payload, dict):
        return None
    session = payload.get("session") if isinstance(payload.get("session"), dict) else payload
    return {
        "source": f"input/planned_session_{target.isoformat()}.json",
        "session": dict(session),
        "raw": payload,
    }


def _predictive_session(root: str | Path | None, target: date) -> dict | None:
    for path in (
        snapshots_dir(root) / f"predictive_session_{target.isoformat()}.json",
        snapshots_dir(root) / "predictive_session_plan.json",
    ):
        payload = read_json(path, None)
        if not isinstance(payload, dict) or payload.get("date") != target.isoformat():
            continue
        expected = ((payload.get("prediction") or {}).get("expected_session") or {})
        if expected:
            try:
                source = path.relative_to(repo_root(root)).as_posix()
            except ValueError:
                source = str(path)
            return {"source": source, "session": expected, "raw": payload}
    return None


def _cached_activity_summary_index(root: str | Path | None, target: date) -> tuple[list[dict], str, str | None]:
    rows = read_json(snapshots_dir(root) / "activity_summary_index.json", None)
    if isinstance(rows, list):
        latest = max((parse_date(row.get("date")) for row in rows), default=None)
        if latest is None or latest >= target or target >= today_local(DEFAULT_TIMEZONE):
            return rows, "snapshots/activity_summary_index.json", latest.isoformat() if latest else None
    rebuilt = build_activity_summary_index(root, target)
    latest = max((parse_date(row.get("date")) for row in rebuilt), default=None)
    return rebuilt, "rebuilt_activity_summary_index", latest.isoformat() if latest else None


def _intended_action(
    root: str | Path | None,
    target: date,
    plan: dict,
    planned_session: dict | None = None,
) -> dict:
    if planned_session:
        session = planned_session.get("session") if isinstance(planned_session.get("session"), dict) else planned_session
        source = planned_session.get("source") or "call_argument"
        source_type = "manual_override"
    else:
        override = _planned_session_override(root, target)
        if override:
            session = override["session"]
            source = override["source"]
            source_type = "planned_session_override"
        else:
            predictive = _predictive_session(root, target)
            if predictive:
                session = predictive["session"]
                source = predictive["source"]
                source_type = "predictive_session"
            else:
                session = plan.get("session") or {}
                source = "snapshots/today_plan.json"
                source_type = "today_plan"
    plan_payload = {"session": session}
    expectation = _session_expectation(plan_payload)
    return {
        "source": source,
        "source_type": source_type,
        "session": session,
        "expected_session": expectation,
    }


def _session_family(session_type: str) -> str:
    if session_type in {"outdoor_mtb", "outdoor_bike_optional", "endurance_skills"}:
        return "outdoor_mtb_or_skill"
    if session_type == "bike_quality" or "bike" in session_type or "cycling" in session_type:
        return "structured_bike"
    if "gym" in session_type:
        return "gym"
    if session_type == "scheduled_rest":
        return "rest"
    return "other"


def _has_hard_constraint(session: dict) -> bool:
    text_parts = []
    for key in ("details", "execution_rules", "stop_rules"):
        value = session.get(key)
        if isinstance(value, list):
            text_parts.extend(str(item).lower() for item in value)
    dose = session.get("dose") or {}
    if isinstance(dose, dict):
        text_parts.extend(str(value).lower() for value in dose.values())
    joined = " ".join(text_parts)
    return any(token in joined for token in ("cap", "lap", "no extra", "stop", "same trail", "one skill", "one variable"))


def _readiness_level(state: dict) -> str:
    return ((state.get("readiness") or {}).get("readiness_level") or "unknown").lower()


def _cached_current_state(root: str | Path | None, target: date) -> dict | None:
    payload = read_json(snapshots_dir(root) / "current_state.json", None)
    if isinstance(payload, dict) and payload.get("date") == target.isoformat():
        return payload
    return None


def _cached_today_plan(root: str | Path | None, target: date) -> dict | None:
    payload = read_json(snapshots_dir(root) / "today_plan.json", None)
    if isinstance(payload, dict) and payload.get("date") == target.isoformat():
        return payload
    return None


def _action_branches(intended: dict, state: dict) -> list[dict]:
    expected = intended["expected_session"]
    session = intended["session"]
    session_type = str(expected.get("type") or session.get("type") or "")
    family = _session_family(session_type)
    constrained = _has_hard_constraint(session) or _has_hard_constraint(expected)
    readiness = _readiness_level(state)
    source_type = intended.get("source_type")

    follow = 0.55
    mission = 0.2
    substitute = 0.12
    defer = 0.13
    drivers = []

    if family == "structured_bike":
        follow += 0.2
        mission -= 0.08
        drivers.append("structured_bike_sessions_have_higher_execution_control")
    elif family == "outdoor_mtb_or_skill":
        follow -= 0.2
        mission += 0.25
        drivers.append("outdoor_mtb_plans_have_higher_mission_creep_and_route_drift")
    elif family == "rest":
        follow += 0.25
        mission -= 0.1
        defer += 0.1
        drivers.append("scheduled_rest_has_a_simple_action")

    if constrained:
        follow += 0.12
        mission -= 0.1
        drivers.append("hard_constraints_reduce_action_drift")
    else:
        mission += 0.08
        drivers.append("loose_session_language_increases_action_drift")

    if readiness == "red":
        defer += 0.2
        follow -= 0.08
        mission -= 0.08
        drivers.append("red_readiness_increases_defer_or_downshift_probability")
    elif readiness == "yellow":
        defer += 0.06
        mission -= 0.03
        drivers.append("yellow_readiness_adds_intraday_caution")

    if source_type == "today_plan":
        substitute += 0.08
        mission += 0.05
        follow -= 0.08
        drivers.append("generic_today_plan_is_less_reliable_than_a_specific_coach_athlete_prescription")
    elif source_type in {"planned_session_override", "manual_override"}:
        follow += 0.08
        substitute -= 0.04
        drivers.append("dated_specific_prescription_improves_action_clarity")

    follow = max(0.05, follow)
    mission = max(0.03, mission)
    substitute = max(0.03, substitute)
    defer = max(0.03, defer)

    expected_load = _number(expected.get("expected_training_load"))
    expected_duration = _number(expected.get("duration_min"))
    mission_multiplier = 1.6 if family == "structured_bike" else 3.5 if family == "outdoor_mtb_or_skill" else 1.8
    mission_duration_multiplier = 1.25 if family == "structured_bike" else 2.5 if family == "outdoor_mtb_or_skill" else 1.3

    branches = [
        {
            "branch_id": BRANCH_IDS["follow"],
            "probability": follow,
            "action": "execute_the_prescription_as_written",
            "expected_duration_min": _round(expected_duration),
            "expected_training_load": _round(expected_load),
            "drivers": list(drivers),
        },
        {
            "branch_id": BRANCH_IDS["mission_creep"],
            "probability": mission,
            "action": "extend_duration_intensity_technical_consequence_or_setup_scope",
            "expected_duration_min": _round(max(expected_duration, expected_duration * mission_duration_multiplier)),
            "expected_training_load": _round(max(expected_load, expected_load * mission_multiplier)),
            "drivers": list(drivers) + ["Clayton_tends_to_ask_whether_clean_low_cost_work_is_too_little"],
        },
        {
            "branch_id": BRANCH_IDS["substitute"],
            "probability": substitute,
            "action": "change_modality_or_session_shape_due_to_logistics_or_preference",
            "expected_duration_min": _round(expected_duration),
            "expected_training_load": _round(expected_load * 1.1),
            "drivers": list(drivers),
        },
        {
            "branch_id": BRANCH_IDS["defer"],
            "probability": defer,
            "action": "defer_downshift_or_rest",
            "expected_duration_min": 0.0,
            "expected_training_load": 0.0,
            "drivers": list(drivers),
        },
    ]
    return _normalize_probabilities(branches)


def _state_level(score: float) -> str:
    if score < 45:
        return "red"
    if score < 70:
        return "yellow"
    return "green"


def _state_prediction_for_branch(branch: dict, state: dict, intended: dict) -> dict:
    readiness = state.get("readiness") or {}
    baseline = _number(readiness.get("readiness_score"), 60.0)
    load = _number(branch.get("expected_training_load"))
    duration = _number(branch.get("expected_duration_min"))
    session_type = str((intended.get("expected_session") or {}).get("type") or "")
    family = _session_family(session_type)

    adjustment = 0.0
    drivers = []
    if branch["branch_id"] == BRANCH_IDS["defer"]:
        adjustment += 4
        drivers.append("defer_or_rest_reduces_training_cost")
    elif load < 50:
        adjustment += 2
        drivers.append("low_load_continuity_cost")
    elif load >= 180:
        adjustment -= 18
        drivers.append("very_high_load_state_cost")
    elif load >= 100:
        adjustment -= 9
        drivers.append("meaningful_load_state_cost")
    else:
        adjustment -= 3
        drivers.append("moderate_load_state_cost")

    if family == "outdoor_mtb_or_skill" and duration >= 120:
        adjustment -= 6
        drivers.append("outdoor_mtb_duration_adds_technical_and_upper_body_cost")
    if branch["branch_id"] == BRANCH_IDS["mission_creep"]:
        adjustment -= 5
        drivers.append("mission_creep_reduces_next_day_optionality")

    predicted_score = max(0.0, min(100.0, baseline + adjustment))
    return {
        "branch_id": branch["branch_id"],
        "predicted_readiness_score": _round(predicted_score),
        "predicted_readiness_level": _state_level(predicted_score),
        "expected_training_load": _round(load),
        "expected_duration_min": _round(duration),
        "risk_to_next_key_session": "high" if predicted_score < 50 or load >= 180 else "moderate" if load >= 90 else "low",
        "confidence": "heuristic",
        "drivers": drivers,
    }


def _actual_activity(root: str | Path | None, target: date) -> dict:
    rows = []
    activity_rows, data_source, latest_date = _cached_activity_summary_index(root, target)
    for row in activity_rows:
        row_date = parse_date(row.get("date"))
        if row_date == target and row.get("counts_for_training_load"):
            rows.append(row)
    categories: dict[str, int] = {}
    duration = 0.0
    load = 0.0
    for row in rows:
        category = str(row.get("category") or "other")
        categories[category] = categories.get(category, 0) + 1
        duration += _number(row.get("duration_min"))
        load += _number(row.get("training_load"))
    return {
        "sessions": len(rows),
        "duration_min": _round(duration),
        "training_load": _round(load),
        "categories": dict(sorted(categories.items())),
        "activities": rows,
        "data_source": data_source,
        "activity_index_latest_date": latest_date,
    }


def _review_actual_action(root: str | Path | None, target: date, intended: dict, branches: list[dict]) -> dict:
    actual = _actual_activity(root, target)
    expected = intended.get("expected_session") or {}
    expected_load = _number(expected.get("expected_training_load"))
    expected_duration = _number(expected.get("duration_min"))
    actual_load = _number(actual.get("training_load"))
    actual_duration = _number(actual.get("duration_min"))
    if actual.get("sessions", 0) == 0 and expected.get("sessions", 0) > 0:
        outcome = "missed_or_deferred"
    elif expected_load and actual_load > expected_load * 1.35:
        outcome = "harder_than_intended"
    elif expected_load and actual_load < expected_load * 0.7:
        outcome = "easier_than_intended"
    else:
        outcome = "matched_intended_load"
    best_branch = None
    best_error = None
    for branch in branches:
        error = abs(_number(branch.get("expected_training_load")) - actual_load)
        if best_error is None or error < best_error:
            best_error = error
            best_branch = branch
    return {
        "status": "available" if actual.get("sessions") else "pending_or_no_activity",
        "actual": actual,
        "outcome": outcome,
        "load_delta": _round(actual_load - expected_load) if expected_load or actual_load else None,
        "duration_delta_min": _round(actual_duration - expected_duration) if expected_duration or actual_duration else None,
        "closest_predicted_branch_id": best_branch.get("branch_id") if best_branch else None,
        "closest_branch_load_error": _round(best_error) if best_error is not None else None,
        "calibration_note": (
            "Action prediction can be calibrated; state prediction still needs next-day wellness."
            if actual.get("sessions")
            else "No actual action is available yet."
        ),
    }


def _text_report(artifact: dict) -> str:
    intended = artifact["intended_action"]
    likely = artifact["action_prediction"]["most_likely_branch"]
    lines = [
        f"Action-State Prediction - {artifact['date']}",
        "",
        f"Intended action: {intended['expected_session'].get('title')} ({intended.get('source_type')})",
        f"Likely action: {likely.get('branch_id')} ({likely.get('probability')})",
        "",
        "Action Branches:",
    ]
    for branch in artifact["action_prediction"]["branches"]:
        lines.append(
            f"- {branch['branch_id']}: p={branch['probability']}, load={branch.get('expected_training_load')}, "
            f"duration={branch.get('expected_duration_min')}"
        )
    lines.extend(["", "State Predictions:"])
    for prediction in artifact["state_predictions_by_branch"]:
        lines.append(
            f"- {prediction['branch_id']}: {prediction['predicted_readiness_score']} / "
            f"{prediction['predicted_readiness_level']}, next-key risk {prediction['risk_to_next_key_session']}"
        )
    review = artifact.get("actual_action_review") or {}
    if review:
        lines.extend(["", f"Actual action review: {review.get('outcome')} ({review.get('status')})"])
    return "\n".join(lines) + "\n"


def build_action_state_prediction(
    root: str | Path | None = None,
    for_date: str | date | None = None,
    state: dict | None = None,
    plan: dict | None = None,
    planned_session: dict | None = None,
) -> dict:
    target = parse_date(for_date) or parse_date((state or {}).get("date")) or today_local(DEFAULT_TIMEZONE)
    state = state or _cached_current_state(root, target) or build_current_state(root, target)
    plan = plan or _cached_today_plan(root, target) or build_today_plan(root, target, state=state)
    intended = _intended_action(root, target, plan, planned_session=planned_session)
    branches = _action_branches(intended, state)
    likely = max(branches, key=lambda branch: branch.get("probability") or 0.0)
    state_predictions = [_state_prediction_for_branch(branch, state, intended) for branch in branches]
    review = _review_actual_action(root, target, intended, branches)
    artifact = {
        "date": target.isoformat(),
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "artifact_type": "action_state_prediction",
        "model_status": "heuristic_prototype",
        "purpose": "Predict both Clayton's likely action and the resulting athlete state branches before judging calibration.",
        "intended_action": intended,
        "action_prediction": {
            "branches": branches,
            "most_likely_branch": likely,
            "calibration_target": "actual action branch, duration, load, modality, and drift reason",
        },
        "state_predictions_by_branch": state_predictions,
        "actual_action_review": review,
        "architecture_notes": [
            "Action prediction is separate from state prediction so wrong-action assumptions do not pollute physiology calibration.",
            "Use input/planned_session_<date>.json to preserve serious chat prescriptions before training.",
            "Promote beyond heuristic only when chronological validation beats a simple branch baseline.",
        ],
        "artifacts": {
            "current": "snapshots/action_state_prediction.json",
            "dated": f"snapshots/action_state_prediction_{target.isoformat()}.json",
            "text": "snapshots/action_state_prediction.txt",
        },
    }
    write_json(snapshots_dir(root) / "action_state_prediction.json", artifact)
    write_json(snapshots_dir(root) / f"action_state_prediction_{target.isoformat()}.json", artifact)
    write_text(snapshots_dir(root) / "action_state_prediction.txt", _text_report(artifact))
    return artifact
