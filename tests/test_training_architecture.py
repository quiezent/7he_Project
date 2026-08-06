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
    assert "stop_rule_outcome" in artifact["session_contract"]["post_session_review"]
    assert artifact["athlete_model"]["highest_return_sequence"][0] == "bike-specific continuity"
    assert artifact["equipment_model"]["trainer"]["model"] == "Elite Suito"
    assert artifact["macrocycle"][0]["phase"] == "base_rebuild"
    assert "density_governor" in artifact["weekly_architecture"]
    assert "weekly_plan" in artifact["artifact_contract"]
    assert "rest_recharge_window" in artifact["artifact_contract"]
    assert "wearable_coverage" in artifact["artifact_contract"]
    sleep_governor = artifact["integrated_coaching_model"]["sleep_work_timing_governor"]
    assert sleep_governor["rest_recharge_window"]["artifact"] == (
        "snapshots/rest_recharge_window.json"
    )
    assert any(
        "Body Battery alone" in rule
        for rule in sleep_governor["rest_recharge_window"]["hard_guards"]
    )
    wear_state = sleep_governor["wear_state_provenance"]
    assert wear_state["artifact"] == "snapshots/wearable_coverage.json"
    assert wear_state["decision_role"] == (
        "coverage_interpretation_only_never_readiness_clearance"
    )
    assert any("Never impute" in rule for rule in wear_state["hard_guards"])
    assert any("predictive models" in rule for rule in wear_state["hard_guards"])
    assert "unexplained_internal_unavailability_with_recurring_context" in wear_state[
        "classifications"
    ]
    assert "insufficient_series_coverage" in wear_state["classifications"]
    assert any("every material run independently" in rule for rule in wear_state["hard_guards"])
    cns_field_gate = artifact["integrated_coaching_model"]["cns_readiness_model"][
        "trail_specific_field_gate"
    ]
    assert "second diagnostic gate" in cns_field_gate["purpose"]
    assert "compensatory focus" in cns_field_gate["sequence"][-1]
    assert "not attentional engagement" in cns_field_gate["chill_rule"]
    assert "does not prove normal CNS reserve" in cns_field_gate["interpretation_rule"]
    assert "daily_work_sleep_timing" in artifact["logging_contract"]
    assert "schedule_translation" in artifact["weekly_architecture"]
    assert "bukit_dinding_dh_setup" in artifact["session_library"]
    assert (tmp_path / "config" / "coaching_architecture.json").exists()
    assert (tmp_path / "snapshots" / "training_architecture.json").exists()
