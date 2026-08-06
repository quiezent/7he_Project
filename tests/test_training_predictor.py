import json
from datetime import date, datetime, timedelta, timezone

from coach_sync.cli import main
from coach_sync.io import read_json, write_json
from coach_sync.body_battery_model import build_body_battery_dataset
from coach_sync.training_predictor import (
    _features_for_day,
    _target_score,
    build_training_predictor,
    build_training_response_dataset,
)


def _write_wellness(root, day: date, good: bool) -> None:
    start = datetime(
        day.year,
        day.month,
        day.day,
        0,
        0,
        tzinfo=timezone(timedelta(hours=8)),
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
                            [
                                int((start + timedelta(minutes=index * 3)).timestamp() * 1000),
                                18 if good else 48,
                            ]
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


def test_partial_wear_low_stress_is_excluded_but_high_stress_remains_usable():
    day = date(2026, 7, 19)
    base = {
        "date": day.isoformat(),
        "sleep_score": 80,
        "sleep_hours": 7.5,
        "body_battery_wake": 75,
        "hrv_status": "BALANCED",
        "resting_hr": 48,
        "avg_stress": 16,
        "sleep_stress": 8,
        "all_day_stress_low_positive_reward_eligible": True,
    }
    complete_target = _target_score(base, [base], day)
    complete_features = _features_for_day(
        day,
        {day.isoformat(): base},
        [base],
        {},
    )
    partial = {**base, "all_day_stress_low_positive_reward_eligible": False}
    partial_target = _target_score(partial, [partial], day)
    partial_features = _features_for_day(
        day,
        {day.isoformat(): partial},
        [partial],
        {},
    )
    high_partial = {**partial, "avg_stress": 48}
    high_target = _target_score(high_partial, [high_partial], day)
    high_features = _features_for_day(
        day,
        {day.isoformat(): high_partial},
        [high_partial],
        {},
    )
    unknown = {key: value for key, value in base.items() if key != "all_day_stress_low_positive_reward_eligible"}
    unknown_target = _target_score(unknown, [unknown], day)
    unknown_features = _features_for_day(
        day,
        {day.isoformat(): unknown},
        [unknown],
        {},
    )

    assert partial_target["next_day_response_score"] == (
        complete_target["next_day_response_score"] - 3
    )
    assert complete_features is not None
    assert complete_features["today_avg_stress"] == 16.0
    assert complete_features["today_sleep_stress_proxy"] == 8.0
    assert partial_features is None
    assert unknown_target["next_day_response_score"] == partial_target[
        "next_day_response_score"
    ]
    assert unknown_features is None
    assert high_target["next_day_response_score"] < partial_target[
        "next_day_response_score"
    ]
    assert high_features is not None
    assert high_features["today_avg_stress"] == 48.0


def test_missing_sleep_stress_does_not_create_synthetic_predictor_feature():
    day = date(2026, 7, 19)
    row = {
        "date": day.isoformat(),
        "sleep_score": 80,
        "sleep_hours": 7.5,
        "body_battery_wake": 75,
        "hrv_status": "BALANCED",
        "resting_hr": 48,
        "avg_stress": 16,
        "sleep_stress": None,
        "all_day_stress_low_positive_reward_eligible": True,
    }

    features = _features_for_day(day, {day.isoformat(): row}, [row], {})

    assert features is None


def test_partial_low_stress_audit_marker_does_not_make_sparse_target_usable():
    day = date(2026, 7, 19)
    row = {
        "date": day.isoformat(),
        "sleep_score": 80,
        "avg_stress": 16,
        "all_day_stress_low_positive_reward_eligible": False,
    }

    target = _target_score(row, [row], day)

    assert target["signals"] == ["sleep_score"]
    assert target["signal_count"] == 1
    assert target["usable"] is False
    assert target["audit_markers"] == [
        "avg_stress_low_positive_withheld_for_partial_wear"
    ]


def test_exact_dated_wear_artifact_false_overrides_raw_true_and_beats_alias(tmp_path):
    target = _seed_training_response_history(tmp_path, days=8)
    guarded_day = date(2026, 4, 3)
    write_json(
        tmp_path / "snapshots" / f"wearable_coverage_{guarded_day.isoformat()}.json",
        {
            "date": guarded_day.isoformat(),
            "decision_use": {"low_stress_positive_reward_eligible": False},
        },
    )
    write_json(
        tmp_path / "snapshots" / "wearable_coverage.json",
        {
            "date": guarded_day.isoformat(),
            "decision_use": {"low_stress_positive_reward_eligible": True},
        },
    )

    dataset = build_training_response_dataset(tmp_path, target)

    rows_by_date = {row["date"]: row for row in dataset["rows"]}
    assert guarded_day.isoformat() not in rows_by_date
    prior_row = rows_by_date[(guarded_day - timedelta(days=1)).isoformat()]
    assert "avg_stress_low_positive_withheld_for_partial_wear" in prior_row["target"][
        "audit_markers"
    ]


def test_same_date_wear_alias_false_is_used_only_when_dated_artifact_is_absent(tmp_path):
    target = _seed_training_response_history(tmp_path, days=8)
    guarded_day = date(2026, 4, 4)
    write_json(
        tmp_path / "snapshots" / "wearable_coverage.json",
        {
            "date": guarded_day.isoformat(),
            "decision_use": {"low_stress_positive_reward_eligible": False},
        },
    )

    dataset = build_training_response_dataset(tmp_path, target)

    assert guarded_day.isoformat() not in {row["date"] for row in dataset["rows"]}


def test_today_prediction_excludes_exact_artifact_guarded_low_stress(tmp_path):
    target = _seed_training_response_history(tmp_path, days=8)
    write_json(
        tmp_path / "snapshots" / f"wearable_coverage_{target.isoformat()}.json",
        {
            "date": target.isoformat(),
            "decision_use": {"low_stress_positive_reward_eligible": False},
        },
    )

    report = build_training_predictor(tmp_path, target, max_depth=3, min_leaf=2)

    assert report["today_prediction"]["status"] == "unavailable"
    assert "Could not build feature vector" in report["today_prediction"]["warnings"][0]


def test_wear_artifact_true_never_promotes_raw_partial_low_stress(tmp_path):
    target = _seed_training_response_history(tmp_path, days=8)
    guarded_day = date(2026, 4, 3)
    path = tmp_path / "snapshots" / f"garmin_wellness_{guarded_day.isoformat()}.json"
    payload = read_json(path, {})
    all_day = next(
        item for item in payload["payloads"] if item["label"] == "get_all_day_stress"
    )
    for index in range(2, 12):
        all_day["data"]["stressValuesArray"][index][1] = -1
    write_json(path, payload)
    write_json(
        tmp_path / "snapshots" / f"wearable_coverage_{guarded_day.isoformat()}.json",
        {
            "date": guarded_day.isoformat(),
            "decision_use": {"low_stress_positive_reward_eligible": True},
        },
    )

    dataset = build_training_response_dataset(tmp_path, target)

    assert guarded_day.isoformat() not in {row["date"] for row in dataset["rows"]}


def test_body_battery_dataset_excludes_low_stress_partial_wear_row(tmp_path):
    day = date(2026, 7, 19)
    _write_wellness(tmp_path, day, good=True)
    path = tmp_path / "snapshots" / f"garmin_wellness_{day.isoformat()}.json"
    payload = read_json(path, {})
    start = datetime(2026, 7, 19, 8, 0, tzinfo=timezone(timedelta(hours=8)))
    all_day = next(
        item for item in payload["payloads"] if item["label"] == "get_all_day_stress"
    )
    all_day["data"]["stressValuesArray"] = [
        [
            int((start + timedelta(minutes=index * 3)).timestamp() * 1000),
            -1 if 2 <= index < 12 else 18,
        ]
        for index in range(20)
    ]
    write_json(path, payload)

    dataset = build_body_battery_dataset(tmp_path, day)

    assert not any(row["date"] == day.isoformat() for row in dataset)
