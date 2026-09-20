from __future__ import annotations

from datetime import date
from pathlib import Path

from .io import write_json, write_text
from .paths import snapshots_dir
from .planning import build_today_plan
from .state import build_current_state
from .time_utils import parse_date


def _line_items(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def build_daily_brief(
    root: str | Path | None = None,
    for_date: str | date | None = None,
    state: dict | None = None,
    plan: dict | None = None,
) -> dict:
    state = state or build_current_state(root, for_date)
    plan = plan or build_today_plan(root, for_date, state=state)
    target_date = parse_date(for_date) or parse_date(state["date"])
    readiness = state["readiness"]
    session = plan["session"]
    latest_wellness = state.get("wellness_trends", {}).get("latest") or {}
    battery_prediction = (
        state.get("body_battery_model", {}).get("latest_prediction") or {}
    )
    training_predictor = state.get("training_predictor", {})
    training_prediction = training_predictor.get("today_prediction") or {}
    training_prediction_payload = training_prediction.get("prediction") or {}
    brief = {
        "date": target_date.isoformat(),
        "readiness": {
            "level": readiness.get("readiness_level"),
            "score": readiness.get("readiness_score"),
            "confidence": readiness.get("confidence"),
            "hard_session_guidance": readiness.get("hard_session_guidance"),
        },
        "phase": state.get("phase"),
        "data_freshness": state.get("data_freshness"),
        "readiness_reasons": readiness.get("reasons", []),
        "wellness_highlights": {
            "sleep_score": latest_wellness.get("sleep_score"),
            "sleep_hours": latest_wellness.get("sleep_hours"),
            "body_battery_current": latest_wellness.get("body_battery_current"),
            "body_battery_wake": latest_wellness.get("body_battery_wake"),
            "hrv_status": latest_wellness.get("hrv_status"),
            "overnight_hrv": latest_wellness.get("overnight_hrv"),
            "avg_stress": latest_wellness.get("avg_stress"),
        },
        "training_status": {
            "feedback": state.get("training_status_current", {}).get("training_status_feedback"),
            "acwr": state.get("training_status_current", {}).get("acute_chronic", {}),
            "flags": state.get("training_status_current", {}).get("flags", []),
        },
        "body_battery_model": state.get("body_battery_model", {}),
        "training_predictor": training_predictor,
        "session": session,
        "gym": plan.get("gym"),
        "nutrition": plan.get("nutrition"),
        "guardrails": plan.get("guardrails", []),
    }
    text = "\n".join(
        [
            f"Daily Brief - {target_date.isoformat()}",
            "",
            f"Readiness: {brief['readiness']['level']} "
            f"({brief['readiness']['score']}/100, confidence {brief['readiness']['confidence']})",
            f"Phase: {brief['phase']['name']} - {brief['phase']['reason']}",
            f"Data: {brief['data_freshness']['message']}",
            "",
            "Fenix signals:",
            _line_items([f"{key}: {value}" for key, value in brief["wellness_highlights"].items()]),
            "",
            "Readiness reasons:",
            _line_items([reason.get("message", "") for reason in brief["readiness_reasons"]])
            if brief["readiness_reasons"]
            else "- No limiting readiness reasons.",
            "",
            "Training status:",
            _line_items(
                [
                    f"feedback: {brief['training_status']['feedback']}",
                    f"ACWR: {brief['training_status']['acwr'].get('ratio')} ({brief['training_status']['acwr'].get('status')})",
                    *[flag.get("message", "") for flag in brief["training_status"]["flags"]],
                ]
            ),
            "",
            "Body Battery model:",
            _line_items(
                [
                    f"samples: {brief['body_battery_model'].get('samples')}",
                    f"LOO accuracy: {brief['body_battery_model'].get('leave_one_out_accuracy')}",
                    "path: "
                    + " -> ".join(
                        (
                            battery_prediction.get("path")
                            or []
                        )
                    ),
                    "prob good wake BB: "
                    + str(
                        (
                            battery_prediction.get("leaf", {})
                            .get("prob_good_wake_body_battery")
                        )
                    ),
                ]
            ),
            "",
            "Training predictor:",
            _line_items(
                [
                    f"samples: {brief['training_predictor'].get('samples')}",
                    "validation: "
                    + str(
                        brief["training_predictor"]
                        .get("validation", {})
                        .get("utility")
                    ),
                    "predicted next-day level: "
                    + str(training_prediction_payload.get("predicted_next_day_readiness_level")),
                    "prob next-day ready: "
                    + str(training_prediction_payload.get("prob_next_day_ready")),
                ]
            ),
            "",
            f"Session: {session['title']}",
            _line_items(session.get("details", [])),
            "",
            f"Gym: {brief['gym']['status']}",
            _line_items(brief["gym"].get("details", [])),
            "",
            "Nutrition:",
            _line_items([f"{key}: {value}" for key, value in brief["nutrition"].items()]),
            "",
            "Guardrails:",
            _line_items(brief["guardrails"]),
            "",
        ]
    )
    write_json(snapshots_dir(root) / "daily_brief.json", brief)
    write_text(snapshots_dir(root) / "daily_brief.txt", text)
    return brief
