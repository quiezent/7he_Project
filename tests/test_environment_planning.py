from __future__ import annotations

from coach_sync.context import load_context
from coach_sync.io import read_json, write_json
from coach_sync.planning import SESSION_CONTRACT_FIELDS, build_today_plan


DAY = "2026-04-29"


def _state(day: str = DAY) -> dict:
    return {
        "date": day,
        "athlete": {},
        "phase": {"name": "base_rebuild"},
        "readiness": {
            "readiness_level": "green",
            "readiness_score": 85,
            "hard_session_guidance": "allow",
        },
        "data_freshness": {
            "status": "current",
            "hard_session_confidence": "normal",
            "hard_session_limiters": [],
        },
        "cns_readiness": {
            "status": "ready",
            "session_ceiling": {"level": "normal_training"},
        },
    }


def _planned_session(root, day: str = DAY, *, venue: str = "Bukit Kiara", modality: str = "mtb") -> None:
    write_json(
        root / "input" / f"planned_session_{day}.json",
        {
            "artifact_type": "coach_authored_planned_session",
            "date": day,
            "generated_at": f"{day}T06:00:00+08:00",
            "status": "active",
            "session": {
                "title": "Explicit outdoor quality",
                "type": "mtb_quality_skill" if modality == "mtb" else "outdoor_endurance",
                "modality": modality,
                "venue": venue,
                "action_identity": {
                    "venue_key": "bukit_kiara" if venue == "Bukit Kiara" else "other_venue"
                },
                "duration_min": 120,
                "intensity": "moderate_technical",
                "schema_version": 3,
                "contract_fields": SESSION_CONTRACT_FIELDS,
                "purpose": "Train a prolonged outdoor quality exposure.",
                "dose": {"duration_min": 120, "cap": "Two hours maximum."},
                "adaptation_hypothesis": "A bounded outdoor dose develops repeatability.",
                "execution_rules": ["Keep execution controlled."],
                "expected_result": {"technical": "Repeatable quality."},
                "stop_rules": ["Stop if execution degrades."],
                "post_session_review_fields": ["stop_rule_outcome"],
            },
        },
    )


def _environment(
    *,
    pm2_5: float,
    gate: str,
    freshness: str = "current",
    forecast_high: float | None = None,
    forecast_confidence: str = "low",
) -> dict:
    reason = f"Bukit Kiara PM2.5 is {pm2_5:.1f} ug/m3; environment gate is {gate}."
    return {
        "artifact_type": "environment_evidence_current",
        "date": DAY,
        "status": freshness,
        "source": {
            "endpoint": "http://192.168.80.147:8765/api/v1/mtb/environment-evidence",
            "fallback": "none",
        },
        "latest_attempt": {
            "attempted_at": f"{DAY}T08:00:00+08:00",
            "status": "success",
            "error": None,
            "evidence_id": "airgradient-86311-test-v1",
        },
        "last_known_good": {
            "identity": {
                "evidence_id": "airgradient-86311-test-v1",
                "schema_version": "1.0.0",
            },
            "observation": {
                "observed_at_utc": "2026-04-29T00:00:00Z",
                "pm2_5_ug_m3": pm2_5,
            },
            "provenance": {
                "sensor": {
                    "provider": "AirGradient",
                    "location_id": 86311,
                    "stale_after_seconds": 420,
                    "expired_after_seconds": 900,
                }
            },
        },
        "freshness": {
            "state": freshness,
            "age_seconds": 120,
            "stale_after_seconds": 420,
            "expired_after_seconds": 900,
        },
        "decision": {
            "gate": gate,
            "severity": "red" if "close" in gate or "closure" in gate else "yellow",
            "reason": reason,
            "reason_codes": [gate],
            "current_pm2_5_ug_m3": pm2_5,
            "forecast_upper_pm2_5_ug_m3": forecast_high,
            "forecast_confidence": {
                "arrival": forecast_confidence,
                "on_trail": forecast_confidence,
            },
            "recheck_minutes": 15,
            "decision_role": "outdoor_downshift_or_hold_only_never_training_promotion",
            "can_promote_training": False,
            "venue_keys": ["bukit_kiara"],
            "venue_aliases": ["Bukit Kiara", "Kiara", "TTDI"],
        },
        "guardrail": "Environment can hold or downshift only.",
    }


def _assert_schema_v3(session: dict) -> None:
    assert session["schema_version"] == 3
    assert session["contract_fields"] == SESSION_CONTRACT_FIELDS
    for field in SESSION_CONTRACT_FIELDS:
        assert session.get(field), field


def test_fresh_moderate_alias_is_caution_only_and_never_promotes(tmp_path):
    load_context(tmp_path)
    _planned_session(tmp_path, venue="Kiara")
    state = _state()
    state["environment_evidence"] = _environment(
        pm2_5=38.0,
        gate="moderate_caution",
    )

    plan = build_today_plan(tmp_path, DAY, state=state)

    assert plan["session"]["title"] == "Explicit outdoor quality"
    assert not any(
        item.get("source") == "environment_evidence"
        for item in plan["constraint_resolution"]["applied"]
    )
    decision = plan["decision_inputs"]["environment_evidence"]["decision"]
    assert decision["gate"] == "moderate_caution"
    assert decision["can_promote_training"] is False


def test_moderate_or_low_confidence_forecast_crossing_only_holds_for_recheck(tmp_path):
    load_context(tmp_path)
    _planned_session(tmp_path)
    state = _state()
    state["environment_evidence"] = _environment(
        pm2_5=38.0,
        gate="hold_and_recheck",
        forecast_high=84.5,
        forecast_confidence="low",
    )

    plan = build_today_plan(tmp_path, DAY, state=state)

    assert plan["session"]["title"] == "Explicit outdoor quality"
    assert not any(
        item.get("source") == "environment_evidence"
        for item in plan["constraint_resolution"]["applied"]
    )
    environment = plan["decision_inputs"]["environment_evidence"]
    assert environment["decision"]["gate"] == "hold_and_recheck"
    assert environment["decision"]["can_promote_training"] is False
    assert environment["evidence"]["evidence_id"] == "airgradient-86311-test-v1"
    assert any("hold_and_recheck" in item for item in plan["guardrails"])


def test_fresh_poor_pm_replaces_explicit_kiara_mtb_with_established_indoor_fallback(tmp_path):
    load_context(tmp_path)
    _planned_session(tmp_path)
    state = _state()
    state["environment_evidence"] = _environment(
        pm2_5=51.0,
        gate="close_mtb_prolonged_endurance_high_ventilation",
    )

    plan = build_today_plan(tmp_path, DAY, state=state)

    assert plan["session"]["type"] == "environment_indoor_continuity"
    assert plan["session"]["modality"] == "bike_indoor"
    assert plan["session"]["duration_min"] == 60
    assert plan["session"]["dose"]["main"] == "40 minutes at 120-130 W / global RPE 2-3."
    _assert_schema_v3(plan["session"])
    applied = plan["constraint_resolution"]["applied"]
    environment = next(item for item in applied if item["source"] == "environment_evidence")
    assert environment["evidence_id"] == "airgradient-86311-test-v1"
    assert environment["freshness"]["state"] == "current"
    assert environment["current_pm2_5_ug_m3"] == 51.0


def test_hazardous_pm_closes_any_explicit_kiara_outdoor_session(tmp_path):
    load_context(tmp_path)
    _planned_session(tmp_path, modality="outdoor_hike")
    state = _state()
    state["environment_evidence"] = _environment(
        pm2_5=151.0,
        gate="close_all_planned_outdoor_exercise",
    )

    plan = build_today_plan(tmp_path, DAY, state=state)

    assert plan["session"]["type"] == "environment_indoor_continuity"
    assert any(
        item.get("source") == "environment_evidence"
        for item in plan["constraint_resolution"]["applied"]
    )


def test_environment_replacement_never_increases_the_written_duration(tmp_path):
    load_context(tmp_path)
    _planned_session(tmp_path)
    planned_path = tmp_path / "input" / f"planned_session_{DAY}.json"
    planned = read_json(planned_path)
    planned["session"]["duration_min"] = 30
    write_json(planned_path, planned)
    state = _state()
    state["environment_evidence"] = _environment(
        pm2_5=70.0,
        gate="close_mtb_prolonged_endurance_high_ventilation",
    )

    plan = build_today_plan(tmp_path, DAY, state=state)

    assert plan["session"]["type"] == "environment_indoor_continuity"
    assert plan["session"]["duration_min"] == 30
    assert plan["session"]["dose"]["main"].startswith("20 minutes")


def test_environment_proxy_is_venue_scoped_and_does_not_touch_another_venue(tmp_path):
    load_context(tmp_path)
    _planned_session(tmp_path, venue="Denai Peladang")
    state = _state()
    state["environment_evidence"] = _environment(
        pm2_5=151.0,
        gate="close_all_planned_outdoor_exercise",
    )

    plan = build_today_plan(tmp_path, DAY, state=state)

    assert plan["session"]["title"] == "Explicit outdoor quality"
    assert not any(
        item.get("source") == "environment_evidence"
        for item in plan["constraint_resolution"]["applied"]
    )


def test_stale_poor_retains_restriction_but_stale_low_only_holds_without_clearance(tmp_path):
    load_context(tmp_path)
    _planned_session(tmp_path)
    poor_state = _state()
    poor_state["environment_evidence"] = _environment(
        pm2_5=70.0,
        gate="retained_high_ventilation_closure_pending_refresh",
        freshness="stale",
    )

    poor_plan = build_today_plan(tmp_path, DAY, state=poor_state)
    assert poor_plan["session"]["type"] == "environment_indoor_continuity"

    low_state = _state()
    low_state["environment_evidence"] = _environment(
        pm2_5=20.0,
        gate="hold_pending_refresh",
        freshness="stale",
    )
    low_plan = build_today_plan(tmp_path, DAY, state=low_state)
    assert low_plan["session"]["title"] == "Explicit outdoor quality"
    decision = low_plan["decision_inputs"]["environment_evidence"]["decision"]
    assert decision["can_promote_training"] is False
    assert any("hold_pending_refresh" in item for item in low_plan["guardrails"])

    expired_state = _state()
    expired_state["environment_evidence"] = _environment(
        pm2_5=70.0,
        gate="environment_unknown_hold",
        freshness="expired",
    )
    expired_plan = build_today_plan(tmp_path, DAY, state=expired_state)
    assert expired_plan["session"]["title"] == "Explicit outdoor quality"
    assert not any(
        item.get("source") == "environment_evidence"
        for item in expired_plan["constraint_resolution"]["applied"]
    )


def test_environment_does_not_replace_stricter_cns_or_sabbath_resolution(tmp_path):
    load_context(tmp_path)
    _planned_session(tmp_path)
    cns_state = _state()
    cns_state["cns_readiness"] = {
        "status": "impaired",
        "session_ceiling": {"level": "recovery_only"},
        "interpretation": "CNS impaired.",
    }
    cns_state["environment_evidence"] = _environment(
        pm2_5=151.0,
        gate="close_all_planned_outdoor_exercise",
    )

    cns_plan = build_today_plan(tmp_path, DAY, state=cns_state)
    assert cns_plan["session"]["type"] == "cns_recovery"
    assert not any(
        item.get("source") == "environment_evidence"
        for item in cns_plan["constraint_resolution"]["applied"]
    )

    sunday = "2026-05-03"
    _planned_session(tmp_path, sunday)
    sabbath_state = _state(sunday)
    sabbath_state["environment_evidence"] = _environment(
        pm2_5=151.0,
        gate="close_all_planned_outdoor_exercise",
    )
    sabbath_plan = build_today_plan(tmp_path, sunday, state=sabbath_state)
    assert sabbath_plan["session"]["type"] == "scheduled_rest"
    assert sabbath_plan["session"]["duration_min"] == 0
    assert not any(
        item.get("source") == "environment_evidence"
        for item in sabbath_plan["constraint_resolution"]["applied"]
    )
