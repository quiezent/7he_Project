from datetime import date, timedelta

from coach_sync.context import load_context
from coach_sync.io import write_json
from coach_sync.predictive_backtest import (
    _calibration_summary,
    _coverage,
    build_predictive_backtest,
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
                        "bodyBatteryAtWakeTime": 82 if good else 48,
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


def _seed_history(root) -> date:
    load_context(root)
    start = date(2026, 4, 1)
    for offset in range(32):
        day = start + timedelta(days=offset)
        previous_day_was_high_load = offset > 0 and (offset - 1) % 4 == 0
        _write_wellness(root, day, good=not previous_day_was_high_load)
        if offset < 31:
            _write_activity(root, day, offset + 1, high_load=offset % 4 == 0)
    return start + timedelta(days=30)


def test_predictive_backtest_replays_pre_session_then_reviews_actuals(tmp_path):
    target = _seed_history(tmp_path)

    artifact = build_predictive_backtest(tmp_path, [target.isoformat()])

    row = artifact["rows"][0]
    assert artifact["coverage"]["dates"] == 1
    assert row["pre_session_context"]["activity_cutoff"] == (target - timedelta(days=1)).isoformat()
    assert row["actual_session"]["sessions"] == 1
    assert row["next_day_recovery"]["status"] == "available"
    assert "execution_risk_stress_test" in row["comparison"]
    assert row["comparison"]["adherence_status"] in {
        "matched_expected_load",
        "harder_than_predicted",
        "easier_than_predicted",
        "missed_prescribed_session",
        "trained_on_planned_rest",
    }
    assert (tmp_path / "snapshots" / "predictive_backtest_10_dates.json").exists()
    assert (tmp_path / "snapshots" / "predictive_backtest_10_dates.txt").exists()


def test_backtest_separates_clean_nominal_from_out_of_policy_response_observation():
    clean = {
        "comparison": {
            "adherence_status": "matched_expected_load",
            "response_delta": 10,
            "physiology_calibration_eligible": True,
            "calibration_eligible": True,
            "learning_disposition": {
                "nominal_contract_validation": {
                    "status": "calibrated",
                    "eligible": True,
                    "weight": 1.0,
                },
                "delivered_action_response": {
                    "status": "calibrated",
                    "eligible": True,
                    "weight": 1.0,
                    "policy_status": "in_policy",
                },
                "execution_boundary_learning": {"eligible": False},
                "safety_adherence_learning": {"eligible": False},
            },
        }
    }
    overrun = {
        "comparison": {
            "adherence_status": "matched_expected_load",
            "response_delta": -30,
            "physiology_calibration_eligible": True,
            "calibration_eligible": False,
            "learning_disposition": {
                "nominal_contract_validation": {
                    "status": "rejected_unsafe_stop_rule_continued",
                    "eligible": False,
                    "weight": 0.0,
                },
                "delivered_action_response": {
                    "status": "observed_model_miss",
                    "eligible": True,
                    "weight": 0.35,
                    "policy_status": "out_of_policy_stop_rule_override",
                },
                "execution_boundary_learning": {"eligible": True},
                "safety_adherence_learning": {"eligible": True},
            },
        }
    }

    rows = [clean, overrun]
    coverage = _coverage(rows)
    summary = _calibration_summary(rows)

    assert coverage["physiology_calibratable_dates"] == 2
    assert coverage["contract_calibratable_dates"] == 1
    assert coverage["nominal_contract_validation_dates"] == 1
    assert coverage["delivered_action_response_dates"] == 2
    assert coverage["out_of_policy_delivered_action_response_dates"] == 1
    assert coverage["execution_boundary_learning_dates"] == 1
    assert coverage["safety_adherence_learning_dates"] == 1
    assert summary["mean_abs_response_error_matched_load"] == 20.0
    assert summary["mean_abs_response_error_clean_nominal"] == 10.0
    assert summary["mean_abs_response_error_delivered_action"] == 20.0
    assert summary["mean_abs_response_error_out_of_policy_delivered_action"] == 30.0
    assert summary["clean_nominal_count"] == 1
    assert summary["out_of_policy_delivered_action_count"] == 1
    assert summary["out_of_policy_excluded_from_clean_nominal_metrics"] is True
