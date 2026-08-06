from datetime import date, datetime, timedelta, timezone

from coach_sync.body_battery_model import build_body_battery_dataset, build_body_battery_model
from coach_sync.io import write_json


def _write_model_wellness(
    root,
    day: date,
    *,
    good: bool = True,
    avg_stress: int | None = None,
    partial_wear: bool = False,
    include_sleep_stress: bool = True,
) -> None:
    average = avg_stress if avg_stress is not None else (18 if good else 40)
    start = datetime(
        day.year,
        day.month,
        day.day,
        0,
        0,
        tzinfo=timezone(timedelta(hours=8)),
    )
    sleep_dto = {
        "calendarDate": day.isoformat(),
        "sleepTimeSeconds": 27000 if good else 16000,
        "awakeSleepSeconds": 1200,
        "sleepScores": {
            "overall": {
                "value": 85 if good else 58,
                "qualifierKey": "GOOD" if good else "POOR",
            }
        },
    }
    if include_sleep_stress:
        sleep_dto["avgSleepStress"] = 8 if good else 24
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
                        "bodyBatteryAtWakeTime": 82 if good else 55,
                        "bodyBatteryDrainedValue": 20 if good else 55,
                        "averageStressLevel": average,
                        "restingHeartRate": 44 if good else 49,
                        "moderateIntensityMinutes": 20 if good else 120,
                        "vigorousIntensityMinutes": 0,
                    },
                },
                {
                    "label": "get_sleep_data",
                    "ok": True,
                    "data": {
                        "avgOvernightHrv": 56 if good else 43,
                        "hrvStatus": "BALANCED",
                        "dailySleepDTO": sleep_dto,
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
                                -1 if partial_wear and 2 <= index < 12 else average,
                            ]
                            for index in range(361)
                        ],
                    },
                },
            ],
        },
    )


def test_body_battery_decision_tree_trains_on_personal_rows(tmp_path):
    start = date(2026, 4, 1)
    for offset in range(16):
        day = start + timedelta(days=offset)
        good = offset % 2 == 0
        _write_model_wellness(tmp_path, day, good=good)

    report = build_body_battery_model(tmp_path, "2026-04-16")

    assert report["samples"] == 16
    assert report["positive_samples"] == 8
    assert report["tree"]["type"] in {"node", "leaf"}
    assert report["latest_prediction"] is not None
    assert (tmp_path / "snapshots" / "body_battery_model_report.json").exists()


def test_body_battery_dataset_excludes_exact_artifact_guarded_low_stress(tmp_path):
    day = date(2026, 7, 19)
    _write_model_wellness(tmp_path, day, avg_stress=18)
    write_json(
        tmp_path / "snapshots" / f"wearable_coverage_{day.isoformat()}.json",
        {
            "date": day.isoformat(),
            "decision_use": {"low_stress_positive_reward_eligible": False},
        },
    )

    dataset = build_body_battery_dataset(tmp_path, day)

    assert dataset == []


def test_body_battery_dataset_never_promotes_partial_low_stress_but_keeps_high(tmp_path):
    low_day = date(2026, 7, 18)
    high_day = date(2026, 7, 19)
    _write_model_wellness(tmp_path, low_day, avg_stress=18, partial_wear=True)
    _write_model_wellness(tmp_path, high_day, avg_stress=48, partial_wear=True)
    for day in (low_day, high_day):
        write_json(
            tmp_path / "snapshots" / f"wearable_coverage_{day.isoformat()}.json",
            {
                "date": day.isoformat(),
                "decision_use": {"low_stress_positive_reward_eligible": True},
            },
        )

    dataset = build_body_battery_dataset(tmp_path, high_day)

    assert [row["date"] for row in dataset] == [high_day.isoformat()]
    assert dataset[0]["features"]["avg_stress"] == 48


def test_body_battery_dataset_does_not_synthesize_missing_sleep_stress(tmp_path):
    day = date(2026, 7, 19)
    _write_model_wellness(
        tmp_path,
        day,
        avg_stress=18,
        include_sleep_stress=False,
    )

    dataset = build_body_battery_dataset(tmp_path, day)

    assert dataset == []
