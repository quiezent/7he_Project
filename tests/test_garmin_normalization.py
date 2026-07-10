from coach_sync.activity_profile import build_activity_profile
from coach_sync.evidence import load_latest_training_status, load_latest_wellness
from coach_sync.io import write_json
from coach_sync.load_model import build_modality_load_rollups
from coach_sync.readiness import build_readiness
from coach_sync.training_status import build_training_status_current
from coach_sync.wellness import build_wellness_trends


def _write_training_status(root, day: str) -> None:
    write_json(
        root / "snapshots" / f"garmin_training_status_{day}.json",
        {
            "date": day,
            "payload": {
                "ok": True,
                "data": {
                    "mostRecentTrainingStatus": {
                        "latestTrainingStatusData": {
                            "dev": {
                                "primaryTrainingDevice": True,
                                "trainingStatusFeedbackPhrase": "MAINTAINING_2",
                                "acuteTrainingLoadDTO": {
                                    "acwrStatus": "OPTIMAL",
                                    "dailyAcuteChronicWorkloadRatio": 0.8,
                                },
                            }
                        }
                    }
                },
            },
        },
    )


def test_failed_dated_payloads_do_not_satisfy_wellness_or_training_status_freshness(tmp_path):
    write_json(
        tmp_path / "snapshots" / "garmin_wellness_2026-04-29.json",
        {
            "date": "2026-04-29",
            "payloads": [{"label": "get_stats", "ok": True, "data": {"restingHeartRate": 48}}],
        },
    )
    write_json(
        tmp_path / "snapshots" / "garmin_wellness_2026-04-30.json",
        {
            "date": "2026-04-30",
            "payloads": [{"label": "get_stats", "ok": False, "error": "timeout"}],
        },
    )
    _write_training_status(tmp_path, "2026-04-29")
    write_json(
        tmp_path / "snapshots" / "garmin_training_status_2026-04-30.json",
        {
            "date": "2026-04-30",
            "payload": {"ok": False, "error": "timeout"},
        },
    )

    wellness_date, _ = load_latest_wellness(tmp_path, "2026-04-30")
    status_date, _ = load_latest_training_status(tmp_path, "2026-04-30")

    assert wellness_date is not None and wellness_date.isoformat() == "2026-04-29"
    assert status_date is not None and status_date.isoformat() == "2026-04-29"


def test_wellness_normalizes_nested_sleep_body_battery_and_hrv(tmp_path):
    write_json(
        tmp_path / "snapshots" / "garmin_wellness_2026-04-30.json",
        {
            "date": "2026-04-30",
            "payloads": [
                {
                    "label": "get_stats",
                    "ok": True,
                    "data": {
                        "calendarDate": "2026-04-30",
                        "bodyBatteryMostRecentValue": 33,
                        "bodyBatteryAtWakeTime": 65,
                        "averageStressLevel": 33,
                        "restingHeartRate": 46,
                    },
                },
                {
                    "label": "get_sleep_data",
                    "ok": True,
                    "data": {
                        "avgOvernightHrv": 51,
                        "hrvStatus": "BALANCED",
                        "dailySleepDTO": {
                            "calendarDate": "2026-04-30",
                            "sleepTimeSeconds": 18000,
                            "awakeSleepSeconds": 1800,
                            "sleepScores": {"overall": {"value": 64, "qualifierKey": "FAIR"}},
                        },
                    },
                },
            ],
        },
    )
    _write_training_status(tmp_path, "2026-04-30")

    trends = build_wellness_trends(tmp_path, "2026-04-30")
    readiness = build_readiness(tmp_path, "2026-04-30")

    assert trends["latest"]["sleep_score"] == 64
    assert trends["latest"]["body_battery_current"] == 33
    assert trends["latest"]["overnight_hrv"] == 51
    assert readiness["readiness_score"] == 73.0
    assert (tmp_path / "snapshots" / "readiness_features_2026-04-30.json").exists()


def test_wellness_prefers_dedicated_body_battery_endpoint(tmp_path):
    write_json(
        tmp_path / "snapshots" / "garmin_wellness_2026-05-06.json",
        {
            "date": "2026-05-06",
            "payloads": [
                {
                    "label": "get_stats",
                    "ok": True,
                    "data": {
                        "calendarDate": "2026-05-06",
                        "bodyBatteryMostRecentValue": 33,
                        "bodyBatteryChargedValue": 28,
                        "bodyBatteryDrainedValue": 0,
                        "averageStressLevel": 11,
                    },
                },
                {
                    "label": "get_body_battery",
                    "ok": True,
                    "data": [
                        {
                            "date": "2026-05-06",
                            "charged": 63,
                            "drained": 18,
                            "startTimestampLocal": "2026-05-06T00:00:00.0",
                            "endTimestampLocal": "2026-05-06T10:05:00.0",
                            "bodyBatteryValuesArray": [
                                [1778019840000, 68],
                                [1778021280000, 67],
                                [1778031360000, 50],
                            ],
                        }
                    ],
                },
            ],
        },
    )

    trends = build_wellness_trends(tmp_path, "2026-05-06")

    assert trends["latest"]["body_battery_current"] == 50
    assert trends["latest"]["body_battery_charge"] == 63
    assert trends["latest"]["body_battery_drain"] == 18
    assert trends["latest"]["body_battery_source"] == "get_body_battery"
    assert trends["latest"]["body_battery_end_time_local"] == "2026-05-06T10:05:00.0"


def test_activity_load_uses_activity_training_load_and_excludes_motorsport(tmp_path):
    write_json(
        tmp_path / "activities" / "ride.json",
        {
            "activityId": 1,
            "activityName": "Base",
            "activityType": {"typeKey": "indoor_cycling"},
            "startTimeLocal": "2026-04-30 08:00:00",
            "duration": 3600,
            "distance": 20000,
            "activityTrainingLoad": 75.5,
            "averageHR": 130,
        },
    )
    write_json(
        tmp_path / "activities" / "car.json",
        {
            "activityId": 2,
            "activityName": "Race",
            "activityType": {"typeKey": "auto_racing"},
            "startTimeLocal": "2026-04-30 10:00:00",
            "duration": 3600,
            "activityTrainingLoad": 999,
            "averageHR": 130,
        },
    )

    profile = build_activity_profile(tmp_path, "2026-04-30")
    rollups = build_modality_load_rollups(tmp_path, "2026-04-30")

    assert profile["categories"]["bike_indoor"] == 1
    assert profile["categories"]["motorsport"] == 1
    assert rollups["windows"]["last_7_days"]["bike_indoor"]["training_load"] == 75.5
    assert rollups["windows"]["last_7_days"]["motorsport"]["excluded_sessions"] == 1


def test_training_status_normalizes_acwr_vo2_and_load_focus(tmp_path):
    write_json(
        tmp_path / "snapshots" / "garmin_training_status_2026-04-30.json",
        {
            "date": "2026-04-30",
            "payload": {
                "ok": True,
                "data": {
                    "mostRecentTrainingStatus": {
                        "latestTrainingStatusData": {
                            "dev": {
                                "primaryTrainingDevice": True,
                                "trainingStatusFeedbackPhrase": "MAINTAINING_2",
                                "acuteTrainingLoadDTO": {
                                    "acwrStatus": "OPTIMAL",
                                    "dailyAcuteChronicWorkloadRatio": 0.8,
                                    "dailyTrainingLoadAcute": 357,
                                    "dailyTrainingLoadChronic": 402,
                                },
                            }
                        }
                    },
                    "mostRecentTrainingLoadBalance": {
                        "metricsTrainingLoadBalanceDTOMap": {
                            "dev": {
                                "primaryTrainingDevice": True,
                                "monthlyLoadAnaerobic": 98,
                                "monthlyLoadAnaerobicTargetMin": 133,
                                "trainingBalanceFeedbackPhrase": "ANAEROBIC_SHORTAGE",
                            }
                        }
                    },
                    "mostRecentVO2Max": {"cycling": {"vo2MaxValue": 45}},
                },
            },
        },
    )

    status = build_training_status_current(tmp_path, "2026-04-30")

    assert status["acute_chronic"]["ratio"] == 0.8
    assert status["vo2max"]["cycling_value"] == 45.0
    assert status["flags"][0]["type"] == "load_focus_gap"
