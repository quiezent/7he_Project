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
            "venue_profiles": {
                "bukit_kiara": {
                    "preferred_environment_report": {
                        "name": "Local Bukit Kiara MTB environment evidence",
                        "location": "Taman Tun Dr. Ismail / Bukit Kiara",
                        "endpoint": "http://192.168.80.147:8765/api/v1/mtb/environment-evidence",
                        "schema_contract": {
                            "schema_major": 1,
                            "kind": "mtb_environment_evidence",
                            "boundary_role": "environmental_evidence",
                            "training_prescription_included": False,
                            "historical_series_included": False,
                        },
                        "freshness_contract": {
                            "stale_after_seconds": 420,
                            "expired_after_seconds": 900,
                        },
                        "sports_exercise_bands_ug_m3": {
                            "normal_below": 25,
                            "moderate_from": 25,
                            "poor_from": 51,
                            "hazardous_above": 150,
                        },
                        "automatic_gate_venue_keys": ["bukit_kiara"],
                        "automatic_gate_venue_aliases": ["Bukit Kiara", "Kiara", "TTDI"],
                        "artifacts": {
                            "current": "snapshots/environment_evidence.json",
                            "dated_pattern": "snapshots/environment_evidence_YYYY-MM-DD.json",
                        },
                        "fields_of_interest": ["weather", "PM2.5", "arrival range"],
                        "decision_use": "Venue-scoped hold or downshift only.",
                        "access_rule": "Use the single v1 endpoint and retain bounded normalized artifacts.",
                        "guardrail": "Cannot promote readiness or increase intensity.",
                    }
                }
            },
            "event_focus": {
                "upcoming_events": [
                    {
                        "event_key": "pdr26",
                        "name": "PDR26",
                        "date": "2026-09-20",
                        "day_of_week": "Sunday",
                        "discipline": "downhill_mtb",
                        "venue_key": "denai_peladang",
                        "equipment_key": "specialized_enduro",
                        "status": "confirmed_with_athlete_authorized_sabbath_shift",
                        "event_schedule": {
                            "official_practice_date": "2026-09-19",
                            "race_date": "2026-09-20",
                        },
                        "sabbath_accommodation": {
                            "exception_date": "2026-09-20",
                            "exception_scope": "PDR26 race participation only",
                            "replacement_sabbath_date": "2026-09-21",
                        },
                        "recce": {
                            "timing": "early September 2026",
                            "date": None,
                            "equipment_key": "specialized_enduro",
                        },
                        "course_familiarity": {
                            "venue": "familiar",
                            "2026_dh_line": "new and not yet ridden",
                        },
                    }
                ]
            },
            "equipment": {
                "trainer": {"model": "Elite Suito"},
                "power_meter_policy": {"enduro_bike": "Ride by feel."},
                "bikes": {
                    "specialized_stumpjumper": {
                        "default_role": "Primary outdoor fitness and volume MTB training bike.",
                        "preferred_training_use": "Routine MTB engine and durability work.",
                        "mileage_policy": "Training mileage is expected.",
                    },
                    "specialized_enduro": {
                        "default_role": "Protected race and race-specific skill-transfer bike.",
                        "training_use_policy": {
                            "maximum_normal_training_exposures_per_week": 1,
                            "normal_training_scope_excludes_declared_event_practice_and_race": True,
                            "recce_consumes_weekly_enduro_exposure": True,
                            "scope": ["race-specific skill transfer", "course recce"],
                            "density_rule": "Replace a protected MTB slot rather than stacking.",
                        },
                    },
                },
            },
            "rider_category": {"current": "experienced", "target": "expert"},
        },
        "goal_progression": {"current_phase": "base_rebuild"},
        "training_rules": {
            "bike_specific_continuity": {
                "primary_outdoor_fitness_equipment_key": "specialized_stumpjumper",
                "enduro_training_max_exposures_per_week": 1,
                "enduro_recce_consumes_training_cap": True,
            },
            "cycling_vo2_rebuild": {
                "current_anchor": {"garmin_cycling_vo2max_precise": 47.6},
                "pre_injury_reference": {"garmin_cycling_vo2max_precise": 51.1},
                "recurring_build_rule": "Use five bike days with structured development.",
            },
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
    adaptive = artifact["adaptive_programming"]
    assert adaptive["boundary"]["garmin_mcp"].startswith("Direct live perception")
    assert adaptive["progression_ladders"]["tempo_torque"][:3] == [
        "3x8_min",
        "3x10_min",
        "3x12_min",
    ]
    assert adaptive["progression_ladders"]["endurance_duration"] == [
        "60_min_120_130_w",
        "75_min_near_125_w",
        "90_min_near_125_w",
    ]
    assert next(
        block for block in adaptive["block_calendar"] if block["start"] == "2026-09-20"
    )["mode"] == "event_race"
    assert adaptive["promotion_logic"]["unsafe_override"].startswith(
        "A triggered_but_continued outcome never promotes"
    )
    assert artifact["integrated_coaching_model"]["purpose"].startswith("Combine directive")
    environment = artifact["integrated_coaching_model"]["same_day_environment_context"]
    assert environment["location"] == "Taman Tun Dr. Ismail / Bukit Kiara"
    assert environment["endpoint"].endswith("/api/v1/mtb/environment-evidence")
    assert "endpoints" not in environment
    assert environment["schema_contract"]["kind"] == "mtb_environment_evidence"
    assert environment["freshness_contract"] == {
        "stale_after_seconds": 420,
        "expired_after_seconds": 900,
    }
    assert environment["sports_exercise_bands_ug_m3"] == {
        "normal_below": 25,
        "moderate_from": 25,
        "poor_from": 51,
        "hazardous_above": 150,
    }
    assert environment["venue_scope"]["keys"] == ["bukit_kiara"]
    assert environment["artifacts"]["current"] == "snapshots/environment_evidence.json"
    assert environment["decision_role"] == "Venue-scoped hold or downshift only."
    assert "automatically increase" in environment["planner_boundary"]["forbidden"]
    assert "Raw endpoint response" in environment["persistence_boundary"]["excluded"]
    assert "outdoor_air_quality_model" not in artifact["integrated_coaching_model"]
    assert "outdoor_air_quality" not in artifact["artifact_contract"]
    assert "environment_evidence" in artifact["artifact_contract"]
    assert artifact["session_contract"]["required_fields"][0] == "purpose"
    assert "stop_rule_outcome" in artifact["session_contract"]["post_session_review"]
    assert artifact["athlete_model"]["highest_return_sequence"][0] == "bike-specific continuity"
    assert artifact["equipment_model"]["trainer"]["model"] == "Elite Suito"
    event = artifact["event_model"]["upcoming_events"][0]
    assert event["event_key"] == "pdr26"
    assert event["date"] == "2026-09-20"
    assert event["day_of_week"] == "Sunday"
    assert event["venue_key"] == "denai_peladang"
    assert event["equipment_key"] == "specialized_enduro"
    assert event["status"] == "confirmed_with_athlete_authorized_sabbath_shift"
    assert event["recce"]["timing"] == "early September 2026"
    assert event["course_familiarity"]["2026_dh_line"] == "new and not yet ridden"
    assert event["sabbath_accommodation"]["replacement_sabbath_date"] == "2026-09-21"
    allocation = artifact["bike_allocation_policy"]
    assert allocation["primary_fitness_and_volume_bike"]["equipment_key"] == (
        "specialized_stumpjumper"
    )
    assert "fitness and volume" in allocation["primary_fitness_and_volume_bike"][
        "role"
    ]
    enduro = allocation["enduro_race_specific_bike"]
    assert enduro["normal_training"]["maximum_exposures_per_week"] == 1
    assert enduro["normal_training"]["recce_consumes_weekly_enduro_exposure"] is True
    assert enduro["normal_training"]["recce_replaces_slot_instead_of_stacking"] is True
    assert enduro["declared_event_exposures"][
        "practice_and_race_are_separate_from_normal_training_cap"
    ] is True
    assert enduro["declared_event_exposures"]["classification"] == (
        "separately_explicit_event_exposure"
    )
    assert artifact["cycling_vo2_rebuild"]["current_anchor"][
        "garmin_cycling_vo2max_precise"
    ] == 47.6
    assert artifact["cycling_vo2_rebuild"]["pre_injury_reference"][
        "garmin_cycling_vo2max_precise"
    ] == 51.1
    assert artifact["macrocycle"][0]["phase"] == "base_rebuild"
    assert artifact["macrocycle"][0]["minimums"][
        "preferred_bike_load_per_week"
    ] == [350, 500]
    assert "density_governor" in artifact["weekly_architecture"]
    assert any(
        "below five unique bike days is underdosed" in rule
        for rule in artifact["weekly_architecture"]["density_governor"]
    )
    assert "standard 60-minute low-aerobic bike dose" in artifact[
        "weekly_architecture"
    ]["default_week"]["monday"]
    steady_endurance = artifact["session_library"]["indoor_steady_endurance"]
    assert "60 minutes" in steady_endurance["routine_low_cost_dose"]
    assert steady_endurance["routine_low_cost_contract"] == {
        "total_duration_min": 60,
        "main_power_w_range": [120, 130],
        "global_rpe_range": [2, 3],
        "density_cost": "low",
    }
    assert "75 minutes toward 90 minutes" in steady_endurance[
        "duration_development_dose"
    ]
    assert "diffuse bilateral" in steady_endurance["symptom_interpretation"]
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
    action_prior = artifact["predictive_training_loop"][
        "matched_route_repeat_load_prior"
    ]
    assert action_prior["identity_schema_v1"]["schema_version"] == 1
    assert "never infer a named route" in action_prior["activation"]
    assert any("Fewer than two" in rule for rule in action_prior["sample_rules"])
    assert "schedule_translation" in artifact["weekly_architecture"]
    assert "bukit_dinding_dh_setup" in artifact["session_library"]
    assert (tmp_path / "config" / "coaching_architecture.json").exists()
    assert (tmp_path / "snapshots" / "training_architecture.json").exists()
