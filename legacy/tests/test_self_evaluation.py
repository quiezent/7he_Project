from coach_sync.io import write_json
from coach_sync.self_evaluation import build_self_evaluation_report, summarize_activity_self_evaluation


def _activity(activity_id, day, name="Tempo", activity_type="indoor_cycling"):
    return {
        "activityId": activity_id,
        "activityName": name,
        "activityType": {"typeKey": activity_type},
        "startTimeLocal": f"{day} 08:00:00",
        "duration": 3600,
    }


def test_summarize_activity_self_evaluation_from_activity_detail():
    payload = {
        "summaryDTO": {
            "directWorkoutFeel": 50,
            "directWorkoutRpe": 40,
        }
    }

    summary = summarize_activity_self_evaluation(payload)

    assert summary["has_self_evaluation"] is True
    assert summary["feel_label"] == "normal"
    assert summary["rpe_label"] == "somewhat_hard"
    assert summary["rpe_out_of_10"] == 4.0


def test_self_evaluation_report_collects_recent_logged_rows(tmp_path):
    write_json(tmp_path / "activities" / "tempo.json", _activity(1, "2026-05-28"))
    write_json(tmp_path / "activities" / "mtb.json", _activity(2, "2026-05-27", "Kuala Lumpur Mountain Biking", "mountain_biking"))
    write_json(
        tmp_path / "snapshots" / "activity_self_evaluation_index.json",
        {
            "activities": [
                {
                    "activity_id": "1",
                    "date": "2026-05-28",
                    "name": "Tempo",
                    "type": "indoor_cycling",
                    "category": "bike_indoor",
                    "detail_fetch_ok": True,
                    "has_self_evaluation": True,
                    "feel_score": 50.0,
                    "feel_label": "normal",
                    "rpe_score": 40.0,
                    "rpe_label": "somewhat_hard",
                    "rpe_out_of_10": 4.0,
                },
                {
                    "activity_id": "2",
                    "date": "2026-05-27",
                    "name": "Kuala Lumpur Mountain Biking",
                    "type": "mountain_biking",
                    "category": "mtb",
                    "detail_fetch_ok": True,
                    "has_self_evaluation": False,
                },
            ]
        },
    )

    report = build_self_evaluation_report(tmp_path, "2026-05-29")

    assert report["checked_activities"] == 2
    assert report["evaluated_activities"] == 1
    assert report["recent_self_evaluations"][0]["rpe_out_of_10"] == 4.0
