from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from .io import read_json, write_json, write_text
from .paths import snapshots_dir
from .planning import build_today_plan
from .state import build_current_state
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local


def _value(value: Any, fallback: str = "unknown") -> Any:
    return fallback if value is None else value


def _signal(
    name: str,
    status: str,
    value: Any,
    decision_use: str,
    message: str,
) -> dict:
    return {
        "name": name,
        "status": status,
        "value": value,
        "decision_use": decision_use,
        "message": message,
    }


def _compact_modalities(windows: dict) -> dict:
    compact = {}
    for window_name in ("last_7_days", "last_28_days"):
        window = windows.get(window_name) or {}
        compact[window_name] = {
            modality: {
                "sessions": row.get("sessions"),
                "duration_min": row.get("duration_min"),
                "training_load": row.get("training_load"),
            }
            for modality, row in sorted(window.items())
        }
    return compact


def _coach_confidence(state: dict) -> str:
    readiness = state.get("readiness", {})
    freshness = state.get("data_freshness", {})
    if readiness.get("readiness_level") == "red":
        return "high_for_downshift"
    if freshness.get("status") != "current":
        return "limited"
    if freshness.get("activity_data", {}).get("status") not in {"current", None}:
        return "limited"
    return readiness.get("confidence") or "medium"


def _training_predictor_use(training_predictor: dict) -> tuple[str, str]:
    validation = training_predictor.get("validation") or {}
    utility = validation.get("utility")
    status = validation.get("status")
    if utility == "useful":
        return "supporting_signal", "Validation beats the majority baseline enough to support coaching judgment."
    if status == "insufficient_samples":
        return "ignored_until_validated", "Insufficient samples for a useful holdout validation."
    return (
        "experimental_caution_only",
        "Validation does not beat a simple baseline, so this cannot steer the plan.",
    )


def _body_battery_model_use(model: dict) -> tuple[str, str]:
    samples = model.get("samples") or 0
    accuracy = model.get("leave_one_out_accuracy")
    if samples >= 30 and accuracy is not None and accuracy >= 0.7:
        return "interpretable_support", "Enough recent samples to explain likely wake Body Battery patterns."
    return "exploratory_only", "Useful for inspection, but not enough validated signal to steer training."


def _build_trusted_evidence(state: dict, plan: dict, root: str | Path | None = None) -> list[dict]:
    freshness = state.get("data_freshness") or {}
    activity_freshness = freshness.get("activity_data") or {}
    readiness = state.get("readiness") or {}
    phase = state.get("phase") or {}
    training_status = state.get("training_status_current") or {}
    rollups = state.get("modality_load_rollups") or {}
    baselines = state.get("historical_baselines") or {}
    gear_audit = state.get("gear_audit") or {}
    device_audit = state.get("device_audit") or {}
    self_evaluation = state.get("self_evaluation") or {}
    predictive = read_json(snapshots_dir(root) / "predictive_training.json", {})
    scheduled_rest = (plan.get("decision_inputs") or {}).get("scheduled_rest")

    trusted = []
    if scheduled_rest:
        trusted.append(
            _signal(
                "Scheduled rest",
                scheduled_rest.get("status") or "active",
                scheduled_rest,
                "hard_daily_constraint",
                scheduled_rest.get("reason") or "Scheduled rest day is active.",
            )
        )

    trusted.extend([
        _signal(
            "Garmin wellness freshness",
            freshness.get("status") or "unknown",
            {
                "latest_wellness_date": freshness.get("latest_wellness_date"),
                "age_days": freshness.get("age_days"),
            },
            "freshness_gate",
            freshness.get("message") or "No Garmin wellness freshness message is available.",
        ),
        _signal(
            "Garmin activity freshness",
            activity_freshness.get("status") or "unknown",
            {
                "latest_activity_date": activity_freshness.get("latest_activity_date"),
                "age_days": activity_freshness.get("age_days"),
            },
            "load_confidence_gate",
            activity_freshness.get("message") or "No Garmin activity freshness message is available.",
        ),
        _signal(
            "Readiness",
            readiness.get("readiness_level") or "unknown",
            {
                "score": readiness.get("readiness_score"),
                "confidence": readiness.get("confidence"),
                "hard_session_guidance": readiness.get("hard_session_guidance"),
            },
            "daily_intensity_ceiling",
            "Use readiness to cap session ambition before adding MTB specificity.",
        ),
        _signal(
            "Current phase",
            phase.get("name") or "unknown",
            {
                "reason": phase.get("reason"),
            },
            "progression_stage",
            "Use the current phase to scale bike specificity, intensity, and durability work.",
        ),
        _signal(
            "Training status",
            training_status.get("training_status_feedback") or "unknown",
            {
                "acwr": (training_status.get("acute_chronic") or {}).get("ratio"),
                "acwr_status": (training_status.get("acute_chronic") or {}).get("status"),
                "load_focus": (training_status.get("load_focus") or {}).get("feedback"),
                "cycling_vo2max": (training_status.get("vo2max") or {}).get("cycling_precise"),
            },
            "load_context_not_daily_command",
            "Use Garmin training status as context, not as an automatic workout prescription.",
        ),
        _signal(
            "Modality load rollups",
            "available" if rollups.get("windows") else "missing",
            _compact_modalities(rollups.get("windows") or {}),
            "specificity_context",
            "Recent load distribution shows what the current fitness is actually built from.",
        ),
        _signal(
            "Historical MTB baseline",
            "available" if baselines.get("pre_injury_mtb_baseline") else "missing",
            baselines.get("pre_injury_mtb_baseline"),
            "long_range_target_context",
            "Historical MTB volume is useful for direction, not an immediate target.",
        ),
        _signal(
            "Activity gear audit",
            "flagged" if gear_audit.get("flags") else "clear",
            {
                "checked_activities": gear_audit.get("checked_activities"),
                "mtb_checked": gear_audit.get("mtb_checked"),
                "recent_mtb_gear": gear_audit.get("recent_mtb_gear"),
            },
            "power_source_and_bike_context",
            "Use Garmin Gear to distinguish Stumpjumper, Enduro, hardtail, and trainer-related activity context.",
        ),
        _signal(
            "Activity device audit",
            "flagged" if device_audit.get("flags") else "clear",
            {
                "checked_activities": device_audit.get("checked_activities"),
                "mtb_checked": device_audit.get("mtb_checked"),
                "recent_mtb_devices": device_audit.get("recent_mtb_devices"),
            },
            "heart_rate_source_confidence",
            "Use Devices & Apps to distinguish chest-strap HR from wrist optical HR before trusting trail HR/load.",
        ),
        _signal(
            "Post-activity self evaluation",
            "available" if self_evaluation.get("evaluated_activities") else "missing",
            {
                "checked_activities": self_evaluation.get("checked_activities"),
                "evaluated_activities": self_evaluation.get("evaluated_activities"),
                "recent_self_evaluations": self_evaluation.get("recent_self_evaluations"),
            },
            "subjective_session_response",
            "Use Garmin self-evaluation feel and RPE to interpret whether load was costly, sustainable, or misleading.",
        ),
        _signal(
            "Predictive training twin",
            (
                predictive.get("today_prescription", {})
                .get("model_confidence", {})
                .get("status")
                or "missing"
            ),
            {
                "today_prediction": (
                    predictive.get("today_prescription", {}).get("prediction") if predictive else None
                ),
                "latest_review": (
                    predictive.get("latest_review", {}).get("comparison") if predictive else None
                ),
            },
            "pre_session_expectation_and_post_session_calibration",
            "Store an expected session response before training, then compare the actual session and next-day Garmin response afterward.",
        ),
    ])
    return trusted


def _build_cautions(state: dict) -> list[dict]:
    cautions = []
    readiness = state.get("readiness") or {}
    for reason in readiness.get("reasons") or []:
        cautions.append(
            {
                "source": "readiness",
                "type": reason.get("type"),
                "severity": reason.get("severity"),
                "message": reason.get("message"),
            }
        )
    for limiter in (state.get("data_freshness") or {}).get("hard_session_limiters") or []:
        cautions.append(
            {
                "source": "data_freshness",
                "type": "hard_session_limiter",
                "severity": "yellow",
                "message": limiter,
            }
        )
    for flag in (state.get("training_status_current") or {}).get("flags") or []:
        cautions.append(
            {
                "source": "training_status",
                "type": flag.get("type"),
                "severity": "yellow",
                "message": flag.get("message"),
            }
        )
    for flag in (state.get("gear_audit") or {}).get("flags") or []:
        cautions.append(
            {
                "source": "gear_audit",
                "type": flag.get("type"),
                "severity": flag.get("severity") or "yellow",
                "message": flag.get("message"),
            }
        )
    for flag in (state.get("device_audit") or {}).get("flags") or []:
        cautions.append(
            {
                "source": "device_audit",
                "type": flag.get("type"),
                "severity": flag.get("severity") or "yellow",
                "message": flag.get("message"),
            }
        )
    return cautions


def _build_experimental_evidence(state: dict) -> tuple[list[dict], list[dict]]:
    experimental = []
    ignored = []

    body_model = state.get("body_battery_model") or {}
    body_use, body_message = _body_battery_model_use(body_model)
    latest = body_model.get("latest_prediction") or {}
    experimental.append(
        {
            "name": "Wake Body Battery decision tree",
            "decision_use": body_use,
            "samples": body_model.get("samples"),
            "validation": {
                "leave_one_out_accuracy": body_model.get("leave_one_out_accuracy"),
            },
            "latest_prediction": {
                "path": latest.get("path"),
                "prob_good_wake_body_battery": (latest.get("leaf") or {}).get(
                    "prob_good_wake_body_battery"
                ),
                "leaf_samples": (latest.get("leaf") or {}).get("samples"),
            },
            "message": body_message,
        }
    )
    if body_use == "exploratory_only":
        ignored.append(
            {
                "name": "Wake Body Battery decision tree",
                "reason": "Not enough validated signal yet for training prescription.",
            }
        )

    predictor = state.get("training_predictor") or {}
    predictor_use, predictor_message = _training_predictor_use(predictor)
    prediction = (predictor.get("today_prediction") or {}).get("prediction") or {}
    validation = predictor.get("validation") or {}
    experimental.append(
        {
            "name": "Next-day training response tree",
            "decision_use": predictor_use,
            "samples": predictor.get("samples"),
            "validation": {
                "utility": validation.get("utility"),
                "status": validation.get("status"),
                "accuracy": validation.get("accuracy"),
                "baseline_majority_accuracy": validation.get("baseline_majority_accuracy"),
                "accuracy_lift_vs_baseline": validation.get("accuracy_lift_vs_baseline"),
            },
            "latest_prediction": {
                "predicted_next_day_readiness_level": prediction.get(
                    "predicted_next_day_readiness_level"
                ),
                "expected_next_day_response_score": prediction.get(
                    "expected_next_day_response_score"
                ),
                "prob_next_day_ready": prediction.get("prob_next_day_ready"),
                "leaf_samples": prediction.get("leaf_samples"),
            },
            "message": predictor_message,
        }
    )
    if predictor_use != "supporting_signal":
        ignored.append(
            {
                "name": "Next-day training response tree",
                "reason": predictor_message,
            }
        )

    return experimental, ignored


def _today_decision(state: dict, plan: dict, cautions: list[dict]) -> dict:
    session = plan.get("session") or {}
    phase = (state.get("phase") or {}).get("name")
    readiness = state.get("readiness") or {}
    confidence = _coach_confidence(state)
    if session.get("type") == "scheduled_rest":
        stance = "sabbath_rest"
    elif readiness.get("readiness_level") == "red":
        stance = "downshift"
    elif session.get("intensity") == "hard":
        stance = "quality_allowed"
    else:
        stance = "aerobic_continuity"
    return {
        "stance": stance,
        "coach_confidence": confidence,
        "session": session,
        "gym": plan.get("gym"),
        "nutrition": plan.get("nutrition"),
        "guardrails": plan.get("guardrails", []),
        "why": [
            f"Readiness is {_value(readiness.get('readiness_level'))} at {_value(readiness.get('readiness_score'))}/100.",
            f"Phase is {_value(phase)}.",
            f"Planned session is {session.get('title', 'unknown session')} at {session.get('intensity', 'unknown')} intensity.",
            f"{len(cautions)} caution item(s) are active.",
        ],
    }


def _next_data_needed(state: dict) -> list[str]:
    needed = [
        "Keep live Garmin wellness and activity sync current before hard-session decisions.",
        "Label what the Fenix cannot see: ride purpose, trail condition, confidence, braking comfort, skill quality, fueling, and heat feel.",
    ]
    if not (state.get("body_battery_model") or {}).get("samples"):
        needed.append("Collect more modern wellness rows before trusting Body Battery modeling.")
    if (state.get("training_predictor") or {}).get("validation", {}).get("utility") != "useful":
        needed.append("Treat the training response model as experimental until validation beats a simple baseline.")
    if not (state.get("gear_audit") or {}).get("checked_activities"):
        needed.append("Sync activity Gear metadata so bike source and trainer/source mismatches can be audited.")
    if not (state.get("device_audit") or {}).get("checked_activities"):
        needed.append("Sync activity Devices & Apps metadata so HR source confidence can be audited.")
    if not (state.get("self_evaluation") or {}).get("evaluated_activities"):
        needed.append("Log Garmin post-activity self evaluation after key rides so RPE and feel can calibrate load.")
    return needed


def _same_date_artifact(root: str | Path | None, filename: str, target: date) -> dict | None:
    payload = read_json(snapshots_dir(root) / filename, {})
    if isinstance(payload, dict) and parse_date(payload.get("date")) == target:
        return payload
    return None


def _packet_text(packet: dict) -> str:
    def bullets(items: list[str]) -> str:
        return "\n".join(f"- {item}" for item in items) if items else "- None"

    trusted = [
        f"{item['name']}: {item['status']} - {item['message']}"
        for item in packet["evidence"]["trusted"]
    ]
    cautions = [
        f"{item.get('source')}/{item.get('type')}: {item.get('message')}"
        for item in packet["evidence"]["cautions"]
    ]
    experimental = [
        f"{item['name']}: {item['decision_use']} - {item['message']}"
        for item in packet["evidence"]["experimental"]
    ]
    ignored = [
        f"{item['name']}: {item['reason']}"
        for item in packet["evidence"]["ignored_for_decision"]
    ]
    today = packet["today_call"]
    return "\n".join(
        [
            f"Coach Packet - {packet['date']}",
            "",
            f"Stack path: {packet['stack_path']['chosen_path']}",
            packet["stack_path"]["why"],
            "",
            f"Today: {today['session'].get('title')} ({today['stance']}, confidence {today['coach_confidence']})",
            bullets(today.get("why") or []),
            "",
            "Trusted evidence:",
            bullets(trusted),
            "",
            "Cautions:",
            bullets(cautions),
            "",
            "Experimental evidence:",
            bullets(experimental),
            "",
            "Ignored for today's decision:",
            bullets(ignored),
            "",
            "Next data needed:",
            bullets(packet.get("next_data_needed") or []),
            "",
        ]
    )


def build_coach_packet(
    root: str | Path | None = None,
    for_date: str | date | None = None,
    state: dict | None = None,
    plan: dict | None = None,
) -> dict:
    target = parse_date(for_date) or parse_date((state or {}).get("date")) or today_local(DEFAULT_TIMEZONE)
    if state is None:
        state = _same_date_artifact(root, "current_state.json", target) or build_current_state(root, target)
    if plan is None:
        plan = _same_date_artifact(root, "today_plan.json", target) or build_today_plan(
            root, target, state=state
        )

    cautions = _build_cautions(state)
    experimental, ignored = _build_experimental_evidence(state)
    packet = {
        "date": target.isoformat(),
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "artifact_type": "coach_decision_packet",
        "stack_path": {
            "chosen_path": "evidence_triage_over_more_models",
            "why": "More artifacts are only useful when they improve the coaching call; this packet promotes current, validated, inspectable evidence and demotes weak model output.",
            "promotion_rule": "A model can influence training only when its validation beats a simple baseline and its target matches the coaching decision.",
        },
        "today_call": _today_decision(state, plan, cautions),
        "evidence": {
            "trusted": _build_trusted_evidence(state, plan, root),
            "cautions": cautions,
            "experimental": experimental,
            "ignored_for_decision": ignored,
        },
        "next_data_needed": _next_data_needed(state),
        "artifacts": {
            "json": "snapshots/coach_packet.json",
            "text": "snapshots/coach_packet.txt",
            "source_state": "snapshots/current_state.json",
            "source_plan": "snapshots/today_plan.json",
        },
    }
    write_json(snapshots_dir(root) / "coach_packet.json", packet)
    write_text(snapshots_dir(root) / "coach_packet.txt", _packet_text(packet))
    return packet
