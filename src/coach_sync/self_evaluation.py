from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .evidence import load_activities
from .io import read_json, write_json, write_text
from .paths import snapshots_dir
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local


FEEL_LABELS = {
    0: "very_weak",
    25: "weak",
    50: "normal",
    75: "strong",
    100: "very_strong",
}

RPE_LABELS = {
    10: "very_light",
    20: "light",
    30: "moderate",
    40: "somewhat_hard",
    50: "hard",
    60: "hard_plus",
    70: "very_hard",
    80: "very_hard_plus",
    90: "extremely_hard",
    100: "maximum",
}

GARMIN_FEEL_COMPONENTS = ["clarity", "strength", "coordination"]


def _feel_out_of_5(value: float | None) -> int | None:
    """Map Garmin's categorical 0/25/50/75/100 values to its 1-5 display scale.

    Fail closed for off-grid values instead of truncating a value into a category.
    """
    if value is None or value not in FEEL_LABELS:
        return None
    return int(value / 25) + 1


def _feel_ordinal_display_out_of_10(value: float | None) -> int | None:
    """Return Clayton's requested 1-5 to 2-10 display remap, not an interval score."""
    display = _feel_out_of_5(value)
    return display * 2 if display is not None else None


def _summary(payload: dict) -> dict:
    summary = payload.get("summaryDTO")
    return summary if isinstance(summary, dict) else {}


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _feel_label(value: float | None) -> str | None:
    if value is None:
        return None
    return FEEL_LABELS.get(value, f"score_{value:g}")


def _rpe_out_of_10(value: float | None) -> float | None:
    if value is None or value not in RPE_LABELS:
        return None
    return round(value / 10, 1)


def _rpe_label(value: float | None) -> str | None:
    if value is None:
        return None
    return RPE_LABELS.get(value)


def summarize_activity_self_evaluation(payload: dict) -> dict:
    summary = _summary(payload)
    feel = _number(summary.get("directWorkoutFeel"))
    rpe = _number(summary.get("directWorkoutRpe"))
    return {
        "has_self_evaluation": feel is not None or rpe is not None,
        "feel_score": feel,
        "feel_label": _feel_label(feel),
        "feel_out_of_5": _feel_out_of_5(feel),
        "feel_ordinal_display_out_of_10": _feel_ordinal_display_out_of_10(feel),
        "feel_display_remap_semantics": "ordinal_display_only_not_comparable_to_rpe",
        "feel_construct": "athlete_state_composite",
        "rpe_score": rpe,
        "rpe_label": _rpe_label(rpe),
        "rpe_out_of_10": _rpe_out_of_10(rpe),
        "global_rpe_out_of_10": _rpe_out_of_10(rpe),
    }


def _read_index(root: str | Path | None) -> dict:
    payload = read_json(snapshots_dir(root) / "activity_self_evaluation_index.json", {})
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list):
        return {"activities": payload}
    return {"activities": []}


def _activity_lookup(root: str | Path | None) -> dict[str, dict]:
    return {str(activity.get("id")): activity for activity in load_activities(root)}


def _text_report(report: dict) -> str:
    lines = [
        f"Self Evaluation - {report['date']}",
        "",
        f"Checked activities: {report['checked_activities']}",
        f"Evaluated activities: {report['evaluated_activities']}",
        f"Coverage: {(report.get('coverage') or {}).get('status', 'unknown')}",
        "",
        "Recent Self Evaluations:",
    ]
    if report["recent_self_evaluations"]:
        for row in report["recent_self_evaluations"]:
            rpe = row.get("rpe_out_of_10")
            rpe_text = f"{rpe}/10" if rpe is not None else "unknown"
            lines.append(
                f"- {row.get('date')}: {row.get('category')} {row.get('name')} - "
                f"feel {row.get('feel_label') or 'unknown'} ({row.get('feel_score')}), "
                f"RPE {row.get('rpe_label') or 'unknown'} ({rpe_text})"
            )
    else:
        lines.append("- None")
    return "\n".join(lines) + "\n"


def build_self_evaluation_report(
    root: str | Path | None = None,
    for_date: str | date | None = None,
    lookback_days: int = 30,
) -> dict:
    target = parse_date(for_date) or today_local(DEFAULT_TIMEZONE)
    start = target - timedelta(days=max(1, lookback_days) - 1)
    index = _read_index(root)
    activities = _activity_lookup(root)
    rows = []

    for row in index.get("activities") or []:
        if not isinstance(row, dict):
            continue
        activity_id = str(row.get("activity_id") or row.get("id") or "")
        activity = activities.get(activity_id, {})
        act_date = parse_date(row.get("date") or activity.get("date"))
        if not act_date or act_date < start or act_date > target:
            continue
        feel_score = _number(row.get("feel_score"))
        rpe_score = _number(row.get("rpe_score"))
        feel_out_of_5 = _feel_out_of_5(feel_score)
        rpe_out_of_10 = _rpe_out_of_10(rpe_score)
        normalized = {
            "activity_id": activity_id,
            "date": act_date.isoformat(),
            "name": row.get("name") or activity.get("name"),
            "type": row.get("type") or activity.get("type"),
            "category": row.get("category") or activity.get("category"),
            "detail_fetch_ok": row.get("detail_fetch_ok"),
            "detail_fetch_error": row.get("detail_fetch_error"),
            "fetch": row.get("fetch"),
            "latest_attempt": row.get("latest_attempt"),
            "last_success_at": row.get("last_success_at"),
            "has_self_evaluation": feel_out_of_5 is not None or rpe_out_of_10 is not None,
            "feel_score": feel_score,
            "feel_label": _feel_label(feel_score),
            "feel_out_of_5": feel_out_of_5,
            "feel_ordinal_display_out_of_10": _feel_ordinal_display_out_of_10(feel_score),
            "feel_display_remap_semantics": "ordinal_display_only_not_comparable_to_rpe",
            "feel_construct": row.get("feel_construct") or "athlete_state_composite",
            "rpe_score": rpe_score,
            "rpe_label": _rpe_label(rpe_score),
            "rpe_out_of_10": rpe_out_of_10,
            "global_rpe_out_of_10": rpe_out_of_10,
        }
        rows.append(normalized)

    evaluated = [row for row in rows if row.get("has_self_evaluation")]
    recent = sorted(evaluated, key=lambda item: item.get("date") or "", reverse=True)[:10]
    eligible_ids = {
        str(activity.get("id"))
        for activity in activities.values()
        if activity.get("id")
        and activity.get("counts_for_training_load")
        and (activity_date := parse_date(activity.get("date"))) is not None
        and start <= activity_date <= target
    }
    indexed_ids = {
        row.get("activity_id") for row in rows if row.get("activity_id") in eligible_ids
    }
    successful_fetches = sum(
        1
        for row in rows
        if row.get("activity_id") in eligible_ids and row.get("detail_fetch_ok") is True
    )
    coverage_status = (
        "complete"
        if eligible_ids and indexed_ids == eligible_ids and successful_fetches == len(eligible_ids)
        else "partial"
        if indexed_ids
        else "missing"
    )
    flags = []
    failed_rows = [
        row
        for row in rows
        if row.get("activity_id") in eligible_ids and row.get("detail_fetch_ok") is False
    ]
    if failed_rows:
        flags.append(
            {
                "type": "self_evaluation_fetch_failures",
                "severity": "yellow",
                "count": len(failed_rows),
                "message": f"Garmin self-evaluation metadata failed for {len(failed_rows)} recent activity record(s).",
            }
        )
    cached_after_failed_refresh = [
        row
        for row in rows
        if row.get("activity_id") in eligible_ids
        and row.get("detail_fetch_ok") is True
        and isinstance(row.get("latest_attempt"), dict)
        and row["latest_attempt"].get("status") in {"failed", "unsupported"}
    ]
    if cached_after_failed_refresh:
        flags.append(
            {
                "type": "self_evaluation_refresh_failed_using_cached",
                "severity": "yellow",
                "count": len(cached_after_failed_refresh),
                "message": (
                    f"Garmin self-evaluation refresh failed for {len(cached_after_failed_refresh)} "
                    "recent activity record(s); using preserved last-known-good values."
                ),
            }
        )
    report = {
        "date": target.isoformat(),
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "source_index": "snapshots/activity_self_evaluation_index.json",
        "lookback_days": lookback_days,
        "evidence_ontology": {
            "garmin_feel": {
                "entity": "athlete_state",
                "construct": "one_indivisible_composite",
                "components": GARMIN_FEEL_COMPONENTS,
                "raw_to_display_scale": {"0": 1, "25": 2, "50": 3, "75": 4, "100": 5},
                "optional_out_of_10_display_remap": (
                    "Map 1-5 to even labels 2-10 only for Clayton's requested display; this is ordinal "
                    "and must not be compared numerically with RPE or treated as an interval scale."
                ),
                "illness_boundary": (
                    "Illness is separate evidence. Garmin Feel cannot prove illness absence, and a contrary "
                    "athlete illness report overrides a favorable Feel value."
                ),
            },
            "garmin_perceived_effort": {
                "entity": "delivered_session_effort",
                "construct": "whole_session_global_rpe",
                "normalization": "directWorkoutRpe divided by 10",
            },
            "separation_rule": (
                "Feel and Perceived Effort do not establish technical execution or a safety-contract outcome."
            ),
        },
        "checked_activities": len(rows),
        "evaluated_activities": len(evaluated),
        "coverage": {
            "status": coverage_status,
            "eligible_activities": len(eligible_ids),
            "indexed_activities": len(indexed_ids),
            "successful_fetches": successful_fetches,
            "missing_activities": max(0, len(eligible_ids) - len(indexed_ids)),
        },
        "recent_self_evaluations": recent,
        "flags": flags,
    }
    write_json(snapshots_dir(root) / "self_evaluation_report.json", report)
    write_text(snapshots_dir(root) / "self_evaluation_report.txt", _text_report(report))
    return report
