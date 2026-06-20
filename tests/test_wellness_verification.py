from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from coach_sync.io import write_json
from coach_sync.readiness import build_readiness
from coach_sync.wellness_verification import build_wellness_verification


KL = ZoneInfo("Asia/Kuala_Lumpur")


def local_ms(value: str) -> int:
    local = datetime.fromisoformat(value).replace(tzinfo=KL)
    return int(local.astimezone(timezone.utc).timestamp() * 1000)


def write_interrupted_sleep_wellness(tmp_path):
    write_json(
        tmp_path / "snapshots" / "garmin_wellness_2026-05-29.json",
        {
            "date": "2026-05-29",
            "payloads": [
                {
                    "label": "get_stats",
                    "ok": True,
                    "data": {
                        "calendarDate": "2026-05-29",
                        "bodyBatteryAtWakeTime": 63,
                        "bodyBatteryMostRecentValue": 52,
                        "bodyBatteryHighestValue": 72,
                        "bodyBatteryLowestValue": 5,
                        "bodyBatteryChargedValue": 72,
                        "bodyBatteryDrainedValue": 25,
                    },
                },
                {
                    "label": "get_sleep_data",
                    "ok": True,
                    "data": {
                        "avgOvernightHrv": 50,
                        "hrvStatus": "BALANCED",
                        "dailySleepDTO": {
                            "calendarDate": "2026-05-29",
                            "sleepStartTimestampGMT": local_ms("2026-05-29T01:10:00"),
                            "sleepEndTimestampGMT": local_ms("2026-05-29T06:20:00"),
                            "sleepTimeSeconds": 18180,
                            "awakeSleepSeconds": 420,
                            "napTimeSeconds": 0,
                            "sleepScores": {"overall": {"value": 75, "qualifierKey": "FAIR"}},
                        },
                    },
                },
                {
                    "label": "get_body_battery",
                    "ok": True,
                    "data": [
                        {
                            "date": "2026-05-29",
                            "charged": 72,
                            "drained": 25,
                            "bodyBatteryValuesArray": [
                                [local_ms("2026-05-29T00:00:00"), 5],
                                [local_ms("2026-05-29T06:36:00"), 64],
                                [local_ms("2026-05-29T10:09:00"), 72],
                                [local_ms("2026-05-29T12:27:00"), 52],
                            ],
                        }
                    ],
                },
            ],
        },
    )


def write_training_status(tmp_path):
    write_json(
        tmp_path / "snapshots" / "garmin_training_status_2026-05-29.json",
        {
            "date": "2026-05-29",
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


def test_wellness_verification_detects_post_wake_body_battery_recharge(tmp_path):
    write_interrupted_sleep_wellness(tmp_path)

    report = build_wellness_verification(tmp_path, "2026-05-29")
    interpretation = report["body_battery_interpretation"]

    assert report["verification_status"] == "garmin_sleep_summary_understates_later_recovery"
    assert interpretation["post_wake_recharge"]["detected"] is True
    assert interpretation["post_wake_recharge"]["delta_from_reported_wake"] == 9.0
    assert interpretation["recommended_morning_anchor"] == 72.0
    assert interpretation["recommended_anchor_source"] == "post_wake_recharge_peak"
    assert (tmp_path / "snapshots" / "wellness_verification.txt").exists()


def test_readiness_uses_verified_morning_anchor_instead_of_blind_wake_value(tmp_path):
    write_interrupted_sleep_wellness(tmp_path)
    write_training_status(tmp_path)

    readiness = build_readiness(tmp_path, "2026-05-29")

    assert readiness["readiness_score"] == 73.0
    assert any(reason["type"] == "body_battery_verified_recharge" for reason in readiness["reasons"])
    assert not any(
        reason["type"] == "body_battery_wake" and reason["severity"] == "yellow"
        for reason in readiness["reasons"]
    )
    verification = readiness["evidence"]["wellness_verification"]
    assert verification["recommended_morning_anchor"] == 72.0
    assert verification["post_wake_recharge"] is True
