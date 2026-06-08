from __future__ import annotations

from datetime import date
from pathlib import Path

from .io import write_json
from .paths import snapshots_dir
from .state import build_current_state
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local


def _nutrition_block(context: dict, session_intensity: str, duration_min: int) -> dict:
    nutrition = context.get("nutrition", {})
    athlete = context.get("athlete", {})
    body_weight = athlete.get("body_weight_kg")
    body_weight_source = athlete.get("body_weight_source")
    carb_ranges = nutrition.get("carb_g_per_kg", {})
    protein_range = nutrition.get("protein_g_per_kg", [1.6, 2.2])
    carbs = carb_ranges.get(session_intensity, carb_ranges.get("easy", [2.0, 4.0]))
    if duration_min <= 0:
        during = None
    elif duration_min < 60:
        during = nutrition.get("during_session_carbs_g_per_hour", {}).get("under_60_min", [0, 20])
    elif duration_min <= 120:
        during = nutrition.get("during_session_carbs_g_per_hour", {}).get("60_to_120_min", [30, 60])
    else:
        during = nutrition.get("during_session_carbs_g_per_hour", {}).get("over_120_min", [60, 90])

    block = {
        "daily_protein": f"{protein_range[0]}-{protein_range[1]} g/kg"
        if body_weight is None
        else f"{round(protein_range[0] * body_weight)}-{round(protein_range[1] * body_weight)} g",
        "daily_carbs": f"{carbs[0]}-{carbs[1]} g/kg",
        "during_session_carbs": "none"
        if during is None
        else f"{during[0]}-{during[1]} g/hour",
        "hydration": "Start hydrated; add electrolytes if the session is hot, long, or sweat-heavy.",
        "recovery": "Eat protein plus carbs within 2 hours when the ride or gym session is meaningful.",
    }
    if body_weight is not None:
        basis = f"{round(body_weight, 2)} kg"
        if isinstance(body_weight_source, dict) and body_weight_source.get("date"):
            basis = f"{basis} from Garmin scale on {body_weight_source['date']}"
        block["body_weight_basis"] = basis
    return block


def _red_plan(state: dict) -> dict:
    return {
        "title": "Recovery day",
        "type": "recovery",
        "duration_min": 20,
        "intensity": "recovery",
        "details": [
            "Keep the day easy: walk, mobility, or a short recovery spin.",
            "No hard intervals or heavy gym loading.",
        ],
    }


def _scheduled_rest_plan(rule: dict) -> dict:
    label = rule.get("label") or "Scheduled rest"
    reason = rule.get("reason") or "Scheduled no-exercise day."
    return {
        "title": f"{label} rest day",
        "type": "scheduled_rest",
        "duration_min": 0,
        "intensity": "recovery",
        "details": [
            reason,
            "No ride, gym, intervals, strength loading, or planned training today.",
            "Normal life, worship, family time, meals, and easy unwinding are enough.",
        ],
    }


def _yellow_base_plan(state: dict) -> dict:
    return {
        "title": "Easy bike continuity",
        "type": "outdoor_bike_optional",
        "duration_min": 45,
        "intensity": "easy",
        "details": [
            "Ride easy Z1-Z2 / RPE 2-4.",
            "Use low-consequence terrain or the indoor trainer.",
            "If Garmin freshness or readiness is the limiter, avoid making this a hard session.",
        ],
    }


def _data_limited_base_plan(state: dict) -> dict:
    return {
        "title": "Aerobic ride; hard work deferred",
        "type": "endurance_data_limited",
        "duration_min": 60,
        "intensity": "moderate",
        "details": [
            "Keep this aerobic because Garmin activity evidence is not sufficient for confident hard-session guidance.",
            "Use Z2 / RPE 3-5 and finish with form still clean.",
        ],
    }


def _green_base_plan(state: dict) -> dict:
    weekday = date.fromisoformat(state["date"]).weekday()
    if weekday in {1, 3}:
        return {
            "title": "Structured bike quality",
            "type": "bike_quality",
            "duration_min": 75,
            "intensity": "hard",
            "details": [
                "Warm up thoroughly, then complete controlled threshold or VO2 work.",
                "Keep the hard work repeatable; stop if form degrades.",
            ],
        }
    return {
        "title": "Aerobic ride plus MTB skill touches",
        "type": "endurance_skills",
        "duration_min": 75,
        "intensity": "moderate",
        "details": [
            "Ride Z2 with short technique touches: corner exits, braking timing, and stable body position.",
            "Do not chase intensity if readiness or Garmin freshness is uncertain.",
        ],
    }


def _scheduled_rest_rule(context: dict, target_date: date) -> dict | None:
    for rule in context.get("training_rules", {}).get("weekly_rest_days", []):
        if int(rule.get("weekday", -1)) == target_date.weekday():
            return rule
    return None


def _gym_block(state: dict, scheduled_rest: dict | None = None) -> dict:
    if scheduled_rest:
        return {
            "status": "skip",
            "details": [
                f"{scheduled_rest.get('label', 'Scheduled rest')} is a no-exercise day."
            ],
        }
    level = state.get("readiness", {}).get("readiness_level")
    if level == "red":
        return {"status": "skip", "details": ["Skip gym loading today; keep mobility only."]}
    return {
        "status": "available",
        "details": [
            "Gym can be layered 1-2x/week if it does not compromise key rides.",
        ],
    }


def build_today_plan(
    root: str | Path | None = None,
    for_date: str | date | None = None,
    state: dict | None = None,
) -> dict:
    state = state or build_current_state(root, for_date)
    context = {"athlete": state.get("athlete", {})}
    from .context import load_context

    full_context = load_context(root)
    tz = full_context.get("athlete", {}).get("timezone", DEFAULT_TIMEZONE)
    target_date = parse_date(for_date) or parse_date(state.get("date")) or today_local(tz)
    scheduled_rest = _scheduled_rest_rule(full_context, target_date)
    readiness = state.get("readiness", {})
    level = readiness.get("readiness_level")
    hard_guidance = readiness.get("hard_session_guidance")
    phase = state.get("phase", {}).get("name")
    data_status = state.get("data_freshness", {}).get("status")
    stale = data_status in {"stale", "missing"}
    hard_confidence_limited = (
        state.get("data_freshness", {}).get("hard_session_confidence") == "limited"
    )

    if scheduled_rest:
        session = _scheduled_rest_plan(scheduled_rest)
    elif level == "red" or hard_guidance == "avoid":
        session = _red_plan(state)
    elif level == "yellow" or stale:
        session = _yellow_base_plan(state)
    else:
        session = _green_base_plan(state)
        if session.get("intensity") == "hard" and hard_confidence_limited:
            session = _data_limited_base_plan(state)

    nutrition_context = {
        **full_context,
        "athlete": {
            **full_context.get("athlete", {}),
            **state.get("athlete", {}),
        },
    }
    nutrition = _nutrition_block(
        nutrition_context,
        session.get("intensity", "easy"),
        int(session.get("duration_min") or 0),
    )
    guardrails = [
        "Progression follows readiness, recent load, bike specificity, and next-day response.",
        "Downshift tomorrow if the session produces unusually poor recovery or skill quality.",
    ]
    if scheduled_rest:
        guardrails.insert(
            0,
            f"{scheduled_rest.get('label', 'Scheduled rest')} is a hard rest constraint: no planned exercise today.",
        )
    if stale:
        guardrails.insert(
            0,
            state.get("data_freshness", {}).get(
                "message", "Garmin data is stale or missing; avoid confident hard-session guidance."
            ),
        )
    if hard_confidence_limited:
        for limiter in state.get("data_freshness", {}).get("hard_session_limiters", []):
            if limiter and limiter not in guardrails:
                guardrails.append(limiter)

    plan = {
        "date": target_date.isoformat(),
        "generated_at": iso_now(tz),
        "coaching_status": "proposal_for_llm_coach",
        "session": session,
        "gym": _gym_block(state, scheduled_rest=scheduled_rest),
        "nutrition": nutrition,
        "guardrails": guardrails,
        "decision_inputs": {
            "phase": phase,
            "readiness_level": level,
            "readiness_score": readiness.get("readiness_score"),
            "hard_session_guidance": hard_guidance,
            "data_freshness": state.get("data_freshness"),
            "scheduled_rest": scheduled_rest,
        },
    }
    write_json(snapshots_dir(root) / "today_plan.json", plan)
    return plan
