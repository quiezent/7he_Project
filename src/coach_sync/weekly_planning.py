from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .context import load_context
from .evidence import as_number
from .garmin_arbitration import build_garmin_arbitration
from .io import read_json, write_json, write_text
from .paths import input_dir, snapshots_dir
from .planning import SESSION_CONTRACT_FIELDS
from .sabbath import (
    replacement_sabbath_rule,
    scheduled_rest_rule,
    validate_sabbath_exception,
)
from .state import build_current_state
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local


def _week_start(target: date) -> date:
    return target - timedelta(days=target.weekday())


def _iso_week_key(day: date) -> str:
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _day_name(day: date) -> str:
    return day.strftime("%A")


def _training_rules(context: dict[str, Any]) -> dict[str, Any]:
    continuity = (
        context.get("training_rules", {})
        .get("bike_specific_continuity", {})
    )
    minimum_bike = int(continuity.get("minimum_bike_touches_per_week") or 2)
    preferred_bike = int(continuity.get("preferred_rebuild_bike_touches_per_week") or max(5, minimum_bike))
    maximum_bike = int(
        continuity.get("maximum_normal_build_bike_touches_per_week")
        or max(6, preferred_bike)
    )
    meaningful_cost_max = int(continuity.get("meaningful_cost_sessions_per_week_max") or 3)
    protect_mtb = int(continuity.get("protect_mtb_exposures_per_week") or 2)
    max_mtb = int(continuity.get("maximum_mtb_exposures_per_week") or 3)
    dose_anchors = continuity.get("indoor_endurance_dose_anchors") or {}
    routine_contract = dose_anchors.get(
        "routine_low_cost_continuity_contract"
    ) or {}
    configured_duration = as_number(routine_contract.get("total_duration_min"))
    continuity_duration_min = (
        int(configured_duration)
        if configured_duration is not None and configured_duration > 0
        else 60
    )

    configured_power = routine_contract.get("main_power_w_range")
    continuity_power_w_range = [120, 130]
    if isinstance(configured_power, (list, tuple)) and len(configured_power) == 2:
        power_low = as_number(configured_power[0])
        power_high = as_number(configured_power[1])
        if (
            power_low is not None
            and power_high is not None
            and 0 < power_low <= power_high
        ):
            continuity_power_w_range = [int(power_low), int(power_high)]

    configured_rpe = routine_contract.get("global_rpe_range")
    continuity_rpe_range = [2, 3]
    if isinstance(configured_rpe, (list, tuple)) and len(configured_rpe) == 2:
        rpe_low = as_number(configured_rpe[0])
        rpe_high = as_number(configured_rpe[1])
        if (
            rpe_low is not None
            and rpe_high is not None
            and 0 <= rpe_low <= rpe_high <= 10
        ):
            continuity_rpe_range = [int(rpe_low), int(rpe_high)]
    return {
        "minimum_bike_touches": minimum_bike,
        "preferred_bike_touches": max(preferred_bike, minimum_bike),
        "maximum_bike_touches": max(maximum_bike, preferred_bike, minimum_bike),
        "meaningful_cost_sessions_max": max(1, meaningful_cost_max),
        "protect_mtb_exposures": protect_mtb,
        "maximum_mtb_exposures": max(max_mtb, protect_mtb),
        "continuity_duration_min": continuity_duration_min,
        "continuity_power_w_range": continuity_power_w_range,
        "continuity_rpe_range": continuity_rpe_range,
    }


def _feedback_family(feedback: Any) -> str:
    text = str(feedback or "").lower()
    if "recovery" in text:
        return "recovery"
    if "unproductive" in text:
        return "unproductive"
    if "overreach" in text:
        return "overreaching"
    if "strained" in text:
        return "strained"
    if "productive" in text:
        return "productive"
    if "maintain" in text:
        return "maintaining"
    if "peaking" in text:
        return "peaking"
    return "unknown"


def _load_focus_position(value: Any, target_min: Any, target_max: Any) -> dict[str, Any]:
    load = as_number(value)
    low = as_number(target_min)
    high = as_number(target_max)
    if load is None:
        return {"value": None, "position": "unknown", "target_ratio": None}
    if high is not None and load > high:
        position = "above_target"
    elif low is not None and load < low:
        position = "below_target"
    elif low is not None or high is not None:
        position = "in_target"
    else:
        position = "unknown_target"
    return {
        "value": load,
        "position": position,
        "target_ratio": round(load / high, 3) if high else None,
    }


def _load_focus_summary(state: dict[str, Any]) -> dict[str, Any]:
    focus = (state.get("training_status_current") or {}).get("load_focus") or {}
    return {
        "feedback": focus.get("feedback"),
        "low_aerobic": _load_focus_position(
            focus.get("low_aerobic"),
            focus.get("low_aerobic_target_min"),
            focus.get("low_aerobic_target_max"),
        ),
        "high_aerobic": _load_focus_position(
            focus.get("high_aerobic"),
            focus.get("high_aerobic_target_min"),
            focus.get("high_aerobic_target_max"),
        ),
        "anaerobic": _load_focus_position(
            focus.get("anaerobic"),
            focus.get("anaerobic_target_min"),
            focus.get("anaerobic_target_max"),
        ),
    }


def _round_to_10(value: float) -> int:
    return int(round(value / 10.0) * 10)


def _weekly_load_target(state: dict[str, Any]) -> dict[str, Any]:
    readiness = state.get("readiness") or {}
    training = state.get("training_load") or {}
    status = state.get("training_status_current") or {}
    acute_chronic = status.get("acute_chronic") or {}
    freshness = state.get("data_freshness") or {}
    last_7 = as_number((training.get("last_7_days") or {}).get("training_load"))
    last_7 = last_7 if last_7 and last_7 > 0 else 500.0
    level = readiness.get("readiness_level")
    acwr_status = str(acute_chronic.get("status") or "").upper()
    acwr_ratio = as_number(acute_chronic.get("ratio"))
    family = _feedback_family(status.get("training_status_feedback"))

    reasons: list[str] = []
    if freshness.get("status") != "current":
        low_mult, high_mult = 0.65, 0.85
        reasons.append("Freshness is not current, so the week remains provisional and capped.")
    elif level == "red":
        low_mult, high_mult = 0.55, 0.75
        reasons.append("Readiness is red; unload before rebuilding density.")
    elif acwr_status and acwr_status != "OPTIMAL":
        low_mult, high_mult = 0.75, 0.95
        reasons.append(f"Garmin ACWR is {acwr_status}; avoid increasing density.")
    elif acwr_ratio is not None and acwr_ratio >= 1.35:
        low_mult, high_mult = 0.75, 0.95
        reasons.append(f"ACWR ratio is {acwr_ratio}; hold or reduce load.")
    elif level == "yellow":
        low_mult, high_mult = 0.80, 1.05
        reasons.append("Readiness is yellow; build only if daily gates stay clean.")
    elif family in {"productive", "maintaining", "peaking"}:
        low_mult, high_mult = 0.90, 1.12
        reasons.append("Garmin status and readiness allow a controlled build range.")
    else:
        low_mult, high_mult = 0.85, 1.00
        reasons.append("Default to a hold week until Garmin diagnosis gives a clearer signal.")

    low = max(250, _round_to_10(last_7 * low_mult))
    high = max(low + 40, _round_to_10(last_7 * high_mult))
    return {
        "training_load_range": [low, high],
        "basis_last_7_day_load": round(last_7, 1),
        "interpretation": "Planning range, not a target to chase. Daily readiness and session quality can lower it.",
        "rationale": reasons,
    }


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


def _default_stop_rules() -> list[str]:
    return [
        "Downshift if morning readiness is red, Garmin freshness is stale, or Sunday Sabbath applies.",
        "Stop intensity if HR drift or RPE turns controlled work into survival.",
        "Stop technical work if braking timing gets lazy, posture gets tall, or line choice becomes reactive.",
        "Do not add a new variable mid-session without naming the new objective.",
    ]


def _session(
    day: date,
    *,
    title: str,
    session_type: str,
    modality: str,
    priority: str,
    duration_min: int,
    intensity: str,
    purpose: str,
    dose: dict[str, Any],
    adaptation_hypothesis: str,
    execution_rules: list[str],
    expected_result: dict[str, Any],
    readiness_gate: dict[str, Any],
    mtb_exposure: bool = False,
    optional: bool = False,
    load_target: str | None = None,
    stop_rules: list[str] | None = None,
    post_session_review_fields: list[str] | None = None,
    bike_touch_status: str = "none",
    density_cost: str = "low",
) -> dict[str, Any]:
    session = {
        "date": day.isoformat(),
        "day_name": _day_name(day),
        "title": title,
        "type": session_type,
        "modality": modality,
        "priority": priority,
        "optional": optional,
        "mtb_exposure": mtb_exposure,
        "bike_touch_status": bike_touch_status,
        "density_cost": density_cost,
        "duration_min": duration_min,
        "intensity": intensity,
        "load_target": load_target,
        "schema_version": 3,
        "contract_fields": list(SESSION_CONTRACT_FIELDS),
        "purpose": purpose,
        "dose": dose,
        "adaptation_hypothesis": adaptation_hypothesis,
        "execution_rules": execution_rules,
        "expected_result": expected_result,
        "stop_rules": stop_rules or _default_stop_rules(),
        "post_session_review_fields": post_session_review_fields or _review_fields(),
        "readiness_gate": readiness_gate,
    }
    return session


def _scheduled_rest(
    day: date,
    rule: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rule = rule or {}
    label = str(rule.get("label") or "Sabbath")
    reason = str(
        rule.get("reason")
        or "Honor Sunday Sabbath as a hard no-exercise day."
    )
    session = {
        "date": day.isoformat(),
        "day_name": _day_name(day),
        "title": f"{label} rest",
        "type": "scheduled_rest",
        "modality": "rest",
        "priority": "hard_constraint",
        "optional": False,
        "mtb_exposure": False,
        "bike_touch_status": "none",
        "density_cost": "none",
        "duration_min": 0,
        "intensity": "recovery",
        "purpose": reason,
        "execution_rules": [
            "No ride, gym, intervals, strength loading, or planned training.",
            "Normal life, worship, family time, meals, and easy unwinding are enough.",
        ],
        "expected_result": {
            "recovery": "Physical and mental space before the next week.",
        },
    }
    if rule:
        session["scheduled_rest_rule"] = rule
    return session


def _apply_explicit_session_overrides(
    root: str | Path | None,
    sessions: list[dict[str, Any]],
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    overrides: dict[str, dict[str, Any]] = {}
    for day_text in {str(item.get("date") or "") for item in sessions}:
        if not day_text:
            continue
        day = parse_date(day_text)
        if day is None:
            continue
        replacement_rule = replacement_sabbath_rule(root, context, day)
        if replacement_rule:
            override = _scheduled_rest(day, replacement_rule)
            provenance = replacement_rule.get("provenance") or {}
            override["weekly_intent_override"] = {
                "source": provenance.get("source_path"),
                "status": "replacement_sabbath_enforced",
                "source_exception_status": replacement_rule.get(
                    "source_exception_status"
                ),
            }
            overrides[day_text] = override
            continue

        payload = read_json(input_dir(root) / f"planned_session_{day_text}.json", {})
        session = payload.get("session") if isinstance(payload, dict) else None
        if not isinstance(session, dict):
            continue
        payload_date = parse_date(payload.get("date"))
        if payload_date is not None and payload_date.isoformat() != day_text:
            continue
        recurring_rest = scheduled_rest_rule(context, day)
        validated_exception = None
        if recurring_rest:
            validated_exception = validate_sabbath_exception(
                {
                    "session": session,
                    "sabbath_exception": payload.get("sabbath_exception"),
                    "source": {
                        "type": "input_planned_session",
                        "path": f"input/planned_session_{day_text}.json",
                    },
                },
                day,
                recurring_rest,
            )
            if validated_exception is None:
                continue
        override = dict(session)
        override.setdefault("date", day_text)
        override.setdefault("day_name", _day_name(day))
        override.setdefault("optional", False)
        race_exception = bool(
            validated_exception
            and validated_exception.get("exception_type")
            == "athlete_authorized_race_event"
        )
        override.setdefault("mtb_exposure", race_exception)
        override.setdefault("bike_touch_status", "normal" if race_exception else "none")
        override.setdefault("density_cost", "meaningful" if race_exception else "none")
        override["weekly_intent_override"] = {
            "source": f"input/planned_session_{day_text}.json",
            "status": payload.get("status"),
        }
        if validated_exception:
            override["sabbath_exception"] = validated_exception
            override["weekly_intent_override"]["sabbath_exception_status"] = (
                validated_exception.get("status")
            )
        overrides[day_text] = override

    if not overrides:
        return sessions
    result: list[dict[str, Any]] = []
    emitted: set[str] = set()
    for session in sessions:
        day_text = str(session.get("date") or "")
        override = overrides.get(day_text)
        if override is None:
            result.append(session)
        elif day_text not in emitted:
            result.append(override)
            emitted.add(day_text)
    return result


def _recovery_gate() -> dict[str, Any]:
    return {
        "green": "Execute as written.",
        "yellow": "Execute as written but keep the cap.",
        "red": "Mobility, walk, or full rest only.",
    }


def _quality_gate() -> dict[str, Any]:
    return {
        "green": "Execute the full dose.",
        "yellow": "Start the session, then cap at the shorter option unless warm-up feels clearly better than Garmin suggests.",
        "red": "Replace with easy spin, mobility, or rest.",
    }


def _build_sessions(
    week_start: date,
    state: dict[str, Any],
    rules: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sessions: list[dict[str, Any]] = []
    monday = week_start
    tuesday = week_start + timedelta(days=1)
    wednesday = week_start + timedelta(days=2)
    thursday = week_start + timedelta(days=3)
    friday = week_start + timedelta(days=4)
    saturday = week_start + timedelta(days=5)
    sunday = week_start + timedelta(days=6)
    continuity_duration = rules["continuity_duration_min"]
    continuity_power = rules["continuity_power_w_range"]
    continuity_rpe = rules["continuity_rpe_range"]
    continuity_power_text = f"{continuity_power[0]}-{continuity_power[1]} W"
    continuity_rpe_text = f"RPE {continuity_rpe[0]}-{continuity_rpe[1]}"

    sessions.append(
        _session(
            monday,
            title="Conversational run or indoor continuity",
            session_type="social_run_optional_bike",
            modality="run_with_optional_bike",
            priority="support",
            duration_min=continuity_duration,
            intensity="easy",
            load_target="low",
            purpose="Preserve the preferred social run with Clayton's wife; when the run does not happen, use the established full indoor continuity dose instead of losing the bike-specific day.",
            dose={
                "run": "30-50 min conversational / RPE 2-3.",
                "if_run_skipped": (
                    f"{continuity_duration} min Suito at {continuity_power_text} / "
                    f"{continuity_rpe_text}, seated with normal cooling."
                ),
                "optional_bike": "20-30 min easy Suito at RPE 2 later in the day only if the run stays conversational and legs feel normal.",
                "cap": "If the run becomes moderate or hard, omit the bike and count the run toward the meaningful-cost cap.",
            },
            adaptation_hypothesis=(
                "A truly easy social run supports general aerobic continuity; an optional easy spin can add bike frequency without stealing Tuesday trail quality."
            ),
            execution_rules=[
                "No pace target, hills, strides, or finish surge.",
                "If the social run does not happen, execute the standard indoor continuity dose; do not shorten it into a primer without a named readiness or calendar constraint.",
                "The optional bike is circulation only, not a second workout.",
                "Do not use a double to manufacture the weekly touch count.",
            ],
            expected_result={
                "run": "Conversational throughout with normal legs afterward.",
                "next_day": "Full technical and physical availability for Tuesday Kiara.",
            },
            readiness_gate=_recovery_gate(),
            bike_touch_status="optional",
            density_cost="low",
        )
    )

    sessions.append(
        _session(
            tuesday,
            title="Kiara MTB quality/engine",
            session_type="mtb_quality_engine",
            modality="mtb",
            priority="key_skill_engine",
            duration_min=90,
            intensity="moderate_hard",
            mtb_exposure=True,
            load_target="meaningful but bounded by technical quality",
            purpose="Use the first protected MTB exposure for one clearly named engine or technical objective.",
            dose={
                "venue": "Kiara.",
                "primary_target": "Choose one: climb repeatability/torque, or descent braking and line quality. Do not stack both as maximal targets.",
                "cap": "Stop adding quality when decision speed, front tracking, or repeatability drops.",
            },
            adaptation_hypothesis=(
                "A focused fresh-state MTB session should convert aerobic power into more repeatable climbing and deliberate descending without hidden mission creep."
            ),
            execution_rules=[
                "Declare the primary target before the first quality effort.",
                "Keep setup unchanged unless the session is explicitly rewritten as a setup test.",
                "Compare the first and final quality rep.",
            ],
            expected_result={
                "technical": "Stable vision, braking release, front-wheel authority, and line choice.",
                "physiology": "One meaningful MTB dose that remains absorbable before Thursday.",
            },
            readiness_gate=_quality_gate(),
            bike_touch_status="normal",
            density_cost="meaningful",
        )
    )

    sessions.append(
        _session(
            wednesday,
            title="Indoor low-aerobic continuity",
            session_type="indoor_low_aerobic",
            modality="bike_indoor",
            priority="aerobic_support",
            duration_min=continuity_duration,
            intensity="easy",
            load_target="low aerobic",
            purpose="Add bike-specific aerobic volume without carrying high-aerobic or neural debt into Thursday.",
            dose={
                "duration_min": continuity_duration,
                "power_anchor_w_range": continuity_power,
                "power_anchor": f"{continuity_power_text}, Clayton's validated routine continuity range.",
                "intensity": f"{continuity_rpe_text}, conversational, seated.",
                "cap": "No tempo, torque blocks, standing surges, or sprint finish.",
            },
            adaptation_hypothesis=(
                "A low-cost continuous bike dose should build mitochondrial and capillary support while improving between-stage recovery without compromising technical freshness."
            ),
            execution_rules=[
                "Hold power and cadence steady enough to assess HR drift.",
                "Reduce 5-10 W if RPE rises above 3 or HR becomes disproportionate.",
                "Finish with normal legs for Thursday.",
            ],
            expected_result={
                "physiology": "Low-aerobic Training Effect with zero intentional high-intensity minutes.",
                "next_day": "Normal legs, clarity, and technical appetite.",
            },
            readiness_gate=_recovery_gate(),
            bike_touch_status="normal",
            density_cost="low",
        )
    )

    sessions.append(
        _session(
            thursday,
            title="Kiara MTB durability/technical quality",
            session_type="mtb_durability_quality",
            modality="mtb",
            priority="key_durability",
            duration_min=120,
            intensity="moderate_hard",
            mtb_exposure=True,
            load_target="meaningful but bounded",
            purpose="Use the second protected MTB exposure to build repeatability while preserving final-descent precision.",
            dose={
                "venue": "Kiara or equivalent enduro-repeatability venue.",
                "primary_target": "Choose one: additional clean repeat, standing-climb torque, or technical durability. Do not maximize all three.",
                "cap": "Stop adding loops when the final descent would no longer match the first in vision, braking, front tracking, and line choice.",
            },
            adaptation_hypothesis=(
                "A bounded repeatability session should improve the ability to climb, recover, and descend precisely under accumulating fatigue."
            ),
            execution_rules=[
                "Climb deliberately; descend with one named technical cue.",
                "Keep setup unchanged unless this is explicitly converted into a setup test.",
                "Technical quality, not elapsed time, decides the final repeat.",
            ],
            expected_result={
                "technical": "The final descent remains assertive and deliberate rather than merely survived.",
                "physiology": "Meaningful MTB load without Friday recovery failure.",
            },
            readiness_gate=_quality_gate(),
            bike_touch_status="normal",
            density_cost="meaningful",
            post_session_review_fields=[
                *_review_fields(),
                "loop_count",
                "final_descent_quality_vs_first",
                "braking_fatigue",
                "upper_body_fatigue",
            ],
        )
    )

    sessions.append(
        _session(
            friday,
            title="Indoor low-aerobic continuity or primer",
            session_type="indoor_low_aerobic",
            modality="bike_indoor",
            priority="aerobic_support",
            duration_min=continuity_duration,
            intensity="easy",
            load_target="low aerobic",
            purpose="Accumulate bike-specific aerobic time while absorbing Thursday and preserving Saturday optionality.",
            dose={
                "green_duration_min": continuity_duration,
                "green_power_w_range": continuity_power,
                "green": f"{continuity_duration} min at {continuity_power_text} / {continuity_rpe_text}.",
                "after_harder_than_written_thursday": "30-45 min recovery at 100-115 W or skip.",
                "cap": "No intensity added to repair load or frequency.",
            },
            adaptation_hypothesis=(
                "A low-aerobic touch should support cycling durability and speed recovery from the key MTB dose without adding high-aerobic debt."
            ),
            execution_rules=[
                "On a green gate after an absorbed Thursday, execute the standard continuity dose.",
                "Shorten only when Thursday was harder than written or a named readiness constraint is present.",
                "Keep the entire session conversational and seated.",
                "If the spin does not improve the legs by 15 minutes, stop.",
            ],
            expected_result={
                "physiology": "Low-aerobic continuity with stable or improving leg feel.",
                "next_day": "Saturday remains optional, never owed.",
            },
            readiness_gate=_recovery_gate(),
            bike_touch_status="normal",
            density_cost="low",
        )
    )

    allow_optional_third = rules["maximum_mtb_exposures"] >= 3
    sessions.append(
        _session(
            saturday,
            title="MTB skill-transfer",
            session_type="mtb_skill_transfer_optional",
            modality="mtb",
            priority="optional_skill",
            duration_min=90,
            intensity="skill",
            mtb_exposure=allow_optional_third,
            optional=True,
            load_target="low to moderate",
            purpose="Use the third MTB exposure for skill transfer, not another hidden hard day.",
            dose={
                "options": "PCP jump-line, low-consequence Kiara skill laps, or short handling practice.",
                "cap": "4-6 quality runs if green; 3-4 easy technique runs if yellow.",
                "normal_build_limit": "This is the third and final MTB exposure for a normal build week.",
            },
            adaptation_hypothesis=(
                "A capped skill-transfer day should improve jump/corner timing while preserving the ability to recover from Friday's durability work."
            ),
            execution_rules=[
                "One skill target only: squash/scrub rhythm, berm exits, braking release, or clipless low-speed control.",
                "Do not add speed and setup changes in the same session.",
                "End when quality drops, not when motivation runs out.",
            ],
            expected_result={
                "technical": "More repeatable skill at lower physiological cost.",
                "next_day": "Sabbath recovery without sympathetic overhang.",
            },
            readiness_gate={
                "green": "Execute 4-6 quality runs/reps.",
                "yellow": "Reduce to easy technique only.",
                "red": "Skip.",
            },
            bike_touch_status="conditional",
            density_cost="low",
        )
    )
    sessions.append(_scheduled_rest(sunday))

    return sessions, _summarize_session_plan(sessions, rules)


def _adaptive_role(session: dict[str, Any]) -> str:
    session_type = str(session.get("type") or "").lower()
    modality = str(session.get("modality") or "").lower()
    if session_type in {"scheduled_rest", "scheduled_recovery"} or modality == "rest":
        return "rest_or_recovery"
    if "tempo" in session_type or "torque" in session_type or "vo2" in session_type:
        return "structured_engine"
    if session.get("mtb_exposure"):
        title = str(session.get("title") or "").lower()
        if any(token in f"{session_type} {title}" for token in ("durability", "enduro", "race_bike", "race-bike")):
            return "protected_enduro_durability_or_race_transfer"
        if "skill" in session_type:
            return "mtb_skill_transfer"
        return "protected_stumpjumper_quality_fitness"
    if "run" in modality or "run" in session_type:
        return "social_run_or_bike_continuity"
    if "bike" in modality or "cycling" in modality:
        return "low_cost_bike_continuity"
    return "support"


def _apply_adaptive_programming(
    sessions: list[dict[str, Any]],
    state: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Reflow future discretionary intent without rewriting explicit contracts.

    The programming controller is upstream of the same-day safety resolver. It may
    lower future discretionary density, but explicit coach-authored contracts stay
    immutable and receive a visible conflict instead of being silently changed.
    """

    adaptive = state.get("adaptive_training") or {}
    shape = adaptive.get("target_shape") or {}
    budget = adaptive.get("weekly_budget") or {}
    target = parse_date(state.get("date"))
    if not adaptive or target is None:
        return sessions, []

    meaningful_remaining = int(budget.get("meaningful_cost_days_remaining") or 0)
    duration_multiplier = as_number(shape.get("duration_multiplier")) or 1.0
    conflicts: list[dict[str, Any]] = []
    result: list[dict[str, Any]] = []
    for raw in sessions:
        session = dict(raw)
        day = parse_date(session.get("date"))
        explicit = isinstance(session.get("weekly_intent_override"), dict)
        future = day is not None and day > target
        meaningful = session.get("density_cost") == "meaningful"
        session["adaptive_programming"] = {
            "state_basis_date": adaptive.get("date"),
            "roadmap_mode": (adaptive.get("roadmap_block") or {}).get("program_mode"),
            "role": _adaptive_role(session),
            "progression_lever": (adaptive.get("progression_decision") or {}).get("active_lever"),
        }

        if future and meaningful:
            if meaningful_remaining > 0:
                meaningful_remaining -= 1
                session["adaptive_programming"]["budget_disposition"] = "uses_remaining_meaningful_slot"
            elif explicit:
                conflict = {
                    "date": session.get("date"),
                    "type": "explicit_contract_exceeds_remaining_meaningful_budget",
                    "source": (session.get("weekly_intent_override") or {}).get("source"),
                    "resolution": "preserved_for_head_coach_review_not_silently_rewritten",
                }
                conflicts.append(conflict)
                session["adaptive_programming"]["budget_disposition"] = "explicit_conflict_preserved"
                session["adaptive_programming"]["conflict"] = conflict
            else:
                session["density_cost"] = "low"
                session["intensity"] = "easy"
                session["optional"] = True
                session["title"] = f"Adaptive low-cost replacement: {session.get('title') or 'bike continuity'}"
                session["adaptive_programming"]["budget_disposition"] = "reflowed_to_low_cost"
                session.setdefault("execution_rules", []).append(
                    "The weekly meaningful-cost budget is spent; keep this genuinely low-cost or omit it."
                )

        if future and not explicit and duration_multiplier < 1.0:
            duration = as_number(session.get("duration_min"))
            if duration and duration > 0:
                scaled = max(15, int(round(duration * duration_multiplier / 5.0) * 5))
                if scaled < duration:
                    session["adaptive_programming"]["original_duration_min"] = duration
                    session["duration_min"] = scaled
                    session["adaptive_programming"]["duration_disposition"] = (
                        f"scaled_by_{duration_multiplier:.2f}_roadmap_multiplier"
                    )
        result.append(session)
    return result, conflicts


def _summarize_session_plan(
    sessions: list[dict[str, Any]],
    rules: dict[str, int],
) -> dict[str, Any]:
    exposure_summary = {
        "protected_mtb_exposures": rules["protect_mtb_exposures"],
        "maximum_normal_build_mtb_exposures": rules["maximum_mtb_exposures"],
        "planned_key_mtb_exposures": sum(1 for item in sessions if item.get("mtb_exposure") and not item.get("optional")),
        "optional_mtb_exposures": sum(1 for item in sessions if item.get("mtb_exposure") and item.get("optional")),
        "rule": "Protect 2 MTB exposures; allow up to 3 when readiness, logistics, and density support it. More than 3 is a race/event block, not a default build week.",
    }
    normal_bike_sessions = [
        item
        for item in sessions
        if item.get("bike_touch_status") in {"normal", "conditional"}
    ]
    optional_bike_sessions = [
        item for item in sessions if item.get("bike_touch_status") == "optional"
    ]
    normal_bike_days = sorted({item["date"] for item in normal_bike_sessions})
    optional_bike_days = sorted({item["date"] for item in optional_bike_sessions})

    def touch_identity(item: dict[str, Any]) -> tuple[str, str]:
        counting = item.get("bike_touch_counting")
        if isinstance(counting, dict) and counting.get("mode") == "mutually_exclusive":
            group = counting.get("group")
            if isinstance(group, str) and group.strip():
                return ("mutually_exclusive", group.strip())
        return ("date", str(item["date"]))

    normal_touch_identities = {touch_identity(item) for item in normal_bike_sessions}
    optional_touch_identities = {touch_identity(item) for item in optional_bike_sessions}
    exclusive_groups: dict[str, dict[str, set[str]]] = {}
    for item in normal_bike_sessions + optional_bike_sessions:
        identity_type, identity_value = touch_identity(item)
        if identity_type != "mutually_exclusive":
            continue
        group = exclusive_groups.setdefault(
            identity_value,
            {"candidate_dates": set(), "bike_touch_statuses": set()},
        )
        group["candidate_dates"].add(str(item["date"]))
        group["bike_touch_statuses"].add(str(item.get("bike_touch_status")))

    exposure_summary["bike_touch_plan"] = {
        "planned_normal_unique_bike_days": normal_bike_days,
        "planned_normal_count": len(normal_touch_identities),
        "optional_additional_unique_bike_days": optional_bike_days,
        "planned_max_count": len(normal_touch_identities | optional_touch_identities),
        "mutually_exclusive_groups": [
            {
                "group": group_name,
                "candidate_dates": sorted(group["candidate_dates"]),
                "bike_touch_statuses": sorted(group["bike_touch_statuses"]),
                "counts_as_at_most": 1,
            }
            for group_name, group in sorted(exclusive_groups.items())
        ],
        "planned_meaningful_cost_sessions": sum(
            1 for item in sessions if item.get("density_cost") == "meaningful"
        ),
        "counting_rule": (
            "Count unique bike days, not split activity files. Five is the normal build shape; "
            "six requires the optional Monday microtouch and clean recovery. A hard run counts "
            "toward the meaningful-cost cap. Sessions with bike_touch_counting.mode set to "
            "mutually_exclusive and the same non-empty group remain visible as candidate days "
            "but count as at most one touch."
        ),
    }
    return exposure_summary


def _weekly_objective(state: dict[str, Any], load_focus: dict[str, Any]) -> dict[str, Any]:
    readiness = state.get("readiness") or {}
    status = state.get("training_status_current") or {}
    family = _feedback_family(status.get("training_status_feedback"))
    if load_focus.get("feedback") == "BALANCED":
        physiology = "Maintain balanced load focus while converting it into bike-specific repeatability."
    elif load_focus["high_aerobic"]["position"] == "below_target":
        physiology = "Raise controlled high-aerobic support without adding anaerobic debt."
    elif load_focus["anaerobic"]["target_ratio"] and load_focus["anaerobic"]["target_ratio"] >= 0.85:
        physiology = "Avoid anaerobic stacking; build through tempo, repeatability, and skill quality."
    else:
        physiology = "Build bike-specific aerobic durability and weekly rhythm."
    return {
        "primary": "Controlled build week for fitness and MTB skill transfer.",
        "physiological_target": physiology,
        "technical_target": "Repeatable precision under fatigue: centred posture, braking release timing, corner-exit speed, and final-descent quality.",
        "constraint": "One physiological target plus one technical target per key session; no mission creep.",
        "review_question": "Did the final quality MTB rep/descent look as deliberate as the first, or did confidence replace precision?",
        "basis": {
            "readiness_level": readiness.get("readiness_level"),
            "readiness_score": readiness.get("readiness_score"),
            "training_status_family": family,
        },
    }


def _daily_gates() -> list[dict[str, str]]:
    return [
        {
            "gate": "freshness",
            "rule": "Before each key session, run Garmin sync/current-state; stale wellness or stale training status blocks upgrades.",
        },
        {
            "gate": "readiness",
            "rule": "Green executes, yellow caps or shortens, red replaces training with recovery.",
        },
        {
            "gate": "CNS consequence",
            "rule": "Impaired or compromised CNS readiness replaces technical, speed, jump, enduro-simulation, setup-test, and structured sessions with low-consequence recovery.",
        },
        {
            "gate": "density",
            "rule": "Keep at most three meaningful-cost sessions including hard running. If a key MTB day drifts harder than written, the next non-key bike touch becomes recovery or is skipped; do not chase 5-6.",
        },
        {
            "gate": "MTB exposure cap",
            "rule": "Normal build weeks protect 2 MTB exposures and allow a 3rd only as capped skill-transfer.",
        },
        {
            "gate": "Sabbath",
            "rule": (
                "Sunday remains no planned exercise regardless of readiness. Only an exact-date, "
                "athlete-authorized named race in a coach-authored contract may shift that Sabbath "
                "to the following Monday; authorization is never inferred."
            ),
        },
    ]


def _compact_text(value: Any) -> str:
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                parts.append(f"{key}: {_compact_text(item)}")
            else:
                parts.append(f"{key}: {item}")
        return "; ".join(parts)
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    return str(value)


def _first_items(items: Any, limit: int = 3) -> list[str]:
    if not isinstance(items, list):
        return []
    return [str(item) for item in items[:limit]]


def _text_summary(plan: dict[str, Any]) -> str:
    adaptive = plan.get("adaptive_programming") or {}
    roadmap = adaptive.get("roadmap_block") or {}
    decision = adaptive.get("progression_decision") or {}
    lines = [
        f"Weekly Plan - {plan['week_key']}",
        f"Week: {plan['week_start']} to {plan['week_end']}",
        f"Status: {plan['status']}",
        "",
        f"Objective: {plan['weekly_objective']['primary']}",
        f"Physiology: {plan['weekly_objective']['physiological_target']}",
        f"Technical: {plan['weekly_objective']['technical_target']}",
        f"Roadmap block: {roadmap.get('label') or 'unavailable'} ({roadmap.get('program_mode') or 'unknown'})",
        f"Adaptive action: {decision.get('program_action') or 'unavailable'}; lever: {decision.get('active_lever') or 'unavailable'}",
        "",
        "Targets:",
        f"- Load range: {plan['targets']['training_load_range'][0]}-{plan['targets']['training_load_range'][1]}",
        f"- Bike touches: maintenance {plan['targets']['bike_touches']['minimum']}; preferred "
        f"{plan['targets']['bike_touches']['preferred']}-{plan['targets']['bike_touches']['maximum_normal_build']} "
        f"(planned normal {plan['targets']['bike_touches']['planned_normal_count']})",
        f"- MTB exposures: protect {plan['targets']['mtb_exposures']['protected_mtb_exposures']}, max {plan['targets']['mtb_exposures']['maximum_normal_build_mtb_exposures']}",
        "",
        "Sessions:",
    ]
    for session in plan["sessions"]:
        marker = "optional " if session.get("optional") else ""
        lines.extend(
            [
                "",
                f"{session['day_name']} {session['date']}: {marker}{session['title']}",
                f"- Type: {session['type']} | Modality: {session['modality']} | Intensity: {session['intensity']} | Duration: {session['duration_min']} min",
                f"- Purpose: {session.get('purpose')}",
            ]
        )
        if session.get("dose"):
            lines.append(f"- Dose: {_compact_text(session['dose'])}")
        if session.get("readiness_gate"):
            lines.append(f"- Readiness gate: {_compact_text(session['readiness_gate'])}")
        execution_rules = _first_items(session.get("execution_rules"), 3)
        if execution_rules:
            lines.append("- Execution rules:")
            lines.extend(f"  - {item}" for item in execution_rules)
        stop_rules = _first_items(session.get("stop_rules"), 3)
        if stop_rules:
            lines.append("- Stop rules:")
            lines.extend(f"  - {item}" for item in stop_rules)
        review_fields = _first_items(session.get("post_session_review_fields"), 6)
        if review_fields:
            lines.append(f"- Review fields: {', '.join(review_fields)}")
    lines.extend(
        [
            "",
            "Daily gates:",
            *[f"- {item['gate']}: {item['rule']}" for item in plan["daily_gates"]],
            "",
        ]
    )
    return "\n".join(lines)


def build_weekly_plan(
    root: str | Path | None = None,
    for_date: str | date | None = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = load_context(root)
    tz = context.get("athlete", {}).get("timezone", DEFAULT_TIMEZONE)
    target = parse_date(for_date) or parse_date((state or {}).get("date")) or today_local(tz)
    state = state or build_current_state(root, target, refresh_models=False)
    start = _week_start(target)
    end = start + timedelta(days=6)
    week_key = _iso_week_key(start)
    rules = _training_rules(context)
    load_focus = _load_focus_summary(state)
    load_target = _weekly_load_target(state)
    sessions, exposure_summary = _build_sessions(start, state, rules)
    sessions = _apply_explicit_session_overrides(root, sessions, context)
    sessions, adaptive_conflicts = _apply_adaptive_programming(sessions, state)
    exposure_summary = _summarize_session_plan(sessions, rules)
    freshness = state.get("data_freshness") or {}
    readiness = state.get("readiness") or {}
    arbitration = build_garmin_arbitration(state)

    status = "ready"
    state_basis_date = parse_date(state.get("date"))
    if state_basis_date is not None and state_basis_date != target:
        status = "provisional_prior_day_basis"
    elif freshness.get("status") != "current":
        status = "provisional_stale_data"
    elif readiness.get("readiness_level") == "red":
        status = "downshifted_readiness"

    dated_json = f"snapshots/weekly_plan_{week_key}.json"
    dated_text = f"snapshots/weekly_plan_{week_key}.txt"
    adaptive = state.get("adaptive_training") or {}
    adaptive_shape = adaptive.get("target_shape") or {}
    adaptive_decision = adaptive.get("progression_decision") or {}
    weekly_objective = _weekly_objective(state, load_focus)
    if adaptive:
        weekly_objective = {
            **weekly_objective,
            "primary": adaptive_decision.get("primary_adaptation_target") or weekly_objective["primary"],
            "active_progression_lever": adaptive_decision.get("active_lever"),
            "program_action": adaptive_decision.get("program_action"),
            "roadmap_block": (adaptive.get("roadmap_block") or {}).get("label"),
        }
    plan = {
        "artifact_type": "weekly_training_plan",
        "date": target.isoformat(),
        "generated_at": iso_now(tz),
        "week_key": week_key,
        "week_start": start.isoformat(),
        "week_end": end.isoformat(),
        "status": status,
        "planning_basis": {
            "state_basis_date": state_basis_date.isoformat() if state_basis_date else None,
            "plan_target_date": target.isoformat(),
            "readiness": {
                "level": readiness.get("readiness_level"),
                "score": readiness.get("readiness_score"),
                "hard_session_guidance": readiness.get("hard_session_guidance"),
            },
            "data_freshness": freshness,
            "training_status": {
                "feedback": (state.get("training_status_current") or {}).get("training_status_feedback"),
                "acwr": (state.get("training_status_current") or {}).get("acute_chronic"),
                "load_focus": load_focus,
            },
            "recent_training": state.get("training_load"),
            "garmin_arbitration": arbitration,
        },
        "weekly_objective": weekly_objective,
        "adaptive_programming": {
            "state_basis_date": adaptive.get("date"),
            "roadmap_block": adaptive.get("roadmap_block"),
            "progression_decision": adaptive_decision,
            "target_shape": adaptive_shape,
            "weekly_budget": adaptive.get("weekly_budget"),
            "recommended_week_roles": adaptive.get("recommended_week_roles"),
            "trainable_limiter_ranking": adaptive.get("trainable_limiter_ranking"),
            "programming_audit": adaptive.get("programming_audit"),
            "explicit_contract_conflicts": adaptive_conflicts,
            "guardrail": (
                "Adaptive programming selects the training direction and reflows only future discretionary intent. "
                "Same-day Sabbath, physical readiness, CNS, freshness, symptoms, environment and consequence still resolve the executable dose."
            ),
        },
        "targets": {
            **load_target,
            "bike_touches": {
                "minimum": rules["minimum_bike_touches"],
                "preferred": adaptive_shape.get("preferred_unique_bike_days", rules["preferred_bike_touches"]),
                "maximum_normal_build": adaptive_shape.get("maximum_unique_bike_days", rules["maximum_bike_touches"]),
                "meaningful_cost_sessions_max": adaptive_shape.get("meaningful_cost_days_max", rules["meaningful_cost_sessions_max"]),
                **exposure_summary["bike_touch_plan"],
                "note": "Bike-specific continuity is the durable fitness currency; elliptical/gym do not replace this, and low-cost touches carry the frequency target.",
            },
            "mtb_exposures": {
                **exposure_summary,
                "protected_mtb_exposures": adaptive_shape.get(
                    "protected_mtb_days", exposure_summary.get("protected_mtb_exposures")
                ),
                "adaptive_program_mode": (adaptive.get("roadmap_block") or {}).get("program_mode"),
            },
            "strength_sessions": {
                "range": [0, 2],
                "rule": "Use strength as support only; remove it if it compromises key trail quality.",
            },
        },
        "daily_gates": _daily_gates(),
        "sessions": sessions,
        "artifacts": {
            "current_json": "snapshots/weekly_plan.json",
            "current_text": "snapshots/weekly_plan.txt",
            "dated_json": dated_json,
            "dated_text": dated_text,
        },
    }
    write_json(snapshots_dir(root) / "weekly_plan.json", plan)
    write_json(snapshots_dir(root) / f"weekly_plan_{week_key}.json", plan)
    text = _text_summary(plan)
    write_text(snapshots_dir(root) / "weekly_plan.txt", text)
    write_text(snapshots_dir(root) / f"weekly_plan_{week_key}.txt", text)
    return plan
