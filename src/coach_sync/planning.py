from __future__ import annotations

from datetime import date, datetime, timedelta
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from .garmin_arbitration import build_garmin_arbitration
from .io import read_json, write_json
from .paths import input_dir, snapshots_dir
from .sabbath import (
    replacement_sabbath_rule,
    scheduled_rest_rule,
    validate_sabbath_exception,
)
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
    "environment_indoor_continuity",
    "garmin_aerobic_continuity",
    "mtb_repeatability_controlled",
    "outdoor_bike_optional",
}


BUKIT_KIARA_VENUE_KEYS = {"bukit_kiara"}
BUKIT_KIARA_VENUE_ALIASES = {
    "bukit kiara",
    "kiara",
    "taman tun dr ismail",
    "ttdi",
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
        "contract_generated_at": payload.get("generated_at"),
        "contract_status": payload.get("status"),
        "sabbath_exception": payload.get("sabbath_exception"),
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


def _is_mtb_session(session: dict) -> bool:
    session_type = str(session.get("type") or "").lower()
    modality = str(session.get("modality") or "").lower()
    return bool(
        modality == "mtb"
        or session.get("mtb_exposure")
        or "mtb" in session_type
        or "enduro" in session_type
        or session_type == "endurance_skills"
    )


def _is_race_practice(session: dict) -> bool:
    if session.get("race_practice") is True or session.get("race_simulation") is True:
        return True
    text = " ".join(
        str(session.get(field) or "")
        for field in ("type", "title", "purpose", "stimulus_intent")
    ).lower()
    return any(
        marker in text
        for marker in ("race practice", "race_practice", "race simulation", "race_simulation")
    )


def _heat_context(state: dict) -> dict:
    training_status = state.get("training_status_current") or {}
    acclimation = training_status.get("acclimation") or state.get("heat_acclimation")
    latest_session = state.get("latest_session_evidence")
    compact_latest = None
    if isinstance(latest_session, dict):
        activity = latest_session.get("activity") or {}
        environment = latest_session.get("environment") or {}
        compact_latest = {
            "date": activity.get("date") or latest_session.get("date"),
            "activity": {
                "date": activity.get("date"),
                "category": activity.get("category"),
            }
            if isinstance(activity, dict) and activity
            else None,
            "device_temperature": environment.get("device_temperature")
            or latest_session.get("temperature"),
            "garmin_estimated_water_loss": environment.get(
                "garmin_estimated_water_loss"
            )
            or (
                {
                    "value_ml": latest_session.get("water_estimated_ml"),
                    "measurement_type": "estimated_not_measured",
                }
                if latest_session.get("water_estimated_ml") is not None
                else None
            ),
            "weather": environment.get("weather"),
            "decision_role": (
                "Compact prior-session heat and sweat-model context only; no device "
                "identifiers, technical evidence, or raw trace are copied into the plan."
            ),
        }
        compact_latest = {
            key: value for key, value in compact_latest.items() if value is not None
        }
    return {
        "heat_acclimation": acclimation if isinstance(acclimation, dict) else None,
        "latest_session_evidence": compact_latest,
        "decision_role": "Context only; use same-day conditions, sweat response, and gut tolerance to select within the range.",
        "weather_rule": "Previous-session temperature or weather is not a forecast for this session.",
        "acclimation_rule": "Heat acclimation can inform tolerance but never reduces the carbohydrate, fluid, or sodium target by itself.",
    }


def _nutrition_block(
    context: dict,
    session_intensity: str,
    duration_min: int,
    session: dict | None = None,
    state: dict | None = None,
) -> dict:
    nutrition = context.get("nutrition", {})
    athlete = context.get("athlete", {})
    session = session or {}
    state = state or {}
    body_weight = athlete.get("body_weight_kg")
    body_weight_source = athlete.get("body_weight_source")
    carb_ranges = nutrition.get("carb_g_per_kg", {})
    protein_range = nutrition.get("protein_g_per_kg", [1.6, 2.2])
    carbs = carb_ranges.get(session_intensity, carb_ranges.get("easy", [2.0, 4.0]))
    low_aerobic_short = bool(
        session.get("type") in {
            "indoor_low_aerobic",
            "indoor_low_aerobic_sabbath_exception",
        }
        and str(session_intensity).lower() in {"easy", "recovery"}
        and duration_min <= 75
    )
    if duration_min <= 0:
        during = None
    elif low_aerobic_short:
        during = [0, 20]
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
        else (
            f"{during[0]}-{during[1]} g/hour (optional if normally fed)"
            if low_aerobic_short
            else f"{during[0]}-{during[1]} g/hour"
        ),
        "hydration": "Start hydrated; add electrolytes if the session is hot, long, or sweat-heavy.",
        "recovery": "Eat protein plus carbs within 2 hours when the ride or gym session is meaningful.",
    }
    if body_weight is not None:
        basis = f"{round(body_weight, 2)} kg"
        if isinstance(body_weight_source, dict) and body_weight_source.get("date"):
            basis = f"{basis} from Garmin scale on {body_weight_source['date']}"
        block["body_weight_basis"] = basis

    heat_context = _heat_context(state)
    if heat_context.get("heat_acclimation") or heat_context.get("latest_session_evidence"):
        block["heat_context"] = heat_context

    mtb_targets = nutrition.get("mtb_heat_fueling_targets") or {}
    target_key = None
    selection_reason = None
    if _is_mtb_session(session):
        if _is_race_practice(session):
            target_key = "over_150_min_or_race_practice"
            selection_reason = "Explicit MTB race-practice or race-simulation intent."
        elif duration_min > 150:
            target_key = "over_150_min_or_race_practice"
            selection_reason = "MTB duration is over 150 minutes."
        elif 90 <= duration_min <= 150:
            target_key = "ride_90_to_150_min"
            selection_reason = "MTB duration is within the 90-150 minute heat-fueling band."
    selected = mtb_targets.get(target_key) if target_key else None
    if isinstance(selected, dict):
        target_ranges = {
            field: list(selected[field])
            for field in ("carbs_g_per_hour", "fluid_ml_per_hour", "sodium_mg_per_hour")
            if isinstance(selected.get(field), (list, tuple)) and len(selected[field]) == 2
        }
        if "carbs_g_per_hour" in target_ranges:
            carb_target = target_ranges["carbs_g_per_hour"]
            block["during_session_carbs"] = f"{carb_target[0]}-{carb_target[1]} g/hour"
        if "fluid_ml_per_hour" in target_ranges and "sodium_mg_per_hour" in target_ranges:
            fluid_target = target_ranges["fluid_ml_per_hour"]
            sodium_target = target_ranges["sodium_mg_per_hour"]
            block["hydration"] = (
                f"{fluid_target[0]}-{fluid_target[1]} ml fluid/hour plus "
                f"{sodium_target[0]}-{sodium_target[1]} mg sodium/hour."
            )
        block["during_session_targets"] = {
            "source": "config/athlete_context.json:nutrition.mtb_heat_fueling_targets",
            "profile": target_key,
            "selection_reason": selection_reason,
            **target_ranges,
        }
        if "body_weight_basis" not in block and mtb_targets.get("basis"):
            block["body_weight_basis"] = mtb_targets.get("basis")
        block["mtb_heat_target_basis"] = mtb_targets.get("basis")
        block["mtb_heat_fueling_rule"] = mtb_targets.get("rule")
        block["range_selection_rule"] = (
            "Choose within the prescribed range from same-day heat/humidity, actual sweat rate, session consequence, "
            "and gut tolerance; bias upward for hotter, more humid, sweat-heavy, or race-consequence riding. "
            "Do not downshift solely because heat acclimation is high."
        )
        block["heat_context"] = heat_context
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

    if session_type == "cns_recovery":
        established_continuity = duration >= 60
        return {
            "purpose": (
                "Preserve bike-specific continuity under a compromised CNS ceiling without adding structured intensity "
                "or technical consequence."
                if established_continuity
                else "Support nervous-system recovery without turning an impaired-CNS day into a training stimulus."
            ),
            "dose": {
                "duration_min": duration,
                "intensity": intensity,
                "completion": (
                    "10 minutes at 105-115 W, 40 minutes at 120-125 W, then 10 minutes at 100-110 W."
                    if established_continuity
                    else "Rest, mobility, walking, or no more than 20 minutes of genuinely restorative movement."
                ),
                "cap": (
                    "Global RPE 2-3, seated, mechanically quiet; no torque repetitions, standing surges, intervals, or extension."
                    if established_continuity
                    else "Recovery only; stop if the movement does not improve clarity or freshness."
                ),
            },
            "adaptation_hypothesis": (
                "The established low-cost aerobic anchor can maintain bike continuity while the strict intensity and "
                "consequence cap allows sleep-related autonomic strain to absorb."
                if established_continuity
                else "Removing training cost should allow autonomic and cognitive recovery before the next meaningful dose."
            ),
            "execution_rules": [
                "Start only after the written coordination, clarity, illness, airway, pain, and unusual-leg-heaviness gate passes.",
                "Use the indoor trainer with normal cooling; keep cadence natural and mechanics quiet.",
                "Do not convert feeling better into extra watts, extra time, standing work, or intervals.",
            ],
            "expected_result": {
                "garmin_load": "low" if established_continuity else "minimal",
                "rpe": "2-3 globally" if established_continuity else "recovery only",
                "next_day": "normal function with no added autonomic, airway, or local-muscular impairment",
            },
            "stop_rules": [
                "Stop if global RPE exceeds 4, power-to-HR response becomes disproportionate, or coordination or clarity declines.",
                "Stop for airway symptoms, focal or asymmetric pain, altered mechanics, neurological symptoms, or unusual leg heaviness.",
                "Do not continue through a triggered stop rule to complete time or power targets.",
            ],
            "post_session_review_fields": [
                "self_gate_result",
                "pre_session_clarity_0_to_10",
                "actual_duration_min",
                "actual_training_load",
                "actual_global_rpe",
                "power_to_hr_drift",
                "leg_response_onset_distribution_and_resolution",
                "mechanics_stable",
                "airway_symptoms",
                "fluid_ml",
                "stop_rule_outcome",
                "next_morning_response",
            ],
        }

    if session_type == "bike_quality":
        return {
            "purpose": "Rebuild bike-specific engine quality using the current dated Garmin FTP with RPE/HR validation.",
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
                "Use the latest dated Garmin operational FTP with RPE and HR response; historical 222 W P20 is not FTP.",
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
    if _has_complete_session_contract(session):
        return dict(session)
    contracted = {**session, **_contract_for_session(session)}
    contracted["schema_version"] = 3
    contracted["contract_fields"] = list(SESSION_CONTRACT_FIELDS)
    return contracted


def _yellow_base_plan(state: dict) -> dict:
    return {
        "title": "Easy bike continuity",
        "type": "outdoor_bike_optional",
        "duration_min": 60,
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
    status = str(cns.get("status") or "").lower()
    if status == "compromised":
        return {
            "title": "CNS-capped low-aerobic continuity",
            "type": "cns_recovery",
            "duration_min": 60,
            "intensity": "easy",
            "details": [
                "Start only with normal walking coordination, mental clarity at least 8/10, and no illness, airway, focal-pain, or unusual-heavy-leg signal; otherwise rest.",
                "Ride the indoor trainer at 120-125 W and global RPE 2-3, seated and mechanically quiet, with no torque repetitions, standing surges, intervals, or durability extension.",
                "No technical trail riding, speed, jumps, enduro simulation, setup testing, or stacked variables.",
                f"CNS ceiling today: {ceiling}.",
            ],
        }
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
        "duration_min": 60,
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


def _has_complete_session_contract(session: dict) -> bool:
    return bool(
        session.get("schema_version") == 3
        and all(session.get(field) for field in SESSION_CONTRACT_FIELDS)
    )


def _is_bounded_familiar_controlled_skill(session: dict) -> bool:
    session_type = str(session.get("type") or "").lower()
    ceiling_class = str(session.get("garmin_ceiling_class") or "").lower()
    duration = int(session.get("duration_min") or 0)
    return bool(
        _is_mtb_session(session)
        and str(session.get("intensity") or "").lower() == "skill"
        and 0 < duration <= 90
        and _has_complete_session_contract(session)
        and (
            session_type == "mtb_skill_familiar_capped"
            or ceiling_class == "controlled_familiar_skill"
        )
    )


def _is_explicit_bounded_familiar_repeatability(
    session: dict,
    state: dict,
    arbitration: dict,
    plan_source: dict,
) -> bool:
    readiness = state.get("readiness") or {}
    cns = state.get("cns_readiness") or {}
    freshness = state.get("data_freshness") or {}
    dose = session.get("dose") or {}
    max_cycles = dose.get("max_cycles")
    valid_cycle_cap = bool(
        isinstance(max_cycles, (int, float))
        and not isinstance(max_cycles, bool)
        and 1 <= max_cycles <= 2
    )
    explicit_no_expansion = all(
        session.get(field) is False
        for field in (
            "novelty_allowed",
            "open_ended",
            "race_simulation",
            "setup_changes_allowed",
            "setup_test",
        )
    )
    duration = int(session.get("duration_min") or 0)
    return bool(
        plan_source.get("type") == "input_planned_session"
        and arbitration.get("ceiling") == "controlled_familiar_skill_or_aerobic_continuity"
        and arbitration.get("feedback_family") == "recovery"
        and (arbitration.get("acwr") or {}).get("status") == "LOW"
        and readiness.get("readiness_level") == "green"
        and readiness.get("hard_session_guidance") in {"allow", "ok"}
        and str(cns.get("status") or "").lower() == "ready"
        and freshness.get("status") == "current"
        and freshness.get("hard_session_confidence") != "limited"
        and _is_mtb_session(session)
        and str(session.get("type") or "").lower() == "mtb_repeatability_controlled"
        and str(session.get("intensity") or "").lower() == "skill"
        and str(session.get("garmin_ceiling_class") or "").lower()
        == "controlled_familiar_repeatability"
        and 0 < duration <= 150
        and valid_cycle_cap
        and bool(dose.get("hard_cap"))
        and explicit_no_expansion
        and _has_complete_session_contract(session)
    )


def _session_fits_garmin_ceiling(
    session: dict,
    arbitration: dict,
    state: dict,
    plan_source: dict,
) -> bool:
    if _session_is_already_low_consequence(session):
        return True
    if bool(
        arbitration.get("ceiling") == "controlled_familiar_skill_or_aerobic_continuity"
        and _is_bounded_familiar_controlled_skill(session)
    ):
        return True
    return _is_explicit_bounded_familiar_repeatability(
        session,
        state,
        arbitration,
        plan_source,
    )


def _parse_session_datetime(value: object) -> datetime | None:
    if value in {None, ""}:
        return None
    try:
        return datetime.fromisoformat(str(value).strip().replace(" ", "T", 1).replace("Z", "+00:00"))
    except ValueError:
        return None


def _activity_matches_session_modality(session: dict, activity: dict) -> bool:
    activity_text = " ".join(
        str(activity.get(field) or "").lower()
        for field in ("category", "type", "activity_type", "name")
    )
    modality = str(session.get("modality") or "").lower()
    session_type = str(session.get("type") or "").lower()
    if _is_mtb_session(session):
        return "mtb" in activity_text or "mountain_bik" in activity_text
    if "run" in modality or "run" in session_type:
        return "run" in activity_text
    if any(marker in modality or marker in session_type for marker in ("bike", "cycling")):
        return any(marker in activity_text for marker in ("bike", "bik", "cycling"))
    return False


def _planned_session_lifecycle(
    planned_session: dict | None,
    state: dict,
    target_date: date,
) -> dict:
    pre_session = {
        "stance": "pre_session",
        "status": "no_matching_activity_after_contract",
        "source": None,
    }
    if not planned_session:
        return pre_session

    contract_started = _parse_session_datetime(planned_session.get("contract_generated_at"))
    latest_session = state.get("latest_session_evidence") or {}
    activity = latest_session.get("activity") if isinstance(latest_session, dict) else None
    if contract_started is None or not isinstance(activity, dict):
        return pre_session

    activity_started = _parse_session_datetime(activity.get("started_at_local"))
    if activity_started is None:
        return pre_session
    if activity_started.tzinfo is None and contract_started.tzinfo is not None:
        activity_started = activity_started.replace(tzinfo=contract_started.tzinfo)
    elif activity_started.tzinfo is not None and contract_started.tzinfo is None:
        contract_started = contract_started.replace(tzinfo=activity_started.tzinfo)

    activity_date = parse_date(activity.get("date")) or activity_started.date()
    if (
        activity_date != target_date
        or activity_started <= contract_started
        or not _activity_matches_session_modality(planned_session["session"], activity)
    ):
        return pre_session

    return {
        "stance": "post_session_review",
        "status": "matching_same_day_activity_started_after_contract",
        "source": "current_state.latest_session_evidence.activity",
        "contract_generated_at": planned_session.get("contract_generated_at"),
        "contract_status": planned_session.get("contract_status"),
        "activity_id": activity.get("activity_id"),
        "activity_started_at_local": activity.get("started_at_local"),
        "rule": (
            "Preserve the coach-authored contract as executed intent. Post-session Garmin, "
            "readiness, and activity evidence inform review and the next decision; they do not "
            "retroactively rewrite the prescription."
        ),
    }


def _session_summary(session: dict) -> dict:
    return {
        "title": session.get("title"),
        "type": session.get("type"),
        "duration_min": session.get("duration_min"),
        "intensity": session.get("intensity"),
    }


def _is_no_training_session(session: dict) -> bool:
    session_type = str(session.get("type") or "").lower()
    duration = session.get("duration_min")
    return session_type in {"scheduled_rest", "calendar_rest", "rest"} or (
        isinstance(duration, (int, float)) and duration <= 0
    )


def _venue_token(value: object) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").split())


def _environment_venue_scope(environment: dict) -> tuple[set[str], set[str]]:
    """Return the validated source scope, never mutable coaching-decision aliases.

    The local endpoint is a direct Bukit Kiara/TTDI evidence product. Even if a
    decision block or future config accidentally advertises another alias, planning
    must not transfer this evidence to Denai Peladang or another venue.
    """
    latest = environment.get("last_known_good") if isinstance(environment, dict) else {}
    latest = latest if isinstance(latest, dict) else {}
    scope = latest.get("scope")
    scope = scope if isinstance(scope, dict) else {}
    advertised_keys = {
        _venue_token(value).replace(" ", "_")
        for value in (scope.get("venue_keys") or BUKIT_KIARA_VENUE_KEYS)
        if value
    }
    keys = advertised_keys.intersection(BUKIT_KIARA_VENUE_KEYS)
    return keys or set(BUKIT_KIARA_VENUE_KEYS), set(BUKIT_KIARA_VENUE_ALIASES)


def _session_explicitly_targets_environment_venue(
    session: dict,
    environment: dict,
) -> bool:
    """Scope the TTDI proxy only to an explicitly named Bukit Kiara session."""
    keys, aliases = _environment_venue_scope(environment)
    action = session.get("action_identity")
    action = action if isinstance(action, dict) else {}
    # A canonical action identity is authoritative. Conflicting prose must not turn
    # a DP session into a Kiara session (or vice versa).
    action_key = action.get("venue_key")
    if action_key:
        return _venue_token(action_key).replace(" ", "_") in keys
    session_key = session.get("venue_key")
    if session_key:
        return _venue_token(session_key).replace(" ", "_") in keys

    venue = session.get("venue") or session.get("location")
    if isinstance(venue, dict):
        venue_key = venue.get("key") or venue.get("venue_key")
        if venue_key:
            return _venue_token(venue_key).replace(" ", "_") in keys
        venue_values = (venue.get("name"), venue.get("label"))
    else:
        venue_values = (venue,)
    return any(_venue_token(value) in aliases for value in venue_values if value)


def _parse_environment_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _environment_timezone(environment: dict) -> ZoneInfo:
    latest = environment.get("last_known_good") or {}
    scope = latest.get("scope") if isinstance(latest, dict) else {}
    scope = scope if isinstance(scope, dict) else {}
    location = scope.get("location")
    location = location if isinstance(location, dict) else {}
    timezone_name = location.get("timezone") or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(str(timezone_name))
    except (KeyError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE)


def _observation_matches_target_date(environment: dict, target_date: date) -> bool:
    if parse_date(environment.get("date")) != target_date:
        return False
    latest = environment.get("last_known_good") or {}
    observation = latest.get("observation") if isinstance(latest, dict) else {}
    observation = observation if isinstance(observation, dict) else {}
    observed = _parse_environment_datetime(
        observation.get("observed_at_utc") or observation.get("observed_at")
    )
    return bool(observed and observed.astimezone(_environment_timezone(environment)).date() == target_date)


def _validated_forecast_windows(environment: dict, target_date: date) -> list[dict]:
    """Return only exact, structured forecast windows for the requested local date.

    `target_day: Tomorrow` is presentation text and is deliberately ignored. A
    retained response cannot therefore roll "tomorrow" forward after a failed
    refresh.
    """
    latest = environment.get("last_known_good") or {}
    ride_windows = latest.get("ride_windows") if isinstance(latest, dict) else {}
    ride_windows = ride_windows if isinstance(ride_windows, dict) else {}
    timezone = _environment_timezone(environment)
    valid: list[dict] = []
    for name in ("morning", "afternoon"):
        window = ride_windows.get(name)
        if not isinstance(window, dict):
            continue
        weather = window.get("weather_forecast")
        weather = weather if isinstance(weather, dict) else {}
        start = _parse_environment_datetime(
            weather.get("start_at_utc") or window.get("start_at_utc")
        )
        end = _parse_environment_datetime(
            weather.get("end_at_utc") or window.get("end_at_utc")
        )
        normalized_date = parse_date(window.get("target_date"))
        weather_date = parse_date(weather.get("target_date"))
        modeled_window = _modeled_interval_minutes(window.get("modeled_session"))
        modeled_weather = _modeled_interval_minutes(weather.get("modeled_session"))
        start_local = start.astimezone(timezone) if start is not None else None
        end_local = end.astimezone(timezone) if end is not None else None
        timestamp_interval = (
            (start_local.hour * 60 + start_local.minute, end_local.hour * 60 + end_local.minute)
            if start_local is not None and end_local is not None
            else None
        )
        modeled_duration_seconds = (
            (modeled_window[1] - modeled_window[0]) * 60
            if modeled_window is not None
            else None
        )
        if (
            start is None
            or end is None
            or end <= start
            or weather.get("available") is False
            or normalized_date != target_date
            or weather_date not in {None, target_date}
            or start_local.date() != target_date
            or end_local.date() != target_date
            or modeled_window is None
            or modeled_weather is None
            or modeled_window != modeled_weather
            or modeled_window != timestamp_interval
            or (end - start).total_seconds() != modeled_duration_seconds
        ):
            continue
        valid.append({"name": name, **window})
    return valid


def _modeled_interval_minutes(value: object) -> tuple[int, int] | None:
    text = str(value or "").strip().replace("–", "-")
    parts = [part.strip() for part in text.split("-")]
    if len(parts) != 2:
        return None
    try:
        start = datetime.strptime(parts[0], "%H:%M").time()
        end = datetime.strptime(parts[1], "%H:%M").time()
    except ValueError:
        return None
    start_min = start.hour * 60 + start.minute
    end_min = end.hour * 60 + end.minute
    return (start_min, end_min) if end_min > start_min else None


def _planned_window_selector(
    session: dict,
    target_date: date,
    timezone: ZoneInfo,
) -> tuple[str | None, datetime | None, str | None]:
    """Parse a named slot or an explicit local start without guessing by hour."""
    action = session.get("action_identity")
    action = action if isinstance(action, dict) else {}
    values = [
        action.get("planned_start_at_local"),
        action.get("start_at_local"),
        action.get("time_of_day"),
        session.get("planned_start_at_local"),
        session.get("start_at_local"),
        session.get("planned_start_time_local"),
        session.get("time_of_day"),
    ]
    for value in values:
        if not value:
            continue
        token = _venue_token(value)
        if token in {"morning", "am"}:
            return "morning", None, "explicit_named_window"
        if token in {"afternoon", "pm"}:
            return "afternoon", None, "explicit_named_window"

        text = str(value).strip()
        try:
            local_time = datetime.strptime(text, "%H:%M").time()
        except ValueError:
            local_time = None
        if local_time is not None:
            return (
                None,
                datetime.combine(target_date, local_time, tzinfo=timezone),
                "explicit_local_clock",
            )

        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            continue
        local = (
            parsed.replace(tzinfo=timezone)
            if parsed.tzinfo is None
            else parsed.astimezone(timezone)
        )
        basis = (
            "explicit_local_datetime"
            if local.date() == target_date
            else "explicit_datetime_wrong_target_date"
        )
        return None, local, basis
    return None, None, None


def _select_forecast_windows(
    windows: list[dict],
    session: dict,
    environment: dict,
    target_date: date,
) -> tuple[list[dict], list[dict], str | None, str]:
    timezone = _environment_timezone(environment)
    window_name, planned_start, time_basis = _planned_window_selector(
        session,
        target_date,
        timezone,
    )
    if window_name is not None:
        candidates = [window for window in windows if window.get("name") == window_name]
        return candidates, [], time_basis, "named_window_planning_context"
    if planned_start is None:
        return windows, [], None, "time_unspecified"
    if planned_start.date() != target_date:
        return [], [], time_basis, "wrong_target_date"

    point_candidates: list[dict] = []
    for window in windows:
        weather = window.get("weather_forecast")
        weather = weather if isinstance(weather, dict) else {}
        start = _parse_environment_datetime(weather.get("start_at_utc"))
        end = _parse_environment_datetime(weather.get("end_at_utc"))
        if start is None or end is None:
            continue
        start_local = start.astimezone(timezone)
        end_local = end.astimezone(timezone)
        if start_local <= planned_start < end_local:
            point_candidates.append(window)

    if not point_candidates:
        return [], [], time_basis, "exact_start_outside_modeled_window"
    duration_min = _explicit_session_duration_min(session)
    if duration_min is None:
        return (
            point_candidates,
            [],
            time_basis,
            "exact_interval_duration_missing",
        )
    planned_end = planned_start + timedelta(minutes=duration_min)
    applicable: list[dict] = []
    for window in point_candidates:
        weather = window.get("weather_forecast")
        weather = weather if isinstance(weather, dict) else {}
        end = _parse_environment_datetime(weather.get("end_at_utc"))
        if end is not None and planned_end <= end.astimezone(timezone):
            applicable.append(window)
    if not applicable:
        return [], [], time_basis, "exact_interval_outside_modeled_window"
    return applicable, applicable, time_basis, "exact_interval_contained"


def _explicit_session_duration_min(session: dict) -> float | None:
    action = session.get("action_identity")
    action = action if isinstance(action, dict) else {}
    dose = session.get("dose")
    dose = dose if isinstance(dose, dict) else {}
    values = (
        action.get("planned_duration_min"),
        action.get("duration_min"),
        session.get("planned_duration_min"),
        session.get("duration_min"),
        dose.get("total_duration_min"),
        dose.get("duration_min"),
    )
    for value in values:
        if isinstance(value, bool):
            continue
        try:
            duration = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(duration) and duration > 0:
            return duration
    return None


def _environment_evaluation_time(environment: dict) -> datetime | None:
    generated = _parse_environment_datetime(environment.get("generated_at"))
    if generated is not None:
        return generated
    attempt = environment.get("latest_attempt")
    attempt = attempt if isinstance(attempt, dict) else {}
    return _parse_environment_datetime(attempt.get("attempted_at"))


def _requires_exact_window_recheck(environment: dict) -> bool:
    latest = environment.get("last_known_good")
    latest = latest if isinstance(latest, dict) else {}
    identity = latest.get("identity")
    identity = identity if isinstance(identity, dict) else {}
    version = str(identity.get("schema_version") or "")
    try:
        major, minor, *_ = (int(part) for part in version.split("."))
    except (TypeError, ValueError):
        return False
    return (major, minor) >= (1, 6)


def _window_recheck_state(environment: dict, window: dict) -> str:
    """Distinguish an issued forecast from a post-recheck successful refresh."""
    timezone = _environment_timezone(environment)
    raw_recheck = window.get("recheck_at_local")
    if not raw_recheck:
        return (
            "invalid_recheck_time"
            if _requires_exact_window_recheck(environment)
            else "not_provided"
        )
    recheck = _parse_environment_datetime(raw_recheck)
    if recheck is None:
        return "invalid_recheck_time"
    recheck = recheck.astimezone(timezone)
    evaluated = _environment_evaluation_time(environment)
    if evaluated is None:
        return "retained_recheck_required"
    evaluated = evaluated.astimezone(timezone)
    if evaluated < recheck:
        return "planning_only_recheck_pending"

    attempt = environment.get("latest_attempt")
    attempt = attempt if isinstance(attempt, dict) else {}
    attempted_at = _parse_environment_datetime(attempt.get("attempted_at"))
    if (
        str(attempt.get("status") or "").lower() == "success"
        and attempted_at is not None
        and attempted_at.astimezone(timezone) >= recheck
    ):
        return "recheck_satisfied"
    return "retained_recheck_required"


def _selected_recheck_state(environment: dict, windows: list[dict]) -> str:
    states = {_window_recheck_state(environment, window) for window in windows}
    if "retained_recheck_required" in states or "invalid_recheck_time" in states:
        return "retained_recheck_required"
    if "planning_only_recheck_pending" in states:
        return "planning_only_recheck_pending"
    if states == {"recheck_satisfied"}:
        return "recheck_satisfied"
    return "not_provided"


def _compact_forecast_window(window: dict, environment: dict) -> dict:
    weather = window.get("weather_forecast")
    weather = weather if isinstance(weather, dict) else {}
    thunderstorm = weather.get("thunderstorm")
    thunderstorm = thunderstorm if isinstance(thunderstorm, dict) else {}
    particle = window.get("particle_forecast")
    particle = particle if isinstance(particle, dict) else {}
    return {
        "name": window.get("name"),
        "target_date": window.get("target_date"),
        "ride_window": window.get("ride_window"),
        "modeled_session": window.get("modeled_session"),
        "current_conditions_applicable": bool(window.get("current_conditions_applicable")),
        "recheck": window.get("recheck"),
        "recheck_at_local": window.get("recheck_at_local"),
        "recheck_state": _window_recheck_state(environment, window),
        "confidence": window.get("confidence"),
        "particle_forecast": {
            "available": particle.get("available"),
            "validation_state": particle.get("validation_state"),
            "mean_range_pm2_5_ug_m3": particle.get("mean_range_pm2_5_ug_m3"),
            "upper_peak_pm2_5_ug_m3": particle.get("upper_peak_pm2_5_ug_m3"),
        },
        "weather_forecast": {
            "available": weather.get("available"),
            "start_at_utc": weather.get("start_at_utc"),
            "end_at_utc": weather.get("end_at_utc"),
            "target_date": weather.get("target_date"),
            "modeled_session": weather.get("modeled_session"),
            "rain_used_for_comparison": weather.get("rain_used_for_comparison"),
            "rain_signal": weather.get("rain_signal"),
            "thunderstorm": {
                key: thunderstorm.get(key)
                for key in ("level", "rank", "label", "basis", "source", "used_for_decision")
            },
        },
    }


def _environment_plan_applicability(
    environment: dict,
    target_date: date,
    session: dict,
) -> dict:
    action = session.get("action_identity")
    action = action if isinstance(action, dict) else {}
    unresolved_options: list[str] = []
    if not action.get("venue_key"):
        raw_options = session.get("venue_options")
        if isinstance(raw_options, (list, tuple)):
            unresolved_options = [_venue_token(value) for value in raw_options if value]
        venue_text = _venue_token(session.get("venue") or session.get("location"))
        if (
            any(alias in venue_text for alias in ("bukit kiara", "kiara", "ttdi"))
            and any(alias in venue_text for alias in ("denai peladang", " dp", "dp "))
        ):
            unresolved_options = ["bukit kiara", "denai peladang"]
    normalized_options = " ".join(unresolved_options)
    if unresolved_options and "kiara" in normalized_options and (
        "denai peladang" in normalized_options or "dp" in normalized_options
    ):
        return {
            "status": "multi_venue_choice_unresolved",
            "target_date": target_date.isoformat(),
            "reason": "Kiara and Denai Peladang require separate environment branches; choose the venue before applying an automatic ceiling.",
            "can_promote_training": False,
            "venue_applicability": {
                "bukit_kiara": "endpoint_evidence_available_subject_to_date",
                "denai_peladang": "venue_specific_evidence_unavailable",
            },
            "windows": [],
        }
    venue_match = _session_explicitly_targets_environment_venue(session, environment)
    if not venue_match:
        return {
            "status": "venue_specific_evidence_unavailable",
            "target_date": target_date.isoformat(),
            "reason": "The endpoint is direct Bukit Kiara/TTDI evidence; it cannot clear or close Denai Peladang or another venue.",
            "can_promote_training": False,
            "windows": [],
        }

    windows = _validated_forecast_windows(environment, target_date)
    if not windows:
        if _observation_matches_target_date(environment, target_date):
            return {
                "status": "current_observation_applicable",
                "target_date": target_date.isoformat(),
                "reason": "The normalized observation date matches the plan date.",
                "can_promote_training": False,
                "windows": [],
            }
        return {
            "status": "forecast_unavailable_recheck",
            "target_date": target_date.isoformat(),
            "reason": "No exact structured Bukit Kiara forecast window matches this plan date; refresh closer to departure.",
            "can_promote_training": False,
            "windows": [],
        }

    safety_windows, applicable_windows, time_basis, interval_state = (
        _select_forecast_windows(
            windows,
            session,
            environment,
            target_date,
        )
    )
    recheck_state = _selected_recheck_state(environment, safety_windows)
    comparison = ((environment.get("last_known_good") or {}).get("ride_windows") or {}).get(
        "comparison"
    ) or {}
    if interval_state == "time_unspecified":
        applicability_status = "forecast_windows_time_unspecified"
        applicability_reason = (
            "Morning and afternoon are separate forecast candidates; no window was auto-selected."
        )
    elif interval_state == "named_window_planning_context":
        applicability_status = "forecast_named_window_planning_context"
        applicability_reason = (
            "The named window is planning and safety context only; an exact action interval is required for normal applicability."
        )
    elif interval_state == "exact_interval_duration_missing":
        applicability_status = "forecast_exact_interval_duration_missing"
        applicability_reason = (
            "The planned start is inside a modeled window, but explicit session duration is missing; the window cannot clear the full action."
        )
    elif interval_state in {
        "wrong_target_date",
        "exact_start_outside_modeled_window",
        "exact_interval_outside_modeled_window",
    }:
        applicability_status = "planned_interval_outside_published_window_recheck"
        applicability_reason = (
            "The complete proposed local action interval is not contained in an exact published modeled window on this date."
        )
    elif recheck_state == "planning_only_recheck_pending":
        applicability_status = "forecast_planning_only_recheck_pending"
        applicability_reason = (
            "The exact window is planning context only until its stated recheck; it cannot clear the ride."
        )
    elif recheck_state == "retained_recheck_required":
        applicability_status = "retained_recheck_required"
        applicability_reason = (
            "The stated recheck has passed without a successful refresh at or after that time; retained evidence cannot clear the ride."
        )
    else:
        applicability_status = "forecast_window_applicable"
        applicability_reason = (
            "An exact structured forecast window matches the plan date and planned start."
        )

    return {
        "status": applicability_status,
        "target_date": target_date.isoformat(),
        "reason": applicability_reason,
        "time_basis": time_basis,
        "action_interval_state": interval_state,
        "recheck_state": recheck_state,
        "can_promote_training": False,
        "current_pm_used_for_clearance": False,
        "preferred_window": comparison.get("preferred_window"),
        "preferred_window_relative_only": comparison.get("relative_only"),
        "ride_approval": comparison.get("ride_approval"),
        "preferred_window_used_for_selection": False,
        "windows": [
            _compact_forecast_window(window, environment) for window in windows
        ],
        "candidate_window_names": [
            window.get("name") for window in safety_windows
        ],
        "applicable_window_names": [
            window.get("name") for window in applicable_windows
        ],
    }


def _is_definitely_indoor_or_rest(session: dict) -> bool:
    if _is_no_training_session(session):
        return True
    modality = str(session.get("modality") or "").lower()
    session_type = str(session.get("type") or "").lower()
    return bool(
        "indoor" in modality
        or session_type in {
            "cns_recovery",
            "environment_indoor_continuity",
            "scheduled_rest",
        }
        or ("indoor" in session_type and "outdoor" not in session_type)
    )


def _compact_environment_input(
    state: dict,
    target_date: date,
    session: dict,
) -> dict | None:
    environment = state.get("environment_evidence")
    if not isinstance(environment, dict) or not environment:
        return None
    latest = environment.get("last_known_good")
    latest = latest if isinstance(latest, dict) else {}
    identity = latest.get("identity")
    identity = identity if isinstance(identity, dict) else {}
    observation = latest.get("observation")
    observation = observation if isinstance(observation, dict) else {}
    provenance = latest.get("provenance")
    provenance = provenance if isinstance(provenance, dict) else {}
    scope = latest.get("scope")
    scope = scope if isinstance(scope, dict) else {}
    attempt = environment.get("latest_attempt")
    attempt = attempt if isinstance(attempt, dict) else {}
    decision = environment.get("decision")
    decision = decision if isinstance(decision, dict) else {}
    return {
        "status": environment.get("status"),
        "date": environment.get("date"),
        "source": {
            "endpoint": (environment.get("source") or {}).get("endpoint"),
            "fallback": (environment.get("source") or {}).get("fallback"),
        },
        "freshness": environment.get("freshness"),
        "latest_attempt": {
            "attempted_at": attempt.get("attempted_at"),
            "status": attempt.get("status"),
            "error": attempt.get("error"),
            "evidence_id": attempt.get("evidence_id"),
        },
        "evidence": {
            "evidence_id": identity.get("evidence_id"),
            "schema_version": identity.get("schema_version"),
            "observed_at_utc": observation.get("observed_at_utc"),
            "pm2_5_ug_m3": observation.get("pm2_5_ug_m3"),
            "sensor": provenance.get("sensor"),
        },
        "scope": {
            "role": scope.get("role"),
            "venue_keys": scope.get("venue_keys") or ["bukit_kiara"],
            "location": scope.get("location"),
            "transfer_to_unlisted_venues": False,
        },
        "decision": {
            key: decision.get(key)
            for key in (
                "gate",
                "severity",
                "reason",
                "reason_codes",
                "current_pm2_5_ug_m3",
                "forecast_lower_pm2_5_ug_m3",
                "forecast_upper_pm2_5_ug_m3",
                "forecast_confidence",
                "recheck_minutes",
                "decision_role",
                "can_promote_training",
            )
        },
        "plan_applicability": _environment_plan_applicability(
            environment,
            target_date,
            session,
        ),
        "guardrail": environment.get("guardrail"),
    }


def _environment_indoor_fallback(environment: dict, source_session: dict) -> dict:
    decision = environment.get("decision") or {}
    source_duration = source_session.get("duration_min")
    try:
        source_duration = int(source_duration)
    except (TypeError, ValueError):
        source_duration = 60
    total_duration = max(0, min(60, source_duration))
    if total_duration < 20:
        return {
            "title": "Environment hold — no planned outdoor exercise",
            "type": "scheduled_rest",
            "duration_min": 0,
            "intensity": "recovery",
            "details": [
                "The venue-matched outdoor candidate is closed by current environmental evidence.",
                "Do not replace a very short planned exposure with a larger automatic training dose.",
            ],
        }
    warm_up_min = 10 if total_duration >= 45 else 5
    cool_down_min = 10 if total_duration >= 45 else 5
    main_min = total_duration - warm_up_min - cool_down_min
    return {
        "title": "Environment-capped indoor bike continuity",
        "type": "environment_indoor_continuity",
        "modality": "bike_indoor",
        "duration_min": total_duration,
        "intensity": "easy",
        "density_cost": "low",
        "bike_touch_status": "normal",
        "schema_version": 3,
        "contract_fields": list(SESSION_CONTRACT_FIELDS),
        "purpose": (
            "Preserve Clayton's established bike-specific continuity dose while avoiding a "
            "deterministic high-ventilation Bukit Kiara exposure breach."
        ),
        "dose": {
            "total_duration_min": total_duration,
            "warm_up": f"{warm_up_min} minutes at 105-115 W.",
            "main": f"{main_min} minutes at 120-130 W / global RPE 2-3.",
            "cool_down": f"{cool_down_min} minutes easy at 100-110 W.",
            "environment_gate": decision.get("gate"),
        },
        "adaptation_hypothesis": (
            "A familiar low-cost aerobic touch maintains bike continuity without the inhaled "
            "particulate dose of prolonged or high-ventilation outdoor MTB."
        ),
        "execution_rules": [
            "Use the indoor trainer only after Clayton confirms materially cleaner indoor air; this outdoor source cannot establish indoor air quality.",
            "Stay seated and mechanically quiet with no torque repetitions, standing surges, intervals, or extension.",
            "Use current airway and eye symptoms as an independent admission gate.",
        ],
        "expected_result": {
            "rpe": "global RPE 2-3",
            "load": "low-cost bike continuity",
            "next_day": "normal breathing, cognition, and legs without added autonomic cost",
        },
        "stop_rules": [
            "Do not start indoors unless materially cleaner indoor air is athlete-confirmed.",
            "Stop for eye, nose, throat, cough, wheeze, chest, neurological, focal-pain, or altered-mechanics symptoms.",
            "Stop or downshift if global RPE exceeds 4 or heart rate becomes disproportionate to power.",
        ],
        "post_session_review_fields": [
            "indoor_air_context",
            "airway_and_eye_symptoms_before_during_after",
            "global_rpe",
            "heart_rate_drift",
            "fluid_consumed",
            "stop_rule_outcome",
        ],
    }


def _is_high_consequence_outdoor_session(session: dict) -> bool:
    if _is_definitely_indoor_or_rest(session):
        return False
    if _is_mtb_session(session):
        return True
    text = " ".join(
        str(session.get(key) or "").lower()
        for key in ("type", "modality", "intensity", "title")
    )
    return "outdoor" in text and any(
        marker in text
        for marker in ("hard", "technical", "quality", "race", "interval", "high")
    )


def _window_has_structured_thunderstorm_hold(window: dict) -> bool:
    weather = window.get("weather_forecast")
    weather = weather if isinstance(weather, dict) else {}
    thunderstorm = weather.get("thunderstorm")
    thunderstorm = thunderstorm if isinstance(thunderstorm, dict) else {}
    return bool(
        str(thunderstorm.get("level") or "").lower() in {"likely", "severe"}
        and thunderstorm.get("used_for_decision") is True
    )


def _environment_constraint_for_session(
    effective: dict,
    original: dict,
    state: dict,
    *,
    target_date: date,
    sabbath_exception: dict | None = None,
    recurring_scheduled_rest: dict | None = None,
) -> tuple[dict, dict] | None:
    environment = state.get("environment_evidence")
    if not isinstance(environment, dict) or not environment:
        return None
    readiness = state.get("readiness") or {}
    cns_status = str((state.get("cns_readiness") or {}).get("status") or "").lower()
    if (
        str(readiness.get("readiness_level") or "").lower() == "red"
        or str(readiness.get("hard_session_guidance") or "").lower() == "avoid"
        or cns_status in {"impaired", "compromised"}
        or _is_definitely_indoor_or_rest(effective)
        or not _session_explicitly_targets_environment_venue(original, environment)
    ):
        return None

    decision = environment.get("decision") or {}
    freshness = environment.get("freshness") or {}
    freshness_state = str(freshness.get("state") or environment.get("status") or "").lower()
    current_applies = _observation_matches_target_date(environment, target_date)
    gate = str(decision.get("gate") or "")
    current_pm: float | None = None
    forecast_window_names: list[str] = []
    forecast_recheck_state: str | None = None
    reason = decision.get("reason")

    current_breach = False
    if current_applies and freshness_state not in {
        "expired",
        "unknown",
        "historical_unavailable",
    }:
        raw_current_pm = decision.get("current_pm2_5_ug_m3")
        try:
            current_pm = float(raw_current_pm) if raw_current_pm is not None else None
        except (TypeError, ValueError):
            current_pm = None
        fresh_or_retained = freshness_state in {"current", "retained_current"}
        stale = freshness_state == "stale"
        all_outdoor_breach = gate in {
            "close_all_planned_outdoor_exercise",
            "outdoor_training_closed",
        } or bool(
            (fresh_or_retained or stale)
            and current_pm is not None
            and current_pm > 150
        )
        mtb_breach = gate in {
            "close_mtb_prolonged_endurance_high_ventilation",
            "retained_high_ventilation_closure_pending_refresh",
            "outdoor_mtb_endurance_high_ventilation_closed",
            "retained_outdoor_mtb_endurance_high_ventilation_closed_pending_refresh",
        } or bool(
            (fresh_or_retained or stale)
            and current_pm is not None
            and current_pm >= 51
        )
        current_breach = all_outdoor_breach or (
            mtb_breach and _is_mtb_session(original)
        )

    if not current_breach:
        # A later window never uses the current reading as clearance. Current high
        # evidence may retain a restriction; otherwise use only the exact window's
        # structured safety evidence.
        windows = _validated_forecast_windows(environment, target_date)
        if not windows or not _is_high_consequence_outdoor_session(original):
            return None
        safety_windows, _, time_basis, _ = _select_forecast_windows(
            windows,
            original,
            environment,
            target_date,
        )
        if not safety_windows:
            return None
        thunder_windows = [
            window
            for window in safety_windows
            if _window_has_structured_thunderstorm_hold(window)
        ]
        # With no planned time, do not choose the favourable window. A deterministic
        # hold is warranted only when every published option carries the structured
        # likely/severe signal; otherwise planning surfaces both for a later choice.
        thunder_applies = bool(thunder_windows) and (
            time_basis is not None or len(thunder_windows) == len(safety_windows)
        )
        if not thunder_applies:
            return None
        gate = "structured_thunderstorm_hold"
        forecast_window_names = [str(window.get("name")) for window in thunder_windows]
        forecast_recheck_state = _selected_recheck_state(
            environment,
            thunder_windows,
        )
        reason = (
            "Structured likely/severe thunderstorm evidence applies to the exact-date "
            "Bukit Kiara forecast window; hold or move this high-consequence outdoor session."
        )

    if sabbath_exception is not None:
        replacement = _scheduled_rest_plan(
            recurring_scheduled_rest
            or {
                "label": "Sunday Sabbath",
                "reason": "The authorized race-event exposure is unavailable; the exception does not authorize substitute training.",
            }
        )
    else:
        replacement = _environment_indoor_fallback(environment, effective)
    latest = environment.get("last_known_good") or {}
    identity = latest.get("identity") if isinstance(latest.get("identity"), dict) else {}
    constraint = {
        "source": "environment_evidence",
        "reason": reason
        or "Bukit Kiara environmental evidence closes the planned outdoor exposure.",
        "gate": gate,
        "evidence_id": identity.get("evidence_id"),
        "freshness": {
            "state": freshness.get("state"),
            "age_seconds": freshness.get("age_seconds"),
            "expired_after_seconds": freshness.get("expired_after_seconds"),
        },
        "current_pm2_5_ug_m3": current_pm,
        "forecast_window_names": forecast_window_names,
        "forecast_recheck_state": forecast_recheck_state,
        "target_date": target_date.isoformat(),
        "decision_role": "venue_scoped_outdoor_downshift_only",
        "original_session": _session_summary(effective),
        "effective_session": _session_summary(replacement),
    }
    return replacement, constraint


def _is_existing_lower_recovery_ceiling(session: dict, replacement: dict) -> bool:
    """Do not let a CNS safety replacement increase a deliberate recovery-only dose."""
    session_type = str(session.get("type") or "").lower()
    if _is_mtb_session(session) or not (
        session_type in {"recovery", "cns_recovery", "bike_recovery_primer"}
        or "recovery" in session_type
    ):
        return False
    duration = session.get("duration_min")
    replacement_duration = replacement.get("duration_min")
    return bool(
        isinstance(duration, (int, float))
        and isinstance(replacement_duration, (int, float))
        and duration <= replacement_duration
    )


def _apply_session_constraints(
    session: dict,
    state: dict,
    arbitration: dict,
    plan_source: dict,
    *,
    target_date: date,
    sabbath_exception: dict | None = None,
    recurring_scheduled_rest: dict | None = None,
) -> tuple[dict, list[dict]]:
    """Apply safety ceilings after resolving the source session but before prediction."""
    original = dict(session)
    effective = dict(session)
    constraints: list[dict] = []
    cns = state.get("cns_readiness") or {}
    cns_status = str(cns.get("status") or "").lower()
    freshness = state.get("data_freshness") or {}
    readiness = state.get("readiness") or {}
    physical_red = (
        str(readiness.get("readiness_level") or "").lower() == "red"
        or str(readiness.get("hard_session_guidance") or "").lower() == "avoid"
    )

    cns_replacement = _cns_recovery_plan(cns)
    if (
        cns_status in {"impaired", "compromised"}
        and not physical_red
        and not _is_no_training_session(effective)
        and not _is_existing_lower_recovery_ceiling(effective, cns_replacement)
    ):
        replacement = cns_replacement
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
        and not _session_fits_garmin_ceiling(
            effective,
            arbitration,
            state,
            plan_source,
        )
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

    environment_resolution = _environment_constraint_for_session(
        effective,
        original,
        state,
        target_date=target_date,
        sabbath_exception=sabbath_exception,
        recurring_scheduled_rest=recurring_scheduled_rest,
    )
    if environment_resolution is not None:
        effective, environment_constraint = environment_resolution
        constraints.append(environment_constraint)

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


def _apply_adaptive_programming_intent(
    session: dict,
    state: dict,
    plan_source: dict,
) -> tuple[dict, dict | None]:
    """Attach the persistent progression decision before hard safety constraints.

    Explicit coach-authored contracts are never rewritten here. A non-explicit
    meaningful candidate is converted to the established low-cost continuity dose
    when the adaptive weekly cost budget is already spent.
    """

    adaptive = state.get("adaptive_training") or {}
    if not adaptive:
        return session, None
    decision = adaptive.get("progression_decision") or {}
    block = adaptive.get("roadmap_block") or {}
    budget = adaptive.get("weekly_budget") or {}
    progression_tracks = adaptive.get("progression_tracks") or {}
    explicit = plan_source.get("type") == "input_planned_session"
    effective = dict(session)
    metadata = {
        "adaptive_state_basis_date": adaptive.get("date"),
        "roadmap_block_id": f"{block.get('start_date') or 'unknown'}:{block.get('program_mode') or 'unknown'}",
        "roadmap_block": block.get("label"),
        "program_action": decision.get("program_action"),
        "progression_lever": decision.get("active_lever"),
        "progression_track": (
            "endurance"
            if decision.get("active_lever") in {"bike_specific_continuity", "endurance_duration", "frequency"}
            else decision.get("active_lever")
        ),
        "planned_step": (
            (progression_tracks.get("endurance") or {}).get("current_rung")
            if decision.get("active_lever") in {"bike_specific_continuity", "endurance_duration", "frequency"}
            else None
        ),
    }
    effective["adaptive_programming"] = {
        **(effective.get("adaptive_programming") or {}),
        **metadata,
        "explicit_contract_preserved": explicit,
    }

    remaining = int(budget.get("meaningful_cost_days_remaining") or 0)
    meaningful = effective.get("density_cost") == "meaningful" or str(
        effective.get("intensity") or ""
    ).lower() in {"hard", "moderate_hard"}
    event = str(effective.get("type") or "").lower() in {"race", "event_race", "official_practice"}
    if meaningful and remaining <= 0 and not explicit and not event:
        effective = {
            "title": "Adaptive low-cost bike continuity",
            "type": "outdoor_bike_optional",
            "modality": "bike_indoor_or_low_consequence_outdoor",
            "duration_min": 60,
            "intensity": "easy",
            "density_cost": "low",
            "bike_touch_status": "normal",
            "details": [
                "Use the established 60-minute 120-130 W / global RPE 2-3 indoor anchor, or an equivalent low-consequence easy ride.",
                "The weekly meaningful-cost budget is spent; do not hide tempo, torque, speed, or a technical stress test inside this touch.",
            ],
            "adaptive_programming": {
                **metadata,
                "budget_disposition": "meaningful_candidate_reflowed_to_low_cost",
                "explicit_contract_preserved": False,
            },
        }
        return effective, {
            "source": "adaptive_training",
            "reason": "meaningful_cost_budget_spent",
            "action": "reflowed_non_explicit_candidate_to_low_cost_continuity",
        }
    if meaningful and remaining <= 0 and explicit:
        return effective, {
            "source": "adaptive_training",
            "reason": "explicit_contract_exceeds_remaining_meaningful_budget",
            "action": "preserved_for_head_coach_resolution",
        }
    return effective, None


def _gym_block(
    state: dict,
    scheduled_rest: dict | None = None,
    sabbath_exception: dict | None = None,
) -> dict:
    if scheduled_rest:
        if sabbath_exception:
            return {
                "status": "skip",
                "details": [
                    "The exact-date Sabbath exception authorizes only its named session; no gym or other training may be added."
                ],
            }
        return {
            "status": "skip",
            "details": [
                f"{scheduled_rest.get('label', 'Scheduled rest')} is a no-exercise day."
            ],
        }
    level = state.get("readiness", {}).get("readiness_level")
    if level == "red":
        return {"status": "skip", "details": ["Skip gym loading today; keep mobility only."]}
    adaptive = state.get("adaptive_training") or {}
    mode = (adaptive.get("roadmap_block") or {}).get("program_mode")
    meaningful_remaining = ((adaptive.get("weekly_budget") or {}).get("meaningful_cost_days_remaining"))
    if mode in {"absorption", "taper", "race_recovery_transition", "event_practice", "event_race"} or meaningful_remaining == 0:
        return {
            "status": "skip_loading",
            "details": [
                "Do not add gym loading to the current adaptive density budget; mobility is optional and must not create fatigue."
            ],
        }
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
    recurring_scheduled_rest = scheduled_rest_rule(full_context, target_date)
    replacement_sabbath = replacement_sabbath_rule(root, full_context, target_date)
    scheduled_rest = replacement_sabbath or recurring_scheduled_rest
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
    sabbath_exception = validate_sabbath_exception(
        planned_session,
        target_date,
        recurring_scheduled_rest,
    )
    enforce_scheduled_rest = bool(scheduled_rest and sabbath_exception is None)
    weekly_session = load_weekly_session(root, target_date)
    selected_session = planned_session or weekly_session
    plan_source = {"type": "today_plan", "path": "snapshots/today_plan.json"}
    garmin_arbitration = build_garmin_arbitration(state)
    session_lifecycle = _planned_session_lifecycle(planned_session, state, target_date)
    post_session_review = bool(
        planned_session
        and session_lifecycle.get("stance") == "post_session_review"
    )

    if enforce_scheduled_rest:
        session = _scheduled_rest_plan(scheduled_rest)
        post_session_review = False
    elif post_session_review:
        session = dict(planned_session["session"])
        plan_source = planned_session["source"]
    elif level == "red" or hard_guidance == "avoid":
        session = _red_plan(state)
    elif selected_session:
        session = dict(selected_session["session"])
        plan_source = selected_session["source"]
        session = _with_adaptive_upgrade_option(session, garmin_arbitration)
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
    adaptive_resolution = None
    if not post_session_review and not enforce_scheduled_rest:
        session, adaptive_resolution = _apply_adaptive_programming_intent(
            session,
            state,
            plan_source,
        )
    if post_session_review:
        applied_constraints = []
    else:
        session, applied_constraints = _apply_session_constraints(
            session,
            state,
            garmin_arbitration,
            plan_source,
            target_date=target_date,
            sabbath_exception=sabbath_exception,
            recurring_scheduled_rest=recurring_scheduled_rest,
        )
        if adaptive_resolution:
            applied_constraints.insert(0, adaptive_resolution)
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
        session=session,
        state=state,
    )
    guardrails = [
        "Progression follows readiness, recent load, bike specificity, and next-day response.",
        "Downshift tomorrow if the session produces unusually poor recovery or skill quality.",
    ]
    environment_scope_session = (
        selected_session.get("session")
        if isinstance(selected_session, dict)
        and isinstance(selected_session.get("session"), dict)
        else session
    )
    environment_input = _compact_environment_input(
        state,
        target_date,
        environment_scope_session,
    )
    environment_decision = (
        environment_input.get("decision")
        if isinstance(environment_input, dict)
        and isinstance(environment_input.get("decision"), dict)
        else {}
    )
    environment_gate = str(environment_decision.get("gate") or "")
    environment_applicability = (
        environment_input.get("plan_applicability")
        if isinstance(environment_input, dict)
        and isinstance(environment_input.get("plan_applicability"), dict)
        else {}
    )
    environment_applicability_status = str(
        environment_applicability.get("status") or ""
    )
    environment_constraint_applied = any(
        item.get("source") == "environment_evidence"
        for item in applied_constraints
        if isinstance(item, dict)
    )
    if (
        environment_input
        and _session_explicitly_targets_environment_venue(
            environment_scope_session,
            state.get("environment_evidence") or {},
        )
        and not environment_constraint_applied
    ):
        environment_guardrail = None
        if (
            environment_applicability_status == "current_observation_applicable"
            and environment_gate
            not in {"", "no_environment_downshift_from_current_point"}
        ):
            environment_guardrail = environment_decision.get("reason")
        elif environment_applicability_status in {
            "forecast_windows_time_unspecified",
            "forecast_window_applicable",
            "forecast_planning_only_recheck_pending",
            "forecast_named_window_planning_context",
            "forecast_exact_interval_duration_missing",
            "retained_recheck_required",
            "planned_interval_outside_published_window_recheck",
            "forecast_unavailable_recheck",
        }:
            environment_guardrail = environment_applicability.get("reason")
        if environment_guardrail:
            guardrails.insert(0, environment_guardrail)
    if selected_session and not enforce_scheduled_rest and not (level == "red" or hard_guidance == "avoid"):
        if plan_source.get("type") == "input_planned_session":
            guardrails.insert(0, f"Using coach-authored planned session from {plan_source['path']}.")
        else:
            guardrails.insert(
                0,
                f"Using this week's intent session from {plan_source['path']}; same-day readiness remains the execution gate.",
            )
    if enforce_scheduled_rest:
        guardrails.insert(
            0,
            f"{scheduled_rest.get('label', 'Scheduled rest')} is a hard rest constraint: no planned exercise today.",
        )
    elif sabbath_exception:
        exception_type = sabbath_exception.get("exception_type")
        if exception_type == "athlete_authorized_race_event":
            event = sabbath_exception.get("event") or {}
            exception_guardrail = (
                "Athlete-authorized exact-date Sabbath exception for the named race "
                f"{event.get('name') or 'event'} only; replacement Sabbath is enforced on "
                f"{(sabbath_exception.get('replacement_sabbath') or {}).get('date')}. "
                "The recurring Sunday rule is unchanged."
            )
        else:
            exception_guardrail = (
                "Athlete-authorized one-off exact-date Sabbath exception: indoor "
                "low-aerobic work only; this does not alter the recurring Sunday rule."
            )
        guardrails.insert(
            0,
            exception_guardrail,
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
    adaptive_decision = (state.get("adaptive_training") or {}).get("progression_decision") or {}
    if adaptive_decision:
        guardrails.append(
            "Adaptive programming: "
            f"{adaptive_decision.get('program_action')} with lever {adaptive_decision.get('active_lever')}; "
            f"next constraint: {adaptive_decision.get('next_constraint')}"
        )
    if post_session_review:
        guardrails.insert(0, session_lifecycle["rule"])
    if (
        not enforce_scheduled_rest
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
        "coaching_status": "post_session_review" if post_session_review else "proposal_for_llm_coach",
        "session": session,
        "plan_source": plan_source,
        "gym": _gym_block(
            state,
            scheduled_rest=scheduled_rest,
            sabbath_exception=sabbath_exception,
        ),
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
            "replacement_sabbath": replacement_sabbath,
            "sabbath_exception": sabbath_exception,
            "garmin_arbitration": garmin_arbitration,
            "cns_readiness": {
                "status": (state.get("cns_readiness") or {}).get("status"),
                "session_ceiling": (state.get("cns_readiness") or {}).get("session_ceiling"),
            },
            "session_lifecycle": session_lifecycle,
            "environment_evidence": environment_input,
            "adaptive_training": {
                "state_basis_date": (state.get("adaptive_training") or {}).get("date"),
                "roadmap_block": (state.get("adaptive_training") or {}).get("roadmap_block"),
                "progression_decision": adaptive_decision,
                "weekly_budget": (state.get("adaptive_training") or {}).get("weekly_budget"),
            },
        },
        "constraint_resolution": {
            "applied": applied_constraints,
            "effective_session_source": (
                "executed_coach_authored_contract"
                if post_session_review
                else ("constraint" if applied_constraints else plan_source.get("type"))
            ),
        },
    }
    write_json(snapshots_dir(root) / "today_plan.json", plan)
    return plan
