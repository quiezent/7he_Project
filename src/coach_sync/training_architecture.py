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
    current_phase = (context.get("goal_progression") or {}).get("current_phase", "base_rebuild")
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
                    "Verify readiness, CNS readiness, training status, devices, gear, self-evaluation, and recent load before prescribing.",
                    "Store a pre-session expected result and compare it with actual load, RPE, feel, and next-day wellness.",
                    "Classify execution drift before calling a model miss or adaptation problem.",
                    "Use subjective trail notes to train what Garmin cannot see.",
                ],
            },
            "cns_readiness_model": {
                "purpose": "Estimate whether Clayton's nervous system can safely process speed, line choice, braking timing, jumps, setup tests, and technical consequence.",
                "trail_specific_field_gate": {
                    "purpose": "A clean car-park or low-speed opening gate cannot by itself prove trail-speed visual processing, decision timing or steering precision. Treat the first familiar low-consequence climb and technical segment as a second diagnostic gate before granting repeats.",
                    "sequence": [
                        "Run the written low-speed opening gate for gross coordination, steering symmetry, bike-body separation and obvious physical or CNS impairment.",
                        "Compare the first deliberately easy climb with its written RPE ceiling. Disproportionate RPE or unexpectedly effortful execution removes optional technical volume even when the opening gate was clean.",
                        "On the first familiar low-consequence trail segment, check visual horizon, line-choice latency, braking timing, intentional front tracking and whether normal execution feels automatic or requires unusually deliberate compensatory focus.",
                    ],
                    "fail_action": "A surprise deflection, delayed or missed line, reactive mid-turn braking, narrowed visual horizon, declining focus, or unusually high compensatory attention removes further setup attribution and optional descents; use the safest familiar egress.",
                    "chill_rule": "Chill reduces speed, physiological load and consequence, not attentional engagement. If Clayton cannot maintain deliberate visual processing at chill pace, stop technical work rather than using a setup change or extra confirmation lap to continue.",
                    "interpretation_rule": "A later clean run achieved through materially greater conscious focus may show that the bike setup is usable, but it does not prove normal CNS reserve or erase an earlier rider-error event.",
                },
                "inputs": [
                    "Subjective CNS notes: brain fog, vision narrowing, delayed line choice, braking timing, unclipping delay, motivation, and confidence.",
                    "Garmin wellness context: HRV status, overnight HRV versus baseline, resting HR, stress, sleep score, wake Body Battery, and current Body Battery.",
                    "Wear-state coverage: material internal Garmin gaps withhold positive low-stress credit; the gap itself is not fatigue or recovery evidence, while high observed stress may still lower the ceiling.",
                    "Completed rest-window cognition: poor post-rest clarity, prolonged sleep inertia, and structured illness may lower the CNS ceiling; positive stress or Body Battery response cannot raise it.",
                    "Post-activity self-evaluation: weak or very weak feel, especially when RPE is low.",
                    "Recent MTB neural cost: long technical duration, meaningful trail load, high-HR trail minutes, and back-to-back technical exposure, carried into the following 48 hours with declining weight.",
                ],
                "decision_role": "Use CNS readiness as a technical-consequence ceiling after Sabbath and physical readiness, not as a replacement for Garmin readiness.",
                "status_meaning": {
                    "ready": "No independent CNS cap; physical readiness, route consequence, and session contract still govern.",
                    "watch": "Skill practice can proceed only if narrow, familiar, and low-to-moderate consequence.",
                    "compromised": "Use recovery or low-consequence repetition only; no speed hunting, jump progression, race simulation, or setup testing.",
                    "impaired": "Recovery only for training purposes; no MTB quality, intervals, gym loading, or high-consequence technical work.",
                },
                "non_overrides": [
                    "CNS readiness cannot authorize intensity when physical readiness is red.",
                    "Good Body Battery cannot override brain fog, strained HRV, weak feel, or delayed decision speed.",
                    "Low load cannot justify technical consequence if CNS status is impaired or compromised.",
                ],
            },
            "sleep_work_timing_governor": {
                "decision_role": "Keep primary sleep opportunity, completed nap evidence, late cognitive-work exposure, and autonomic recovery separate. Sleep duration constrains the call independently even when Garmin sleep score, HRV, or Body Battery look good.",
                "current_context": "Clayton's AI workflow for NYSE and Nasdaq trading often extends late into the Kuala Lumpur evening. Train after the main sleep period and before the next market-work window; exact recurring work hours still require prospective logging.",
                "evidence_order": [
                    "Primary sleep start, end, duration, and rolling short-sleep density.",
                    "Actual prior work-end time, cognitive-work strain, late caffeine, and whether work crossed midnight.",
                    "Completed nap or sleep-extension timing, duration, sleep inertia, and post-nap mental clarity.",
                    "Fresh physical readiness, CNS processing, Garmin autonomic context, and recent training density.",
                ],
                "non_compensation_rules": [
                    "A good Garmin sleep score does not cancel a short primary sleep duration.",
                    "A Body Battery recharge later in the day is intraday recovery evidence; it must not be rewritten as the wake or morning anchor.",
                    "A planned nap is not evidence until completed and reported; a completed nap does not erase chronic short-sleep density.",
                    "Low average stress early in the day is partial pre-work evidence, not proof that the demanding workday was controlled.",
                    "A late endpoint cutoff does not make daily stress representative when the internal series contains a material wearable gap.",
                ],
                "wear_state_provenance": {
                    "artifact": "snapshots/wearable_coverage.json",
                    "decision_role": "coverage_interpretation_only_never_readiness_clearance",
                    "inputs": [
                        "Descriptor-aware all-day stress timestamps, valid values, negative sentinels, and missing timestamp slots.",
                        "Body Battery measured/missing status and reset boundaries, without bridging a delta across missing wear.",
                        "Direct target-date Fenix wrist-HR samples and timestamp gaps from get_heart_rates, interpreted only as optical-HR measurement availability at sample-transition resolution.",
                        "Strict target-date athlete-confirmed off-wrist start/end windows using bounded reason categories.",
                        "Recurring wearable habits as context only; exact dated occurrence and timing are never instantiated from a usual pattern.",
                        "Endpoint success, latest-attempt state, last success, and retained-response provenance kept separate from series coverage.",
                    ],
                    "classifications": [
                        "confirmed_optical_hr_measurement_unavailability",
                        "partially_confirmed_optical_hr_with_unexplained_internal_unavailability",
                        "confirmed_intentional_off_wrist",
                        "partially_confirmed_with_unexplained_internal_unavailability",
                        "confirmed_interval_plus_unexplained_internal_unavailability",
                        "unexplained_internal_unavailability_with_recurring_context",
                        "unexplained_internal_unavailability",
                        "insufficient_series_coverage",
                        "discordant",
                        "reported_routine_context_only",
                        "no_material_internal_unavailability",
                        "endpoint_unavailable",
                    ],
                    "hard_guards": [
                        "Missing or negative-sentinel samples never prove off-wrist status without athlete confirmation.",
                        "A direct wrist-HR gap confirms optical-HR measurement unavailability only; it cannot prove physical watch removal or assign dress-watch use, loose fit, showering, charging, or another cause.",
                        "A stress-only unavailable run with continuing wrist HR is not an optical-contact gap; retain it as stress-specific unavailability with unknown mechanism.",
                        "Keep measurement availability, physical wear state, and cause attribution as separate axes for every material run.",
                        "A recurring Sunday dress-watch habit cannot instantiate a dated window or exact timing.",
                        "Attribute every material run independently; an exact dated report explains only its overlap, and unmatched run time remains unexplained.",
                        "Wear-state context cannot convert a failed, empty, wrong-date, or retained endpoint attempt into success.",
                        "Never impute stress, Body Battery, sleep, HRV, steps, source cutoffs, or readiness across a wearable gap.",
                        "Material internal unavailability withholds positive low-stress credit in CNS and predictive models; high observed stress may still downshift.",
                        "Positive low-stress use requires dense target-date valid samples from near midnight through a declared cutoff at or after 18:00, with credible Garmin cadence and no uncovered tail.",
                        "Fresh target-date wear coverage supersedes any cached same-day model prediction that depended on low stress; mask it until rebuilt rather than letting a date-only cache preserve optimism.",
                        "Wear-state evidence cannot raise physical readiness, CNS readiness, technical consequence, or the written session.",
                    ],
                },
                "rest_recharge_window": {
                    "artifact": "snapshots/rest_recharge_window.json",
                    "classification_labels": [
                        "restorative",
                        "quiet_but_non_restorative",
                        "discordant",
                        "insufficient_evidence",
                    ],
                    "joined_evidence": [
                        "Athlete-confirmed local start/end and estimated sleep or rest duration.",
                        "Descriptor-aware pre/during/post all-day stress with negative Garmin sentinels excluded from numeric summaries.",
                        "Paired near-boundary Body Battery response and recharge-onset latency without replacing the canonical wake/current readiness anchors.",
                        "Primary-sleep shortfall and a seven-day observed sleep-opportunity shortfall proxy, explicitly not clinical sleep debt.",
                        "Structured illness status, exact or elapsed-duration-derived preceding 48-hour training load, sleep inertia, and post-window clarity.",
                    ],
                    "hard_guards": [
                        "A positive Garmin nap label may support occurrence; zero, empty, or missing Garmin nap data never disproves an athlete-confirmed nap.",
                        "Body Battery alone cannot classify a window as restorative and cannot promote high-consequence training.",
                        "Even a restorative result cannot raise physical readiness, the CNS ceiling, the technical ceiling, or the written session, and cannot erase primary-sleep, illness, freshness, density, Sabbath, or consequence constraints.",
                        "Poor post-rest clarity, prolonged inertia, and active or suspected illness may lower the CNS ceiling but positive rest evidence has no upward path.",
                        "Wrong-date outer or nested all-day data cannot satisfy a current rest window; latest failed/empty refresh state remains visible while the last nonempty target-date raw response is preserved.",
                    ],
                },
                "provisional_gates": {
                    "primary_sleep_under_5h": "Recovery only when accompanied by brain fog, delayed processing, poor coordination, or other red evidence; otherwise no speed, jumps, setup testing, maximal standing efforts, or high-consequence descending.",
                    "primary_sleep_5_to_under_6h": "Treat an isolated night as acute short-sleep amber, not automatic CNS compromise. When current clarity and coordination are normal, illness is absent, autonomic context is stable, rolling sleep and recent neural density are acceptable, and a consequence-sensitive field gate is clean, permit controlled indoor work or familiar capped MTB. Do not permit maximum speed, jumps, novel/high-consequence lines, race simulation, setup testing, or stacked variables.",
                    "two_consecutive_nights_under_6h": "Postpone or convert the next high-consequence session unless the head coach documents why fresh subjective/CNS evidence safely changes the call.",
                    "rolling_7d_under_6h": "Cap the week at two meaningful-cost sessions; a third bike touch must be easy and optional MTB or lower-body gym density is removed first.",
                    "primary_sleep_at_least_6_5h": "Normal session eligibility only when current physical readiness, CNS processing, density, route consequence, and the session contract also agree.",
                },
                "acute_short_sleep_field_gate": {
                    "admission": "Use only for an isolated 5-to-under-6-hour night with no brain fog, illness, coordination complaint, recent neural-density warning, or adverse HRV/RHR pattern.",
                    "opening_dose": "Begin with 10-15 minutes of easy movement plus one familiar low-consequence skill segment; this is an execution test, not automatic clearance.",
                    "pass_markers": [
                        "Normal visual scanning and decision speed.",
                        "Consistent braking markers and deliberate release.",
                        "Early familiar line choice with normal bike-body separation.",
                        "No unforced correction, RPE mismatch, or coordination error."
                    ],
                    "pass_action": "Permit one bounded familiar moderate-consequence submaximal dose inside the written contract; do not promote to maximum speed, novelty, jumps, race simulation, or setup testing.",
                    "fail_action": "Downshift to low-consequence recovery or end the session for narrowed vision, delayed line choice, reactive braking, unexpected foot or steering correction, repeated line miss, weak feel, or disproportionate RPE."
                },
                "placement_rules": [
                    "Preserve a late-afternoon pre-market window for recoverable Suito work when it fits Clayton's actual schedule.",
                    "Place key MTB late morning or early afternoon after the best sleep opportunity and after sleep inertia has cleared.",
                    "Do not add hard post-20:00 training without prospective evidence that it preserves sleep and next-day response.",
                    "If work timing prevents two protected MTB opportunities, use the minimum viable week: one controlled Suito session and one MTB session.",
                ],
                "validation": "Run a 2-3 week prospective segmented-sleep/work-timing audit before treating naps or a fixed clock-time schedule as Clayton-specific architecture.",
            },
            "garmin_diagnosis_arbitration": {
                "purpose": "Use Garmin's sport-specific diagnosis as a structured co-diagnostic signal while keeping the coach responsible for MTB-specific execution, route consequence, and adaptation logic.",
                "inputs": [
                    "Training Status feedback such as Productive, Maintaining, Recovery, Unproductive, Overreaching, or Strained.",
                    "Garmin acute/chronic workload ratio and status.",
                    "Load Focus buckets for low aerobic, high aerobic, and anaerobic relative to Garmin target ranges.",
                    "Freshness of wellness, activity, and training-status data.",
                    "Clayton's subjective sharpness and technical intent.",
                ],
                "allowed_outputs": {
                    "controlled_upgrade": "When Garmin status is Productive or Peaking, ACWR is optimal, readiness is not red, CNS is not compromised, and load focus shows the objective needs more specific stimulus, the coach may raise an easy plan into a bounded high-aerobic or MTB repeatability session.",
                    "hold_plan": "When Garmin status supports the written plan but does not clearly expose a useful stimulus gap, follow the plan and protect the next key session.",
                    "downshift": "When ACWR is non-optimal, Garmin status is Recovery/Unproductive/Overreaching/Strained, readiness is red, CNS readiness is impaired/compromised, or the session would stack the wrong load bucket, reduce the ceiling.",
                    "no_hard_guidance": "When Garmin freshness is limited, do not use stale status to justify intensity.",
                },
                "load_focus_rules": [
                    "If low aerobic is above target while high aerobic is below or inside target, do not keep adding only easy volume; consider controlled tempo, torque, or MTB repeatability if readiness, CNS readiness, and route consequence agree.",
                    "If anaerobic is near or above its upper target, avoid stacking sprints, VO2, or full-attack descents even when Productive.",
                    "If high aerobic is low and anaerobic is not near the upper target, a controlled high-aerobic session can be more useful than another recovery ride.",
                    "Garmin can raise the stimulus ceiling only within an explicit contract; it cannot authorize open-ended mission creep.",
                ],
                "non_overrides": [
                    "Sunday Sabbath.",
                    "Red readiness or hard-session avoid guidance.",
                    "Impaired or compromised CNS readiness.",
                    "Stale Garmin freshness.",
                    "Poor subjective sharpness, unsafe trail conditions, or technical consequence above the session purpose.",
                    "Schema v3 stop rules.",
                ],
            },
            "garmin_surface_governance": {
                "decision_boundary": "A non-empty Garmin response is not automatically usable evidence. Validate the required nested surface, keep latest-attempt state separate from last-known-good data, and reduce confidence rather than inferring absence.",
                "endpoint_states": [
                    "success",
                    "success_empty",
                    "failed",
                    "unsupported",
                    "not_attempted",
                ],
                "surface_manifest": "Use snapshots/garmin_surface_manifest.json to distinguish configured, collected, normalized, consumed, empty, failed, unsupported, and never-attempted Garmin surfaces.",
                "device_metadata_rule": "Devices & Apps is usable only when the activity response contains valid device metadata. Missing metadataDTO is unknown, not evidence of wrist HR.",
                "device_readiness_capability_rule": "Training Readiness capability is false only after a successful non-empty device response explicitly reports false for the registered devices; failed, empty, or capability-absent responses remain unknown.",
                "readiness_rule": "Reject wrong-date Training Readiness rows, expose source date and age, and keep the feed context-only behind Sabbath, physical readiness, CNS ceiling, and freshness constraints.",
                "latest_session_rule": "Use the compact latest-session block to interpret what load represented, but respect HR-source confidence, weather-unit gates, Garmin modeled-water limits, loop-estimation limits, and stale-power policy.",
                "moving_duration_rule": "For steep hiking, preserve Garmin movingDuration as raw and withhold stopped-time subtraction when timer and elapsed duration agree but reported moving time is physiologically or mechanically implausible.",
                "oxygenation_rule": "Keep daily, sleep, and activity-time Pulse Ox and respiration separate. Negative activity-period sentinels mean unavailable, not normal; absent activity samples cannot be inferred from sleep values, and wrist Pulse Ox cannot promote readiness or establish medical safety.",
            },
            "technical_interpretation_standard": {
                "clipless_body_position_frame": "Do not reduce clipless gains to 'riding more forward.' The expert interpretation is that clipless can provide enough foot security for Clayton to remain dynamically centred, maintain front-tyre authority, and use a larger range of bike-body separation.",
                "equipment_causality_rule": "Treat suspension and cockpit setup changes as secondary consequences of changed rider loading, speed, terrain, and confidence; do not generalize them into universal clipless setup rules.",
                "response_shape": [
                    "State the coach's verdict plainly.",
                    "Identify the primary rider adaptation before the equipment implication.",
                    "Name the technical mechanism in expert MTB terms.",
                    "Give validation and failure criteria.",
                    "Prescribe the next constraint or test.",
                ],
                "setup_validation_criteria": [
                    "Keep a fork rebound change only if the fork does not pack down under repeated hits.",
                    "Keep a shock rebound change only if the rear does not kick or wallow through repeated compressions.",
                    "Keep the combined chassis setting only if the bike recovers without pitching entering and exiting steep chutes or linked corners.",
                ],
                "voice": "Direct, diagnostic, specific, and willing to sharpen Clayton's interpretation rather than merely agree with it.",
            },
            "subagent_delegation_model": {
                "authorization": "Clayton has authorized bounded subagent delegation; the main assistant remains head coach, lead engineer, and final decision owner.",
                "governance": [
                    "Use subagents only for concrete, bounded work that materially improves the coaching or stack outcome.",
                    "Subagents may advise or implement scoped changes, but the main assistant owns final training prescriptions, code integration, and Git hygiene.",
                    "No subagent output can override fresh readiness evidence, Sunday Sabbath, CNS readiness, data-quality caveats, or the schema v3 session contract.",
                    "Subagent advice must state assumptions, confidence, evidence used, and what would change the recommendation.",
                ],
                "roles": {
                    "coaching_physiology": "Reviews load, adaptation, FTP/VO2, fatigue, readiness, CNS status, density, and week structure.",
                    "mtb_skills_and_setup": "Reviews braking, body position, line choice, bike-body separation, suspension feel, clipless adaptation, jumps, and descending quality.",
                    "garmin_data_engineer": "Maintains sync, artifact hygiene, lap parsing, gear/device audits, and fast same-day decision paths.",
                    "ai_prediction_researcher": "Tests action/state prediction, backtests, baseline comparisons, and whether model output should influence coaching.",
                    "nutrition_heat": "Reviews carbs, sodium, hydration, recovery, and Kuala Lumpur heat effects on technical execution.",
                    "qa_git_steward": "Checks tests, dirty worktree risk, GitHub backup, raw-data protection, and documentation alignment.",
                },
                "use_cases": [
                    "Parallel code review or implementation on disjoint file scopes.",
                    "Independent MTB setup interpretation when subjective trail notes are technically nuanced.",
                    "Model validity review before promoting a predictor into coaching decisions.",
                    "Data hygiene audit when sync, artifact freshness, or Garmin metadata becomes noisy.",
                ],
                "anti_patterns": [
                    "Spawning agents for vague brainstorming without a concrete deliverable.",
                    "Letting a subagent prescribe training without current readiness evidence.",
                    "Duplicating the same investigation across agents.",
                    "Allowing model novelty to outrank simple validated coaching rules.",
                ],
            },
            "operating_loop": [
                "Diagnose the current limiter from Garmin history plus recent subjective trail evidence.",
                "Choose the highest-specificity session that fits the week and recovery state.",
                "Resolve session provenance: explicit coach-authored plan overrides matching weekly intent, but Sabbath, physical readiness, data freshness, Garmin arbitration, and CNS ceilings can still replace or cap either source.",
                "Prescribe purpose, dose, adaptation hypothesis, execution rules, expected result, and stop rules.",
                "After sync, compare expected versus actual load, CNS readiness, self-evaluation, skill notes, and next-day response.",
                "Update the next prescription and the athlete model only when the evidence changes the coaching call.",
            ],
            "coaching_standard": "More is not better; more specific, better absorbed, and more repeatable is better.",
        },
        "athlete_model": {
            "current_phase": current_phase,
            "current_category": ((context.get("athlete") or {}).get("rider_category") or {}).get("current"),
            "target_category": ((context.get("athlete") or {}).get("rider_category") or {}).get("target"),
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
                "CNS readiness is separate from physical readiness; Body Battery rebound does not prove visual processing, decision speed, braking timing, or technical confidence are restored.",
            ],
            "do_not_assume": [
                "Do not confuse historical 222 W P20 with the current Garmin operational FTP; use the latest dated Garmin FTP surface with its clean-test-validity caveat.",
                "Do not count elliptical load as bike-specific maintenance.",
                "Do not treat Garmin readiness as direct trail-skill readiness without subjective notes.",
                "Do not infer exercise-time oxygenation or respiration from daily or sleep Garmin summaries.",
                "Do not treat low training load as permission for high-consequence MTB when CNS readiness is impaired or compromised.",
                "Do not use historical finger injury as a current training gate.",
                "Do not copy a full ideal week when life load, sleep, HRV, arm pump, or back-to-back trail plans require density control.",
            ],
        },
        "equipment_model": (context.get("athlete") or {}).get("equipment", {}),
        "evidence_basis": evidence,
        "decision_hierarchy": [
            "Sabbath hard rest and current readiness.",
            "CNS readiness: cap technical consequence, novelty, speed, jumps, enduro simulation, and setup testing when nervous-system processing is not restored.",
            "Garmin freshness: wellness, activity, and training status must be current for hard guidance; Garmin Training Readiness is separately dated context only.",
            "Garmin diagnosis arbitration: use Training Status, ACWR, and Load Focus to decide whether the session ceiling should downshift, hold, or allow a controlled upgrade.",
            "Current phase and recent load: avoid spikes while rebuilding.",
            "Bike-specific continuity: protect the weekly minimum before adding non-bike work.",
            "MTB specificity: protect trail quality and durability exposures before gym or extra intensity.",
            "Power prescription: use the current dated Garmin operational FTP plus RPE/HR validation; historical P20 is not FTP.",
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
            "post_session_review": "Record the required review fields after the session. For a calibratable session, record an explicit stop_rule_outcome. MTB/technical work must also record technical_quality_notes and late_session_skill_fade; a triggered-but-continued stop rule, degraded technical quality, or an incomplete review blocks full digital-twin calibration.",
            "stop_rules": [
                "End technical work if braking timing gets lazy or line choice becomes reactive.",
                "End DH/jump quality if arm pump changes grip, brake modulation, or body position.",
                "End intensity if HR drift or RPE turns a controlled session into survival.",
                "Downshift immediately if rain or wet roots/rocks raise consequence beyond the session purpose.",
                "Downshift technical consequence if brain fog, visual processing, braking timing, or line choice gets slower than the first quality rep.",
            ],
        },
        "predictive_training_loop": {
            "purpose": "Turn each prescription into a testable expectation for Clayton's digital twin.",
            "matched_route_repeat_load_prior": {
                "role": "Load-expectation prior only; it is separate from physiology and full digital-twin calibration eligibility.",
                "activation": "Opt in only through an explicit versioned session.action_identity; never infer a named route from prose or GPS shape.",
                "identity_schema_v1": {
                    "schema_version": 1,
                    "venue_key": "canonical venue key",
                    "route_key": "canonical named-route key",
                    "bike_key": "canonical bike key",
                    "access_key": "self_pedaled, uplift, or mixed",
                    "quality_descent_count": "positive integer",
                },
                "sample_rules": [
                    "Use only activity-scoped structured feedback recorded before the prediction date.",
                    "Require corroborating Garmin Gear, external-HR provenance, dated loop structure, official-load agreement, comparable duration, and independent activity dates.",
                    "Fewer than two comparable samples fails closed; retain the generic estimate for audit but do not simulate it as the matched action.",
                    "Keep the immutable dated prediction unchanged; a later route-repeat prior updates future prescriptions only.",
                ],
            },
            "pre_session": [
                "Store expected duration, training load, high-intensity minutes, RPE range, and next-day response before training.",
                "Store the adaptation hypothesis and the execution stop rules so the review can judge quality, not only load.",
                "Store the expected CNS and technical sharpness state for MTB sessions when recent stress, illness, or brain fog is part of the decision.",
                "Store an execution-drift stress test when similar Clayton prescriptions historically became longer or harder than written.",
                "Record separate state-basis, action, and next-day response dates; if target-day wellness is absent, use the prior wellness row only as an explicitly labelled state basis while simulating the action on its planned date.",
                "Use the coaching-adjusted strain response for the practical call while keeping the raw tree output visible for audit.",
                "Mark model confidence from validation; experimental predictions can guide questions but should not govern training automatically.",
            ],
            "post_session": [
                "After Garmin sync, compare actual load, self-evaluation, and next-day response with the stored expectation.",
                "If actual load differs materially, classify execution/adherence before judging adaptation.",
                "Only complete contract-quality sessions are eligible for digital-twin calibration: matched load, modality/session-count/duration alignment, explicit stop-rule outcome, complete relevant review fields, clean technical outcome when applicable, and next-day Garmin response. Keep physiology-only matches visible but do not use them as full calibration rows.",
                "Audit raw timing, elevation, moving/rest, device temperature, Garmin estimated water loss, power, Training Effect/TSS, Gear, HR-source confidence, matching loop context, fueling rates, and session/next-day CNS when available; these improve interpretation but cannot loosen calibration eligibility.",
                "If actual load matches but response misses, treat the miss as a model-calibration signal and inspect heat, fueling, sleep, stress, trail violence, and sensor quality.",
                "If load matches but trail skill faded, treat the workout as under-fuelled, under-recovered, too technically dense, or poorly targeted until notes prove otherwise.",
                "If CNS readiness misses the plan, update the technical-consequence gate before changing the physical load model.",
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
                    "preferred_bike_touches_per_week": 5,
                    "maximum_normal_build_bike_touches_per_week": 6,
                    "meaningful_cost_sessions_per_week_max": 3,
                    "low_cost_bike_touches_per_week": [2, 3],
                    "preferred_bike_load_per_week": [250, 400],
                    "mtb_exposures_per_week": [1, 2],
                },
                "key_sessions": [
                    "Indoor tempo/torque: 3x8 progressing to 3x12 before raising power.",
                    "MTB quality: low-to-moderate volume, high focus on braking, line choice, and corner exits.",
                    "MTB durability: repeat climbs/descents while preserving final-descent quality.",
                ],
                "exit_gate": "Three consecutive weeks near 5-6 bike touches when logistics allow, with 2-3 low-cost touches, at least 2 MTB exposures, no density breach, no poor next-morning response, and no trail-skill collapse late in rides.",
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
                    "current_garmin_ftp_allowed_for_ftp_relative_prescription": True,
                    "clean_test_recommended_before_maximal_threshold_validation": True,
                },
                "key_sessions": [
                    "Optional clean P20/FTP validation when readiness and bike continuity are stable; do not erase the current Garmin operational FTP while waiting.",
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
                "monday": "Preferred conversational run with Clayton's wife; optional short easy bike touch only if the run stays easy and recovery is clean.",
                "tuesday": "Protected MTB quality/engine exposure.",
                "wednesday": "Indoor low-aerobic bike continuity.",
                "thursday": "Protected MTB durability or technical-quality exposure.",
                "friday": "Indoor low-aerobic continuity or primer; shorten after a harder-than-written Thursday.",
                "saturday": "Optional third MTB skill-transfer/Enduro-bike exposure; skip for church/family obligations or density.",
                "sunday": "Sabbath hard rest.",
            },
            "minimum_viable_week": [
                "Two bike touches: one indoor torque/tempo and one MTB ride.",
                "One short support strength or mobility dose only if it does not reduce bike quality.",
                "No attempt to replace missed bike with hard elliptical and call it equivalent.",
            ],
            "high_return_week": [
                "Five bike touches: two protected MTB exposures plus three low-cost/controlled bike touches; a sixth is optional only when recovery and calendar support it.",
                "Only 2-3 sessions across bike and hard running may be meaningfully costly.",
                "One short strength support session.",
                "One recovery elliptical only if it improves next-day freshness.",
            ],
            "six_week_rebuild_targets": {
                "bike_specific_sessions_per_week": {"maintenance_floor": 2, "preferred": 5, "maximum_normal_build": 6},
                "low_cost_bike_touches_per_week": [2, 3],
                "mtb_sessions_per_week": {"minimum": 2, "ideal": "2-3 with one quality and one durability exposure"},
                "weekly_bike_load": {"minimum": "350-450", "ideal_by_weeks_5_to_6": "450-600"},
                "meaningful_cost_sessions_per_week": {"minimum": 2, "maximum": 3, "hard_run_counts": True},
                "strength_sessions_per_week": {"minimum": 1, "ideal": "2 small doses only if trail quality is unaffected"},
                "elliptical_or_run": "Recovery/support only; never counted as bike-specific maintenance.",
            },
            "density_governor": [
                "Build toward 5-6 bike touches through 2-3 low-cost Z1/Z2 or primer days; do not manufacture frequency with split files or unnecessary doubles.",
                "Keep only 2-3 sessions meaningfully costly, and count a hard run toward that cap.",
                "If Friday and Saturday are both trail days, Thursday becomes primer or recovery, not repeatability intervals.",
                "If a Tuesday or Wednesday MTB ride creates arm pump or high load, remove lower-body strength and indoor intensity until the key trail days are protected.",
                "Use one hard engine session plus one or two trail-specific sessions as the default; do not stack threshold, repeatability, and two hard MTB days in the same rebuild week unless recovery is clearly green.",
                "If CNS readiness is impaired or compromised, protect recovery and low-consequence repetition before protecting weekly load targets.",
                "Protect 2 MTB exposures per week and allow up to 3 when readiness, CNS readiness, logistics, and load density support it; more than 3 is an event/race block, not a default build week.",
                "When in doubt, protect the session that most resembles the goal event demand.",
            ],
            "back_to_back_proxy": "Use Friday practice-style exposure plus Saturday attack-style effort to train race weekend demands while preserving Sunday Sabbath.",
            "schedule_translation": {
                "rule": "The weekday template expresses session roles, not immutable clock placement. Resolve actual sessions around sleep opportunity, prior work-end time, planned market-work window, family/church obligations, readiness, and density.",
                "indoor_preference": "Use a late-afternoon pre-market slot for controlled Suito work when it leaves adequate transition time for work and does not delay the next primary sleep.",
                "mtb_preference": "Use late morning or early afternoon after the best sleep opportunity, cleared sleep inertia, and a fresh CNS check.",
            },
        },
        "session_library": {
            "indoor_tempo_torque": {
                "purpose": "Rebuild bike force and threshold durability using the current Garmin operational FTP, RPE, and HR without turning every bike touch into intensity.",
                "progression": ["3x8 min", "3x10 min", "3x12 min", "raise watts only after repeatability is easy"],
                "cadence_bias": "65-75 rpm unless knee/hip comfort says otherwise.",
                "power_guidance": "Use the latest dated Garmin operational FTP for relative targets and validate against RPE/HR; do not substitute historical 222 W P20 or treat Garmin estimation as a clean-test guarantee.",
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
            "cns_recovery_gate": {
                "purpose": "Return the nervous system to reliable technical processing before speed, jumps, enduro simulation, or setup testing.",
                "use_when": "Brain fog, low HRV, high stress, weak feel at low RPE, delayed line choice, or slow braking decisions are present.",
                "allowed_work": "Rest, easy circulation, mobility, familiar low-speed skill repetition, or short low-consequence bike touch.",
                "blocked_work": "Intervals, speed hunting, high-consequence descents, jump progression, race simulation, heavy gym loading, and stacked bike/setup variables.",
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
            "range_selection_rule": "Choose within the canonical band from same-day heat/humidity, actual sweat response, session consequence, and gut tolerance. Previous-session weather is not a forecast, and Garmin heat acclimation never reduces the target by itself.",
        },
        "progression_rules": [
            "Progress only one lever at a time: ride count, MTB technical consequence, duration, intensity, or gym load.",
            "A week with fewer than 2 bike touches is a maintenance failure for MTB goals unless there is a clear logistical reason.",
            "The active build target is 5-6 unique bike days, but missed sleep, work, church/family logistics, or a hard run may reduce the count; never game frequency with doubles or hidden intensity.",
            "A week with high total load but low bike load is not a build week for enduro.",
            "After two MTB days close together, use next-morning feel and upper-body durability response before adding intensity.",
            "Use deloads by cutting non-essential load first, then intensity, while preserving at least easy bike touch when possible.",
            "If sleep is under 5.5 hours and HRV is unbalanced, remove intensity and keep only skill, Z2, recovery, or rest.",
            "Evaluate primary sleep duration independently from Garmin sleep score, HRV, and Body Battery; autonomic green does not make sleep opportunity complete.",
            "If primary sleep is under 5 hours, remove high-consequence technical work and maximal intensity; use recovery when mental clarity, coordination, or CNS evidence is also impaired.",
            "If an isolated primary sleep is 5 to under 6 hours, use acute short-sleep amber: controlled familiar training requires normal current cognition and coordination, stable supporting context, low recent neural density, and a clean consequence-sensitive field gate; never auto-promote to maximum speed, jumps, novelty, race simulation, setup testing, or stacked variables.",
            "If rolling 7-day primary sleep remains under 6 hours, allow at most two meaningful-cost sessions and make the third bike touch easy.",
            "If legs feel good but arms/grip are dead, skip jumps and high-speed DH; use braking/cornering control or recover.",
            "If CNS readiness is impaired or compromised, remove technical consequence even when legs, motivation, or Body Battery look acceptable.",
            "If CNS is watch-level, use one familiar skill target and stop when decision speed or vision narrows.",
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
                "brain fog or mental clarity",
                "vision and line-choice speed",
                "braking timing compared with first descent",
                "unclipping delay or foot-security issues",
                "best moment",
                "mistake to fix",
                "final descent quality compared with first descent",
            ],
            "after_gym": ["exercises", "sets/reps/load", "RPE", "24h soreness", "48h soreness", "effect on next trail day"],
            "daily_work_sleep_timing": [
                "prior_work_start_local and prior_work_end_local",
                "whether work crossed midnight",
                "planned_next_work_start_local and expected cognitive demand",
                "late_caffeine_time_and_dose",
                "primary_sleep_start_local",
                "primary_sleep_end_local",
                "nap status: planned/completed/skipped",
                "nap_start_local and nap_end_local",
                "sleep_inertia_minutes",
                "mental_clarity_before_and_after_nap 1-10",
                "structured illness status",
            ],
        },
        "artifact_contract": {
            "config_context": "config/athlete_context.json remains the canonical athlete strategy store.",
            "coaching_architecture": "config/coaching_architecture.json is the durable training architecture for future agents.",
            "snapshot": "snapshots/training_architecture.json is the dated generated copy for audit.",
            "daily_decision_surface": "snapshots/coach_packet.json remains the same-day decision surface after sync/rebuild.",
            "cns_readiness": "snapshots/cns_readiness.json is the current technical-consequence ceiling after current_state rebuild.",
            "garmin_surface_manifest": "snapshots/garmin_surface_manifest.json inventories configured Garmin endpoints, attempt states, raw/normalized field coverage, eras/gaps, units, lineage, privacy checks, and actual downstream consumers.",
            "garmin_training_readiness": "snapshots/garmin_training_readiness_current.json is a separate context-only Garmin feed with source date, age, endpoint health, and tri-state device-capability provenance; it cannot replace physical readiness or the CNS ceiling.",
            "latest_session_evidence": "snapshots/current_state.json latest_session_evidence is the bounded raw-session interpretation block; every joined field must retain source, unit, and confidence limits.",
            "live_sync_provenance": "snapshots/last_live_sync_status.json and snapshots/sync_run_ledger.json preserve live-contact truth across later rebuild-only runs.",
            "oxygenation_respiration": "Dedicated Garmin Pulse Ox and respiration endpoints retain attempt state, coverage, valid samples, and negative sentinels. Daily or sleep values are contextual only and never imputed into an activity interval or used as medical diagnosis or training clearance.",
            "raw_key_session_detail": "activities/details and activities/fit preserve bounded rich Garmin API detail and original FIT/ZIP evidence; nested detail is private raw evidence, is excluded from longitudinal activity counts, and is never derived-cache cleanup material.",
            "rest_recharge_window": "snapshots/rest_recharge_window.json joins athlete-confirmed nap/rest timing to the retained Garmin all-day stress and Body Battery series, recharge latency, primary-sleep shortfall, illness, preceding 48-hour load, inertia, and clarity. It is upward-inert intraday context; poor cognition or illness may only lower the CNS ceiling.",
            "wearable_coverage": "snapshots/wearable_coverage.json separates endpoint health, optical-HR measurement availability, physical wear state, and cause attribution; joins only strict target-date athlete confirmation; withholds optimistic low-stress use across material gaps; and never imputes physiology or promotes training.",
            "weekly_plan": "snapshots/weekly_plan.json is the weekly intent layer; a matching session feeds today_plan unless an explicit coach-authored plan overrides it, and all sources still pass through freshness, Garmin, CNS, and Sabbath constraints.",
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
