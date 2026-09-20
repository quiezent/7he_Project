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
    return FEEL_LABELS.get(int(value), f"score_{value:g}")


def _rpe_out_of_10(value: float | None) -> float | None:
    if value is None:
        return None
    if 0 <= value <= 100:
        return round(value / 10, 1)
    return value


def _rpe_label(value: float | None) -> str | None:
    if value is None:
        return None
    return RPE_LABELS.get(int(value), f"score_{value:g}")


def summarize_activity_self_evaluation(payload: dict) -> dict:
    summary = _summary(payload)
    feel = _number(summary.get("directWorkoutFeel"))
    rpe = _number(summary.get("directWorkoutRpe"))
    return {
        "has_self_evaluation": feel is not None or rpe is not None,
        "feel_score": feel,
        "feel_label": _feel_label(feel),
        "rpe_score": rpe,
        "rpe_label": _rpe_label(rpe),
        "rpe_out_of_10": _rpe_out_of_10(rpe),
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
        normalized = {
            "activity_id": activity_id,
            "date": act_date.isoformat(),
            "name": row.get("name") or activity.get("name"),
            "type": row.get("type") or activity.get("type"),
            "category": row.get("category") or activity.get("category"),
            "detail_fetch_ok": row.get("detail_fetch_ok"),
            "has_self_evaluation": bool(row.get("has_self_evaluation")),
            "feel_score": row.get("feel_score"),
            "feel_label": row.get("feel_label"),
            "rpe_score": row.get("rpe_score"),
            "rpe_label": row.get("rpe_label"),
            "rpe_out_of_10": row.get("rpe_out_of_10"),
        }
        rows.append(normalized)

    evaluated = [row for row in rows if row.get("has_self_evaluation")]
    recent = sorted(evaluated, key=lambda item: item.get("date") or "", reverse=True)[:10]
    report = {
        "date": target.isoformat(),
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "source_index": "snapshots/activity_self_evaluation_index.json",
        "lookback_days": lookback_days,
        "checked_activities": len(rows),
        "evaluated_activities": len(evaluated),
        "recent_self_evaluations": recent,
    }
    write_json(snapshots_dir(root) / "self_evaluation_report.json", report)
    write_text(snapshots_dir(root) / "self_evaluation_report.txt", _text_report(report))
    return report
