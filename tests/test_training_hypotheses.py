from coach_sync.io import write_json
from coach_sync.training_hypotheses import build_training_hypotheses


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


def test_training_hypotheses_writes_repeatable_artifacts(tmp_path):
    write_json(
        tmp_path / "activities" / "bike_high.json",
        _activity(1, "2026-04-05", "indoor_cycling", 120, p20=190, vo2=50),
    )
    write_json(
        tmp_path / "activities" / "mtb.json",
        _activity(2, "2026-04-10", "mountain_biking", 130, duration=7200, p20=185, vo2=50),
    )
    write_json(
        tmp_path / "activities" / "elliptical.json",
        _activity(3, "2026-05-05", "elliptical", 140, p20=None, vo2=47),
    )
    write_json(
        tmp_path / "activities" / "bike_low.json",
        _activity(4, "2026-05-20", "indoor_cycling", 40, p20=145, vo2=47),
    )
    write_json(
        tmp_path / "snapshots" / "training_response_dataset.json",
        {
            "target": "next_day_response_score",
            "rows": [
                {
                    "features": {"today_training_load": 90, "today_high_intensity_min": 12},
                    "target": {"next_day_response_score": 62},
                },
                {
                    "features": {"today_training_load": 0, "today_high_intensity_min": 0},
                    "target": {"next_day_response_score": 78},
                },
            ],
        },
    )

    artifact = build_training_hypotheses(tmp_path, "2026-05-26")

    assert artifact["analysis_type"] == "garmin_training_hypothesis_tests"
    assert artifact["scope"]["full_range"]["activity_rows"] == 4
    assert {item["id"] for item in artifact["hypotheses"]} == {
        "bike_specificity",
        "nonbike_substitution",
        "structured_intensity",
        "mtb_durability",
        "consistency_detraining",
        "gym_support",
    }
    assert (tmp_path / "snapshots" / "training_hypothesis_tests.json").exists()
    assert (tmp_path / "snapshots" / "training_hypothesis_tests.txt").exists()
