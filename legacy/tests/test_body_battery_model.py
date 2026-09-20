from datetime import date, timedelta

from coach_sync.body_battery_model import build_body_battery_model
from coach_sync.io import write_json


def test_body_battery_decision_tree_trains_on_personal_rows(tmp_path):
    start = date(2026, 4, 1)
    for offset in range(16):
        day = start + timedelta(days=offset)
        good = offset % 2 == 0
        write_json(
            tmp_path / "snapshots" / f"garmin_wellness_{day.isoformat()}.json",
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
                            "averageStressLevel": 18 if good else 40,
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
                            "dailySleepDTO": {
                                "calendarDate": day.isoformat(),
                                "sleepTimeSeconds": 27000 if good else 16000,
                                "awakeSleepSeconds": 1200,
                                "avgSleepStress": 8 if good else 24,
                                "sleepScores": {
                                    "overall": {
                                        "value": 85 if good else 58,
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

    report = build_body_battery_model(tmp_path, "2026-04-16")

    assert report["samples"] == 16
    assert report["positive_samples"] == 8
    assert report["tree"]["type"] in {"node", "leaf"}
    assert report["latest_prediction"] is not None
    assert (tmp_path / "snapshots" / "body_battery_model_report.json").exists()
