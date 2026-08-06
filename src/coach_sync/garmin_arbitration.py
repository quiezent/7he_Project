from __future__ import annotations

from typing import Any

from .evidence import as_number


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


def _load_bucket(value: Any, target_min: Any, target_max: Any) -> dict[str, Any]:
    load = as_number(value)
    low = as_number(target_min)
    high = as_number(target_max)
    if load is None:
        return {
            "value": None,
            "target_min": low,
            "target_max": high,
            "position": "unknown",
            "target_ratio": None,
            "near_upper": False,
        }

    if high is not None and load > high:
        position = "above_target"
    elif low is not None and load < low:
        position = "below_target"
    elif low is not None or high is not None:
        position = "in_target"
    else:
        position = "unknown_target"

    target_ratio = round(load / high, 3) if high else None
    near_upper = bool(high and load >= 0.85 * high)
    return {
        "value": load,
        "target_min": low,
        "target_max": high,
        "position": position,
        "target_ratio": target_ratio,
        "near_upper": near_upper,
    }


def _load_focus_buckets(load_focus: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        "low_aerobic": _load_bucket(
            load_focus.get("low_aerobic"),
            load_focus.get("low_aerobic_target_min"),
            load_focus.get("low_aerobic_target_max"),
        ),
        "high_aerobic": _load_bucket(
            load_focus.get("high_aerobic"),
            load_focus.get("high_aerobic_target_min"),
            load_focus.get("high_aerobic_target_max"),
        ),
        "anaerobic": _load_bucket(
            load_focus.get("anaerobic"),
            load_focus.get("anaerobic_target_min"),
            load_focus.get("anaerobic_target_max"),
        ),
    }


def build_garmin_arbitration(state: dict[str, Any]) -> dict[str, Any]:
    """Turn Garmin diagnosis into a bounded session-ceiling recommendation.

    This deliberately sits below readiness/Sabbath and above generic session
    factories: Garmin can adjust the dose ceiling, but it does not prescribe
    trail execution or override data-quality and recovery constraints.
    """

    freshness = state.get("data_freshness") or {}
    readiness = state.get("readiness") or {}
    training_status = state.get("training_status_current") or {}
    load_focus = training_status.get("load_focus") or {}
    acute_chronic = training_status.get("acute_chronic") or {}
    buckets = _load_focus_buckets(load_focus)

    feedback = training_status.get("training_status_feedback")
    family = _feedback_family(feedback)
    acwr_status = str(acute_chronic.get("status") or "").upper()
    acwr_ratio = as_number(acute_chronic.get("ratio"))
    readiness_level = readiness.get("readiness_level")

    reasons: list[str] = []
    allowed: list[str] = []
    avoid: list[str] = []
    action = "hold_plan"
    ceiling = "planned_session"
    confidence = "medium"
    stimulus = "planned"
    summary_override = None

    if not training_status:
        return {
            "status": "missing",
            "recommended_action": "hold_plan",
            "ceiling": "planned_session",
            "stimulus": "planned",
            "confidence": "low",
            "training_status_feedback": None,
            "feedback_family": "unknown",
            "acwr": {"status": None, "ratio": None},
            "load_focus": buckets,
            "allowed_stimulus": [],
            "avoid": ["Do not raise the session ceiling from Garmin diagnosis because training status is missing."],
            "reasons": ["No Garmin training status is available."],
            "summary": "Garmin diagnosis is unavailable; hold the written plan.",
        }

    if freshness.get("status") not in {"current", None}:
        return {
            "status": "freshness_limited",
            "recommended_action": "no_hard_guidance",
            "ceiling": "data_limited_aerobic",
            "stimulus": "aerobic_only",
            "confidence": "low",
            "training_status_feedback": feedback,
            "feedback_family": family,
            "acwr": {"status": acwr_status or None, "ratio": acwr_ratio},
            "load_focus": buckets,
            "allowed_stimulus": ["Easy aerobic continuity only until Garmin freshness is restored."],
            "avoid": ["Do not use stale Garmin status to justify intensity."],
            "reasons": [freshness.get("message") or "Garmin freshness is not current."],
            "summary": "Garmin diagnosis cannot raise the ceiling while freshness is limited.",
        }

    if readiness_level == "red":
        reasons.append("Readiness is red; Garmin diagnosis cannot raise the session ceiling.")
        action = "downshift"
        ceiling = "recovery"
        stimulus = "recovery"
        confidence = "high"
        allowed.append("Recovery, mobility, or very easy circulation only.")
        avoid.append("No hard ride, gym loading, or technical consequence.")
    elif training_status.get("training_paused"):
        reasons.append("Garmin training status is paused.")
        action = "downshift"
        ceiling = "recovery_or_easy"
        stimulus = "recovery"
        allowed.append("Easy aerobic continuity only if it improves freshness.")
        avoid.append("No planned hard stimulus while Garmin training status is paused.")
    elif acwr_status not in {"", "OPTIMAL", "LOW"}:
        reasons.append(f"Garmin ACWR status is {acwr_status}, indicating a non-low load warning.")
        action = "downshift"
        ceiling = "aerobic_continuity"
        stimulus = "easy_to_moderate"
        allowed.append("Capped aerobic work if readiness agrees.")
        avoid.append("Do not add intensity until ACWR returns to optimal or the coach has a strong reason.")
    elif acwr_ratio is not None and acwr_ratio >= 1.45:
        reasons.append(f"Garmin ACWR ratio is {acwr_ratio}, close to spike territory.")
        action = "downshift"
        ceiling = "aerobic_continuity"
        stimulus = "easy_to_moderate"
        allowed.append("Capped aerobic work only.")
        avoid.append("No extra high-aerobic, anaerobic, or durability extension.")
    elif family == "recovery" and acwr_status == "LOW":
        reasons.append(
            "Garmin Recovery status coincides with LOW ACWR; this is compatible with low acute load "
            "and is not, by itself, evidence of overload."
        )
        action = "downshift"
        ceiling = "controlled_familiar_skill_or_aerobic_continuity"
        stimulus = "easy_to_moderate"
        allowed.append(
            "Bounded familiar technique or aerobic continuity if readiness, CNS status, "
            "subjective sharpness, and route consequence agree."
        )
        avoid.append(
            "Do not use low ACWR to justify an abrupt load spike, open-ended extension, "
            "novel technical consequence, or hard repeatability."
        )
        summary_override = (
            "Garmin Recovery with LOW ACWR narrows the ceiling without vetoing "
            "bounded familiar technique."
        )
    elif family in {"recovery", "unproductive", "overreaching", "strained"}:
        reasons.append(f"Garmin training status family is {family}.")
        action = "downshift"
        ceiling = "aerobic_continuity"
        stimulus = "easy_to_moderate"
        allowed.append("Technique or aerobic continuity only if subjective sharpness is good.")
        avoid.append("Do not chase a fitness stimulus from a poor Garmin status family.")
    else:
        low = buckets["low_aerobic"]
        high = buckets["high_aerobic"]
        anaerobic = buckets["anaerobic"]
        acwr_ok = acwr_status in {"", "OPTIMAL"} and (acwr_ratio is None or acwr_ratio < 1.45)
        productive_or_peaking = family in {"productive", "peaking"}

        if acwr_status == "LOW":
            reasons.append("Garmin ACWR is LOW; this reflects low acute load rather than overload.")
            allowed.append("Hold the written plan when readiness, CNS status, and route consequence agree.")
            avoid.append("Do not use low ACWR alone to justify an abrupt load spike or open-ended session extension.")

        if anaerobic["position"] == "above_target" or anaerobic["near_upper"]:
            avoid.append("Avoid extra anaerobic/VO2/sprint stacking; Garmin anaerobic load is near or above the upper target.")

        if productive_or_peaking and acwr_ok and readiness_level in {"green", "yellow"}:
            if low["position"] == "above_target" and high["position"] in {"below_target", "in_target"}:
                action = "controlled_upgrade"
                ceiling = "controlled_mtb_repeatability"
                stimulus = "controlled_high_aerobic_mtb"
                reasons.append(
                    "Garmin is Productive/Peaking with optimal ACWR while load focus is skewed toward low aerobic."
                )
                allowed.extend(
                    [
                        "Controlled high-aerobic or MTB repeatability stimulus.",
                        "Repeatable climb/descent loops with slow-to-tempo climbs and smooth descents.",
                    ]
                )
                avoid.append("Do not answer the low-aerobic skew with only more easy volume.")
            elif high["position"] == "below_target":
                action = "controlled_upgrade"
                ceiling = "controlled_high_aerobic"
                stimulus = "controlled_high_aerobic"
                reasons.append("Garmin status supports a controlled high-aerobic stimulus.")
                allowed.append("Tempo, torque, or repeatability work that stays below survival intensity.")
            else:
                reasons.append("Garmin diagnosis supports holding the written plan.")
                allowed.append("Follow the planned session and protect the next key ride.")
        else:
            reasons.append("Garmin diagnosis does not justify raising the session ceiling.")
            allowed.append("Hold the written plan unless subjective readiness and coaching context say otherwise.")

    if not avoid:
        avoid.append("Avoid mission creep that changes the adaptation target without naming it first.")

    summary = summary_override or {
        "controlled_upgrade": "Garmin diagnosis permits a controlled upgrade, not an open-ended hard day.",
        "downshift": "Garmin diagnosis lowers the ceiling for today.",
        "no_hard_guidance": "Garmin diagnosis cannot support hard guidance because freshness is limited.",
        "hold_plan": "Garmin diagnosis supports holding the written plan.",
    }.get(action, "Garmin diagnosis reviewed.")

    return {
        "status": "available",
        "recommended_action": action,
        "ceiling": ceiling,
        "stimulus": stimulus,
        "confidence": confidence,
        "training_status_feedback": feedback,
        "feedback_family": family,
        "acwr": {"status": acwr_status or None, "ratio": acwr_ratio},
        "load_focus": buckets,
        "allowed_stimulus": allowed,
        "avoid": avoid,
        "reasons": reasons,
        "summary": summary,
    }
