from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

from coach_sync.io import read_json, write_json
from coach_sync.ride_conditions import build_environment_evidence, fetch_ride_conditions


KL = ZoneInfo("Asia/Kuala_Lumpur")
ENDPOINT = "http://192.168.80.147:8765/api/v1/mtb/environment-evidence"


def _context(root) -> None:
    write_json(
        root / "config" / "athlete_context.json",
        {
            "athlete": {
                "timezone": "Asia/Kuala_Lumpur",
                "venue_profiles": {
                    "bukit_kiara": {
                        "preferred_environment_report": {
                            "name": "Clayton local MTB environment evidence",
                            "endpoint": ENDPOINT,
                            "schema_version": "1.0.0",
                            "kind": "mtb_environment_evidence",
                            "location_name": "Bukit Kiara",
                            "location_id": 86311,
                            "timezone": "Asia/Kuala_Lumpur",
                            "sports_exercise_bands_ug_m3": {
                                "normal_below": 25,
                                "poor_from": 51,
                                "hazardous_above": 150,
                            },
                        }
                    }
                },
            }
        },
    )


def _payload(
    *,
    pm2_5: float = 38.0,
    observed_at: str = "2026-08-28T02:00:00Z",
    fresh: bool = True,
    forecast_low: float = 36.0,
    forecast_high: float = 65.0,
    forecast_confidence: str = "low",
    calibrated: bool = False,
) -> dict:
    return {
        "schemaVersion": "1.0.0",
        "kind": "mtb_environment_evidence",
        "generatedAt": "2026-08-28T02:01:00Z",
        "evidenceId": "airgradient-86311-test-v1",
        "location": {
            "name": "Bukit Kiara / Taman Tun Dr Ismail",
            "timezone": "Asia/Kuala_Lumpur",
        },
        "logistics": {
            "decisionToTrailMinutes": 90,
            "typicalTrailDurationMinutes": {"minimum": 90, "maximum": 120},
            "modeledTrailDurationMinutes": 120,
            "modeledExposureWindowMinutes": {"start": 90, "end": 210},
        },
        "boundary": {
            "role": "environmental_evidence",
            "trainingPrescriptionIncluded": False,
            "historicalSeriesIncluded": False,
        },
        "status": {
            "state": "ok",
            "usable": True,
            "fresh": fresh,
            "message": "Environmental evidence is current.",
            "issues": [],
        },
        "observation": {
            "id": "airgradient-86311-test",
            "timestamp": observed_at,
            "ageSeconds": 120,
            "particles": {"pm25UgM3": pm2_5, "pm10UgM3": pm2_5 + 8},
            "heat": {
                "heatIndexC": 36.0,
                "temperatureC": 29.0,
                "relativeHumidityPct": 60.0,
            },
        },
        "particleNowcast": {
            "available": True,
            "state": "steady",
            "label": "No strong particle shift",
            "change30MinutesUgM3": 1.0,
            "change60MinutesUgM3": 2.0,
            "fastRise": False,
            "minimumSinceEventUgM3": None,
            "recheckMinutes": 15,
            "particleMixSignal": {
                "state": "balanced",
                "label": "Mixed particle signal",
                "fineSharePct": 80,
                "coarseParticlesUgM3": 8,
            },
        },
        "exposureOutlook": {
            "arrival": {
                "available": True,
                "offsetMinutes": 90,
                "expectedAt": "2026-08-28T03:31:00Z",
                "headline": "Uncertain",
                "baselinePm25UgM3": pm2_5,
                "likelyRangePm25UgM3": {
                    "low": forecast_low,
                    "high": forecast_high,
                    "calibrated": calibrated,
                },
                "confidence": forecast_confidence,
                "confidenceDetail": "Test confidence",
                "methodId": "test-v1",
                "support": {"originCount": 24, "matchedCount": 24},
            },
            "onTrail": {
                "available": True,
                "startOffsetMinutes": 90,
                "endOffsetMinutes": 210,
                "startAt": "2026-08-28T03:31:00Z",
                "endAt": "2026-08-28T05:31:00Z",
                "headline": "Uncertain",
                "baselineMeanPm25UgM3": pm2_5,
                "likelyMeanRangePm25UgM3": {
                    "low": forecast_low,
                    "high": forecast_high,
                    "calibrated": calibrated,
                },
                "upperPeakPm25UgM3": {
                    "value": forecast_high + 10,
                    "calibrated": calibrated,
                },
                "confidence": forecast_confidence,
                "confidenceDetail": "Test confidence",
                "methodId": "test-v1",
                "support": {"originCount": 24, "matchedCount": 24},
            },
        },
        "weather": {
            "available": True,
            "forecastSource": "Open-Meteo Best Match",
            "forecastFetchedAt": "2026-08-28T01:55:00Z",
            "forecastAgeMinutes": 6,
            "trailPeriod": {
                "available": True,
                "startAt": "2026-08-28T03:31:00Z",
                "endAt": "2026-08-28T05:31:00Z",
                "apparentTemperatureMaxC": 37.0,
                "precipitationProbabilityMaxPct": 20,
                "precipitationMm": 0,
                "rainSignal": "No strong rain signal",
                "airflow": {
                    "context": "Modeled flow only",
                    "modeledWind10mMeanKmh": 5,
                    "modeledWind180mMeanKmh": 10,
                    "modeledDirection180m": "S",
                    "usedForParticleForecast": False,
                },
            },
            "particleRelationshipValidated": False,
        },
        "clearanceEvent": {"detected": False, "state": "none"},
        "rideWindows": {"comparison": {"status": "collecting"}},
        "evidenceQuality": {
            "state": "limited",
            "limitations": [
                {"code": "outlook_low_confidence", "message": "Limited local history"}
            ],
        },
        "provenance": {
            "sensor": {
                "provider": "AirGradient",
                "locationId": 86311,
                "measurement": "raw particle concentration",
                "observedAt": observed_at,
                "ageSeconds": 120,
                "pollSeconds": 180,
                "staleAfterSeconds": 420,
                "expiredAfterSeconds": 900,
            },
            "weatherForecast": {
                "provider": "Open-Meteo Best Match",
                "fetchedAt": "2026-08-28T01:55:00Z",
                "ageMinutes": 6,
                "validation": {"supported": False, "originCount": 30},
            },
            "weatherReference": {
                "provider": "MET Malaysia",
                "station": "SUBANG",
                "wigosId": "0-20000-0-48647",
            },
            "analysisDaysRequested": 28,
            "historyHoursAvailable": 70,
            "sampleCount": 1500,
        },
        "links": {"sourceCurrent": "/api/current", "sourceAnalysis": "/api/analysis"},
    }


def _now(minute: int = 2) -> datetime:
    return datetime(2026, 8, 28, 10, minute, tzinfo=KL)


def test_v1_endpoint_is_normalized_and_persisted_as_bounded_evidence(tmp_path):
    _context(tmp_path)
    calls = []

    def fetch(url: str, timeout: float):
        calls.append((url, timeout))
        return _payload()

    result = build_environment_evidence(tmp_path, fetch_json=fetch, now=_now())

    assert calls == [(ENDPOINT, 5.0)]
    assert result["status"] == "current"
    assert result["last_known_good"]["observation"]["pm2_5_ug_m3"] == 38.0
    assert result["decision"]["gate"] == "hold_and_recheck"
    assert result["decision"]["can_promote_training"] is False
    assert result["persistence"]["raw_payload_stored"] is False
    assert "links" not in result["last_known_good"]
    assert "historicalBaseline" not in str(result["last_known_good"]["ride_windows"])
    assert read_json(tmp_path / "snapshots" / "environment_evidence.json")["date"] == "2026-08-28"
    assert read_json(
        tmp_path / "snapshots" / "environment_evidence_2026-08-28.json"
    )["latest_attempt"]["status"] == "success"


def test_current_poor_reading_closes_prolonged_high_ventilation_mtb(tmp_path):
    _context(tmp_path)
    result = build_environment_evidence(
        tmp_path,
        fetch_json=lambda _url, _timeout: _payload(pm2_5=62.0),
        now=_now(),
    )

    assert result["decision"]["gate"] == "close_mtb_prolonged_endurance_high_ventilation"
    assert result["decision"]["severity"] == "red"


def test_supported_calibrated_poor_lower_bound_can_close_mtb(tmp_path):
    _context(tmp_path)
    result = build_environment_evidence(
        tmp_path,
        fetch_json=lambda _url, _timeout: _payload(
            pm2_5=38.0,
            forecast_low=55.0,
            forecast_high=80.0,
            forecast_confidence="medium",
            calibrated=True,
        ),
        now=_now(),
    )

    assert result["decision"]["gate"] == "close_mtb_prolonged_endurance_high_ventilation"
    assert "calibrated_supported_forecast_lower_bound_poor" in result["decision"]["reason_codes"]


def test_semantic_invalid_refresh_retains_high_restriction_not_bad_payload(tmp_path):
    _context(tmp_path)
    build_environment_evidence(
        tmp_path,
        fetch_json=lambda _url, _timeout: _payload(pm2_5=60.0),
        now=_now(),
    )
    invalid = _payload(pm2_5=10.0)
    invalid["boundary"]["trainingPrescriptionIncluded"] = True

    result = build_environment_evidence(
        tmp_path,
        fetch_json=lambda _url, _timeout: invalid,
        now=_now(3),
    )

    assert result["latest_attempt"]["status"] == "semantic_invalid"
    assert "training_prescription_boundary_violation" in result["latest_attempt"][
        "semantic_issues"
    ]
    assert result["last_known_good"]["observation"]["pm2_5_ug_m3"] == 60.0
    assert result["decision"]["gate"] == "retained_high_ventilation_closure_pending_refresh"


def test_failed_refresh_never_reuses_low_reading_as_clearance(tmp_path):
    _context(tmp_path)
    build_environment_evidence(
        tmp_path,
        fetch_json=lambda _url, _timeout: _payload(pm2_5=12.0, forecast_high=18.0),
        now=_now(),
    )

    def fail(_url: str, _timeout: float):
        raise TimeoutError("local service timed out")

    result = build_environment_evidence(tmp_path, fetch_json=fail, now=_now(3))

    assert result["status"] == "retained_current"
    assert result["freshness"]["current"] is False
    assert result["latest_attempt"]["status"] == "failed"
    assert result["decision"]["gate"] == "hold_pending_refresh"


def test_retained_evidence_becomes_stale_then_expires_from_observation_time(tmp_path):
    _context(tmp_path)
    build_environment_evidence(
        tmp_path,
        fetch_json=lambda _url, _timeout: _payload(pm2_5=20.0, forecast_high=24.0),
        now=_now(),
    )

    stale = build_environment_evidence(
        tmp_path,
        now=datetime(2026, 8, 28, 10, 8, tzinfo=KL),
        refresh=False,
    )
    expired = build_environment_evidence(
        tmp_path,
        now=datetime(2026, 8, 28, 10, 16, tzinfo=KL),
        refresh=False,
    )

    assert stale["status"] == "stale"
    assert stale["decision"]["gate"] == "hold_pending_refresh"
    assert expired["status"] == "expired"
    assert expired["decision"]["gate"] == "environment_unknown_hold"


def test_wrong_schema_without_prior_good_evidence_is_unknown(tmp_path):
    _context(tmp_path)
    invalid = _payload()
    invalid["schemaVersion"] = "2.0.0"

    result = build_environment_evidence(
        tmp_path,
        fetch_json=lambda _url, _timeout: invalid,
        now=_now(),
    )

    assert result["status"] == "unknown"
    assert result["latest_attempt"]["status"] == "semantic_invalid"
    assert result["last_known_good"] is None
    assert result["decision"]["gate"] == "environment_unknown_hold"


def test_historical_request_never_fetches_or_projects_current_evidence(tmp_path):
    _context(tmp_path)

    def should_not_fetch(_url: str, _timeout: float):
        raise AssertionError("historical builds must not contact the current endpoint")

    result = build_environment_evidence(
        tmp_path,
        "2026-08-27",
        fetch_json=should_not_fetch,
        now=_now(),
    )

    assert result["status"] == "historical_unavailable"
    assert result["decision"]["gate"] == "historical_environment_unavailable"
    assert not (tmp_path / "snapshots" / "environment_evidence_2026-08-27.json").exists()


def test_compatibility_command_accepts_exact_endpoint_override(tmp_path):
    _context(tmp_path)
    seen = []

    def fetch(url: str, _timeout: float):
        seen.append(url)
        return deepcopy(_payload())

    result = fetch_ride_conditions(tmp_path, base_url=ENDPOINT, fetch_json=fetch)

    assert seen == [ENDPOINT]
    assert result["latest_attempt"]["status"] == "success"
