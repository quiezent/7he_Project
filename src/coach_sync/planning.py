from __future__ import annotations

from datetime import date
from pathlib import Path

from .garmin_arbitration import build_garmin_arbitration
from .io import read_json, write_json
from .paths import input_dir, snapshots_dir
from .state import build_current_state
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local


SESSION_CONTRACT_FIELDS = [
    "purpose",
    "dose",
    "adaptation_hypothesis",
    "execution_rules",
    "expected_result",
    "stop_rules",
    "post_session_review_fields",
]

TRAINABLE_SESSION_TYPES = {
    "bike_quality",
    "cns_recovery",
    "endurance_data_limited",
    "endurance_skills",
    "garmin_aerobic_continuity",
    "mtb_repeatability_controlled",
    "outdoor_bike_optional",
}


def _planned_session_path(target_date: date) -> str:
    return f"input/planned_session_{target_date.isoformat()}.json"


def _weekly_plan_path(target_date: date) -> str:
    iso = target_date.isocalendar()
    return f"snapshots/weekly_plan_{iso.year}-W{iso.week:02d}.json"


def _load_planned_session(root: str | Path | None, target_date: date) -> dict | None:
    path = input_dir(root) / f"planned_session_{target_date.isoformat()}.json"
    payload = read_json(path, {})
    if not isinstance(payload, dict):
        return None
    session = payload.get("session")
    if not isinstance(session, dict):
        return None
    payload_date = parse_date(payload.get("date"))
    if payload_date and payload_date != target_date:
        return None
    return {
        "session": session,
        "source": {
            "type": "input_planned_session",
            "path": _planned_session_path(target_date),
        },
    }


def load_weekly_session(root: str | Path | None, target_date: date) -> dict | None:
    """Return the matching weekly-intent session only when its date range is valid."""
    dated_path = snapshots_dir(root) / Path(_weekly_plan_path(target_date)).name
    current_path = snapshots_dir(root) / "weekly_plan.json"
    for path in (dated_path, current_path):
        payload = read_json(path, {})
        if not isinstance(payload, dict) or payload.get("artifact_type") != "weekly_training_plan":
            continue
        week_start = parse_date(payload.get("week_start"))
        week_end = parse_date(payload.get("week_end"))
        if week_start is None or week_end is None or not week_start <= target_date <= week_end:
            continue
        for session in payload.get("sessions") or []:
            if not isinstance(session, dict) or parse_date(session.get("date")) != target_date:
                continue
            return {
                "session": dict(session),
                "source": {
                    "type": "weekly_plan_session",
                    "path": f"snapshots/{path.name}",
                    "week_key": payload.get("week_key"),
                    "generated_at": payload.get("generated_at"),
                    "weekly_plan_status": payload.get("status"),
                },
            }
    return None


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


def _default_stop_rules() -> list[str]:
    return [
        "End technical work if braking timing gets lazy or line choice becomes reactive.",
        "End intensity if HR drift or RPE turns controlled work into survival.",
        "Downshift immediately if rain, traffic, heat, or trail consequence exceeds the session purpose.",
    ]


def _review_fields() -> list[str]:
    return [
        "actual_duration_min",
        "actual_training_load",
        "actual_rpe",
        "workout_feel",
        "next_morning_response",
        "stop_rule_outcome",
        "fueling_carbs_g_per_hour",
        "fluid_ml_per_hour",
        "sodium_mg_per_hour",
        "technical_quality_notes",
        "late_session_skill_fade",
    ]


def _contract_for_session(session: dict) -> dict:
    session_type = session.get("type")
    duration = int(session.get("duration_min") or 0)
    intensity = session.get("intensity") or "easy"

    if session_type == "bike_quality":
        return {
            "purpose": "Rebuild bike-specific engine quality without using stale FTP as the prescription anchor.",
            "dose": {
                "duration_min": duration,
                "intensity": intensity,
                "completion": "Controlled quality work is complete when the final interval is repeatable, not barely survived.",
                "cap": "Keep the session inside the written duration and skip extra work.",
            },
            "adaptation_hypothesis": (
                "A controlled bike-quality dose should rebuild torque, threshold durability, and repeatable power while still allowing the next key trail session to stay sharp."
            ),
            "execution_rules": [
                "Warm up thoroughly before any hard work.",
                "Use current RPE and HR response rather than historical 222 W P20 assumptions.",
                "Keep cadence, posture, and breathing controlled through the final work block.",
            ],
            "expected_result": {
                "garmin_load": "meaningful but controlled bike-specific load",
                "rpe": "hard but repeatable",
                "next_day": "no poor recovery signal and no loss of trail-quality readiness",
            },
            "stop_rules": _default_stop_rules(),
            "post_session_review_fields": _review_fields(),
        }
    if session_type == "endurance_skills":
        return {
            "purpose": "Maintain aerobic bike continuity while touching MTB skills without creating a hard-session recovery cost.",
            "dose": {
                "duration_min": duration,
                "intensity": intensity,
                "completion": "Z2 ride plus a few clean technique touches, finished with form still crisp.",
                "cap": "No chasing segment time, extra descents, or late intensity creep.",
            },
            "adaptation_hypothesis": (
                "Low-cost skill touches under aerobic load should support continuity and technical confidence while preserving capacity for the next key session."
            ),
            "execution_rules": [
                "Keep skill work low consequence and repeat only clean reps.",
                "Use full recovery between technique touches.",
                "Let precision, not speed, decide whether another rep is useful.",
            ],
            "expected_result": {
                "garmin_load": "moderate aerobic load",
                "rpe": "moderate, never race-like",
                "next_day": "ready for normal training with no late-ride skill collapse",
            },
            "stop_rules": _default_stop_rules(),
            "post_session_review_fields": _review_fields(),
        }
    if session_type == "endurance_data_limited":
        return {
            "purpose": "Preserve aerobic and bike-specific continuity while deferring hard work because evidence confidence is limited.",
            "dose": {
                "duration_min": duration,
                "intensity": intensity,
                "completion": "Aerobic work only, finished fresh enough that missing data did not hide a hard session.",
                "cap": "No intervals, race efforts, or durability extension.",
            },
            "adaptation_hypothesis": (
                "A capped aerobic ride should keep the bike rhythm alive without making an unverified recovery or load state worse."
            ),
            "execution_rules": [
                "Ride by RPE if Garmin evidence is stale or incomplete.",
                "Keep breathing conversational and form smooth.",
                "Treat the next Garmin sync as the final gate for future hard work.",
            ],
            "expected_result": {
                "garmin_load": "low-to-moderate aerobic load",
                "rpe": "easy to moderate",
                "next_day": "no avoidable recovery penalty from a low-confidence day",
            },
            "stop_rules": _default_stop_rules(),
            "post_session_review_fields": _review_fields(),
        }
    if session_type == "mtb_repeatability_controlled":
        return {
            "purpose": "Use Garmin Productive/optimal load status to buy a controlled MTB repeatability stimulus instead of adding more easy-only volume.",
            "dose": {
                "duration_min": duration,
                "intensity": intensity,
                "completion": "Repeatable climb/descent work is complete when the final descent is still precise and the load stays inside the cap.",
                "cap": "Controlled high-aerobic/MTB repeatability only; no sprint, VO2, KOM, or extra anaerobic stacking.",
            },
            "adaptation_hypothesis": (
                "A bounded MTB repeatability dose should convert Garmin's productive status and low-aerobic skew into useful high-aerobic trail fitness while preserving technical precision and next-day recovery."
            ),
            "execution_rules": [
                "Use a repeatable loop so the session tests repeatability rather than novelty.",
                "Climb controlled: mostly Z2 to low tempo unless the prescription states otherwise.",
                "Descend smooth and technically deliberate; speed is allowed only if braking, vision, and body position stay clean.",
                "Name any upgrade before doing it; do not let feeling good turn the session into open-ended testing.",
            ],
            "expected_result": {
                "garmin_load": "meaningful but bounded MTB load",
                "rpe": "moderate to hard, but not survival",
                "next_day": "no poor response beyond expected fatigue and no technical slop carried forward",
            },
            "stop_rules": [
                *_default_stop_rules(),
                "Stop adding loops if Garmin load approaches the cap, HR stays high on easy climbs, or descents become reactive.",
                "Stop anaerobic attacks if Garmin anaerobic load is already near the upper target.",
            ],
            "post_session_review_fields": [
                *_review_fields(),
                "loop_count",
                "climb_power_or_hr_by_loop",
                "descent_quality_by_loop",
                "garmin_load_focus_after_session",
            ],
        }
    return {
        "purpose": "Protect bike-specific continuity without adding meaningful recovery debt.",
        "dose": {
            "duration_min": duration,
            "intensity": intensity,
            "completion": "Easy spin or low-consequence ride completed within the cap.",
            "cap": "Stay Z1-Z2 / RPE 2-4 and stop before the ride becomes training density.",
        },
        "adaptation_hypothesis": (
            "An easy bike touch should support the weekly continuity floor while leaving freshness for higher-value MTB or structured sessions."
        ),
        "execution_rules": [
            "Use low-consequence terrain or the indoor trainer.",
            "Avoid climbs, descents, or group dynamics that turn the ride hard.",
            "Finish with better freshness than you started with.",
        ],
        "expected_result": {
            "garmin_load": "low",
            "rpe": "easy",
            "next_day": "same or better readiness, with no skill-quality penalty",
        },
        "stop_rules": _default_stop_rules(),
        "post_session_review_fields": _review_fields(),
    }


def _with_session_contract(session: dict) -> dict:
    if session.get("type") not in TRAINABLE_SESSION_TYPES:
        return session
    contracted = {**session, **_contract_for_session(session)}
    contracted["schema_version"] = 3
    contracted["contract_fields"] = list(SESSION_CONTRACT_FIELDS)
    return contracted


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


def _cns_recovery_plan(cns: dict) -> dict:
    ceiling = (cns.get("session_ceiling") or {}).get("level") or "low_consequence_only"
    return {
        "title": "CNS low-consequence recovery",
        "type": "cns_recovery",
        "duration_min": 20,
        "intensity": "recovery",
        "details": [
            "Use a walk, mobility, or a very easy indoor spin only if it improves clarity and freshness.",
            "No technical trail riding, speed, jumps, enduro simulation, setup testing, or stacked variables.",
            f"CNS ceiling today: {ceiling}.",
        ],
    }


def _garmin_aerobic_continuity_plan(arbitration: dict) -> dict:
    return {
        "title": "Garmin-capped aerobic continuity",
        "type": "garmin_aerobic_continuity",
        "duration_min": 45,
        "intensity": "easy",
        "details": [
            "Keep the work conversational and bounded; preserve the next quality opportunity.",
            "Use the indoor trainer or low-consequence terrain only.",
            "No intervals, durability extension, speed hunting, jump progression, or setup testing.",
            f"Garmin ceiling: {arbitration.get('ceiling') or 'aerobic continuity'}.",
        ],
    }


def _session_is_already_low_consequence(session: dict) -> bool:
    return (
        str(session.get("intensity") or "").lower() in {"recovery", "easy"}
        and session.get("type") not in {"mtb_quality_skill", "mtb_skill_transfer_optional"}
    )


def _session_summary(session: dict) -> dict:
    return {
        "title": session.get("title"),
        "type": session.get("type"),
        "duration_min": session.get("duration_min"),
        "intensity": session.get("intensity"),
    }


def _apply_session_constraints(
    session: dict,
    state: dict,
    arbitration: dict,
) -> tuple[dict, list[dict]]:
    """Apply safety ceilings after resolving the source session but before prediction."""
    effective = dict(session)
    constraints: list[dict] = []
    cns = state.get("cns_readiness") or {}
    cns_status = str(cns.get("status") or "").lower()
    freshness = state.get("data_freshness") or {}

    if cns_status in {"impaired", "compromised"} and effective.get("type") != "scheduled_rest":
        replacement = _cns_recovery_plan(cns)
        constraints.append(
            {
                "source": "cns_readiness",
                "reason": cns.get("interpretation")
                or "CNS status caps technical consequence and structured training today.",
                "ceiling": (cns.get("session_ceiling") or {}).get("level"),
                "original_session": _session_summary(effective),
                "effective_session": _session_summary(replacement),
            }
        )
        effective = replacement
    elif (
        freshness.get("status") in {"stale", "future", "missing"}
        or freshness.get("hard_session_confidence") == "limited"
    ) and not _session_is_already_low_consequence(effective):
        replacement = _data_limited_base_plan(state)
        constraints.append(
            {
                "source": "data_freshness",
                "reason": freshness.get("message")
                or "Hard-session evidence is stale, missing, or otherwise limited.",
                "original_session": _session_summary(effective),
                "effective_session": _session_summary(replacement),
            }
        )
        effective = replacement
    elif (
        arbitration.get("recommended_action") in {"downshift", "no_hard_guidance"}
        and not _session_is_already_low_consequence(effective)
    ):
        replacement = _garmin_aerobic_continuity_plan(arbitration)
        constraints.append(
            {
                "source": "garmin_diagnosis_arbitration",
                "reason": arbitration.get("summary") or "Garmin diagnosis lowers the session ceiling.",
                "ceiling": arbitration.get("ceiling"),
                "original_session": _session_summary(effective),
                "effective_session": _session_summary(replacement),
            }
        )
        effective = replacement

    return effective, constraints


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


def _controlled_upgrade_plan(state: dict, arbitration: dict) -> dict:
    return {
        "title": "Controlled MTB repeatability",
        "type": "mtb_repeatability_controlled",
        "duration_min": 75,
        "intensity": "moderate",
        "stimulus_intent": arbitration.get("stimulus"),
        "details": [
            "Use Garmin Productive/optimal status as permission for a bounded stimulus, not an open-ended hard day.",
            "Choose repeatable climb/descent loops; climb controlled and descend with technical precision.",
            "If anaerobic load is near the upper band, skip sprint, VO2, and attack efforts.",
        ],
    }


def _session_can_receive_adaptive_upgrade(session: dict) -> bool:
    return str(session.get("intensity") or "").lower() in {"easy", "recovery", "recovery_skill"}


def _with_adaptive_upgrade_option(session: dict, arbitration: dict) -> dict:
    if arbitration.get("recommended_action") != "controlled_upgrade":
        return session
    if not _session_can_receive_adaptive_upgrade(session):
        return session
    upgraded = dict(session)
    upgraded["adaptive_upgrade_option"] = {
        "source": "garmin_diagnosis_arbitration",
        "ceiling": arbitration.get("ceiling"),
        "stimulus": arbitration.get("stimulus"),
        "allowed_stimulus": arbitration.get("allowed_stimulus", []),
        "avoid": arbitration.get("avoid", []),
        "rule": "Only use this upgrade if subjective sharpness, route consequence, and the written session purpose still agree.",
    }
    return upgraded


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
    stale = data_status in {"stale", "future", "missing"}
    hard_confidence_limited = (
        state.get("data_freshness", {}).get("hard_session_confidence") == "limited"
    )
    planned_session = _load_planned_session(root, target_date)
    weekly_session = load_weekly_session(root, target_date)
    selected_session = planned_session or weekly_session
    plan_source = {"type": "today_plan", "path": "snapshots/today_plan.json"}
    garmin_arbitration = build_garmin_arbitration(state)

    if scheduled_rest:
        session = _scheduled_rest_plan(scheduled_rest)
    elif level == "red" or hard_guidance == "avoid":
        session = _red_plan(state)
    elif selected_session:
        session = dict(selected_session["session"])
        session = _with_adaptive_upgrade_option(session, garmin_arbitration)
        plan_source = selected_session["source"]
    elif level == "yellow" or stale:
        if not stale and garmin_arbitration.get("recommended_action") == "controlled_upgrade":
            session = _controlled_upgrade_plan(state, garmin_arbitration)
        else:
            session = _yellow_base_plan(state)
    else:
        session = _green_base_plan(state)
        if session.get("intensity") == "hard" and hard_confidence_limited:
            session = _data_limited_base_plan(state)
        elif (
            session.get("intensity") == "hard"
            and garmin_arbitration.get("recommended_action") in {"downshift", "no_hard_guidance"}
        ):
            session = _yellow_base_plan(state)
    session, applied_constraints = _apply_session_constraints(session, state, garmin_arbitration)
    session = _with_session_contract(session)

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
    if selected_session and not scheduled_rest and not (level == "red" or hard_guidance == "avoid"):
        if plan_source.get("type") == "input_planned_session":
            guardrails.insert(0, f"Using coach-authored planned session from {plan_source['path']}.")
        else:
            guardrails.insert(
                0,
                f"Using this week's intent session from {plan_source['path']}; same-day readiness remains the execution gate.",
            )
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
    for constraint in applied_constraints:
        reason = constraint.get("reason")
        if reason and reason not in guardrails:
            guardrails.insert(0, reason)
    if (
        not scheduled_rest
        and not (level == "red" or hard_guidance == "avoid")
        and garmin_arbitration.get("status") in {"available", "freshness_limited"}
    ):
        guardrails.append(
            f"Garmin arbitration: {garmin_arbitration.get('summary')} "
            f"Allowed: {'; '.join(garmin_arbitration.get('allowed_stimulus') or [])} "
            f"Avoid: {'; '.join(garmin_arbitration.get('avoid') or [])}"
        )

    plan = {
        "date": target_date.isoformat(),
        "generated_at": iso_now(tz),
        "coaching_status": "proposal_for_llm_coach",
        "session": session,
        "plan_source": plan_source,
        "gym": _gym_block(state, scheduled_rest=scheduled_rest),
        "nutrition": nutrition,
        "guardrails": guardrails,
        "decision_inputs": {
            "phase": phase,
            "readiness_level": level,
            "readiness_score": readiness.get("readiness_score"),
            "readiness_accuracy": readiness.get("readiness_accuracy"),
            "hard_session_guidance": hard_guidance,
            "data_freshness": state.get("data_freshness"),
            "scheduled_rest": scheduled_rest,
            "garmin_arbitration": garmin_arbitration,
            "cns_readiness": {
                "status": (state.get("cns_readiness") or {}).get("status"),
                "session_ceiling": (state.get("cns_readiness") or {}).get("session_ceiling"),
            },
        },
        "constraint_resolution": {
            "applied": applied_constraints,
            "effective_session_source": "constraint" if applied_constraints else plan_source.get("type"),
        },
    }
    write_json(snapshots_dir(root) / "today_plan.json", plan)
    return plan
