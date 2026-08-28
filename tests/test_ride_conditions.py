from __future__ import annotations

from coach_sync.io import write_json
from coach_sync.ride_conditions import fetch_ride_conditions


def _context(root) -> None:
    write_json(
        root / "config" / "athlete_context.json",
        {
            "athlete": {
                "venue_profiles": {
                    "bukit_kiara": {
                        "preferred_environment_report": {
                            "name": "Local ride conditions",
                            "base_url": "http://192.168.80.147:8765/",
                            "endpoints": {
                                "current": "/api/current",
                                "analysis": "/api/analysis?days=28",
                            },
                        }
                    }
                }
            }
        },
    )


def _fetcher(
    age_seconds: int = 120,
    *,
    collector_error: str | None = None,
    analysis_available: bool = True,
):
    def fetch(url: str, timeout: float):
        assert timeout == 5.0
        if url.endswith("/api/current"):
            return {
                "reading": {
                    "timestamp": "2026-08-28T01:09:02Z",
                    "ageSeconds": age_seconds,
                    "pm02": 47.7,
                    "pm10": 58.5,
                    "atmp": 28.3,
                    "rhum": 57,
                    "heatindex": 33,
                },
                "collector": {
                    "last_success": "2026-08-28T01:09:16Z",
                    "error": collector_error,
                },
            }
        assert "/api/analysis?days=28" in url
        return {
            "available": analysis_available,
            "reason": None if analysis_available else "insufficient history",
            "outlook": {"momentumDirection": "Rising"},
            "airWindow": {
                "state": "rebound",
                "label": "Particle rebound may be starting",
                "change30": 7.4,
                "change60": 17.9,
                "recheckMinutes": 15,
                "arrival": {
                    "point": 48.1,
                    "rangeLow": 50.7,
                    "rangeHigh": 82.3,
                    "confidence": "Low",
                },
                "trail": {
                    "point": 48.1,
                    "rangeLow": 48.1,
                    "rangeHigh": 85.9,
                    "peakUpper": 102.5,
                    "confidence": "Low",
                },
            },
            "weather": {
                "trail": {
                    "apparentTemperatureMax": 37.4,
                    "precipitationProbabilityMax": 22,
                    "ventilationLabel": "Stronger ventilation may support dispersion",
                },
                "preference": {"label": "Weather favors morning", "reason": "lower rain chance"},
            },
        }

    return fetch


def test_local_ride_conditions_exposes_fresh_transient_evidence(tmp_path):
    _context(tmp_path)
    result = fetch_ride_conditions(tmp_path, fetch_json=_fetcher())

    assert result["status"] == "ready"
    assert result["current"]["pm2_5"] == {"value": 47.7, "unit": "ug/m3"}
    assert result["particle_movement"]["state"] == "rebound"
    assert result["arrival_90_min"]["range_high_ug_m3"] == 82.3
    assert result["trail_weather"]["apparent_temperature_max_c"] == 37.4
    assert result["persistence"] == "none"
    assert "cannot promote" in result["guardrail"]


def test_local_ride_conditions_marks_old_reading_stale(tmp_path):
    _context(tmp_path)
    result = fetch_ride_conditions(
        tmp_path,
        fetch_json=_fetcher(age_seconds=900, collector_error="latest poll failed"),
    )
    assert result["status"] == "stale"
    assert result["freshness"]["collector_error"] == "latest poll failed"


def test_local_ride_conditions_requires_usable_pm2_5(tmp_path):
    _context(tmp_path)

    def malformed(url: str, _timeout: float):
        if url.endswith("/api/current"):
            return {"reading": {"ageSeconds": 30, "pm10": 22.0}}
        return {"available": True}

    result = fetch_ride_conditions(tmp_path, fetch_json=malformed)
    assert result["status"] == "unavailable"
    assert "PM2.5" in result["error"]


def test_local_ride_conditions_marks_missing_analysis_degraded(tmp_path):
    _context(tmp_path)
    result = fetch_ride_conditions(
        tmp_path,
        fetch_json=_fetcher(analysis_available=False),
    )
    assert result["status"] == "degraded"
    assert result["analysis_availability"]["available"] is False


def test_local_ride_conditions_surfaces_endpoint_failure(tmp_path):
    _context(tmp_path)

    def fail(_url: str, _timeout: float):
        raise TimeoutError("local service timed out")

    result = fetch_ride_conditions(tmp_path, fetch_json=fail)
    assert result["status"] == "unavailable"
    assert "TimeoutError" in result["error"]
    assert result["persistence"] == "none"
