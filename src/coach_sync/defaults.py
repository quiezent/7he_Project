from __future__ import annotations

from copy import deepcopy

from .time_utils import DEFAULT_TIMEZONE, iso_now


SCHEMA_VERSION = 1
DR_TEH_CLEARANCE_DATE = "2026-04-29"


def default_athlete_context() -> dict:
    now = iso_now(DEFAULT_TIMEZONE)
    clearances = {
        gate: {
            "status": "cleared",
            "date": DR_TEH_CLEARANCE_DATE,
            "source": "Dr. Teh",
            "note": "Cleared to resume outdoor biking, gym, and normal activity.",
        }
        for gate in ("cardio", "grip", "loading", "trail")
    }

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
                    "note": "MTB volume dropped to zero after July 2025.",
                },
                {
                    "label": "left_pinky_surgery",
                    "date": "2026-03-10",
                    "type": "surgery",
                },
                {
                    "label": "dr_teh_green_light",
                    "date": DR_TEH_CLEARANCE_DATE,
                    "type": "medical_clearance",
                    "source": "Dr. Teh",
                    "note": "Finger cleared for outdoor biking, gym, and normal activity.",
                },
            ],
            "clearance_gates": clearances,
            "modality_overrides": [],
        },
        "goal_progression": {
            "current_phase": "return_to_outdoor_reentry",
            "phase_order": [
                "protected_recovery",
                "return_to_outdoor_reentry",
                "base_rebuild",
                "mtb_specificity",
                "performance_build",
                "expert_specific_work",
            ],
            "phase_rules": {
                "return_to_outdoor_reentry": {
                    "minimum_days": 14,
                    "purpose": "Reintroduce outdoor trail, grip, and gym loading without a load spike.",
                    "default_bias": "low-consequence outdoor rides, aerobic continuity, gym primer work",
                },
                "base_rebuild": {
                    "purpose": "Rebuild aerobic durability, strength rhythm, and weekly repeatability.",
                },
                "mtb_specificity": {
                    "purpose": "Add trail handling under fatigue, braking/grip endurance, and technical repeatability.",
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
            "acute_chronic_load_spike_ratio": 1.5,
            "first_14_day_reentry": {
                "max_outdoor_mtb_days_per_7d": 3,
                "avoid_stack": "Do not combine first hard outdoor ride, heavy gym, and big technical exposure on the same day.",
                "progression_signal": "No next-morning pain, swelling, grip regression, or unusual fatigue.",
            },
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
                "note": "Default rebuild context created with Dr. Teh clearance recorded.",
            }
        ],
    }


def clone_default_context() -> dict:
    return deepcopy(default_athlete_context())

