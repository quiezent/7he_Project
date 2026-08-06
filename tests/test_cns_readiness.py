from coach_sync.cns_readiness import build_cns_readiness
from coach_sync.coach_packet import build_coach_packet
from coach_sync.context import load_context
from coach_sync.io import write_json
from coach_sync.time_utils import DEFAULT_TIMEZONE, today_local


def _wellness(
    *,
    day="2026-06-30",
    sleep_score=83,
    sleep_hours=7.5,
    body_battery=82,
    current_body_battery=60,
    hrv_status="BALANCED",
    overnight_hrv=52,
    hrv_low=48,
    resting_hr=48,
    rhr_7d_avg=49,
    avg_stress=28,
    sleep_stress=18,
    source_data_cutoff_local=None,
    source_data_cutoff_source=None,
):
    return {
        "date": day,
        "latest": {
            "sleep_score": sleep_score,
            "sleep_hours": sleep_hours,
            "sleep_stress": sleep_stress,
            "body_battery_wake": body_battery,
            "body_battery_verified_morning_anchor": body_battery,
            "body_battery_current": current_body_battery,
            "hrv_status": hrv_status,
            "overnight_hrv": overnight_hrv,
            "hrv_balanced_low": hrv_low,
            "resting_hr": resting_hr,
            "rhr_7d_avg": rhr_7d_avg,
            "avg_stress": avg_stress,
            "source_data_cutoff_local": source_data_cutoff_local,
            "source_data_cutoff_source": source_data_cutoff_source,
        },
    }


def test_cns_readiness_downshifts_brain_fog_despite_body_battery_rebound(tmp_path):
    load_context(tmp_path)
    write_json(
        tmp_path / "input" / "feedback_2026-06-30.json",
        {
            "date": "2026-06-30",
            "entries": [
                {
                    "subjective_report": (
                        "Back to the groove but brain still has fog. "
                        "Likely CNS fatigue after back-to-back MTB exposure."
                    )
                }
            ],
        },
    )

    report = build_cns_readiness(
        tmp_path,
        "2026-06-30",
        wellness_trends=_wellness(hrv_status="LOW", overnight_hrv=47),
        training_status_current={"date": "2026-06-30", "training_status_feedback": "STRAINED_3"},
        training_load={
            "latest_training_activity": {
                "date": "2026-06-30",
                "category": "mtb",
                "duration_min": 84.1,
                "training_load": 128.2,
                "hr_zone_min": {"z4": 12.2, "z5": 0.0},
            }
        },
        self_evaluation={
            "recent_self_evaluations": [
                {
                    "date": "2026-06-30",
                    "feel_label": "weak",
                    "rpe_score": 30,
                }
            ]
        },
    )

    assert report["status"] in {"impaired", "compromised"}
    assert report["session_ceiling"]["level"] in {"recovery_only", "low_consequence_repetition_only"}
    assert any(item["type"] == "brain_fog" for item in report["signals"]["subjective"])
    assert (tmp_path / "snapshots" / "cns_readiness_2026-06-30.json").exists()


def test_cns_readiness_no_brain_fog_phrase_does_not_trigger_negative_fog(tmp_path):
    load_context(tmp_path)
    write_json(
        tmp_path / "input" / "feedback_2026-06-30.json",
        {
            "date": "2026-06-30",
            "entries": [
                {
                    "subjective_report": "Feeling better, no brain fog, motivated, rhythm is back."
                }
            ],
        },
    )

    report = build_cns_readiness(
        tmp_path,
        "2026-06-30",
        wellness_trends=_wellness(body_battery=86, hrv_status="BALANCED", overnight_hrv=55),
        training_status_current={"date": "2026-06-30", "training_status_feedback": "PRODUCTIVE_1"},
        training_load={},
        self_evaluation={},
    )

    assert report["status"] == "ready"
    assert any(item["type"] == "no_brain_fog" for item in report["signals"]["subjective"])
    assert not any(
        item["type"] == "brain_fog" and item["direction"] == "negative"
        for item in report["signals"]["subjective"]
    )


def test_cns_readiness_no_current_brain_fog_is_protected_negation(tmp_path):
    write_json(
        tmp_path / "input" / "feedback_2026-07-25.json",
        {
            "date": "2026-07-25",
            "entries": [
                {
                    "subjective_report": (
                        "Mental clarity is great and there is no current brain fog."
                    )
                }
            ],
        },
    )
    report = build_cns_readiness(
        tmp_path,
        "2026-07-25",
        wellness_trends={
            "date": "2026-07-25",
            "latest": {
                "date": "2026-07-25",
                "sleep_hours": 7.5,
                "sleep_score": 85,
                "hrv_status": "BALANCED",
            },
        },
    )

    assert any(
        item["type"] == "no_brain_fog" and item["direction"] == "positive"
        for item in report["signals"]["subjective"]
    )
    assert not any(
        item["type"] == "brain_fog" and item["direction"] == "negative"
        for item in report["signals"]["subjective"]
    )


def test_cns_readiness_carries_recent_mtb_cost_into_next_morning(tmp_path):
    load_context(tmp_path)

    report = build_cns_readiness(
        tmp_path,
        "2026-06-30",
        wellness_trends=_wellness(body_battery=78, hrv_status="BALANCED", overnight_hrv=54),
        training_status_current={"date": "2026-06-30", "training_status_feedback": "PRODUCTIVE_1"},
        training_load={
            "recent_technical_activities": [
                {
                    "date": "2026-06-29",
                    "category": "mtb",
                    "duration_min": 90,
                    "training_load": 150,
                    "hr_zone_min": {"z4": 14, "z5": 2},
                }
            ]
        },
        self_evaluation={},
    )

    signals = report["signals"]["latest_activity"]
    assert any(signal["type"] == "mtb_duration_cost" and signal["age_days"] == 1 for signal in signals)
    assert any(signal["type"] == "mtb_load_cost" and signal["age_days"] == 1 for signal in signals)
    assert any(driver["source"] == "recent_technical_activity" for driver in report["drivers"])


def test_cns_readiness_does_not_score_stale_training_status_as_current(tmp_path):
    load_context(tmp_path)

    report = build_cns_readiness(
        tmp_path,
        "2026-06-30",
        wellness_trends=_wellness(),
        training_status_current={"date": "2026-06-29", "training_status_feedback": "STRAINED_3"},
        training_load={},
        self_evaluation={},
    )

    assert not any(driver["source"] == "training_status" for driver in report["drivers"])
    assert report["inputs"]["training_status"]["training_status_feedback"] is None


def test_cns_primary_short_sleep_caps_technical_consequence_despite_positive_garmin(tmp_path):
    load_context(tmp_path)

    report = build_cns_readiness(
        tmp_path,
        "2026-06-30",
        wellness_trends=_wellness(
            sleep_score=73,
            sleep_hours=5.53,
            body_battery=82,
            hrv_status="BALANCED",
            source_data_cutoff_local="2026-06-30T19:00:00+08:00",
            source_data_cutoff_source="daily_summary.wellnessEndTimeLocal",
        ),
        training_status_current={"date": "2026-06-30", "training_status_feedback": "PRODUCTIVE_1"},
        training_load={},
        self_evaluation={},
    )

    assert report["status"] == "watch"
    assert report["session_ceiling"]["level"] == "controlled_skill_only"
    assert report["score"] <= 74
    assert report["signals"]["sleep"] == [
        {
            "type": "primary_short_sleep",
            "direction": "negative",
            "core_sleep_hours": 5.53,
            "duration_band": "5_5_to_under_6_hours",
            "technical_consequence_ceiling": "controlled_skill_only",
        }
    ]
    assert any(item["type"] == "primary_short_sleep" for item in report["flags"])
    assert any("nap" in item["reason"].lower() for item in report["drivers"])


def test_cns_isolated_five_hour_sleep_is_watch_not_automatic_compromise(tmp_path):
    load_context(tmp_path)
    write_json(
        tmp_path / "input" / "feedback_2026-06-30.json",
        {
            "date": "2026-06-30",
            "entries": [
                {
                    "subjective_report": (
                        "Mental clarity and body feel are great, with no brain fog or illness."
                    )
                }
            ],
        },
    )

    report = build_cns_readiness(
        tmp_path,
        "2026-06-30",
        wellness_trends=_wellness(
            sleep_score=66,
            sleep_hours=5.03,
            body_battery=75,
            current_body_battery=48,
            hrv_status="BALANCED",
            overnight_hrv=50,
            resting_hr=46,
            rhr_7d_avg=48,
        ),
        training_status_current={"date": "2026-06-30", "training_status_feedback": "DETRAINING"},
        training_load={},
        self_evaluation={},
    )

    assert report["status"] == "watch"
    assert report["score"] <= 74
    assert report["session_ceiling"]["level"] == "controlled_skill_only"
    assert report["signals"]["sleep"][0]["duration_band"] == "5_to_under_5_5_hours"
    assert report["signals"]["sleep"][0]["technical_consequence_ceiling"] == (
        "controlled_skill_only"
    )


def test_cns_uses_poor_post_rest_cognition_only_as_a_downward_gate(tmp_path):
    load_context(tmp_path)

    report = build_cns_readiness(
        tmp_path,
        "2026-06-30",
        wellness_trends=_wellness(),
        training_status_current={"date": "2026-06-30", "training_status_feedback": "PRODUCTIVE_1"},
        training_load={},
        self_evaluation={},
        rest_recharge_window={
            "date": "2026-06-30",
            "classification": {"signals": {"subjective_negative": True}},
            "window": {"sleep_inertia_minutes": 50, "post_clarity_low_10": 4},
            "context": {"illness": {"status": "unknown"}},
        },
    )

    assert report["status"] in {"impaired", "compromised"}
    assert report["session_ceiling"]["level"] in {
        "recovery_only",
        "low_consequence_repetition_only",
    }
    assert any(
        signal["type"] == "poor_post_rest_cognition"
        for signal in report["signals"]["rest_recharge"]
    )


def test_cns_ignores_positive_rest_and_body_battery_for_upward_clearance(tmp_path):
    load_context(tmp_path)
    inputs = {
        "wellness_trends": _wellness(sleep_hours=5.53, sleep_score=73),
        "training_status_current": {},
        "training_load": {},
        "self_evaluation": {},
    }
    baseline = build_cns_readiness(tmp_path, "2026-06-30", **inputs)
    with_rest = build_cns_readiness(
        tmp_path,
        "2026-06-30",
        **inputs,
        rest_recharge_window={
            "date": "2026-06-30",
            "classification": {
                "label": "restorative",
                "signals": {
                    "quiet_stress": True,
                    "body_battery_recharge": True,
                    "subjective_positive": True,
                    "subjective_negative": False,
                },
            },
            "objective_response": {"body_battery": {"delta_during": 20}},
            "context": {"illness": {"status": "none"}},
        },
    )

    assert with_rest["score"] == baseline["score"]
    assert with_rest["status"] == baseline["status"]
    assert with_rest["session_ceiling"] == baseline["session_ceiling"]
    assert with_rest["inputs"]["rest_recharge_negative_only"][
        "positive_evidence_decision_use"
    ] == "ignored_for_upward_clearance"


def test_cns_active_illness_from_rest_context_forces_recovery_ceiling(tmp_path):
    load_context(tmp_path)

    report = build_cns_readiness(
        tmp_path,
        "2026-06-30",
        wellness_trends=_wellness(),
        training_status_current={"date": "2026-06-30", "training_status_feedback": "PRODUCTIVE_1"},
        training_load={},
        self_evaluation={},
        rest_recharge_window={
            "date": "2026-06-30",
            "classification": {
                "label": "restorative",
                "signals": {"subjective_negative": False},
            },
            "context": {"illness": {"status": "active"}},
        },
    )

    assert report["status"] == "impaired"
    assert report["session_ceiling"]["level"] == "recovery_only"
    assert any(
        signal["type"] == "illness_active"
        for signal in report["signals"]["rest_recharge"]
    )


def test_cns_partial_morning_stress_withholds_reward_but_high_stress_still_penalizes(tmp_path):
    load_context(tmp_path)
    partial = _wellness(
        avg_stress=24,
        source_data_cutoff_local="2026-06-30T09:55:00+08:00",
        source_data_cutoff_source="daily_summary.wellnessEndTimeLocal",
    )

    low_report = build_cns_readiness(
        tmp_path,
        "2026-06-30",
        wellness_trends=partial,
        training_status_current={},
        training_load={},
        self_evaluation={},
    )
    high_report = build_cns_readiness(
        tmp_path,
        "2026-06-30",
        wellness_trends={
            **partial,
            "latest": {**partial["latest"], "avg_stress": 45},
        },
        training_status_current={},
        training_load={},
        self_evaluation={},
    )

    coverage = low_report["inputs"]["wellness"]["daily_stress_coverage"]
    assert coverage["positive_reward_eligible"] is False
    assert coverage["cutoff_local"] == "2026-06-30T09:55:00+08:00"
    assert any(
        item["source"] == "wellness_coverage"
        and item["points"] == 0
        and "withheld" in item["reason"].lower()
        for item in low_report["drivers"]
    )
    assert any(item["type"] == "partial_daily_stress_coverage" for item in low_report["flags"])
    assert any(
        item["source"] == "wellness" and item["points"] == -8 and "stress" in item["reason"].lower()
        for item in high_report["drivers"]
    )
    assert not any(item["source"] == "wellness_coverage" for item in high_report["drivers"])


def test_cns_internal_wear_gap_withholds_low_stress_reward_after_evening_cutoff(tmp_path):
    load_context(tmp_path)
    full_cutoff = _wellness(
        avg_stress=16,
        source_data_cutoff_local="2026-06-30T20:00:00+08:00",
        source_data_cutoff_source="daily_summary.wellnessEndTimeLocal",
    )
    complete = build_cns_readiness(
        tmp_path,
        "2026-06-30",
        wellness_trends=full_cutoff,
        training_status_current={},
        training_load={},
        self_evaluation={},
        wearable_coverage={
            "date": "2026-06-30",
            "classification": {"label": "no_material_internal_unavailability"},
            "observed_coverage": {
                "stress": {"material_unavailable_minutes": 0.0}
            },
            "decision_use": {"low_stress_positive_reward_eligible": True},
        },
    )
    gapped = build_cns_readiness(
        tmp_path,
        "2026-06-30",
        wellness_trends=full_cutoff,
        training_status_current={},
        training_load={},
        self_evaluation={},
        wearable_coverage={
            "date": "2026-06-30",
            "classification": {"label": "unexplained_internal_unavailability"},
            "observed_coverage": {
                "stress": {"material_unavailable_minutes": 405.0}
            },
            "decision_use": {"low_stress_positive_reward_eligible": False},
        },
    )
    high = build_cns_readiness(
        tmp_path,
        "2026-06-30",
        wellness_trends={
            **full_cutoff,
            "latest": {**full_cutoff["latest"], "avg_stress": 45},
        },
        training_status_current={},
        training_load={},
        self_evaluation={},
        wearable_coverage={
            "date": "2026-06-30",
            "classification": {"label": "unexplained_internal_unavailability"},
            "observed_coverage": {
                "stress": {"material_unavailable_minutes": 405.0}
            },
            "decision_use": {"low_stress_positive_reward_eligible": False},
        },
    )

    assert gapped["score"] == complete["score"] - 3
    assert gapped["inputs"]["wellness"]["daily_stress_coverage"][
        "wearable_coverage_guard_applied"
    ] is True
    assert any(item["source"] == "wellness_coverage" for item in gapped["drivers"])
    assert any(
        item["source"] == "wellness" and item["points"] == -8
        for item in high["drivers"]
    )


def test_cns_historical_build_prefers_dated_wearable_coverage_and_explicit_override(
    tmp_path,
):
    load_context(tmp_path)
    full_cutoff = _wellness(
        avg_stress=16,
        source_data_cutoff_local="2026-06-30T20:00:00+08:00",
        source_data_cutoff_source="daily_summary.wellnessEndTimeLocal",
    )
    write_json(
        tmp_path / "snapshots" / "wearable_coverage_2026-06-30.json",
        {
            "date": "2026-06-30",
            "classification": {"label": "historical_internal_gap"},
            "observed_coverage": {
                "stress": {"material_unavailable_minutes": 180.0}
            },
            "decision_use": {"low_stress_positive_reward_eligible": False},
        },
    )
    write_json(
        tmp_path / "snapshots" / "wearable_coverage.json",
        {
            "date": "2026-07-19",
            "classification": {"label": "newer_current_alias"},
            "observed_coverage": {
                "stress": {"material_unavailable_minutes": 0.0}
            },
            "decision_use": {"low_stress_positive_reward_eligible": True},
        },
    )

    historical = build_cns_readiness(
        tmp_path,
        "2026-06-30",
        wellness_trends=full_cutoff,
        training_status_current={},
        training_load={},
        self_evaluation={},
    )
    explicit = build_cns_readiness(
        tmp_path,
        "2026-06-30",
        wellness_trends=full_cutoff,
        training_status_current={},
        training_load={},
        self_evaluation={},
        wearable_coverage={
            "date": "2026-06-30",
            "classification": {"label": "explicit_complete_coverage"},
            "observed_coverage": {
                "stress": {"material_unavailable_minutes": 0.0}
            },
            "decision_use": {"low_stress_positive_reward_eligible": True},
        },
    )

    historical_coverage = historical["inputs"]["wellness"]["daily_stress_coverage"]
    assert historical_coverage["wearable_coverage_guard_applied"] is True
    assert historical_coverage["wearable_coverage_classification"] == (
        "historical_internal_gap"
    )
    assert historical["inputs"]["wearable_coverage"]["date"] == "2026-06-30"
    assert explicit["score"] == historical["score"] + 3
    assert explicit["inputs"]["wearable_coverage"]["classification"] == (
        "explicit_complete_coverage"
    )
    assert "wearable_coverage_guard_applied" not in (
        explicit["inputs"]["wellness"]["daily_stress_coverage"]
    )


def test_cns_normalized_wear_gap_fallback_withholds_reward_without_artifact(tmp_path):
    load_context(tmp_path)
    normalized_gap = _wellness(
        avg_stress=16,
        source_data_cutoff_local="2026-06-30T20:00:00+08:00",
        source_data_cutoff_source="daily_summary.wellnessEndTimeLocal",
    )
    normalized_gap["latest"]["all_day_stress_low_positive_reward_eligible"] = False

    low = build_cns_readiness(
        tmp_path,
        "2026-06-30",
        wellness_trends=normalized_gap,
        training_status_current={},
        training_load={},
        self_evaluation={},
    )
    high = build_cns_readiness(
        tmp_path,
        "2026-06-30",
        wellness_trends={
            **normalized_gap,
            "latest": {**normalized_gap["latest"], "avg_stress": 45},
        },
        training_status_current={},
        training_load={},
        self_evaluation={},
    )

    coverage = low["inputs"]["wellness"]["daily_stress_coverage"]
    assert coverage["positive_reward_eligible"] is False
    assert coverage["normalized_wellness_coverage_guard_applied"] is True
    assert any(
        driver["source"] == "wellness_coverage" and driver["points"] == 0
        for driver in low["drivers"]
    )
    assert not any(
        driver["source"] == "wellness" and driver["points"] == 3
        and driver["reason"] == "Daily stress is controlled."
        for driver in low["drivers"]
    )
    assert high["score"] == low["score"] - 8
    assert any(
        driver["source"] == "wellness" and driver["points"] == -8
        and "stress" in driver["reason"].lower()
        for driver in high["drivers"]
    )
    assert not any(driver["source"] == "wellness_coverage" for driver in high["drivers"])


def test_cns_normalized_coverage_false_overrides_same_date_wearable_true(tmp_path):
    load_context(tmp_path)
    normalized_gap = _wellness(
        avg_stress=16,
        source_data_cutoff_local="2026-06-30T20:00:00+08:00",
        source_data_cutoff_source="daily_summary.wellnessEndTimeLocal",
    )
    normalized_gap["latest"]["all_day_stress_low_positive_reward_eligible"] = False

    report = build_cns_readiness(
        tmp_path,
        "2026-06-30",
        wellness_trends=normalized_gap,
        training_status_current={},
        training_load={},
        self_evaluation={},
        wearable_coverage={
            "date": "2026-06-30",
            "classification": {"label": "explicit_complete_coverage"},
            "observed_coverage": {
                "stress": {"material_unavailable_minutes": 0.0}
            },
            "decision_use": {"low_stress_positive_reward_eligible": True},
        },
    )

    coverage = report["inputs"]["wellness"]["daily_stress_coverage"]
    assert coverage["positive_reward_eligible"] is False
    assert coverage["normalized_wellness_coverage_guard_applied"] is True
    assert not any(
        driver["source"] == "wellness"
        and driver["points"] == 3
        and driver["reason"] == "Daily stress is controlled."
        for driver in report["drivers"]
    )
    assert any(
        driver["source"] == "wellness_coverage" and driver["points"] == 0
        for driver in report["drivers"]
    )


def test_cns_same_day_unknown_internal_coverage_withholds_evening_low_stress_reward(
    tmp_path,
):
    load_context(tmp_path)
    target = today_local(DEFAULT_TIMEZONE).isoformat()

    report = build_cns_readiness(
        tmp_path,
        target,
        wellness_trends=_wellness(
            day=target,
            avg_stress=16,
            source_data_cutoff_local=f"{target}T20:00:00+08:00",
            source_data_cutoff_source="daily_summary.wellnessEndTimeLocal",
        ),
        training_status_current={},
        training_load={},
        self_evaluation={},
    )

    coverage = report["inputs"]["wellness"]["daily_stress_coverage"]
    assert coverage["positive_reward_eligible"] is False
    assert coverage["explicit_positive_coverage_guard_applied"] is True
    assert any(
        driver["source"] == "wellness_coverage" and driver["points"] == 0
        for driver in report["drivers"]
    )
    assert not any(
        driver["source"] == "wellness"
        and driver["points"] == 3
        and driver["reason"] == "Daily stress is controlled."
        for driver in report["drivers"]
    )


def test_coach_packet_uses_cns_readiness_as_technical_ceiling(tmp_path):
    load_context(tmp_path)
    state = {
        "date": "2026-06-30",
        "readiness": {
            "readiness_level": "yellow",
            "readiness_score": 66,
            "confidence": "medium",
            "hard_session_guidance": "caution",
            "reasons": [],
        },
        "cns_readiness": {
            "score": 42,
            "status": "impaired",
            "confidence": "high",
            "session_ceiling": {
                "level": "recovery_only",
                "rule": "No technical consequence, no MTB quality, no intervals, and no gym loading.",
            },
            "interpretation": "CNS readiness is impaired; prioritize recovery and avoid technical consequence.",
            "flags": [
                {
                    "type": "cns_downshift",
                    "severity": "yellow",
                    "message": "No technical consequence, no MTB quality, no intervals, and no gym loading.",
                }
            ],
        },
        "data_freshness": {
            "status": "current",
            "activity_data": {"status": "current"},
        },
        "phase": {"name": "base_rebuild"},
        "training_status_current": {},
    }
    plan = {
        "date": "2026-06-30",
        "session": {
            "title": "Kiara MTB quality",
            "type": "mtb_quality_skill",
            "duration_min": 90,
            "intensity": "moderate",
        },
    }

    packet = build_coach_packet(tmp_path, "2026-06-30", state=state, plan=plan)

    assert packet["today_call"]["stance"] == "cns_downshift"
    assert any(item["name"] == "CNS readiness" for item in packet["evidence"]["trusted"])
    assert any(item["source"] == "cns_readiness" for item in packet["evidence"]["cautions"])
