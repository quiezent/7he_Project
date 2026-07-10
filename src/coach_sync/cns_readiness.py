from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from .evidence import as_number
from .io import read_json, write_json, write_text
from .paths import snapshots_dir
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local
from .wellness import build_wellness_trends


NEGATIVE_PATTERNS = {
    "brain_fog": [
        r"\bbrain\s+(still\s+has\s+)?fog\b",
        r"\bfoggy\b",
        r"\bmental\s+fog\b",
    ],
    "sick_or_systemic": [
        r"\bfeel\s+sick\b",
        r"\bsystemic\s+(shutdown|shut\s*down)\b",
        r"\bnot\s+normal\b",
    ],
    "cns_fatigue": [
        r"\bcns\s+fatigue\b",
        r"\bcentral\s+nervous\s+system\b",
        r"\bneural\s+fatigue\b",
    ],
    "processing_delay": [
        r"\bdelayed\s+processing\b",
        r"\bslow\s+processing\b",
        r"\bdelayed\s+line\b",
        r"\breaction\s+time\b",
    ],
    "weakness": [
        r"\bvery\s+weak\b",
        r"\bweak\b",
        r"\bflat\b",
    ],
}

POSITIVE_PATTERNS = {
    "no_brain_fog": [
        r"\bno\s+brain\s+fog\b",
        r"\bwithout\s+brain\s+fog\b",
        r"\bbrain\s+fog\s+(is\s+)?(gone|resolved|cleared)\b",
    ],
    "feeling_better": [
        r"\bfeeling\s+better\b",
        r"\bfeel\s+better\b",
        r"\bback\s+and\s+feeling\s+better\b",
    ],
    "motivated": [
        r"\bmotivated\b",
        r"\beager\b",
    ],
    "groove_or_memory": [
        r"\bback\s+to\s+the\s+groove\b",
        r"\bmuscle\s+memory\b",
        r"\brhythm\b",
    ],
}


def _matches(text: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _feedback_for_date(root: str | Path | None, target: date) -> dict:
    path = Path(root or ".").resolve() / "input" / f"feedback_{target.isoformat()}.json"
    payload = read_json(path, {})
    return payload if isinstance(payload, dict) else {}


def _feedback_text(payload: dict) -> str:
    pieces: list[str] = []
    for entry in payload.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        for key in (
            "subjective_report",
            "coaching_interpretation",
            "recovery_priority",
            "coaching_decision",
        ):
            value = entry.get(key)
            if value:
                pieces.append(str(value))
        sleep_context = entry.get("subjective_sleep_context")
        if isinstance(sleep_context, dict):
            pieces.extend(str(value) for value in sleep_context.values() if value)
    return "\n".join(pieces)


def _latest_wellness(
    root: str | Path | None,
    target: date,
    wellness_trends: dict | None,
) -> dict:
    trends = wellness_trends or read_json(snapshots_dir(root) / "wellness_trends.json", {})
    if not trends or parse_date(trends.get("date")) != target:
        try:
            trends = build_wellness_trends(root, target)
        except Exception:
            trends = {}
    latest = trends.get("latest") if isinstance(trends, dict) else {}
    if not isinstance(latest, dict):
        return {}
    return {"date": trends.get("date"), **latest}


def _training_status(
    root: str | Path | None,
    target: date,
    training_status_current: dict | None,
) -> dict:
    status = training_status_current or read_json(snapshots_dir(root) / "garmin_training_status_current.json", {})
    if isinstance(status, dict) and parse_date(status.get("date")) == target:
        return status
    return {}


def _training_load(root: str | Path | None, training_load: dict | None) -> dict:
    payload = training_load or read_json(snapshots_dir(root) / "training_load.json", {})
    return payload if isinstance(payload, dict) else {}


def _self_evaluation(root: str | Path | None, self_evaluation: dict | None) -> dict:
    payload = self_evaluation or read_json(snapshots_dir(root) / "self_evaluation_report.json", {})
    return payload if isinstance(payload, dict) else {}


def _add(
    drivers: list[dict],
    score_ref: dict[str, float],
    points: float,
    source: str,
    reason: str,
    value: Any = None,
) -> None:
    score_ref["score"] += points
    drivers.append(
        {
            "source": source,
            "points": round(points, 1),
            "reason": reason,
            "value": value,
        }
    )


def _status(score: float) -> str:
    if score < 45:
        return "impaired"
    if score < 60:
        return "compromised"
    if score < 75:
        return "watch"
    return "ready"


def _session_ceiling(status: str) -> dict:
    if status == "ready":
        return {
            "level": "normal_if_physical_readiness_allows",
            "rule": "Normal skill work is allowed only if Garmin readiness, symptoms, and route consequence agree.",
        }
    if status == "watch":
        return {
            "level": "controlled_skill_only",
            "rule": "Allow low-to-moderate consequence skill practice; cap speed, novelty, jumps, and enduro simulation.",
        }
    if status == "compromised":
        return {
            "level": "low_consequence_repetition_only",
            "rule": "Use easy repetition or recovery movement only; no speed hunting, jump progression, race simulation, or setup testing.",
        }
    return {
        "level": "recovery_only",
        "rule": "No technical consequence, no MTB quality, no intervals, and no gym loading.",
    }


def _subjective_signals(text: str, drivers: list[dict], score_ref: dict[str, float]) -> list[dict]:
    signals: list[dict] = []
    positive_spans = []
    for name, patterns in POSITIVE_PATTERNS.items():
        if _matches(text, patterns):
            signals.append({"type": name, "direction": "positive"})
            points = {
                "no_brain_fog": 8,
                "feeling_better": 5,
                "motivated": 3,
                "groove_or_memory": 3,
            }.get(name, 2)
            _add(drivers, score_ref, points, "subjective", f"Positive CNS report: {name}.")
            for pattern in patterns:
                positive_spans.extend(re.finditer(pattern, text, flags=re.IGNORECASE))

    protected = text
    for match in positive_spans:
        start, end = match.span()
        protected = protected[:start] + (" " * (end - start)) + protected[end:]

    for name, patterns in NEGATIVE_PATTERNS.items():
        if _matches(protected, patterns):
            signals.append({"type": name, "direction": "negative"})
            points = {
                "brain_fog": -25,
                "sick_or_systemic": -22,
                "cns_fatigue": -16,
                "processing_delay": -15,
                "weakness": -8,
            }.get(name, -8)
            _add(drivers, score_ref, points, "subjective", f"Negative CNS report: {name}.")
    return signals


def _self_eval_signals(
    target: date,
    report: dict,
    drivers: list[dict],
    score_ref: dict[str, float],
) -> list[dict]:
    signals: list[dict] = []
    for row in report.get("recent_self_evaluations") or []:
        if parse_date(row.get("date")) != target:
            continue
        feel = str(row.get("feel_label") or "").lower()
        rpe = as_number(row.get("rpe_score"))
        if feel == "very_weak":
            _add(drivers, score_ref, -18, "self_evaluation", "Garmin feel was very weak.", row)
            signals.append({"type": "very_weak_feel", "direction": "negative", "row": row})
        elif feel == "weak":
            _add(drivers, score_ref, -8, "self_evaluation", "Garmin feel was weak.", row)
            signals.append({"type": "weak_feel", "direction": "negative", "row": row})
        elif feel in {"strong", "very_strong"}:
            _add(drivers, score_ref, 5, "self_evaluation", "Garmin feel was strong.", row)
            signals.append({"type": "strong_feel", "direction": "positive", "row": row})
        if feel in {"weak", "very_weak"} and rpe is not None and rpe <= 30:
            _add(
                drivers,
                score_ref,
                -5,
                "self_evaluation",
                "Low RPE with weak feel suggests systemic/CNS cost rather than muscular effort.",
                row,
            )
    return signals


def _latest_training_signals(
    target: date,
    training: dict,
    drivers: list[dict],
    score_ref: dict[str, float],
) -> list[dict]:
    signals: list[dict] = []
    recent = training.get("recent_technical_activities") or []
    if not recent:
        latest = training.get("latest_training_activity") or training.get("latest_activity") or {}
        recent = [latest] if isinstance(latest, dict) else []

    for activity in recent:
        if not isinstance(activity, dict):
            continue
        activity_date = parse_date(activity.get("date"))
        age_days = (target - activity_date).days if activity_date else None
        if age_days not in {0, 1, 2}:
            continue
        carryover = {0: 1.0, 1: 0.65, 2: 0.35}[age_days]
        category = str(activity.get("category") or "").lower()
        duration = as_number(activity.get("duration_min"))
        load = as_number(activity.get("training_load"))
        zones = activity.get("hr_zone_min") or {}
        z4 = as_number(zones.get("z4")) or 0
        z5 = as_number(zones.get("z5")) or 0
        high_intensity = z4 + z5
        context = {"age_days": age_days, "carryover": carryover}

        if category == "mtb" and duration is not None and duration >= 75:
            _add(
                drivers,
                score_ref,
                -6 * carryover,
                "recent_technical_activity",
                "MTB duration carries technical/CNS cost into the following 48 hours.",
                {**context, "duration_min": duration},
            )
            signals.append({"type": "mtb_duration_cost", "direction": "negative", **context})
        if category == "mtb" and load is not None and load >= 100:
            _add(
                drivers,
                score_ref,
                -5 * carryover,
                "recent_technical_activity",
                "Meaningful MTB load carries post-stress processing cost into the following 48 hours.",
                {**context, "training_load": load},
            )
            signals.append({"type": "mtb_load_cost", "direction": "negative", **context})
        if category == "mtb" and high_intensity >= 10:
            _add(
                drivers,
                score_ref,
                -5 * carryover,
                "recent_technical_activity",
                "High-HR trail minutes carry technical processing cost beyond the activity day.",
                {**context, "high_intensity_min": round(high_intensity, 1)},
            )
            signals.append({"type": "trail_high_intensity_cost", "direction": "negative", **context})
    return signals


def build_cns_readiness(
    root: str | Path | None = None,
    for_date: str | date | None = None,
    *,
    wellness_trends: dict | None = None,
    training_status_current: dict | None = None,
    training_load: dict | None = None,
    self_evaluation: dict | None = None,
) -> dict:
    target = parse_date(for_date) or today_local(DEFAULT_TIMEZONE)
    latest_wellness = _latest_wellness(root, target, wellness_trends)
    status_payload = _training_status(root, target, training_status_current)
    training = _training_load(root, training_load)
    self_eval = _self_evaluation(root, self_evaluation)
    feedback = _feedback_for_date(root, target)
    feedback_text = _feedback_text(feedback)

    score_ref = {"score": 75.0}
    drivers: list[dict] = []
    flags: list[dict] = []

    sleep_score = as_number(latest_wellness.get("sleep_score"))
    if sleep_score is not None:
        if sleep_score < 60:
            _add(drivers, score_ref, -10, "wellness", "Low sleep score suppresses CNS readiness.", sleep_score)
        elif sleep_score >= 80:
            _add(drivers, score_ref, 4, "wellness", "Good sleep score supports CNS readiness.", sleep_score)

    body_battery_anchor = (
        as_number(latest_wellness.get("body_battery_verified_morning_anchor"))
        or as_number(latest_wellness.get("body_battery_wake"))
    )
    current_body_battery = as_number(latest_wellness.get("body_battery_current"))
    if body_battery_anchor is not None:
        if body_battery_anchor < 45:
            _add(drivers, score_ref, -8, "wellness", "Low morning Body Battery limits CNS reserve.", body_battery_anchor)
        elif body_battery_anchor >= 75:
            _add(drivers, score_ref, 5, "wellness", "Strong morning Body Battery supports CNS reserve.", body_battery_anchor)
    if current_body_battery is not None and current_body_battery < 30:
        _add(drivers, score_ref, -6, "wellness", "Low current Body Battery is an intraday CNS limiter.", current_body_battery)

    hrv_status = str(latest_wellness.get("hrv_status") or "").lower()
    overnight_hrv = as_number(latest_wellness.get("overnight_hrv"))
    hrv_low = as_number(latest_wellness.get("hrv_balanced_low"))
    if hrv_status:
        if "low" in hrv_status:
            _add(drivers, score_ref, -18, "wellness", "HRV status is LOW.", latest_wellness.get("hrv_status"))
        elif "unbalanced" in hrv_status or "poor" in hrv_status:
            _add(
                drivers,
                score_ref,
                -12,
                "wellness",
                "HRV status is not stable.",
                latest_wellness.get("hrv_status"),
            )
        elif "balanced" in hrv_status:
            _add(drivers, score_ref, 4, "wellness", "HRV status is balanced.", latest_wellness.get("hrv_status"))
    if overnight_hrv is not None and hrv_low is not None and overnight_hrv < hrv_low:
        gap = round(hrv_low - overnight_hrv, 1)
        _add(drivers, score_ref, -4 if gap <= 5 else -7, "wellness", "Overnight HRV is below baseline.", gap)

    rhr = as_number(latest_wellness.get("resting_hr"))
    rhr_avg = as_number(latest_wellness.get("rhr_7d_avg"))
    if rhr is not None and rhr_avg is not None:
        delta = round(rhr - rhr_avg, 1)
        if delta >= 3:
            _add(drivers, score_ref, -8, "wellness", "Resting HR is elevated versus 7-day average.", delta)
        elif delta >= 2:
            _add(drivers, score_ref, -5, "wellness", "Resting HR is mildly elevated.", delta)
        elif delta <= -1:
            _add(drivers, score_ref, 2, "wellness", "Resting HR is not elevated.", delta)

    avg_stress = as_number(latest_wellness.get("avg_stress"))
    sleep_stress = as_number(latest_wellness.get("sleep_stress"))
    if avg_stress is not None:
        if avg_stress >= 40:
            _add(drivers, score_ref, -8, "wellness", "Daily stress is high.", avg_stress)
        elif avg_stress >= 35:
            _add(drivers, score_ref, -4, "wellness", "Daily stress is elevated.", avg_stress)
        elif avg_stress <= 30:
            _add(drivers, score_ref, 3, "wellness", "Daily stress is controlled.", avg_stress)
    if sleep_stress is not None:
        if sleep_stress >= 35:
            _add(drivers, score_ref, -8, "wellness", "Sleep stress was high.", sleep_stress)
        elif sleep_stress >= 25:
            _add(drivers, score_ref, -4, "wellness", "Sleep stress was elevated.", sleep_stress)
        elif sleep_stress <= 20:
            _add(drivers, score_ref, 3, "wellness", "Sleep stress was controlled.", sleep_stress)

    training_status = str(status_payload.get("training_status_feedback") or "").lower()
    if "strained" in training_status:
        _add(drivers, score_ref, -10, "training_status", "Garmin training status is strained.", training_status)
    elif any(term in training_status for term in ("productive", "peaking")):
        _add(drivers, score_ref, 4, "training_status", "Garmin training status supports readiness.", training_status)

    subjective_signals = _subjective_signals(feedback_text.lower(), drivers, score_ref)
    self_eval_signals = _self_eval_signals(target, self_eval, drivers, score_ref)
    activity_signals = _latest_training_signals(target, training, drivers, score_ref)

    score = max(0.0, min(100.0, round(score_ref["score"], 1)))
    status = _status(score)
    ceiling = _session_ceiling(status)
    if status in {"impaired", "compromised"}:
        flags.append(
            {
                "type": "cns_downshift",
                "severity": "yellow",
                "message": ceiling["rule"],
            }
        )
    elif status == "watch":
        flags.append(
            {
                "type": "cns_watch",
                "severity": "info",
                "message": ceiling["rule"],
            }
        )

    wellness_current = parse_date(latest_wellness.get("date")) == target
    confidence = "high" if feedback_text and wellness_current else "medium" if wellness_current else "low"
    artifact = {
        "artifact_type": "cns_readiness",
        "date": target.isoformat(),
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "score": score,
        "status": status,
        "confidence": confidence,
        "session_ceiling": ceiling,
        "decision_use": "technical_consequence_ceiling",
        "drivers": drivers,
        "flags": flags,
        "signals": {
            "subjective": subjective_signals,
            "self_evaluation": self_eval_signals,
            "latest_activity": activity_signals,
        },
        "inputs": {
            "wellness": {
                "sleep_score": sleep_score,
                "sleep_hours": latest_wellness.get("sleep_hours"),
                "sleep_stress": sleep_stress,
                "body_battery_anchor": body_battery_anchor,
                "body_battery_current": current_body_battery,
                "overnight_hrv": overnight_hrv,
                "hrv_status": latest_wellness.get("hrv_status"),
                "hrv_balanced_low": hrv_low,
                "resting_hr": rhr,
                "rhr_7d_avg": rhr_avg,
                "avg_stress": avg_stress,
            },
            "training_status": {
                "date": status_payload.get("date"),
                "training_status_feedback": status_payload.get("training_status_feedback"),
                "acute_chronic": status_payload.get("acute_chronic"),
            },
            "feedback_file": f"input/feedback_{target.isoformat()}.json" if feedback else None,
        },
        "interpretation": _interpretation(status),
    }
    write_json(snapshots_dir(root) / "cns_readiness.json", artifact)
    write_json(snapshots_dir(root) / f"cns_readiness_{target.isoformat()}.json", artifact)
    write_text(snapshots_dir(root) / "cns_readiness.txt", _text_report(artifact))
    return artifact


def _interpretation(status: str) -> str:
    if status == "ready":
        return "CNS signals do not independently limit technical work; physical readiness and route consequence still govern the final call."
    if status == "watch":
        return "CNS is usable but not robust; keep the session narrow and stop before decision speed degrades."
    if status == "compromised":
        return "CNS processing is not reliable enough for speed, jumps, enduro simulation, or stacked variables."
    return "CNS readiness is impaired; prioritize recovery and avoid technical consequence."


def _text_report(artifact: dict) -> str:
    lines = [
        f"CNS Readiness - {artifact['date']}",
        "",
        f"Score: {artifact['score']} ({artifact['status']})",
        f"Decision use: {artifact['decision_use']}",
        f"Session ceiling: {artifact['session_ceiling']['level']}",
        f"Rule: {artifact['session_ceiling']['rule']}",
        "",
        "Drivers:",
    ]
    for driver in artifact.get("drivers") or []:
        sign = "+" if driver.get("points", 0) >= 0 else ""
        lines.append(f"- {driver.get('source')}: {sign}{driver.get('points')} - {driver.get('reason')}")
    if not artifact.get("drivers"):
        lines.append("- None")
    lines.extend(["", "Interpretation:", artifact.get("interpretation") or ""])
    return "\n".join(lines) + "\n"
