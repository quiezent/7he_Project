from coach_sync.action_state_prediction import build_action_state_prediction
from coach_sync.io import write_json


def _state(day: str, readiness: str = "yellow") -> dict:
    return {
        "date": day,
        "readiness": {
            "readiness_level": readiness,
            "readiness_score": 60 if readiness == "yellow" else 78,
        },
    }


def _loose_outdoor_plan() -> dict:
    return {
        "session": {
            "title": "Easy bike continuity",
            "type": "outdoor_bike_optional",
            "duration_min": 45,
            "intensity": "easy",
            "details": ["Ride easy if possible."],
        }
    }


def test_action_state_prediction_uses_planned_session_override(tmp_path):
    day = "2026-06-09"
    write_json(
        tmp_path / "input" / f"planned_session_{day}.json",
        {
            "session": {
                "title": "3 x 8 tempo torque",
                "type": "bike_quality",
                "duration_min": 52,
                "intensity": "moderate",
                "dose": {"cap": "No extra intervals."},
                "execution_rules": ["Hold 140 W and stop after three work reps."],
            }
        },
    )

    artifact = build_action_state_prediction(
        tmp_path,
        day,
        state=_state(day),
        plan=_loose_outdoor_plan(),
    )

    intended = artifact["intended_action"]
    branches = {row["branch_id"]: row for row in artifact["action_prediction"]["branches"]}

    assert intended["source_type"] == "planned_session_override"
    assert intended["expected_session"]["title"] == "3 x 8 tempo torque"
    assert branches["follow_intended_action"]["probability"] > branches["mission_creep_or_extension"]["probability"]
    assert artifact["state_predictions_by_branch"]
    assert (tmp_path / "snapshots" / "action_state_prediction.json").exists()


def test_loose_outdoor_plan_predicts_mission_creep_risk(tmp_path):
    day = "2026-06-09"

    artifact = build_action_state_prediction(
        tmp_path,
        day,
        state=_state(day),
        plan=_loose_outdoor_plan(),
    )

    branches = {row["branch_id"]: row for row in artifact["action_prediction"]["branches"]}

    assert artifact["intended_action"]["source_type"] == "today_plan"
    assert branches["mission_creep_or_extension"]["probability"] > branches["follow_intended_action"]["probability"]
    assert artifact["action_prediction"]["most_likely_branch"]["branch_id"] == "mission_creep_or_extension"


def test_actual_action_review_flags_harder_than_intended(tmp_path):
    day = "2026-06-09"
    write_json(
        tmp_path / "activities" / "mtb.json",
        {
            "activityId": 1,
            "activityName": "Kuala Lumpur Mountain Biking",
            "activityType": {"typeKey": "mountain_biking"},
            "startTimeLocal": f"{day} 08:00:00",
            "duration": 7200,
            "activityTrainingLoad": 180,
            "averageHR": 148,
            "maxHR": 178,
            "hrTimeInZone_4": 600,
            "hrTimeInZone_5": 0,
        },
    )

    artifact = build_action_state_prediction(
        tmp_path,
        day,
        state=_state(day),
        plan=_loose_outdoor_plan(),
    )

    review = artifact["actual_action_review"]

    assert review["status"] == "available"
    assert review["outcome"] == "harder_than_intended"
    assert review["actual"]["training_load"] == 180
    assert review["closest_predicted_branch_id"] == "mission_creep_or_extension"


def test_actual_action_review_prefers_cached_activity_index(tmp_path):
    day = "2026-06-09"
    write_json(
        tmp_path / "snapshots" / "activity_summary_index.json",
        [
            {
                "activity_ref": "cached",
                "date": day,
                "type": "indoor_cycling",
                "category": "bike_indoor",
                "counts_for_training_load": True,
                "duration_min": 52,
                "training_load": 37.1,
            }
        ],
    )

    artifact = build_action_state_prediction(
        tmp_path,
        day,
        state=_state(day),
        plan=_loose_outdoor_plan(),
    )

    actual = artifact["actual_action_review"]["actual"]

    assert actual["data_source"] == "snapshots/activity_summary_index.json"
    assert actual["activity_index_latest_date"] == day
    assert actual["training_load"] == 37.1
