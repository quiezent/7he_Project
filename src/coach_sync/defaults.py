from __future__ import annotations

from copy import deepcopy

from .time_utils import DEFAULT_TIMEZONE, iso_now


SCHEMA_VERSION = 1


def default_athlete_context() -> dict:
    now = iso_now(DEFAULT_TIMEZONE)

    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": now,
        "athlete": {
            "name": "Clayton",
            "timezone": DEFAULT_TIMEZONE,
            "sport": "mountain_biking",
            "body_weight_kg": None,
            "goal": "Progress from experienced MTB rider to expert rider.",
            "rider_category": {
                "current": "experienced",
                "target": "expert",
                "experienced_definition": (
                    "Carries speed through most corners found on trail, including rough, "
                    "banked, and flat corners. Larger jumps and drops are hit frequently "
                    "and with precision. Rides hard trails with consistent comfort, "
                    "confidence, and precision."
                ),
                "expert_definition": (
                    "Upper echelon aggressive trail skill: scrubbing jumps, heavy flat "
                    "landings at pace, aggressive technical terrain, repeatable speed "
                    "into and out of challenging corners, and comfort with wheel slides."
                ),
            },
        },
        "operating_model": {
            "software_role": "stage evidence, normalize data, apply repeatable safety rules",
            "coach_role": "interpret evidence, resolve conflicts, and make the final coaching call",
            "nutrition_role": "fuel training, recovery, body composition, and adaptation",
        },
        "medical": {
            "history": [
                {
                    "label": "left_pinky_fracture",
                    "date": "2025-08",
                    "note": "Historical context only; not used as a current training gate.",
                },
            ],
        },
        "goal_progression": {
            "current_phase": "base_rebuild",
            "phase_order": [
                "base_rebuild",
                "mtb_specificity",
                "performance_build",
                "expert_specific_work",
            ],
            "phase_rules": {
                "base_rebuild": {
                    "purpose": "Rebuild aerobic durability, strength rhythm, and weekly repeatability.",
                },
                "mtb_specificity": {
                    "purpose": "Add trail handling under fatigue, braking endurance, and technical repeatability.",
                },
                "performance_build": {
                    "purpose": "Layer threshold, VO2, anaerobic repeatability, and strength-power.",
                },
                "expert_specific_work": {
                    "purpose": "Progress speed, creativity, jumps/drops, heavy landings, and aggressive technical riding.",
                },
            },
        },
        "training_rules": {
            "stale_data_warning_days": 1,
            "wellness_stale_hard_days": 2,
            "training_status_stale_warning_days": 1,
            "training_status_stale_hard_days": 2,
            "acute_chronic_load_spike_ratio": 1.5,
            "bike_specific_continuity": {
                "minimum_bike_touches_per_week": 2,
                "preferred_rebuild_bike_touches_per_week": 5,
                "maximum_normal_build_bike_touches_per_week": 6,
                "meaningful_cost_sessions_per_week_max": 3,
                "low_cost_bike_touches_per_week": [2, 3],
                "protect_mtb_exposures_per_week": 2,
                "maximum_mtb_exposures_per_week": 3,
                "downshift_warning": "Do not allow high elliptical/non-bike load to hide a bike-specific drought.",
            },
            "weekly_rest_days": [
                {
                    "weekday": 6,
                    "label": "Sabbath",
                    "status": "hard_rest",
                    "reason": "Clayton treats Sunday as Sabbath; no planned exercise.",
                }
            ],
        },
        "nutrition": {
            "protein_g_per_kg": [1.6, 2.2],
            "carb_g_per_kg": {
                "recovery": [1.5, 3.0],
                "easy": [2.0, 4.0],
                "moderate": [3.0, 5.0],
                "hard": [4.0, 7.0],
            },
            "during_session_carbs_g_per_hour": {
                "under_60_min": [0, 20],
                "60_to_120_min": [30, 60],
                "over_120_min": [60, 90],
            },
        },
        "history": [
            {
                "timestamp": now,
                "type": "context_initialized",
                "note": "Default rebuild context created with historical left pinky fracture context only.",
            }
        ],
    }


def clone_default_context() -> dict:
    return deepcopy(default_athlete_context())
