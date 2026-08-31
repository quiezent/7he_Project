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


def test_latest_session_response_uses_activity_matched_garmin_subjective_without_duplicate_questionnaire(tmp_path):
    response = build_latest_session_response(
        tmp_path,
        "2026-08-31",
        activity_id="24179129115",
        self_evaluation={
            "status": "available",
            "activity_id": "24179129115",
            "date": "2026-08-31",
            "feel_score": 75,
            "feel_out_of_5": 4,
            "rpe_score": 30,
            "global_rpe_out_of_10": 3,
            "source": "snapshots/activity_self_evaluation_index.json",
        },
    )

    assert response["status"] == "garmin_self_evaluation_only"
    assert response["global_rpe_0_to_10"] == 3
    assert response["subjective_evaluation"]["garmin_feel"]["out_of_5"] == 4
    assert response["subjective_evaluation"]["garmin_feel"]["components"] == [
        "clarity",
        "strength",
        "coordination",
    ]
    assert response["stop_rule_outcome"] is None
    assert response["stop_rule_outcome_explicit"] is False
    assert response["symptom"]["character"] == "unknown"
    assert response["decision_use"]["classification"] == (
        "subjective_session_response_available_safety_unknown"
    )


def test_latest_session_response_preserves_manual_garmin_rpe_disagreement(tmp_path):
    write_json(
        tmp_path / "input" / "feedback_2026-08-31.json",
        {
            "date": "2026-08-31",
            "entries": [
                {
                    "activity_id": "1",
                    "session_contract_review": {"global_session_rpe_0_to_10": 4},
                }
            ],
        },
    )

    response = build_latest_session_response(
        tmp_path,
        "2026-08-31",
        activity_id="1",
        self_evaluation={
            "status": "available",
            "activity_id": "1",
            "date": "2026-08-31",
            "feel_score": 50,
            "rpe_score": 30,
            "rpe_out_of_10": 3,
            "source": "snapshots/activity_self_evaluation_index.json",
        },
    )

    assert response["global_rpe_0_to_10"] == 4
    assert response["subjective_evaluation"]["rpe_resolution"] == (
        "manual_and_garmin_disagree_manual_retained"
    )
    assert response["stop_rule_outcome"] is None


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


def test_latest_session_response_rejects_malformed_nested_entry_date(tmp_path):
    write_json(
        tmp_path / "input" / "feedback_2026-08-31.json",
        {
            "date": "2026-08-31",
            "entries": [
                {
                    "date": "not-a-date",
                    "activity_id": "1",
                    "session_contract_review": {
                        "stop_rule_outcome": "not_triggered"
                    },
                }
            ],
        },
    )

    response = build_latest_session_response(
        tmp_path, "2026-08-31", activity_id="1"
    )

    assert response["status"] == "feedback_has_no_matching_entry"
    assert response["provenance"]["malformed_entry_date_count"] == 1
    assert response["stop_rule_outcome"] is None


def _garmin_review(activity_id="1", day="2026-08-31", feel=75, rpe=30):
    return {
        "status": "available",
        "activity_id": activity_id,
        "date": day,
        "feel_score": feel,
        "feel_out_of_5": int(feel / 25) + 1 if feel in {0, 25, 50, 75, 100} else None,
        "rpe_score": rpe,
        "source": "snapshots/activity_self_evaluation_index.json",
    }


def _reference_policy():
    return {
        "reference_action": {
            "date": "2026-08-31",
            "activity_id": "1",
            "bike_key": "specialized_stumpjumper",
            "action": "Two controlled familiar full-2K descents.",
            "minimum_retrospective_feel_1_to_5": 3,
        },
        "above_reference_promotion": {
            "minimum_retrospective_feel_1_to_5": 4,
            "increases_requiring_4_of_5": ["duration", "descent_count"],
        },
    }


def test_garmin_review_requires_matching_activity_and_date(tmp_path):
    for review in (
        _garmin_review(activity_id="other"),
        _garmin_review(day="2026-08-30"),
        {**_garmin_review(), "activity_id": None},
        {**_garmin_review(), "date": "malformed"},
    ):
        response = build_latest_session_response(
            tmp_path,
            "2026-08-31",
            activity_id="1",
            self_evaluation=review,
        )
        assert response["status"] == "feedback_missing"
        assert response["routine_review"]["status"] == "unusable"
        assert response["global_rpe_0_to_10"] is None


def test_complete_garmin_review_is_separate_from_unknown_safety(tmp_path):
    response = build_latest_session_response(
        tmp_path,
        "2026-08-31",
        activity_id="1",
        self_evaluation=_garmin_review(),
        subjective_review_policy=_reference_policy(),
    )

    assert response["routine_review"]["status"] == "complete"
    assert response["routine_review"]["duplicate_general_questionnaire_required"] is False
    assert response["safety_contract_outcome"]["status"] == "unknown"
    assert response["stop_rule_outcome"] is None
    assert response["subjective_tolerance_policy"]["reference_action"]["threshold_met"] is True
    assert response["subjective_tolerance_policy"]["above_reference_prerequisite"][
        "threshold_met"
    ] is True
    assert response["subjective_tolerance_policy"]["above_reference_prerequisite"][
        "same_day_clearance_granted"
    ] is False
    feel_source = response["subjective_evaluation"]["field_sources"][
        "garmin_feel_out_of_5"
    ]
    rpe_source = response["subjective_evaluation"]["field_sources"][
        "global_rpe_0_to_10"
    ]
    assert feel_source["path"].endswith("feel_out_of_5")
    assert feel_source["derived_from"].endswith("feel_score")
    assert rpe_source["path"].endswith("global_rpe_out_of_10")
    assert rpe_source["derived_from"].endswith("rpe_score")
    assert response["decision_use"]["classification_scope"] == "symptom_response"
    assert response["decision_use"]["review_summary"] == {
        "routine_review": "complete",
        "illness_airway": "unknown",
        "technical_execution": "unknown",
        "safety_contract_outcome": "unknown",
    }


def test_manual_typed_illness_and_technical_axes_remain_separate_from_garmin_feel(tmp_path):
    write_json(
        tmp_path / "input" / "feedback_2026-08-31.json",
        {
            "date": "2026-08-31",
            "entries": [
                {
                    "activity_id": "1",
                    "reported_context": {
                        "airway_and_illness": {
                            "illness_status": "absent",
                            "airway_symptoms": {
                                "sore_throat": "absent",
                                "runny_nose": "absent",
                            },
                            "reported_by": "athlete",
                        }
                    },
                    "session_contract_review": {
                        "technical_quality_notes": "Braking stayed deliberate.",
                        "late_session_skill_fade": "none",
                    },
                }
            ],
        },
    )

    response = build_latest_session_response(
        tmp_path,
        "2026-08-31",
        activity_id="1",
        self_evaluation=_garmin_review(),
    )

    assert response["illness_airway"]["status"] == "observed"
    assert response["illness_airway"]["illness_status"] == "absent"
    assert response["illness_airway"]["airway_symptoms"]["sore_throat"] == "absent"
    assert response["technical_execution"]["status"] == "observed"
    assert response["technical_execution"]["technical_quality_notes"] == (
        "Braking stayed deliberate."
    )
    assert response["technical_execution"]["late_session_skill_fade"] == "none"
    assert response["stop_rule_outcome"] is None
    assert response["decision_use"]["classification"] == "insufficient_evidence"
    assert response["decision_use"]["reasons"] == [
        "symptom_response_fields_unknown",
        "safety_contract_outcome_unknown",
    ]


def test_technical_missing_sentinel_strings_remain_unknown(tmp_path):
    for sentinel in (
        "not reported",
        "unknown",
        "not collected",
        "No questionnaire was collected.",
    ):
        write_json(
            tmp_path / "input" / "feedback_2026-08-31.json",
            {
                "date": "2026-08-31",
                "entries": [
                    {
                        "activity_id": "1",
                        "session_contract_review": {
                            "technical_quality_notes": sentinel,
                            "late_session_skill_fade": sentinel,
                        },
                    }
                ],
            },
        )

        response = build_latest_session_response(
            tmp_path,
            "2026-08-31",
            activity_id="1",
            self_evaluation=_garmin_review(feel=100),
        )

        technical = response["technical_execution"]
        assert technical["status"] == "unknown"
        assert technical["technical_quality_notes"] is None
        assert technical["late_session_skill_fade"] is None
        assert technical["field_observation_status"] == {
            "technical_quality_notes": "unknown",
            "late_session_skill_fade": "unknown",
        }


def test_legacy_indoor_technical_fields_are_not_applicable_not_observed(tmp_path):
    legacy_sessions = (
        ("2026-08-26", "24121900953"),
        ("2026-08-27", "24131659432"),
    )
    for day, activity_id in legacy_sessions:
        write_json(
            tmp_path / "input" / f"feedback_{day}.json",
            {
                "date": day,
                "entries": [
                    {
                        "activity_id": activity_id,
                        "session_contract_review": {
                            "technical_quality_notes": (
                                "Indoor seated low-aerobic session; MTB technical fields "
                                "are not applicable."
                            ),
                            "late_session_skill_fade": "not_applicable_indoor_cycling",
                        },
                    }
                ],
            },
        )

        response = build_latest_session_response(
            tmp_path,
            day,
            activity_id=activity_id,
        )

        technical = response["technical_execution"]
        assert technical["status"] == "not_applicable"
        assert technical["technical_quality_notes"] is None
        assert technical["late_session_skill_fade"] is None
        assert technical["field_observation_status"] == {
            "technical_quality_notes": "not_applicable",
            "late_session_skill_fade": "not_applicable",
        }


def test_not_applicable_field_does_not_erase_real_technical_observation(tmp_path):
    write_json(
        tmp_path / "input" / "feedback_2026-08-31.json",
        {
            "date": "2026-08-31",
            "entries": [
                {
                    "activity_id": "1",
                    "session_contract_review": {
                        "technical_quality_notes": "Braking stayed deliberate.",
                        "late_session_skill_fade": "not_applicable_custom_marker",
                    },
                }
            ],
        },
    )

    response = build_latest_session_response(
        tmp_path,
        "2026-08-31",
        activity_id="1",
    )

    technical = response["technical_execution"]
    assert technical["status"] == "observed"
    assert technical["technical_quality_notes"] == "Braking stayed deliberate."
    assert technical["late_session_skill_fade"] is None
    assert technical["field_observation_status"] == {
        "technical_quality_notes": "observed",
        "late_session_skill_fade": "not_applicable",
    }


def test_illness_phase_aliases_create_caution_even_with_favorable_garmin_feel(tmp_path):
    for illness_phase in ("suspected", "active", "recovering"):
        write_json(
            tmp_path / "input" / "feedback_2026-08-31.json",
            {
                "date": "2026-08-31",
                "entries": [
                    {
                        "activity_id": "1",
                        "reported_context": {
                            "airway_and_illness": {
                                "illness_status": illness_phase,
                                "reported_by": "athlete",
                            }
                        },
                    }
                ],
            },
        )

        response = build_latest_session_response(
            tmp_path,
            "2026-08-31",
            activity_id="1",
            self_evaluation=_garmin_review(feel=100),
        )

        illness = response["illness_airway"]
        caution = response["decision_use"]["illness_airway_caution"]
        assert response["subjective_evaluation"]["garmin_feel"]["out_of_5"] == 5
        assert illness["status"] == "observed"
        assert illness["illness_status"] == "present"
        assert illness["illness_phase"] == illness_phase
        assert caution["status"] == "present"
        assert caution["training_promotion_allowed"] is False
        assert "illness_present" in response["decision_use"]["reasons"]
        assert f"illness_{illness_phase}" in response["decision_use"]["reasons"]


def test_airway_presence_creates_caution_and_none_alias_means_absent(tmp_path):
    write_json(
        tmp_path / "input" / "feedback_2026-08-31.json",
        {
            "date": "2026-08-31",
            "entries": [
                {
                    "activity_id": "1",
                    "reported_context": {
                        "airway_and_illness": {
                            "illness_status": "none",
                            "airway_symptoms": {"sore_throat": "present"},
                        }
                    },
                }
            ],
        },
    )

    response = build_latest_session_response(
        tmp_path,
        "2026-08-31",
        activity_id="1",
        self_evaluation=_garmin_review(feel=100),
    )

    assert response["illness_airway"]["illness_status"] == "absent"
    assert response["illness_airway"]["airway_symptoms"]["sore_throat"] == "present"
    assert response["decision_use"]["illness_airway_caution"]["status"] == "present"
    assert "airway_symptom_present_sore_throat" in response["decision_use"]["reasons"]


def test_garmin_subjective_categories_fail_closed_when_raw_values_are_off_grid(tmp_path):
    invalid_feel = _garmin_review(feel=74)
    invalid_feel["feel_out_of_5"] = 4
    response = build_latest_session_response(
        tmp_path,
        "2026-08-31",
        activity_id="1",
        self_evaluation=invalid_feel,
    )
    assert response["routine_review"]["status"] == "partial"
    assert response["subjective_evaluation"]["garmin_feel"]["out_of_5"] is None

    invalid_rpe = _garmin_review(rpe=35)
    invalid_rpe["global_rpe_out_of_10"] = 3
    response = build_latest_session_response(
        tmp_path,
        "2026-08-31",
        activity_id="1",
        self_evaluation=invalid_rpe,
    )
    assert response["routine_review"]["status"] == "partial"
    assert response["subjective_evaluation"]["garmin_perceived_effort"][
        "global_rpe_0_to_10"
    ] is None


def test_reference_policy_does_not_infer_equivalence_for_other_session(tmp_path):
    response = build_latest_session_response(
        tmp_path,
        "2026-09-01",
        activity_id="2",
        self_evaluation=_garmin_review(activity_id="2", day="2026-09-01", feel=100),
        subjective_review_policy=_reference_policy(),
    )

    reference = response["subjective_tolerance_policy"]["reference_action"]
    assert reference["exact_identifier_match"] is False
    assert reference["action_equivalence_inferred"] is False
    assert reference["threshold_met"] is None


def test_exact_reference_feel_three_meets_reference_but_not_above_reference(tmp_path):
    response = build_latest_session_response(
        tmp_path,
        "2026-08-31",
        activity_id="1",
        self_evaluation=_garmin_review(feel=50),
        subjective_review_policy=_reference_policy(),
    )

    policy = response["subjective_tolerance_policy"]
    assert policy["reference_action"]["threshold_met"] is True
    assert policy["above_reference_prerequisite"]["threshold_met"] is False
    assert policy["above_reference_prerequisite"]["necessary_not_sufficient"] is True


def test_stop_rule_outcome_accepts_only_canonical_enum(tmp_path):
    valid = {
        "not_triggered",
        "triggered_and_stopped",
        "triggered_and_downshifted",
        "triggered_but_continued",
    }
    for index, outcome in enumerate(sorted(valid)):
        day = f"2026-08-{20 + index:02d}"
        write_json(
            tmp_path / "input" / f"feedback_{day}.json",
            {
                "date": day,
                "entries": [
                    {
                        "activity_id": str(index),
                        "session_contract_review": {"stop_rule_outcome": outcome},
                    }
                ],
            },
        )
        response = build_latest_session_response(tmp_path, day, activity_id=str(index))
        assert response["stop_rule_outcome"] == outcome
        assert response["stop_rule_outcome_explicit"] is True
        assert response["safety_contract_outcome"]["audit"]["validation"] == "canonical"

    for index, outcome in enumerate(("not_reported", "unknown", "none", 3, True)):
        day = f"2026-07-{20 + index:02d}"
        write_json(
            tmp_path / "input" / f"feedback_{day}.json",
            {
                "date": day,
                "entries": [
                    {
                        "activity_id": str(index),
                        "session_contract_review": {"stop_rule_outcome": outcome},
                    }
                ],
            },
        )
        response = build_latest_session_response(tmp_path, day, activity_id=str(index))
        assert response["stop_rule_outcome"] is None
        assert response["stop_rule_outcome_explicit"] is False
        assert response["safety_contract_outcome"]["audit"]["validation"] in {
            "not_reported_sentinel",
            "invalid",
        }


def test_rejected_manual_feedback_remains_visible_with_valid_garmin_review(tmp_path):
    write_json(
        tmp_path / "input" / "feedback_2026-08-31.json",
        {"date": "malformed", "entries": []},
    )
    response = build_latest_session_response(
        tmp_path,
        "2026-08-31",
        activity_id="1",
        self_evaluation=_garmin_review(),
    )

    assert response["status"] == "feedback_wrong_date"
    assert response["manual_feedback"]["status"] == "feedback_wrong_date"
    assert response["routine_review"]["status"] == "complete"
    assert response["global_rpe_0_to_10"] == 3
