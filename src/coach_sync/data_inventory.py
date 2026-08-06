from __future__ import annotations

from datetime import date
from pathlib import Path

from .io import write_json
from .paths import snapshots_dir
from .surface_manifest import build_garmin_surface_manifest


def build_data_inventory(
    root: str | Path | None = None,
    for_date: str | date | None = None,
) -> dict:
    manifest = build_garmin_surface_manifest(root, for_date)
    wellness = (manifest.get("raw_sources") or {}).get("wellness") or {}
    activity = (manifest.get("raw_sources") or {}).get("activities") or {}
    training_status = (manifest.get("raw_sources") or {}).get("training_status") or {}
    training_readiness = (manifest.get("raw_sources") or {}).get("training_readiness") or {}
    device_capabilities = (manifest.get("raw_sources") or {}).get("device_capabilities") or {}
    metadata = (manifest.get("raw_sources") or {}).get("activity_metadata") or {}
    fields = activity.get("top_level_field_coverage") or {}
    power = ((activity.get("power_coverage") or {}).get("average_power") or {})
    heart_rate = ((activity.get("heart_rate_coverage") or {}).get("average_hr") or {})
    training_load = activity.get("training_load_coverage") or {}
    endpoint_states = {
        endpoint_id: endpoint.get("state")
        for endpoint_id, endpoint in (manifest.get("configured_endpoints") or {}).items()
    }
    inventory = {
        "generated_at": manifest.get("generated_at"),
        "basis_date": manifest.get("basis_date"),
        "surface_manifest": "snapshots/garmin_surface_manifest.json",
        "source_timestamps": manifest.get("source_timestamps", {}),
        "endpoint_states": endpoint_states,
        "endpoint_state_counts": manifest.get("endpoint_state_counts", {}),
        "wellness_days_available": wellness.get("usable_snapshot_count", 0),
        "wellness_latest_date": (wellness.get("date_span") or {}).get("latest"),
        "wellness_payloads": sorted(
            endpoint.removeprefix("wellness.")
            for endpoint, state in endpoint_states.items()
            if endpoint.startswith("wellness.") and state in {"success", "success_empty"}
        ),
        "wellness_data_eras": wellness.get("usable_eras", []),
        "wellness_data_gaps": wellness.get("gaps", []),
        "wellness_endpoint_coverage": wellness.get("endpoints", {}),
        "activity_count": activity.get("valid_activity_count", 0),
        "activity_date_span": activity.get("date_span"),
        "activity_categories": activity.get("categories", {}),
        "activity_top_level_field_count": activity.get("top_level_field_count", 0),
        "activity_field_coverage": fields,
        "activity_heart_rate_coverage": activity.get("heart_rate_coverage", {}),
        "activity_training_load_coverage": training_load,
        "activity_power_coverage": activity.get("power_coverage", {}),
        "metadata_coverage": metadata,
        "training_status_coverage": training_status,
        "training_readiness_coverage": training_readiness,
        "device_capabilities": device_capabilities,
        "training_data_available": {
            "activity_training_load": (training_load.get("non_null_count") or 0) > 0,
            "heart_rate": (heart_rate.get("non_null_count") or 0) > 0,
            "heart_rate_sessions": heart_rate.get("non_null_count", 0),
            "power_sessions": power.get("non_null_count", 0),
        },
        "normalized_surfaces": manifest.get("normalized_surfaces", {}),
        "units_and_provenance": manifest.get("units_and_provenance", {}),
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
