from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

from .briefing import build_daily_brief
from .cleanup import cleanup_derived
from .coach_packet import build_coach_packet
from .data_quality import build_data_quality_report
from .device_audit import build_device_audit, summarize_activity_devices
from .data_inventory import build_data_inventory
from .evidence import summarize_activity, wellness_snapshot_is_usable
from .gear_audit import BIKE_GEAR_CATEGORIES, build_gear_audit, summarize_gear_items
from .io import read_json, write_json
from .paths import activities_dir, ensure_layout, snapshots_dir
from .planning import build_today_plan, load_weekly_session
from .predictive_training import build_predictive_training
from .reports import review_block
from .self_evaluation import build_self_evaluation_report, summarize_activity_self_evaluation
from .state import build_current_state
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local
from .weekly_planning import build_weekly_plan


def _safe_call(label: str, func: Callable[..., Any], *args: Any) -> dict:
    try:
        return {"label": label, "ok": True, "data": func(*args)}
    except Exception as exc:  # Live Garmin API methods differ across versions.
        return {"label": label, "ok": False, "error": str(exc)}


def _write_wellness_payload(root: str | Path | None, day: str, payloads: list[dict]) -> dict:
    combined = {
        "date": day,
        "source": "garminconnect",
        "payloads": payloads,
    }
    write_json(snapshots_dir(root) / f"garmin_wellness_{day}.json", combined)
    return combined


def _write_merged_activity_index(
    root: str | Path | None,
    filename: str,
    source: str,
    rows: list[dict],
    **metadata: Any,
) -> dict:
    path = snapshots_dir(root) / filename
    existing = read_json(path, {})
    merged: dict[str, dict] = {}
    if isinstance(existing, dict):
        for row in existing.get("activities") or []:
            if not isinstance(row, dict):
                continue
            activity_id = str(row.get("activity_id") or row.get("id") or "")
            if activity_id:
                merged[activity_id] = row
    for row in rows:
        activity_id = str(row.get("activity_id") or row.get("id") or "")
        if activity_id:
            merged[activity_id] = row
    artifact = {
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "source": source,
        "last_sync_rows": len(rows),
        "retained_rows": max(0, len(merged) - len(rows)),
        **metadata,
        "activities": sorted(
            merged.values(),
            key=lambda item: (item.get("date") or "", str(item.get("activity_id") or "")),
            reverse=True,
        ),
    }
    write_json(path, artifact)
    return artifact


def _write_activity_gear_index(
    root: str | Path | None,
    client: Any,
    activities: list[dict],
) -> dict | None:
    if not hasattr(client, "get_activity_gear"):
        return None
    rows = []
    for activity in activities:
        summary = summarize_activity(activity)
        if summary.get("category") not in BIKE_GEAR_CATEGORIES:
            continue
        activity_id = summary.get("id")
        if not activity_id:
            continue
        result = _safe_call("get_activity_gear", client.get_activity_gear, activity_id)
        gear_payload = result.get("data") if result.get("ok") else []
        rows.append(
            {
                "activity_id": activity_id,
                "date": summary.get("date"),
                "name": summary.get("name"),
                "type": summary.get("type"),
                "category": summary.get("category"),
                "gear_fetch_ok": result.get("ok"),
                "gear_fetch_error": result.get("error"),
                "gear": summarize_gear_items(gear_payload),
            }
        )
    return _write_merged_activity_index(
        root,
        "activity_gear_index.json",
        "garminconnect.get_activity_gear",
        rows,
    )


def _write_activity_device_index(
    root: str | Path | None,
    client: Any,
    activities: list[dict],
) -> dict | None:
    if not hasattr(client, "get_activity"):
        return None
    rows = []
    for activity in activities:
        summary = summarize_activity(activity)
        if summary.get("category") not in BIKE_GEAR_CATEGORIES:
            continue
        activity_id = summary.get("id")
        if not activity_id:
            continue
        result = _safe_call("get_activity", client.get_activity, activity_id)
        device_payload = result.get("data") if result.get("ok") and isinstance(result.get("data"), dict) else {}
        device_summary = summarize_activity_devices(device_payload)
        rows.append(
            {
                "activity_id": activity_id,
                "date": summary.get("date"),
                "name": summary.get("name"),
                "type": summary.get("type"),
                "category": summary.get("category"),
                "device_fetch_ok": result.get("ok"),
                "device_fetch_error": result.get("error"),
                **device_summary,
            }
        )
    return _write_merged_activity_index(
        root,
        "activity_device_index.json",
        "garminconnect.get_activity.metadataDTO",
        rows,
    )


def _write_activity_self_evaluation_index(
    root: str | Path | None,
    client: Any,
    activities: list[dict],
    max_detail_fetches: int = 20,
) -> dict | None:
    if not hasattr(client, "get_activity"):
        return None
    rows = []
    for activity in activities[:max(1, max_detail_fetches)]:
        summary = summarize_activity(activity)
        activity_id = summary.get("id")
        if not activity_id:
            continue
        result = _safe_call("get_activity", client.get_activity, activity_id)
        detail_payload = result.get("data") if result.get("ok") and isinstance(result.get("data"), dict) else {}
        rows.append(
            {
                "activity_id": activity_id,
                "date": summary.get("date"),
                "name": summary.get("name"),
                "type": summary.get("type"),
                "category": summary.get("category"),
                "detail_fetch_ok": result.get("ok"),
                "detail_fetch_error": result.get("error"),
                **summarize_activity_self_evaluation(detail_payload),
            }
        )
    return _write_merged_activity_index(
        root,
        "activity_self_evaluation_index.json",
        "garminconnect.get_activity.summaryDTO.directWorkoutFeel/directWorkoutRpe",
        rows,
        max_detail_fetches=max_detail_fetches,
    )


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
    wellness_unusable = []
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
        snapshot = _write_wellness_payload(root, day, payloads)
        wellness_written.append(day)
        if not wellness_snapshot_is_usable(snapshot):
            wellness_unusable.append(day)

    training_status = None
    training_status_attempts = []
    for method_name in ("get_training_status", "get_training_readiness"):
        method = getattr(client, method_name, None)
        if method:
            result = _safe_call(method_name, method, today.isoformat())
            training_status_attempts.append(result)
            if training_status is None:
                training_status = result
            if result.get("ok") and isinstance(result.get("data"), dict) and result.get("data"):
                training_status = result
                break
    if training_status is not None:
        write_json(
            snapshots_dir(root) / f"garmin_training_status_{today.isoformat()}.json",
            {"date": today.isoformat(), "source": "garminconnect", "payload": training_status},
        )
    training_status_usable = bool(
        training_status
        and training_status.get("ok")
        and isinstance(training_status.get("data"), dict)
        and training_status.get("data")
    )

    activities_written = 0
    activity_gear_index = None
    activity_device_index = None
    activity_self_evaluation_index = None
    activity_fetch_failure = None
    if activity_limit > 0 and hasattr(client, "get_activities"):
        result = _safe_call("get_activities", client.get_activities, 0, activity_limit)
        if result.get("ok") and isinstance(result.get("data"), list):
            for activity in result["data"]:
                activity_id = activity.get("activityId") or activity.get("id") or activities_written
                write_json(activities_dir(root) / f"garmin_{activity_id}.json", activity)
                activities_written += 1
            activity_gear_index = _write_activity_gear_index(root, client, result["data"])
            activity_device_index = _write_activity_device_index(root, client, result["data"])
            activity_self_evaluation_index = _write_activity_self_evaluation_index(root, client, result["data"])
        else:
            activity_fetch_failure = result.get("error") or "Garmin activities response was not a list."

    failures = []
    if wellness_unusable:
        failures.append({"source": "wellness", "dates": wellness_unusable})
    if training_status is not None and not training_status_usable:
        failures.append(
            {
                "source": "training_status",
                "attempts": training_status_attempts,
            }
        )
    if activity_fetch_failure:
        failures.append({"source": "activities", "message": activity_fetch_failure})

    return {
        "status": "partial" if failures else "ok",
        "auth_method": auth_method,
        "wellness_written": wellness_written,
        "wellness_unusable_dates": wellness_unusable,
        "training_status_written": training_status_usable,
        "activities_written": activities_written,
        "activity_gear_checked": len((activity_gear_index or {}).get("activities") or []),
        "activity_device_checked": len((activity_device_index or {}).get("activities") or []),
        "activity_self_evaluation_checked": len((activity_self_evaluation_index or {}).get("activities") or []),
        "failures": failures,
    }


def sync_connect(
    root: str | Path | None = None,
    wellness_days: int = 30,
    activity_limit: int = 200,
    rebuild_only: bool = False,
    cleanup_after: bool = False,
    decision_only: bool = False,
) -> dict:
    ensure_layout(root)
    live = {"status": "skipped", "reason": "rebuild_only"} if rebuild_only else _fetch_live(root, wellness_days, activity_limit)
    state = build_current_state(root, refresh_models=not decision_only)
    state_date = parse_date(state.get("date"))
    weekly_session = load_weekly_session(root, state_date) if state_date else None
    weekly_plan = (
        build_weekly_plan(root, state=state)
        if state_date and (state_date.weekday() == 0 or weekly_session is None)
        else None
    )
    weekly_plan_available = weekly_plan is not None or weekly_session is not None
    plan = build_today_plan(root, state=state)
    predictive = build_predictive_training(root, state=state, plan=plan)
    if decision_only:
        coach_packet = build_coach_packet(root, state=state, plan=plan)
        status = {
            "generated_at": iso_now(DEFAULT_TIMEZONE),
            "mode": "decision_only",
            "live_sync": live,
            "state_file": str(snapshots_dir(root) / "current_state.json"),
            "weekly_plan_file": str(snapshots_dir(root) / "weekly_plan.txt") if weekly_plan_available else None,
            "coach_packet_file": str(snapshots_dir(root) / "coach_packet.txt"),
            "readiness_level": state.get("readiness", {}).get("readiness_level"),
            "session_title": plan.get("session", {}).get("title"),
            "predictive_training_file": str(snapshots_dir(root) / "predictive_training.json"),
            "predictive_model_confidence": (
                predictive.get("today_prescription", {}).get("model_confidence", {}).get("status")
            ),
            "predictive_latest_review": (
                predictive.get("latest_review", {}).get("comparison", {}).get("interpretation")
            ),
            "coach_packet_confidence": coach_packet.get("today_call", {}).get("coach_confidence"),
            "coach_packet_stance": coach_packet.get("today_call", {}).get("stance"),
            "skipped_full_rebuild_artifacts": [
                "daily_brief",
                "review_block",
                "data_inventory",
                "data_quality",
            ],
        }
        write_json(snapshots_dir(root) / "sync_status.json", status)
        return status
    brief = build_daily_brief(root, state=state, plan=plan)
    review = review_block(root, state=state)
    inventory = build_data_inventory(root)
    quality = build_data_quality_report(root)
    gear_audit = build_gear_audit(root)
    device_audit = build_device_audit(root)
    self_evaluation = build_self_evaluation_report(root)
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
        "weekly_plan_file": str(snapshots_dir(root) / "weekly_plan.txt") if weekly_plan_available else None,
        "coach_packet_file": str(snapshots_dir(root) / "coach_packet.txt"),
        "review_block_file": str(snapshots_dir(root) / "review_block.txt"),
        "readiness_level": state.get("readiness", {}).get("readiness_level"),
        "session_title": brief.get("session", {}).get("title"),
        "predictive_training_file": str(snapshots_dir(root) / "predictive_training.json"),
        "predictive_model_confidence": (
            predictive.get("today_prescription", {}).get("model_confidence", {}).get("status")
        ),
        "predictive_latest_review": (
            predictive.get("latest_review", {}).get("comparison", {}).get("interpretation")
        ),
        "next_review_date": review.get("next_review_date"),
        "wellness_days_available": inventory.get("wellness_days_available"),
        "activity_count": inventory.get("activity_count"),
        "data_quality_flags": quality.get("flags", []),
        "gear_audit_flags": gear_audit.get("flags", []),
        "device_audit_flags": device_audit.get("flags", []),
        "self_evaluation_checked": self_evaluation.get("checked_activities"),
        "self_evaluation_logged": self_evaluation.get("evaluated_activities"),
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
