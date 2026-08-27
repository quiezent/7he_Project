from coach_sync.io import write_json
from coach_sync.session_response import build_latest_session_response


def test_latest_session_response_normalizes_2026_08_27_feedback_shape(tmp_path):
    write_json(
        tmp_path / "input" / "feedback_2026-08-27.json",
        {
            "date": "2026-08-27",
            "entries": [
                {
                    "timestamp_local": "2026-08-27T12:00:00+08:00",
                    "activity_id": "older",
                    "session_contract_review": {"global_session_rpe_0_to_10": 2},
                },
                {
                    "timestamp_local": "2026-08-27T13:20:15+08:00",
                    "source": "athlete_post_session_report_with_matched_garmin_trace_audit",
                    "activity_id": "24131659432",
                    "reported_context": {
                        "athlete_response": (
                            "The same diffuse bilateral muscular burning appeared only during the final "
                            "five minutes of cooldown and resolved fully after the ride."
                        ),
                        "symptom_classification": (
                            "Transient bilateral exertional muscular burning in the glutes, not sharp "
                            "or focal pain. Athlete reported 0/10 at approximately 14:45 local."
                        ),
                    },
                    "session_contract_review": {
                        "actual_duration_min": 60.0,
                        "global_session_rpe_0_to_10": 3,
                        "local_leg_rpe_0_to_10": 8,
                        "local_leg_symptom": (
                            "The same diffuse bilateral glute muscular burning, occurring only in the "
                            "final five minutes of cooldown and resolving to 0/10 after the session."
                        ),
                        "local_leg_onset": "Approximately 55 minutes after session start.",
                        "stop_rule_outcome": "not_triggered",
                    },
                },
            ],
        },
    )

    response = build_latest_session_response(tmp_path, "2026-08-27", activity_id="24131659432")

    assert response["status"] == "available"
    assert response["activity_id"] == "24131659432"
    assert response["global_rpe_0_to_10"] == 3.0
    assert response["local_rpe_0_to_10"] == 8.0
    assert response["stop_rule_outcome"] == "not_triggered"
    assert response["stop_rule_outcome_explicit"] is True
    assert response["symptom"] == {
        "distribution": "bilateral",
        "character": "muscular_burn",
        "locations": ["glutes"],
        "mechanics_altered": "unknown",
        "onset": "Approximately 55 minutes after session start.",
        "onset_min": 55.0,
        "post_session_intensity_0_to_10": 0.0,
        "resolved": "yes",
        "resolution_time_local": "14:45",
        "resolution_duration_min": None,
    }
    assert response["decision_use"]["classification"] == "benign_terminal_muscular_burn_candidate"
    assert "mechanics_not_reported" in response["decision_use"]["reasons"]
    assert response["provenance"]["selection_rule"] == "activity_id_then_latest_timestamp"
    assert response["provenance"]["source"] == "input/feedback_2026-08-27.json"


def test_latest_session_response_keeps_missing_fields_unknown_and_never_infers_stop_outcome(tmp_path):
    write_json(
        tmp_path / "input" / "feedback_2026-08-27.json",
        {
            "date": "2026-08-27",
            "entries": [
                {
                    "timestamp_local": "2026-08-27T16:00:00+08:00",
                    "activity_id": "123",
                    "session_contract_review": {
                        "global_session_rpe_0_to_10": 4,
                        "local_leg_symptom": "Legs felt worked.",
                    },
                }
            ],
        },
    )

    response = build_latest_session_response(tmp_path, "2026-08-27")

    assert response["stop_rule_outcome"] is None
    assert response["stop_rule_outcome_explicit"] is False
    assert response["local_rpe_0_to_10"] is None
    assert response["symptom"]["distribution"] == "unknown"
    assert response["symptom"]["character"] == "unknown"
    assert response["symptom"]["mechanics_altered"] == "unknown"
    assert response["symptom"]["resolved"] == "unknown"
    assert response["decision_use"]["classification"] == "insufficient_evidence"
    assert "stop_rule_outcome is surfaced only when explicitly recorded" in response["decision_use"]["guardrail"]


def test_latest_session_response_rejects_wrong_date_payload(tmp_path):
    write_json(
        tmp_path / "input" / "feedback_2026-08-27.json",
        {
            "date": "2026-08-26",
            "entries": [
                {
                    "activity_id": "123",
                    "session_contract_review": {"stop_rule_outcome": "not_triggered"},
                }
            ],
        },
    )

    response = build_latest_session_response(tmp_path, "2026-08-27")

    assert response["status"] == "feedback_wrong_date"
    assert response["stop_rule_outcome"] is None
    assert response["decision_use"]["classification"] == "insufficient_evidence"


def test_latest_session_response_missing_file_is_explicitly_unavailable(tmp_path):
    response = build_latest_session_response(tmp_path, "2026-08-27", activity_id="123")

    assert response["status"] == "feedback_missing"
    assert response["activity_id"] == "123"
    assert response["stop_rule_outcome"] is None
    assert response["stop_rule_outcome_explicit"] is False
    assert response["symptom"]["character"] == "unknown"
    assert response["provenance"]["source"] == "input/feedback_2026-08-27.json"


def test_latest_session_response_does_not_fall_back_to_another_activity(tmp_path):
    write_json(
        tmp_path / "input" / "feedback_2026-08-27.json",
        {
            "date": "2026-08-27",
            "entries": [
                {
                    "activity_id": "different",
                    "session_contract_review": {"stop_rule_outcome": "not_triggered"},
                }
            ],
        },
    )

    response = build_latest_session_response(tmp_path, "2026-08-27", activity_id="requested")

    assert response["status"] == "feedback_has_no_matching_entry"
    assert response["stop_rule_outcome"] is None
    assert response["decision_use"]["reasons"] == ["requested_activity_id_not_found"]
