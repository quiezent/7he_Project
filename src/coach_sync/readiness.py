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
from .io import write_json
from .paths import snapshots_dir
from .training_status import normalize_training_status_payload
from .time_utils import DEFAULT_TIMEZONE, iso_now, now_local, parse_date, today_local
from .wellness import normalize_wellness_payload
from .wellness_verification import build_wellness_verification


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
    from .io import read_json

    return read_json(files[-1], {})


def _dated_subjective_context(
    root: str | Path | None,
    target_date: date,
) -> tuple[dict, dict | None]:
    daily = load_daily_checkin(root, target_date.isoformat())
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

    score = 70.0
    confidence = "medium"
    hard_block = False
    reasons: list[dict] = []

    if subjective_warning:
        reasons.append(subjective_warning)

    if wellness is None:
        confidence = "low"
        score -= 5
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
            score -= 5
            reasons.append(
                {
                    "type": "stale_wellness",
                    "severity": "yellow",
                    "message": f"Latest Garmin wellness data is {data_age} day(s) old.",
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

    if training_status:
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
        if acwr_status and acwr_status not in {"optimal"}:
            score -= 8
            reasons.append(
                {
                    "type": "acute_chronic_load",
                    "severity": "yellow",
                    "message": f"Garmin acute/chronic workload status is {acwr_status}.",
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
    if spike_ratio is not None and spike_ratio > threshold:
        score -= 10
        reasons.append(
            {
                "type": "load_spike",
                "severity": "yellow",
                "message": f"7-day training load is {spike_ratio}x the prior 7 days.",
            }
        )

    score = max(0, min(100, round(score, 1)))
    level = readiness_level(score, hard_block)
    hard_session_guidance = "ok" if level == "green" else "caution" if level == "yellow" else "avoid"

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
