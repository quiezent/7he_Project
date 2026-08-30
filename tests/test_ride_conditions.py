from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

from coach_sync.io import read_json, write_json
from coach_sync import ride_conditions as ride_conditions_module
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
                            "schema_version": "1.6.0",
                            "contract_revision": "sha256:test-v16",
                            "contract_discovery": {"evidence_poll_seconds": 180},
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
        "schemaVersion": "1.6.0",
        "kind": "mtb_environment_evidence",
        "generatedAt": "2026-08-28T02:01:00Z",
        "evidenceId": "airgradient-86311-test-v1",
        "contract": {
            "schemaVersion": "1.6.0",
            "revision": "sha256:test-v16",
            "openapi": "/api/openapi.json",
            "jsonSchema": "/api/v1/mtb/environment-evidence/schema",
            "documentation": "/docs/coach-api.md",
            "revisionRole": "contract_and_documentation_only",
            "evidencePollSeconds": 180,
            "refreshPolicy": {
                "mode": "conditional_get",
                "useIfNoneMatch": True,
                "reloadWhen": ["schemaVersion", "revision"],
                "resources": [
                    "/api/openapi.json",
                    "/api/v1/mtb/environment-evidence/schema",
                    "/docs/coach-api.md",
                ],
            },
        },
        "location": {
            "name": "Bukit Kiara / Taman Tun Dr Ismail",
            "latitude": 3.1411106257487,
            "longitude": 101.62749852676,
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
            "sustainedImprovement": False,
            "minimumSinceEventUgM3": None,
            "recheckMinutes": 15,
            "analysisBucketEndAt": observed_at,
            "analysisBucketMinutes": 15,
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
                "basedOnObservedAt": observed_at,
                "persistenceAnchorPm25UgM3": pm2_5,
                "persistenceAnchorRole": "latest_raw_sensor_reading",
                "modelFeatureAnchorPm25UgM3": pm2_5,
                "projectedPm25UgM3": forecast_high + 100,
                "baselinePm25UgM3": pm2_5,
                "pointRole": "aggressive_local_analogue",
                "forecastState": "aggressive_experimental_point_and_range",
                "approximate": True,
                "validationState": "experimental_not_validated",
                "likelyRangePm25UgM3": {
                    "low": forecast_low,
                    "high": forecast_high,
                    "calibrated": calibrated,
                },
                "decisionEnvelopePm25UgM3": {
                    "low": forecast_low,
                    "high": forecast_high,
                    "containsBaseline": True,
                    "containsExperimentalProjection": True,
                    "role": "conservative_persistence_envelope",
                },
                "upperPm25UgM3": {
                    "value": forecast_high,
                    "quantile": 0.9,
                    "finiteSampleRank": True,
                    "finiteSampleRankCoverage": 0.92,
                    "calibrated": calibrated,
                },
                "decisionUpperPm25UgM3": forecast_high,
                "confidence": forecast_confidence,
                "confidenceDetail": "Test confidence",
                "methodId": "test-v1",
                "support": {
                    "originCount": 512,
                    "independentOriginCount": 87,
                    "matchedCount": 24,
                    "matchedIndependentOriginCount": 16,
                    "matchedDistinctDays": 5,
                    "distinctDays": 6,
                    "minimumRequired": 24,
                },
            },
            "onTrail": {
                "available": True,
                "startOffsetMinutes": 90,
                "endOffsetMinutes": 210,
                "startAt": "2026-08-28T03:31:00Z",
                "endAt": "2026-08-28T05:31:00Z",
                "headline": "Uncertain",
                "basedOnObservedAt": observed_at,
                "persistenceAnchorPm25UgM3": pm2_5,
                "persistenceAnchorRole": "latest_raw_sensor_reading",
                "modelFeatureAnchorPm25UgM3": pm2_5,
                "projectedMeanPm25UgM3": forecast_high + 100,
                "projectedPeakPm25UgM3": forecast_high + 120,
                "baselineMeanPm25UgM3": pm2_5,
                "pointRole": "aggressive_local_analogue",
                "forecastState": "aggressive_experimental_point_and_range",
                "approximate": True,
                "validationState": "experimental_not_validated",
                "likelyMeanRangePm25UgM3": {
                    "low": forecast_low,
                    "high": forecast_high,
                    "calibrated": calibrated,
                },
                "decisionMeanEnvelopePm25UgM3": {
                    "low": forecast_low,
                    "high": forecast_high,
                    "containsBaseline": True,
                    "containsExperimentalProjection": True,
                    "role": "conservative_persistence_envelope",
                },
                "upperMeanPm25UgM3": {
                    "value": forecast_high,
                    "quantile": 0.9,
                    "finiteSampleRank": True,
                    "finiteSampleRankCoverage": 0.92,
                    "calibrated": calibrated,
                },
                "upperPeakPm25UgM3": {
                    "value": forecast_high + 10,
                    "calibrated": calibrated,
                },
                "decisionPeakRiskMarkerPm25UgM3": forecast_high + 10,
                "confidence": forecast_confidence,
                "confidenceDetail": "Test confidence",
                "methodId": "test-v1",
                "support": {
                    "originCount": 497,
                    "independentOriginCount": 37,
                    "matchedCount": 24,
                    "matchedIndependentOriginCount": 11,
                    "matchedDistinctDays": 5,
                    "distinctDays": 6,
                    "minimumRequired": 24,
                },
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
                "rainUsedForComparison": False,
                "rainSignal": "No strong rain signal",
                "thunderstorm": {
                    "level": "none",
                    "rank": 0,
                    "label": "No thunderstorm signal",
                    "basis": "No compound evidence",
                    "source": "model",
                    "usedForDecision": False,
                    "metrics": {
                        "weatherCodes": [1],
                        "capeMaxJkg": 1000,
                        "liftedIndexMin": -3,
                        "convectiveInhibitionMinJkg": 10,
                        "modeledGustMaxKmh": 10,
                        "modeledShowersMm": 0,
                        "precipitationProbabilityMaxPct": 20,
                    },
                },
                "airflow": {
                    "context": "Modeled flow only",
                    "modeledWind10mMeanKmh": 5,
                    "modeledWind180mMeanKmh": 10,
                    "modeledDirection180m": "S",
                    "usedForParticleForecast": False,
                },
            },
            "nearbyStorm": {
                "available": True,
                "fresh": True,
                "ageMinutes": 15,
                "presentWeather": "HAZE",
                "level": "none",
                "rank": 0,
                "label": "No thunderstorm signal",
                "basis": "No compound evidence",
                "source": "model",
                "usedForDecision": False,
            },
            "decisionPolicy": {
                "ordinaryRainUsedForComparison": False,
                "thunderstormUsedForComparison": True,
                "heatUsedAsTieBreaker": True,
            },
            "particleRelationshipValidated": False,
        },
        "clearanceEvent": {"detected": False, "state": "none"},
        "rideWindows": {
            "comparison": {
                "status": "ready",
                "preferredWindow": "morning",
                "historicalLowerExposureWindow": None,
                "relativeOnly": True,
                "rideApproval": False,
                "policyId": "aggressive_pm_thunderstorm_v1",
                "aggressiveExperimental": True,
                "verdict": "Morning is relatively preferable",
                "reason": "Relative comparison only.",
                "confidence": "low",
                "particleModel": {
                    "status": "aggressive_experimental",
                    "applicationStatus": "aggressive_applied_to_comparison",
                    "usedForDecision": True,
                    "usedForComparison": True,
                    "modelVersion": "cams_anchor_v1",
                    "aggressiveMode": True,
                    "scoredWindowCount": 3,
                    "minimumScoredWindows": 60,
                    "distinctDays": 1,
                    "minimumDistinctDays": 30,
                    "note": "Experimental until validated.",
                },
            },
            "morning": {
                "targetDay": "Tomorrow",
                "rideWindow": "09:00–13:00",
                "modeledSession": "09:00–11:00",
                "leadHours": 23,
                "active": False,
                "currentConditionsApplicable": False,
                "recheck": "Recheck tomorrow at 07:30",
                "confidence": "Low · limited local history",
                "particleForecast": {
                    "available": True,
                    "usedLearnedModelForDecision": True,
                    "forecastState": "aggressive_experimental_point_and_range",
                    "pointRole": "aggressive_cams_forecast",
                    "method": "Experimental CAMS delta",
                    "confidence": "low",
                    "confidenceDetail": "Low",
                    "baselineMeanPm25UgM3": pm2_5,
                    "persistenceAnchorRole": "latest_raw_sensor_reading",
                    "projectedMeanPm25UgM3": 44,
                    "projectedPeakPm25UgM3": 50,
                    "approximate": True,
                    "validationState": "experimental_not_validated",
                    "usedForValidatedDecision": False,
                    "meanRangePm25UgM3": {
                        "low": 10,
                        "high": 90,
                        "quantiles": [0.1, 0.9],
                        "nominalCoverage": 0.8,
                        "calibrated": False,
                        "role": "conservative_persistence_envelope",
                    },
                    "upperPeakPm25UgM3": 108,
                    "uncertaintyMethod": "Conservative persistence envelope",
                    "leadHours": 22.9,
                    "durationHours": 2,
                    "support": {
                        "originCount": 466,
                        "independentOriginCount": 30,
                        "matchedCount": 24,
                        "matchedDistinctDays": 6,
                    },
                    "experimentalCamsCandidate": {"rawModelMeanPm25UgM3": 999},
                },
                "observedHistory": {"raw": "must not persist"},
                "historicalBaseline": {"raw": "must not persist"},
                "weatherForecast": {
                    "available": True,
                    "startAt": "2026-08-29T01:00:00Z",
                    "endAt": "2026-08-29T03:00:00Z",
                    "modeledSession": "09:00–11:00",
                    "apparentTemperatureMaxC": 35,
                    "precipitationProbabilityMaxPct": 10,
                    "precipitationMm": 0,
                    "rainUsedForComparison": False,
                    "rainSignal": "Low rain context",
                    "thunderstorm": {"level": "none", "rank": 0},
                },
            },
            "afternoon": {
                "targetDay": "Tomorrow",
                "rideWindow": "14:00–18:00",
                "modeledSession": "14:00–16:00",
                "leadHours": 28,
                "active": False,
                "currentConditionsApplicable": False,
                "recheck": "Recheck tomorrow at 12:30",
                "confidence": "low",
                "particleForecast": {"available": False},
                "weatherForecast": {
                    "available": True,
                    "startAt": "2026-08-29T06:00:00Z",
                    "endAt": "2026-08-29T08:00:00Z",
                    "modeledSession": "14:00–16:00",
                    "rainUsedForComparison": False,
                    "thunderstorm": {"level": "likely", "rank": 2},
                },
            },
        },
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
                "validation": {
                    "supported": False,
                    "originCount": 30,
                    "unknownHistory": {"must": "not persist"},
                },
            },
            "particleForecast": {
                "provider": "Open-Meteo / Copernicus CAMS Global",
                "modelVersion": "cams_anchor_v1",
                "usedForDecision": True,
                "usedForComparison": True,
                "fetchedAt": "2026-08-28T01:45:00Z",
                "error": None,
                "attributionUrl": "https://example.invalid/raw-link-must-not-persist",
                "validation": {
                    "mode": "prospective_exact_ride_windows",
                    "supported": False,
                    "scoredWindowCount": 3,
                    "minimumScoredWindows": 60,
                    "distinctDays": 1,
                    "minimumDistinctDays": 30,
                },
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
    assert result["contract_state"]["state"] == "accepted"
    assert result["contract_state"]["revision"] == "sha256:test-v16"
    assert result["contract_state"]["evidence_poll_seconds"] == 180
    assert result["contract_state"]["freshness_independent"] is True
    assert result["last_known_good"]["observation"]["pm2_5_ug_m3"] == 38.0
    assert result["decision"]["gate"] == "hold_and_recheck"
    assert result["decision"]["can_promote_training"] is False
    assert result["persistence"]["raw_payload_stored"] is False
    assert "links" not in result["last_known_good"]
    assert "historicalBaseline" not in str(result["last_known_good"]["ride_windows"])
    assert "observedHistory" not in str(result["last_known_good"]["ride_windows"])
    assert "experimentalCamsCandidate" not in str(
        result["last_known_good"]["ride_windows"]
    )
    assert result["last_known_good"]["ride_windows"]["morning"]["target_date"] == "2026-08-29"
    assert (
        result["last_known_good"]["ride_windows"]["morning"]["recheck_at_local"]
        == "2026-08-29T07:30+08:00"
    )
    assert (
        result["last_known_good"]["ride_windows"]["morning"]["weather_forecast"][
            "start_at_utc"
        ]
        == "2026-08-29T01:00:00Z"
    )
    assert result["last_known_good"]["ride_windows"]["comparison"]["ride_approval"] is False
    assert result["last_known_good"]["provenance"]["weather_forecast"]["validation"] == {
        "supported": False,
        "origin_count": 30.0,
        "minimum_origins": None,
    }
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


def test_future_request_fetches_live_windows_without_writing_future_observation_artifact(
    tmp_path,
):
    _context(tmp_path)
    calls = []

    def fetch(url: str, timeout: float):
        calls.append((url, timeout))
        return _payload()

    result = build_environment_evidence(
        tmp_path,
        "2026-08-29",
        fetch_json=fetch,
        now=_now(),
    )

    assert calls == [(ENDPOINT, 5.0)]
    assert result["date"] == "2026-08-29"
    assert result["decision"]["gate"] == "future_exact_window_evidence_only"
    assert result["decision"]["current_observation_applicable_to_target"] is False
    assert result["decision"]["current_pm_used_for_clearance_or_closure"] is False
    assert result["future_target"] == {
        "target_date": "2026-08-29",
        "source_observation_date": "2026-08-28",
        "exact_target_window_names": ["morning", "afternoon"],
        "current_observation_projected": False,
        "future_observation_artifact_written": False,
    }
    assert result["last_known_good"]["ride_windows"]["morning"]["target_date"] == (
        "2026-08-29"
    )
    assert not (tmp_path / "snapshots" / "environment_evidence_2026-08-29.json").exists()
    stored = read_json(tmp_path / "snapshots" / "environment_evidence.json")
    assert stored["date"] == "2026-08-28"
    assert read_json(
        tmp_path / "snapshots" / "environment_evidence_2026-08-28.json"
    )["date"] == "2026-08-28"


def test_compatibility_command_accepts_exact_endpoint_override(tmp_path):
    _context(tmp_path)
    seen = []

    def fetch(url: str, _timeout: float):
        seen.append(url)
        return deepcopy(_payload())

    result = fetch_ride_conditions(tmp_path, base_url=ENDPOINT, fetch_json=fetch)

    assert seen == [ENDPOINT]
    assert result["latest_attempt"]["status"] == "success"
    assert result["source"]["endpoint"] == ENDPOINT
    assert result["source"]["endpoint_source"] == "explicit_override"


def test_legacy_same_major_fixture_without_contract_remains_accepted(tmp_path):
    _context(tmp_path)
    payload = _payload()
    payload["schemaVersion"] = "1.0.0"
    payload.pop("contract")

    result = build_environment_evidence(
        tmp_path,
        fetch_json=lambda _url, _timeout: payload,
        now=_now(),
    )

    assert result["latest_attempt"]["status"] == "success"
    assert result["last_known_good"]["identity"]["schema_version"] == "1.0.0"
    assert result["last_known_good"]["contract"]["revision"] is None


def test_v16_requires_contract_and_rejects_top_contract_schema_mismatch(tmp_path):
    _context(tmp_path)
    missing = _payload()
    missing.pop("contract")
    missing_result = build_environment_evidence(
        tmp_path,
        fetch_json=lambda _url, _timeout: missing,
        now=_now(),
    )
    assert missing_result["latest_attempt"]["status"] == "semantic_invalid"
    assert "contract_required" in missing_result["latest_attempt"]["semantic_issues"]

    mismatch = _payload()
    mismatch["contract"]["schemaVersion"] = "1.5.0"
    mismatch_result = build_environment_evidence(
        tmp_path,
        fetch_json=lambda _url, _timeout: mismatch,
        now=_now(3),
    )
    assert mismatch_result["latest_attempt"]["status"] == "semantic_invalid"
    assert "contract_schema_mismatch" in mismatch_result["latest_attempt"][
        "semantic_issues"
    ]


def test_contract_revision_change_blocks_new_evidence_until_resources_are_revalidated(tmp_path):
    _context(tmp_path)
    build_environment_evidence(
        tmp_path,
        fetch_json=lambda _url, _timeout: _payload(pm2_5=151, forecast_high=170),
        now=_now(),
    )
    changed = _payload(pm2_5=20, forecast_low=15, forecast_high=25)
    changed["contract"]["revision"] = "sha256:changed-v16"

    result = build_environment_evidence(
        tmp_path,
        fetch_json=lambda _url, _timeout: changed,
        now=_now(3),
    )

    assert result["latest_attempt"]["status"] == "contract_revalidation_required"
    assert result["contract_state"]["state"] == "changed_revalidation_needed"
    assert result["contract_state"]["revalidation_needed"] is True
    assert "live_revision_differs_from_last_accepted" in result["contract_state"]["drift"]
    assert result["last_known_good"]["observation"]["pm2_5_ug_m3"] == 151
    assert result["decision"]["gate"] == "retained_all_outdoor_closure_pending_refresh"

    context = read_json(tmp_path / "config" / "athlete_context.json")
    context["athlete"]["venue_profiles"]["bukit_kiara"][
        "preferred_environment_report"
    ]["contract_revision"] = "sha256:changed-v16"
    write_json(tmp_path / "config" / "athlete_context.json", context)
    revalidated = build_environment_evidence(
        tmp_path,
        fetch_json=lambda _url, _timeout: changed,
        now=_now(4),
    )
    assert revalidated["latest_attempt"]["status"] == "success"
    assert revalidated["contract_state"]["state"] == (
        "accepted_after_configured_revalidation"
    )
    assert revalidated["contract_state"]["revalidation_needed"] is False
    assert revalidated["last_known_good"]["observation"]["pm2_5_ug_m3"] == 20

    empty_root = tmp_path / "no_accepted_lkg"
    _context(empty_root)
    first_seen_changed = _payload(pm2_5=20, forecast_low=15, forecast_high=25)
    first_seen_changed["contract"]["revision"] = "sha256:unverified"
    unknown = build_environment_evidence(
        empty_root,
        fetch_json=lambda _url, _timeout: first_seen_changed,
        now=_now(),
    )
    assert unknown["latest_attempt"]["status"] == "contract_revalidation_required"
    assert unknown["last_known_good"] is None
    assert unknown["status"] == "unknown"
    assert unknown["decision"]["gate"] == "environment_unknown_hold"


def test_observation_timestamp_regression_is_rejected_and_last_good_retained(tmp_path):
    _context(tmp_path)
    build_environment_evidence(
        tmp_path,
        fetch_json=lambda _url, _timeout: _payload(pm2_5=62),
        now=_now(),
    )
    regressed = _payload(pm2_5=10, observed_at="2026-08-28T01:59:59Z")

    result = build_environment_evidence(
        tmp_path,
        fetch_json=lambda _url, _timeout: regressed,
        now=_now(3),
    )

    assert result["latest_attempt"]["status"] == "semantic_invalid"
    assert "observation_timestamp_regression" in result["latest_attempt"][
        "semantic_issues"
    ]
    assert result["last_known_good"]["observation"]["pm2_5_ug_m3"] == 62


def test_experimental_point_estimates_never_create_deterministic_closure(tmp_path):
    _context(tmp_path)
    payload = _payload(pm2_5=20, forecast_low=15, forecast_high=40)
    payload["exposureOutlook"]["arrival"]["projectedPm25UgM3"] = 180
    payload["exposureOutlook"]["onTrail"]["projectedMeanPm25UgM3"] = 190
    payload["exposureOutlook"]["onTrail"]["projectedPeakPm25UgM3"] = 220

    result = build_environment_evidence(
        tmp_path,
        fetch_json=lambda _url, _timeout: payload,
        now=_now(),
    )

    assert result["decision"]["gate"] == "no_environment_downshift_from_current_point"
    assert result["decision"]["experimental_points_used_for_deterministic_closure"] is False


def test_uncalibrated_conservative_envelope_crossing_poor_is_hold_not_closure(tmp_path):
    _context(tmp_path)
    result = build_environment_evidence(
        tmp_path,
        fetch_json=lambda _url, _timeout: _payload(
            pm2_5=20,
            forecast_low=15,
            forecast_high=70,
            calibrated=False,
        ),
        now=_now(),
    )

    assert result["decision"]["gate"] == "hold_and_recheck"
    assert result["decision"]["severity"] == "yellow"
    assert "conservative_uncertain_exposure_envelope_crosses_poor" in result["decision"][
        "reason_codes"
    ]


def test_structured_thunderstorm_holds_but_context_only_rain_does_not_close(tmp_path):
    _context(tmp_path)
    storm = _payload(pm2_5=20, forecast_low=15, forecast_high=40)
    storm["weather"]["trailPeriod"]["thunderstorm"]["level"] = "likely"
    storm["weather"]["trailPeriod"]["thunderstorm"]["usedForDecision"] = True
    storm_result = build_environment_evidence(
        tmp_path,
        fetch_json=lambda _url, _timeout: storm,
        now=_now(),
    )
    assert storm_result["decision"]["gate"] == "hold_and_recheck"
    assert "structured_thunderstorm_likely_or_severe" in storm_result["decision"][
        "reason_codes"
    ]

    rain = _payload(pm2_5=20, forecast_low=15, forecast_high=40)
    rain["observation"]["timestamp"] = "2026-08-28T02:00:01Z"
    rain["provenance"]["sensor"]["observedAt"] = "2026-08-28T02:00:01Z"
    rain["weather"]["trailPeriod"].update(
        {
            "precipitationProbabilityMaxPct": 92,
            "precipitationMm": 3.2,
            "rainUsedForComparison": False,
            "rainSignal": "Rain likely · context only",
        }
    )
    rain_result = build_environment_evidence(
        tmp_path,
        fetch_json=lambda _url, _timeout: rain,
        now=_now(3),
    )
    assert rain_result["decision"]["gate"] == "no_environment_downshift_from_current_point"
    assert "rain_context_only" in rain_result["decision"]["modifiers"]


def test_thunderstorm_requires_explicit_decision_use_and_fresh_nearby_evidence(tmp_path):
    trail_root = tmp_path / "trail"
    _context(trail_root)
    trail = _payload(pm2_5=20, forecast_low=15, forecast_high=40)
    trail["weather"]["trailPeriod"]["thunderstorm"].update(
        {"level": "likely", "usedForDecision": False}
    )
    trail_result = build_environment_evidence(
        trail_root,
        fetch_json=lambda _url, _timeout: trail,
        now=_now(),
    )
    assert trail_result["decision"]["gate"] == "no_environment_downshift_from_current_point"

    stale_root = tmp_path / "stale_nearby"
    _context(stale_root)
    stale = _payload(pm2_5=20, forecast_low=15, forecast_high=40)
    stale["weather"]["nearbyStorm"].update(
        {"level": "severe", "usedForDecision": True, "fresh": False}
    )
    stale_result = build_environment_evidence(
        stale_root,
        fetch_json=lambda _url, _timeout: stale,
        now=_now(),
    )
    assert stale_result["decision"]["gate"] == "no_environment_downshift_from_current_point"

    fresh_root = tmp_path / "fresh_nearby"
    _context(fresh_root)
    fresh = _payload(pm2_5=20, forecast_low=15, forecast_high=40)
    fresh["weather"]["nearbyStorm"].update(
        {"level": "likely", "usedForDecision": True, "fresh": True}
    )
    fresh_result = build_environment_evidence(
        fresh_root,
        fetch_json=lambda _url, _timeout: fresh,
        now=_now(),
    )
    assert fresh_result["decision"]["gate"] == "hold_and_recheck"
    assert "structured_thunderstorm_likely_or_severe" in fresh_result["decision"][
        "reason_codes"
    ]


def test_stale_hazardous_reading_retains_all_outdoor_closure(tmp_path):
    _context(tmp_path)
    build_environment_evidence(
        tmp_path,
        fetch_json=lambda _url, _timeout: _payload(pm2_5=151, forecast_high=170),
        now=_now(),
    )

    result = build_environment_evidence(
        tmp_path,
        now=datetime(2026, 8, 28, 10, 8, tzinfo=KL),
        refresh=False,
    )

    assert result["status"] == "stale"
    assert result["decision"]["gate"] == "retained_all_outdoor_closure_pending_refresh"


def test_default_client_honors_advertised_poll_interval_but_refreshes_after_it(
    tmp_path,
    monkeypatch,
):
    _context(tmp_path)
    calls = []

    def fake_http(url: str, timeout: float):
        calls.append((url, timeout))
        return _payload()

    monkeypatch.setattr(ride_conditions_module, "_http_json", fake_http)
    first = build_environment_evidence(tmp_path, now=_now())
    second = build_environment_evidence(tmp_path, now=_now(3))
    third = build_environment_evidence(tmp_path, now=_now(6))

    assert first["latest_attempt"]["status"] == "success"
    assert second["latest_attempt"]["attempted_at"] == first["latest_attempt"]["attempted_at"]
    assert third["latest_attempt"]["attempted_at"] != first["latest_attempt"]["attempted_at"]
    assert calls == [(ENDPOINT, 5.0), (ENDPOINT, 5.0)]


def test_failed_passive_attempt_starts_a_new_poll_interval(tmp_path, monkeypatch):
    _context(tmp_path)
    calls = []

    def fake_http(url: str, timeout: float):
        calls.append((url, timeout))
        if len(calls) == 1:
            return _payload()
        raise TimeoutError("simulated HTTP 503-style refresh failure")

    monkeypatch.setattr(ride_conditions_module, "_http_json", fake_http)
    first = build_environment_evidence(tmp_path, now=_now())
    failed = build_environment_evidence(tmp_path, now=_now(5))
    suppressed = build_environment_evidence(tmp_path, now=_now(6))

    assert first["latest_attempt"]["status"] == "success"
    assert failed["latest_attempt"]["status"] == "failed"
    assert suppressed["latest_attempt"]["status"] == "failed"
    assert suppressed["latest_attempt"]["attempted_at"] == failed["latest_attempt"][
        "attempted_at"
    ]
    assert calls == [(ENDPOINT, 5.0), (ENDPOINT, 5.0)]


def test_configured_poll_cadence_throttles_failure_without_last_known_good(
    tmp_path,
    monkeypatch,
):
    _context(tmp_path)
    calls = []

    def fail(url: str, timeout: float):
        calls.append((url, timeout))
        raise TimeoutError("service unavailable")

    monkeypatch.setattr(ride_conditions_module, "_http_json", fail)
    first = build_environment_evidence(tmp_path, now=_now())
    second = build_environment_evidence(tmp_path, now=_now(3))

    assert first["latest_attempt"]["status"] == "failed"
    assert second["latest_attempt"]["status"] == "failed"
    assert second["latest_attempt"]["attempted_at"] == first["latest_attempt"][
        "attempted_at"
    ]
    assert second["last_known_good"] is None
    assert calls == [(ENDPOINT, 5.0)]
