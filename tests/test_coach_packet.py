import json

import coach_sync.coach_packet as coach_packet_module
from coach_sync.cli import main
from coach_sync.coach_packet import _compact_latest_session, build_coach_packet
from coach_sync.context import load_context
from coach_sync.io import write_json


def _write_green_wellness(root, day: str) -> None:
    write_json(
        root / "snapshots" / f"garmin_wellness_{day}.json",
        {
            "date": day,
            "payloads": [
                {
                    "ok": True,
                    "data": {
                        "calendarDate": day,
                        "sleepScore": 90,
                        "hrvStatus": "balanced",
                        "bodyBattery": 82,
                        "bodyBatteryAtWakeTime": 86,
                    },
                }
            ],
        },
    )


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


def _write_activity(root, day: str) -> None:
    write_json(
        root / "activities" / f"activity_{day}.json",
        {
            "activityId": int(day.replace("-", "")),
            "activityName": "Indoor Cycling",
            "activityType": {"typeKey": "indoor_cycling"},
            "startTimeLocal": f"{day} 10:00:00",
            "duration": 3600,
            "activityTrainingLoad": 55,
        },
    )


def test_coach_packet_writes_decision_surface_and_triages_models(tmp_path):
    load_context(tmp_path)
    _write_green_wellness(tmp_path, "2026-04-29")
    _write_training_status(tmp_path, "2026-04-29")
    _write_activity(tmp_path, "2026-04-29")

    packet = build_coach_packet(tmp_path, "2026-04-29")

    assert packet["artifact_type"] == "coach_decision_packet"
    assert packet["stack_path"]["chosen_path"] == "evidence_triage_over_more_models"
    assert packet["today_call"]["session"]["type"] == "endurance_skills"
    assert any(item["name"] == "Readiness" for item in packet["evidence"]["trusted"])
    assert any(
        item["name"] == "Garmin diagnosis arbitration"
        for item in packet["evidence"]["trusted"]
    )
    assert any(
        item["name"] == "Next-day training response tree"
        for item in packet["evidence"]["ignored_for_decision"]
    )
    assert (tmp_path / "snapshots" / "coach_packet.json").exists()
    assert (tmp_path / "snapshots" / "coach_packet.txt").exists()


def test_coach_packet_cli_command(tmp_path, capsys):
    load_context(tmp_path)
    _write_green_wellness(tmp_path, "2026-04-29")

    code = main(["coach-packet", "--root", str(tmp_path), "--date", "2026-04-29"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert code == 0
    assert payload["artifacts"]["json"] == "snapshots/coach_packet.json"


def test_coach_packet_promotes_sabbath_constraint(tmp_path):
    load_context(tmp_path)
    _write_green_wellness(tmp_path, "2026-05-03")

    packet = build_coach_packet(tmp_path, "2026-05-03")

    assert packet["today_call"]["stance"] == "sabbath_rest"
    assert packet["today_call"]["session"]["type"] == "scheduled_rest"
    assert packet["evidence"]["trusted"][0]["name"] == "Scheduled rest"


def test_coach_packet_labels_zero_dose_scheduled_recovery_as_recovery(tmp_path):
    load_context(tmp_path)
    state = {
        "date": "2026-07-17",
        "readiness": {
            "readiness_level": "green",
            "readiness_score": 73,
            "confidence": "medium",
            "reasons": [],
        },
        "data_freshness": {
            "status": "current",
            "activity_data": {"status": "current"},
        },
        "phase": {"name": "base_rebuild"},
        "training_status_current": {},
        "wellness_trends": {
            "latest": {
                "sleep_start_local": "2026-07-17T01:39+08:00",
                "sleep_end_local": "2026-07-17T07:14+08:00",
                "sleep_window_hours": 5.58,
                "primary_sleep_hours": 5.53,
                "nap_hours_reported": 0,
                "total_sleep_hours_reported": 5.53,
            },
            "last_7": {"avg_sleep_hours": 5.56},
        },
    }
    plan = {
        "date": "2026-07-17",
        "session": {
            "title": "Post-MTB absorption day",
            "type": "scheduled_recovery",
            "modality": "rest",
            "duration_min": 0,
            "intensity": "rest",
        },
    }

    packet = build_coach_packet(tmp_path, "2026-07-17", state=state, plan=plan)

    assert packet["today_call"]["stance"] == "recovery"
    sleep = next(
        item for item in packet["evidence"]["trusted"] if item["name"] == "Sleep opportunity"
    )
    assert sleep["status"] == "sleep_incomplete"
    assert sleep["value"]["primary_sleep_hours"] == 5.53


def test_coach_packet_labels_watch_level_mtb_as_controlled_skill(tmp_path):
    load_context(tmp_path)
    state = {
        "date": "2026-07-25",
        "readiness": {
            "readiness_level": "yellow",
            "readiness_score": 61,
            "confidence": "high",
            "hard_session_guidance": "caution",
            "reasons": [],
        },
        "cns_readiness": {
            "status": "watch",
            "score": 74,
            "confidence": "high",
            "session_ceiling": {"level": "controlled_skill_only"},
            "flags": [],
        },
        "data_freshness": {
            "status": "current",
            "activity_data": {"status": "current"},
        },
        "phase": {"name": "base_rebuild"},
        "training_status_current": {},
        "wellness_trends": {"latest": {"primary_sleep_hours": 5.03}},
    }
    plan = {
        "date": "2026-07-25",
        "session": {
            "title": "Familiar Kiara skill gate",
            "type": "mtb_skill_familiar_capped",
            "modality": "mtb",
            "duration_min": 70,
            "intensity": "skill",
        },
    }

    packet = build_coach_packet(tmp_path, "2026-07-25", state=state, plan=plan)

    assert packet["today_call"]["stance"] == "controlled_skill"


def test_coach_packet_surfaces_oxygenation_context_without_promoting_red_readiness(
    tmp_path,
):
    load_context(tmp_path)
    state = {
        "date": "2026-08-06",
        "readiness": {
            "readiness_level": "red",
            "readiness_score": 44,
            "confidence": "high",
            "reasons": [],
        },
        "data_freshness": {
            "status": "current",
            "activity_data": {"status": "current"},
        },
        "phase": {"name": "base_rebuild"},
        "wellness_trends": {
            "latest": {
                "date": "2026-08-06",
                "primary_sleep_hours": 4.93,
                "monitoring_altitude_m": 86,
                "monitoring_altitude_source": (
                    "daily_summary.averageMonitoringEnvironmentAltitude"
                ),
                "spo2_endpoint_status": "success",
                "spo2_latest_attempt_status": "success",
                "spo2_freshness": "target_date",
                "spo2_daily_average_pct": 99,
                "spo2_sleep_average_pct": 99,
                "spo2_lowest_pct": 98,
                "spo2_hourly_aggregate_count": 7,
                "spo2_in_activity_interpretation": (
                    "The daily endpoint does not establish activity-time SpO2."
                ),
                "respiration_endpoint_status": "success",
                "respiration_latest_attempt_status": "success",
                "respiration_freshness": "target_date",
                "respiration_waking_average_brpm": 14,
                "respiration_sleep_average_brpm": 12,
                "respiration_two_min_valid_count": 242,
                "respiration_two_min_sentinel_count": 8,
                "respiration_two_min_activity_sentinel_count": 0,
                "respiration_two_min_series_coverage_ratio": 1.0,
            },
            "last_7": {
                "avg_spo2_daily_pct": 92,
                "avg_respiration_waking_brpm": 14,
            },
        },
    }
    plan = {
        "date": "2026-08-06",
        "session": {
            "title": "Hard session that readiness must block",
            "type": "intervals",
            "modality": "bike",
            "duration_min": 60,
            "intensity": "hard",
        },
    }

    packet = build_coach_packet(tmp_path, "2026-08-06", state=state, plan=plan)

    signal = next(
        item
        for item in packet["evidence"]["trusted"]
        if item["name"] == "Oxygenation and respiration context"
    )
    assert signal["status"] == "available"
    assert signal["decision_use"] == (
        "context_only_downshift_or_verify_never_readiness_promotion"
    )
    assert signal["value"]["spo2"]["daily_average_pct"] == 99
    assert "never raises readiness" in signal["message"]
    assert packet["today_call"]["stance"] == "downshift"


def test_latest_session_compaction_keeps_hike_timing_phases_and_safe_fit_sources():
    compact = _compact_latest_session(
        {
            "activity": {"category": "hike"},
            "timing": {
                "elapsed_min": 173.2,
                "moving_min": 105.4,
                "stopped_min": 67.7,
                "nonmoving_or_stopped_estimate_min": 67.7,
                "stopped_derivation": "derived_detail_trace_movement_timeline",
                "garmin_reported": {
                    "elapsed_min": 173.2,
                    "moving_min": 30.1,
                    "implied_stopped_min": 143.1,
                    "source_fields": {
                        "elapsed": "elapsedDuration",
                        "moving": "movingDuration",
                    },
                },
                "plausibility": {
                    "status": "garmin_moving_duration_replaced_by_trace_estimate",
                    "reason": "steep hike",
                    "trace": {
                        "moving_min": 105.4,
                        "stopped_min": 67.7,
                        "sample_count": 1050,
                        "source": "activities/details/garmin_PRIVATE_ID_detail.json",
                    },
                },
            },
            "device": {
                "status": "available",
                "hr_confidence": "onboard_likely",
                "standard_fit_device_sources": {
                    "status": "available",
                    "device_info_rows": 3,
                    "source_types": ["local"],
                    "external_device_source_present": False,
                    "local_or_onboard_source_present": True,
                    "local_or_onboard_only": True,
                    "serial_numbers": ["PRIVATE_SERIAL"],
                    "creator": {
                        "manufacturer": "garmin",
                        "product": 4197,
                        "software_version": 6.6,
                        "source_type": "local",
                        "serial_number": "PRIVATE_SERIAL",
                    },
                },
                "interpretation_guardrail": "Onboard is likely, not proven wrist HR.",
            },
            "hike_phase_summary": {
                "status": "available_derived",
                "trace_sample_count": 1050,
                "sample_count": 1049,
                "valid_hr_elevation_sample_ratio": 0.999,
                "trace_min_elevation_m": 1879.0,
                "trace_max_elevation_m": 2222.8,
                "top_band": {
                    "floor_elevation_m": 2214.8,
                    "rule": "within_8_m_of_trace_max",
                    "first_entry_offset_min": 65.1,
                    "last_exit_offset_min": 95.1,
                },
                "phases": {
                    "ascent_to_first_top_band_entry": {
                        "duration_min": 65.1,
                        "average_hr_bpm": 126.3,
                        "max_hr_bpm": 151,
                    },
                    "top_band_dwell": {"duration_min": 30.0},
                    "descent_after_last_top_band_exit": {"duration_min": 78.0},
                },
                "source": "activities/details/garmin_PRIVATE_ID_detail.json",
            },
        }
    )

    assert compact["timing"]["garmin_reported"]["moving_min"] == 30.1
    assert compact["timing"]["moving_min"] == 105.4
    assert compact["hike_phase_summary"]["phases"][
        "ascent_to_first_top_band_entry"
    ]["average_hr_bpm"] == 126.3
    assert compact["hr_source"]["standard_fit_device_sources"][
        "local_or_onboard_only"
    ] is True
    serialized = json.dumps(compact)
    assert "PRIVATE_ID" not in serialized
    assert "PRIVATE_SERIAL" not in serialized


def test_coach_packet_labels_executed_contract_as_post_session_review(tmp_path):
    load_context(tmp_path)
    state = {
        "date": "2026-07-25",
        "readiness": {
            "readiness_level": "red",
            "readiness_score": 38,
            "confidence": "high",
            "hard_session_guidance": "avoid",
            "reasons": [],
        },
        "cns_readiness": {
            "status": "watch",
            "score": 63,
            "confidence": "high",
            "session_ceiling": {"level": "controlled_skill_only"},
            "flags": [],
        },
        "data_freshness": {
            "status": "current",
            "activity_data": {"status": "current"},
        },
        "phase": {"name": "base_rebuild"},
        "training_status_current": {},
        "wellness_trends": {"latest": {"primary_sleep_hours": 5.03}},
    }
    plan = {
        "date": "2026-07-25",
        "coaching_status": "post_session_review",
        "session": {
            "title": "Familiar Kiara skill gate",
            "type": "mtb_skill_familiar_capped",
            "modality": "mtb",
            "duration_min": 70,
            "intensity": "skill",
        },
        "decision_inputs": {
            "session_lifecycle": {"stance": "post_session_review"},
        },
        "constraint_resolution": {
            "applied": [],
            "effective_session_source": "executed_coach_authored_contract",
        },
    }

    packet = build_coach_packet(tmp_path, "2026-07-25", state=state, plan=plan)

    assert packet["today_call"]["stance"] == "post_session_review"
    assert (
        packet["today_call"]["constraint_resolution"]["effective_session_source"]
        == "executed_coach_authored_contract"
    )


def test_coach_packet_surfaces_wear_state_without_promoting_the_session(tmp_path):
    load_context(tmp_path)
    state = {
        "date": "2026-07-19",
        "readiness": {
            "readiness_level": "yellow",
            "readiness_score": 68,
            "confidence": "medium",
            "hard_session_guidance": "caution",
            "reasons": [],
        },
        "cns_readiness": {
            "status": "ready",
            "score": 94,
            "session_ceiling": {"level": "normal_if_physical_readiness_allows"},
            "flags": [],
        },
        "data_freshness": {
            "status": "current",
            "message": "Current.",
            "activity_data": {"status": "current", "message": "Current."},
        },
        "phase": {"name": "base_rebuild"},
        "wearable_coverage": {
            "date": "2026-07-19",
            "classification": {
                "label": "unexplained_internal_unavailability_with_recurring_context",
                "confidence": "high",
            },
            "observed_coverage": {
                "stress": {
                    "sample_count": 334,
                    "valid_sample_count": 184,
                    "sentinel_sample_count": 150,
                    "material_unavailable_run_count": 2,
                    "material_unavailable_minutes": 438.0,
                    "material_unavailable_runs": [],
                },
                "body_battery": {
                    "valid_level_sample_count": 190,
                    "missing_level_sample_count": 144,
                    "material_unavailable_minutes": 423.0,
                },
            },
            "recurring_context": [
                {
                    "label": "reported recurring wearable exception",
                    "reason_category": "church_dress_watch",
                    "occurrence_confirmed_for_date": False,
                    "exact_timing_known": False,
                }
            ],
            "athlete_reported_windows": [],
            "sensor_alignment": [],
            "material_run_attribution": [
                {
                    "start_local": "2026-07-19T07:18+08:00",
                    "end_local": "2026-07-19T14:03+08:00",
                    "duration_minutes": 405.0,
                    "attribution": "unexplained_internal_unavailability",
                    "confirmed_overlap_minutes": 0.0,
                    "unexplained_minutes": 405.0,
                }
            ],
            "attribution_summary": {
                "material_run_count": 2,
                "unattributed_run_count": 2,
                "confirmed_overlap_minutes": 0.0,
                "unexplained_material_minutes": 438.0,
                "recurring_context_is_not_attribution": True,
            },
            "decision_use": {
                "role": "coverage_interpretation_only_never_readiness_clearance",
                "low_stress_positive_reward_eligible": False,
            },
            "safety_contract": {
                "may_impute_stress": False,
                "may_raise_cns_ceiling": False,
            },
            "provenance": {"endpoint_status": "success"},
        },
    }
    plan = {
        "date": "2026-07-19",
        "decision_inputs": {
            "scheduled_rest": {
                "status": "hard_rest",
                "reason": "Sunday Sabbath.",
            }
        },
        "session": {
            "title": "Sabbath rest day",
            "type": "scheduled_rest",
            "duration_min": 0,
            "intensity": "rest",
        },
    }

    packet = build_coach_packet(tmp_path, "2026-07-19", state=state, plan=plan)

    wear = next(
        item
        for item in packet["evidence"]["trusted"]
        if item["name"] == "Wear-state coverage"
    )
    assert wear["decision_use"] == (
        "coverage_interpretation_only_never_readiness_clearance"
    )
    assert wear["value"]["low_stress_positive_reward_eligible"] is False
    assert packet["today_call"]["stance"] == "sabbath_rest"
    assert packet["today_call"]["session"]["title"] == "Sabbath rest day"
    assert any(
        item["source"] == "wearable_coverage"
        for item in packet["evidence"]["cautions"]
    )
    wear_caution = next(
        item
        for item in packet["evidence"]["cautions"]
        if item["source"] == "wearable_coverage"
    )
    assert wear_caution["severity"] == "yellow"
    assert any("Fenix-off start and end" in item for item in packet["next_data_needed"])
    assert "Private" not in json.dumps(packet)


def test_coach_packet_reuses_same_date_state_and_plan_artifacts(tmp_path, monkeypatch):
    load_context(tmp_path)
    write_json(
        tmp_path / "snapshots" / "current_state.json",
        {
            "date": "2026-06-08",
            "readiness": {
                "readiness_level": "yellow",
                "readiness_score": 65,
                "confidence": "medium",
                "hard_session_guidance": "caution",
                "reasons": [],
            },
            "data_freshness": {
                "status": "current",
                "message": "Garmin wellness data is current.",
                "activity_data": {
                    "status": "current",
                    "message": "Recent activity data is available.",
                },
            },
            "phase": {"name": "base_rebuild", "reason": "test"},
        },
    )
    write_json(
        tmp_path / "snapshots" / "today_plan.json",
        {
            "date": "2026-06-08",
            "session": {
                "title": "Easy bike continuity",
                "type": "outdoor_bike_optional",
                "duration_min": 45,
                "intensity": "easy",
            },
        },
    )

    def fail_rebuild(*_args, **_kwargs):
        raise AssertionError("coach packet should reuse same-date artifacts")

    monkeypatch.setattr(coach_packet_module, "build_current_state", fail_rebuild)

    packet = build_coach_packet(tmp_path, "2026-06-08")

    assert packet["date"] == "2026-06-08"
    assert packet["today_call"]["session"]["title"] == "Easy bike continuity"


def test_coach_packet_does_not_promote_stale_predictive_artifact(tmp_path):
    load_context(tmp_path)
    state = {
        "date": "2026-06-08",
        "readiness": {"readiness_level": "yellow", "readiness_score": 65, "reasons": []},
        "data_freshness": {"status": "current", "activity_data": {"status": "current"}},
        "phase": {"name": "base_rebuild"},
    }
    plan = {
        "date": "2026-06-08",
        "session": {
            "title": "Easy bike continuity",
            "type": "outdoor_bike_optional",
            "duration_min": 45,
            "intensity": "easy",
        },
    }
    write_json(
        tmp_path / "snapshots" / "predictive_training.json",
        {
            "date": "2026-06-07",
            "today_prescription": {"prediction": {"stale": "do not use"}},
        },
    )

    packet = build_coach_packet(tmp_path, "2026-06-08", state=state, plan=plan)
    signal = next(item for item in packet["evidence"]["trusted"] if item["name"] == "Predictive training twin")

    assert signal["status"] == "stale"
    assert signal["value"]["today_prediction"] is None


def test_coach_packet_stance_shows_adaptive_upgrade_option(tmp_path):
    load_context(tmp_path)
    state = {
        "date": "2026-06-08",
        "readiness": {
            "readiness_level": "yellow",
            "readiness_score": 68,
            "confidence": "medium",
            "hard_session_guidance": "caution",
            "reasons": [],
        },
        "data_freshness": {
            "status": "current",
            "activity_data": {"status": "current"},
        },
        "phase": {"name": "base_rebuild"},
        "training_status_current": {},
    }
    plan = {
        "date": "2026-06-08",
        "session": {
            "title": "Easy planned trail check",
            "type": "outdoor_mtb",
            "duration_min": 45,
            "intensity": "recovery_skill",
            "adaptive_upgrade_option": {
                "source": "garmin_diagnosis_arbitration",
                "ceiling": "controlled_mtb_repeatability",
            },
        },
        "decision_inputs": {
            "garmin_arbitration": {
                "recommended_action": "controlled_upgrade",
                "summary": "Garmin diagnosis permits a controlled upgrade, not an open-ended hard day.",
            }
        },
    }

    packet = build_coach_packet(tmp_path, "2026-06-08", state=state, plan=plan)

    assert packet["today_call"]["stance"] == "controlled_upgrade_option"
