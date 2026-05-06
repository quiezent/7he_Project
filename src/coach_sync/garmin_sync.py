from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

from .briefing import build_daily_brief
from .cleanup import cleanup_derived
from .coach_packet import build_coach_packet
from .data_quality import build_data_quality_report
from .data_inventory import build_data_inventory
from .io import write_json
from .paths import activities_dir, ensure_layout, snapshots_dir
from .planning import build_today_plan
from .reports import review_block
from .state import build_current_state
from .time_utils import DEFAULT_TIMEZONE, iso_now, today_local


def _safe_call(label: str, func: Callable[..., Any], *args: Any) -> dict:
    try:
        return {"label": label, "ok": True, "data": func(*args)}
    except Exception as exc:  # Live Garmin API methods differ across versions.
        return {"label": label, "ok": False, "error": str(exc)}


def _write_wellness_payload(root: str | Path | None, day: str, payloads: list[dict]) -> None:
    combined = {
        "date": day,
        "source": "garminconnect",
        "payloads": payloads,
    }
    write_json(snapshots_dir(root) / f"garmin_wellness_{day}.json", combined)


def _fetch_live(root: str | Path | None, wellness_days: int, activity_limit: int) -> dict:
    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    if not email or not password:
        return {
            "status": "skipped",
            "reason": "GARMIN_EMAIL and GARMIN_PASSWORD are not both set.",
        }
    try:
        from garminconnect import Garmin
    except Exception as exc:
        return {"status": "skipped", "reason": f"garminconnect import failed: {exc}"}

    tokenstore = os.getenv("GARMINTOKENS")
    default_tokenstore = Path.home() / ".garminconnect"
    if not tokenstore and default_tokenstore.exists():
        tokenstore = str(default_tokenstore)

    try:
        client = Garmin(email, password)
        if tokenstore:
            client.login(tokenstore=tokenstore)
            auth_method = "tokenstore"
        else:
            client.login()
            auth_method = "credentials"
    except Exception as exc:
        if tokenstore:
            return {
                "status": "failed",
                "reason": f"Garmin cached-token login failed from {tokenstore}: {exc}",
                "auth_method": "tokenstore",
            }
        return {
            "status": "failed",
            "reason": f"Garmin credential login failed: {exc}",
            "auth_method": "credentials",
        }
    today = today_local(DEFAULT_TIMEZONE)
    wellness_written = []
    for offset in range(max(1, wellness_days)):
        day = (today - timedelta(days=offset)).isoformat()
        payloads = [
            _safe_call("get_stats", client.get_stats, day),
            _safe_call("get_user_summary", client.get_user_summary, day),
        ]
        if hasattr(client, "get_body_battery"):
            payloads.append(_safe_call("get_body_battery", client.get_body_battery, day, day))
        if hasattr(client, "get_body_battery_events"):
            payloads.append(_safe_call("get_body_battery_events", client.get_body_battery_events, day))
        for method_name in ("get_sleep_data", "get_hrv_data", "get_body_composition"):
            method = getattr(client, method_name, None)
            if method:
                payloads.append(_safe_call(method_name, method, day))
        _write_wellness_payload(root, day, payloads)
        wellness_written.append(day)

    training_status = None
    for method_name in ("get_training_status", "get_training_readiness"):
        method = getattr(client, method_name, None)
        if method:
            training_status = _safe_call(method_name, method, today.isoformat())
            write_json(
                snapshots_dir(root) / f"garmin_training_status_{today.isoformat()}.json",
                {"date": today.isoformat(), "source": "garminconnect", "payload": training_status},
            )
            break

    activities_written = 0
    if activity_limit > 0 and hasattr(client, "get_activities"):
        result = _safe_call("get_activities", client.get_activities, 0, activity_limit)
        if result.get("ok") and isinstance(result.get("data"), list):
            for activity in result["data"]:
                activity_id = activity.get("activityId") or activity.get("id") or activities_written
                write_json(activities_dir(root) / f"garmin_{activity_id}.json", activity)
                activities_written += 1

    return {
        "status": "ok",
        "auth_method": auth_method,
        "wellness_written": wellness_written,
        "training_status_written": training_status is not None,
        "activities_written": activities_written,
    }


def sync_connect(
    root: str | Path | None = None,
    wellness_days: int = 30,
    activity_limit: int = 200,
    rebuild_only: bool = False,
    cleanup_after: bool = False,
) -> dict:
    ensure_layout(root)
    live = {"status": "skipped", "reason": "rebuild_only"} if rebuild_only else _fetch_live(root, wellness_days, activity_limit)
    state = build_current_state(root)
    plan = build_today_plan(root, state=state)
    brief = build_daily_brief(root, state=state, plan=plan)
    review = review_block(root, state=state)
    inventory = build_data_inventory(root)
    quality = build_data_quality_report(root)
    battery_model = state.get("body_battery_model", {})
    baselines = state.get("historical_baselines", {})
    training_predictor = state.get("training_predictor", {})
    coach_packet = build_coach_packet(root, state=state, plan=plan)
    cleanup = cleanup_derived(root, apply=True) if cleanup_after else None
    status = {
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "live_sync": live,
        "state_file": str(snapshots_dir(root) / "current_state.json"),
        "brief_file": str(snapshots_dir(root) / "daily_brief.txt"),
        "coach_packet_file": str(snapshots_dir(root) / "coach_packet.txt"),
        "review_block_file": str(snapshots_dir(root) / "review_block.txt"),
        "readiness_level": state.get("readiness", {}).get("readiness_level"),
        "session_title": brief.get("session", {}).get("title"),
        "next_reentry_check": review.get("next_reentry_check"),
        "wellness_days_available": inventory.get("wellness_days_available"),
        "activity_count": inventory.get("activity_count"),
        "data_quality_flags": quality.get("flags", []),
        "body_battery_model_samples": battery_model.get("samples"),
        "body_battery_model_accuracy": battery_model.get("leave_one_out_accuracy"),
        "historical_activity_count": baselines.get("activity_count"),
        "training_predictor_samples": training_predictor.get("samples"),
        "training_predictor_validation": training_predictor.get("validation"),
        "coach_packet_confidence": coach_packet.get("today_call", {}).get("coach_confidence"),
        "coach_packet_stance": coach_packet.get("today_call", {}).get("stance"),
        "cleanup": cleanup,
    }
    write_json(snapshots_dir(root) / "sync_status.json", status)
    return status
