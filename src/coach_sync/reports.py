from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from .context import clearance_summary, load_context
from .evidence import load_activities, summarize_recent_training
from .io import read_json, write_json, write_text
from .paths import snapshots_dir
from .state import build_current_state
from .time_utils import DEFAULT_TIMEZONE, parse_date, today_local


def _recent_readiness(root: str | Path | None, days: int, target_date: date) -> list[dict]:
    start = target_date - timedelta(days=days - 1)
    out = []
    for path in snapshots_dir(root).glob("readiness_*.json"):
        try:
            snap_date = parse_date(path.stem.removeprefix("readiness_"))
        except ValueError:
            continue
        if snap_date and start <= snap_date <= target_date:
            out.append(read_json(path, {}))
    return sorted(out, key=lambda item: item.get("date") or "")


def weekly_report(root: str | Path | None = None, days: int = 7) -> dict:
    context = load_context(root)
    tz = context.get("athlete", {}).get("timezone", DEFAULT_TIMEZONE)
    target_date = today_local(tz)
    activities = load_activities(root)
    training = summarize_recent_training(activities, target_date)
    readiness = _recent_readiness(root, days, target_date)
    report = {
        "date": target_date.isoformat(),
        "days": days,
        "training": training,
        "readiness_days": readiness,
        "coach_focus": [
            "Keep outdoor and gym re-entry controlled until symptoms and load response are boringly stable.",
            "Build weekly repeatability before chasing expert-level intensity and impact exposure.",
        ],
    }
    write_json(snapshots_dir(root) / "weekly_report.json", report)
    write_text(
        snapshots_dir(root) / "weekly_report.txt",
        "\n".join(
            [
                f"Weekly Report - {target_date.isoformat()}",
                f"Window: {days} days",
                f"Sessions: {training['last_7_days']['sessions']}",
                f"Duration: {training['last_7_days']['duration_min']} min",
                f"Training load: {training['last_7_days']['training_load']}",
                "",
                "Coach focus:",
                *[f"- {item}" for item in report["coach_focus"]],
                "",
            ]
        ),
    )
    return report


def insight_memo(root: str | Path | None = None, days: int = 28) -> dict:
    state = build_current_state(root)
    memo = {
        "date": state["date"],
        "days": days,
        "phase": state.get("phase"),
        "readiness": state.get("readiness"),
        "training_load": state.get("training_load"),
        "insights": [
            "The stack is currently strongest when Garmin wellness and activities are synced daily.",
            "Use subjective check-ins only for what Garmin cannot see: hand symptoms, grip tolerance, and context.",
        ],
    }
    write_json(snapshots_dir(root) / "insight_memo.json", memo)
    return memo


def review_block(root: str | Path | None = None, days: int = 14, state: dict | None = None) -> dict:
    state = state or build_current_state(root)
    context = load_context(root)
    clearance = clearance_summary(context)
    clearance_dates = [
        parse_date(gate.get("date"))
        for gate in clearance.get("gates", {}).values()
        if gate.get("status") == "cleared" and gate.get("date")
    ]
    clearance_dates = [value for value in clearance_dates if value]
    full_clearance_date = max(clearance_dates) if clearance_dates else parse_date(state["date"])
    next_check = full_clearance_date + timedelta(days=days)
    block = {
        "date": state["date"],
        "review_type": "post_clearance_reentry",
        "full_clearance_date": full_clearance_date.isoformat(),
        "next_reentry_check": next_check.isoformat(),
        "items_to_track": [
            "Outdoor ride count and terrain consequence",
            "Gym grip and loading tolerance",
            "Pain, swelling, inflammation, and next-morning response",
            "Training load ramp versus prior week",
            "Nutrition support for key rides and gym days",
        ],
    }
    write_json(snapshots_dir(root) / "review_block.json", block)
    write_text(
        snapshots_dir(root) / "review_block.txt",
        "\n".join(
            [
                "Review Block",
                f"Full clearance: {block['full_clearance_date']}",
                f"Next re-entry check: {block['next_reentry_check']}",
                "",
                *[f"- {item}" for item in block["items_to_track"]],
                "",
            ]
        ),
    )
    return block


def microcycle_forecast(root: str | Path | None = None, days: int = 24) -> dict:
    state = build_current_state(root)
    start = parse_date(state["date"])
    assert start is not None
    entries = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        if offset < 14:
            focus = "re-entry aerobic ride / gym primer / symptom tracking"
        elif offset % 7 in {1, 3}:
            focus = "structured quality if readiness and symptoms support it"
        elif offset % 7 == 5:
            focus = "longer aerobic MTB or endurance ride"
        else:
            focus = "easy endurance, skills, gym, or recovery"
        entries.append({"date": day.isoformat(), "focus": focus})
    forecast = {"date": state["date"], "days": days, "entries": entries}
    write_json(snapshots_dir(root) / "microcycle_forecast.json", forecast)
    return forecast


def local_estimates(root: str | Path | None = None) -> dict:
    state = build_current_state(root)
    estimates = {
        "date": state["date"],
        "estimates": {
            "fitness_confidence": "low" if state["evidence_sources"]["activity_count"] == 0 else "medium",
            "note": "Power/threshold estimates require recent activities with power data.",
        },
    }
    write_json(snapshots_dir(root) / "local_estimates.json", estimates)
    return estimates


def intraday_trends(root: str | Path | None = None) -> dict:
    state = build_current_state(root)
    trends = {
        "date": state["date"],
        "status": "not_configured",
        "note": "Intraday trend ingestion is a future source; daily Garmin wellness is active.",
    }
    write_json(snapshots_dir(root) / "intraday_trends.json", trends)
    return trends


def weather_snapshot(root: str | Path | None = None) -> dict:
    state = build_current_state(root)
    weather = {
        "date": state["date"],
        "status": "not_configured",
        "note": "No weather provider is configured in this local rebuild yet.",
    }
    write_json(snapshots_dir(root) / "weather_snapshot.json", weather)
    return weather
