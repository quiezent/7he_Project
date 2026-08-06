from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from .checkin import load_daily_checkin
from .context import load_context
from .evidence import (
    as_number,
    find_value,
    load_activities,
    load_latest_training_status,
    load_latest_wellness,
    summarize_recent_training,
)
from .io import read_json, write_json
from .paths import snapshots_dir
from .training_status import normalize_training_status_payload
from .time_utils import DEFAULT_TIMEZONE, iso_now, now_local, parse_date, today_local
from .wellness import normalize_wellness_payload
from .wellness_verification import build_wellness_verification


def _as_non_negative_int(value: object, default: int) -> int:
    try:
        as_int = int(value)
    except (TypeError, ValueError):
        return default
    return as_int if as_int >= 0 else default


def _freshness_thresholds(
    training_rules: dict,
    warning_key: str,
    hard_key: str,
    default_warning: int,
    default_hard: int | None = None,
) -> tuple[int, int]:
    warning = _as_non_negative_int(training_rules.get(warning_key), default_warning)
    hard = _as_non_negative_int(training_rules.get(hard_key), default_hard if default_hard is not None else warning)
    if hard < warning:
        hard = warning
    return warning, hard


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip().lower()


def _checkin_value(checkin: dict, *names: str) -> Any:
    for name in names:
        if name in checkin and checkin[name] not in ("", None):
            return checkin[name]
    return None


def _latest_feedback(root: str | Path | None = None) -> dict:
    files = sorted(Path(root or ".").resolve().joinpath("input").glob("feedback_*.json"))
    if not files:
        return {}
    return read_json(files[-1], {})


def _feedback_for_date(root: str | Path | None, target_date: date) -> dict:
    path = (
        Path(root or ".")
        .resolve()
        .joinpath("input", f"feedback_{target_date.isoformat()}.json")
    )
    payload = read_json(path, {})
    if not isinstance(payload, dict):
        return {}
    try:
        payload_date = parse_date(payload.get("date"))
    except (TypeError, ValueError):
        payload_date = None
    if payload_date != target_date:
        return {}
    return payload


def _dated_subjective_context(
    root: str | Path | None,
    target_date: date,
) -> tuple[dict, dict | None]:
    daily = load_daily_checkin(root, target_date.isoformat())
    try:
        daily_date = parse_date(daily.get("date")) if daily else None
    except (TypeError, ValueError):
        daily_date = None
    if daily and daily_date == target_date:
        return daily, None

    # A stale convenience markdown file must not mask the explicitly dated,
    # structured feedback for the decision date.
    dated_feedback = _feedback_for_date(root, target_date)
    if dated_feedback:
        return dated_feedback, None

    latest = daily or _latest_feedback(root)
    if not latest:
        return {}, None
    raw_date = latest.get("date")
    try:
        entry_date = parse_date(raw_date)
    except (TypeError, ValueError):
        entry_date = None
    if entry_date == target_date:
        return latest, None
    return (
        {},
        {
            "type": "stale_checkin",
            "severity": "yellow",
            "message": (
                f"Ignoring subjective check-in dated {raw_date}; "
                f"target date is {target_date.isoformat()}."
            ),
        },
    )


def _readiness_accuracy_plan(
    target_date: date,
    is_today: bool,
    confidence: str,
    blockers: list[dict],
    body_battery_current: float | None,
) -> dict:
    blocker_types = [item.get("type") for item in blockers]
    high_blockers = [item for item in blockers if item.get("priority") == "high"]
    medium_blockers = [
        item for item in blockers if item.get("priority") in {"medium", "high"} and item not in high_blockers
    ]
    sync_cmd = "python tools/sync_connect.py --wellness-days 30 --activity-limit 0"
    if "wellness_missing" in blocker_types or "training_status_missing" in blocker_types:
        return {
            "decision_quality": "insufficient",
            "decision_risk": "high",
            "recommended_next_step": {
                "status": "required",
                "priority": 1,
                "label": "Sync fresh Garmin wellness + training status",
                "rationale": (
                    "Readiness is missing required Garmin signal for the target date; "
                    "without it, confidence is not reliable."
                ),
                "commands": [sync_cmd, "python tools/current_state.py", "python tools/coach_packet.py"],
                "expected_effect": "Rebuild readiness inputs and restore actionable confidence.",
            },
            "blockers": blockers,
        }
    if any(
        item.get("type") in {"future_wellness_snapshot", "future_training_status"}
        for item in blockers
    ):
        return {
            "decision_quality": "insufficient",
            "decision_risk": "high",
            "recommended_next_step": {
                "status": "required",
                "priority": 1,
                "label": "Refresh using target-date snapshots only",
                "rationale": (
                    "A future-dated Garmin record was found for this target date. "
                    "Treating it as current is backward-in-time contamination."
                ),
                "commands": [sync_cmd, "python tools/current_state.py", "python tools/coach_packet.py"],
                "expected_effect": "Forces readiness scoring to use target-date evidence only.",
            },
            "blockers": blockers,
        }
    if high_blockers:
        return {
            "decision_quality": "limited",
            "decision_risk": "high",
            "recommended_next_step": {
                "status": "required",
                "priority": 2,
                "label": "Refresh stale core readiness sources",
                "rationale": (
                    f"{len(high_blockers)} high-impact freshness blocker(s) remain "
                    f"for {target_date.isoformat()}. "
                    f"{'Do this first if this is a training day.' if is_today else ''}"
                ).strip(),
                "commands": [sync_cmd, "python tools/current_state.py", "python tools/coach_packet.py"],
                "expected_effect": "Improves confidence for hard-session guidance and lowers decision noise.",
            },
            "blockers": blockers,
        }
    if medium_blockers:
        return {
            "decision_quality": "limited",
            "decision_risk": "moderate",
            "recommended_next_step": {
                "status": "recommended",
                "priority": 3,
                "label": "Refresh freshness (non-blocking)",
                "rationale": (
                    f"{len(medium_blockers)} readiness sources are behind but not yet hard-blocking."
                ),
                "commands": [sync_cmd, "python tools/current_state.py"],
                "expected_effect": "Lets hard-session confidence move from likely-caution to clearer guidance.",
            },
            "blockers": blockers,
        }
    if body_battery_current is not None and body_battery_current < 40:
        return {
            "decision_quality": "adequate" if confidence in {"medium", "high"} else "limited",
            "decision_risk": "moderate",
            "recommended_next_step": {
                "status": "current_limit",
                "priority": 4,
                "label": "Cap session by current Body Battery",
                "rationale": (
                    "Freshness is sufficient; use current Body Battery as the primary limiter "
                    "for session load and intensity."
                ),
                "commands": [],
                "expected_effect": "Maintains safety while avoiding unnecessary hard-session bans.",
            },
            "blockers": blockers,
        }
    return {
        "decision_quality": "adequate" if not blockers and confidence in {"medium", "high"} else "limited",
        "decision_risk": "low" if not blockers else "moderate",
        "recommended_next_step": {
            "status": "ready",
            "priority": 5,
            "label": "Proceed on current evidence",
            "rationale": "No high-impact readiness-data blockers are present.",
            "commands": [],
            "expected_effect": "Use current readiness score in plan and training call.",
        },
        "blockers": blockers,
    }


def readiness_level(score: float, hard_block: bool = False) -> str:
    if hard_block or score < 45:
        return "red"
    if score < 70:
        return "yellow"
    return "green"


def build_readiness(root: str | Path | None = None, for_date: str | date | None = None) -> dict:
    context = load_context(root)
    tz = context.get("athlete", {}).get("timezone", DEFAULT_TIMEZONE)
    target_date = parse_date(for_date) or today_local(tz)
    wellness_date, wellness = load_latest_wellness(root, target_date)
    training_status_date, training_status = load_latest_training_status(root, target_date)
    checkin, subjective_warning = _dated_subjective_context(root, target_date)
    activities = load_activities(root)
    training = summarize_recent_training(activities, target_date)
    normalized_wellness = normalize_wellness_payload(wellness) if wellness else {}
    wellness_verification = build_wellness_verification(root, target_date) if wellness else {}
    normalized_training_status = normalize_training_status_payload(
        training_status,
        training_status_date.isoformat() if training_status_date else None,
    )
    training_rules = context.get("training_rules", {})
    wellness_warning_days, wellness_hard_days = _freshness_thresholds(
        training_rules,
        "stale_data_warning_days",
        "wellness_stale_hard_days",
        default_warning=1,
    )
    training_status_warning_days, training_status_hard_days = _freshness_thresholds(
        training_rules,
        "training_status_stale_warning_days",
        "training_status_stale_hard_days",
        default_warning=wellness_warning_days,
        default_hard=wellness_hard_days,
    )

    score = 70.0
    confidence = "medium"
    hard_block = False
    data_blockers: list[dict] = []
    body_battery_current = None
    reasons: list[dict] = []
    core_sleep_hours: float | None = None
    core_sleep_guidance: str | None = None
    core_sleep_score_ceiling: float | None = None

    if subjective_warning:
        reasons.append(subjective_warning)

    if wellness is None:
        confidence = "low"
        score -= 5
        data_blockers.append(
            {
                "type": "wellness_missing",
                "priority": "high",
                "message": "No Garmin wellness snapshot is available for readiness scoring.",
            }
        )
        reasons.append(
            {
                "type": "data_missing",
                "severity": "yellow",
                "message": "No Garmin wellness snapshot is available for readiness scoring.",
            }
        )
    else:
        data_age = (target_date - wellness_date).days if wellness_date else None
        if data_age is not None and data_age > 0:
            confidence = "low"
            if data_age > wellness_hard_days:
                hard_block = True
                score -= 10
                data_blockers.append(
                    {
                        "type": "stale_wellness",
                        "priority": "high",
                        "age_days": data_age,
                        "source": "wellness",
                    }
                )
                reasons.append(
                    {
                        "type": "stale_wellness",
                        "severity": "yellow",
                        "message": (
                            f"Latest Garmin wellness data is {data_age} day(s) old. "
                            "Hard-session guidance confidence is limited."
                        ),
                        "latest_date": wellness_date.isoformat(),
                    }
                )
            else:
                score -= 5
                data_blockers.append(
                    {
                        "type": "stale_wellness",
                        "priority": "medium",
                        "age_days": data_age,
                        "source": "wellness",
                    }
                )
                reasons.append(
                    {
                        "type": "stale_wellness",
                        "severity": "yellow",
                        "message": f"Latest Garmin wellness data is {data_age} day(s) old.",
                        "latest_date": wellness_date.isoformat(),
                    }
                )
        elif data_age is not None and data_age < 0:
            confidence = "low"
            hard_block = True
            score -= 10
            data_blockers.append(
                {
                    "type": "future_wellness_snapshot",
                    "priority": "high",
                    "age_days": data_age,
                    "source": "wellness",
                }
            )
            reasons.append(
                {
                    "type": "future_wellness_snapshot",
                    "severity": "yellow",
                    "message": (
                        "Garmin wellness snapshot date is in the future relative to the target date; "
                        "treat readiness as uncertain."
                    ),
                    "latest_date": wellness_date.isoformat(),
                }
            )

        sleep_score = normalized_wellness.get("sleep_score") or as_number(
            find_value(wellness, ("sleepScore", "overallSleepScore", "sleep_score"))
        )
        if sleep_score is not None:
            if sleep_score < 60:
                score -= 15
                reasons.append(
                    {
                        "type": "sleep",
                        "severity": "yellow",
                        "message": f"Sleep score is low at {sleep_score:g}.",
                    }
                )
            elif sleep_score >= 80:
                score += 5

        core_sleep_hours = as_number(normalized_wellness.get("sleep_hours"))
        if core_sleep_hours is None:
            sleep_seconds = as_number(find_value(wellness, ("sleepTimeSeconds", "sleep_time_seconds")))
            if sleep_seconds is not None:
                core_sleep_hours = round(sleep_seconds / 3600, 2)
        if core_sleep_hours is not None and core_sleep_hours < 6:
            if core_sleep_hours < 5:
                score -= 20
                hard_block = True
                core_sleep_guidance = "avoid"
                core_sleep_score_ceiling = 44
                band = "under_5_hours"
                message = (
                    f"Primary sleep was only {core_sleep_hours:g} h. This is a strong limiter: "
                    "avoid hard training and technical consequence."
                )
            elif core_sleep_hours < 5.5:
                score -= 15
                core_sleep_guidance = "caution"
                core_sleep_score_ceiling = 69
                band = "5_to_under_5_5_hours"
                message = (
                    f"Primary sleep was only {core_sleep_hours:g} h. Treat readiness as acute "
                    "short-sleep amber: require a consequence-sensitive field gate and do not "
                    "give unrestricted hard-session clearance."
                )
            else:
                score -= 8
                core_sleep_guidance = "caution"
                core_sleep_score_ceiling = 69
                band = "5_5_to_under_6_hours"
                message = (
                    f"Primary sleep was only {core_sleep_hours:g} h. Keep hard and technical work "
                    "capped even when sleep score, HRV, or Body Battery look reassuring."
                )
            reasons.append(
                {
                    "type": "primary_short_sleep",
                    "severity": "yellow",
                    "message": (
                        f"{message} A reported nap may improve alertness, but it does not erase "
                        "the uncertainty from short primary sleep."
                    ),
                    "core_sleep_hours": core_sleep_hours,
                    "duration_band": band,
                    "hard_session_effect": core_sleep_guidance,
                }
            )

        body_battery_current = normalized_wellness.get("body_battery_current") or as_number(
            find_value(wellness, ("bodyBattery", "body_battery", "bb"))
        )
        body_battery_wake = normalized_wellness.get("body_battery_wake")
        verified_morning_anchor = normalized_wellness.get("body_battery_verified_morning_anchor")
        body_battery_anchor_source = normalized_wellness.get("body_battery_verified_anchor_source")
        body_battery_for_wake_scoring = body_battery_wake
        if (
            verified_morning_anchor is not None
            and (
                body_battery_for_wake_scoring is None
                or verified_morning_anchor > body_battery_for_wake_scoring
            )
        ):
            body_battery_for_wake_scoring = verified_morning_anchor
            if body_battery_anchor_source == "post_wake_recharge_peak":
                wake_text = f"{body_battery_wake:g}" if body_battery_wake is not None else "missing"
                reasons.append(
                    {
                        "type": "body_battery_verified_recharge",
                        "severity": "info",
                        "message": (
                            "Body Battery rose after Garmin's reported wake value: "
                            f"{wake_text} -> {verified_morning_anchor:g}. "
                            "Using the verified morning anchor for readiness scoring."
                        ),
                    }
                )
        is_today = target_date == today_local(tz)
        local_hour = now_local(tz).hour if is_today else None
        if body_battery_for_wake_scoring is not None and body_battery_for_wake_scoring < 65:
            score -= 8 if body_battery_for_wake_scoring < 55 else 5
            label = (
                "Verified morning Body Battery anchor"
                if body_battery_for_wake_scoring != body_battery_wake
                else "Wake Body Battery"
            )
            reasons.append(
                {
                    "type": "body_battery_wake",
                    "severity": "yellow",
                    "message": f"{label} is modest at {body_battery_for_wake_scoring:g}.",
                }
            )
        use_current_body_battery = is_today or body_battery_wake is None
        if use_current_body_battery and body_battery_current is not None and body_battery_current < 40:
            if is_today and local_hour is not None and local_hour >= 16:
                score -= 5
                message = (
                    f"Current Body Battery is {body_battery_current:g} in the evening; "
                    "use it as an intraday limiter, not a full-day readiness defect."
                )
            else:
                score -= 12 if body_battery_current < 25 else 5
                message = f"Current Body Battery is low at {body_battery_current:g}."
            reasons.append(
                {
                    "type": "body_battery_current",
                    "severity": "yellow",
                    "message": message,
                }
            )

        hrv_status = _text(
            normalized_wellness.get("hrv_status")
            or find_value(wellness, ("hrvStatus", "hrv_status", "status"))
        )
        if hrv_status:
            if any(term in hrv_status for term in ("low", "poor", "unbalanced")):
                score -= 10
                reasons.append(
                    {
                        "type": "hrv",
                        "severity": "yellow",
                        "message": f"HRV status is {hrv_status}.",
                    }
                )
            elif "balanced" in hrv_status:
                score += 3

    if not training_status:
        confidence = "low"
        score -= 5
        data_blockers.append(
            {
                "type": "training_status_missing",
                "priority": "high",
                "message": "No Garmin training status snapshot is available for readiness scoring.",
            }
        )
        reasons.append(
            {
                "type": "training_status_missing",
                "severity": "yellow",
                "message": "No Garmin training status snapshot is available for readiness scoring.",
            }
        )
    elif training_status_date is None or training_status_date <= target_date:
        status_text = _text(normalized_training_status.get("training_status_feedback")) or _text(
            find_value(training_status, ("trainingStatus", "training_status", "status"))
        )
        if any(term in status_text for term in ("strained", "overreaching", "unproductive")):
            score -= 15
            reasons.append(
                {
                    "type": "training_status",
                    "severity": "yellow",
                    "message": f"Garmin training status is {status_text}.",
                }
            )
        acwr_status = _text(normalized_training_status.get("acute_chronic", {}).get("status"))
        if acwr_status and acwr_status not in {"optimal", "low"}:
            score -= 8
            reasons.append(
                {
                    "type": "acute_chronic_load",
                    "severity": "yellow",
                    "message": f"Garmin acute/chronic workload status is {acwr_status}.",
                }
            )
    if training_status_date is not None:
        status_age = (target_date - training_status_date).days
        if status_age > 0:
            if status_age > training_status_hard_days:
                hard_block = True
                score -= 8
                data_blockers.append(
                    {
                        "type": "stale_training_status",
                        "priority": "high",
                        "age_days": status_age,
                        "source": "training_status",
                    }
                )
                reasons.append(
                    {
                        "type": "stale_training_status",
                        "severity": "yellow",
                        "message": (
                            f"Latest Garmin training status is {status_age} day(s) old. "
                            "Hard-session confidence is limited."
                        ),
                        "latest_training_status_date": training_status_date.isoformat(),
                    }
                )
            else:
                score -= 5
                data_blockers.append(
                    {
                        "type": "stale_training_status",
                        "priority": "medium",
                        "age_days": status_age,
                        "source": "training_status",
                    }
                )
                reasons.append(
                    {
                        "type": "stale_training_status",
                        "severity": "yellow",
                        "message": f"Latest Garmin training status is {status_age} day(s) old.",
                        "latest_training_status_date": training_status_date.isoformat(),
                    }
                )
        elif status_age < 0:
            hard_block = True
            score -= 8
            data_blockers.append(
                {
                    "type": "future_training_status",
                    "priority": "high",
                    "age_days": status_age,
                    "source": "training_status",
                }
            )
            reasons.append(
                {
                    "type": "future_training_status",
                    "severity": "yellow",
                    "message": (
                        "Garmin training status date is in the future relative to the target date; "
                        "treat readiness as uncertain."
                    ),
                    "latest_training_status_date": training_status_date.isoformat(),
                }
            )

    next_morning = _text(
        _checkin_value(checkin, "next_morning_response", "morning_response", "next_day_response")
    )

    if next_morning and any(term in next_morning for term in ("worse", "poor", "bad", "exhausted", "dead")):
        score -= 20
        reasons.append(
            {
                "type": "next_morning_response",
                "severity": "yellow",
                "message": f"Next-morning response: {next_morning}.",
            }
        )

    spike_ratio = training.get("acute_load_spike_ratio")
    threshold = context.get("training_rules", {}).get("acute_chronic_load_spike_ratio", 1.5)
    acwr = normalized_training_status.get("acute_chronic") or {}
    acwr_status = _text(acwr.get("status"))
    acwr_ratio = as_number(acwr.get("ratio"))
    garmin_acwr_optimal = acwr_status == "optimal" and (acwr_ratio is None or acwr_ratio <= 1.0)
    if spike_ratio is not None and spike_ratio > threshold and not garmin_acwr_optimal:
        score -= 10
        reasons.append(
            {
                "type": "load_spike",
                "severity": "yellow",
                "message": f"7-day training load is {spike_ratio}x the prior 7 days.",
            }
        )

    if core_sleep_score_ceiling is not None:
        # Duration is an independent gate. Strong modeled signals must not turn
        # a sub-six-hour primary sleep into unrestricted green clearance.
        score = min(score, core_sleep_score_ceiling)
    score = max(0, min(100, round(score, 1)))
    is_today = target_date == today_local(tz)
    readiness_accuracy = _readiness_accuracy_plan(
        target_date,
        is_today=is_today,
        confidence=confidence,
        blockers=data_blockers,
        body_battery_current=body_battery_current,
    )
    level = readiness_level(score, hard_block)
    hard_session_guidance = "ok" if level == "green" else "caution" if level == "yellow" else "avoid"
    if core_sleep_guidance == "avoid":
        hard_session_guidance = "avoid"

    artifact = {
        "date": target_date.isoformat(),
        "generated_at": iso_now(tz),
        "readiness_score": score,
        "readiness_level": level,
        "confidence": confidence,
        "hard_session_guidance": hard_session_guidance,
        "reasons": reasons,
        "subjective": {
            "next_morning_response": next_morning or None,
        },
        "sleep_duration_gate": {
            "core_sleep_hours": core_sleep_hours,
            "hard_session_effect": core_sleep_guidance or "none",
            "nap_rule": (
                "A nap may improve alertness but does not remove a primary short-sleep gate "
                "without independently verified total-sleep and post-nap clarity evidence."
            ),
        },
        "evidence": {
            "wellness_date": wellness_date.isoformat() if wellness_date else None,
            "training_status_date": (
                training_status_date.isoformat() if training_status_date else None
            ),
            "checkin_date": checkin.get("date"),
            "recent_training": training,
            "wellness_verification": {
                "verification_status": wellness_verification.get("verification_status"),
                "confidence": wellness_verification.get("confidence"),
                "recommended_morning_anchor": (
                    wellness_verification.get("body_battery_interpretation") or {}
                ).get("recommended_morning_anchor"),
                "recommended_anchor_source": (
                    wellness_verification.get("body_battery_interpretation") or {}
                ).get("recommended_anchor_source"),
                "post_wake_recharge": (
                    (
                        wellness_verification.get("body_battery_interpretation") or {}
                    ).get("post_wake_recharge")
                    or {}
                ).get("detected"),
            },
        },
        "readiness_accuracy": readiness_accuracy,
    }
    readiness_features = {
        "date": target_date.isoformat(),
        "generated_at": iso_now(tz),
        "wellness": {
            "sleep_score": normalized_wellness.get("sleep_score"),
            "sleep_hours": normalized_wellness.get("sleep_hours"),
            "sleep_quality": normalized_wellness.get("sleep_quality"),
            "overnight_hrv": normalized_wellness.get("overnight_hrv"),
            "hrv_status": normalized_wellness.get("hrv_status"),
            "hrv_weekly_avg": normalized_wellness.get("hrv_weekly_avg"),
            "resting_hr": normalized_wellness.get("resting_hr"),
            "body_battery_wake": normalized_wellness.get("body_battery_wake"),
            "body_battery_current": normalized_wellness.get("body_battery_current"),
            "body_battery_verified_morning_anchor": normalized_wellness.get(
                "body_battery_verified_morning_anchor"
            ),
            "body_battery_verified_anchor_source": normalized_wellness.get(
                "body_battery_verified_anchor_source"
            ),
            "body_battery_verification_status": normalized_wellness.get(
                "body_battery_verification_status"
            ),
            "avg_stress": normalized_wellness.get("avg_stress"),
            "avg_respiration": normalized_wellness.get("avg_respiration"),
            "avg_spo2": normalized_wellness.get("avg_spo2"),
        },
        "training_status": {
            "training_status_feedback": normalized_training_status.get("training_status_feedback"),
            "acwr": normalized_training_status.get("acute_chronic"),
            "load_focus": normalized_training_status.get("load_focus"),
            "vo2max": normalized_training_status.get("vo2max"),
            "flags": normalized_training_status.get("flags"),
        },
        "recent_training": training,
        "subjective": artifact["subjective"],
        "score_result": {
            "readiness_score": score,
            "readiness_level": level,
            "confidence": confidence,
            "hard_session_guidance": hard_session_guidance,
        },
    }
    write_json(
        snapshots_dir(root) / f"readiness_features_{target_date.isoformat()}.json",
        readiness_features,
    )
    write_json(snapshots_dir(root) / f"readiness_{target_date.isoformat()}.json", artifact)
    return artifact
