from datetime import date, datetime, timedelta

import pytest

from coach_sync.context import load_context
from coach_sync.io import read_json, write_json
from coach_sync.planning import SESSION_CONTRACT_FIELDS
from coach_sync.predictive_training import (
    _action_alignment,
    _apply_matched_2k_load_baseline,
    _rpe_score_range,
    _risk_adjusted_expected_session,
    _self_evaluation_for_date,
    _selected_training_load_expectation,
    _session_expectation,
    _simulate_activity_day,
    _training_load_range,
    build_predictive_prescription,
    build_predictive_review,
    build_predictive_training,
)


MATCHED_2K_IDENTITY = {
    "schema_version": 1,
    "venue_key": "bukit_kiara",
    "route_key": "full_2k",
    "bike_key": "stumpjumper_expert_my25",
    "access_key": "self_pedaled",
    "quality_descent_count": 3,
}


def test_predictive_self_evaluation_excludes_off_grid_garmin_categories(tmp_path):
    day = date(2026, 8, 31)
    write_json(
        tmp_path / "snapshots" / "activity_self_evaluation_index.json",
        {
            "activities": [
                {
                    "activity_id": "bad",
                    "date": day.isoformat(),
                    "has_self_evaluation": True,
                    "feel_score": 74,
                    "rpe_score": 35,
                },
                {
                    "activity_id": "good",
                    "date": day.isoformat(),
                    "has_self_evaluation": True,
                    "feel_score": 75,
                    "rpe_score": 30,
                },
            ]
        },
    )

    result = _self_evaluation_for_date(tmp_path, day)

    assert result["count"] == 1
    assert result["avg_feel_score"] == 75
    assert result["avg_rpe_score"] == 30
    assert result["invalid_category_rows"] == [
        {
            "activity_id": "bad",
            "invalid_fields": ["rpe_score", "feel_score"],
        }
    ]


def _matched_2k_plan(*, session_type: str = "mtb_skill_familiar_capped", identity=True) -> dict:
    session = {
        "title": "Exact three-run Stumpjumper full-2K session",
        "type": session_type,
        "modality": "mtb",
        "duration_min": 90,
        "intensity": "skill",
        "schema_version": 3,
        "contract_fields": SESSION_CONTRACT_FIELDS,
        "purpose": "Rebuild exact familiar-route processing under a bounded dose.",
        "dose": {"hard_cap": "Three quality descents."},
        "adaptation_hypothesis": "Matched actions make load comparison interpretable.",
        "execution_rules": ["Keep route, bike and access mode fixed."],
        "expected_result": {"technical": "Final execution matches the first."},
        "stop_rules": ["Stop for technical fade."],
        "post_session_review_fields": ["stop_rule_outcome"],
    }
    if identity:
        session["action_identity"] = dict(MATCHED_2K_IDENTITY)
    return {"session": session}


def _append_index_row(root, filename: str, row: dict) -> None:
    path = root / "snapshots" / filename
    payload = read_json(path, {"activities": []})
    payload.setdefault("activities", []).append(row)
    write_json(path, payload)


def _write_exact_2k_sample(
    root,
    day: date,
    activity_id: int,
    training_load: float,
    *,
    external_hr: bool = True,
    loop_structure: str = "paired",
    duration_min: float = 82.5,
) -> None:
    write_json(
        root / "activities" / f"activity_{activity_id}.json",
        {
            "activityId": activity_id,
            "activityName": "Kiara exact full 2K repeats",
            "activityType": {"typeKey": "mountain_biking"},
            "startTimeLocal": f"{day.isoformat()} 09:00:00",
            "duration": duration_min * 60,
            "activityTrainingLoad": training_load,
            "averageHR": 146,
            "maxHeartRate": 184,
        },
    )
    _append_index_row(
        root,
        "activity_gear_index.json",
        {
            "activity_id": str(activity_id),
            "date": day.isoformat(),
            "category": "mtb",
            "gear_fetch_ok": True,
            "gear": [{"label": "Stumpjumper Expert MY25"}],
        },
    )
    _append_index_row(
        root,
        "activity_device_index.json",
        {
            "activity_id": str(activity_id),
            "date": day.isoformat(),
            "category": "mtb",
            "device_fetch_ok": True,
            "external_hr_sensor": external_hr,
            "hr_source_classification": "external_standard_metadata",
            "external_hr_battery_statuses": ["GOOD"],
        },
    )
    write_json(
        root / "input" / f"feedback_{day.isoformat()}.json",
        {
            "activity_id": str(activity_id),
            "action_identity": dict(MATCHED_2K_IDENTITY),
            "action_identity_recorded_at_local": (
                f"{day.isoformat()}T12:00:00+08:00"
            ),
        },
    )
    if loop_structure == "legacy":
        loops = [
            {
                "loop": index + 1,
                "label": label,
                "lap_kinds": ["descent" if "2K" in label else "climb"],
            }
            for index, label in enumerate(
                [
                    "Lap1",
                    "Lap2_2K",
                    "Lap3",
                    "Lap4_2K_hard",
                    "Lap5",
                    "Lap6_2K_hard",
                    "Lap7_2K+",
                ]
            )
        ]
    else:
        loops = [
            {
                "loop": index,
                "label": f"Loop {index}",
                "lap_kinds": ["climb", "descent"],
            }
            for index in range(1, 4)
        ]
    write_json(
        root
        / "snapshots"
        / f"activity_loop_load_{day.isoformat()}_{activity_id}.json",
        {
            "date": day.isoformat(),
            "activity_id": str(activity_id),
            "official_activity_training_load": round(training_load, 1),
            "loops": loops,
        },
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


def test_optional_session_skip_is_allowed_and_not_calibratable(tmp_path):
    latest = _seed_history(tmp_path)
    review_day = latest + timedelta(days=1)
    _write_wellness(tmp_path, review_day, good=True)
    _write_wellness(tmp_path, review_day + timedelta(days=1), good=True)
    prescription = _contract_prescription(
        review_day,
        session_type="indoor_low_aerobic_travel_day",
        modality="bike_indoor",
        categories={"bike_indoor": 1},
        review_fields=["stop_rule_outcome", "next_morning_response"],
    )
    expected = prescription["prediction"]["expected_session"]
    expected["optional"] = True
    expected["dose"]["skip_branch"] = (
        "Skip after travel if the circulation spin is not useful; no replacement is owed."
    )
    prescription["prediction"]["execution_risk"] = {
        "stress_test_coaching_adjusted_next_day_response": {"score": 55}
    }

    review = build_predictive_review(
        tmp_path,
        review_day,
        prescription=prescription,
    )
    comparison = review["comparison"]

    assert review["actual_activity"]["sessions"] == 0
    assert review["actual_next_day_response"]["status"] == "available"
    assert comparison["adherence_status"] == "allowed_optional_skip"
    assert comparison["response_status"] == "not_applicable_optional_skip"
    assert comparison["response_delta"] is None
    assert comparison["execution_risk_stress_test"] == {
        "available": False,
        "expected_response_source": "coaching_adjusted_stress_test",
        "score": 55,
        "response_delta": None,
        "response_status": "not_applicable_optional_skip",
    }
    assert comparison["physiology_calibration_status"] == "not_calibratable_optional_skip"
    assert comparison["physiology_calibration_eligible"] is False
    assert comparison["physiology_calibration_weight"] == 0.0
    assert comparison["calibration_status"] == "not_calibratable_optional_skip"
    assert comparison["calibration_eligible"] is False
    assert comparison["calibration_weight"] == 0.0
    assert comparison["contract_quality"]["status"] == "optional_skip"
    assert comparison["contract_quality"]["action_alignment"]["status"] == (
        "allowed_optional_skip"
    )
    assert comparison["execution_drift"] == {
        "training_load_ratio": 0.0,
        "duration_ratio": 0.0,
        "actual_changed_model_input": True,
        "adherence_drift": False,
        "within_written_optionality": True,
    }
    assert "not adherence drift" in comparison["interpretation"]


def _legacy_optional_prescription_and_source(day: date) -> tuple[dict, dict]:
    prescription = _contract_prescription(
        day,
        session_type="indoor_low_aerobic_travel_day",
        modality="bike_indoor",
        categories={"bike_indoor": 1},
        review_fields=["stop_rule_outcome", "next_morning_response"],
    )
    expected = prescription["prediction"]["expected_session"]
    expected["dose"]["skip_branch"] = (
        "Skip after travel if rest better serves recovery; no replacement is owed."
    )
    prescription["plan_source"] = {
        "type": "input_planned_session",
        "path": f"input/planned_session_{day.isoformat()}.json",
    }
    source_fields = (
        "title",
        "type",
        "modality",
        "duration_min",
        "intensity",
        "schema_version",
        "contract_fields",
        *SESSION_CONTRACT_FIELDS,
    )
    source_session = {field: expected[field] for field in source_fields}
    source_session["optional"] = True
    source = {
        "date": day.isoformat(),
        "session": source_session,
    }
    return prescription, source


def test_review_recovers_legacy_optionality_from_exact_recorded_input_plan(tmp_path):
    latest = _seed_history(tmp_path)
    review_day = latest + timedelta(days=1)
    _write_wellness(tmp_path, review_day, good=True)
    _write_wellness(tmp_path, review_day + timedelta(days=1), good=True)
    prescription, source = _legacy_optional_prescription_and_source(review_day)
    dated_path = (
        tmp_path
        / "snapshots"
        / f"predictive_session_{review_day.isoformat()}.json"
    )
    write_json(dated_path, prescription)
    write_json(
        tmp_path / "input" / f"planned_session_{review_day.isoformat()}.json",
        source,
    )

    review = build_predictive_review(tmp_path, review_day)
    persisted_prescription = read_json(dated_path, {})
    resolution = review["optionality_resolution"]

    assert "optional" not in prescription["prediction"]["expected_session"]
    assert "optional" not in persisted_prescription["prediction"]["expected_session"]
    assert review["expected"]["expected_session"]["optional"] is True
    assert resolution["applied"] is True
    assert resolution["status"] == "recovered_from_exact_dated_input_plan"
    assert resolution["source"] == {
        "type": "input_planned_session",
        "path": f"input/planned_session_{review_day.isoformat()}.json",
        "date": review_day.isoformat(),
    }
    assert resolution["mismatched_fields"] == []
    assert resolution["stored_prediction_mutated"] is False
    assert review["comparison"]["adherence_status"] == "allowed_optional_skip"
    assert review["comparison"]["calibration_eligible"] is False


def test_review_rejects_legacy_optionality_when_recorded_plan_changed(tmp_path):
    latest = _seed_history(tmp_path)
    review_day = latest + timedelta(days=1)
    _write_wellness(tmp_path, review_day, good=True)
    _write_wellness(tmp_path, review_day + timedelta(days=1), good=True)
    prescription, source = _legacy_optional_prescription_and_source(review_day)
    source["session"]["dose"] = {
        **source["session"]["dose"],
        "skip_branch": "This source was changed after the immutable prediction was stored.",
    }
    write_json(
        tmp_path / "input" / f"planned_session_{review_day.isoformat()}.json",
        source,
    )

    review = build_predictive_review(
        tmp_path,
        review_day,
        prescription=prescription,
    )
    resolution = review["optionality_resolution"]

    assert resolution["applied"] is False
    assert resolution["status"] == "rejected_stored_action_mismatch"
    assert resolution["mismatched_fields"] == ["dose"]
    assert "optional" not in review["expected"]["expected_session"]
    assert review["comparison"]["adherence_status"] == "missed_prescribed_session"
    assert review["comparison"]["execution_drift"]["adherence_drift"] is True


def test_session_expectation_preserves_explicit_optionality_and_skip_branch():
    expected = _session_expectation(
        {
            "session": {
                "title": "Optional travel-day spin",
                "type": "indoor_low_aerobic_travel_day",
                "modality": "bike_indoor",
                "duration_min": 40,
                "intensity": "easy",
                "optional": True,
                "dose": {
                    "skip_branch": "Skip after travel if recovery is better served by rest."
                },
            }
        }
    )

    assert expected["optional"] is True
    assert expected["dose"]["skip_branch"] == (
        "Skip after travel if recovery is better served by rest."
    )


def test_session_expectation_preserves_compact_adaptive_progression_identity():
    expected = _session_expectation(
        {
            "session": {
                "title": "Adaptive endurance duration",
                "type": "outdoor_bike_optional",
                "modality": "bike_indoor",
                "duration_min": 60,
                "intensity": "easy",
                "adaptive_programming": {
                    "progression_track": "endurance",
                    "from_step": "60_min_120_130_w",
                    "planned_step": "75_min_near_125_w",
                    "progression_lever": "endurance_duration",
                    "adaptive_state_basis_date": "2026-09-28",
                    "roadmap_block_id": "2026-09-28:build",
                    "weekly_budget": {"large_payload_must_not_copy": True},
                },
            }
        }
    )

    assert expected["adaptive_progression_identity"] == {
        "progression_track": "endurance",
        "from_step": "60_min_120_130_w",
        "planned_step": "75_min_near_125_w",
        "progression_lever": "endurance_duration",
        "adaptive_state_basis_date": "2026-09-28",
        "roadmap_block_id": "2026-09-28:build",
    }
    assert "weekly_budget" not in expected["adaptive_progression_identity"]


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
        "scale": "garmin_rpe_score_10_to_100",
    }


@pytest.mark.parametrize(
    ("raw_value", "expected_range"),
    [
        ([1, 10], [10.0, 100.0]),
        ([10, 100], [10.0, 100.0]),
        ({"range": [5, 7]}, [50.0, 70.0]),
        ("whole-session RPE 5-7", [50.0, 70.0]),
    ],
)
def test_contract_rpe_parser_accepts_only_canonical_garmin_categories(
    raw_value, expected_range
):
    assert _rpe_score_range(raw_value, require_rpe_label=isinstance(raw_value, str)) == (
        expected_range
    )


@pytest.mark.parametrize(
    "raw_value",
    [
        [0, 2],
        [3.5, 7],
        [35, 70],
        [10, 101],
    ],
)
def test_contract_rpe_parser_rejects_zero_and_off_grid_categories(raw_value):
    assert _rpe_score_range(raw_value) is None


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
    learning = comparison["learning_disposition"]
    assert learning["nominal_contract_validation"] == {
        "status": "rejected_unsafe_stop_rule_continued",
        "eligible": False,
        "weight": 0.0,
        "permanent_exclusion": True,
        "target": "nominal_prescription",
        "reason": (
            "The stop rule was overridden, so this session can never validate the nominal prescription, "
            "even if next-day recovery is favorable."
        ),
    }
    assert learning["delivered_action_response"]["status"] == (
        "not_eligible_insufficient_characterization"
    )
    assert learning["execution_boundary_learning"]["eligible"] is False
    assert learning["safety_adherence_learning"]["eligible"] is True
    assert learning["counterfactual_nominal_response"]["status"] == "unidentifiable"


def test_characterized_stop_rule_overrun_learns_boundary_then_low_weight_delivered_response(
    tmp_path,
):
    latest = _seed_history(tmp_path)
    review_day = latest + timedelta(days=1)
    activity_id = 97
    _write_indoor_contract_activity(tmp_path, review_day, activity_id=activity_id)
    _write_self_evaluation(tmp_path, review_day, activity_id=activity_id)
    prescription = _contract_prescription(
        review_day,
        session_type="indoor_tempo_torque",
        modality="bike_indoor",
        categories={"bike_indoor": 1},
        review_fields=[
            "actual_duration_min",
            "actual_training_load",
            "actual_rpe",
            "workout_feel",
            "stop_rule_outcome",
            "next_morning_response",
        ],
    )
    expected = prescription["prediction"]["expected_session"]
    expected.update(
        {
            "duration_min": 60,
            "expected_training_load": 50,
            "expected_training_load_range": [45, 55],
            "expected_rpe_score_range": [50, 60],
            "expected_result": {"garmin_load": "45-55"},
        }
    )
    write_json(
        tmp_path / "input" / f"feedback_{review_day.isoformat()}.json",
        {
            "date": review_day.isoformat(),
            "entries": [
                {
                    "activity_id": str(activity_id),
                    "reported_context": {
                        "continuation_reason": (
                            "Athlete believed completing repetition three was necessary for VO2max."
                        )
                    },
                    "session_contract_review": {
                        "stop_rule_outcome": "triggered_but_continued",
                        "stop_trigger_timing": "repetition_3",
                        "repetition_reported_rpe_0_to_10": [5, 6, 8],
                        "repetition_3_rpe_components_0_to_10": {
                            "local_legs": 8,
                            "breathing": 5,
                            "whole_body": 5,
                        },
                    },
                    "objective_interval_evidence": {
                        "repetitions": [
                            {
                                "number": 1,
                                "duration_min": 8,
                                "average_power_w": 169,
                                "average_hr_bpm": 140,
                                "average_cadence_rpm": 70,
                            },
                            {
                                "number": 2,
                                "duration_min": 8,
                                "average_power_w": 168,
                                "average_hr_bpm": 145,
                                "average_cadence_rpm": 68,
                            },
                            {
                                "number": 3,
                                "duration_min": 8,
                                "average_power_w": 168,
                                "average_hr_bpm": 150,
                                "average_cadence_rpm": 70,
                            },
                        ]
                    },
                }
            ],
        },
    )

    pending = build_predictive_review(tmp_path, review_day, prescription=prescription)
    pending_comparison = pending["comparison"]
    pending_learning = pending_comparison["learning_disposition"]

    assert pending_comparison["calibration_status"] == "contract_unreliable"
    assert pending_learning["nominal_contract_validation"]["permanent_exclusion"] is True
    assert pending_learning["delivered_action_response"]["status"] == "pending_next_day"
    assert pending_learning["delivered_action_response"]["eligible"] is False
    boundary = pending_learning["execution_boundary_learning"]
    assert boundary["eligible"] is True
    assert boundary["trigger"]["repetition"] == 3
    assert boundary["trigger"]["dimension"] == "local_legs"
    assert boundary["repetition_reported_rpe_0_to_10"] == [5.0, 6.0, 8.0]
    assert boundary["last_within_rpe_ceiling_repetition"] == 2
    assert boundary["external_work_stable"] is True
    safety = pending_learning["safety_adherence_learning"]
    assert safety["eligible"] is True
    assert safety["event"] == "stop_rule_overridden"
    assert safety["characterization_status"] == "complete"

    _write_wellness(tmp_path, review_day + timedelta(days=1), good=True)
    observed = build_predictive_review(tmp_path, review_day, prescription=prescription)
    observed_comparison = observed["comparison"]
    observed_learning = observed_comparison["learning_disposition"]
    delivered = observed_learning["delivered_action_response"]

    assert delivered["status"] in {
        "observed_within_expected_band",
        "observed_model_miss",
    }
    assert delivered["eligible"] is True
    assert delivered["weight"] == 0.35
    assert delivered["target"] == "executed_action_only"
    assert delivered["policy_status"] == "out_of_policy_stop_rule_override"
    assert delivered["nominal_prescription_validation"] is False
    assert observed_comparison["physiology_calibration_eligible"] is True
    assert observed_comparison["physiology_calibration_weight"] == 0.35
    assert observed_comparison["calibration_eligible"] is False
    assert observed_learning["nominal_contract_validation"]["weight"] == 0.0
    assert observed_learning["counterfactual_nominal_response"]["status"] == (
        "unidentifiable"
    )


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


def test_predictive_review_does_not_use_same_date_rolling_plan_without_dated_prescription(
    tmp_path,
):
    target = _seed_history(tmp_path)
    write_json(
        tmp_path / "snapshots" / "predictive_session_plan.json",
        {
            "date": target.isoformat(),
            "generated_at": f"{target.isoformat()}T20:00:00+08:00",
            "prediction": {
                "expected_session": {
                    "title": "Post-session rolling recovery plan",
                    "type": "recovery_reset",
                    "modality": "other",
                    "duration_min": 20,
                }
            },
            "artifacts": {"dated_write_status": "skipped_after_activity"},
        },
    )

    review = build_predictive_review(tmp_path, target)

    assert review["prescription_available"] is False
    assert review["expected"] == {}
    assert review["comparison"]["adherence_status"] == "no_stored_prescription"
    assert review["comparison"]["calibration_status"] == "not_calibratable"
    assert review["comparison"]["contract_quality"]["reasons"] == [
        "No dated pre-session prescription was stored for this date."
    ]


def test_exact_2k_baseline_is_strictly_prior_and_matches_future_three_run_action(tmp_path):
    load_context(tmp_path)
    _write_exact_2k_sample(
        tmp_path,
        date(2026, 7, 9),
        7001,
        191.0406,
        loop_structure="legacy",
    )
    _write_exact_2k_sample(tmp_path, date(2026, 8, 13), 7002, 200.0730)
    _write_exact_2k_sample(tmp_path, date(2026, 8, 14), 7003, 999.0)
    plan = _matched_2k_plan()

    same_day_expected = _apply_matched_2k_load_baseline(
        tmp_path,
        date(2026, 8, 13),
        plan,
        _session_expectation(plan),
    )
    same_day = same_day_expected["action_matched_training_load_expectation"]
    assert same_day["status"] == "insufficient_matched_route_repeat_samples"
    assert same_day["sample_dates"] == ["2026-07-09"]
    assert same_day_expected["expected_training_load"] is None

    future_expected = _apply_matched_2k_load_baseline(
        tmp_path,
        date(2026, 8, 14),
        plan,
        _session_expectation(plan),
    )
    future = future_expected["action_matched_training_load_expectation"]
    assert future["status"] == "matched_route_repeat_baseline"
    assert future["sample_count"] == 2
    assert future["sample_dates"] == ["2026-07-09", "2026-08-13"]
    assert all(date.fromisoformat(day) < date(2026, 8, 14) for day in future["sample_dates"])
    assert future["expected_value"] == 195.6
    assert future["expected_range"] == [160.0, 220.0]
    assert [
        row["loop_artifact"]["repeat_structure_mode"]
        for row in future["samples"]
    ] == ["legacy_full_2k_descent_labels", "paired_climb_descent_loops"]
    assert all(
        row["loop_artifact"]["corroborated_quality_descent_count"] == 3
        for row in future["samples"]
    )
    assert all(
        row["loop_artifact"]["official_load_absolute_delta"] <= 0.1
        for row in future["samples"]
    )
    assert future_expected["generic_training_load_expectation"] == {
        "expected_value": 82.5,
        "expected_range": [57.7, 111.4],
        "source": "generic_deterministic_duration_intensity_audit_only",
    }
    assert future["calibration_role"] == "load_expectation_only_not_digital_twin_calibration"


def test_matched_route_repeat_prior_rejects_noncomparable_duration(tmp_path):
    load_context(tmp_path)
    _write_exact_2k_sample(
        tmp_path, date(2026, 7, 9), 7051, 191.0, duration_min=91.2
    )
    _write_exact_2k_sample(
        tmp_path, date(2026, 8, 13), 7052, 200.1, duration_min=82.4
    )
    plan = _matched_2k_plan()
    plan["session"]["duration_min"] = 180

    baseline = _apply_matched_2k_load_baseline(
        tmp_path,
        date(2026, 8, 14),
        plan,
        _session_expectation(plan),
    )["action_matched_training_load_expectation"]

    assert baseline["status"] == "insufficient_matched_route_repeat_samples"
    assert baseline["sample_count"] == 0
    assert baseline["rejected_reason_counts"]["sample_duration_not_comparable"] == 2
    assert baseline["sample_duration_ratio_range"] == [0.8, 1.25]


def test_matched_route_repeat_prior_rejects_retroactive_action_annotation(tmp_path):
    load_context(tmp_path)
    sample_day = date(2026, 7, 9)
    _write_exact_2k_sample(tmp_path, sample_day, 7071, 191.0)
    feedback_path = tmp_path / "input" / f"feedback_{sample_day.isoformat()}.json"
    feedback = read_json(feedback_path, {})
    feedback["action_identity_recorded_at_local"] = "2026-08-13T15:37:04+08:00"
    write_json(feedback_path, feedback)
    plan = _matched_2k_plan()

    baseline = _apply_matched_2k_load_baseline(
        tmp_path,
        date(2026, 7, 10),
        plan,
        _session_expectation(plan),
    )["action_matched_training_load_expectation"]

    assert baseline["status"] == "insufficient_matched_route_repeat_samples"
    assert baseline["sample_count"] == 0
    assert baseline["rejected_reason_counts"] == {
        "action_identity_not_available_before_prediction_date": 1
    }


def test_matched_route_repeat_prior_rejects_invalid_annotation_timestamp(tmp_path):
    load_context(tmp_path)
    sample_day = date(2026, 7, 9)
    _write_exact_2k_sample(tmp_path, sample_day, 7081, 191.0)
    feedback_path = tmp_path / "input" / f"feedback_{sample_day.isoformat()}.json"
    feedback = read_json(feedback_path, {})
    feedback["action_identity_recorded_at_local"] = "not-a-timestamp"
    write_json(feedback_path, feedback)
    plan = _matched_2k_plan()

    baseline = _apply_matched_2k_load_baseline(
        tmp_path,
        date(2026, 7, 10),
        plan,
        _session_expectation(plan),
    )["action_matched_training_load_expectation"]

    assert baseline["status"] == "insufficient_matched_route_repeat_samples"
    assert baseline["sample_count"] == 0
    assert baseline["rejected_reason_counts"] == {
        "action_identity_recorded_at_invalid": 1
    }


def test_exact_2k_baseline_is_opt_in_and_fails_closed_for_bad_sensor_quality(tmp_path):
    load_context(tmp_path)
    _write_exact_2k_sample(tmp_path, date(2026, 7, 9), 7101, 191.0406)
    _write_exact_2k_sample(
        tmp_path,
        date(2026, 8, 13),
        7102,
        200.0730,
        external_hr=False,
    )
    target = date(2026, 8, 14)

    missing_plan = _matched_2k_plan(identity=False)
    missing = _apply_matched_2k_load_baseline(
        tmp_path, target, missing_plan, _session_expectation(missing_plan)
    )
    assert "action_matched_training_load_expectation" not in missing
    assert missing["expected_training_load"] == 82.5

    malformed_plan = _matched_2k_plan()
    malformed_plan["session"]["action_identity"]["route_key"] = "pure_quill"
    malformed = _apply_matched_2k_load_baseline(
        tmp_path, target, malformed_plan, _session_expectation(malformed_plan)
    )
    assert malformed["action_matched_training_load_expectation"]["status"] == (
        "missing_action_identity"
    )
    assert malformed["expected_training_load"] is None

    complete_plan = _matched_2k_plan()
    insufficient = _apply_matched_2k_load_baseline(
        tmp_path, target, complete_plan, _session_expectation(complete_plan)
    )
    baseline = insufficient["action_matched_training_load_expectation"]
    assert baseline["status"] == "insufficient_matched_route_repeat_samples"
    assert baseline["sample_count"] == 1
    assert baseline["sample_dates"] == ["2026-07-09"]
    assert any(
        "external_hr_provenance_not_confirmed" in row["reasons"]
        for row in baseline["rejected_candidates"]
        if row["date"] == "2026-08-13"
    )
    assert insufficient["expected_training_load"] is None


def test_exact_2k_baseline_rejects_missing_or_conflicting_loop_corroboration(tmp_path):
    load_context(tmp_path)
    _write_exact_2k_sample(tmp_path, date(2026, 7, 9), 7151, 191.0406)
    _write_exact_2k_sample(tmp_path, date(2026, 8, 13), 7152, 200.0730)
    target = date(2026, 8, 14)
    plan = _matched_2k_plan()
    loop_path = (
        tmp_path
        / "snapshots"
        / "activity_loop_load_2026-08-13_7152.json"
    )
    loop_path.unlink()

    missing = _apply_matched_2k_load_baseline(
        tmp_path, target, plan, _session_expectation(plan)
    )["action_matched_training_load_expectation"]
    missing_row = next(
        row for row in missing["rejected_candidates"] if row["date"] == "2026-08-13"
    )
    assert "dated_loop_artifact_missing" in missing_row["reasons"]
    assert missing_row["evidence"]["loop_artifact"]["source"] == (
        "dated_activity_loop_load_artifact"
    )
    assert missing["sample_count"] == 1

    write_json(
        loop_path,
        {
            "date": "2026-08-12",
            "activity_id": "wrong-activity",
            "official_activity_training_load": 205.0,
            "loops": [
                {
                    "loop": index,
                    "label": f"Loop {index}",
                    "lap_kinds": ["climb", "descent"],
                }
                for index in range(1, 3)
            ],
        },
    )
    conflicting = _apply_matched_2k_load_baseline(
        tmp_path, target, plan, _session_expectation(plan)
    )["action_matched_training_load_expectation"]
    conflict_row = next(
        row
        for row in conflicting["rejected_candidates"]
        if row["date"] == "2026-08-13"
    )
    assert {
        "loop_artifact_activity_id_mismatch",
        "loop_artifact_date_mismatch",
        "loop_artifact_official_load_mismatch",
        "loop_artifact_quality_descent_count_mismatch",
    }.issubset(conflict_row["reasons"])
    assert conflict_row["evidence"]["loop_artifact"]["official_load_tolerance"] == 0.1
    assert conflict_row["evidence"]["loop_artifact"]["route_authority"] == (
        "none_structure_corroboration_only"
    )
    assert conflicting["sample_count"] == 1
    assert conflicting["expected_value"] is None


def test_matched_route_repeat_prior_requires_independent_activity_dates(tmp_path):
    load_context(tmp_path)
    first_day = date(2026, 7, 9)
    second_day = date(2026, 7, 10)
    _write_exact_2k_sample(tmp_path, first_day, 7171, 191.0)
    _write_exact_2k_sample(tmp_path, second_day, 7172, 195.0)

    duplicate_activity_path = tmp_path / "activities" / "activity_7171_duplicate.json"
    duplicate_activity_path.write_bytes(
        (tmp_path / "activities" / "activity_7171.json").read_bytes()
    )
    same_day_activity_path = tmp_path / "activities" / "activity_7173.json"
    same_day_payload = read_json(
        tmp_path / "activities" / "activity_7172.json", {}
    )
    same_day_payload["activityId"] = 7173
    write_json(same_day_activity_path, same_day_payload)
    _append_index_row(
        tmp_path,
        "activity_gear_index.json",
        {
            "activity_id": "7173",
            "date": second_day.isoformat(),
            "category": "mtb",
            "gear_fetch_ok": True,
            "gear": [{"label": "Stumpjumper Expert MY25"}],
        },
    )
    _append_index_row(
        tmp_path,
        "activity_device_index.json",
        {
            "activity_id": "7173",
            "date": second_day.isoformat(),
            "category": "mtb",
            "device_fetch_ok": True,
            "external_hr_sensor": True,
            "hr_source_classification": "external_standard_metadata",
            "external_hr_battery_statuses": ["GOOD"],
        },
    )
    write_json(
        tmp_path / "snapshots" / "activity_loop_load_2026-07-10_7173.json",
        {
            "date": second_day.isoformat(),
            "activity_id": "7173",
            "official_activity_training_load": 195.0,
            "loops": [
                {
                    "loop": index,
                    "label": f"Loop {index}",
                    "lap_kinds": ["climb", "descent"],
                }
                for index in range(1, 4)
            ],
        },
    )
    feedback_path = tmp_path / "input" / "feedback_2026-07-10.json"
    feedback = read_json(feedback_path, {})
    feedback["entries"] = [
        {
            "activity_id": "7172",
            "action_identity": dict(MATCHED_2K_IDENTITY),
            "action_identity_recorded_at_local": "2026-07-10T12:00:00+08:00",
        },
        {
            "activity_id": "7173",
            "action_identity": dict(MATCHED_2K_IDENTITY),
            "action_identity_recorded_at_local": "2026-07-10T12:05:00+08:00",
        },
    ]
    feedback.pop("activity_id", None)
    feedback.pop("action_identity", None)
    feedback.pop("action_identity_recorded_at_local", None)
    write_json(feedback_path, feedback)

    plan = _matched_2k_plan()
    baseline = _apply_matched_2k_load_baseline(
        tmp_path,
        date(2026, 7, 11),
        plan,
        _session_expectation(plan),
    )["action_matched_training_load_expectation"]

    assert baseline["status"] == "matched_route_repeat_baseline"
    assert baseline["sample_count"] == 2
    assert baseline["sample_dates"] == ["2026-07-09", "2026-07-10"]
    assert baseline["rejected_reason_counts"]["duplicate_activity_ref"] == 1
    assert baseline["rejected_reason_counts"]["non_independent_sample_date"] == 2


def test_exact_2k_rejection_audit_ignores_unannotated_rows_and_bounds_details(tmp_path):
    load_context(tmp_path)
    start = date(2026, 6, 1)
    for offset in range(30):
        day = start + timedelta(days=offset)
        activity_id = 8000 + offset
        _write_exact_2k_sample(tmp_path, day, activity_id, 180.0 + offset)
        feedback_path = tmp_path / "input" / f"feedback_{day.isoformat()}.json"
        feedback = read_json(feedback_path, {})
        feedback["action_identity"]["route_key"] = "not_full_2k"
        write_json(feedback_path, feedback)
    for offset in range(30, 34):
        day = start + timedelta(days=offset)
        activity_id = 8000 + offset
        _write_exact_2k_sample(tmp_path, day, activity_id, 180.0 + offset)
        write_json(
            tmp_path / "input" / f"feedback_{day.isoformat()}.json",
            {"activity_id": str(activity_id)},
        )

    plan = _matched_2k_plan()
    baseline = _apply_matched_2k_load_baseline(
        tmp_path,
        date(2026, 7, 10),
        plan,
        _session_expectation(plan),
    )["action_matched_training_load_expectation"]

    assert baseline["status"] == "insufficient_matched_route_repeat_samples"
    assert baseline["sample_count"] == 0
    assert baseline["unannotated_mtb_rows_ignored"] == 4
    assert baseline["rejected_candidate_count"] == 30
    assert baseline["rejected_reason_counts"]["route_key_mismatch"] == 30
    assert baseline["rejected_candidates_limit"] == 25
    assert baseline["rejected_candidates_truncated"] == 5
    assert len(baseline["rejected_candidates"]) == 25
    assert baseline["rejected_candidates"][0]["date"] == "2026-06-06"
    assert baseline["rejected_candidates"][-1]["date"] == "2026-06-30"


def test_action_baseline_does_not_change_unrelated_mtb_session_type(tmp_path):
    load_context(tmp_path)
    _write_exact_2k_sample(tmp_path, date(2026, 7, 9), 7201, 191.0406)
    _write_exact_2k_sample(tmp_path, date(2026, 8, 13), 7202, 200.0730)
    plan = _matched_2k_plan(session_type="outdoor_mtb")

    generic = _session_expectation(plan)
    result = _apply_matched_2k_load_baseline(
        tmp_path, date(2026, 8, 14), plan, generic
    )

    assert result == generic
    assert result["expected_training_load"] == 82.5
    assert result["expected_training_load_range"] == [57.7, 111.4]
    assert "action_matched_training_load_expectation" not in result


def test_execution_risk_stress_load_overrides_nominal_action_prior():
    plan = _matched_2k_plan()
    expected = _session_expectation(plan)
    expected.update(
        {
            "expected_training_load": 195.6,
            "expected_training_load_range": [160.0, 220.0],
            "action_matched_training_load_expectation": {
                "status": "matched_route_repeat_baseline",
                "source": "historical_matched_route_repeat_load_prior",
                "expected_value": 195.6,
                "expected_range": [160.0, 220.0],
            },
        }
    )
    stress = _risk_adjusted_expected_session(
        expected,
        {
            "likely_harder_than_plan": True,
            "median_actual_training_load": 300.0,
            "p75_actual_training_load": 330.0,
            "median_actual_duration_min": 150.0,
            "median_actual_high_intensity_min": 20.0,
        },
    )

    assert stress is not None
    selected = _selected_training_load_expectation(stress)
    assert selected["selected_source"] == "execution_risk_stress_test"
    assert selected["expected_value"] == 300.0
    simulated = _simulate_activity_day({}, date(2026, 8, 14), stress)
    assert simulated["2026-08-14"]["training_load"] == 300.0


def test_existing_dated_prediction_is_immutable_before_activity_while_current_uses_match(tmp_path):
    _seed_history(tmp_path)
    target = date(2026, 5, 27)
    _write_wellness(tmp_path, target, good=True)
    _write_exact_2k_sample(tmp_path, date(2026, 5, 10), 7301, 191.0406)
    _write_exact_2k_sample(tmp_path, date(2026, 5, 15), 7302, 200.0730)
    dated_path = tmp_path / "snapshots" / f"predictive_session_{target.isoformat()}.json"
    write_json(
        dated_path,
        {
            "date": target.isoformat(),
            "generated_at": "2026-05-27T07:00:00+08:00",
            "prediction": {"expected_session": {"title": "Frozen original"}},
        },
    )
    original_bytes = dated_path.read_bytes()

    artifact = build_predictive_prescription(
        tmp_path,
        target,
        state={"date": target.isoformat()},
        plan=_matched_2k_plan(),
    )

    assert dated_path.read_bytes() == original_bytes
    assert artifact["artifacts"]["dated_write_status"] == "preserved_existing_immutable"
    current = read_json(tmp_path / "snapshots" / "predictive_session_plan.json", {})
    matched = current["prediction"]["expected_session"][
        "action_matched_training_load_expectation"
    ]
    assert matched["expected_value"] == 195.6
    assert matched["expected_range"] == [160.0, 220.0]
    assert current["prediction"]["simulated_features"]["today_training_load"] == 195.6


def test_schema_v3_contract_load_remains_authoritative_over_action_prior(tmp_path):
    target = _seed_history(tmp_path)
    _write_wellness(tmp_path, target, good=True)
    _write_exact_2k_sample(tmp_path, date(2026, 5, 10), 7351, 191.0)
    _write_exact_2k_sample(tmp_path, date(2026, 5, 15), 7352, 200.1)
    plan = _matched_2k_plan()
    plan["session"]["expected_result"] = {
        "technical": "Final execution matches the first.",
        "garmin_training_load": {"range": [100, 120]},
    }

    artifact = build_predictive_prescription(
        tmp_path, target, state={"date": target.isoformat()}, plan=plan
    )
    expected = artifact["prediction"]["expected_session"]

    assert expected["action_matched_training_load_expectation"]["expected_value"] == 195.6
    assert expected["selected_training_load_expectation"]["selected_source"] == (
        "schema_v3_contract.expected_result.garmin_training_load"
    )
    assert expected["expected_training_load"] == 110.0
    assert expected["expected_training_load_range"] == [100.0, 120.0]
    assert artifact["prediction"]["simulated_features"]["today_training_load"] == 110.0


def test_existing_empty_or_corrupt_dated_prediction_is_preserved(tmp_path):
    target = _seed_history(tmp_path)
    dated_path = tmp_path / "snapshots" / f"predictive_session_{target.isoformat()}.json"
    plan = {
        "session": {
            "title": "Current rolling plan",
            "type": "outdoor_bike_optional",
            "modality": "bike_indoor",
            "duration_min": 45,
            "intensity": "easy",
        }
    }

    write_json(dated_path, {})
    empty_bytes = dated_path.read_bytes()
    empty_artifact = build_predictive_prescription(
        tmp_path, target, state={"date": target.isoformat()}, plan=plan
    )
    assert dated_path.read_bytes() == empty_bytes
    assert empty_artifact["artifacts"]["dated_write_status"] == (
        "preserved_existing_invalid_or_empty"
    )
    assert empty_artifact["artifacts"]["existing_dated_integrity"] == (
        "invalid_or_empty"
    )

    corrupt_bytes = b"{not-json"
    dated_path.write_bytes(corrupt_bytes)
    corrupt_artifact = build_predictive_prescription(
        tmp_path, target, state={"date": target.isoformat()}, plan=plan
    )
    assert dated_path.read_bytes() == corrupt_bytes
    assert corrupt_artifact["artifacts"]["dated_write_status"] == (
        "preserved_existing_corrupt"
    )
    assert corrupt_artifact["artifacts"]["existing_dated_integrity"] == "corrupt"
