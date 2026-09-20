import json
from datetime import date, timedelta

from coach_sync.cli import main
from coach_sync.io import write_json
from coach_sync.training_predictor import (
    build_training_predictor,
    build_training_response_dataset,
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


def _seed_training_response_history(root, days: int = 26) -> date:
    start = date(2026, 4, 1)
    for offset in range(days):
        day = start + timedelta(days=offset)
        previous_day_was_high_load = offset > 0 and (offset - 1) % 4 == 0
        _write_wellness(root, day, good=not previous_day_was_high_load)
        _write_activity(root, day, offset + 1, high_load=offset % 4 == 0)
    return start + timedelta(days=days - 1)


def test_training_predictor_builds_dataset_model_and_prediction(tmp_path):
    target = _seed_training_response_history(tmp_path)

    report = build_training_predictor(tmp_path, target, max_depth=3, min_leaf=3)

    assert report["model_type"] == "pure_python_bounded_decision_tree_classifier"
    assert report["samples"] == 25
    assert report["positive_samples"] > 0
    assert report["negative_samples"] > 0
    assert report["validation"]["method"] == "chronological_holdout"
    assert report["today_prediction"]["status"] in {"ok", "caution"}
    assert report["today_prediction"]["prediction"]["leaf_samples"] > 0
    assert (tmp_path / "snapshots" / "training_response_dataset.json").exists()
    assert (tmp_path / "snapshots" / "training_response_model_report.json").exists()
    assert (tmp_path / "snapshots" / "training_prediction_today.json").exists()


def test_training_response_dataset_is_bounded_to_recent_rows(tmp_path):
    target = _seed_training_response_history(tmp_path)

    dataset = build_training_response_dataset(tmp_path, target, max_rows=10)

    assert dataset["samples"] == 10
    assert dataset["date_span"]["start"] == "2026-04-16"
    assert dataset["date_span"]["end"] == "2026-04-25"


def test_training_predictor_cli_command(tmp_path, capsys):
    target = _seed_training_response_history(tmp_path)

    code = main(["training-predictor", "--root", str(tmp_path), "--date", target.isoformat()])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert code == 0
    assert payload["samples"] == 25
    assert payload["artifacts"]["today_prediction"] == "snapshots/training_prediction_today.json"
