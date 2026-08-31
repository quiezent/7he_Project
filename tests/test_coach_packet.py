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


def _environment_evidence(
    *,
    status: str = "current",
    gate: str = "hold_and_recheck",
    severity: str = "yellow",
    forecast_confidence: str = "Low · limited local history",
) -> dict:
    return {
        "artifact_type": "environment_evidence_current",
        "version": "mtb_environment_evidence_adapter_v1",
        "date": "2026-08-28",
        "status": status,
        "source": {
            "name": "Clayton local Bukit Kiara environment evidence",
            "endpoint": "http://192.168.80.147:8765/api/v1/mtb/environment-evidence",
            "fallback": "none",
        },
        "contract_state": {
            "state": "accepted",
            "drift": [],
            "revalidation_needed": False,
            "freshness_independent": True,
        },
        "latest_attempt": {
            "attempted_at": "2026-08-28T09:30:00+08:00",
            "status": "success",
            "evidence_id": "env-20260828-0130",
        },
        "last_known_good": {
            "contract": {
                "schema_version": "1.6.0",
                "revision": "sha256:test-contract",
            },
            "identity": {
                "schema_version": "1.6.0",
                "evidence_id": "env-20260828-0130",
            },
            "scope": {
                "role": "direct_venue_environment_evidence",
                "venue_keys": ["bukit_kiara"],
                "transfer_to_unlisted_venues": False,
            },
            "location": {
                "name": "Taman Tun Dr. Ismail / Bukit Kiara",
                "timezone": "Asia/Kuala_Lumpur",
            },
            "observation": {
                "observed_at_utc": "2026-08-28T01:29:00Z",
                "pm2_5_ug_m3": 47.2,
                "pm10_ug_m3": 58.0,
                "temperature_c": 28.4,
                "relative_humidity_pct": 56.0,
                "heat_index_c": 33.0,
            },
            "particle_nowcast": {
                "state": "rebound",
                "change_30_min_ug_m3": 1.2,
                "change_60_min_ug_m3": 10.2,
                "recheck_minutes": 15,
            },
            "exposure_outlook": {
                "arrival": {
                    "confidence": forecast_confidence,
                    "expected_at_utc": "2026-08-28T03:00:00Z",
                    "persistence_anchor_pm2_5_ug_m3": 47.2,
                    "validation_state": "experimental_not_validated",
                    "likely_range_pm2_5_ug_m3": {"low": 49.8, "high": 81.4},
                    "decision_envelope_pm2_5_ug_m3": {
                        "low": 47.2,
                        "high": 81.4,
                        "calibrated": False,
                    },
                },
                "on_trail": {
                    "confidence": forecast_confidence,
                    "validation_state": "experimental_not_validated",
                    "likely_mean_range_pm2_5_ug_m3": {"low": 47.2, "high": 85.0},
                    "decision_mean_envelope_pm2_5_ug_m3": {
                        "low": 47.2,
                        "high": 85.0,
                        "calibrated": False,
                    },
                },
            },
            "weather": {
                "trail_period": {
                    "apparent_temperature_max_c": 37.4,
                    "precipitation_probability_max_pct": 22,
                }
            },
            "ride_windows": {
                "comparison": {
                    "preferred_window": "morning",
                    "relative_only": True,
                    "ride_approval": False,
                    "verdict": "Morning ranks lower, recheck before departure.",
                },
                "morning": {
                    "recheck": "Recheck at 07:30",
                    "weather_forecast": {"apparent_temperature_max_c": 34.8},
                },
                "afternoon": {
                    "recheck": "Recheck at 12:30",
                    "weather_forecast": {"apparent_temperature_max_c": 38.2},
                },
            },
            "evidence_quality": {"state": "limited", "limitations": ["short_history"]},
            "provenance": {
                "sensor": {
                    "provider": "AirGradient",
                    "location_id": 86311,
                    "observed_at_utc": "2026-08-28T01:29:00Z",
                },
                "weather_forecast": {"provider": "Open-Meteo"},
            },
        },
        "freshness": {"state": status, "age_seconds": 60},
        "decision": {
            "gate": gate,
            "severity": severity,
            "reason": f"Environment gate is {gate}.",
            "reason_codes": ["test_environment_signal"],
            "current_pm2_5_ug_m3": 47.2,
            "forecast_lower_pm2_5_ug_m3": 49.8,
            "forecast_upper_pm2_5_ug_m3": 85.0,
            "forecast_confidence": {
                "arrival": forecast_confidence,
                "on_trail": forecast_confidence,
            },
            "recheck_minutes": 15,
            "decision_role": "outdoor_downshift_or_hold_only_never_training_promotion",
            "can_promote_training": False,
        },
        "guardrail": "Environment evidence cannot promote training.",
    }


def _environment_packet_state(environment: dict) -> dict:
    return {
        "date": "2026-08-28",
        "readiness": {
            "readiness_level": "green",
            "readiness_score": 82,
            "confidence": "medium",
            "reasons": [],
        },
        "data_freshness": {
            "status": "current",
            "activity_data": {"status": "current"},
            "hard_session_limiters": [],
        },
        "phase": {"name": "base_rebuild"},
        "cns_readiness": {},
        "training_status_current": {},
        "environment_evidence": environment,
    }


def _environment_packet_plan(
    *,
    applied: list[dict] | None = None,
    applicability: dict | None = None,
) -> dict:
    return {
        "date": "2026-08-28",
        "session": {
            "title": "Easy bike continuity",
            "type": "outdoor_bike_optional",
            "duration_min": 60,
            "intensity": "easy",
        },
        "decision_inputs": {
            "garmin_arbitration": {},
            "environment_evidence": {
                "plan_applicability": applicability
                or {
                    "status": "current_observation_applicable",
                    "target_date": "2026-08-28",
                    "can_promote_training": False,
                    "windows": [],
                }
            },
        },
        "constraint_resolution": {"applied": applied or []},
    }


def _forecast_applicability() -> dict:
    def window(name: str, start: str, end: str) -> dict:
        return {
            "name": name,
            "target_date": "2026-08-29",
            "ride_window": "09:00-13:00" if name == "morning" else "14:00-18:00",
            "modeled_session": "09:00-11:00" if name == "morning" else "14:00-16:00",
            "current_conditions_applicable": False,
            "recheck": f"Recheck {name}",
            "particle_forecast": {
                "validation_state": "experimental_not_validated",
                "mean_range_pm2_5_ug_m3": {
                    "low": 20.0,
                    "high": 90.0,
                    "calibrated": False,
                    "role": "conservative_persistence_envelope",
                },
                "upper_peak_pm2_5_ug_m3": 105.0,
            },
            "weather_forecast": {
                "start_at_utc": start,
                "end_at_utc": end,
                "thunderstorm": {
                    "level": "likely",
                    "rank": 2,
                    "label": "Thunderstorm signal elevated",
                    "basis": "Issued convection supports a thunderstorm signal.",
                    "used_for_decision": True,
                },
            },
        }

    return {
        "status": "forecast_windows_time_unspecified",
        "target_date": "2026-08-29",
        "reason": "Morning and afternoon remain separate forecast candidates.",
        "can_promote_training": False,
        "preferred_window": "morning",
        "preferred_window_relative_only": True,
        "ride_approval": False,
        "applicable_window_names": [],
        "windows": [
            window("morning", "2026-08-29T01:00:00Z", "2026-08-29T03:00:00Z"),
            window("afternoon", "2026-08-29T06:00:00Z", "2026-08-29T08:00:00Z"),
        ],
    }


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


def test_coach_packet_surfaces_compact_environment_signal_and_hold_cautions(tmp_path):
    environment = _environment_evidence()
    packet = build_coach_packet(
        tmp_path,
        "2026-08-28",
        state=_environment_packet_state(environment),
        plan=_environment_packet_plan(),
    )

    signal = next(
        item
        for item in packet["evidence"]["trusted"]
        if item["name"] == "Bukit Kiara environment evidence"
    )
    assert signal["status"] == "current"
    assert signal["can_promote_training"] is False
    assert signal["value"]["can_promote_training"] is False
    assert signal["value"]["evidence"]["current_pm2_5_ug_m3"] == 47.2
    assert signal["value"]["evidence"]["evidence_id"] == "env-20260828-0130"
    assert signal["value"]["contract"] == {
        "schema_version": "1.6.0",
        "revision": "sha256:test-contract",
        "drift": [],
        "revalidation_needed": False,
        "freshness_independent": True,
    }
    assert signal["value"]["immediate"] == {
        "arrival_at_utc": "2026-08-28T03:00:00Z",
        "persistence_anchor_pm2_5_ug_m3": 47.2,
        "arrival_decision_envelope_pm2_5_ug_m3": {
            "low": 47.2,
            "high": 81.4,
            "calibrated": False,
        },
        "trail_decision_envelope_pm2_5_ug_m3": {
            "low": 47.2,
            "high": 85.0,
            "calibrated": False,
        },
        "validation": {
            "arrival": "experimental_not_validated",
            "on_trail": "experimental_not_validated",
        },
        "recheck_minutes": 15,
    }
    assert (
        packet["artifacts"]["source_environment_evidence"]
        == "snapshots/environment_evidence.json"
    )
    assert any(
        item["source"] == "environment_evidence"
        and item["type"] == "environment_limited"
        and item["can_promote_training"] is False
        for item in packet["evidence"]["cautions"]
    )
    assert any(
        item["source"] == "environment_evidence"
        and item["type"] == "environment_hold"
        for item in packet["evidence"]["cautions"]
    )
    assert packet["today_call"]["stance"] == "aerobic_continuity"


def test_coach_packet_surfaces_exact_date_forecast_without_turning_it_into_clearance(
    tmp_path,
):
    environment = _environment_evidence()
    packet = build_coach_packet(
        tmp_path,
        "2026-08-29",
        state=_environment_packet_state(environment),
        plan=_environment_packet_plan(applicability=_forecast_applicability()),
    )

    signal = next(
        item
        for item in packet["evidence"]["trusted"]
        if item["name"] == "Bukit Kiara environment evidence"
    )["value"]
    morning, afternoon = signal["forecast_windows"]
    assert morning["target_date"] == afternoon["target_date"] == "2026-08-29"
    assert morning["weather_start_at_utc"] == "2026-08-29T01:00:00Z"
    assert morning["mean_decision_envelope_pm2_5_ug_m3"]["high"] == 90.0
    assert morning["upper_peak_pm2_5_ug_m3"] == 105.0
    assert afternoon["heat_index_max_c"] == 38.2
    assert morning["thunderstorm"]["level"] == "likely"
    assert signal["comparison"]["preferred_window"] == "morning"
    assert signal["comparison"]["relative_only"] is True
    assert signal["comparison"]["ride_approval"] is False
    assert any(
        item["type"] == "environment_forecast_experimental_unvalidated"
        for item in packet["evidence"]["cautions"]
    )
    assert any(
        item["type"] == "environment_thunderstorm_hold"
        for item in packet["evidence"]["cautions"]
    )
    assert packet["today_call"]["stance"] == "aerobic_continuity"


def test_coach_packet_uses_planner_scope_for_denai_peladang_isolation(tmp_path):
    for status in (
        "venue_specific_evidence_unavailable",
        "multi_venue_choice_unresolved",
    ):
        applicability = _forecast_applicability()
        applicability.update(
            {
                "status": status,
                "reason": (
                    "The endpoint is direct Bukit Kiara/TTDI evidence; it cannot clear or "
                    "close Denai Peladang or an unresolved Kiara/DP choice."
                ),
                "venue_applicability": {
                    "bukit_kiara": "endpoint_evidence_available_subject_to_date",
                    "denai_peladang": "venue_specific_evidence_unavailable",
                },
            }
        )
        packet = build_coach_packet(
            tmp_path,
            "2026-08-29",
            state=_environment_packet_state(_environment_evidence()),
            plan=_environment_packet_plan(applicability=applicability),
        )

        signal_entry = next(
            item
            for item in packet["evidence"]["trusted"]
            if item["name"] == "Bukit Kiara environment evidence"
        )
        signal = signal_entry["value"]
        assert "cannot clear or close Denai Peladang" in signal_entry["message"]
        assert signal["forecast_windows"] == []
        assert signal["immediate"] == {"status": "not_applicable"}
        assert signal["decision"] == {
            "status": "not_applicable",
            "can_promote_training": False,
        }
        assert signal["comparison"] == {"status": "not_applicable"}
        environment_caution_types = {
            item["type"]
            for item in packet["evidence"]["cautions"]
            if item.get("source") == "environment_evidence"
        }
        assert environment_caution_types == {
            "environment_scope_unavailable_for_venue"
        }


def test_thunderstorm_caution_requires_decision_use(tmp_path):
    applicability = _forecast_applicability()
    for window in applicability["windows"]:
        window["weather_forecast"]["thunderstorm"]["used_for_decision"] = False

    packet = build_coach_packet(
        tmp_path,
        "2026-08-29",
        state=_environment_packet_state(_environment_evidence()),
        plan=_environment_packet_plan(applicability=applicability),
    )

    assert not any(
        item["type"] == "environment_thunderstorm_hold"
        for item in packet["evidence"]["cautions"]
    )


def test_nearby_thunderstorm_caution_requires_fresh_evidence(tmp_path):
    environment = _environment_evidence()
    nearby = {
        "fresh": False,
        "level": "likely",
        "rank": 2,
        "label": "Nearby thunderstorm signal elevated",
        "used_for_decision": True,
    }
    environment["last_known_good"]["weather"]["nearby_storm"] = nearby
    state = _environment_packet_state(environment)
    plan = _environment_packet_plan()

    stale_packet = build_coach_packet(
        tmp_path, "2026-08-28", state=state, plan=plan
    )
    assert not any(
        item["type"] == "environment_thunderstorm_hold"
        for item in stale_packet["evidence"]["cautions"]
    )

    nearby["fresh"] = True
    fresh_packet = build_coach_packet(
        tmp_path, "2026-08-28", state=state, plan=plan
    )
    assert any(
        item["type"] == "environment_thunderstorm_hold"
        for item in fresh_packet["evidence"]["cautions"]
    )


def test_coach_packet_suppresses_session_environment_cautions_for_rest(tmp_path):
    plan = _environment_packet_plan(
        applicability={
            "status": "venue_specific_evidence_unavailable",
            "target_date": "2026-08-28",
            "reason": "No outdoor venue is selected.",
            "can_promote_training": False,
            "windows": [],
        }
    )
    plan["session"] = {
        "title": "Sabbath rest day",
        "type": "scheduled_rest",
        "duration_min": 0,
        "intensity": "rest",
    }

    packet = build_coach_packet(
        tmp_path,
        "2026-08-28",
        state=_environment_packet_state(
            _environment_evidence(
                gate="close_mtb_prolonged_endurance_high_ventilation",
                severity="red",
            )
        ),
        plan=plan,
    )

    assert not any(
        item.get("source") == "environment_evidence"
        for item in packet["evidence"]["cautions"]
    )


def test_coach_packet_surfaces_missing_exact_date_forecast(tmp_path):
    applicability = {
        "status": "forecast_unavailable_recheck",
        "target_date": "2026-08-30",
        "reason": "No exact structured Bukit Kiara forecast window matches this plan date.",
        "can_promote_training": False,
        "windows": [],
    }
    packet = build_coach_packet(
        tmp_path,
        "2026-08-30",
        state=_environment_packet_state(_environment_evidence()),
        plan=_environment_packet_plan(applicability=applicability),
    )

    assert any(
        item["type"] == "environment_forecast_date_unavailable"
        for item in packet["evidence"]["cautions"]
    )


def test_coach_packet_surfaces_environment_contract_drift(tmp_path):
    environment = _environment_evidence()
    environment["contract_state"].update(
        {
            "state": "changed_revalidation_needed",
            "drift": ["live_revision_differs_from_configured"],
            "revalidation_needed": True,
        }
    )
    packet = build_coach_packet(
        tmp_path,
        "2026-08-28",
        state=_environment_packet_state(environment),
        plan=_environment_packet_plan(),
    )

    signal = next(
        item
        for item in packet["evidence"]["trusted"]
        if item["name"] == "Bukit Kiara environment evidence"
    )["value"]
    assert signal["contract"]["drift"] == ["live_revision_differs_from_configured"]
    assert signal["contract"]["revalidation_needed"] is True
    assert any(
        item["type"] == "environment_contract_revalidation_required"
        for item in packet["evidence"]["cautions"]
    )


def test_coach_packet_cautions_for_unavailable_and_stale_environment(tmp_path):
    cases = (
        ("historical_unavailable", "environment_unavailable"),
        ("stale", "environment_stale"),
    )
    for status, expected_type in cases:
        environment = _environment_evidence(
            status=status,
            gate=(
                "historical_environment_unavailable"
                if status == "historical_unavailable"
                else "hold_pending_refresh"
            ),
        )
        packet = build_coach_packet(
            tmp_path,
            "2026-08-28",
            state=_environment_packet_state(environment),
            plan=_environment_packet_plan(),
        )
        assert any(
            item["source"] == "environment_evidence"
            and item["type"] == expected_type
            for item in packet["evidence"]["cautions"]
        )


def test_environment_downshift_stance_requires_applied_planner_constraint(tmp_path):
    environment = _environment_evidence(
        gate="close_mtb_prolonged_endurance_high_ventilation",
        severity="red",
        forecast_confidence="High",
    )
    state = _environment_packet_state(environment)
    no_constraint = build_coach_packet(
        tmp_path,
        "2026-08-28",
        state=state,
        plan=_environment_packet_plan(),
    )
    assert no_constraint["today_call"]["stance"] == "aerobic_continuity"
    assert any(
        item["type"] == "environment_downshift"
        for item in no_constraint["evidence"]["cautions"]
    )

    applied = build_coach_packet(
        tmp_path,
        "2026-08-28",
        state=state,
        plan=_environment_packet_plan(
            applied=[
                {
                    "source": "environment_evidence",
                    "reason": "Planner replaced venue-matched outdoor MTB.",
                }
            ]
        ),
    )
    assert applied["today_call"]["stance"] == "environment_downshift"


def test_coach_packet_surfaces_weekly_accountability_and_flags_unnamed_short_dose(tmp_path):
    state = {
        "date": "2026-08-27",
        "readiness": {
            "readiness_level": "yellow",
            "readiness_score": 65,
            "confidence": "medium",
            "reasons": [],
        },
        "data_freshness": {
            "status": "current",
            "activity_data": {"status": "current"},
            "hard_session_limiters": [],
        },
        "phase": {"name": "base_rebuild"},
        "cns_readiness": {},
        "training_status_current": {},
        "bike_continuity_accountability": {
            "status": "building_toward_preferred",
            "targets": {"preferred_unique_bike_days": 5},
            "current_calendar_week": {"unique_bike_days": 4},
            "rolling_last_7_days": {"unique_bike_days": 5},
            "previous_7_days": {"unique_bike_days": 3},
            "preferred_gap_unique_days": 1,
            "remaining_non_rest_calendar_dates": ["2026-08-28", "2026-08-29"],
            "routine_low_cost_continuity_contract": {
                "total_duration_min": 60,
                "main_power_w_range": [120, 130],
                "global_rpe_range": [2, 3],
            },
            "decision_use": "Accountability only; never training clearance.",
            "provenance": {"targets": "config/athlete_context.json"},
        },
    }
    plan = {
        "date": "2026-08-27",
        "plan_source": {
            "type": "input_planned_session",
            "path": "input/planned_session_2026-08-27.json",
        },
        "constraint_resolution": {"applied": []},
        "decision_inputs": {},
        "session": {
            "title": "Short primer",
            "type": "bike_recovery_primer",
            "modality": "indoor_cycling",
            "duration_min": 20,
            "intensity": "recovery",
            "density_cost": "low",
        },
    }

    packet = build_coach_packet(tmp_path, "2026-08-27", state=state, plan=plan)

    accountability = next(
        item
        for item in packet["evidence"]["trusted"]
        if item["name"] == "Build accountability"
    )
    assert accountability["value"]["current_calendar_week"]["unique_bike_days"] == 4
    assert (
        accountability["value"]["policy_alignment"]["status"]
        == "below_anchor_without_named_constraint"
    )
    assert any(
        item["type"]
        == "low_cost_dose_below_established_anchor_without_named_constraint"
        for item in packet["evidence"]["cautions"]
    )


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


def test_coach_packet_surfaces_named_race_exception_and_replacement_provenance(tmp_path):
    load_context(tmp_path)
    state = {
        "date": "2026-09-20",
        "readiness": {
            "readiness_level": "green",
            "readiness_score": 82,
            "confidence": "medium",
            "reasons": [],
        },
        "data_freshness": {
            "status": "current",
            "activity_data": {"status": "current"},
        },
        "phase": {"name": "race_specific"},
        "training_status_current": {},
    }
    exception = {
        "exception_type": "athlete_authorized_race_event",
        "status": "validated_exact_date_race_event_exception",
        "event": {
            "name": "PDR26",
            "date": "2026-09-20",
            "discipline": "downhill",
            "venue": "Denai Peladang",
        },
        "replacement_sabbath": {
            "date": "2026-09-21",
            "status": "hard_no_exercise",
        },
        "provenance": {
            "source_type": "coach_authored_planned_session",
            "source_path": "input/planned_session_2026-09-20.json",
        },
    }
    plan = {
        "date": "2026-09-20",
        "decision_inputs": {
            "scheduled_rest": {"label": "Sabbath", "status": "hard_rest"},
            "sabbath_exception": exception,
        },
        "session": {
            "title": "PDR26 downhill race",
            "type": "mtb_downhill_race",
            "modality": "mtb",
            "duration_min": 180,
            "intensity": "race",
        },
    }

    packet = build_coach_packet(tmp_path, "2026-09-20", state=state, plan=plan)

    signal = next(
        item
        for item in packet["evidence"]["trusted"]
        if item["name"] == "One-off Sabbath exception"
    )
    assert signal["value"]["provenance"]["source_path"].endswith(
        "planned_session_2026-09-20.json"
    )
    assert "2026-09-21 is the hard replacement sabbath" in signal["message"].lower()


def test_coach_packet_labels_non_sabbath_scheduled_rest_as_recovery(tmp_path):
    load_context(tmp_path)
    state = {
        "date": "2026-08-07",
        "readiness": {
            "readiness_level": "green",
            "readiness_score": 78,
            "confidence": "medium",
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
        "date": "2026-08-07",
        "decision_inputs": {
            "scheduled_rest": None,
            "sabbath_exception": None,
        },
        "session": {
            "title": "Sabah family trip — travel and family activity only",
            "type": "scheduled_rest",
            "modality": "rest",
            "duration_min": 0,
            "intensity": "recovery",
        },
    }

    packet = build_coach_packet(tmp_path, "2026-08-07", state=state, plan=plan)

    assert packet["today_call"]["stance"] == "recovery"


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
                "speed_measurement": {
                    "status": "external_bike_speed_sensor_in_standard_metadata",
                    "external_speed_sensor": True,
                    "battery_statuses": ["GOOD"],
                    "ontology_entity": "measurement_provenance",
                    "interpretation_guardrail": (
                        "A BIKE_SPEED sensor is present, but wheel location is not inferred."
                    ),
                },
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
            "performance_condition": {
                "status": "available",
                "ontology_entity": "physiological_response_context",
                "power_context_basis": ["garmin_activity_summary.avgPower"],
                "context_scope": "matched_stumpjumper_fitness_context",
                "held_trace_observation_count": 5,
                "state_point_count": 4,
                "change_count": 3,
                "first_value": 0,
                "first_elapsed_min": 7.15,
                "final_value": -3,
                "last_elapsed_min": 58.38,
                "minimum": -3,
                "maximum": 0,
                "change_final_minus_first": -3,
                "state_points": [
                    {"elapsed_min": 7.15, "value": 0},
                    {"elapsed_min": 27.07, "value": -1},
                    {"elapsed_min": 34.73, "value": -2},
                    {"elapsed_min": 46.15, "value": -3},
                ],
                "state_points_truncated": False,
                "timing_basis": "sumElapsedDuration_second",
                "descriptor_units": {
                    "directPerformanceCondition": "dimensionless",
                    "sumElapsedDuration": "second",
                    "directTimestamp": "gmt",
                },
                "source": "activities/details/garmin_PRIVATE_ID_detail.json",
                "interpretation_guardrail": "Physiological context only.",
            },
            "self_evaluation": {
                "status": "available",
                "feel_score": 75,
                "feel_label": "strong",
                "feel_out_of_5": 4,
                "feel_ordinal_display_out_of_10": 8,
                "feel_display_remap": "ordinal_display_only_not_comparable_to_rpe",
                "feel_construct": "athlete_state_composite",
                "rpe_score": 30,
                "rpe_label": "moderate",
                "rpe_out_of_10": 3,
                "global_rpe_out_of_10": 3,
                "ontology": {
                    "feel_entity": "athlete_state",
                    "rpe_entity": "delivered_session_effort",
                    "separation_rule": "No safety outcome is inferred.",
                },
                "latest_attempt": {"status": "success", "private": "omit"},
                "source": "snapshots/activity_self_evaluation_index.json",
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
    assert compact["performance_condition"]["first_value"] == 0
    assert compact["performance_condition"]["final_value"] == -3
    assert compact["performance_condition"]["power_context_basis"] == [
        "garmin_activity_summary.avgPower"
    ]
    assert compact["performance_condition"]["provenance"]["source_surface"] == (
        "latest_session_evidence.performance_condition"
    )
    assert compact["performance_condition"]["provenance"]["named_metric"] == (
        "directPerformanceCondition"
    )
    assert compact["speed_measurement"]["external_speed_sensor"] is True
    assert compact["speed_measurement"]["provenance"]["standard_sensor_type"] == (
        "BIKE_SPEED"
    )
    assert compact["self_evaluation"]["feel_out_of_5"] == 4
    assert compact["self_evaluation"]["global_rpe_out_of_10"] == 3
    assert compact["self_evaluation"]["provenance"]["latest_attempt"] == {
        "status": "success"
    }
    serialized = json.dumps(compact)
    assert "PRIVATE_ID" not in serialized
    assert "PRIVATE_SERIAL" not in serialized
    assert '"private"' not in serialized


def test_coach_packet_surfaces_bounded_subjective_evaluation_without_inferring_safety(
    tmp_path,
):
    state = {
        "date": "2026-08-31",
        "readiness": {
            "readiness_level": "green",
            "readiness_score": 80,
            "confidence": "medium",
            "reasons": [],
        },
        "data_freshness": {
            "status": "current",
            "activity_data": {"status": "current"},
            "hard_session_limiters": [],
        },
        "phase": {"name": "base_rebuild"},
        "cns_readiness": {},
        "training_status_current": {},
        "latest_session_response": {
            "date": "2026-08-31",
            "activity_id": "24179129115",
            "status": "garmin_self_evaluation_only",
            "global_rpe_0_to_10": 3,
            "local_rpe_0_to_10": None,
            "subjective_evaluation": {
                "status": "available",
                "activity_id": "24179129115",
                "date": "2026-08-31",
                "validation": {"status": "matched", "usable": True},
                "garmin_feel": {
                    "raw_score_0_to_100": 75,
                    "out_of_5": 4,
                    "ordinal_display_out_of_10": 8,
                    "display_remap": "ordinal_display_only_not_comparable_to_rpe",
                    "construct": "athlete_state_composite",
                    "components": ["clarity", "strength", "coordination"],
                },
                "garmin_perceived_effort": {
                    "raw_score_10_to_100": 30,
                    "global_rpe_0_to_10": 3,
                    "construct": "delivered_session_effort",
                },
                "ontology_guardrail": (
                    "Illness, technical execution, and safety remain separate."
                ),
                "source": "snapshots/activity_self_evaluation_index.json",
            },
            "routine_review": {
                "status": "complete",
                "duplicate_general_questionnaire_required": False,
            },
            "subjective_tolerance_policy": {
                "reference_action": {
                    "threshold_met": True,
                    "exact_identifier_match": True,
                },
                "above_reference_prerequisite": {
                    "threshold_met": True,
                    "necessary_not_sufficient": True,
                },
            },
            "safety_contract_outcome": {
                "status": "unknown",
                "outcome": None,
                "explicit_canonical_outcome": False,
            },
            "illness_airway": {
                "status": "observed",
                "illness_status": "absent",
                "airway_symptoms": {"sore_throat": "present"},
            },
            "technical_execution": {"status": "unknown"},
            "manual_feedback": {"status": "feedback_missing"},
            "stop_rule_outcome": None,
            "stop_rule_outcome_explicit": False,
            "symptom": {"character": "unknown"},
            "decision_use": {
                "classification": (
                    "subjective_session_response_available_safety_unknown"
                ),
                "classification_scope": "symptom_response",
                "illness_airway_caution": {
                    "status": "present",
                    "reasons": ["airway_symptom_present_sore_throat"],
                    "training_promotion_allowed": False,
                    "guardrail": (
                        "Reported airway symptoms remain a caution even when Garmin Feel is favorable."
                    ),
                },
                "review_summary": {
                    "routine_review": "complete",
                    "illness_airway": "observed",
                    "technical_execution": "unknown",
                    "safety_contract_outcome": "unknown",
                },
            },
            "provenance": {
                "target_date": "2026-08-31",
                "requested_activity_id": "24179129115",
                "selected_activity_id": "24179129115",
                "selection_rule": "activity_id_then_latest_timestamp",
            },
        },
    }
    plan = {
        "date": "2026-08-31",
        "session": {
            "title": "Post-session rest",
            "type": "scheduled_recovery",
            "duration_min": 0,
            "intensity": "rest",
        },
        "decision_inputs": {},
        "constraint_resolution": {"applied": []},
    }

    packet = build_coach_packet(tmp_path, "2026-08-31", state=state, plan=plan)
    response = packet["today_call"]["latest_session_response"]

    assert response["subjective_evaluation"]["garmin_feel"]["out_of_5"] == 4
    assert response["date"] == "2026-08-31"
    assert response["activity_id"] == "24179129115"
    assert (
        response["subjective_evaluation"]["garmin_perceived_effort"][
            "global_rpe_0_to_10"
        ]
        == 3
    )
    assert response["subjective_evaluation"]["provenance"]["source_surface"] == (
        "latest_session_response.subjective_evaluation"
    )
    assert response["routine_review"]["status"] == "complete"
    assert response["safety_contract_outcome"]["status"] == "unknown"
    assert response["illness_airway"]["illness_status"] == "absent"
    assert response["decision_use"]["illness_airway_caution"] == {
        "status": "present",
        "reasons": ["airway_symptom_present_sore_throat"],
        "training_promotion_allowed": False,
        "guardrail": (
            "Reported airway symptoms remain a caution even when Garmin Feel is favorable."
        ),
    }
    assert response["subjective_tolerance_policy"]["above_reference_prerequisite"][
        "necessary_not_sufficient"
    ] is True
    assert response["stop_rule_outcome"] is None
    assert response["stop_rule_outcome_explicit"] is False
    assert not any(
        "Label what the Fenix cannot see" in item
        or "Label CNS/technical sharpness" in item
        for item in packet["next_data_needed"]
    )


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


def test_coach_packet_surfaces_adaptive_programming_controller(tmp_path):
    load_context(tmp_path)
    adaptive = {
        "status": "ready",
        "date": "2026-08-27",
        "roadmap_block": {"label": "Absorption", "program_mode": "absorption"},
        "progression_decision": {
            "program_action": "absorb_and_hold",
            "active_lever": "bike_specific_continuity",
        },
        "weekly_budget": {"meaningful_cost_days_remaining": 0},
        "progression_tracks": {"engine": {"torque": {"decision": "hold_no_promotion"}}},
        "recommended_week_roles": [],
        "programming_audit": {"status": "on_track", "items": []},
    }
    state = {
        "date": "2026-08-27",
        "readiness": {"readiness_level": "yellow", "readiness_score": 60, "reasons": []},
        "data_freshness": {"status": "current", "activity_data": {"status": "current"}},
        "phase": {"name": "base_rebuild"},
        "training_status_current": {},
        "adaptive_training": adaptive,
    }
    plan = {
        "date": "2026-08-27",
        "session": {"title": "Easy continuity", "type": "outdoor_bike_optional", "duration_min": 60, "intensity": "easy"},
        "decision_inputs": {"garmin_arbitration": {}},
    }

    packet = build_coach_packet(tmp_path, "2026-08-27", state=state, plan=plan)

    signal = next(item for item in packet["evidence"]["trusted"] if item["name"] == "Adaptive training controller")
    assert signal["status"] == "ready"
    assert signal["value"]["progression_decision"]["active_lever"] == "bike_specific_continuity"
    assert packet["today_call"]["adaptive_programming"]["progression_decision"]["program_action"] == "absorb_and_hold"
