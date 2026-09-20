from __future__ import annotations

from pathlib import Path

from .activity_profile import build_activity_profile
from .io import write_json
from .paths import snapshots_dir
from .wellness import build_wellness_trends


def build_data_inventory(root: str | Path | None = None) -> dict:
    wellness = build_wellness_trends(root)
    activity = build_activity_profile(root)
    inventory = {
        "wellness_days_available": wellness.get("days_available", 0),
        "wellness_latest_date": (wellness.get("latest") or {}).get("date"),
        "wellness_payloads": (wellness.get("latest") or {}).get("available_payloads", []),
        "activity_count": activity.get("activity_count", 0),
        "activity_date_span": activity.get("date_span"),
        "activity_categories": activity.get("categories", {}),
        "training_data_available": {
            "activity_training_load": activity.get("last_28_days", {}).get("training_load", 0) > 0,
            "heart_rate": activity.get("activity_count", 0) > 0,
            "power_sessions": activity.get("power_sessions", 0),
        },
        "recommended_stack_inputs": [
            "sleep score, sleep duration, stages, and sleep stress",
            "overnight HRV and HRV baseline/status",
            "resting HR trend",
            "Body Battery wake/current/charge/drain",
            "daily stress, respiration, SpO2, steps, calories, and intensity minutes",
            "activity training load, HR zones, aerobic/anaerobic effects, and cycling power",
            "subjective trail-skill, fueling, heat, and next-morning response",
        ],
    }
    write_json(snapshots_dir(root) / "garmin_data_inventory.json", inventory)
    return inventory
