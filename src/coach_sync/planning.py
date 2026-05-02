from __future__ import annotations

from datetime import date
from pathlib import Path

from .io import write_json
from .paths import snapshots_dir
from .state import build_current_state
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local


def _nutrition_block(context: dict, session_intensity: str, duration_min: int) -> dict:
    nutrition = context.get("nutrition", {})
    body_weight = context.get("athlete", {}).get("body_weight_kg")
    carb_ranges = nutrition.get("carb_g_per_kg", {})
    protein_range = nutrition.get("protein_g_per_kg", [1.6, 2.2])
    carbs = carb_ranges.get(session_intensity, carb_ranges.get("easy", [2.0, 4.0]))
    if duration_min < 60:
        during = nutrition.get("during_session_carbs_g_per_hour", {}).get("under_60_min", [0, 20])
    elif duration_min <= 120:
        during = nutrition.get("during_session_carbs_g_per_hour", {}).get("60_to_120_min", [30, 60])
    else:
        during = nutrition.get("during_session_carbs_g_per_hour", {}).get("over_120_min", [60, 90])

    return {
        "daily_protein": f"{protein_range[0]}-{protein_range[1]} g/kg"
        if body_weight is None
        else f"{round(protein_range[0] * body_weight)}-{round(protein_range[1] * body_weight)} g",
        "daily_carbs": f"{carbs[0]}-{carbs[1]} g/kg",
        "during_session_carbs": f"{during[0]}-{during[1]} g/hour",
        "hydration": "Start hydrated; add electrolytes if the session is hot, long, or sweat-heavy.",
        "recovery": "Eat protein plus carbs within 2 hours when the ride or gym session is meaningful.",
    }


def _blocked_plan(state: dict) -> dict:
    return {
        "title": "Protected recovery / indoor-only fallback",
        "type": "recovery",
        "duration_min": 30,
        "intensity": "recovery",
        "details": [
            "Use easy walking, mobility, or very easy spin only.",
            "Do not add trail, grip-intensive gym, or impact loading until gates and symptoms support it.",
        ],
    }


def _red_plan(state: dict) -> dict:
    return {
        "title": "Recovery and tissue check",
        "type": "recovery",
        "duration_min": 20,
        "intensity": "recovery",
        "details": [
            "Keep the day easy: walk, mobility, or a short recovery spin.",
            "No hard intervals, heavy gripping, hard braking practice, or heavy gym loading.",
        ],
    }


def _yellow_reentry_plan(state: dict) -> dict:
    return {
        "title": "Easy outdoor re-entry or indoor spin",
        "type": "outdoor_bike_optional",
        "duration_min": 45,
        "intensity": "easy",
        "details": [
            "Ride easy Z1-Z2 / RPE 2-4.",
            "Use low-consequence terrain and stop before grip fatigue changes handling.",
            "If the stale-data or symptom flag is the limiter, avoid making this a hard session.",
        ],
    }


def _indoor_reentry_plan(state: dict, reason: str) -> dict:
    return {
        "title": "Indoor aerobic ride / no new MTB exposure",
        "type": "indoor_bike",
        "duration_min": 45,
        "intensity": "easy",
        "details": [
            reason,
            "Ride easy Z1-Z2 / RPE 2-4 with no new outdoor trail exposure today.",
            "Use the day to preserve aerobic continuity and let hand/trail exposure consolidate.",
        ],
    }


def _green_reentry_plan(state: dict) -> dict:
    return {
        "title": "Outdoor MTB re-entry ride",
        "type": "outdoor_mtb",
        "duration_min": 60,
        "intensity": "easy",
        "details": [
            "Ride 45-75 min mostly aerobic, keeping technical choices below crash-risk appetite.",
            "Include relaxed cornering, braking feel, body-position checks, and hand tolerance monitoring.",
            "Keep jumps, drops, heavy landings, and race-like descending out of the same first-load stack.",
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
                "Keep the hard work repeatable; stop if hand control or form degrades.",
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


def _blocked_modalities(state: dict, aliases: set[str]) -> list[dict]:
    blocked = []
    normalized_aliases = {alias.lower().replace(" ", "_") for alias in aliases}
    for override in state.get("active_modality_overrides", []):
        status = str(override.get("status", "")).lower()
        if status not in {"blocked", "restricted"}:
            continue
        modalities = {
            str(modality).lower().replace(" ", "_")
            for modality in override.get("modalities", [])
        }
        if modalities & normalized_aliases:
            blocked.append(override)
    return blocked


def _gym_block(state: dict) -> dict:
    if not state.get("clearance", {}).get("gates", {}).get("loading", {}).get("status") == "cleared":
        return {"status": "blocked", "details": ["Loading gate is not cleared."]}
    blocked = _blocked_modalities(state, {"gym", "loading", "strength", "strength_training"})
    if blocked:
        return {
            "status": "blocked",
            "details": [f"Active modality override blocks gym/loading: {blocked[0].get('reason', '')}".strip()],
        }
    level = state.get("readiness", {}).get("readiness_level")
    if level == "red":
        return {"status": "skip", "details": ["Skip gym loading today; keep mobility only."]}
    if state.get("phase", {}).get("name") == "return_to_outdoor_reentry":
        return {
            "status": "primer",
            "details": [
                "Optional 25-40 min gym primer: legs, trunk, pushing, light pulling.",
                "Use conservative grip exposure and leave 3 reps in reserve.",
            ],
        }
    return {
        "status": "available",
        "details": [
            "Gym can be layered 2-3x/week if it does not compromise key rides or hand response.",
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
    readiness = state.get("readiness", {})
    level = readiness.get("readiness_level")
    hard_guidance = readiness.get("hard_session_guidance")
    all_cleared = state.get("clearance", {}).get("all_cleared")
    phase = state.get("phase", {}).get("name")
    data_status = state.get("data_freshness", {}).get("status")
    stale = data_status in {"stale", "missing"}
    hard_confidence_limited = (
        state.get("data_freshness", {}).get("hard_session_confidence") == "limited"
    )
    trail_blocked = _blocked_modalities(
        state,
        {"trail", "outdoor_biking", "outdoor_bike", "mountain_biking", "mtb"},
    )
    mtb_cap_reached = any(
        flag.get("type") == "reentry_mtb_cap_reached"
        for flag in state.get("injury_return", {}).get("flags", [])
    )

    if not all_cleared:
        session = _blocked_plan(state)
    elif trail_blocked:
        session = _indoor_reentry_plan(
            state,
            f"Active modality override blocks trail/outdoor MTB: {trail_blocked[0].get('reason', '')}".strip(),
        )
    elif level == "red" or hard_guidance == "avoid":
        session = _red_plan(state)
    elif phase == "return_to_outdoor_reentry" and mtb_cap_reached:
        session = _indoor_reentry_plan(
            state,
            "Re-entry MTB exposure cap is reached for the last 7 days.",
        )
    elif phase == "return_to_outdoor_reentry":
        session = _yellow_reentry_plan(state) if level == "yellow" or stale else _green_reentry_plan(state)
    elif level == "yellow" or stale:
        session = _yellow_reentry_plan(state)
    else:
        session = _green_base_plan(state)
        if session.get("intensity") == "hard" and hard_confidence_limited:
            session = _data_limited_base_plan(state)

    nutrition_context = full_context
    nutrition = _nutrition_block(
        nutrition_context,
        session.get("intensity", "easy"),
        int(session.get("duration_min") or 0),
    )
    guardrails = [
        "Dr. Teh's clearance opens outdoor biking, gym, and normal activity; progression still follows symptom and load response.",
        "Downshift tomorrow if pain, swelling, inflammation, reduced grip tolerance, or poor next-morning response appears.",
    ]
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
    if mtb_cap_reached:
        guardrails.append("Re-entry MTB exposure cap is reached; choose indoor/easy non-trail work today.")
    for override in trail_blocked:
        guardrails.append(
            f"Active modality override: {override.get('status')} {', '.join(override.get('modalities', []))} - {override.get('reason', '')}"
        )

    plan = {
        "date": target_date.isoformat(),
        "generated_at": iso_now(tz),
        "coaching_status": "proposal_for_llm_coach",
        "session": session,
        "gym": _gym_block(state),
        "nutrition": nutrition,
        "guardrails": guardrails,
        "decision_inputs": {
            "phase": phase,
            "readiness_level": level,
            "readiness_score": readiness.get("readiness_score"),
            "hard_session_guidance": hard_guidance,
            "data_freshness": state.get("data_freshness"),
            "clearance_all_cleared": all_cleared,
        },
    }
    write_json(snapshots_dir(root) / "today_plan.json", plan)
    return plan
