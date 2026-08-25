from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import coach_sync.garmin_sync as garmin_sync_module
import coach_sync.state as state_module
import pytest
from coach_sync.air_quality import load_air_quality_context, refresh_air_quality
from coach_sync.coach_packet import build_coach_packet
from coach_sync.context import load_context, save_context
from coach_sync.io import read_json, write_json
from coach_sync.planning import (
    SESSION_CONTRACT_FIELDS,
    _apply_session_constraints,
    build_today_plan,
)


KL = ZoneInfo("Asia/Kuala_Lumpur")
ENDPOINT = (
    "https://api.airgradient.com/public/api/v1/world/locations/86311/measures/current"
)


def _configure(root) -> dict:
    context = load_context(root)
    context.setdefault("athlete", {}).setdefault("venue_profiles", {}).setdefault(
        "bukit_kiara", {}
    )["air_quality_proxy"] = {
        "provider": "AirGradient",
        "endpoint": ENDPOINT,
        "location_id": 86311,
        "location_name": "Taman Tun Dr. Ismail",
        "station_timezone": "Asia/Kuala_Lumpur",
        "fresh_max_age_minutes": 10,
        "usable_max_age_minutes": 60,
        "future_timestamp_tolerance_minutes": 2,
        "timeout_seconds": 8,
        "spatial_scope": "Hyperlocal outdoor PM2.5 proxy for TTDI and Bukit Kiara.",
        "spatial_guardrail": "Does not positively clear the whole Klang Valley.",
        "automatic_gate_venue_keys": ["bukit_kiara"],
        "automatic_gate_venue_aliases": ["Bukit Kiara", "Kiara", "TTDI"],
        "coaching_thresholds_ug_m3": {
            "elevated_from": 9.1,
            "outdoor_hard_closed_from": 35.5,
            "all_outdoor_closed_from": 55.5,
        },
    }
    return save_context(context, root)


def _payload(timestamp: str, pm25: float = 72.5, **updates) -> dict:
    payload = {
        "locationId": 86311,
        "publicLocationName": "Taman Tun Dr. Ismail",
        "timezone": "Asia/Kuala_Lumpur",
        "timestamp": timestamp,
        "offline": False,
        "pm02": pm25,
        "pm02_corrected": 12.3,
        "latitude": 3.15,
        "longitude": 101.62,
        "publicContributorName": "private contributor value",
        "serialno": "private serial value",
        "firmwareVersion": "private firmware value",
        "wifi": -61,
    }
    payload.update(updates)
    return payload


def test_air_quality_refresh_selects_raw_pm25_and_closes_outdoor_training(tmp_path):
    _configure(tmp_path)
    now = datetime(2026, 8, 25, 14, 10, tzinfo=KL)

    artifact = refresh_air_quality(
        tmp_path,
        now=now,
        fetcher=lambda _url, _timeout: (
            200,
            _payload("2026-08-25T06:08:30Z"),
            {},
        ),
    )

    assert artifact["status"] == "available_current"
    assert artifact["freshness"]["status"] == "current"
    assert artifact["current"]["observed_at_local"] == "2026-08-25T14:08:30+08:00"
    assert artifact["current"]["pm2_5"] == {
        "value": 72.5,
        "unit": "ug/m3",
        "source_field": "pm02",
        "value_kind": "raw_unadjusted_mass_concentration",
        "corrected_value_used": False,
    }
    assert artifact["decision"]["gate"] == "outdoor_training_closed"
    assert artifact["decision"]["can_promote_training"] is False
    assert artifact["privacy"]["raw_payload_stored"] is False
    assert set(artifact["current"]) == {
        "location",
        "observed_at_utc",
        "observed_at_local",
        "age_min_at_fetch",
        "offline",
        "pm2_5",
        "station_config_sha256",
        "selected_payload_sha256",
        "fetched_at",
    }
    assert set(artifact["current"]["location"]) == {"id", "name", "timezone"}
    assert "private serial value" not in str(artifact)
    ledger = read_json(tmp_path / "snapshots" / "air_quality_ledger.json", {})
    assert ledger["entries"][-1]["pm2_5_ug_m3"] == 72.5


def test_air_quality_retains_last_good_after_semantic_or_transport_failure(tmp_path):
    _configure(tmp_path)
    first_now = datetime(2026, 8, 25, 14, 10, tzinfo=KL)
    refresh_air_quality(
        tmp_path,
        now=first_now,
        fetcher=lambda _url, _timeout: (
            200,
            _payload("2026-08-25T06:09:00Z"),
            {},
        ),
    )

    unusable = refresh_air_quality(
        tmp_path,
        now=first_now + timedelta(minutes=2),
        fetcher=lambda _url, _timeout: (
            200,
            _payload("2026-08-25T06:11:00Z", locationId=999),
            {},
        ),
    )
    assert unusable["latest_attempt"]["status"] == "success_unusable"
    assert unusable["latest_attempt"]["semantic_error"] == "location_id_mismatch"
    assert unusable["current"]["pm2_5"]["value"] == 72.5
    assert unusable["status"] == "available_retained_current"

    def fail(_url, _timeout):
        raise RuntimeError("secret transport detail")

    failed = refresh_air_quality(
        tmp_path,
        now=first_now + timedelta(minutes=3),
        fetcher=fail,
    )
    assert failed["latest_attempt"]["status"] == "failed"
    assert failed["latest_attempt"]["error"] == "Unexpected air-quality fetch failure."
    assert "secret transport detail" not in str(failed)
    assert failed["current"]["pm2_5"]["value"] == 72.5


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        (
            _payload("2026-08-25T06:09:00Z", locationId=999),
            "location_id_mismatch",
        ),
        (
            _payload("2026-08-25T06:09:00Z", offline=True),
            "station_offline_or_state_missing",
        ),
        (
            _payload("2026-08-25T06:09:00Z", pm25=-1),
            "pm02_missing_or_invalid",
        ),
        (
            _payload("2026-08-25T06:09:00"),
            "timestamp_missing_or_not_timezone_aware",
        ),
        (
            _payload("2026-08-25T06:13:00Z"),
            "measurement_timestamp_is_in_the_future",
        ),
        (
            _payload("2026-08-25T05:00:00Z"),
            "measurement_older_than_usable_limit",
        ),
    ],
)
def test_semantically_invalid_air_quality_never_becomes_current(
    tmp_path,
    payload,
    expected_error,
):
    _configure(tmp_path)
    artifact = refresh_air_quality(
        tmp_path,
        now=datetime(2026, 8, 25, 14, 10, tzinfo=KL),
        fetcher=lambda _url, _timeout: (200, payload, {}),
    )

    assert artifact["status"] == "unavailable"
    assert artifact["current"] is None
    assert artifact["last_known_good"] is None
    assert artifact["latest_attempt"]["status"] == "success_unusable"
    assert artifact["latest_attempt"]["semantic_error"] == expected_error
    assert artifact["decision"]["can_promote_training"] is False


def test_air_quality_freshness_and_historical_projection_never_reuse_current_as_clearance(
    tmp_path,
):
    _configure(tmp_path)
    observed_now = datetime(2026, 8, 25, 14, 10, tzinfo=KL)
    refresh_air_quality(
        tmp_path,
        now=observed_now,
        fetcher=lambda _url, _timeout: (
            200,
            _payload("2026-08-25T06:09:00Z", pm25=5.0),
            {},
        ),
    )

    stale = load_air_quality_context(
        tmp_path,
        "2026-08-25",
        now=observed_now + timedelta(minutes=20),
    )
    assert stale["freshness"]["status"] == "stale"
    assert stale["decision"]["gate"] == "unknown_downshift_only"
    assert stale["decision"]["can_promote_training"] is False

    historical = load_air_quality_context(
        tmp_path,
        "2026-08-24",
        now=observed_now + timedelta(minutes=2),
    )
    assert historical["freshness"]["status"] == "historical_unavailable"
    assert historical["current"] is None
    assert historical["last_known_good"] is None
    assert historical["latest_attempt"] is None
    assert historical["retention_validation"][
        "historical_projection_hides_current_measurements"
    ] is True
    assert historical["decision"]["gate"] == "unknown_downshift_only"


def test_stale_high_air_quality_retains_downshift_but_requires_refresh(tmp_path):
    _configure(tmp_path)
    observed_now = datetime(2026, 8, 25, 14, 10, tzinfo=KL)
    refresh_air_quality(
        tmp_path,
        now=observed_now,
        fetcher=lambda _url, _timeout: (
            200,
            _payload("2026-08-25T06:09:00Z", pm25=72.5),
            {},
        ),
    )

    retained = load_air_quality_context(
        tmp_path,
        "2026-08-25",
        now=observed_now + timedelta(minutes=20),
    )

    assert retained["freshness"]["status"] == "stale"
    assert retained["decision"]["gate"] == (
        "retained_outdoor_training_closed_pending_refresh"
    )
    assert retained["decision"]["can_promote_training"] is False
    assert "until a fresh venue-relevant source is checked" in retained["decision"][
        "reason"
    ]


def test_air_quality_gate_replaces_written_mtb_with_indoor_and_surfaces_in_packet(
    tmp_path,
):
    _configure(tmp_path)
    air_quality = refresh_air_quality(
        tmp_path,
        now=datetime(2026, 8, 25, 14, 10, tzinfo=KL),
        fetcher=lambda _url, _timeout: (
            200,
            _payload("2026-08-25T06:09:00Z", pm25=72.5),
            {},
        ),
    )
    write_json(
        tmp_path / "input" / "planned_session_2026-08-25.json",
        {
            "date": "2026-08-25",
            "generated_at": "2026-08-25T13:00:00+08:00",
            "session": {
                "title": "Two controlled Kiara loops",
                "type": "mtb_repeatability_controlled",
                "modality": "mtb",
                "venue": "Bukit Kiara",
                "duration_min": 120,
                "intensity": "skill",
            },
        },
    )
    state = {
        "date": "2026-08-25",
        "athlete": {"timezone": "Asia/Kuala_Lumpur"},
        "phase": {"name": "base_rebuild"},
        "readiness": {
            "readiness_level": "green",
            "readiness_score": 82,
            "hard_session_guidance": "allow",
        },
        "data_freshness": {
            "status": "current",
            "hard_session_confidence": "full",
        },
        "cns_readiness": {
            "status": "ready",
            "session_ceiling": {"level": "normal"},
        },
        "air_quality": air_quality,
    }

    plan = build_today_plan(tmp_path, "2026-08-25", state=state)

    assert plan["session"]["type"] == "air_quality_indoor_substitute"
    assert plan["session"]["modality"] == "bike_indoor"
    assert plan["session"]["schema_version"] == 3
    assert plan["session"]["duration_min"] == 45
    assert any(
        item["source"] == "air_quality"
        for item in plan["constraint_resolution"]["applied"]
    )
    assert plan["decision_inputs"]["air_quality"]["decision"]["gate"] == (
        "outdoor_training_closed"
    )

    packet = build_coach_packet(
        tmp_path,
        "2026-08-25",
        state=state,
        plan=plan,
    )
    signal = next(
        item
        for item in packet["evidence"]["trusted"]
        if item["name"] == "TTDI/Bukit Kiara outdoor air quality"
    )
    assert signal["value"]["pm2_5"]["value"] == 72.5
    assert signal["decision_use"].endswith("never_readiness_promotion")
    caution = next(
        item
        for item in packet["evidence"]["cautions"]
        if item["source"] == "air_quality"
    )
    assert caution["severity"] == "red"
    assert caution["type"] == "outdoor_training_closed"


def test_air_quality_does_not_override_stricter_cns_recovery_or_other_venue(
    tmp_path,
):
    _configure(tmp_path)
    high_air = refresh_air_quality(
        tmp_path,
        now=datetime(2026, 8, 25, 14, 10, tzinfo=KL),
        fetcher=lambda _url, _timeout: (
            200,
            _payload("2026-08-25T06:09:00Z", pm25=72.5),
            {},
        ),
    )
    kiara = {
        "title": "Kiara technical ride",
        "type": "mtb_repeatability_controlled",
        "modality": "mtb",
        "venue_key": "bukit_kiara",
        "duration_min": 120,
        "intensity": "skill",
    }
    cns_state = {
        "cns_readiness": {
            "status": "compromised",
            "interpretation": "CNS compromise requires recovery only.",
            "session_ceiling": {"level": "low_consequence_only"},
        },
        "data_freshness": {"status": "current"},
        "air_quality": high_air,
    }

    effective, constraints = _apply_session_constraints(
        kiara,
        cns_state,
        {"recommended_action": "hold_plan"},
        {"type": "input_planned_session"},
    )

    assert effective["type"] == "cns_recovery"
    assert [item["source"] for item in constraints] == [
        "cns_readiness",
        "air_quality",
    ]
    assert constraints[-1]["resolution"] == (
        "stricter_existing_non_outdoor_constraint_preserved"
    )

    denai = {**kiara, "title": "Denai recce", "venue_key": "denai_peladang"}
    effective, constraints = _apply_session_constraints(
        denai,
        {**cns_state, "cns_readiness": {"status": "ready"}},
        {"recommended_action": "hold_plan"},
        {"type": "input_planned_session"},
    )
    assert effective["type"] == "mtb_repeatability_controlled"
    assert not any(item["source"] == "air_quality" for item in constraints)


def test_air_quality_blocked_sunday_race_returns_to_sabbath_not_indoor_training(
    tmp_path,
):
    context = _configure(tmp_path)
    proxy = context["athlete"]["venue_profiles"]["bukit_kiara"][
        "air_quality_proxy"
    ]
    proxy["automatic_gate_venue_keys"] = ["denai_peladang"]
    proxy["automatic_gate_venue_aliases"] = ["Denai Peladang"]
    save_context(context, tmp_path)
    high_air = refresh_air_quality(
        tmp_path,
        now=datetime(2026, 9, 20, 8, 10, tzinfo=KL),
        fetcher=lambda _url, _timeout: (
            200,
            _payload("2026-09-20T00:09:00Z", pm25=72.5),
            {},
        ),
    )
    race = {
        "title": "PDR26 downhill race",
        "type": "mtb_downhill_race",
        "modality": "mtb",
        "race_event": True,
        "duration_min": 180,
        "intensity": "race",
        "schema_version": 3,
        "contract_fields": SESSION_CONTRACT_FIELDS,
        "purpose": "Race the named downhill event after Saturday practice.",
        "dose": {"event": "Timed downhill race runs plus event warm-up."},
        "adaptation_hypothesis": "Convert preparation into race performance.",
        "execution_rules": ["Use only the practised line and race setup."],
        "expected_result": {"technical": "Clean, repeatable race execution."},
        "stop_rules": ["Withdraw for unsafe course or environmental conditions."],
        "post_session_review_fields": [
            "stop_rule_outcome",
            "race_run_time",
            "technical_quality_notes",
        ],
    }
    write_json(
        tmp_path / "input" / "planned_session_2026-09-20.json",
        {
            "date": "2026-09-20",
            "generated_at": "2026-09-19T18:00:00+08:00",
            "sabbath_exception": {
                "date": "2026-09-20",
                "authorized_by": "athlete",
                "authorization_source": "Athlete direct statement on 2026-08-24",
                "explicit_one_off": True,
                "scope": "named_race_event_only",
                "recurring_rule_unchanged": True,
                "replacement_sabbath_date": "2026-09-21",
                "event": {
                    "name": "PDR26",
                    "date": "2026-09-20",
                    "discipline": "downhill_mtb",
                    "venue_key": "denai_peladang",
                },
            },
            "session": race,
        },
    )
    state = {
        "date": "2026-09-20",
        "athlete": {"timezone": "Asia/Kuala_Lumpur"},
        "phase": {"name": "race_specific"},
        "readiness": {
            "readiness_level": "green",
            "readiness_score": 85,
            "hard_session_guidance": "allow",
        },
        "data_freshness": {
            "status": "current",
            "hard_session_confidence": "full",
        },
        "cns_readiness": {
            "status": "ready",
            "session_ceiling": {"level": "normal"},
        },
        "air_quality": high_air,
    }

    plan = build_today_plan(tmp_path, "2026-09-20", state=state)

    assert plan["decision_inputs"]["sabbath_exception"]["status"] == (
        "validated_exact_date_race_event_exception"
    )
    assert plan["session"]["type"] == "scheduled_rest"
    assert plan["session"]["duration_min"] == 0
    assert plan["constraint_resolution"]["applied"][-1]["resolution"] == (
        "event_blocked_so_recurring_sabbath_rest_no_substitute_training"
    )


def test_retained_measurement_is_invalidated_when_station_configuration_changes(
    tmp_path,
):
    context = _configure(tmp_path)
    observed_now = datetime(2026, 8, 25, 14, 10, tzinfo=KL)
    refresh_air_quality(
        tmp_path,
        now=observed_now,
        fetcher=lambda _url, _timeout: (
            200,
            _payload("2026-08-25T06:09:00Z", pm25=72.5),
            {},
        ),
    )
    context["athlete"]["venue_profiles"]["bukit_kiara"]["air_quality_proxy"][
        "location_id"
    ] = 999
    save_context(context, tmp_path)

    projected = load_air_quality_context(
        tmp_path,
        "2026-08-25",
        now=observed_now + timedelta(minutes=2),
    )

    assert projected["current"] is None
    assert projected["last_known_good"] is None
    assert projected["retention_validation"]["status"] == (
        "station_config_fingerprint_mismatch_or_missing"
    )
    assert projected["decision"]["gate"] == "unknown_downshift_only"


def test_sync_refreshes_air_quality_once_live_and_never_on_rebuild(
    tmp_path,
    monkeypatch,
):
    calls = {"refresh": 0, "load": 0}
    projected = {
        "status": "available_current",
        "freshness": {"status": "current"},
        "current": {
            "observed_at_local": "2026-08-25T14:08:30+08:00",
            "pm2_5": {"value": 72.5, "unit": "ug/m3"},
        },
        "decision": {"gate": "outdoor_training_closed"},
        "latest_attempt": {"status": "success"},
    }

    def refresh(*_args, **_kwargs):
        calls["refresh"] += 1
        return projected

    def load(*_args, **_kwargs):
        calls["load"] += 1
        return projected

    monkeypatch.setattr(garmin_sync_module, "refresh_air_quality", refresh)
    monkeypatch.setattr(garmin_sync_module, "load_air_quality_context", load)
    monkeypatch.setattr(state_module, "load_air_quality_context", load)
    monkeypatch.setattr(
        garmin_sync_module,
        "_fetch_live",
        lambda *_args, **_kwargs: {"status": "success", "contacted_garmin": False},
    )

    rebuild = garmin_sync_module.sync_connect(
        tmp_path,
        rebuild_only=True,
        decision_only=True,
    )
    assert calls["refresh"] == 0
    assert calls["load"] >= 2
    assert rebuild["air_quality_sync"]["gate"] == "outdoor_training_closed"
    stored_state = read_json(tmp_path / "snapshots" / "current_state.json", {})
    assert stored_state["air_quality"]["current"]["pm2_5"]["value"] == 72.5

    calls.update(refresh=0, load=0)
    live = garmin_sync_module.sync_connect(
        tmp_path,
        rebuild_only=False,
        decision_only=True,
    )
    assert calls["refresh"] == 1
    assert live["air_quality_sync"]["pm2_5_ug_m3"] == 72.5


def test_clean_air_never_promotes_red_readiness_or_cns_ceiling(tmp_path):
    _configure(tmp_path)
    clean_air = refresh_air_quality(
        tmp_path,
        now=datetime(2026, 8, 25, 14, 10, tzinfo=KL),
        fetcher=lambda _url, _timeout: (
            200,
            _payload("2026-08-25T06:09:00Z", pm25=5.0),
            {},
        ),
    )
    state = {
        "date": "2026-08-25",
        "athlete": {"timezone": "Asia/Kuala_Lumpur"},
        "phase": {"name": "base_rebuild"},
        "readiness": {
            "readiness_level": "red",
            "readiness_score": 30,
            "hard_session_guidance": "avoid",
        },
        "data_freshness": {
            "status": "current",
            "hard_session_confidence": "full",
        },
        "cns_readiness": {
            "status": "impaired",
            "session_ceiling": {"level": "recovery_only"},
        },
        "air_quality": clean_air,
    }

    plan = build_today_plan(tmp_path, "2026-08-25", state=state)

    assert clean_air["decision"]["gate"] == "no_pm25_downshift_from_current_sample"
    assert clean_air["decision"]["can_promote_training"] is False
    assert plan["decision_inputs"]["readiness_level"] == "red"
    assert plan["decision_inputs"]["cns_readiness"]["status"] == "impaired"
    assert plan["session"]["intensity"] == "recovery"
