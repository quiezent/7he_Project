from coach_sync.io import write_json
from coach_sync.training_architecture import build_training_architecture


def _activity(activity_id, day, type_key, load, p20=None):
    payload = {
        "activityId": activity_id,
        "activityName": "Training",
        "activityType": {"typeKey": type_key},
        "startTimeLocal": f"{day} 08:00:00",
        "duration": 3600,
        "activityTrainingLoad": load,
        "averageHR": 130,
        "maxHR": 150,
        "aerobicTrainingEffect": 3.0,
        "anaerobicTrainingEffect": 0.0,
        "hrTimeInZone_1": 600,
        "hrTimeInZone_2": 1200,
        "hrTimeInZone_3": 900,
    }
    if p20 is not None:
        payload["maxAvgPower_1200"] = p20
        payload["maxAvgPower_600"] = p20 + 10
        payload["avgPower"] = p20 - 30
        payload["normPower"] = p20 - 20
    return payload


def _context():
    return {
        "athlete": {
            "name": "Clayton",
            "equipment": {
                "trainer": {"model": "Elite Suito"},
                "power_meter_policy": {"enduro_bike": "Ride by feel."},
            },
            "rider_category": {"current": "experienced", "target": "expert"},
        },
        "goal_progression": {"current_phase": "base_rebuild"},
        "training_rules": {
            "weekly_rest_days": [
                {"weekday": 6, "label": "Sabbath", "status": "hard_rest"}
            ]
        },
    }


def test_training_architecture_builds_config_and_snapshot(tmp_path):
    write_json(tmp_path / "config" / "athlete_context.json", _context())
    write_json(
        tmp_path / "activities" / "indoor.json",
        _activity(1, "2026-05-05", "indoor_cycling", 70, p20=150),
    )
    write_json(
        tmp_path / "activities" / "mtb.json",
        _activity(2, "2026-05-06", "mountain_biking", 120, p20=145),
    )
    write_json(tmp_path / "snapshots" / "wellness_daily.json", [])

    artifact = build_training_architecture(tmp_path, "2026-05-26")

    assert artifact["architecture_type"] == "clayton_specific_enduro_training_architecture"
    assert artifact["schema_version"] == 3
    assert artifact["integrated_coaching_model"]["purpose"].startswith("Combine directive")
    assert artifact["session_contract"]["required_fields"][0] == "purpose"
    assert artifact["athlete_model"]["highest_return_sequence"][0] == "bike-specific continuity"
    assert artifact["equipment_model"]["trainer"]["model"] == "Elite Suito"
    assert artifact["macrocycle"][0]["phase"] == "base_rebuild"
    assert "density_governor" in artifact["weekly_architecture"]
    assert "bukit_dinding_dh_setup" in artifact["session_library"]
    assert (tmp_path / "config" / "coaching_architecture.json").exists()
    assert (tmp_path / "snapshots" / "training_architecture.json").exists()
