from __future__ import annotations

from datetime import date
from pathlib import Path
from statistics import mean
from typing import Any

from .adaptation_profile import build_adaptation_profile
from .athlete_questions import build_athlete_question_audit
from .context import load_context
from .io import write_json, write_text
from .paths import config_dir, snapshots_dir
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local
from .training_hypotheses import build_training_hypotheses


def _round(value: float | None, digits: int = 1) -> float | None:
    return round(value, digits) if value is not None else None


def _avg(values: list[float | None]) -> float | None:
    usable = [value for value in values if value is not None]
    return mean(usable) if usable else None


def _category_total(profile: dict, category: str, key: str) -> float | int | None:
    return ((profile.get("totals") or {}).get("by_category") or {}).get(category, {}).get(key)


def _hypothesis_result(hypotheses: dict, hypothesis_id: str) -> dict:
    for item in hypotheses.get("hypotheses", []):
        if item.get("id") == hypothesis_id:
            return item
    return {}


def _week_block_summary(weeks: list[dict], start: str, end: str) -> dict:
    selected = [week for week in weeks if start <= week.get("week_start", "") <= end]
    return {
        "start": start,
        "end": end,
        "weeks": len(selected),
        "avg_bike_sessions": _round(_avg([week.get("bike_sessions") for week in selected]), 2),
        "avg_bike_days": _round(_avg([week.get("bike_days") for week in selected]), 2),
        "avg_bike_load": _round(_avg([week.get("bike_load") for week in selected])),
        "avg_total_load": _round(_avg([week.get("total_load") for week in selected])),
        "zero_bike_weeks": sum(1 for week in selected if (week.get("bike_sessions") or 0) == 0),
        "one_or_fewer_bike_weeks": sum(1 for week in selected if (week.get("bike_sessions") or 0) <= 1),
        "two_plus_bike_weeks": sum(1 for week in selected if (week.get("bike_sessions") or 0) >= 2),
        "weeks_with_mtb": sum(1 for week in selected if (week.get("mtb_sessions") or 0) > 0),
    }


def _evidence_summary(profile: dict, hypotheses: dict, audit: dict) -> dict:
    totals = profile.get("totals") or {}
    recent_28 = ((profile.get("rolling_highlights") or {}).get("recent_28d")) or {}
    recent_7 = ((profile.get("rolling_highlights") or {}).get("recent_7d")) or {}
    weeks = audit.get("bike_specific_minimum_weeks") or []
    bike_specific = _hypothesis_result(hypotheses, "bike_specificity")
    nonbike = _hypothesis_result(hypotheses, "nonbike_substitution")
    consistency = _hypothesis_result(hypotheses, "consistency_detraining")
    return {
        "full_range": {
            "activity_start": profile.get("window", {}).get("start"),
            "activity_end": profile.get("window", {}).get("end"),
            "counted_sessions": totals.get("sessions"),
            "duration_min": totals.get("duration_min"),
            "training_load": totals.get("training_load"),
            "mtb_sessions": _category_total(profile, "mtb", "sessions"),
            "mtb_load": _category_total(profile, "mtb", "training_load"),
            "indoor_bike_sessions": _category_total(profile, "bike_indoor", "sessions"),
            "indoor_bike_load": _category_total(profile, "bike_indoor", "training_load"),
            "elliptical_sessions": _category_total(profile, "elliptical", "sessions"),
            "elliptical_load": _category_total(profile, "elliptical", "training_load"),
        },
        "recent_28d": {
            "training_load": recent_28.get("training_load"),
            "bike_specific_load": recent_28.get("bike_specific_load"),
            "bike_specific_ratio": recent_28.get("bike_specific_ratio"),
            "mtb_sessions": (recent_28.get("category_sessions") or {}).get("mtb", 0),
            "indoor_bike_sessions": (recent_28.get("category_sessions") or {}).get("bike_indoor", 0),
            "elliptical_ratio": recent_28.get("elliptical_ratio"),
        },
        "recent_7d": {
            "training_load": recent_7.get("training_load"),
            "bike_specific_load": recent_7.get("bike_specific_load"),
            "bike_specific_ratio": recent_7.get("bike_specific_ratio"),
            "mtb_sessions": (recent_7.get("category_sessions") or {}).get("mtb", 0),
            "indoor_bike_sessions": (recent_7.get("category_sessions") or {}).get("bike_indoor", 0),
        },
        "weekly_continuity_blocks": {
            "good_bike_continuity_aug_to_mid_nov_2025": _week_block_summary(
                weeks, "2025-08-04", "2025-11-16"
            ),
            "substitution_danger_dec_2025_to_apr_2026": _week_block_summary(
                weeks, "2025-12-08", "2026-04-26"
            ),
            "current_rebuild_apr_to_may_2026": _week_block_summary(
                weeks, "2026-04-27", "2026-05-24"
            ),
        },
        "hypothesis_support": {
            "bike_specificity": bike_specific.get("result"),
            "nonbike_substitution": nonbike.get("result"),
            "consistency_detraining": consistency.get("result"),
            "recent_365_bike_specific_corr_to_p20": (
                (bike_specific.get("evidence") or {})
                .get("recent_365_correlations", {})
                .get("bike_specific_load")
            ),
            "recent_365_indoor_bike_corr_to_p20": (
                (bike_specific.get("evidence") or {})
                .get("recent_365_correlations", {})
                .get("indoor_bike_load")
            ),
            "recent_365_elliptical_corr_to_p20": (
                (bike_specific.get("evidence") or {})
                .get("recent_365_correlations", {})
                .get("elliptical_load")
            ),
        },
        "current_power": audit.get("power_test_and_ftp"),
        "current_limiters": audit.get("limiter_ranking_provisional"),
        "nutrition_targets": audit.get("nutrition_targets"),
    }


def _architecture(context: dict, profile: dict, hypotheses: dict, audit: dict, target: date) -> dict:
    evidence = _evidence_summary(profile, hypotheses, audit)
    athlete = context.get("athlete") or {}
    current_phase = (context.get("goal_progression") or {}).get("current_phase", "base_rebuild")
    coaching_interface = athlete.get("coaching_interface") or {
        "profile_summary": (
            "High-agency, high-cognition, performance-driven, technically curious, and already comfortable "
            "with aggressive MTB risk when it serves progression."
        ),
        "best_training_environment": [
            "clear objective",
            "constrained drill",
            "measurable output",
            "review",
            "refinement",
            "repeat",
        ],
        "coaching_implications": [
            "Use direct, specific coaching with the reason for each prescription.",
            "Challenge weak logic and vague goals without softening the standard.",
            "Prioritize high-return constraints over broad option lists.",
            "Tie training decisions to Malaysian trail transfer: wet roots, traction loss, heat, repeated accelerations, braking fatigue, and technical descending under fatigue.",
            "Treat motivation as present; provide sharper direction, constraint, and feedback instead of generic encouragement.",
        ],
        "risk_to_manage": "Curiosity plus aggression plus too many variables can scatter adaptation; keep key sessions constrained enough to learn from them.",
        "coach_voice_rules": [
            "Be precise and evidence-backed.",
            "Call out when a proposed session is not specific to the venue or goal.",
            "Separate equipment testing, skill acquisition, fitness development, and race simulation so one ride does not pretend to optimize all of them.",
            "Use honest post-session review to turn intelligence and aggression into repeatable execution.",
        ],
    }
    return {
        "date": target.isoformat(),
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "schema_version": 3,
        "architecture_type": "clayton_specific_enduro_training_architecture",
        "mission": {
            "primary": "Coach Clayton from experienced MTB rider toward expert enduro MTB performance using Garmin evidence, subjective trail notes, and coaching judgment.",
            "performance_goal": "Build the physical and technical ability to absorb a chill Saturday practice day and still attack Sunday-style race efforts.",
            "north_star_metric": "Can Clayton ride the final descent of the day with the same aggression, braking precision, and corner-exit speed as the first one?",
        },
        "integrated_coaching_model": {
            "purpose": "Combine directive enduro-specific coaching with Garmin-backed N-of-1 verification.",
            "style_blend": {
                "directive_macro_coach": [
                    "State the limiter stack plainly.",
                    "Give each day and each ride a specific job.",
                    "Protect non-negotiables: bike continuity, two MTB exposures when possible, Sunday rest, and trail quality.",
                    "Use stop rules when technique, braking, arm durability, or focus degrades.",
                ],
                "garmin_digital_twin_coach": [
                    "Verify readiness, training status, devices, gear, self-evaluation, and recent load before prescribing.",
                    "Store a pre-session expected result and compare it with actual load, RPE, feel, and next-day wellness.",
                    "Classify execution drift before calling a model miss or adaptation problem.",
                    "Use subjective trail notes to train what Garmin cannot see.",
                ],
            },
            "athlete_interface": coaching_interface,
            "operating_loop": [
                "Diagnose the current limiter from Garmin history plus recent subjective trail evidence.",
                "Choose the highest-specificity session that fits the week and recovery state.",
                "Prescribe purpose, dose, adaptation hypothesis, execution rules, expected result, and stop rules.",
                "After sync, compare expected versus actual load, self-evaluation, skill notes, and next-day response.",
                "Update the next prescription and the athlete model only when the evidence changes the coaching call.",
            ],
            "coaching_standard": "More is not better; more specific, better absorbed, and more repeatable is better.",
        },
        "stack_governance": {
            "better_definition": [
                "Better means a file, artifact, or model changes the coaching call in a way that is fresher, more specific, more inspectable, or better protected by tests.",
                "A signal is not promoted just because it is available; it must improve the prescription, the stop rules, the review, or the confidence statement.",
                "The coach packet remains the same-day decision surface; other artifacts explain, audit, or stage evidence for that surface.",
            ],
            "workflow_order": [
                "Garmin/live evidence and curated subjective notes enter raw or input storage.",
                "Core builders normalize wellness, activities, readiness, training load, gear, devices, self-evaluation, and current state.",
                "The deterministic plan applies safety rules and the schema v3 session contract.",
                "The coach packet triages trusted, cautionary, experimental, and ignored evidence.",
                "The model makes the final coaching call and records predictions/reviews only when they are testable.",
            ],
            "tool_tiers": {
                "decision_surface": [
                    "snapshots/coach_packet.json",
                    "snapshots/coach_packet.txt",
                    "snapshots/current_state.json",
                    "snapshots/today_plan.json",
                    "snapshots/daily_brief.txt",
                ],
                "core_evidence_builders": [
                    "readiness",
                    "state",
                    "plan",
                    "brief",
                    "wellness-trends",
                    "wellness-verification",
                    "training-status",
                    "activity-index",
                    "modality-rollups",
                    "gear-audit",
                    "device-audit",
                    "self-evaluation",
                ],
                "architecture_and_context": [
                    "training-architecture",
                    "adaptation-profile",
                    "training-hypotheses",
                    "athlete-questions",
                    "historical-baselines",
                    "data-quality",
                    "data-inventory",
                ],
                "predictive_and_experimental": [
                    "body-battery-model",
                    "training-predictor",
                    "predictive-training",
                    "predictive-review",
                    "predictive-backtest",
                ],
                "operational_or_backfill": [
                    "sync",
                    "rebuild",
                    "historical-backfill",
                    "cleanup-derived",
                    "log",
                    "loop-load",
                    "context",
                ],
                "low_authority_reports_or_unconfigured_sources": [
                    "weekly-report",
                    "insight-memo",
                    "review-block",
                    "forecast",
                    "local-estimates",
                    "intraday-trends",
                    "weather-snapshot",
                ],
                "compatibility_wrappers": [
                    "tools/*.py files are thin wrappers around python -m coach_sync commands and are kept for AGENTS.md, README, and operator ergonomics.",
                ],
            },
            "promotion_rules": [
                "Promote an artifact when it is current, tested, athlete-specific, and changes readiness, dose, stop rules, fueling, sensor confidence, or post-session review.",
                "Promote a model only when validation beats a simple baseline and the prediction target matches the coaching decision.",
                "Keep Garmin load subordinate to trail-specific quality when the venue under-represents impact, such as PCP jump-line sessions or uplift DH.",
                "Prefer fewer stronger artifacts in the coach packet over broad artifact lists that do not change the recommendation.",
            ],
            "deprecation_rules": [
                "If a tool is a placeholder or unconfigured source, label it low authority until it has real data and a test that proves the output changes a coaching call.",
                "If two artifacts answer the same question, keep the one closer to the decision surface and retire or merge the weaker one.",
                "Do not delete raw evidence, compatibility wrappers, or tests simply to reduce file count; remove only stale behavior after the workflow has a safer replacement.",
            ],
        },
        "athlete_model": {
            "current_phase": current_phase,
            "current_category": (athlete.get("rider_category") or {}).get("current"),
            "target_category": (athlete.get("rider_category") or {}).get("target"),
            "highest_return_sequence": [
                "bike-specific continuity",
                "enduro repeatability",
                "expert skill execution under fatigue",
            ],
            "individual_response": [
                "Bike-specific load and frequency are Clayton's main durable fitness currency.",
                "Total load can be a false signal when elliptical, run, or gym replaces bike work.",
                "Structured bike tempo/threshold improves sustained power, but must be recoverable and placed away from key trail days.",
                "High MTB months can build durability while hiding P20 if there is no clean power expression.",
                "Gym supports durability only when it does not steal trail quality.",
                "Directive macro standards help only when the daily Garmin/readiness layer prevents them from becoming unabsorbed density.",
            ],
            "do_not_assume": [
                "Do not treat 222 W as current FTP.",
                "Do not count elliptical load as bike-specific maintenance.",
                "Do not treat Garmin readiness as direct trail-skill readiness without subjective notes.",
                "Do not use historical finger injury as a current training gate.",
                "Do not copy a full ideal week when life load, sleep, HRV, arm pump, or back-to-back trail plans require density control.",
            ],
        },
        "equipment_model": athlete.get("equipment", {}),
        "evidence_basis": evidence,
        "decision_hierarchy": [
            "Sabbath hard rest and current readiness.",
            "Garmin freshness: wellness, activity, and training status must be current for hard guidance.",
            "Current phase and recent load: avoid spikes while rebuilding.",
            "Bike-specific continuity: protect the weekly minimum before adding non-bike work.",
            "MTB specificity: protect trail quality and durability exposures before gym or extra intensity.",
            "Power prescription: use current controlled efforts, not stale historical P20.",
            "Nutrition and heat: fuel technical quality before late-session skill degradation appears.",
        ],
        "session_contract": {
            "required_fields": [
                "purpose",
                "dose",
                "adaptation_hypothesis",
                "execution_rules",
                "expected_result",
                "stop_rules",
                "post_session_review_fields",
            ],
            "purpose": "Every prescription must state exactly what adaptation it is buying: continuity, torque/threshold, repeatability, braking durability, fork setup, jump timing, wet-tech confidence, or recovery.",
            "dose": "Specify duration, repeats, descents/stages, load cap, RPE/HR/power cap where useful, and what makes the session complete.",
            "adaptation_hypothesis": "State what should improve and what next-day response would mean the dose was absorbed.",
            "execution_rules": "Describe how to ride the session and what not to chase.",
            "expected_result": "Store expected Garmin load/range, high-intensity minutes, RPE/feel, and next-day readiness expectation before training when the predictive loop is used.",
            "stop_rules": [
                "End technical work if braking timing gets lazy or line choice becomes reactive.",
                "End DH/jump quality if arm pump changes grip, brake modulation, or body position.",
                "End intensity if HR drift or RPE turns a controlled session into survival.",
                "Downshift immediately if rain or wet roots/rocks raise consequence beyond the session purpose.",
            ],
        },
        "predictive_training_loop": {
            "purpose": "Turn each prescription into a testable expectation for Clayton's digital twin.",
            "pre_session": [
                "Store expected duration, training load, high-intensity minutes, RPE range, and next-day response before training.",
                "Store the adaptation hypothesis and the execution stop rules so the review can judge quality, not only load.",
                "Store an execution-drift stress test when similar Clayton prescriptions historically became longer or harder than written.",
                "Use the coaching-adjusted strain response for the practical call while keeping the raw tree output visible for audit.",
                "Mark model confidence from validation; experimental predictions can guide questions but should not govern training automatically.",
            ],
            "post_session": [
                "After Garmin sync, compare actual load, self-evaluation, and next-day response with the stored expectation.",
                "If actual load differs materially, classify execution/adherence before judging adaptation.",
                "Only matched-load sessions are eligible for digital-twin calibration; drifted sessions update execution-risk rules first.",
                "If actual load matches but response misses, treat the miss as a model-calibration signal and inspect heat, fueling, sleep, stress, trail violence, and sensor quality.",
                "If load matches but trail skill faded, treat the workout as under-fuelled, under-recovered, too technically dense, or poorly targeted until notes prove otherwise.",
            ],
            "artifacts": [
                "snapshots/predictive_session_plan.json",
                "snapshots/predictive_session_review.json",
                "snapshots/predictive_training.json",
                "snapshots/predictive_backtest_10_dates.json",
            ],
        },
        "macrocycle": [
            {
                "phase": "base_rebuild",
                "status": "current",
                "duration_weeks": [4, 6],
                "purpose": "Restore repeatable bike-specific weekly rhythm without reintroducing a load spike.",
                "minimums": {
                    "bike_touches_per_week": 2,
                    "preferred_bike_touches_per_week": 3,
                    "preferred_bike_load_per_week": [250, 400],
                    "mtb_exposures_per_week": [1, 2],
                },
                "key_sessions": [
                    "Indoor tempo/torque: 3x8 progressing to 3x12 before raising power.",
                    "MTB quality: low-to-moderate volume, high focus on braking, line choice, and corner exits.",
                    "MTB durability: repeat climbs/descents while preserving final-descent quality.",
                ],
                "exit_gate": "Three consecutive weeks with 2-3 bike touches, at least 1-2 MTB exposures, no poor next-morning response, and no trail-skill collapse late in rides.",
            },
            {
                "phase": "mtb_specificity",
                "duration_weeks": [4, 8],
                "purpose": "Turn rebuilt bike continuity into Bukit Kiara enduro repeatability.",
                "minimums": {
                    "mtb_exposures_per_week": 2,
                    "structured_bike_session_per_week": 1,
                    "support_strength_sessions_per_week": [1, 2],
                },
                "key_sessions": [
                    "Quality trail day: braking/cornering/line choice with full-focus reps.",
                    "Durability trail day: 3-5 repeat climbs and descents, fuelled.",
                    "Indoor bike: tempo/threshold durability or short repeatability, not both in the same overloaded week.",
                ],
                "exit_gate": "Can complete a practice-style trail day and feel capable of attacking the next bike day after normal recovery.",
            },
            {
                "phase": "performance_build",
                "duration_weeks": [4, 6],
                "purpose": "Layer threshold, VO2, and 2-5 min repeat power on top of trail durability.",
                "minimums": {
                    "mtb_exposures_per_week": 2,
                    "structured_intensity_sessions_per_week": [1, 2],
                    "fresh_power_test_required_before_ftp_based_prescription": True,
                },
                "key_sessions": [
                    "Fresh P20/FTP assessment when readiness and bike continuity are stable.",
                    "Repeat 2-5 min punch work for stage surges and climb attacks.",
                    "Race-format simulations with controlled Saturday/Sunday proxy around Sabbath constraints.",
                ],
                "exit_gate": "Power and trail handling improve together without needing non-bike load to prop up total training volume.",
            },
            {
                "phase": "expert_specific_work",
                "duration_weeks": [4, 8],
                "purpose": "Train aggressive technical execution: jumps, drops, slides, heavy braking, and speed under fatigue.",
                "minimums": {
                    "quality_skill_exposure_per_week": 1,
                    "durability_or_race_sim_exposure_per_week": 1,
                    "recovery_protected": True,
                },
                "key_sessions": [
                    "PCP jump/scrub timing and landing tolerance.",
                    "Wet-tech braking and line choice sessions when conditions allow.",
                    "Enduro stage repeatability with fuelling and heat strategy.",
                ],
                "exit_gate": "The final run of the day is technically assertive, not merely survived.",
            },
        ],
        "weekly_architecture": {
            "default_week": {
                "monday": "Indoor tempo/torque or easy bike touch.",
                "tuesday": "Rest, mobility, or short support strength.",
                "wednesday": "MTB quality/skill day when available.",
                "thursday": "Recovery elliptical HR-capped near 120 bpm or short strength support.",
                "friday": "MTB durability/enduro-volume when available.",
                "saturday": "PCP/jump/skills or second trail exposure; downshift if driving/event load is high.",
                "sunday": "Sabbath hard rest.",
            },
            "minimum_viable_week": [
                "Two bike touches: one indoor torque/tempo and one MTB ride.",
                "One short support strength or mobility dose only if it does not reduce bike quality.",
                "No attempt to replace missed bike with hard elliptical and call it equivalent.",
            ],
            "high_return_week": [
                "Three bike touches: indoor tempo/torque, MTB quality, MTB durability.",
                "One short strength support session.",
                "One recovery elliptical only if it improves next-day freshness.",
            ],
            "six_week_rebuild_targets": {
                "bike_specific_sessions_per_week": {"minimum": 3, "ideal": "4-5 only when recovery and calendar support it"},
                "mtb_sessions_per_week": {"minimum": 2, "ideal": "2-3 with one quality and one durability exposure"},
                "weekly_bike_load": {"minimum": "350-450", "ideal_by_weeks_5_to_6": "450-600"},
                "hard_sessions_per_week": {"minimum": 2, "maximum": 3},
                "strength_sessions_per_week": {"minimum": 1, "ideal": "2 small doses only if trail quality is unaffected"},
                "elliptical_or_run": "Recovery/support only; never counted as bike-specific maintenance.",
            },
            "density_governor": [
                "Start from three good bike touches before chasing four or five.",
                "If Friday and Saturday are both trail days, Thursday becomes primer or recovery, not repeatability intervals.",
                "If a Tuesday or Wednesday MTB ride creates arm pump or high load, remove lower-body strength and indoor intensity until the key trail days are protected.",
                "Use one hard engine session plus one or two trail-specific sessions as the default; do not stack threshold, repeatability, and two hard MTB days in the same rebuild week unless recovery is clearly green.",
                "When in doubt, protect the session that most resembles the goal event demand.",
            ],
            "back_to_back_proxy": "Use Friday practice-style exposure plus Saturday attack-style effort to train race weekend demands while preserving Sunday Sabbath.",
        },
        "session_library": {
            "indoor_tempo_torque": {
                "purpose": "Rebuild bike force and threshold durability without stale FTP chasing.",
                "progression": ["3x8 min", "3x10 min", "3x12 min", "raise watts only after repeatability is easy"],
                "cadence_bias": "65-75 rpm unless knee/hip comfort says otherwise.",
                "power_guidance": "Use current RPE/HR response; initial useful work is likely around controlled tempo, not historical 222 W P20 assumptions.",
                "stop_rule": "Stop adding work when cadence/form degrades or HR drift makes the final rep survival.",
            },
            "enduro_repeatability_intervals": {
                "purpose": "Train repeated hard efforts after descents and repeated 2-5 min punch without turning every week into VO2 survival.",
                "starter_dose": "2 sets of 5x30 sec hard / 90 sec easy, 6 min easy between sets.",
                "progression": ["2x5 reps", "2x6 reps", "3x5 reps", "deload with 6x20 sec", "later progress toward 5-6x2 min hard / 3 min easy"],
                "placement_rule": "Use only when it does not blunt the next key MTB exposure; skip it in weeks with Friday/Saturday back-to-back trail focus.",
            },
            "mtb_quality_skill": {
                "purpose": "Raise expert-skill ceiling while fresh enough to learn.",
                "focus_rotation": ["heavy braking", "corner entry and exit", "wet roots/rocks", "line choice", "jump speed/scrub timing"],
                "volume_rule": "Low to moderate volume; end before slop becomes the main thing being rehearsed.",
                "example_dose": "6-8 focused reps of one corner, chute, jump timing, or braking-release point; finish with 1-2 smooth descents at controlled pace.",
            },
            "mtb_durability_enduro": {
                "purpose": "Build the ability to keep climbing and descending without losing aggression.",
                "progression": ["2-3 repeat climbs/descents", "3-4 repeats", "4-6 repeats", "practice-day simulation"],
                "quality_rule": "The last descent must still be technically sharp.",
            },
            "kiara_practice_race_proxy": {
                "purpose": "Rebuild the ability to absorb a chill practice-style day and still ride aggressively the next key day.",
                "progression": ["3 stages", "4 stages", "5 stages", "deload with 2-3 easy stages", "5 stages with selected timing", "6-stage controlled simulation"],
                "execution": "Climb mostly Z2/tempo, use short controlled surges late in selected climbs, descend smooth and precise, stop when descending quality drops below 7/10.",
                "fueling_priority": "Start carbs in the first 20 min; late braking mistakes and timid riding may be heat or fuel failures.",
            },
            "bukit_dinding_dh_setup": {
                "purpose": "Develop DH line confidence, braking durability, arm-pump tolerance, and suspension setup without confusing uplift load with pedaled enduro load.",
                "execution": "Classify access as uplift, self-pedaled, or mixed. For uplift days, prescribe by descent count and quality; for self-pedaled days, also cap climbing load.",
                "setup_rule": "Change only one suspension variable at a time and compare arm pump, brake dive, front grip, harshness, and corner-entry support.",
                "current_enduro_fork_note": "For the next Dinding test, compare baseline against 1 click more rebound damping and 1 click less low-speed compression damping on the Zeb Ultimate.",
            },
            "low_cost_skill": {
                "purpose": "Maintain skill touch without stealing from the next durability or DH day.",
                "options": ["pump and squash timing", "corner-to-jump timing", "low-speed wet-tech braking release", "flat-corner exit speed"],
                "dose": "60-90 min max, full recovery between quality reps, no big sends when tired.",
            },
            "strength_support": {
                "purpose": "Support braking, descending posture, impact tolerance, and long-run durability.",
                "dose": "1-2 sessions/week, 20-35 min.",
                "movements": ["split squat or lunge", "hip hinge/RDL", "calf raise", "row/pull", "push-up/press", "anti-rotation core", "loaded carry"],
                "interference_rule": "Any DOMS or dead legs that reduces Friday/Saturday trail quality means the gym dose was too high or badly placed.",
            },
            "elliptical_recovery": {
                "purpose": "Recovery, circulation, and aerobic support without replacing bike-specific work.",
                "dose": "30-45 min, HR cap around 120 bpm.",
                "red_flag": "Hard elliptical during a low-bike week hides the problem instead of solving it.",
            },
        },
        "fueling_architecture": {
            "daily": {
                "protein_g_per_kg": [1.6, 2.2],
                "carbs_match_training": "Use higher carbs on MTB, tempo, race-simulation, and heat-heavy days.",
            },
            "during_mtb_90_to_150_min": evidence.get("nutrition_targets", {}).get("mtb_90_to_150_min"),
            "during_mtb_over_150_min_or_race_practice": evidence.get("nutrition_targets", {}).get("mtb_over_150_min_or_race_practice"),
            "heat_rule": "In Kuala Lumpur, treat fuel/hydration as skill protection: sloppy braking, weak pumping, timid jumps, or poor line choice late can be carb/sodium/heat failure.",
        },
        "progression_rules": [
            "Progress only one lever at a time: ride count, MTB technical consequence, duration, intensity, or gym load.",
            "A week with fewer than 2 bike touches is a maintenance failure for MTB goals unless there is a clear logistical reason.",
            "A week with high total load but low bike load is not a build week for enduro.",
            "After two MTB days close together, use next-morning feel and upper-body durability response before adding intensity.",
            "Use deloads by cutting non-essential load first, then intensity, while preserving at least easy bike touch when possible.",
            "If sleep is under 5.5 hours and HRV is unbalanced, remove intensity and keep only skill, Z2, recovery, or rest.",
            "If legs feel good but arms/grip are dead, skip jumps and high-speed DH; use braking/cornering control or recover.",
            "If Body Battery wakes low and key trail days are ahead, cut indoor intensity before cutting the protected trail day.",
            "Historical finger injury is retained as context only; it must not gate current riding unless Clayton reports a new symptom.",
        ],
        "logging_contract": {
            "why": "Garmin sees engine and load; it does not see expert-skill execution.",
            "after_mtb": [
                "ride purpose",
                "skill focus",
                "trail condition and wet/slippery notes",
                "heat/humidity feel",
                "carbs per hour",
                "fluid per hour",
                "sodium per hour",
                "caffeine if used",
                "grip confidence 1-10",
                "arm pump/braking fatigue 1-10",
                "late-ride skill fade yes/no",
                "best moment",
                "mistake to fix",
                "final descent quality compared with first descent",
            ],
            "after_gym": ["exercises", "sets/reps/load", "RPE", "24h soreness", "48h soreness", "effect on next trail day"],
        },
        "artifact_contract": {
            "config_context": "config/athlete_context.json remains the canonical athlete strategy store.",
            "coaching_architecture": "config/coaching_architecture.json is the durable training architecture for future agents.",
            "snapshot": "snapshots/training_architecture.json is the dated generated copy for audit.",
            "daily_decision_surface": "snapshots/coach_packet.json remains the same-day decision surface after sync/rebuild.",
        },
        "caveats": [
            "This architecture is observational and personal to Clayton's Garmin record.",
            "P20 is an observed expression metric; trail-heavy months can build real enduro fitness without a clean P20.",
            "Modern HRV/wake Body Battery is strongest from March 2026 onward.",
            "Subjective trail notes are required for expert-skill decisions.",
        ],
    }


def _text_report(artifact: dict) -> str:
    evidence = artifact["evidence_basis"]
    lines = [
        f"Clayton Training Architecture - {artifact['date']}",
        "",
        f"Mission: {artifact['mission']['performance_goal']}",
        f"North star: {artifact['mission']['north_star_metric']}",
        "",
        "Highest Return Sequence:",
    ]
    lines.extend(f"- {item}" for item in artifact["athlete_model"]["highest_return_sequence"])
    lines.extend(["", "Stack Governance:"])
    lines.extend(f"- {item}" for item in artifact["stack_governance"]["better_definition"])
    lines.extend(
        [
            "",
            "Evidence Basis:",
            f"- Full range: {evidence['full_range']['activity_start']} to {evidence['full_range']['activity_end']}, "
            f"{evidence['full_range']['counted_sessions']} counted sessions, load {evidence['full_range']['training_load']}",
            f"- MTB: {evidence['full_range']['mtb_sessions']} sessions, load {evidence['full_range']['mtb_load']}",
            f"- Indoor bike: {evidence['full_range']['indoor_bike_sessions']} sessions, load {evidence['full_range']['indoor_bike_load']}",
            f"- Recent 28d bike load: {evidence['recent_28d']['bike_specific_load']} "
            f"({evidence['recent_28d']['bike_specific_ratio']} ratio), MTB sessions {evidence['recent_28d']['mtb_sessions']}",
            "",
            "Current Architecture:",
        ]
    )
    for phase in artifact["macrocycle"]:
        status = f" [{phase['status']}]" if phase.get("status") else ""
        lines.append(f"- {phase['phase']}{status}: {phase['purpose']}")
    lines.extend(["", "Weekly Architecture:"])
    lines.extend(f"- {item}" for item in artifact["weekly_architecture"]["high_return_week"])
    lines.extend(["", "Non-Negotiables:"])
    lines.extend(f"- {item}" for item in artifact["progression_rules"])
    return "\n".join(lines) + "\n"


def build_training_architecture(
    root: str | Path | None = None,
    for_date: str | date | None = None,
) -> dict:
    target = parse_date(for_date) or today_local(DEFAULT_TIMEZONE)
    context = load_context(root)
    profile = build_adaptation_profile(root, target, days=None)
    hypotheses = build_training_hypotheses(root, target)
    audit = build_athlete_question_audit(root, target)
    artifact = _architecture(context, profile, hypotheses, audit, target)
    write_json(config_dir(root) / "coaching_architecture.json", artifact)
    write_json(snapshots_dir(root) / "training_architecture.json", artifact)
    write_text(snapshots_dir(root) / "training_architecture.txt", _text_report(artifact))
    return artifact
