from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
from pathlib import Path
from typing import Any, Iterable

from .evidence import activity_date, summarize_activity, wellness_snapshot_is_usable
from .io import read_json, write_json
from .paths import activities_dir, repo_root, snapshots_dir
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date, today_local
from .wellness import normalize_wellness_payload


ENDPOINT_STATES = {"success", "success_empty", "failed", "unsupported", "not_attempted"}
_MANIFEST_CACHE: dict[tuple, dict] = {}

WELLNESS_NORMALIZED_FIELDS = [
    "date",
    "source_fetched_at",
    "source_last_sync_timestamp_gmt",
    "source_data_cutoff_local",
    "source_data_cutoff_source",
    "source_data_cutoffs",
    "daily_summary_payloads_used",
    "available_payloads",
    "all_day_stress_endpoint_status",
    "all_day_stress_latest_attempt_status",
    "all_day_stress_last_success_at",
    "all_day_stress_sample_count",
    "all_day_stress_parsed_sample_count",
    "all_day_stress_target_date_sample_count",
    "all_day_stress_valid_sample_count",
    "all_day_stress_sentinel_sample_count",
    "all_day_stress_malformed_sample_count",
    "all_day_stress_other_date_sample_count",
    "all_day_stress_after_cutoff_sample_count",
    "all_day_stress_duplicate_timestamp_count",
    "all_day_stress_start_time_local",
    "all_day_stress_end_time_local",
    "all_day_stress_declared_cutoff_local",
    "all_day_stress_effective_cutoff_local",
    "all_day_stress_sample_cutoff_local",
    "all_day_stress_start_lag_minutes",
    "all_day_stress_tail_lag_minutes",
    "all_day_stress_density_ratio",
    "all_day_stress_parsed_density_ratio",
    "all_day_stress_coverage_sufficient",
    "all_day_stress_sufficiency_issues",
    "all_day_stress_cadence_seconds",
    "all_day_stress_material_unavailable_run_count",
    "all_day_stress_material_unavailable_minutes",
    "all_day_stress_longest_material_unavailable_minutes",
    "all_day_stress_low_positive_reward_eligible",
    "all_day_body_battery_sample_count",
    "all_day_body_battery_latest_value",
    "all_day_body_battery_latest_timestamp_local",
    "steps",
    "step_goal",
    "active_kcal",
    "bmr_kcal",
    "wellness_kcal",
    "resting_hr",
    "rhr_7d_avg",
    "min_hr",
    "max_hr",
    "avg_stress",
    "max_stress",
    "rest_stress_min",
    "low_stress_min",
    "medium_stress_min",
    "high_stress_min",
    "body_battery_wake",
    "body_battery_current",
    "body_battery_charge",
    "body_battery_drain",
    "body_battery_source",
    "body_battery_latest_timestamp",
    "body_battery_start_time_local",
    "body_battery_end_time_local",
    "body_battery_verified_morning_anchor",
    "body_battery_verified_anchor_source",
    "body_battery_post_wake_recharge",
    "body_battery_verification_status",
    "body_battery_verification_confidence",
    "sleep_score",
    "sleep_quality",
    "sleep_hours",
    "sleep_start_local",
    "sleep_end_local",
    "sleep_window_hours",
    "primary_sleep_hours",
    "nap_hours_reported",
    "total_sleep_hours_reported",
    "nap_reporting_status",
    "total_sleep_reporting_status",
    "sleep_duration_provenance",
    "sleep_efficiency_pct",
    "deep_sleep_hours",
    "light_sleep_hours",
    "rem_sleep_hours",
    "awake_sleep_hours",
    "sleep_stress",
    "restless_moments",
    "overnight_hrv",
    "hrv_status",
    "hrv_weekly_avg",
    "hrv_balanced_low",
    "hrv_balanced_upper",
    "avg_spo2",
    "sleep_spo2",
    "avg_respiration",
    "monitoring_altitude_m",
    "monitoring_altitude_source",
    "spo2_endpoint_status",
    "spo2_latest_attempt_status",
    "spo2_last_success_at",
    "spo2_response_date",
    "spo2_response_date_matches_target",
    "spo2_data_cutoff_local",
    "spo2_data_retained_after_degraded_attempt",
    "spo2_freshness",
    "spo2_daily_average_pct",
    "spo2_sleep_average_pct",
    "spo2_lowest_pct",
    "spo2_latest_pct",
    "spo2_latest_timestamp_local",
    "spo2_7d_average_pct",
    "spo2_hourly_aggregate_count",
    "spo2_hourly_valid_count",
    "spo2_hourly_sentinel_count",
    "spo2_hourly_malformed_count",
    "spo2_hourly_lowest_pct",
    "spo2_hourly_highest_pct",
    "spo2_hourly_start_local",
    "spo2_hourly_end_local",
    "spo2_single_reading_count",
    "spo2_single_valid_count",
    "spo2_continuous_reading_count",
    "spo2_continuous_valid_count",
    "spo2_in_activity_interpretation",
    "spo2_decision_use",
    "spo2_provenance",
    "respiration_endpoint_status",
    "respiration_latest_attempt_status",
    "respiration_last_success_at",
    "respiration_response_date",
    "respiration_response_date_matches_target",
    "respiration_data_cutoff_local",
    "respiration_data_retained_after_degraded_attempt",
    "respiration_freshness",
    "respiration_waking_average_brpm",
    "respiration_sleep_average_brpm",
    "respiration_lowest_brpm",
    "respiration_highest_brpm",
    "respiration_two_min_reported_count",
    "respiration_two_min_parsed_target_count",
    "respiration_two_min_valid_count",
    "respiration_two_min_sentinel_count",
    "respiration_two_min_activity_sentinel_count",
    "respiration_two_min_malformed_count",
    "respiration_two_min_other_date_count",
    "respiration_two_min_expected_count_through_cutoff",
    "respiration_two_min_series_coverage_ratio",
    "respiration_two_min_valid_measurement_ratio",
    "respiration_two_min_start_local",
    "respiration_two_min_end_local",
    "respiration_hourly_aggregate_count",
    "respiration_hourly_valid_count",
    "respiration_exercise_aligned_unavailable_interpretation",
    "respiration_decision_use",
    "respiration_provenance",
    "moderate_intensity_min",
    "vigorous_intensity_min",
    "weighted_intensity_min",
    "intensity_goal_min",
    "body_weight",
    "body_weight_kg",
    "bmi",
    "body_fat_pct",
    "body_water_pct",
    "muscle_mass_kg",
    "bone_mass_kg",
    "metabolic_age",
    "physique_rating",
    "visceral_fat",
    "body_composition_source",
    "body_composition_sample_time_gmt",
]

REST_RECHARGE_NORMALIZED_FIELDS = [
    "date",
    "status",
    "classification.label",
    "classification.confidence",
    "classification.rule_version",
    "classification.signals",
    "classification.reasons",
    "classification.missing_fields",
    "window.type",
    "window.occurrence_source",
    "window.start_local",
    "window.end_local",
    "window.time_in_bed_minutes",
    "window.estimated_sleep_minutes",
    "window.sleep_inertia_minutes",
    "window.post_clarity_low_10",
    "window.post_clarity_high_10",
    "garmin_nap_evidence.nap_time_status",
    "garmin_nap_evidence.interpretation",
    "objective_response.stress.pre",
    "objective_response.stress.during",
    "objective_response.stress.post",
    "objective_response.body_battery.pre",
    "objective_response.body_battery.during",
    "objective_response.body_battery.post",
    "objective_response.body_battery.recharge_onset",
    "context.primary_sleep_hours",
    "context.rolling_7d_primary_sleep_hours",
    "context.sleep_opportunity_debt_proxy",
    "context.shortfall_to_architecture_threshold",
    "context.illness",
    "context.preceding_48h_load",
    "provenance.source_endpoint",
    "provenance.endpoint_data_status",
    "provenance.endpoint_last_attempt_status",
    "provenance.endpoint_last_attempt_at",
    "provenance.endpoint_last_success_at",
    "provenance.endpoint_data_retained_after_degraded_attempt",
    "provenance.endpoint_data_cutoff_local",
    "provenance.retention_policy",
    "safety_contract",
]

WEARABLE_COVERAGE_NORMALIZED_FIELDS = [
    "date",
    "status",
    "classification.label",
    "classification.confidence",
    "classification.reasons",
    "observed_coverage.stress.sample_count",
    "observed_coverage.stress.valid_sample_count",
    "observed_coverage.stress.sentinel_sample_count",
    "observed_coverage.stress.malformed_sample_count",
    "observed_coverage.stress.discarded_outside_target_date_count",
    "observed_coverage.stress.discarded_after_cutoff_count",
    "observed_coverage.stress.cadence_seconds",
    "observed_coverage.stress.first_sample_local",
    "observed_coverage.stress.last_sample_local",
    "observed_coverage.stress.material_unavailable_run_count",
    "observed_coverage.stress.material_unavailable_minutes",
    "observed_coverage.stress.material_unavailable_runs",
    "observed_coverage.stress.positive_use_coverage.expected_start_local",
    "observed_coverage.stress.positive_use_coverage.expected_cutoff_local",
    "observed_coverage.stress.positive_use_coverage.valid_density_pct",
    "observed_coverage.stress.positive_use_coverage.start_boundary_complete",
    "observed_coverage.stress.positive_use_coverage.end_boundary_complete",
    "observed_coverage.stress.positive_use_coverage.cadence_credible",
    "observed_coverage.stress.positive_use_coverage.cutoff_reaches_decision_day",
    "observed_coverage.stress.positive_use_coverage.series_sufficient_for_low_stress_reward",
    "observed_coverage.body_battery.valid_level_sample_count",
    "observed_coverage.body_battery.missing_level_sample_count",
    "observed_coverage.body_battery.material_unavailable_minutes",
    "observed_coverage.optical_heart_rate.endpoint_status",
    "observed_coverage.optical_heart_rate.endpoint_usable",
    "observed_coverage.optical_heart_rate.payload_date",
    "observed_coverage.optical_heart_rate.sample_count",
    "observed_coverage.optical_heart_rate.measured_sample_count",
    "observed_coverage.optical_heart_rate.unavailable_sample_count",
    "observed_coverage.optical_heart_rate.cadence_seconds",
    "observed_coverage.optical_heart_rate.first_sample_local",
    "observed_coverage.optical_heart_rate.last_sample_local",
    "observed_coverage.optical_heart_rate.endpoint_start_local",
    "observed_coverage.optical_heart_rate.endpoint_cutoff_local",
    "observed_coverage.optical_heart_rate.material_unavailable_run_count",
    "observed_coverage.optical_heart_rate.material_unavailable_minutes",
    "observed_coverage.optical_heart_rate.material_unavailable_runs",
    "athlete_reported_windows",
    "athlete_reported_window_summary",
    "recurring_context",
    "sensor_alignment",
    "material_run_attribution",
    "attribution_summary",
    "decision_use.low_stress_positive_reward_eligible",
    "decision_use.high_observed_stress_may_still_downshift",
    "safety_contract",
    "provenance.endpoint_status",
    "provenance.endpoint_record_usable",
    "provenance.wellness_snapshot_date",
    "provenance.wellness_snapshot_date_matches_target",
    "provenance.endpoint_payload_date",
    "provenance.effective_sample_cutoff_local",
    "provenance.latest_attempt",
    "provenance.last_success_at",
    "provenance.retained_after_degraded_attempt",
    "provenance.retention_policy",
    "provenance.optical_hr_endpoint",
]

ACTIVITY_NORMALIZED_FIELDS = [
    "id",
    "date",
    "start_time_local",
    "end_time_local",
    "start_time_bucket",
    "type",
    "category",
    "counts_for_training_load",
    "duration_min",
    "distance_km",
    "training_load",
    "avg_hr",
    "max_hr",
    "avg_power",
    "normalized_power",
    "intensity_factor",
    "garmin_detected_ftp",
    "aerobic_te",
    "anaerobic_te",
    "hr_zone_min.z1",
    "hr_zone_min.z2",
    "hr_zone_min.z3",
    "hr_zone_min.z4",
    "hr_zone_min.z5",
]

TRAINING_STATUS_NORMALIZED_FIELDS = [
    "date",
    "source_payload_ok",
    "training_status_code",
    "training_status_feedback",
    "acute_chronic.status",
    "acute_chronic.ratio",
    "acute_chronic.acute_load",
    "acute_chronic.chronic_load",
    "load_focus.low_aerobic",
    "load_focus.high_aerobic",
    "load_focus.anaerobic",
    "load_focus.feedback",
    "vo2max.cycling_value",
    "vo2max.generic_value",
    "acclimation.heat_pct",
    "acclimation.heat_trend",
    "acclimation.altitude_acclimation",
    "acclimation.acclimation_percentage",
    "acclimation.previous_altitude_acclimation",
    "acclimation.previous_acclimation_percentage",
    "acclimation.current_altitude",
    "acclimation.previous_altitude",
    "acclimation.altitude_trend",
    "acclimation.altitude_date",
    "acclimation.altitude_local_timestamp",
    "acclimation.units",
    "acclimation.provenance",
]

TRAINING_READINESS_NORMALIZED_FIELDS = [
    "date",
    "target_date",
    "source_snapshot_date",
    "source_availability_status",
    "freshness",
    "status",
    "decision_use",
    "source_endpoint",
    "score",
    "level",
    "feedback",
    "factors",
    "endpoint_health",
    "source_fetched_at",
    "device_capability",
]

WELLNESS_ENDPOINT_OUTPUTS = {
    "get_stats": [
        "steps",
        "step_goal",
        "active_kcal",
        "resting_hr",
        "avg_stress",
        "body_battery_wake",
        "body_battery_current",
        "avg_spo2",
        "avg_respiration",
        "weighted_intensity_min",
    ],
    "get_user_summary": [
        "steps",
        "resting_hr",
        "avg_stress",
        "body_battery_wake",
        "body_battery_current",
    ],
    "get_all_day_stress": [
        "all_day_stress_sample_count",
        "all_day_stress_valid_sample_count",
        "all_day_stress_sentinel_sample_count",
        "all_day_stress_start_time_local",
        "all_day_stress_end_time_local",
        "all_day_stress_cadence_seconds",
        "all_day_stress_material_unavailable_run_count",
        "all_day_stress_material_unavailable_minutes",
        "all_day_stress_longest_material_unavailable_minutes",
        "all_day_stress_low_positive_reward_eligible",
        "all_day_body_battery_sample_count",
        "all_day_body_battery_latest_value",
        "all_day_body_battery_latest_timestamp_local",
    ],
    "get_heart_rates": [
        "observed_coverage.optical_heart_rate.sample_count",
        "observed_coverage.optical_heart_rate.material_unavailable_runs",
        "material_run_attribution.measurement_availability",
        "material_run_attribution.optical_hr_measurement",
        "material_run_attribution.device_wear_state",
        "material_run_attribution.cause_attribution",
    ],
    "get_spo2_data": [
        "spo2_daily_average_pct",
        "spo2_sleep_average_pct",
        "spo2_lowest_pct",
        "spo2_latest_pct",
        "spo2_hourly_aggregate_count",
        "spo2_hourly_lowest_pct",
        "spo2_hourly_highest_pct",
        "spo2_single_reading_count",
        "spo2_continuous_reading_count",
        "spo2_provenance",
    ],
    "get_respiration_data": [
        "respiration_waking_average_brpm",
        "respiration_sleep_average_brpm",
        "respiration_lowest_brpm",
        "respiration_highest_brpm",
        "respiration_two_min_valid_count",
        "respiration_two_min_sentinel_count",
        "respiration_two_min_activity_sentinel_count",
        "respiration_two_min_series_coverage_ratio",
        "respiration_hourly_aggregate_count",
        "respiration_provenance",
    ],
    "get_body_battery": [
        "body_battery_current",
        "body_battery_charge",
        "body_battery_drain",
        "body_battery_latest_timestamp",
    ],
    "get_body_battery_events": [],
    "get_sleep_data": [
        "sleep_score",
        "sleep_hours",
        "sleep_start_local",
        "sleep_end_local",
        "sleep_window_hours",
        "primary_sleep_hours",
        "nap_hours_reported",
        "total_sleep_hours_reported",
        "nap_reporting_status",
        "total_sleep_reporting_status",
        "sleep_duration_provenance",
        "sleep_efficiency_pct",
        "deep_sleep_hours",
        "light_sleep_hours",
        "rem_sleep_hours",
        "awake_sleep_hours",
        "sleep_stress",
        "overnight_hrv",
        "hrv_status",
        "avg_spo2",
        "sleep_spo2",
        "avg_respiration",
    ],
    "get_hrv_data": [
        "overnight_hrv",
        "hrv_status",
        "hrv_weekly_avg",
        "hrv_balanced_low",
        "hrv_balanced_upper",
    ],
    "get_body_composition": [
        "body_weight_kg",
        "bmi",
        "body_fat_pct",
        "body_water_pct",
        "muscle_mass_kg",
        "bone_mass_kg",
    ],
}


def _endpoint_specs() -> dict[str, dict]:
    wellness_consumers = [
        "wellness_daily",
        "wellness_trends",
        "wellness_verification",
        "readiness",
        "cns_readiness",
        "current_state",
        "coach_packet",
    ]
    specs: dict[str, dict] = {}
    for method, outputs in WELLNESS_ENDPOINT_OUTPUTS.items():
        rest_recharge_only = method == "get_all_day_stress"
        contact_provenance_only = method == "get_heart_rates"
        oxygenation_context_only = method in {"get_spo2_data", "get_respiration_data"}
        consumers = (
            [
                "wellness_daily",
                "wellness_trends",
                "rest_recharge_window",
                "wearable_coverage",
                "current_state",
                "coach_packet",
                "cns_readiness_coverage_guard",
                "training_predictor_coverage_guard",
                "body_battery_model_coverage_guard",
            ]
            if rest_recharge_only
            else ["wearable_coverage", "current_state", "coach_packet"]
            if contact_provenance_only
            else [
                "wellness_daily",
                "wellness_trends",
                "readiness_features_context_only",
                "current_state",
                "coach_packet",
            ]
            if oxygenation_context_only
            else wellness_consumers
        )
        specs[f"wellness.{method}"] = {
            "method": method,
            "group": "wellness",
            "collection_mode": "automatic_live_sync",
            "source_paths": ["snapshots/garmin_wellness_*.json"],
            "normalized_outputs": outputs,
            "downstream_consumers": consumers,
            "decision_use": (
                "intraday_coverage_and_recovery_context_never_session_clearance"
                if rest_recharge_only
                else "wear_state_contact_provenance_only_never_physiology_or_session_clearance"
                if contact_provenance_only
                else "context_only_downshift_or_verify_never_readiness_promotion"
                if oxygenation_context_only
                else "verification_context_only"
                if method == "get_body_battery_events"
                else "direct_readiness_and_recovery"
            ),
        }
    specs.update(
        {
            "training.get_training_status": {
                "method": "get_training_status",
                "group": "training_status",
                "collection_mode": "automatic_live_sync",
                "source_paths": ["snapshots/garmin_training_status_*.json"],
                "normalized_outputs": TRAINING_STATUS_NORMALIZED_FIELDS,
                "downstream_consumers": [
                    "garmin_training_status_current",
                    "readiness",
                    "current_state",
                    "weekly_plan",
                    "today_plan",
                    "coach_packet",
                ],
                "decision_use": "co_diagnostic_after_readiness",
            },
            "training.get_training_readiness": {
                "method": "get_training_readiness",
                "group": "training_readiness",
                "collection_mode": "automatic_live_sync",
                "source_paths": ["snapshots/garmin_training_readiness_*.json"],
                "normalized_outputs": TRAINING_READINESS_NORMALIZED_FIELDS,
                "downstream_consumers": ["garmin_training_readiness_current", "current_state"],
                "decision_use": "context_only_custom_readiness_remains_authoritative",
            },
            "training.get_morning_training_readiness": {
                "method": "get_morning_training_readiness",
                "group": "training_readiness",
                "collection_mode": "automatic_live_sync",
                "source_paths": ["snapshots/garmin_training_readiness_*.json"],
                "normalized_outputs": TRAINING_READINESS_NORMALIZED_FIELDS,
                "downstream_consumers": ["garmin_training_readiness_current", "current_state"],
                "decision_use": "context_only_custom_readiness_remains_authoritative",
            },
            "training.get_cycling_ftp": {
                "method": "get_cycling_ftp",
                "group": "cycling_ftp",
                "collection_mode": "automatic_live_sync",
                "source_paths": ["snapshots/garmin_cycling_ftp_current.json"],
                "normalized_outputs": [
                    "ftp_w",
                    "effective_date",
                    "effective_at",
                    "sport",
                    "detection_source",
                    "biometric_source_type",
                    "latest_attempt",
                    "last_known_good",
                ],
                "downstream_consumers": [
                    "current_state",
                    "coach_packet",
                    "training_architecture",
                    "ftp_relative_prescription",
                ],
                "decision_use": "current_garmin_operational_ftp_with_rpe_hr_validation",
            },
            "capabilities.get_devices": {
                "method": "get_devices",
                "group": "device_capabilities",
                "collection_mode": "automatic_live_sync",
                "source_paths": ["snapshots/garmin_device_capabilities_*.json"],
                "normalized_outputs": ["training_readiness_capable", "registered_device_count"],
                "downstream_consumers": ["garmin_surface_manifest"],
                "decision_use": "capability_and_absence_interpretation",
            },
            "capabilities.get_unit_system": {
                "method": "get_unit_system",
                "group": "device_capabilities",
                "collection_mode": "automatic_live_sync",
                "source_paths": ["snapshots/garmin_device_capabilities_*.json"],
                "normalized_outputs": ["unit_system"],
                "downstream_consumers": ["garmin_surface_manifest"],
                "decision_use": "unit_provenance_only",
            },
            "activities.count_activities": {
                "method": "count_activities",
                "group": "activities",
                "collection_mode": "historical_backfill",
                "source_paths": ["snapshots/historical_backfill_status.json"],
                "normalized_outputs": [],
                "downstream_consumers": ["historical_backfill"],
                "decision_use": "collection_control_only",
            },
            "activities.get_activities": {
                "method": "get_activities",
                "group": "activities",
                "collection_mode": "automatic_live_sync_and_historical_backfill",
                "source_paths": ["activities/*.json"],
                "normalized_outputs": ACTIVITY_NORMALIZED_FIELDS,
                "downstream_consumers": [
                    "activity_profile",
                    "activity_summary_index",
                    "training_load",
                    "modality_load_rollups",
                    "cns_readiness",
                    "current_state",
                    "predictive_training",
                ],
                "decision_use": "direct_load_continuity_and_modality",
            },
            "metadata.get_activity_gear": {
                "method": "get_activity_gear",
                "group": "activity_metadata",
                "collection_mode": "automatic_recent_bike_activities",
                "source_paths": ["snapshots/activity_gear_index.json"],
                "normalized_outputs": ["gear", "gear_fetch_ok", "gear_fetch_error"],
                "downstream_consumers": ["gear_audit", "current_state"],
                "decision_use": "sensor_and_bike_context_confidence",
            },
            "metadata.get_activity_devices": {
                "method": "get_activity",
                "group": "activity_metadata",
                "collection_mode": "automatic_recent_bike_activities",
                "source_paths": ["snapshots/activity_device_index.json"],
                "normalized_outputs": [
                    "recording_device",
                    "apps",
                    "sensors",
                    "external_hr_sensor",
                    "external_hr_battery_statuses",
                ],
                "downstream_consumers": ["device_audit", "current_state"],
                "decision_use": "heart_rate_source_confidence",
            },
            "metadata.get_activity_self_evaluation": {
                "method": "get_activity",
                "group": "activity_metadata",
                "collection_mode": "automatic_recent_activity_detail",
                "source_paths": ["snapshots/activity_self_evaluation_index.json"],
                "normalized_outputs": [
                    "has_self_evaluation",
                    "feel_score",
                    "feel_label",
                    "rpe_score",
                    "rpe_label",
                    "rpe_out_of_10",
                ],
                "downstream_consumers": [
                    "self_evaluation_report",
                    "cns_readiness",
                    "current_state",
                    "predictive_training",
                ],
                "decision_use": "subjective_response_context",
            },
        }
    )
    for logical_name, method, use, outputs, consumers in (
        (
            "splits",
            "get_activity_splits",
            "loop_boundary_and_lap_analysis",
            ["activity_loop_load"],
            ["loop_load", "latest_session_evidence", "predictive_session_review"],
        ),
        (
            "details",
            "get_activity_details",
            "action_terrain_timeline_and_sample_inventory",
            ["activity_loop_load", "predictive_session_review.activity_detail"],
            ["loop_load", "latest_session_evidence", "predictive_session_review"],
        ),
        (
            "hr_zones",
            "get_activity_hr_in_timezones",
            "hr_zone_cross_check",
            ["activity_loop_load"],
            ["loop_load", "predictive_session_review"],
        ),
        (
            "power_zones",
            "get_activity_power_in_timezones",
            "retained_key_session_power_zone_context",
            [],
            [],
        ),
        (
            "weather",
            "get_activity_weather",
            "historical_heat_context_with_explicit_unit_gate_not_forecast",
            ["latest_session_evidence.environment.weather"],
            ["latest_session_evidence", "today_plan.nutrition_context", "predictive_session_review"],
        ),
        (
            "exercise_sets",
            "get_activity_exercise_sets",
            "key_gym_set_rep_rest_and_exercise_detection_context",
            ["latest_session_evidence.gym.detailed_sets"],
            ["latest_session_evidence", "coach_packet"],
        ),
        (
            "activity",
            "get_activity",
            "summary_metadata_device_self_evaluation_context",
            ["activity_device_index", "activity_self_evaluation_index"],
            ["device_audit", "self_evaluation_report", "latest_session_evidence", "predictive_session_review"],
        ),
        (
            "original_download",
            "download_activity_original",
            "durable_raw_evidence_preservation",
            [],
            [],
        ),
    ):
        specs[f"detail.{logical_name}"] = {
            "method": method,
            "group": "activity_detail",
            "collection_mode": "automatic_bounded_key_sessions_and_manual_loop_fetch",
            "source_paths": [
                "activities/details/garmin_*_detail.json",
                "activities/fit/*",
                "snapshots/activity_detail_*.json (legacy)",
            ],
            "normalized_outputs": outputs,
            "downstream_consumers": consumers,
            "decision_use": use,
        }
    return specs


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _mtime(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")


def _latest_mtime(paths: Iterable[Path]) -> str | None:
    existing = [path for path in paths if path.exists()]
    if not existing:
        return None
    return _mtime(max(existing, key=lambda item: item.stat().st_mtime_ns))


def _has_content(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (dict, list, tuple, set, str, bytes)):
        return bool(value)
    return True


def _bounded_error_category(value: Any) -> str:
    """Keep endpoint diagnostics useful without copying connector exception text."""
    text = str(value or "unknown_error").strip().lower().replace("-", "_")
    exact = {
        "unknown_error",
        "timeout",
        "network_error",
        "authentication_failed",
        "rate_limited",
        "missing_method",
        "client_method_unavailable",
        "response_date_mismatch",
        "response_date_missing",
        "missing_required_series",
        "empty_required_series",
        "unsupported_endpoint",
        "not_applicable",
        "activity_limit_zero",
        "original_download_format_unavailable",
    }
    if text in exact:
        return text
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "rate limit" in text or "too many requests" in text:
        return "rate_limited"
    if "auth" in text or "unauthorized" in text or "forbidden" in text:
        return "authentication_failed"
    if "network" in text or "connection" in text or "dns" in text:
        return "network_error"
    if "method" in text and ("missing" in text or "unavailable" in text):
        return "client_method_unavailable"
    return "connector_error_redacted"


def _state(stats: dict) -> str:
    attempts = int(stats.get("attempts") or 0)
    nonempty = int(stats.get("nonempty_successes") or 0)
    successes = int(stats.get("successes") or 0)
    unsupported = int(stats.get("unsupported") or 0)
    if nonempty:
        return "success"
    if successes:
        return "success_empty"
    if attempts and unsupported == attempts:
        return "unsupported"
    if attempts:
        return "failed"
    return "not_attempted"


def _observation_stats(observations: list[tuple[date | None, dict]]) -> dict:
    success_dates: list[date] = []
    nonempty_dates: list[date] = []
    errors: Counter[str] = Counter()
    successes = 0
    nonempty = 0
    unsupported = 0
    not_attempted = 0
    attempts = 0
    attempted_at_values: list[str] = []
    ordered_attempts: list[tuple[str, dict]] = []
    for observed_date, result in observations:
        explicit_status = str(result.get("status") or "").lower()
        attempted_at = result.get("attempted_at") or result.get("fetched_at")
        if isinstance(attempted_at, str):
            attempted_at_values.append(attempted_at)
            ordered_attempts.append((attempted_at, result))
        if explicit_status == "not_attempted":
            not_attempted += 1
            continue
        attempts += 1
        ok = explicit_status in {"success", "success_empty"} or result.get("ok") is True
        if ok:
            successes += 1
            if observed_date:
                success_dates.append(observed_date)
            response_nonempty = (
                explicit_status == "success"
                or (not explicit_status and _has_content(result.get("data")))
            )
            if response_nonempty:
                nonempty += 1
                if observed_date:
                    nonempty_dates.append(observed_date)
        else:
            raw_error = str(result.get("error") or "unknown_error")
            errors[_bounded_error_category(raw_error)] += 1
            if explicit_status == "unsupported" or raw_error in {
                "missing_method",
                "client_method_unavailable",
            }:
                unsupported += 1
    stats = {
        "observations": len(observations),
        "attempts": attempts,
        "not_attempted": not_attempted,
        "successes": successes,
        "nonempty_successes": nonempty,
        "empty_successes": max(0, successes - nonempty),
        "failures": max(0, attempts - successes),
        "unsupported": unsupported,
        "error_counts": [
            {"error": message, "count": count}
            for message, count in errors.most_common(10)
        ],
        "success_eras": contiguous_date_eras(success_dates),
        "nonempty_eras": contiguous_date_eras(nonempty_dates),
        "latest_attempted_at": max(attempted_at_values) if attempted_at_values else None,
    }
    latest_result = max(ordered_attempts, key=lambda item: item[0])[1] if ordered_attempts else None
    if latest_result is not None:
        explicit_status = str(latest_result.get("status") or "").lower()
        if explicit_status in ENDPOINT_STATES:
            latest_state = explicit_status
        elif latest_result.get("ok") is True:
            latest_state = "success" if _has_content(latest_result.get("data")) else "success_empty"
        elif str(latest_result.get("error") or "") in {
            "missing_method",
            "client_method_unavailable",
        }:
            latest_state = "unsupported"
        else:
            latest_state = "failed"
        stats["latest_attempt_state"] = latest_state
        stats["latest_attempt_error"] = (
            _bounded_error_category(latest_result.get("error"))
            if latest_result.get("error")
            else None
        )
    else:
        stats["latest_attempt_state"] = "not_attempted"
        stats["latest_attempt_error"] = None
    stats["state"] = _state(stats)
    return stats


def contiguous_date_eras(values: Iterable[date | str | None]) -> list[dict]:
    parsed = sorted(
        {
            parsed
            for value in values
            if value is not None and (parsed := parse_date(value)) is not None
        }
    )
    if not parsed:
        return []
    eras: list[dict] = []
    start = previous = parsed[0]
    count = 1
    for current in parsed[1:]:
        if current == previous + timedelta(days=1):
            previous = current
            count += 1
            continue
        eras.append(
            {
                "start": start.isoformat(),
                "end": previous.isoformat(),
                "calendar_days": (previous - start).days + 1,
                "observed_days": count,
            }
        )
        start = previous = current
        count = 1
    eras.append(
        {
            "start": start.isoformat(),
            "end": previous.isoformat(),
            "calendar_days": (previous - start).days + 1,
            "observed_days": count,
        }
    )
    return eras


def era_gaps(eras: list[dict]) -> list[dict]:
    gaps = []
    for earlier, later in zip(eras, eras[1:]):
        earlier_end = parse_date(earlier.get("end"))
        later_start = parse_date(later.get("start"))
        if not earlier_end or not later_start:
            continue
        missing = (later_start - earlier_end).days - 1
        if missing <= 0:
            continue
        gaps.append(
            {
                "after": earlier_end.isoformat(),
                "before": later_start.isoformat(),
                "missing_days": missing,
                "missing_start": (earlier_end + timedelta(days=1)).isoformat(),
                "missing_end": (later_start - timedelta(days=1)).isoformat(),
            }
        )
    return gaps


def _scan_wellness(root: Path, basis: date) -> tuple[dict, dict[str, dict]]:
    paths: list[Path] = []
    dates: list[date] = []
    usable_dates: list[date] = []
    observations: dict[str, list[tuple[date | None, dict]]] = defaultdict(list)
    latest_cutoff = None
    fetched_at_values: list[str] = []
    invalid_files = 0
    for path in sorted((root / "snapshots").glob("garmin_wellness_*.json")):
        raw_date = path.stem.removeprefix("garmin_wellness_")[:10]
        snap_date = parse_date(raw_date)
        if not snap_date or snap_date > basis:
            continue
        payload = read_json(path, None)
        paths.append(path)
        dates.append(snap_date)
        if not isinstance(payload, dict):
            invalid_files += 1
            continue
        if wellness_snapshot_is_usable(payload):
            usable_dates.append(snap_date)
        if isinstance(payload.get("fetched_at"), str):
            fetched_at_values.append(payload["fetched_at"])
        cutoff = normalize_wellness_payload(payload).get("source_data_cutoff_local")
        if cutoff and (latest_cutoff is None or cutoff > latest_cutoff):
            latest_cutoff = cutoff
        for item in payload.get("payloads") or []:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "unknown")
            observations[label].append((snap_date, item))
            latest_attempt = item.get("latest_attempt")
            if (
                isinstance(latest_attempt, dict)
                and item.get("status") == "success"
                and latest_attempt.get("status") != "success"
            ):
                observations[label].append((snap_date, latest_attempt))
    snapshot_eras = contiguous_date_eras(dates)
    usable_eras = contiguous_date_eras(usable_dates)
    endpoint_stats = {
        endpoint: _observation_stats(observations.get(endpoint, []))
        for endpoint in WELLNESS_ENDPOINT_OUTPUTS
    }
    summary = {
        "source_pattern": "snapshots/garmin_wellness_*.json",
        "snapshot_count": len(paths),
        "usable_snapshot_count": len(set(usable_dates)),
        "invalid_file_count": invalid_files,
        "date_span": {
            "first": min(dates).isoformat() if dates else None,
            "latest": max(dates).isoformat() if dates else None,
        },
        "snapshot_eras": snapshot_eras,
        "usable_eras": usable_eras,
        "gaps": era_gaps(usable_eras),
        "latest_source_modified_at": _latest_mtime(paths),
        "latest_fetched_at": max(fetched_at_values) if fetched_at_values else None,
        "latest_data_cutoff_local": latest_cutoff,
        "endpoints": endpoint_stats,
    }
    return summary, endpoint_stats


def _coverage_row(non_null: int, present: int, total: int) -> dict:
    return {
        "present_count": present,
        "non_null_count": non_null,
        "presence_pct": round(100 * present / total, 1) if total else None,
        "non_null_pct": round(100 * non_null / total, 1) if total else None,
    }


def _first_top_level(payload: dict, names: Iterable[str]) -> tuple[bool, Any]:
    for name in names:
        if name in payload:
            return True, payload.get(name)
    return False, None


def _scan_activities(root: Path, basis: date) -> tuple[dict, list[dict]]:
    # Only top-level files are Garmin activity summaries. Rich detail JSON is
    # deliberately retained under activities/details and inventoried separately.
    paths = sorted((root / "activities").glob("*.json"))
    valid_paths: list[Path] = []
    summaries: list[dict] = []
    raw_payloads: list[dict] = []
    present: Counter[str] = Counter()
    non_null: Counter[str] = Counter()
    invalid_files = 0
    future_files = 0
    for path in paths:
        payload = read_json(path, None)
        if not isinstance(payload, dict):
            invalid_files += 1
            continue
        act_date = activity_date(payload)
        if act_date and act_date > basis:
            future_files += 1
            continue
        valid_paths.append(path)
        raw_payloads.append(payload)
        summaries.append(summarize_activity(payload, path))
        for key, value in payload.items():
            present[str(key)] += 1
            if value is not None:
                non_null[str(key)] += 1
    total = len(raw_payloads)
    fields = {
        key: _coverage_row(non_null[key], present[key], total)
        for key in sorted(present, key=str.lower)
    }

    def alias_coverage(names: tuple[str, ...]) -> dict:
        has_count = value_count = 0
        for payload in raw_payloads:
            has_field, value = _first_top_level(payload, names)
            has_count += int(has_field)
            value_count += int(has_field and value is not None)
        return _coverage_row(value_count, has_count, total)

    average_hr = alias_coverage(("averageHR", "avgHR", "averageHeartRate"))
    maximum_hr = alias_coverage(("maxHR", "maxHr", "maxHeartRate"))
    zone_present = zone_non_null = complete_zones = 0
    for payload in raw_payloads:
        observed = []
        for zone in range(1, 6):
            has_field, value = _first_top_level(payload, (f"hrTimeInZone_{zone}", f"hr_zone_{zone}"))
            observed.append((has_field, value))
        zone_present += int(any(has_field for has_field, _ in observed))
        zone_non_null += int(any(value is not None for _, value in observed))
        complete_zones += int(all(has_field and value is not None for has_field, value in observed))
    date_values = [parse_date(item.get("date")) for item in summaries]
    date_values = [value for value in date_values if value is not None]
    categories = Counter(item.get("category") or "other" for item in summaries)
    training_load = alias_coverage(("activityTrainingLoad", "trainingLoad", "training_load"))
    average_power = alias_coverage(("avgPower", "averagePower", "avgWatts"))
    hr_coverage = {
        "average_hr": average_hr,
        "maximum_hr": maximum_hr,
        "any_hr_zone_fields": _coverage_row(zone_non_null, zone_present, total),
        "complete_five_hr_zones": {
            "count": complete_zones,
            "pct": round(100 * complete_zones / total, 1) if total else None,
        },
        "source_confidence_note": (
            "Raw HR availability does not identify wrist versus external sensor; join the device index."
        ),
    }
    summary = {
        "source_pattern": "activities/*.json",
        "raw_file_count": len(paths),
        "valid_activity_count": total,
        "invalid_file_count": invalid_files,
        "future_dated_file_count": future_files,
        "date_span": {
            "first": min(date_values).isoformat() if date_values else None,
            "latest": max(date_values).isoformat() if date_values else None,
        },
        "categories": dict(categories.most_common()),
        "top_level_field_count": len(fields),
        "top_level_field_coverage": fields,
        "heart_rate_coverage": hr_coverage,
        "training_load_coverage": training_load,
        "power_coverage": {"average_power": average_power},
        "latest_source_modified_at": _latest_mtime(valid_paths),
    }
    return summary, summaries


def _read_index(path: Path) -> tuple[dict, list[dict]]:
    payload = read_json(path, {})
    if isinstance(payload, list):
        return {}, [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return {}, []
    return payload, [row for row in payload.get("activities") or [] if isinstance(row, dict)]


def _metadata_coverage(
    root: Path,
    basis: date,
    activities: list[dict],
) -> tuple[dict, dict[str, dict]]:
    configs = {
        "gear": {
            "path": root / "snapshots" / "activity_gear_index.json",
            "ok_key": "gear_fetch_ok",
            "lookback_days": 90,
            "eligible_categories": {"mtb", "bike_indoor", "bike_outdoor"},
            "counts_for_training_load_only": False,
            "nonempty": lambda row: bool(row.get("gear")),
        },
        "devices": {
            "path": root / "snapshots" / "activity_device_index.json",
            "ok_key": "device_fetch_ok",
            "lookback_days": 90,
            "eligible_categories": {"mtb", "bike_indoor", "bike_outdoor"},
            "counts_for_training_load_only": False,
            "nonempty": lambda row: bool(
                row.get("sensors") or row.get("recording_device") or row.get("apps")
            ),
        },
        "self_evaluation": {
            "path": root / "snapshots" / "activity_self_evaluation_index.json",
            "ok_key": "detail_fetch_ok",
            "lookback_days": 30,
            "eligible_categories": None,
            "counts_for_training_load_only": True,
            "nonempty": lambda row: row.get("has_self_evaluation") is True,
        },
    }
    coverage: dict[str, dict] = {}
    endpoint_stats: dict[str, dict] = {}
    for name, config in configs.items():
        path = config["path"]
        artifact, rows = _read_index(path)
        start = basis - timedelta(days=config["lookback_days"] - 1)
        eligible_ids = {
            str(item.get("id"))
            for item in activities
            if item.get("id")
            and parse_date(item.get("date"))
            and start <= parse_date(item.get("date")) <= basis
            and (
                config["eligible_categories"] is None
                or item.get("category") in config["eligible_categories"]
            )
            and (
                not config["counts_for_training_load_only"]
                or item.get("counts_for_training_load") is True
            )
        }
        scoped_rows = []
        for row in rows:
            row_date = parse_date(row.get("date"))
            row_id = str(row.get("activity_id") or row.get("id") or "")
            if row_date and start <= row_date <= basis and row_id in eligible_ids:
                scoped_rows.append(row)
        indexed_ids = {
            str(row.get("activity_id") or row.get("id"))
            for row in scoped_rows
            if row.get("activity_id") or row.get("id")
        }
        successful = sum(1 for row in scoped_rows if row.get(config["ok_key"]) is True)
        latest_attempts = [
            (
                row.get("latest_attempt")
                if isinstance(row.get("latest_attempt"), dict)
                else row.get("fetch")
                if isinstance(row.get("fetch"), dict)
                else None
            )
            for row in scoped_rows
        ]
        latest_status_counts = Counter(
            str(attempt.get("status") or "unknown")
            for attempt in latest_attempts
            if isinstance(attempt, dict)
        )
        failed = sum(
            1
            for row, attempt in zip(scoped_rows, latest_attempts)
            if (
                isinstance(attempt, dict)
                and attempt.get("status") in {"failed", "unsupported"}
            )
            or (attempt is None and row.get(config["ok_key"]) is False)
        )
        nonempty = sum(
            1
            for row in scoped_rows
            if row.get(config["ok_key"]) is True and config["nonempty"](row)
        )
        if not eligible_ids:
            status = "not_applicable"
        elif indexed_ids == eligible_ids and successful == len(eligible_ids):
            status = "complete"
        elif indexed_ids:
            status = "partial"
        else:
            status = "missing"
        coverage[name] = {
            "source_path": _relative(path, root),
            "source_exists": path.exists(),
            "source_generated_at": artifact.get("generated_at"),
            "source_modified_at": _mtime(path),
            "lookback_days": config["lookback_days"],
            "status": status,
            "eligible_activities": len(eligible_ids),
            "indexed_activities": len(indexed_ids),
            "successful_fetches": successful,
            "failed_fetches": failed,
            "latest_attempt_status_counts": dict(sorted(latest_status_counts.items())),
            "last_sync_summary": artifact.get("last_sync_summary"),
            "missing_activities": max(0, len(eligible_ids - indexed_ids)),
            "nonempty_records": nonempty,
            "coverage_pct": (
                round(100 * len(indexed_ids) / len(eligible_ids), 1)
                if eligible_ids
                else None
            ),
        }
        observations = []
        for row, attempt in zip(scoped_rows, latest_attempts):
            if isinstance(attempt, dict):
                observation = {
                    "status": attempt.get("status"),
                    "attempted_at": attempt.get("fetched_at") or attempt.get("attempted_at"),
                    "error": attempt.get("error"),
                }
            else:
                observation = {
                    "ok": row.get(config["ok_key"]),
                    "data": {"present": True} if config["nonempty"](row) else {},
                    "error": row.get(
                        {
                            "gear": "gear_fetch_error",
                            "devices": "device_fetch_error",
                            "self_evaluation": "detail_fetch_error",
                        }[name]
                    ),
                }
            observations.append(
                (
                    parse_date(row.get("date")),
                    observation,
                )
            )
        endpoint_stats[name] = _observation_stats(observations)
    return coverage, endpoint_stats


def _scan_training_status(root: Path, basis: date) -> tuple[dict, dict[str, dict]]:
    paths = []
    dates: list[date] = []
    usable_dates: list[date] = []
    observations: dict[str, list[tuple[date | None, dict]]] = defaultdict(list)
    for path in sorted((root / "snapshots").glob("garmin_training_status_*.json")):
        if path.name == "garmin_training_status_current.json":
            continue
        snap_date = parse_date(path.stem.removeprefix("garmin_training_status_")[:10])
        if not snap_date or snap_date > basis:
            continue
        paths.append(path)
        dates.append(snap_date)
        payload = read_json(path, {})
        result = payload.get("payload") if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            continue
        label = str(result.get("label") or "get_training_status")
        observations[label].append((snap_date, result))
        if result.get("ok") is True and _has_content(result.get("data")):
            usable_dates.append(snap_date)
    endpoint_stats = {
        endpoint: _observation_stats(observations.get(endpoint, []))
        for endpoint in ("get_training_status", "get_training_readiness")
    }
    eras = contiguous_date_eras(usable_dates)
    current_path = root / "snapshots" / "garmin_training_status_current.json"
    current = read_json(current_path, {})

    def has_nested(*keys: str) -> bool:
        value: Any = current
        for key in keys:
            if not isinstance(value, dict):
                return False
            value = value.get(key)
        return value is not None

    summary = {
        "source_pattern": "snapshots/garmin_training_status_*.json",
        "snapshot_count": len(paths),
        "usable_snapshot_count": len(set(usable_dates)),
        "date_span": {
            "first": min(dates).isoformat() if dates else None,
            "latest": max(dates).isoformat() if dates else None,
        },
        "usable_eras": eras,
        "gaps": era_gaps(eras),
        "latest_source_modified_at": _latest_mtime(paths),
        "endpoints": endpoint_stats,
        "normalized_current": {
            "source_path": _relative(current_path, root),
            "exists": current_path.exists(),
            "source_modified_at": _mtime(current_path),
            "date": current.get("date") if isinstance(current, dict) else None,
            "source_payload_ok": current.get("source_payload_ok") if isinstance(current, dict) else None,
            "available_signals": {
                "training_status_feedback": has_nested("training_status_feedback"),
                "acwr_ratio": has_nested("acute_chronic", "ratio"),
                "load_focus": has_nested("load_focus"),
                "cycling_vo2max": has_nested("vo2max", "cycling_value"),
                "heat_acclimation": has_nested("acclimation", "heat_pct"),
            },
        },
    }
    return summary, endpoint_stats


def _scan_training_readiness(root: Path, basis: date) -> tuple[dict, dict[str, dict]]:
    paths: list[Path] = []
    dates: list[date] = []
    observations: dict[str, list[tuple[date | None, dict]]] = defaultdict(list)
    fetched_at_values: list[str] = []
    for path in sorted((root / "snapshots").glob("garmin_training_readiness_*.json")):
        if path.name == "garmin_training_readiness_current.json":
            continue
        snap_date = parse_date(path.stem.removeprefix("garmin_training_readiness_")[:10])
        if not snap_date or snap_date > basis:
            continue
        payload = read_json(path, {})
        if not isinstance(payload, dict):
            continue
        paths.append(path)
        dates.append(snap_date)
        if isinstance(payload.get("fetched_at"), str):
            fetched_at_values.append(payload["fetched_at"])
        for result in payload.get("payloads") or []:
            if not isinstance(result, dict):
                continue
            label = str(result.get("label") or "unknown")
            observations[label].append((snap_date, result))
    endpoint_stats = {
        method: _observation_stats(observations.get(method, []))
        for method in ("get_training_readiness", "get_morning_training_readiness")
    }
    current_path = root / "snapshots" / "garmin_training_readiness_current.json"
    current = read_json(current_path, {})
    eras = contiguous_date_eras(dates)
    return {
        "source_pattern": "snapshots/garmin_training_readiness_*.json",
        "snapshot_count": len(paths),
        "date_span": {
            "first": min(dates).isoformat() if dates else None,
            "latest": max(dates).isoformat() if dates else None,
        },
        "snapshot_eras": eras,
        "gaps": era_gaps(eras),
        "latest_fetched_at": max(fetched_at_values) if fetched_at_values else None,
        "latest_source_modified_at": _latest_mtime(paths),
        "endpoints": endpoint_stats,
        "normalized_current": {
            "source_path": _relative(current_path, root),
            "exists": current_path.exists(),
            "source_modified_at": _mtime(current_path),
            "date": current.get("date") if isinstance(current, dict) else None,
            "target_date": current.get("target_date") if isinstance(current, dict) else None,
            "source_snapshot_date": current.get("source_snapshot_date") if isinstance(current, dict) else None,
            "freshness": current.get("freshness") if isinstance(current, dict) else None,
            "status": current.get("status") if isinstance(current, dict) else None,
            "decision_use": current.get("decision_use") if isinstance(current, dict) else None,
            "source_endpoint": current.get("source_endpoint") if isinstance(current, dict) else None,
        },
    }, endpoint_stats


def _scan_cycling_ftp(root: Path, basis: date) -> tuple[dict, dict]:
    path = root / "snapshots" / "garmin_cycling_ftp_current.json"
    payload = read_json(path, {})
    effective = parse_date(payload.get("effective_date")) if isinstance(payload, dict) else None
    future_excluded = effective is not None and effective > basis
    latest_attempt = payload.get("latest_attempt") if isinstance(payload, dict) else None
    observations: list[tuple[date | None, dict]] = []
    if isinstance(latest_attempt, dict) and not future_excluded:
        observations.append((effective, latest_attempt))
    endpoint_stats = _observation_stats(observations)
    available = bool(
        isinstance(payload, dict)
        and payload.get("ftp_w") is not None
        and not future_excluded
    )
    summary = {
        "source_path": _relative(path, root),
        "exists": path.exists(),
        "source_modified_at": _mtime(path),
        "status": (
            "future_excluded"
            if future_excluded
            else payload.get("status")
            if isinstance(payload, dict)
            else "missing"
        ),
        "ftp_w": payload.get("ftp_w") if available else None,
        "effective_date": effective.isoformat() if effective else None,
        "effective_at": payload.get("effective_at") if isinstance(payload, dict) else None,
        "sport": payload.get("sport") if isinstance(payload, dict) else None,
        "biometric_source_type": (
            payload.get("biometric_source_type") if isinstance(payload, dict) else None
        ),
        "detection_source": payload.get("detection_source") if isinstance(payload, dict) else None,
        "last_success_at": payload.get("last_success_at") if isinstance(payload, dict) else None,
        "latest_attempt": latest_attempt,
        "decision_use": (
            payload.get("decision_use") if isinstance(payload, dict) else None
        ),
        "future_excluded": future_excluded,
    }
    return summary, endpoint_stats


def _scan_device_capabilities(root: Path, basis: date) -> tuple[dict, dict[str, dict]]:
    paths: list[Path] = []
    dates: list[date] = []
    fetched_at_values: list[str] = []
    observations: dict[str, list[tuple[date | None, dict]]] = defaultdict(list)
    latest_capability: bool | None = None
    latest_device_count: int | None = None
    latest_unit_system: Any = None
    for path in sorted((root / "snapshots").glob("garmin_device_capabilities_*.json")):
        snap_date = parse_date(path.stem.removeprefix("garmin_device_capabilities_")[:10])
        if not snap_date or snap_date > basis:
            continue
        payload = read_json(path, {})
        if not isinstance(payload, dict):
            continue
        paths.append(path)
        dates.append(snap_date)
        if isinstance(payload.get("fetched_at"), str):
            fetched_at_values.append(payload["fetched_at"])
        if isinstance(payload.get("training_readiness_capable"), bool):
            latest_capability = payload["training_readiness_capable"]
        calls = payload.get("calls") if isinstance(payload.get("calls"), dict) else {}
        for logical_name, method in (("devices", "get_devices"), ("unit_system", "get_unit_system")):
            result = calls.get(logical_name)
            if not isinstance(result, dict):
                continue
            observations[method].append((snap_date, result))
            if method == "get_devices" and result.get("ok") and isinstance(result.get("data"), list):
                latest_device_count = len(result["data"])
            if method == "get_unit_system" and result.get("ok"):
                raw_units = result.get("data")
                if isinstance(raw_units, (str, int, float, bool)) or raw_units is None:
                    latest_unit_system = raw_units
                elif isinstance(raw_units, dict):
                    latest_unit_system = {
                        key: value
                        for key, value in raw_units.items()
                        if isinstance(value, (str, int, float, bool))
                        and key.lower()
                        in {
                            "unitsystem",
                            "measurementsystem",
                            "distance",
                            "temperature",
                            "weight",
                            "height",
                        }
                    }
                else:
                    latest_unit_system = {"response_type": type(raw_units).__name__}
    endpoint_stats = {
        method: _observation_stats(observations.get(method, []))
        for method in ("get_devices", "get_unit_system")
    }
    eras = contiguous_date_eras(dates)
    return {
        "source_pattern": "snapshots/garmin_device_capabilities_*.json",
        "snapshot_count": len(paths),
        "date_span": {
            "first": min(dates).isoformat() if dates else None,
            "latest": max(dates).isoformat() if dates else None,
        },
        "snapshot_eras": eras,
        "gaps": era_gaps(eras),
        "latest_fetched_at": max(fetched_at_values) if fetched_at_values else None,
        "latest_source_modified_at": _latest_mtime(paths),
        "training_readiness_capable": latest_capability,
        "registered_device_count": latest_device_count,
        "unit_system": latest_unit_system,
        "privacy": "Device identities stay in the raw private artifact; only capability/count/unit summaries are surfaced here.",
        "endpoints": endpoint_stats,
    }, endpoint_stats


def _scan_detail_endpoints(root: Path, basis: date) -> tuple[dict, dict[str, dict]]:
    logical = {
        "splits": "get_activity_splits",
        "details": "get_activity_details",
        "hr_zones": "get_activity_hr_in_timezones",
        "power_zones": "get_activity_power_in_timezones",
        "weather": "get_activity_weather",
        "exercise_sets": "get_activity_exercise_sets",
        "activity": "get_activity",
        "original_download": "download_activity_original",
    }
    observations: dict[str, list[tuple[date | None, dict]]] = defaultdict(list)
    paths = []
    detail_candidates = [
        *((root / "activities" / "details").glob("garmin_*_detail.json")),
        *((root / "snapshots").glob("activity_detail_*.json")),
    ]
    for path in sorted(detail_candidates):
        payload = read_json(path, {})
        if not isinstance(payload, dict):
            continue
        observed_date = (
            parse_date(payload.get("fetched_at"))
            or parse_date(payload.get("generated_at"))
            or parse_date(payload.get("date"))
        )
        if observed_date and observed_date > basis:
            continue
        paths.append(path)
        calls = payload.get("calls") if isinstance(payload.get("calls"), dict) else {}
        for logical_name, method in logical.items():
            result = calls.get(logical_name)
            if isinstance(result, dict):
                observations[method].append((observed_date, result))
                latest_attempt = result.get("latest_attempt")
                if (
                    isinstance(latest_attempt, dict)
                    and result.get("status") == "success"
                    and latest_attempt.get("status") != "success"
                ):
                    observations[method].append((observed_date, latest_attempt))
    stats = {method: _observation_stats(observations.get(method, [])) for method in logical.values()}
    fit_paths = sorted((root / "activities" / "fit").glob("*"))
    return {
        "source_patterns": [
            "activities/details/garmin_*_detail.json",
            "snapshots/activity_detail_*.json (legacy)",
        ],
        "fit_source_pattern": "activities/fit/*",
        "artifact_count": len(paths),
        "retained_detail_count": sum(
            1 for path in paths if path.parent == root / "activities" / "details"
        ),
        "legacy_snapshot_detail_count": sum(
            1 for path in paths if path.parent == root / "snapshots"
        ),
        "fit_file_count": len(fit_paths),
        "latest_source_modified_at": _latest_mtime(paths),
        "latest_fit_modified_at": _latest_mtime(fit_paths),
        "endpoints": stats,
    }, stats


def _walk_keys_and_strings(value: Any, prefix: str = "") -> tuple[list[str], list[str]]:
    keys: list[str] = []
    strings: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            keys.append(path)
            nested_keys, nested_strings = _walk_keys_and_strings(child, path)
            keys.extend(nested_keys)
            strings.extend(nested_strings)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            nested_keys, nested_strings = _walk_keys_and_strings(child, f"{prefix}[{index}]")
            keys.extend(nested_keys)
            strings.extend(nested_strings)
    elif isinstance(value, str):
        strings.append(value)
    return keys, strings


def verify_activity_summary_privacy(
    root: str | Path | None = None,
    raw_activity_summary: dict | None = None,
) -> dict:
    base = repo_root(root)
    index_path = snapshots_dir(base) / "activity_summary_index.json"
    payload = read_json(index_path, None)
    rows = payload if isinstance(payload, list) else []
    keys, strings = _walk_keys_and_strings(rows)
    key_leaves = {path.split(".")[-1].split("[")[0].lower() for path in keys}
    name_fields = sorted(
        key_leaves
        & {
            "name",
            "activityname",
            "ownerdisplayname",
            "ownerfullname",
            "locationname",
        }
    )
    source_fields = sorted(
        key_leaves
        & {"source_file", "sourcefile", "source_path", "sourcepath", "filepath", "filename", "path"}
    )
    local_path_values = [
        value
        for value in strings
        if value.startswith(("/", "\\\\"))
        or (len(value) > 2 and value[1:3] in {":\\", ":/"})
        or "\\activities\\" in value.lower()
        or "/activities/" in value.lower()
    ]
    raw_paths = sorted(activities_dir(base).glob("*.json"))
    readable_raw = (
        int(raw_activity_summary.get("valid_activity_count") or 0)
        if raw_activity_summary is not None
        else sum(1 for path in raw_paths if isinstance(read_json(path, None), dict))
    )
    invalid_raw = (
        int(raw_activity_summary.get("invalid_file_count") or 0)
        if raw_activity_summary is not None
        else max(0, len(raw_paths) - readable_raw)
    )
    index_exists = index_path.exists() and isinstance(payload, list)
    return {
        "verified_at": iso_now(DEFAULT_TIMEZONE),
        "activity_summary_index_path": _relative(index_path, base),
        "activity_summary_index_exists": index_exists,
        "activity_summary_index_rows_scanned": len(rows),
        "activity_summary_index_redacts_names": index_exists and not name_fields,
        "activity_summary_index_redacts_source_paths": (
            index_exists and not source_fields and not local_path_values
        ),
        "forbidden_name_fields_found": name_fields,
        "source_path_fields_found": source_fields,
        "local_path_value_count": len(local_path_values),
        "raw_activity_files_preserved": bool(raw_paths) and invalid_raw == 0,
        "raw_activity_files_present": len(raw_paths),
        "raw_activity_files_readable": readable_raw,
        "preservation_limit": (
            "Current presence/readability is verified; historical deletion cannot be proven from a point-in-time scan."
        ),
    }


def _source_fingerprint(base: Path) -> tuple[int, int, int, str]:
    paths = list((base / "activities").glob("*.json"))
    paths.extend((base / "activities" / "details").glob("garmin_*_detail.json"))
    paths.extend((base / "activities" / "fit").glob("*"))
    paths.extend((base / "snapshots").glob("garmin_wellness_*.json"))
    paths.extend(
        path
        for path in (base / "snapshots").glob("garmin_training_status_*.json")
        if path.name != "garmin_training_status_current.json"
    )
    paths.extend(
        path
        for path in (base / "snapshots").glob("garmin_training_readiness_*.json")
        if path.name != "garmin_training_readiness_current.json"
    )
    paths.extend((base / "snapshots").glob("garmin_device_capabilities_*.json"))
    paths.extend((base / "snapshots").glob("activity_detail_*.json"))
    for filename in (
        "activity_gear_index.json",
        "activity_device_index.json",
        "activity_self_evaluation_index.json",
        "activity_summary_index.json",
    ):
        path = base / "snapshots" / filename
        if path.exists():
            paths.append(path)
    latest_mtime_ns = 0
    total_size = 0
    existing = 0
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        existing += 1
        latest_mtime_ns = max(latest_mtime_ns, stat.st_mtime_ns)
        total_size += stat.st_size
    current_paths = (
        base / "snapshots" / "garmin_training_status_current.json",
        base / "snapshots" / "garmin_training_readiness_current.json",
        base / "snapshots" / "garmin_cycling_ftp_current.json",
    )
    digest = hashlib.sha256()
    for current_path in current_paths:
        try:
            digest.update(current_path.read_bytes())
        except OSError:
            digest.update(f"missing:{current_path.name}".encode("utf-8"))
    current_digest = digest.hexdigest()[:16]
    return existing, latest_mtime_ns, total_size, current_digest


def build_garmin_surface_manifest(
    root: str | Path | None = None,
    for_date: str | date | None = None,
) -> dict:
    base = repo_root(root)
    basis = parse_date(for_date) or today_local(DEFAULT_TIMEZONE)
    cache_key = (str(base), basis.isoformat(), *_source_fingerprint(base))
    cached = _MANIFEST_CACHE.get(cache_key)
    if cached is not None:
        manifest = deepcopy(cached)
        write_json(snapshots_dir(base) / "garmin_surface_manifest.json", manifest)
        return manifest
    wellness, wellness_endpoints = _scan_wellness(base, basis)
    activities, activity_rows = _scan_activities(base, basis)
    metadata, metadata_endpoints = _metadata_coverage(base, basis, activity_rows)
    training_status, training_endpoints = _scan_training_status(base, basis)
    training_readiness, training_readiness_endpoints = _scan_training_readiness(base, basis)
    cycling_ftp, cycling_ftp_endpoint = _scan_cycling_ftp(base, basis)
    device_capabilities, capability_endpoints = _scan_device_capabilities(base, basis)
    detail, detail_endpoints = _scan_detail_endpoints(base, basis)
    specs = _endpoint_specs()

    observed: dict[str, dict] = {}
    for method, stats in wellness_endpoints.items():
        observed[f"wellness.{method}"] = stats
    observed["training.get_training_status"] = training_endpoints["get_training_status"]
    observed["training.get_training_readiness"] = (
        training_readiness_endpoints["get_training_readiness"]
        if training_readiness_endpoints["get_training_readiness"].get("observations")
        else training_endpoints["get_training_readiness"]
    )
    observed["training.get_morning_training_readiness"] = training_readiness_endpoints[
        "get_morning_training_readiness"
    ]
    observed["training.get_cycling_ftp"] = cycling_ftp_endpoint
    observed["capabilities.get_devices"] = capability_endpoints["get_devices"]
    observed["capabilities.get_unit_system"] = capability_endpoints["get_unit_system"]
    observed["metadata.get_activity_gear"] = metadata_endpoints["gear"]
    observed["metadata.get_activity_devices"] = metadata_endpoints["devices"]
    observed["metadata.get_activity_self_evaluation"] = metadata_endpoints["self_evaluation"]
    for logical_name, method in (
        ("splits", "get_activity_splits"),
        ("details", "get_activity_details"),
        ("hr_zones", "get_activity_hr_in_timezones"),
        ("power_zones", "get_activity_power_in_timezones"),
        ("weather", "get_activity_weather"),
        ("exercise_sets", "get_activity_exercise_sets"),
        ("activity", "get_activity"),
        ("original_download", "download_activity_original"),
    ):
        observed[f"detail.{logical_name}"] = detail_endpoints[method]

    activity_endpoint = {
        "attempts": None,
        "successes": None,
        "nonempty_successes": activities["valid_activity_count"],
        "empty_successes": None,
        "failures": None,
        "unsupported": 0,
        "error_counts": [],
        "state": "success" if activities["valid_activity_count"] else "not_attempted",
        "inference": (
            "State is inferred from persisted raw activity summaries; per-call attempt history is not retained."
        ),
    }
    observed["activities.get_activities"] = activity_endpoint
    observed["activities.count_activities"] = {
        "attempts": 0,
        "successes": 0,
        "nonempty_successes": 0,
        "empty_successes": 0,
        "failures": 0,
        "unsupported": 0,
        "error_counts": [],
        "state": "not_attempted",
        "inference": "The historical backfill status does not persist count_activities call results.",
    }

    endpoints = {}
    for endpoint_id, spec in specs.items():
        stats = observed.get(endpoint_id, {"state": "not_attempted"})
        state = stats.get("state")
        if state not in ENDPOINT_STATES:
            state = "not_attempted"
        endpoints[endpoint_id] = {**spec, **stats, "state": state}

    state_counts = Counter(item["state"] for item in endpoints.values())
    manifest = {
        "schema_version": 1,
        "generated_at": iso_now(DEFAULT_TIMEZONE),
        "basis_date": basis.isoformat(),
        "scope": (
            "Persisted Garmin evidence and configured collection surfaces; this is not a claim that every Garmin API field exists locally."
        ),
        "endpoint_state_definitions": {
            "success": "At least one persisted call succeeded with a non-empty response.",
            "success_empty": "At least one persisted call succeeded, but all observed responses were empty.",
            "failed": "Calls were attempted and none succeeded.",
            "unsupported": "Every observed attempt explicitly reported a missing client method.",
            "not_attempted": "No persisted call evidence exists; support cannot be inferred.",
        },
        "configured_endpoints": endpoints,
        "endpoint_state_counts": dict(sorted(state_counts.items())),
        "raw_sources": {
            "wellness": wellness,
            "activities": activities,
            "training_status": training_status,
            "training_readiness": training_readiness,
            "cycling_ftp": cycling_ftp,
            "device_capabilities": device_capabilities,
            "activity_detail": detail,
            "activity_metadata": metadata,
        },
        "normalized_surfaces": {
            "wellness_daily": {
                "source_path": "snapshots/wellness_daily.json",
                "fields": WELLNESS_NORMALIZED_FIELDS,
            },
            "activity_summary": {
                "source_path": "snapshots/activity_summary_index.json",
                "fields": ACTIVITY_NORMALIZED_FIELDS,
                "privacy": "Names, raw identifiers, and source paths are intentionally omitted.",
            },
            "training_status_current": {
                "source_path": "snapshots/garmin_training_status_current.json",
                "fields": TRAINING_STATUS_NORMALIZED_FIELDS,
            },
            "training_readiness_current": {
                "source_path": "snapshots/garmin_training_readiness_current.json",
                "fields": TRAINING_READINESS_NORMALIZED_FIELDS,
                "decision_use": "context_only_custom_readiness_remains_authoritative",
            },
            "cycling_ftp_current": {
                "source_path": "snapshots/garmin_cycling_ftp_current.json",
                "fields": [
                    "status",
                    "ftp_w",
                    "effective_date",
                    "effective_at",
                    "sport",
                    "detection_source",
                    "biometric_source_type",
                    "latest_attempt",
                    "last_known_good",
                ],
                "decision_use": "current_garmin_operational_ftp_with_rpe_hr_validation",
                "downstream_consumers": [
                    "current_state",
                    "coach_packet",
                    "training_architecture",
                    "ftp_relative_prescription",
                ],
            },
            "rest_recharge_window": {
                "source_path": "snapshots/rest_recharge_window.json",
                "fields": REST_RECHARGE_NORMALIZED_FIELDS,
                "decision_use": "intraday_recovery_context_only_never_session_clearance",
                "downstream_consumers": [
                    "current_state",
                    "coach_packet",
                    "cns_readiness_negative_only",
                ],
                "privacy": (
                    "Derived bounded evidence; no raw profile identifier, activity name, or local source path is copied."
                ),
            },
            "wearable_coverage": {
                "source_path": "snapshots/wearable_coverage.json",
                "fields": WEARABLE_COVERAGE_NORMALIZED_FIELDS,
                "decision_use": "coverage_interpretation_only_never_readiness_clearance",
                "downstream_consumers": [
                    "current_state",
                    "coach_packet",
                    "cns_readiness_coverage_guard",
                    "training_predictor_coverage_guard",
                    "body_battery_model_coverage_guard",
                ],
                "privacy": (
                    "Derived bounded coverage evidence; only reason categories and validated timing are surfaced, never raw notes, locations, profile identifiers, or sample values."
                ),
            },
        },
        "source_timestamps": {
            "wellness_latest_source_modified_at": wellness.get("latest_source_modified_at"),
            "wellness_latest_fetched_at": wellness.get("latest_fetched_at"),
            "wellness_latest_data_cutoff_local": wellness.get("latest_data_cutoff_local"),
            "activities_latest_source_modified_at": activities.get("latest_source_modified_at"),
            "training_status_latest_source_modified_at": training_status.get(
                "latest_source_modified_at"
            ),
            "training_readiness_latest_fetched_at": training_readiness.get("latest_fetched_at"),
            "training_readiness_latest_source_modified_at": training_readiness.get(
                "latest_source_modified_at"
            ),
            "cycling_ftp_latest_source_modified_at": cycling_ftp.get(
                "source_modified_at"
            ),
            "cycling_ftp_last_success_at": cycling_ftp.get("last_success_at"),
            "device_capabilities_latest_fetched_at": device_capabilities.get("latest_fetched_at"),
            "device_capabilities_latest_source_modified_at": device_capabilities.get(
                "latest_source_modified_at"
            ),
            "activity_detail_latest_source_modified_at": detail.get("latest_source_modified_at"),
            "activity_fit_latest_source_modified_at": detail.get("latest_fit_modified_at"),
            "gear_index_generated_at": metadata.get("gear", {}).get("source_generated_at"),
            "device_index_generated_at": metadata.get("devices", {}).get("source_generated_at"),
            "self_evaluation_index_generated_at": metadata.get("self_evaluation", {}).get(
                "source_generated_at"
            ),
        },
        "units_and_provenance": {
            "raw_activity_duration": "seconds (Garmin activity summary)",
            "normalized_activity_duration_min": "minutes, derived from raw seconds",
            "raw_activity_distance": "meters (Garmin activity summary)",
            "normalized_activity_distance_km": "kilometers, derived from raw meters",
            "heart_rate": "beats per minute; sensor source requires activity_device_index join",
            "power": "watts; Garmin/device-derived depending on activity source",
            "cycling_ftp": "watts; Garmin biometric-service operational estimate, dated and validated against RPE/HR for prescription",
            "training_load": "Garmin activityTrainingLoad; modeled load, not a physical unit",
            "training_effect": "Garmin aerobic/anaerobic Training Effect scale",
            "body_battery": "Garmin-modeled 0-100 score",
            "hrv": "milliseconds from Garmin overnight HRV summaries",
            "spo2": (
                "percent from garminconnect.get_spo2_data; daily/sleep summaries and hourly "
                "aggregates remain distinct, are not medical measurements, and never establish "
                "in-activity oxygen saturation or promote readiness"
            ),
            "respiration": (
                "breaths per minute from garminconnect.get_respiration_data; two-minute and "
                "hourly series are distinct, negative sentinels are unavailable, and -2 activity "
                "sentinels never become inferred exercise respiration"
            ),
            "monitoring_altitude": (
                "meters from daily_summary.averageMonitoringEnvironmentAltitude; contextual "
                "wearable monitoring environment, not an activity elevation trace"
            ),
            "training_status_altitude_acclimation": (
                "Garmin-native altitudeAcclimation value with undeclared unit; only the separate "
                "acclimationPercentage fields are percentages. Current/previous altitude retain "
                "Garmin-native altitude units until independently verified."
            ),
            "temperature": (
                "Do not merge device temperature and activity weather until each source declares units; weather payloads may be imperial despite metric profile settings."
            ),
            "water_estimated": (
                "Garmin estimate; preserve as source context and do not substitute it for recorded fluid intake or measured sweat loss."
            ),
            "decision_boundary": (
                "Garmin-derived values are co-diagnostic. Technical execution, CNS state, heat, fueling, and subjective review remain separate evidence."
            ),
        },
        "privacy_verification": verify_activity_summary_privacy(base, activities),
    }
    write_json(snapshots_dir(base) / "garmin_surface_manifest.json", manifest)
    _MANIFEST_CACHE.clear()
    _MANIFEST_CACHE[cache_key] = deepcopy(manifest)
    return manifest
