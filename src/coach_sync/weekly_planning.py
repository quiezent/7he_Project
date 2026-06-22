from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .context import load_context
from .evidence import as_number
from .garmin_arbitration import build_garmin_arbitration
from .io import write_json, write_text
from .paths import snapshots_dir
from .planning import SESSION_CONTRACT_FIELDS
from .state import build_current_state
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local


def _week_start(target: date) -> date:
    return target - timedelta(days=target.weekday())


def _iso_week_key(day: date) -> str:
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _day_name(day: date) -> str:
    return day.strftime("%A")


def _training_rules(context: dict[str, Any]) -> dict[str, int]:
    continuity = (
        context.get("training_rules", {})
        .get("bike_specific_continuity", {})
    )
    minimum_bike = int(continuity.get("minimum_bike_touches_per_week") or 2)
    preferred_bike = int(continuity.get("preferred_rebuild_bike_touches_per_week") or max(3, minimum_bike))
    protect_mtb = int(continuity.get("protect_mtb_exposures_per_week") or 2)
    max_mtb = int(continuity.get("maximum_mtb_exposures_per_week") or 3)
    return {
        "minimum_bike_touches": minimum_bike,
        "preferred_bike_touches": max(preferred_bike, minimum_bike),
        "protect_mtb_exposures": protect_mtb,
        "maximum_mtb_exposures": max(max_mtb, protect_mtb),
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


def _current_wellness(state: dict[str, Any]) -> dict[str, Any]:
    return (state.get("wellness_trends") or {}).get("latest") or {}


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


def _scheduled_rest(day: date) -> dict[str, Any]:
    return {
        "date": day.isoformat(),
        "day_name": _day_name(day),
        "title": "Sabbath rest",
        "type": "scheduled_rest",
        "modality": "rest",
        "priority": "hard_constraint",
        "optional": False,
        "mtb_exposure": False,
        "duration_min": 0,
        "intensity": "recovery",
        "purpose": "Honor Sunday Sabbath as a hard no-exercise day.",
        "execution_rules": [
            "No ride, gym, intervals, strength loading, or planned training.",
            "Normal life, worship, family time, meals, and easy unwinding are enough.",
        ],
        "expected_result": {
            "recovery": "Physical and mental space before the next week.",
        },
    }


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
    rules: dict[str, int],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    readiness = state.get("readiness") or {}
    wellness = _current_wellness(state)
    level = readiness.get("readiness_level")
    hrv_status = str(wellness.get("hrv_status") or "").lower()
    wake_bb = as_number(wellness.get("body_battery_wake"))
    monday_tempo = level == "green" and "unbalanced" not in hrv_status and (wake_bb is None or wake_bb >= 65)

    sessions: list[dict[str, Any]] = []
    monday = week_start
    tuesday = week_start + timedelta(days=1)
    wednesday = week_start + timedelta(days=2)
    thursday = week_start + timedelta(days=3)
    friday = week_start + timedelta(days=4)
    saturday = week_start + timedelta(days=5)
    sunday = week_start + timedelta(days=6)

    if monday_tempo:
        sessions.append(
            _session(
                monday,
                title="Indoor tempo/torque",
                session_type="indoor_tempo_torque",
                modality="bike_indoor",
                priority="key_engine",
                duration_min=70,
                intensity="moderate",
                load_target="controlled, not threshold",
                purpose="Start the week with controlled bike-specific engine work while readiness is clean.",
                dose={
                    "main_set": "3 x 10 min tempo/torque with 4 min easy between.",
                    "power_anchor": "Use current controlled tempo, not stale FTP.",
                    "cap": "No extra interval if the final block is not repeatable.",
                },
                adaptation_hypothesis=(
                    "Controlled tempo should rebuild sustainable climbing power without adding trail consequence or anaerobic debt."
                ),
                execution_rules=[
                    "Keep cadence and breathing controlled.",
                    "Use HR/RPE drift to cap the dose.",
                    "Finish able to ride skillfully the next day.",
                ],
                expected_result={
                    "physiology": "High-aerobic durability without threshold survival.",
                    "next_day": "No loss of MTB skill readiness.",
                },
                readiness_gate=_quality_gate(),
            )
        )
        tuesday_title = "Recovery and activation"
        tuesday_type = "recovery_activation"
        tuesday_priority = "support"
        tuesday_duration = 25
        tuesday_purpose = "Absorb Monday's engine dose before the MTB quality day."
    else:
        sessions.append(
            _session(
                monday,
                title="Easy bike continuity",
                session_type="easy_bike_continuity",
                modality="bike",
                priority="support",
                duration_min=45,
                intensity="easy",
                load_target="low",
                purpose="Keep bike rhythm without carrying weekend debt into the build week.",
                dose={
                    "duration_min": "30-45",
                    "intensity": "Z1-Z2 / RPE 2-4",
                    "cap": "Finish fresher than you started.",
                },
                adaptation_hypothesis=(
                    "A low-cost bike touch preserves continuity while allowing Tuesday or Wednesday to carry the useful stimulus."
                ),
                execution_rules=[
                    "Use indoor trainer or low-consequence terrain.",
                    "No climbs that become work.",
                    "No technical progression.",
                ],
                expected_result={
                    "garmin_load": "low",
                    "next_day": "same or better readiness",
                },
                readiness_gate=_recovery_gate(),
            )
        )
        tuesday_title = "Indoor tempo/torque"
        tuesday_type = "indoor_tempo_torque"
        tuesday_priority = "key_engine"
        tuesday_duration = 70
        tuesday_purpose = "Add the week's controlled engine stimulus after Monday confirms recovery."

    sessions.append(
        _session(
            tuesday,
            title=tuesday_title,
            session_type=tuesday_type,
            modality="bike_indoor" if tuesday_type == "indoor_tempo_torque" else "recovery",
            priority=tuesday_priority,
            duration_min=tuesday_duration,
            intensity="moderate" if tuesday_type == "indoor_tempo_torque" else "recovery",
            load_target="controlled" if tuesday_type == "indoor_tempo_torque" else "very low",
            purpose=tuesday_purpose,
            dose={
                "main_set": "3 x 10 min tempo/torque with 4 min easy between."
                if tuesday_type == "indoor_tempo_torque"
                else "20-30 min easy cardio, mobility, or light activation.",
                "yellow_day_option": "2 x 10 min only if HRV/RHR is still off."
                if tuesday_type == "indoor_tempo_torque"
                else "Keep it purely restorative.",
                "cap": "No extra work.",
            },
            adaptation_hypothesis=(
                "Tempo work builds repeatable climbing support without the noise of trail intensity."
                if tuesday_type == "indoor_tempo_torque"
                else "A small support dose should improve readiness for the MTB quality day."
            ),
            execution_rules=[
                "Use current RPE/HR response rather than stale FTP.",
                "Keep the final repetition repeatable.",
                "Do not turn this into threshold testing.",
            ]
            if tuesday_type == "indoor_tempo_torque"
            else [
                "No DOMS-producing strength.",
                "No high-intensity cardio.",
                "Stop if it does not improve freshness.",
            ],
            expected_result={
                "physiology": "High-aerobic support for Kiara climbs.",
                "next_day": "Ready for MTB skill quality.",
            }
            if tuesday_type == "indoor_tempo_torque"
            else {
                "recovery": "Better freshness for Wednesday.",
            },
            readiness_gate=_quality_gate() if tuesday_type == "indoor_tempo_torque" else _recovery_gate(),
        )
    )

    sessions.append(
        _session(
            wednesday,
            title="Kiara MTB quality/skill",
            session_type="mtb_quality_skill",
            modality="mtb",
            priority="key_skill",
            duration_min=90,
            intensity="moderate",
            mtb_exposure=True,
            load_target="moderate, capped by skill quality",
            purpose="Build repeatable technical speed while keeping the session narrow and measurable.",
            dose={
                "venue": "Kiara, Stumpjumper or chosen skill bike.",
                "laps": "3-4 controlled 2K / 2K+ style loops.",
                "cap": "No KOM chasing and no extra trail novelty.",
            },
            adaptation_hypothesis=(
                "Repeated same-venue laps should convert fitness into braking timing, corner exits, and clipless body-position confidence."
            ),
            execution_rules=[
                "One technical target only.",
                "Climb steady enough that descents stay precise.",
                "Review first descent versus final descent.",
            ],
            expected_result={
                "technical": "More centred posture, cleaner braking release, better corner-exit speed.",
                "physiology": "Moderate MTB load without deep recovery debt.",
            },
            readiness_gate=_quality_gate(),
        )
    )

    sessions.append(
        _session(
            thursday,
            title="Recovery and durability support",
            session_type="recovery_activation",
            modality="recovery_gym",
            priority="support",
            duration_min=30,
            intensity="recovery",
            load_target="very low",
            purpose="Prepare for Friday/Saturday trail quality without stealing freshness.",
            dose={
                "options": "20-30 min easy cardio, mobility, or light activation strength.",
                "strength": "Split squat/lunge, hinge, row, push, core, calf, all sub-DOMS.",
                "cap": "If it compromises Friday, it was too much.",
            },
            adaptation_hypothesis=(
                "A small support dose improves tissue readiness and posture durability without interfering with key MTB sessions."
            ),
            execution_rules=[
                "No heavy legs.",
                "No metabolic conditioning.",
                "Leave the gym feeling better than when you entered.",
            ],
            expected_result={
                "recovery": "Better trail readiness for Friday.",
            },
            readiness_gate=_recovery_gate(),
        )
    )

    sessions.append(
        _session(
            friday,
            title="Kiara Enduro durability",
            session_type="mtb_durability_enduro",
            modality="mtb",
            priority="key_durability",
            duration_min=120,
            intensity="moderate_hard",
            mtb_exposure=True,
            load_target="meaningful but bounded",
            purpose="Rebuild enduro repeatability: climb, recover, descend with precision, then repeat.",
            dose={
                "venue": "Kiara or equivalent enduro repeatability venue.",
                "laps": "3 climb-plus-descent loops; 4th only if first 3 are technically sharp.",
                "cap": "Stop adding loops when descent quality drops.",
            },
            adaptation_hypothesis=(
                "A bounded enduro-density day should improve the ability to finish the final descent with the aggression and precision of the first."
            ),
            execution_rules=[
                "Climb controlled; descend deliberately.",
                "Treat final descent quality as the main metric.",
                "No setup experiments unless the session is explicitly converted into a setup test.",
            ],
            expected_result={
                "technical": "Stable braking, posture, and line choice late in the ride.",
                "physiology": "Enduro-specific fatigue resistance without uncontrolled overreach.",
            },
            readiness_gate=_quality_gate(),
            post_session_review_fields=[
                *_review_fields(),
                "loop_count",
                "final_descent_quality_vs_first",
                "braking_fatigue",
                "upper_body_fatigue",
            ],
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
        )
    )
    sessions.append(_scheduled_rest(sunday))

    exposure_summary = {
        "protected_mtb_exposures": rules["protect_mtb_exposures"],
        "maximum_normal_build_mtb_exposures": rules["maximum_mtb_exposures"],
        "planned_key_mtb_exposures": sum(1 for item in sessions if item.get("mtb_exposure") and not item.get("optional")),
        "optional_mtb_exposures": sum(1 for item in sessions if item.get("mtb_exposure") and item.get("optional")),
        "rule": "Protect 2 MTB exposures; allow up to 3 when readiness, logistics, and density support it. More than 3 is a race/event block, not a default build week.",
    }
    return sessions, exposure_summary


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
            "gate": "density",
            "rule": "If a key MTB day drifts harder than written, the next non-key day becomes recovery.",
        },
        {
            "gate": "MTB exposure cap",
            "rule": "Normal build weeks protect 2 MTB exposures and allow a 3rd only as capped skill-transfer.",
        },
        {
            "gate": "Sabbath",
            "rule": "Sunday remains no planned exercise regardless of readiness.",
        },
    ]


def _text_summary(plan: dict[str, Any]) -> str:
    lines = [
        f"Weekly Plan - {plan['week_key']}",
        f"Week: {plan['week_start']} to {plan['week_end']}",
        f"Status: {plan['status']}",
        "",
        f"Objective: {plan['weekly_objective']['primary']}",
        f"Physiology: {plan['weekly_objective']['physiological_target']}",
        f"Technical: {plan['weekly_objective']['technical_target']}",
        "",
        "Targets:",
        f"- Load range: {plan['targets']['training_load_range'][0]}-{plan['targets']['training_load_range'][1]}",
        f"- Bike touches: {plan['targets']['bike_touches']['minimum']}-{plan['targets']['bike_touches']['preferred']}",
        f"- MTB exposures: protect {plan['targets']['mtb_exposures']['protected_mtb_exposures']}, max {plan['targets']['mtb_exposures']['maximum_normal_build_mtb_exposures']}",
        "",
        "Sessions:",
    ]
    for session in plan["sessions"]:
        marker = "optional " if session.get("optional") else ""
        lines.append(
            f"- {session['day_name']} {session['date']}: {marker}{session['title']} "
            f"({session['modality']}, {session['intensity']})"
        )
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
    freshness = state.get("data_freshness") or {}
    readiness = state.get("readiness") or {}
    arbitration = build_garmin_arbitration(state)

    status = "ready"
    if freshness.get("status") != "current":
        status = "provisional_stale_data"
    elif readiness.get("readiness_level") == "red":
        status = "downshifted_readiness"

    dated_json = f"snapshots/weekly_plan_{week_key}.json"
    dated_text = f"snapshots/weekly_plan_{week_key}.txt"
    plan = {
        "artifact_type": "weekly_training_plan",
        "date": target.isoformat(),
        "generated_at": iso_now(tz),
        "week_key": week_key,
        "week_start": start.isoformat(),
        "week_end": end.isoformat(),
        "status": status,
        "planning_basis": {
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
        "weekly_objective": _weekly_objective(state, load_focus),
        "targets": {
            **load_target,
            "bike_touches": {
                "minimum": rules["minimum_bike_touches"],
                "preferred": rules["preferred_bike_touches"],
                "note": "Bike-specific continuity is the durable fitness currency; elliptical/gym do not replace this.",
            },
            "mtb_exposures": exposure_summary,
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
