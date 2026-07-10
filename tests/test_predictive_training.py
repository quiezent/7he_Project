from datetime import date, timedelta

from coach_sync.context import load_context
from coach_sync.io import read_json, write_json
from coach_sync.planning import SESSION_CONTRACT_FIELDS
from coach_sync.predictive_training import (
    _session_expectation,
    build_predictive_prescription,
    build_predictive_review,
    build_predictive_training,
)


def _write_wellness(root, day: date, good: bool) -> None:
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
    }
    assert "execution_risk_stress_test" in review["comparison"]


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
