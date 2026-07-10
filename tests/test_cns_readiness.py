from coach_sync.cns_readiness import build_cns_readiness
from coach_sync.coach_packet import build_coach_packet
from coach_sync.context import load_context
from coach_sync.io import write_json


def _wellness(
    *,
    sleep_score=83,
    body_battery=82,
    current_body_battery=60,
    hrv_status="BALANCED",
    overnight_hrv=52,
    hrv_low=48,
    resting_hr=48,
    rhr_7d_avg=49,
    avg_stress=28,
    sleep_stress=18,
):
    return {
        "date": "2026-06-30",
        "latest": {
            "sleep_score": sleep_score,
            "sleep_hours": 7.5,
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
