from __future__ import annotations

from datetime import date
from pathlib import Path

from .activity_profile import build_activity_profile
from .body_battery_model import build_body_battery_model
from .context import load_context
from .device_audit import build_device_audit
from .evidence import load_activities, load_latest_training_status, load_latest_wellness, summarize_recent_training
from .gear_audit import build_gear_audit
from .historical_baselines import build_historical_baselines
from .io import write_json
from .load_model import build_modality_load_rollups
from .paths import snapshots_dir
from .readiness import build_readiness
from .self_evaluation import build_self_evaluation_report
from .training_status import build_training_status_current
from .training_predictor import build_training_predictor
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local
from .wellness import build_wellness_trends
from .wellness_verification import build_wellness_verification


def determine_phase(context: dict, target_date: date) -> dict:
    configured = context.get("goal_progression", {}).get("current_phase", "base_rebuild")
    return {
        "name": configured,
        "reason": "Using configured goal progression phase.",
    }


def build_training_load_snapshot(
    root: str | Path | None,
    activities: list[dict],
    target_date: date,
    context: dict,
) -> dict:
    training = summarize_recent_training(activities, target_date)
    threshold = context.get("training_rules", {}).get("acute_chronic_load_spike_ratio", 1.5)
    spike = training.get("acute_load_spike_ratio")
    training["flags"] = []
    if spike is not None and spike > threshold:
        training["flags"].append(
            {
                "type": "load_spike",
                "message": f"Acute load is {spike}x prior 7 days.",
            }
        )
    write_json(snapshots_dir(root) / "training_load.json", training)
    return training


def build_current_state(root: str | Path | None = None, for_date: str | date | None = None) -> dict:
    context = load_context(root)
    tz = context.get("athlete", {}).get("timezone", DEFAULT_TIMEZONE)
    target_date = parse_date(for_date) or today_local(tz)
    readiness = build_readiness(root, target_date)
    activities = load_activities(root)
    phase = determine_phase(context, target_date)
    wellness_date, wellness = load_latest_wellness(root, target_date)
    training_status_date, training_status = load_latest_training_status(root, target_date)
    training_load = build_training_load_snapshot(root, activities, target_date, context)
    wellness_trends = build_wellness_trends(root, target_date)
    wellness_verification = build_wellness_verification(root, target_date)
    activity_profile = build_activity_profile(root, target_date)
    training_status_current = build_training_status_current(root, target_date.isoformat())
    modality_load_rollups = build_modality_load_rollups(root, target_date)
    body_battery_model = build_body_battery_model(root, target_date)
    historical_baselines = build_historical_baselines(root, target_date)
    training_predictor = build_training_predictor(root, target_date)
    gear_audit = build_gear_audit(root, target_date)
    device_audit = build_device_audit(root, target_date)
    self_evaluation = build_self_evaluation_report(root, target_date)
    athlete = dict(context.get("athlete", {}))
    latest_body_composition = wellness_trends.get("latest_body_composition") or {}
    if athlete.get("body_weight_kg") is None and latest_body_composition.get("body_weight_kg") is not None:
        athlete["body_weight_kg"] = latest_body_composition.get("body_weight_kg")
        athlete["body_weight_source"] = {
            "source": "garmin_body_composition",
            "date": latest_body_composition.get("date"),
            "age_days": latest_body_composition.get("age_days"),
        }

    if wellness_date is None:
        freshness = {"status": "missing", "message": "No Garmin wellness data has been synced."}
    else:
        age = (target_date - wellness_date).days
        freshness = {
            "status": "current" if age <= 0 else "stale",
            "age_days": age,
            "latest_wellness_date": wellness_date.isoformat(),
            "message": "Garmin wellness data is current."
            if age <= 0
            else f"Garmin wellness data is {age} day(s) behind the wall-clock date.",
        }
    latest_activity = training_load.get("latest_activity")
    hard_limiters = []
    if wellness_date is None or freshness.get("status") != "current":
        hard_limiters.append(freshness.get("message"))
    if latest_activity is None:
        activity_freshness = {
            "status": "missing",
            "message": "No Garmin activity data is available to verify recent load.",
        }
        hard_limiters.append(activity_freshness["message"])
    else:
        latest_activity_date = parse_date(latest_activity.get("date"))
        activity_age = (target_date - latest_activity_date).days if latest_activity_date else None
        if activity_age is None:
            activity_freshness = {
                "status": "unknown",
                "message": "Latest activity has no usable date.",
            }
            hard_limiters.append(activity_freshness["message"])
        elif activity_age < 0:
            activity_freshness = {
                "status": "future",
                "age_days": activity_age,
                "message": "Latest activity is dated after the target date and cannot support this plan.",
            }
            hard_limiters.append(activity_freshness["message"])
        elif activity_age > 7:
            activity_freshness = {
                "status": "stale",
                "age_days": activity_age,
                "latest_activity_date": latest_activity_date.isoformat(),
                "message": f"Latest activity is {activity_age} day(s) old.",
            }
            hard_limiters.append(activity_freshness["message"])
        else:
            activity_freshness = {
                "status": "current",
                "age_days": activity_age,
                "latest_activity_date": latest_activity_date.isoformat(),
                "message": "Recent activity data is available.",
            }
    freshness["activity_data"] = activity_freshness
    freshness["hard_session_confidence"] = "normal" if not hard_limiters else "limited"
    freshness["hard_session_limiters"] = [item for item in hard_limiters if item]

    state = {
        "date": target_date.isoformat(),
        "generated_at": iso_now(tz),
        "athlete": athlete,
        "goal": context.get("athlete", {}).get("goal"),
        "phase": phase,
        "readiness": readiness,
        "data_freshness": freshness,
        "training_load": training_load,
        "wellness_trends": wellness_trends,
        "wellness_verification": wellness_verification,
        "body_composition": latest_body_composition or None,
        "activity_profile": activity_profile,
        "training_status_current": training_status_current,
        "modality_load_rollups": modality_load_rollups,
        "body_battery_model": {
            "samples": body_battery_model.get("samples"),
            "leave_one_out_accuracy": body_battery_model.get("leave_one_out_accuracy"),
            "latest_prediction": body_battery_model.get("latest_prediction"),
            "caveats": body_battery_model.get("caveats"),
        },
        "historical_baselines": {
            "activity_count": historical_baselines.get("activity_count"),
            "date_span": historical_baselines.get("date_span"),
            "category_counts": historical_baselines.get("category_counts"),
            "peak_windows": historical_baselines.get("peak_windows"),
            "pre_injury_mtb_baseline": historical_baselines.get("pre_injury_mtb_baseline"),
        },
        "training_predictor": {
            "samples": training_predictor.get("samples"),
            "validation": training_predictor.get("validation"),
            "feature_usage": training_predictor.get("feature_usage"),
            "today_prediction": training_predictor.get("today_prediction"),
            "caveats": training_predictor.get("caveats"),
        },
        "gear_audit": gear_audit,
        "device_audit": device_audit,
        "self_evaluation": self_evaluation,
        "latest_activity": training_load.get("latest_activity"),
        "evidence_sources": {
            "wellness_date": wellness_date.isoformat() if wellness_date else None,
            "training_status_date": (
                training_status_date.isoformat() if training_status_date else None
            ),
            "wellness_available": wellness is not None,
            "training_status_available": training_status is not None,
            "activity_count": len(activities),
        },
    }
    write_json(snapshots_dir(root) / "current_state.json", state)
    return state
