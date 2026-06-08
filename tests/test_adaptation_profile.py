from coach_sync.adaptation_profile import build_adaptation_profile
from coach_sync.io import write_json


def _activity(activity_id, day, type_key, load, duration=3600, p20=None, vo2=None):
    payload = {
        "activityId": activity_id,
        "activityName": "Training",
        "activityType": {"typeKey": type_key},
        "startTimeLocal": f"{day} 08:00:00",
        "duration": duration,
        "activityTrainingLoad": load,
        "averageHR": 130,
        "maxHR": 150,
        "aerobicTrainingEffect": 3.0,
        "hrTimeInZone_1": 600,
        "hrTimeInZone_2": 1200,
        "hrTimeInZone_3": 900,
    }
    if p20 is not None:
        payload["maxAvgPower_1200"] = p20
        payload["maxAvgPower_600"] = p20 + 10
        payload["avgPower"] = p20 - 25
        payload["normPower"] = p20 - 15
    if vo2 is not None:
        payload["vO2MaxValue"] = vo2
    return payload


def test_adaptation_profile_writes_monthly_personal_rules_and_flags(tmp_path):
    write_json(
        tmp_path / "activities" / "bike_good.json",
        _activity(1, "2026-04-05", "indoor_cycling", 90, p20=180, vo2=49),
    )
    write_json(
        tmp_path / "activities" / "bike_lower.json",
        _activity(2, "2026-05-05", "indoor_cycling", 45, p20=150, vo2=47),
    )
    write_json(
        tmp_path / "activities" / "elliptical.json",
        _activity(3, "2026-05-06", "elliptical", 120),
    )
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
                        "bodyBatteryAtWakeTime": 80,
                        "restingHeartRate": 45,
                        "averageStressLevel": 20,
                    },
                }
            ],
        },
    )

    profile = build_adaptation_profile(tmp_path, "2026-05-26", days=60)

    assert profile["analysis_type"] == "one_year_n_of_1_adaptation_profile"
    assert profile["window"]["mode"] == "fixed_day_window"
    assert profile["monthly"][0]["best_p20_w"] == 180
    assert profile["monthly"][1]["best_p20_w"] == 150
    assert any(flag["type"] == "cycling_power_drop" for flag in profile["detraining_flags"])
    assert profile["personal_rules"]
    assert (tmp_path / "snapshots" / "adaptation_profile.json").exists()
    assert (tmp_path / "snapshots" / "adaptation_profile.txt").exists()


def test_adaptation_profile_supports_full_available_range(tmp_path):
    write_json(
        tmp_path / "activities" / "old.json",
        _activity(1, "2024-01-02", "indoor_cycling", 80, p20=150),
    )
    write_json(
        tmp_path / "activities" / "new.json",
        _activity(2, "2026-05-05", "indoor_cycling", 90, p20=160),
    )

    profile = build_adaptation_profile(tmp_path, "2026-05-26", days=0)

    assert profile["analysis_type"] == "full_range_n_of_1_adaptation_profile"
    assert profile["window"]["mode"] == "full_available_range"
    assert profile["window"]["start"] == "2024-01-02"
