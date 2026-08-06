from datetime import date, datetime, timedelta

from coach_sync.context import load_context
from coach_sync.io import read_json, write_json
from coach_sync.planning import SESSION_CONTRACT_FIELDS
from coach_sync.predictive_training import (
    _action_alignment,
    _session_expectation,
    _simulate_activity_day,
    _training_load_range,
    build_predictive_prescription,
    build_predictive_review,
    build_predictive_training,
)


def _write_wellness(root, day: date, good: bool) -> None:
    stress_start = int(
        datetime.fromisoformat(f"{day.isoformat()}T00:00:00+08:00").timestamp()
        * 1000
    )
    write_json(
        root / "snapshots" / f"garmin_wellness_{day.isoformat()}.json",
        {
            "date": day.isoformat(),
            "payloads": [
                {
                    "label": "get_stats",
                    "ok": True,
                    "data": {
                        "calendarDate": day.isoformat(),
                        "bodyBatteryAtWakeTime": 82 if good else 50,
                        "bodyBatteryMostRecentValue": 72 if good else 24,
                        "bodyBatteryDrainedValue": 22 if good else 72,
                        "averageStressLevel": 18 if good else 48,
                        "restingHeartRate": 44 if good else 54,
                        "moderateIntensityMinutes": 20 if good else 90,
                        "vigorousIntensityMinutes": 0 if good else 20,
                    },
                },
                {
                    "label": "get_sleep_data",
                    "ok": True,
                    "data": {
                        "avgOvernightHrv": 56 if good else 39,
                        "hrvStatus": "BALANCED" if good else "LOW",
                        "dailySleepDTO": {
                            "calendarDate": day.isoformat(),
                            "sleepTimeSeconds": 27000 if good else 18000,
                            "awakeSleepSeconds": 1200,
                            "avgSleepStress": 8 if good else 28,
                            "sleepScores": {
                                "overall": {
                                    "value": 86 if good else 55,
                                    "qualifierKey": "GOOD" if good else "POOR",
                                }
                            },
                        },
                    },
                },
                {
                    "label": "get_hrv_data",
                    "ok": True,
                    "data": {"hrvSummary": {"baseline": {"balancedLow": 48}}},
                },
                {
                    "label": "get_all_day_stress",
                    "ok": True,
                    "status": "success",
                    "data": {
                        "calendarDate": day.isoformat(),
                        "endTimestampLocal": f"{day.isoformat()}T18:00:00+08:00",
                        "stressValuesArray": [
                            [stress_start + index * 180_000, 18 if good else 48]
                            for index in range(361)
                        ],
                    },
                },
            ],
        },
    )


def _write_activity(root, day: date, index: int, high_load: bool) -> None:
    write_json(
        root / "activities" / f"activity_{index}.json",
        {
            "activityId": index,
            "activityName": "Indoor Cycling",
            "activityType": {"typeKey": "indoor_cycling"},
            "startTimeLocal": f"{day.isoformat()} 10:00:00",
            "duration": 5400 if high_load else 2100,
            "activityTrainingLoad": 185 if high_load else 25,
            "averageHR": 154 if high_load else 118,
            "maxHeartRate": 178 if high_load else 140,
            "hrTimeInZone_4": 1800 if high_load else 0,
            "hrTimeInZone_5": 300 if high_load else 0,
        },
    )


def _seed_history(root, days: int = 28) -> date:
    load_context(root)
    start = date(2026, 4, 29)
    for offset in range(days):
        day = start + timedelta(days=offset)
        previous_day_was_high_load = offset > 0 and (offset - 1) % 4 == 0
        _write_wellness(root, day, good=not previous_day_was_high_load)
        _write_activity(root, day, offset + 1, high_load=offset % 4 == 0)
    return start + timedelta(days=days - 1)


def _contract_prescription(
    day: date,
    *,
    session_type: str,
    modality: str,
    categories: dict[str, int],
    review_fields: list[str],
) -> dict:
    mtb = int(bool(categories.get("mtb")))
    return {
        "date": day.isoformat(),
        "prediction": {
            "expected_session": {
                "title": "Contract calibration test",
                "type": session_type,
                "modality": modality,
                "intensity": "easy",
                "duration_min": 35,
                "sessions": 1,
                "expected_training_load": 25,
                "expected_training_load_range": [20, 30],
                "expected_high_intensity_min": 0,
                "expected_rpe_score_range": [20, 40],
                "expected_feel": "normal",
                "mtb_sessions": mtb,
                "gym_sessions": 0,
                "categories": categories,
                "schema_version": 3,
                "contract_fields": SESSION_CONTRACT_FIELDS,
                "purpose": "Test whether the delivered session is evidence-complete enough to calibrate.",
                "dose": {"duration_min": 35, "load_cap": 30},
                "adaptation_hypothesis": "A matched low-cost session should leave a normal next-day response.",
                "execution_rules": ["Hold the planned dose and record the review outcome."],
                "expected_result": {"garmin_load": "20-30"},
                "stop_rules": ["Stop if the session no longer matches its purpose."],
                "post_session_review_fields": review_fields,
            },
            "coaching_adjusted_next_day_response": {"score": 70},
            "expected_next_day_response": {"score": 70},
        },
    }


def _write_self_evaluation(root, day: date, activity_id: int = 27) -> None:
    write_json(
        root / "snapshots" / "activity_self_evaluation_index.json",
        {
            "activities": [
                {
                    "activity_id": str(activity_id),
                    "date": day.isoformat(),
                    "has_self_evaluation": True,
                    "rpe_score": 40,
                    "feel_score": 75,
                }
            ]
        },
    )


def _write_mtb_activity(root, day: date, activity_id: int = 27) -> None:
    write_json(
        root / "activities" / f"activity_{activity_id}.json",
        {
            "activityId": activity_id,
            "activityName": "Kiara MTB contract test",
            "activityType": {"typeKey": "mountain_biking"},
            "startTimeLocal": f"{day.isoformat()} 10:00:00",
            "duration": 2100,
            "movingDuration": 1800,
            "activityTrainingLoad": 25,
            "averageHR": 118,
            "maxHeartRate": 140,
            "elevationGain": 245.0,
            "elevationLoss": 238.0,
            "minTemperature": 30.0,
            "maxTemperature": 35.0,
            "waterEstimated": 520,
            "avgPower": 112,
            "normPower": 145,
            "maxPower": 720,
            "max20MinPower": 158,
            "aerobicTrainingEffect": 2.8,
            "anaerobicTrainingEffect": 1.2,
            "trainingEffectLabel": "TEMPO",
            "trainingStressScore": 38,
            "hrTimeInZone_4": 0,
            "hrTimeInZone_5": 0,
        },
    )


def _write_indoor_contract_activity(root, day: date, activity_id: int = 27) -> None:
    write_json(
        root / "activities" / f"activity_{activity_id}.json",
        {
            "activityId": activity_id,
            "activityName": "Suito low-aerobic contract test",
            "activityType": {"typeKey": "indoor_cycling"},
            "startTimeLocal": f"{day.isoformat()} 18:00:00",
            "duration": 3600,
            "movingDuration": 3600,
            "activityTrainingLoad": 49,
            "averageHR": 122,
            "maxHeartRate": 138,
            "avgPower": 125,
            "normPower": 125,
            "aerobicTrainingEffect": 2.5,
            "anaerobicTrainingEffect": 0.0,
            "trainingEffectLabel": "AEROBIC_BASE",
            "trainingStressScore": 35.1,
            "hrTimeInZone_4": 0,
            "hrTimeInZone_5": 0,
        },
    )


def _write_hike_activity(root, day: date, activity_id: int = 27) -> None:
    write_json(
        root / "activities" / f"activity_{activity_id}.json",
        {
            "activityId": activity_id,
            "activityName": "Controlled family hike",
            "activityType": {"typeKey": "hiking"},
            "startTimeLocal": f"{day.isoformat()} 09:21:03",
            "duration": 10392,
            "elapsedDuration": 10392,
            "movingDuration": 1804,
            "activityTrainingLoad": 51.2,
            "averageHR": 106,
            "maxHeartRate": 151,
            "hrTimeInZone_4": 29,
            "hrTimeInZone_5": 0,
        },
    )


def _planned_mtb_nutrition() -> dict:
    return {
        "during_session_targets": {
            "source": "config/athlete_context.json:nutrition.mtb_heat_fueling_targets",
            "profile": "ride_90_to_150_min",
            "selection_reason": "MTB duration is within the 90-150 minute heat-fueling band.",
            "carbs_g_per_hour": [45, 75],
            "fluid_ml_per_hour": [500, 900],
            "sodium_mg_per_hour": [600, 1000],
        }
    }


def _write_enriched_mtb_evidence(root, day: date, activity_id: int = 27) -> None:
    write_json(
        root / "snapshots" / "activity_device_index.json",
        {
            "activities": [
                {
                    "activity_id": str(activity_id),
                    "date": day.isoformat(),
                    "device_fetch_ok": True,
                    "external_hr_sensor": True,
                    "sensors": [
                        {"sensor_type": "HEART_RATE"},
                        {"sensor_type": "BIKE_POWER"},
                    ],
                }
            ]
        },
    )
    write_json(
        root / "snapshots" / "activity_gear_index.json",
        {
            "activities": [
                {
                    "activity_id": str(activity_id),
                    "date": day.isoformat(),
                    "gear_fetch_ok": True,
                    "gear": [{"label": "Stumpjumper Expert MY25"}],
                }
            ]
        },
    )
    write_json(
        root / "activities" / "details" / f"garmin_{activity_id}_detail.json",
        {
            "activity_id": activity_id,
            "calls": {
                "details": {
                    "ok": True,
                    "data": {
                        "measurementCount": 25,
                        "metricsCount": 700,
                        "totalMetricsCount": 2100,
                    },
                },
                "weather": {
                    "ok": True,
                    "data": {"temp": 91, "apparentTemp": 99, "relativeHumidity": 70},
                },
            },
        },
    )
    write_json(
        root / "snapshots" / f"activity_loop_load_{day.isoformat()}_{activity_id}.json",
        {
            "date": day.isoformat(),
            "activity_id": activity_id,
            "official_activity_training_load": 25,
            "loops": [
                {
                    "loop": 1,
                    "label": "climb_descent_1",
                    "lap_kinds": ["climb", "descent"],
                    "elapsed_min": 30,
                    "moving_min": 25,
                    "stop_min": 5,
                    "elevation_gain_m": 120,
                    "elevation_loss_m": 115,
                    "estimated_load": {"primary_continuous_hr": 13},
                }
            ],
            "laps": [
                {
                    "timeline": {
                        "flags": ["contains_long_rest"],
                        "action_terrain_summary": {
                            "sections": {
                                "punchy_climb_pedaling": {"duration_s": 600},
                                "downhill_coasting": {"duration_s": 180},
                            }
                        },
                    }
                }
            ],
            "method": {"limits": ["Impact and braking cost are not fully captured by HR."]},
        },
    )
    next_day = day + timedelta(days=1)
    write_json(
        root / "snapshots" / "current_state.json",
        {
            "date": next_day.isoformat(),
            "latest_session_evidence": {
                "date": next_day.isoformat(),
                "activity": {"date": day.isoformat(), "category": "mtb"},
                "elevation_gain_m": 245,
                "device_temperature_max_c": 35,
                "water_estimated_ml": 520,
            },
        },
    )
    write_json(
        root / "snapshots" / f"cns_readiness_{next_day.isoformat()}.json",
        {
            "date": next_day.isoformat(),
            "status": "ready",
            "score": 84,
            "confidence": "medium",
            "session_ceiling": {"level": "full_if_physical_readiness_green"},
            "interpretation": "Decision speed and processing are normal.",
        },
    )


def test_predictive_training_builds_prescription_and_latest_review(tmp_path):
    target = _seed_history(tmp_path)
    build_predictive_training(tmp_path, target - timedelta(days=1))

    artifact = build_predictive_training(tmp_path, target)

    prediction = artifact["today_prescription"]["prediction"]
    assert prediction["status"] in {"ok", "caution"}
    assert prediction["expected_session"]["expected_training_load"] is not None
    for field in SESSION_CONTRACT_FIELDS:
        assert prediction["expected_session"].get(field), field
    assert prediction["expected_next_day_response"]["leaf_samples"] >= 0
    assert prediction["coaching_adjusted_next_day_response"]["score"] is not None
    assert artifact["latest_review"]["comparison"]["adherence_status"] in {
        "matched_expected_load",
        "harder_than_predicted",
        "easier_than_predicted",
        "missed_prescribed_session",
        "trained_on_planned_rest",
    }
    assert (tmp_path / "snapshots" / "predictive_training.json").exists()
    assert (tmp_path / "snapshots" / f"predictive_session_{target.isoformat()}.json").exists()


def test_predictive_training_uses_dated_planned_session_input(tmp_path):
    target = _seed_history(tmp_path)
    context = read_json(tmp_path / "config" / "athlete_context.json", {})
    context.setdefault("nutrition", {})["mtb_heat_fueling_targets"] = {
        "basis": "Current Garmin scale basis.",
        "ride_90_to_150_min": {
            "carbs_g_per_hour": [45, 75],
            "fluid_ml_per_hour": [500, 900],
            "sodium_mg_per_hour": [600, 1000],
        },
        "over_150_min_or_race_practice": {
            "carbs_g_per_hour": [60, 90],
            "fluid_ml_per_hour": [650, 1000],
            "sodium_mg_per_hour": [800, 1200],
        },
        "rule": "Fuel skill quality before obvious bonking.",
    }
    write_json(tmp_path / "config" / "athlete_context.json", context)
    write_json(
        tmp_path / "input" / f"planned_session_{target.isoformat()}.json",
        {
            "date": target.isoformat(),
            "session": {
                "title": "Stumpjumper 2K repeatability",
                "type": "outdoor_mtb",
                "duration_min": 105,
                "intensity": "moderate",
                "schema_version": 3,
                "contract_fields": SESSION_CONTRACT_FIELDS,
                "purpose": "Use the planned Kiara loop prescription, not the generic daily fallback.",
                "dose": {"required_repeats": 3, "optional_repeats": 1},
                "adaptation_hypothesis": "Capped repeats should build climb-to-descent repeatability.",
                "execution_rules": ["Keep descents smooth and do not chase segments."],
                "expected_result": {"garmin_load": "moderate"},
                "stop_rules": ["Stop optional work if technique fades."],
                "post_session_review_fields": ["actual_repeats_completed"],
            },
        },
    )

    artifact = build_predictive_training(tmp_path, target)

    expected = artifact["today_prescription"]["prediction"]["expected_session"]
    assert artifact["today_prescription"]["plan_source"] == {
        "type": "input_planned_session",
        "path": f"input/planned_session_{target.isoformat()}.json",
    }
    assert expected["title"] == "Stumpjumper 2K repeatability"
    assert expected["type"] == "outdoor_mtb"
    assert expected["duration_min"] == 105
    assert expected["mtb_sessions"] == 1
    assert expected["planned_nutrition"]["during_session_targets"]["profile"] == "ride_90_to_150_min"
    for field in SESSION_CONTRACT_FIELDS:
        assert expected.get(field), field


def test_weekly_session_types_are_classified_as_mtb_or_indoor_bike_actions():
    mtb = _session_expectation(
        {
            "session": {
                "title": "Kiara Enduro durability",
                "type": "mtb_durability_enduro",
                "modality": "mtb",
                "duration_min": 120,
                "intensity": "moderate_hard",
            }
        }
    )
    tempo = _session_expectation(
        {
            "session": {
                "title": "Indoor tempo/torque",
                "type": "indoor_tempo_torque",
                "modality": "bike",
                "duration_min": 75,
                "intensity": "moderate",
            }
        }
    )

    assert mtb["mtb_sessions"] == 1
    assert mtb["categories"] == {"mtb": 1}
    assert mtb["expected_high_intensity_min"] > 0
    assert tempo["mtb_sessions"] == 0
    assert tempo["categories"] == {"bike_indoor": 1}


def test_hike_and_hiking_plans_normalize_to_hike_without_hiding_duration_drift():
    for session_type, modality in (("hiking", "hike"), ("hike", "hiking")):
        expected = _session_expectation(
            {
                "session": {
                    "title": "Controlled family hike",
                    "type": session_type,
                    "modality": modality,
                    "duration_min": 90,
                    "intensity": "easy",
                }
            }
        )

        assert expected["modality"] == "hike"
        assert expected["categories"] == {"hike": 1}

        alignment = _action_alignment(
            expected,
            {
                "sessions": 1,
                "categories": {"hike": 1},
                "duration_min": 173.2,
            },
        )

        assert alignment["modality_status"] == "matched"
        assert alignment["session_count_status"] == "matched"
        assert alignment["duration_status"] == "drifted"
        assert alignment["status"] == "mismatched"


def test_review_normalizes_legacy_stored_hike_category_without_mutating_prediction(
    tmp_path,
):
    target = _seed_history(tmp_path)
    review_day = target - timedelta(days=1)
    _write_hike_activity(tmp_path, review_day)
    prescription = _contract_prescription(
        review_day,
        session_type="hiking",
        modality="hike",
        categories={"other": 1},
        review_fields=[],
    )
    expected = prescription["prediction"]["expected_session"]
    expected["duration_min"] = 90
    expected["planned_nutrition"] = {
        "heat_context": {
            "latest_session_evidence": {
                "device": {
                    "recording_device": {
                        "device_id": "PRIVATE-DEVICE",
                        "device_type_pk": 123,
                    }
                }
            }
        }
    }
    write_json(
        tmp_path / "snapshots" / f"predictive_session_{review_day.isoformat()}.json",
        prescription,
    )
    write_json(
        tmp_path / "snapshots" / "current_state.json",
        {
            "date": target.isoformat(),
            "latest_session_evidence": {
                "activity": {
                    "activity_id": "27",
                    "date": review_day.isoformat(),
                    "category": "hike",
                },
                "timing": {
                    "moving_min": 105.4,
                    "nonmoving_or_stopped_estimate_min": 67.8,
                    "stopped_min": 67.8,
                    "stopped_interpretation": (
                        "Trace-derived nonmoving-or-stopped estimate; not proof of "
                        "continuous stationary time."
                    ),
                    "plausibility": {
                        "status": (
                            "garmin_moving_duration_replaced_by_trace_estimate"
                        )
                    },
                },
            },
        },
    )
    write_json(
        tmp_path
        / "snapshots"
        / f"activity_loop_load_{review_day.isoformat()}_27.json",
        {"activity_id": "27", "laps": [], "loops": []},
    )

    review = build_predictive_review(tmp_path, review_day)
    stored_expected = review["expected"]["expected_session"]
    alignment = review["comparison"]["contract_quality"]["action_alignment"]
    correction = alignment["expected_category_normalization"]

    assert stored_expected["categories"] == {"other": 1}
    assert "PRIVATE-DEVICE" not in str(review["expected"])
    assert review["privacy"]["stored_prediction_mutated"] is False
    assert review["privacy"]["review_surface_identifier_redaction_applied"] is True
    assert alignment["stored_expected_categories"] == ["other"]
    assert alignment["expected_categories"] == ["hike"]
    assert alignment["actual_categories"] == ["hike"]
    assert alignment["modality_status"] == "matched"
    assert correction["applied"] is True
    assert correction["source"] == (
        "review_time_legacy_expected_category_normalization"
    )
    assert correction["stored_prediction_mutated"] is False
    assert correction["evidence"]["expected_session.categories"] == {"other": 1}
    assert alignment["duration_ratio"] == 1.92
    assert alignment["duration_status"] == "drifted"
    assert alignment["status"] == "mismatched"
    assert review["comparison"]["calibration_eligible"] is False
    assert review["coaching_evidence_audit"]["actual_session_evidence"][
        "loop_context"
    ]["available"] is False
    audited = review["coaching_evidence_audit"]["actual_session_evidence"][
        "activities"
    ][0]
    assert audited["moving_duration_min"] == 105.4
    assert audited["stopped_duration_min"] is None
    assert audited["nonmoving_or_stopped_estimate_min"] == 67.8
    assert audited["garmin_reported_timing"] == {
        "moving_duration_min": 30.1,
        "implied_stopped_duration_min": 143.1,
    }
    assert "not proof" in audited["timing_interpretation_guardrail"]


def test_session_expectation_preserves_numeric_planned_fueling_ranges_for_review():
    nutrition = _planned_mtb_nutrition()

    expected = _session_expectation(
        {
            "session": {
                "title": "Kiara heat fueling test",
                "type": "outdoor_mtb",
                "modality": "mtb",
                "duration_min": 120,
                "intensity": "moderate",
            },
            "nutrition": nutrition,
        }
    )

    assert expected["planned_nutrition"] == nutrition


def test_session_expectation_preserves_explicit_contract_garmin_load_provenance():
    expected = _session_expectation(
        {
            "session": {
                "title": "One-off Suito low-aerobic bridge",
                "type": "indoor_low_aerobic_sabbath_exception",
                "modality": "bike_indoor",
                "duration_min": 60,
                "intensity": "easy",
                "schema_version": 3,
                "contract_fields": SESSION_CONTRACT_FIELDS,
                "purpose": "Restore low-aerobic continuity.",
                "dose": {"duration_min": 60},
                "adaptation_hypothesis": "The easy dose should preserve next-day readiness.",
                "execution_rules": ["Remain conversational."],
                "expected_result": {"garmin_training_load": "Approximately 45-55."},
                "stop_rules": ["Stop if the session is no longer easy."],
                "post_session_review_fields": ["stop_rule_outcome"],
            }
        }
    )

    assert expected["expected_training_load"] == 35.0
    assert expected["expected_training_load_range"] == [24.5, 47.2]
    assert expected["contract_training_load_expectation"] == {
        "source": "schema_v3_contract.expected_result.garmin_training_load",
        "raw_value": "Approximately 45-55.",
        "expected_value": 50.0,
        "expected_range": [45.0, 55.0],
        "range_basis": "explicit_range",
    }


def test_session_expectation_prefers_contract_whole_session_rpe_for_composite_intensity():
    physiology = (
        "Climbs remain RPE 2-5 and whole-session RPE approximately 5-7, "
        "with no deliberate engine interval."
    )
    expected = _session_expectation(
        {
            "session": {
                "title": "HSC validation",
                "type": "mtb_setup_validation",
                "modality": "mtb",
                "duration_min": 95,
                "intensity": "skill_moderate_hard",
                "schema_version": 3,
                "contract_fields": SESSION_CONTRACT_FIELDS,
                "purpose": "Validate setup support.",
                "dose": {"first_descent": "2K+"},
                "adaptation_hypothesis": "Support should preserve intended steering.",
                "execution_rules": ["Hold the route and setup."],
                "expected_result": {
                    "physiology": physiology,
                    "garmin_training_load": "Approximately 120-175.",
                },
                "stop_rules": ["Stop for unintended steering."],
                "post_session_review_fields": ["stop_rule_outcome"],
            }
        }
    )

    assert expected["generic_intensity_alias"] == {
        "raw": "skill_moderate_hard",
        "normalized": "moderate_hard",
    }
    assert expected["generic_expected_rpe_score_range"] == [40, 65]
    assert expected["expected_rpe_score_range"] == [50.0, 70.0]
    assert expected["contract_rpe_expectation"] == {
        "source": "schema_v3_contract.expected_result.physiology",
        "raw_value": physiology,
        "expected_score_range": [50.0, 70.0],
        "scale": "garmin_rpe_score_0_to_100",
    }


def test_session_expectation_prefers_structured_contract_rpe_and_simulates_contract_load():
    target = date(2026, 7, 30)
    expected = _session_expectation(
        {
            "session": {
                "title": "Structured MTB contract",
                "type": "outdoor_mtb",
                "modality": "mtb",
                "duration_min": 95,
                "intensity": "skill_moderate_hard",
                "schema_version": 3,
                "contract_fields": SESSION_CONTRACT_FIELDS,
                "purpose": "Test structured contract precedence.",
                "dose": {"duration_min": 95},
                "adaptation_hypothesis": "The planned dose should remain auditable.",
                "execution_rules": ["Keep the planned dose."],
                "expected_result": {
                    "rpe_range_out_of_10": [5, 7],
                    "physiology": "Climbs RPE 2-5 and whole-session RPE 4-6.",
                    "garmin_training_load": "120-175",
                },
                "stop_rules": ["Stop if the action changes."],
                "post_session_review_fields": ["stop_rule_outcome"],
            }
        }
    )

    simulated = _simulate_activity_day({}, target, expected)[target.isoformat()]

    assert expected["contract_rpe_expectation"]["source"].endswith(
        "expected_result.rpe_range_out_of_10"
    )
    assert expected["expected_rpe_score_range"] == [50.0, 70.0]
    assert expected["expected_training_load"] == 126.7
    assert expected["contract_training_load_expectation"]["expected_value"] == 147.5
    assert simulated["training_load"] == 147.5


def test_contract_training_load_parser_prefers_explicit_range_and_consistent_midpoint():
    assert _training_load_range("About 50, acceptable range 45–55.") == (
        [45.0, 55.0],
        50.0,
        "explicit_range",
    )
    assert _training_load_range("Approximately 45 to 55.") == (
        [45.0, 55.0],
        50.0,
        "explicit_range",
    )
    assert _training_load_range({"range": [45, 55], "value": 70}) == (
        [45.0, 55.0],
        50.0,
        "explicit_range",
    )


def test_review_prefers_contract_garmin_load_and_completes_objective_activity_fields(tmp_path):
    target = _seed_history(tmp_path)
    review_day = target - timedelta(days=1)
    _write_indoor_contract_activity(tmp_path, review_day)
    _write_self_evaluation(tmp_path, review_day)
    review_fields = [
        "actual_duration_min",
        "average_power_w",
        "normalized_power_w",
        "average_hr",
        "heart_rate_drift",
        "actual_training_load",
        "aerobic_training_effect",
        "anaerobic_training_effect",
        "actual_rpe",
        "workout_feel",
        "stop_rule_outcome",
        "next_morning_response",
    ]
    prescription = _contract_prescription(
        review_day,
        session_type="indoor_low_aerobic_sabbath_exception",
        modality="bike_indoor",
        categories={"bike_indoor": 1},
        review_fields=review_fields,
    )
    expected = prescription["prediction"]["expected_session"]
    expected.update(
        {
            "duration_min": 60,
            "expected_training_load": 35.0,
            "expected_training_load_range": [24.5, 47.2],
            "expected_result": {"garmin_training_load": "Approximately 45-55."},
        }
    )

    review = build_predictive_review(tmp_path, review_day, prescription=prescription)
    comparison = review["comparison"]
    quality = comparison["contract_quality"]
    field_rows = {
        row["field"]: row
        for row in quality["review_field_completion"]["fields"]
    }

    assert comparison["adherence_status"] == "matched_expected_load"
    assert comparison["training_load_delta"] == -1.0
    assert comparison["execution_drift"]["actual_changed_model_input"] is False
    assert comparison["training_load_expectation"] == {
        "selected_source": "schema_v3_contract.expected_result.garmin_training_load",
        "expected_value": 50.0,
        "expected_range": [45.0, 55.0],
        "contract": {
            "source": "schema_v3_contract.expected_result.garmin_training_load",
            "raw_value": "Approximately 45-55.",
            "expected_value": 50.0,
            "expected_range": [45.0, 55.0],
            "range_basis": "explicit_range",
        },
        "generic_deterministic": {
            "source": "generic_deterministic_duration_intensity",
            "expected_value": 35.0,
            "expected_range": [24.5, 47.2],
        },
    }
    for field in (
        "average_power_w",
        "normalized_power_w",
        "average_hr",
        "aerobic_training_effect",
        "anaerobic_training_effect",
    ):
        assert field_rows[field]["status"] == "completed"
        assert field_rows[field]["source"].startswith("garmin_activity.activities[0].")
    assert field_rows["heart_rate_drift"]["status"] == "missing"
    assert quality["stop_rule_outcome"]["status"] == "not_logged"
    assert comparison["physiology_calibration_eligible"] is True
    assert comparison["calibration_eligible"] is False


def test_prediction_keeps_action_on_target_date_when_using_prior_wellness_basis(tmp_path):
    latest = _seed_history(tmp_path)
    target = latest + timedelta(days=1)
    plan = {
        "session": {
            "title": "Future indoor tempo",
            "type": "indoor_tempo_torque",
            "modality": "bike",
            "duration_min": 60,
            "intensity": "moderate",
        }
    }

    artifact = build_predictive_prescription(
        tmp_path,
        target,
        state={"date": target.isoformat()},
        plan=plan,
    )
    prediction = artifact["prediction"]

    assert prediction["basis_date"] == latest.isoformat()
    assert prediction["action_date"] == target.isoformat()
    assert prediction["predicts_date"] == (target + timedelta(days=1)).isoformat()
    assert prediction["simulated_features"]["today_training_load"] > 0


def test_predictive_review_compares_dated_prescription_to_actuals(tmp_path):
    target = _seed_history(tmp_path)
    review_day = target - timedelta(days=1)
    build_predictive_training(tmp_path, review_day)

    review = build_predictive_review(tmp_path, review_day)

    assert review["prescription_available"] is True
    assert review["actual_activity"]["sessions"] > 0
    assert review["actual_next_day_response"]["status"] == "available"
    assert review["comparison"]["response_status"] in {
        "within_expected_band",
        "worse_than_expected",
        "better_than_expected",
        "no_expected_response",
    }
    assert review["comparison"]["calibration_status"] in {
        "calibrated",
        "model_miss",
        "execution_changed_input",
        "not_calibratable",
        "contract_missing",
        "contract_action_mismatch",
        "contract_dose_stopped",
        "contract_unreliable",
        "technical_quality_degraded",
        "contract_incomplete",
    }
    assert "execution_risk_stress_test" in review["comparison"]


def test_contract_quality_requires_complete_technical_review_for_full_calibration(tmp_path):
    target = _seed_history(tmp_path)
    review_day = target - timedelta(days=1)
    _write_mtb_activity(tmp_path, review_day)
    _write_enriched_mtb_evidence(tmp_path, review_day)
    _write_self_evaluation(tmp_path, review_day)
    review_fields = [
        "actual_duration_min",
        "actual_training_load",
        "actual_rpe",
        "workout_feel",
        "next_morning_response",
        "stop_rule_outcome",
        "fueling_carbs_g_per_hour",
        "fluid_ml_per_hour",
        "sodium_mg_per_hour",
        "technical_quality_notes",
        "late_session_skill_fade",
        "actual_repeats_completed",
    ]
    prescription = _contract_prescription(
        review_day,
        session_type="outdoor_mtb",
        modality="mtb",
        categories={"mtb": 1},
        review_fields=review_fields,
    )
    prescription["prediction"]["expected_session"]["planned_nutrition"] = _planned_mtb_nutrition()
    write_json(
        tmp_path / "input" / f"feedback_{review_day.isoformat()}.json",
        {
            "date": review_day.isoformat(),
            "entries": [
                {
                    "activity_id": "27",
                    "session_contract_review": {
                        "stop_rule_outcome": "not_triggered",
                        "technical_quality_notes": "Braking and line choice stayed deliberate through the final descent.",
                        "late_session_skill_fade": "none",
                        "fueling_carbs_g_per_hour": 45,
                        "fluid_ml_per_hour": 650,
                        "sodium_mg_per_hour": 600,
                        "actual_repeats_completed": 3,
                    },
                }
            ],
        },
    )

    review = build_predictive_review(tmp_path, review_day, prescription=prescription)
    comparison = review["comparison"]
    quality = comparison["contract_quality"]

    assert comparison["physiology_calibration_eligible"] is True
    assert quality["status"] == "complete"
    assert quality["feedback"]["scope"] == "activity_matched"
    assert quality["technical_quality"]["status"] == "clean"
    assert quality["stop_rule_outcome"]["status"] == "not_triggered"
    assert quality["review_field_completion"]["missing"] == []
    assert comparison["calibration_eligible"] is True
    assert comparison["calibration_status"] in {"calibrated", "model_miss"}
    fueling = quality["fueling_adequacy"]
    assert fueling["status"] == "within_planned_ranges"
    assert fueling["metrics"]["carbs_g_per_hour"]["status"] == "within_planned_range"
    audit = review["coaching_evidence_audit"]
    activity = audit["actual_session_evidence"]["activities"][0]
    assert activity["elevation_gain_m"] == 245.0
    assert activity["temperature"]["device_max_c"] == 35.0
    assert activity["water_estimated_ml"] == 520.0
    assert activity["power"]["normalized_w"] == 145.0
    assert "maxFtp is Garmin's sparse FTP-detection surface" in activity["power"]["decision_use"]
    assert activity["training_effect"] == {"aerobic": 2.8, "anaerobic": 1.2, "label": "TEMPO"}
    assert activity["metadata_confidence"]["hr_source_confidence"] == "external_hr_confirmed"
    assert activity["metadata_confidence"]["gear_labels"] == ["Stumpjumper Expert MY25"]
    detail = audit["actual_session_evidence"]["activity_detail"][0]
    assert detail["metric_descriptor_count"] == 25
    assert detail["sample_count"] == 700
    assert detail["total_metric_values"] == 2100
    assert detail["source"] == "activities/details/garmin_27_detail.json"
    assert detail["weather"]["unit_status"] == "unknown_do_not_use_quantitatively"
    loops = audit["actual_session_evidence"]["loop_context"]
    assert loops["available"] is True
    assert loops["activities"][0]["action_terrain_minutes"]["punchy_climb_pedaling"] == 10.0
    assert audit["actual_session_evidence"]["latest_session_evidence"]["available"] is True
    assert audit["cns_outcome"]["next_day"]["status"] == "ready"
    assert audit["calibration_eligibility_effect"] == "none"


def test_contract_quality_keeps_physiology_match_out_of_calibration_when_review_is_incomplete(tmp_path):
    target = _seed_history(tmp_path)
    review_day = target - timedelta(days=1)
    _write_mtb_activity(tmp_path, review_day)
    _write_self_evaluation(tmp_path, review_day)
    prescription = _contract_prescription(
        review_day,
        session_type="outdoor_mtb",
        modality="mtb",
        categories={"mtb": 1},
        review_fields=[
            "actual_duration_min",
            "actual_training_load",
            "actual_rpe",
            "workout_feel",
            "next_morning_response",
            "technical_quality_notes",
            "late_session_skill_fade",
        ],
    )
    prescription["prediction"]["expected_session"]["planned_nutrition"] = _planned_mtb_nutrition()
    write_json(
        tmp_path / "input" / f"feedback_{review_day.isoformat()}.json",
        {
            "date": review_day.isoformat(),
            "entries": [
                {
                    "activity_id": "27",
                    "session_contract_review": {
                        "technical_quality_notes": "The final descent stayed controlled.",
                        "fueling_carbs_g_per_hour": 20,
                        "fluid_ml_per_hour": 300,
                        "sodium_mg_per_hour": 400,
                    },
                }
            ],
        },
    )

    review = build_predictive_review(tmp_path, review_day, prescription=prescription)
    comparison = review["comparison"]
    quality = comparison["contract_quality"]

    assert comparison["physiology_calibration_eligible"] is True
    assert quality["status"] == "incomplete"
    assert quality["stop_rule_outcome"]["status"] == "not_logged"
    assert "late_session_skill_fade" in quality["review_field_completion"]["missing"]
    assert comparison["calibration_status"] == "contract_incomplete"
    assert comparison["calibration_eligible"] is False
    fueling = quality["fueling_adequacy"]
    assert fueling["status"] == "outside_planned_range"
    assert fueling["metrics"]["fluid_ml_per_hour"]["status"] == "below_planned_range"
    assert fueling["affects_calibration_eligibility"] is False
    assert review["coaching_evidence_audit"]["calibration_eligibility_effect"] == "none"


def test_contract_quality_rejects_stop_rule_overrun_even_with_complete_review_fields(tmp_path):
    target = _seed_history(tmp_path)
    review_day = target - timedelta(days=1)
    _write_mtb_activity(tmp_path, review_day)
    _write_self_evaluation(tmp_path, review_day)
    prescription = _contract_prescription(
        review_day,
        session_type="outdoor_mtb",
        modality="mtb",
        categories={"mtb": 1},
        review_fields=[
            "actual_duration_min",
            "actual_training_load",
            "actual_rpe",
            "workout_feel",
            "next_morning_response",
            "technical_quality_notes",
            "late_session_skill_fade",
        ],
    )
    write_json(
        tmp_path / "input" / f"feedback_{review_day.isoformat()}.json",
        {
            "date": review_day.isoformat(),
            "entries": [
                {
                    "activity_id": "27",
                    "session_contract_review": {
                        "stop_rule_outcome": "triggered_but_continued",
                        "technical_quality_notes": "Line choice became reactive late in the session.",
                        "late_session_skill_fade": "present",
                    },
                }
            ],
        },
    )

    review = build_predictive_review(tmp_path, review_day, prescription=prescription)
    comparison = review["comparison"]
    quality = comparison["contract_quality"]

    assert comparison["physiology_calibration_eligible"] is True
    assert quality["status"] == "unsafe_stop_rule_continued"
    assert quality["stop_rule_outcome"]["status"] == "triggered_but_continued"
    assert comparison["calibration_status"] == "contract_unreliable"
    assert comparison["calibration_eligible"] is False


def test_contract_quality_rejects_explicit_structured_route_mismatch(tmp_path):
    target = _seed_history(tmp_path)
    review_day = target - timedelta(days=1)
    _write_mtb_activity(tmp_path, review_day)
    _write_self_evaluation(tmp_path, review_day)
    prescription = _contract_prescription(
        review_day,
        session_type="outdoor_mtb",
        modality="mtb",
        categories={"mtb": 1},
        review_fields=[
            "actual_duration_min",
            "actual_training_load",
            "actual_rpe",
            "workout_feel",
            "next_morning_response",
            "technical_quality_notes",
            "late_session_skill_fade",
        ],
    )
    write_json(
        tmp_path / "input" / f"feedback_{review_day.isoformat()}.json",
        {
            "date": review_day.isoformat(),
            "entries": [
                {
                    "activity_id": "27",
                    "session_contract_review": {
                        "stop_rule_outcome": "not_triggered",
                        "technical_quality_notes": "Braking and line choice stayed controlled.",
                        "late_session_skill_fade": "none",
                    },
                    "coach_contract_audit": {
                        "action_alignment": "route_and_descent_count_mismatch",
                    },
                }
            ],
        },
    )

    review = build_predictive_review(tmp_path, review_day, prescription=prescription)
    comparison = review["comparison"]
    quality = comparison["contract_quality"]
    alignment = quality["action_alignment"]
    structured = alignment["structured_feedback_alignment"]

    assert comparison["physiology_calibration_eligible"] is True
    assert alignment["modality_status"] == "matched"
    assert alignment["session_count_status"] == "matched"
    assert alignment["duration_status"] == "matched"
    assert alignment["status"] == "mismatched"
    assert structured == {
        "available": True,
        "status": "mismatched",
        "value": "route_and_descent_count_mismatch",
        "normalized_value": "route_and_descent_count_mismatch",
        "source": "feedback.entries[0].coach_contract_audit.action_alignment",
    }
    assert quality["status"] == "action_mismatch"
    assert quality["calibration_eligible"] is False
    assert comparison["calibration_status"] == "contract_action_mismatch"
    assert comparison["calibration_eligible"] is False


def test_contract_quality_does_not_infer_route_mismatch_from_feedback_prose(tmp_path):
    target = _seed_history(tmp_path)
    review_day = target - timedelta(days=1)
    _write_mtb_activity(tmp_path, review_day)
    _write_self_evaluation(tmp_path, review_day)
    prescription = _contract_prescription(
        review_day,
        session_type="outdoor_mtb",
        modality="mtb",
        categories={"mtb": 1},
        review_fields=[
            "actual_duration_min",
            "actual_training_load",
            "actual_rpe",
            "workout_feel",
            "next_morning_response",
            "technical_quality_notes",
            "late_session_skill_fade",
        ],
    )
    write_json(
        tmp_path / "input" / f"feedback_{review_day.isoformat()}.json",
        {
            "date": review_day.isoformat(),
            "entries": [
                {
                    "activity_id": "27",
                    "subjective_report": "The rider described a route mismatch in conversation.",
                    "session_contract_review": {
                        "stop_rule_outcome": "not_triggered",
                        "technical_quality_notes": "Braking and line choice stayed controlled.",
                        "late_session_skill_fade": "none",
                    },
                    "coach_contract_audit": {
                        "reason": "The written and actual routes may have drifted.",
                    },
                }
            ],
        },
    )

    review = build_predictive_review(tmp_path, review_day, prescription=prescription)
    quality = review["comparison"]["contract_quality"]
    alignment = quality["action_alignment"]

    assert alignment["status"] == "matched"
    assert alignment["structured_feedback_alignment"] == {
        "available": False,
        "status": "not_logged",
        "value": None,
        "normalized_value": None,
        "source": None,
    }
    assert quality["status"] == "complete"
    assert quality["calibration_eligible"] is True


def test_predictive_prescription_adds_execution_risk_from_backtest(tmp_path):
    target = _seed_history(tmp_path)
    rows = []
    for index in range(4):
        rows.append(
            {
                "date": f"2026-05-{10 + index:02d}",
                "prediction": {
                    "expected_session": {
                        "type": "outdoor_bike_optional",
                        "expected_training_load": 33.8,
                    }
                },
                "actual_session": {
                    "duration_min": 150 + index,
                    "training_load": 180 + index * 10,
                    "high_intensity_min": 20 + index,
                    "categories": {"mtb": 1},
                },
                "comparison": {"adherence_status": "harder_than_predicted"},
            }
        )
    write_json(tmp_path / "snapshots" / "predictive_backtest_10_dates.json", {"rows": rows})
    plan = {
        "session": {
            "title": "Easy bike continuity",
            "type": "outdoor_bike_optional",
            "duration_min": 45,
            "intensity": "easy",
        }
    }

    artifact = build_predictive_prescription(tmp_path, target, state={"date": target.isoformat()}, plan=plan)

    risk = artifact["prediction"]["execution_risk"]
    assert risk["status"] == "calibrated"
    assert risk["level"] == "high"
    assert risk["likely_harder_than_plan"] is True
    assert risk["stress_test_expected_session"]["expected_training_load"] > 33.8
    assert risk["stress_test_next_day_response"]["leaf_samples"] >= 0
    assert risk["stress_test_coaching_adjusted_next_day_response"]["score"] <= risk["stress_test_next_day_response"]["score"]


def test_live_prescription_preserves_existing_dated_file_after_activity(tmp_path, monkeypatch):
    target = _seed_history(tmp_path)
    dated_path = tmp_path / "snapshots" / f"predictive_session_{target.isoformat()}.json"
    write_json(
        dated_path,
        {
            "date": target.isoformat(),
            "generated_at": "2026-05-26T08:00:00+08:00",
            "prediction": {"expected_session": {"title": "Pre-ride plan"}},
        },
    )
    monkeypatch.setattr(
        "coach_sync.predictive_training.today_local",
        lambda tz_name=None: target,
    )
    plan = {
        "session": {
            "title": "Post-sync rebuilt plan",
            "type": "outdoor_bike_optional",
            "duration_min": 45,
            "intensity": "easy",
        }
    }

    artifact = build_predictive_prescription(
        tmp_path,
        target,
        state={"date": target.isoformat()},
        plan=plan,
    )

    preserved = read_json(dated_path, {})
    assert artifact["artifacts"]["dated_write_status"] == "preserved_existing_after_activity"
    assert preserved["prediction"]["expected_session"]["title"] == "Pre-ride plan"
    assert read_json(tmp_path / "snapshots" / "predictive_session_plan.json", {})["prediction"][
        "expected_session"
    ]["title"] == "Post-sync rebuilt plan"
