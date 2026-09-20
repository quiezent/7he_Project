from coach_sync.athlete_questions import build_athlete_question_audit
from coach_sync.io import write_json


def _activity(activity_id, day, type_key, load, name="Training", p20=None):
    payload = {
        "activityId": activity_id,
        "activityName": name,
        "activityType": {"typeKey": type_key},
        "startTimeLocal": f"{day} 08:00:00",
        "duration": 3600,
        "activityTrainingLoad": load,
        "averageHR": 130,
        "maxHR": 150,
        "aerobicTrainingEffect": 3.0,
        "anaerobicTrainingEffect": 0.0,
        "hrTimeInZone_1": 600,
        "hrTimeInZone_2": 1200,
        "hrTimeInZone_3": 900,
    }
    if p20 is not None:
        payload["maxAvgPower_1200"] = p20
        payload["avgPower"] = p20 - 30
        payload["normPower"] = p20 - 20
    return payload


def test_athlete_question_audit_writes_weekly_and_power_sections(tmp_path):
    write_json(
        tmp_path / "activities" / "indoor.json",
        _activity(1, "2026-05-05", "indoor_cycling", 70, name="Tempo", p20=150),
    )
    write_json(
        tmp_path / "activities" / "mtb.json",
        _activity(2, "2026-05-06", "mountain_biking", 120, name="Kuala Lumpur Mountain Biking", p20=145),
    )
    write_json(
        tmp_path / "activities" / "elliptical.json",
        _activity(3, "2026-05-07", "elliptical", 80, name="Elliptical"),
    )
    write_json(tmp_path / "snapshots" / "wellness_daily.json", [])

    audit = build_athlete_question_audit(tmp_path, "2026-05-26")

    assert audit["analysis_type"] == "athlete_profile_question_audit"
    assert audit["bike_specific_minimum_weeks"]
    assert audit["power_test_and_ftp"]["historical_best_p20"]["p20_w"] == 150
    assert audit["last_10_mtb_ride_labels"][0]["ride_type_inferred"]
    assert (tmp_path / "snapshots" / "athlete_question_audit.json").exists()
