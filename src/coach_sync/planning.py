from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

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


def _apply_session_constraints(
    session: dict,
    state: dict,
    arbitration: dict,
    plan_source: dict,
    *,
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
    if post_session_review:
        applied_constraints = []
    else:
        session, applied_constraints = _apply_session_constraints(
            session,
            state,
            garmin_arbitration,
            plan_source,
            sabbath_exception=sabbath_exception,
            recurring_scheduled_rest=recurring_scheduled_rest,
        )
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
