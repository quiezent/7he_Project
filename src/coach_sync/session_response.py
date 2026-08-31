from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from .evidence import as_number
from .io import read_json
from .paths import input_dir, repo_root
from .time_utils import parse_date, today_local


SESSION_RESPONSE_VERSION = "session_response_v2"
CANONICAL_STOP_RULE_OUTCOMES = frozenset(
    {
        "not_triggered",
        "triggered_and_stopped",
        "triggered_and_downshifted",
        "triggered_but_continued",
    }
)
_UNKNOWN_STOP_RULE_OUTCOMES = frozenset(
    {"unknown", "not_reported", "unreported", "not_observed", "na", "n_a"}
)
_UNKNOWN_OBSERVATION_TEXT = frozenset(
    {
        "unknown",
        "not reported",
        "not yet reported",
        "not yet reported today",
        "unreported",
        "not collected",
        "questionnaire not collected",
        "no questionnaire collected",
        "no questionnaire was collected",
    }
)
_NOT_APPLICABLE_OBSERVATION_TEXT = frozenset(
    {
        "n a",
        "na",
        "not applicable",
        "not applicable indoor cycling",
        "not applicable indoor ride",
        "not applicable non mtb session",
    }
)


def _safe_parse_date(value: Any) -> date | None:
    try:
        return parse_date(value)
    except (TypeError, ValueError):
        return None


def _garmin_field_source(payload: dict, field: str) -> str | None:
    if payload.get(field) in (None, ""):
        return None
    return f"latest_session_evidence.self_evaluation.{field}"


def _garmin_category_derivation_source(
    payload: dict,
    *,
    normalized_field: str,
    raw_field: str,
    derivation: str,
) -> dict | None:
    normalized_path = _garmin_field_source(payload, normalized_field)
    raw_path = _garmin_field_source(payload, raw_field)
    if normalized_path is None and raw_path is None:
        return None
    return {
        "path": normalized_path
        or f"latest_session_response.subjective_evaluation.{normalized_field}",
        "derived_from": raw_path,
        "derivation": derivation if raw_path is not None else "upstream_normalized_category",
    }


def _garmin_subjective_evaluation(
    value: dict | None,
    *,
    target: date,
    requested_activity_id: str | None,
) -> dict:
    payload = value if isinstance(value, dict) else {}
    upstream_status = str(payload.get("status") or "not_supplied")
    upstream_available = upstream_status.startswith("available")
    observed_activity_id = (
        str(payload.get("activity_id")) if payload.get("activity_id") not in (None, "") else None
    )
    observed_date = _safe_parse_date(payload.get("date"))
    validation_reasons: list[str] = []
    if not payload:
        validation_reasons.append("self_evaluation_not_supplied")
    elif not upstream_available:
        validation_reasons.append("upstream_self_evaluation_not_available")
    if requested_activity_id is None:
        validation_reasons.append("requested_activity_id_missing")
    elif observed_activity_id != requested_activity_id:
        validation_reasons.append(
            "self_evaluation_activity_id_missing"
            if observed_activity_id is None
            else "self_evaluation_activity_id_mismatch"
        )
    if observed_date is None:
        validation_reasons.append("self_evaluation_date_missing_or_malformed")
    elif observed_date != target:
        validation_reasons.append("self_evaluation_date_mismatch")
    identity_matched = (
        upstream_available
        and requested_activity_id is not None
        and observed_activity_id == requested_activity_id
        and observed_date == target
    )

    feel_score = as_number(payload.get("feel_score"))
    supplied_feel_out_of_5 = as_number(payload.get("feel_out_of_5"))
    feel_out_of_5 = None
    feel_source_field = None
    if feel_score in {0, 25, 50, 75, 100}:
        feel_out_of_5 = int(feel_score / 25) + 1
        feel_source_field = "feel_score"
    elif feel_score is None and supplied_feel_out_of_5 in {1, 2, 3, 4, 5}:
        feel_out_of_5 = supplied_feel_out_of_5
        feel_source_field = "feel_out_of_5"
    if feel_score is not None and feel_out_of_5 is None:
        validation_reasons.append("garmin_feel_category_invalid")

    raw_rpe = as_number(payload.get("rpe_score"))
    global_rpe = None
    rpe_source_field = None
    if raw_rpe in {10, 20, 30, 40, 50, 60, 70, 80, 90, 100}:
        global_rpe = raw_rpe / 10.0
        rpe_source_field = "rpe_score"
    elif raw_rpe is None:
        for field in ("global_rpe_out_of_10", "rpe_out_of_10"):
            candidate = as_number(payload.get(field))
            if candidate in {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}:
                global_rpe = float(candidate)
                rpe_source_field = field
                break
    if raw_rpe is not None and global_rpe is None:
        validation_reasons.append("garmin_rpe_category_invalid")

    score_available = feel_out_of_5 is not None or global_rpe is not None
    if identity_matched and score_available:
        status = "available"
    elif not payload or not upstream_available:
        status = "not_available"
    else:
        status = "unusable"
    return {
        "status": status,
        "upstream_status": upstream_status,
        "activity_id": observed_activity_id,
        "date": observed_date.isoformat() if observed_date is not None else None,
        "validation": {
            "status": "matched" if identity_matched else "unusable",
            "usable": identity_matched,
            "reasons": validation_reasons,
            "expected": {
                "activity_id": requested_activity_id,
                "date": target.isoformat(),
            },
            "observed": {
                "activity_id": observed_activity_id,
                "date": observed_date.isoformat() if observed_date is not None else None,
                "raw_date": payload.get("date"),
            },
        },
        "garmin_feel": {
            "raw_score_0_to_100": feel_score,
            "out_of_5": int(feel_out_of_5) if feel_out_of_5 is not None else None,
            "ordinal_display_out_of_10": (
                int(feel_out_of_5) * 2 if feel_out_of_5 is not None else None
            ),
            "display_remap": "ordinal_1_to_5_mapped_to_even_labels_2_to_10_not_interval_equivalence",
            "construct": "athlete_state_composite",
            "components": ["clarity", "strength", "coordination"],
        },
        "garmin_perceived_effort": {
            "raw_score_10_to_100": raw_rpe,
            "global_rpe_0_to_10": global_rpe,
            "construct": "delivered_session_effort",
        },
        "field_sources": {
            "garmin_feel_raw_score": _garmin_field_source(payload, "feel_score"),
            "garmin_feel_out_of_5": (
                _garmin_category_derivation_source(
                    payload,
                    normalized_field="feel_out_of_5",
                    raw_field="feel_score",
                    derivation="strict_map_0_25_50_75_100_to_1_2_3_4_5",
                )
                if feel_source_field is not None
                else None
            ),
            "global_rpe_0_to_10": (
                _garmin_category_derivation_source(
                    payload,
                    normalized_field="global_rpe_out_of_10",
                    raw_field="rpe_score",
                    derivation="strict_map_10_20_through_100_to_1_2_through_10",
                )
                if rpe_source_field is not None
                else None
            ),
        },
        "ontology_guardrail": (
            "Garmin Feel is Clayton's indivisible clarity-strength-coordination composite, not three "
            "fabricated component scores. Illness, technical execution, and safety outcome remain "
            "separate evidence."
        ),
        "source": payload.get("source"),
        "latest_attempt": payload.get("latest_attempt"),
    }


def _routine_review(garmin: dict) -> dict:
    feel_available = (garmin.get("garmin_feel") or {}).get("out_of_5") is not None
    rpe_available = (
        (garmin.get("garmin_perceived_effort") or {}).get("global_rpe_0_to_10")
        is not None
    )
    identity_matched = (garmin.get("validation") or {}).get("usable") is True
    complete = identity_matched and feel_available and rpe_available
    if complete:
        status = "complete"
    elif identity_matched and (feel_available or rpe_available):
        status = "partial"
    elif garmin.get("status") == "unusable":
        status = "unusable"
    else:
        status = "not_available"
    return {
        "status": status,
        "basis": "activity_and_date_matched_garmin_feel_plus_global_rpe",
        "activity_and_date_matched": identity_matched,
        "feel_available": feel_available if identity_matched else False,
        "global_rpe_available": rpe_available if identity_matched else False,
        "duplicate_general_questionnaire": (
            "suppressed" if complete else "not_suppressed_by_complete_garmin_review"
        ),
        "duplicate_general_questionnaire_required": False if complete else None,
        "targeted_follow_up_only": complete,
        "guardrail": (
            "A complete matched Garmin Feel and Perceived Effort review replaces a duplicate general "
            "questionnaire. It does not establish illness absence, technical execution, or a safety-contract "
            "outcome; ask only a targeted question when one of those facts is decision-critical."
        ),
    }


def _subjective_policy_evaluation(
    policy: dict | None,
    *,
    target: date,
    requested_activity_id: str | None,
    garmin: dict,
) -> dict:
    canonical = policy if isinstance(policy, dict) else {}
    reference = canonical.get("reference_action") if isinstance(canonical.get("reference_action"), dict) else {}
    above = (
        canonical.get("above_reference_promotion")
        if isinstance(canonical.get("above_reference_promotion"), dict)
        else {}
    )
    reference_date = _safe_parse_date(reference.get("date"))
    reference_activity_id = (
        str(reference.get("activity_id"))
        if reference.get("activity_id") not in (None, "")
        else None
    )
    reference_minimum = as_number(reference.get("minimum_retrospective_feel_1_to_5"))
    above_minimum = as_number(above.get("minimum_retrospective_feel_1_to_5"))
    policy_valid = (
        reference_date is not None
        and reference_activity_id is not None
        and reference_minimum in {1, 2, 3, 4, 5}
        and above_minimum in {1, 2, 3, 4, 5}
    )
    exact_match = bool(
        policy_valid
        and requested_activity_id is not None
        and target == reference_date
        and requested_activity_id == reference_activity_id
    )
    feel = (
        (garmin.get("garmin_feel") or {}).get("out_of_5")
        if garmin.get("status") == "available"
        else None
    )

    def threshold_result(minimum: float | None) -> tuple[str, bool | None]:
        if not policy_valid or not exact_match:
            return "not_applicable", None
        if feel is None or minimum is None:
            return "unavailable", None
        met = float(feel) >= float(minimum)
        return ("met" if met else "not_met"), met

    reference_status, reference_met = threshold_result(reference_minimum)
    above_status, above_met = threshold_result(above_minimum)
    return {
        "status": "available" if policy_valid else "canonical_policy_missing_or_invalid",
        "reference_action": {
            "date": reference_date.isoformat() if reference_date is not None else reference.get("date"),
            "activity_id": reference_activity_id,
            "bike_key": reference.get("bike_key"),
            "action": reference.get("action"),
            "exact_identifier_match": exact_match,
            "match_basis": ["date", "activity_id"],
            "action_equivalence_inferred": False,
            "minimum_retrospective_feel_1_to_5": (
                int(reference_minimum) if reference_minimum is not None else None
            ),
            "threshold_status": reference_status,
            "threshold_met": reference_met,
            "guardrail": (
                "The 3/5 threshold applies only when the canonical reference date and activity ID match. "
                "No other session is inferred to be equivalent, easier, or harder."
            ),
        },
        "above_reference_prerequisite": {
            "minimum_retrospective_feel_1_to_5": (
                int(above_minimum) if above_minimum is not None else None
            ),
            "threshold_status": above_status,
            "threshold_met": above_met,
            "evaluated_from_exact_reference_only": exact_match,
            "action_classification_for_other_sessions": "not_inferred",
            "increases_requiring_prerequisite": above.get("increases_requiring_4_of_5") or [],
            "necessary_not_sufficient": True,
            "same_day_clearance_granted": False,
            "guardrail": (
                "Meeting 4/5 is only a retrospective prerequisite for considering more than the exact "
                "reference action. It is never sufficient clearance; readiness, CNS, symptoms, environment, "
                "density, and consequence still govern."
            ),
        },
    }


def _attach_response_axes(
    response: dict,
    *,
    garmin: dict,
    policy_evaluation: dict,
    stop_audit: dict | None = None,
    illness_airway: dict | None = None,
    technical_execution: dict | None = None,
) -> dict:
    merged = dict(response)
    routine = _routine_review(garmin)
    merged["routine_review"] = routine
    merged["subjective_evaluation"] = garmin
    merged["subjective_tolerance_policy"] = policy_evaluation
    outcome = merged.get("stop_rule_outcome")
    merged["safety_contract_outcome"] = {
        "status": "observed" if outcome is not None else "unknown",
        "outcome": outcome,
        "explicit_canonical_outcome": outcome is not None,
        "audit": stop_audit,
        "nominal_stop_evidence_complete": outcome is not None,
    }
    merged["illness_airway"] = illness_airway or {
        "status": "unknown",
        "illness_status": "unknown",
        "illness_phase": None,
        "airway_symptoms": {},
        "guardrail": (
            "Illness and airway status remain separate athlete-reported evidence and are never inferred "
            "from Garmin Feel, RPE, Body Battery, or a completed activity."
        ),
    }
    merged["technical_execution"] = technical_execution or {
        "status": "unknown",
        "technical_quality_notes": None,
        "late_session_skill_fade": None,
        "field_observation_status": {
            "technical_quality_notes": "unknown",
            "late_session_skill_fade": "unknown",
        },
        "guardrail": (
            "Garmin Feel, RPE, and objective activity data do not independently establish technical execution."
        ),
    }
    decision_use = dict(merged.get("decision_use") or {})
    decision_use["routine_review_status"] = routine.get("status")
    decision_use["duplicate_general_questionnaire"] = routine.get(
        "duplicate_general_questionnaire"
    )
    decision_use["classification_scope"] = "symptom_response"
    reasons = list(decision_use.get("reasons") or [])
    if decision_use.get("classification") == "insufficient_evidence" and not reasons:
        if (merged.get("symptom") or {}).get("character") == "unknown":
            reasons.append("symptom_response_fields_unknown")
        if outcome is None:
            reasons.append("safety_contract_outcome_unknown")
    illness_caution_reasons: list[str] = []
    illness_axis = merged["illness_airway"]
    if illness_axis.get("illness_status") == "present":
        illness_caution_reasons.append("illness_present")
        illness_phase = illness_axis.get("illness_phase")
        if illness_phase in {"suspected", "active", "recovering"}:
            illness_caution_reasons.append(f"illness_{illness_phase}")
    for symptom_name, symptom_status in sorted(
        (illness_axis.get("airway_symptoms") or {}).items()
    ):
        if symptom_status == "present":
            normalized_name = _normalized_text_key(str(symptom_name)) or "unspecified"
            illness_caution_reasons.append(
                f"airway_symptom_present_{normalized_name.replace(' ', '_')}"
            )
    for reason in illness_caution_reasons:
        if reason not in reasons:
            reasons.append(reason)
    decision_use["reasons"] = reasons
    decision_use["illness_airway_caution"] = {
        "status": (
            "present"
            if illness_caution_reasons
            else "not_present_in_reported_fields"
            if illness_axis.get("status") == "observed"
            else "unknown"
        ),
        "reasons": illness_caution_reasons,
        "training_promotion_allowed": False if illness_caution_reasons else None,
        "guardrail": (
            "Reported illness or airway symptoms remain a caution even when Garmin Feel is favorable. "
            "Absence of a reported caution is not general medical clearance."
        ),
    }
    decision_use["review_summary"] = {
        "routine_review": routine.get("status"),
        "illness_airway": merged["illness_airway"].get("status"),
        "technical_execution": merged["technical_execution"].get("status"),
        "safety_contract_outcome": merged["safety_contract_outcome"].get("status"),
    }
    merged["decision_use"] = decision_use
    return merged


def _merge_garmin_only_response(
    base: dict,
    garmin: dict,
    *,
    policy_evaluation: dict,
) -> dict:
    merged = dict(base)
    merged["manual_feedback"] = {
        "status": base.get("status"),
        "reasons": list((base.get("decision_use") or {}).get("reasons") or []),
        "source": (base.get("provenance") or {}).get("source"),
    }
    garmin_available = garmin.get("status") == "available"
    rpe = (
        (garmin.get("garmin_perceived_effort") or {}).get("global_rpe_0_to_10")
        if garmin_available
        else None
    )
    if garmin_available:
        # Preserve wrong-date and wrong-activity manual feedback statuses for audit.
        if merged.get("status") == "feedback_missing":
            merged["status"] = "garmin_self_evaluation_only"
        merged["global_rpe_0_to_10"] = rpe
    provenance = dict(merged.get("provenance") or {})
    provenance["garmin_self_evaluation_source"] = garmin.get("source")
    provenance["garmin_self_evaluation_upstream_status"] = garmin.get("upstream_status")
    provenance["garmin_self_evaluation_latest_attempt"] = garmin.get("latest_attempt")
    provenance["garmin_self_evaluation_validation"] = garmin.get("validation")
    provenance["field_sources"] = {
        "global_rpe_0_to_10": (garmin.get("field_sources") or {}).get(
            "global_rpe_0_to_10"
        ) if garmin_available else None,
        "garmin_feel": (garmin.get("field_sources") or {}).get(
            "garmin_feel_out_of_5"
        ) if garmin_available else None,
        "stop_rule_outcome": None,
    }
    merged["provenance"] = provenance
    if garmin_available and base.get("status") == "feedback_missing":
        merged["decision_use"] = {
            "classification": "subjective_session_response_available_safety_unknown",
            "reasons": ["garmin_self_evaluation_available", "manual_symptom_and_stop_fields_unknown"],
            "guardrail": (
                "Garmin Feel and Perceived Effort satisfy the routine subjective session review without "
                "a duplicate questionnaire. They do not prove illness absence, technical quality, or that "
                "a stop rule was not triggered; those fields remain unknown unless separately observed."
            ),
        }
    return _attach_response_axes(
        merged,
        garmin=garmin,
        policy_evaluation=policy_evaluation,
    )


def _normalize_stop_rule_outcome(raw: Any, source: str | None) -> tuple[str | None, dict]:
    if isinstance(raw, str):
        normalized = raw.strip().lower().replace("-", "_").replace(" ", "_")
    else:
        normalized = None
    if normalized in CANONICAL_STOP_RULE_OUTCOMES:
        return normalized, {
            "validation": "canonical",
            "raw_value": raw,
            "source": source,
        }
    if normalized in _UNKNOWN_STOP_RULE_OUTCOMES or raw in (None, ""):
        validation = "not_reported_sentinel" if normalized else "missing"
    else:
        validation = "invalid"
    return None, {
        "validation": validation,
        "raw_value": raw,
        "source": source,
        "allowed_values": sorted(CANONICAL_STOP_RULE_OUTCOMES),
    }


def _relative_path(root: str | Path | None, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root(root)).as_posix()
    except (OSError, ValueError):
        return path.name


def _review(entry: dict) -> dict:
    value = entry.get("session_contract_review")
    return value if isinstance(value, dict) else {}


def _normalized_text_key(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = re.sub(r"[^a-z0-9]+", " ", value.strip().casefold())
    normalized = " ".join(normalized.split())
    return normalized or None


def _typed_presence(value: Any) -> str | None:
    normalized = _normalized_text_key(value)
    if normalized in {"absent", "none", "no", "not present"}:
        return "absent"
    if normalized in {"present", "yes"}:
        return "present"
    if normalized in _UNKNOWN_OBSERVATION_TEXT:
        return "unknown"
    return None


def _typed_illness_presence(value: Any) -> tuple[str | None, str | None]:
    normalized = _normalized_text_key(value)
    if normalized in {"suspected", "active", "recovering"}:
        return "present", normalized
    return _typed_presence(value), None


def _optional_observation_text(value: Any) -> tuple[str | None, str]:
    if value in (None, ""):
        return None, "unknown"
    text = str(value).strip()
    if not text:
        return None, "unknown"
    normalized = _normalized_text_key(text)
    if normalized in _UNKNOWN_OBSERVATION_TEXT:
        return None, "unknown"
    if (
        normalized in _NOT_APPLICABLE_OBSERVATION_TEXT
        or (normalized is not None and normalized.startswith(("not applicable ", "n a ")))
        or (
            normalized is not None
            and re.search(
                r"\b(?:mtb )?technical (?:field|fields|execution) (?:is|are) not applicable\b",
                normalized,
            )
        )
    ):
        return None, "not_applicable"
    return text, "observed"


def _illness_airway_axis(reported: dict) -> dict:
    payload = reported.get("airway_and_illness")
    if not isinstance(payload, dict):
        return {
            "status": "unknown",
            "illness_status": "unknown",
            "illness_phase": None,
            "airway_symptoms": {},
            "provenance": {
                "source": "entries[].reported_context.airway_and_illness",
                "validation": "typed_object_not_reported",
            },
            "guardrail": (
                "Free prose is not converted into illness clearance. Use typed athlete-reported illness "
                "and airway fields when the distinction is decision-critical."
            ),
        }
    raw_illness_status = payload.get("illness_status")
    illness_status, illness_phase = _typed_illness_presence(raw_illness_status)
    symptoms_payload = (
        payload.get("airway_symptoms")
        if isinstance(payload.get("airway_symptoms"), dict)
        else {}
    )
    symptoms = {
        str(name): status
        for name, raw in symptoms_payload.items()
        if (status := _typed_presence(raw)) is not None
    }
    observed = illness_status in {"absent", "present"} or any(
        status in {"absent", "present"} for status in symptoms.values()
    )
    return {
        "status": "observed" if observed else "unknown",
        "illness_status": illness_status or "unknown",
        "illness_phase": illness_phase,
        "airway_symptoms": symptoms,
        "context_note": payload.get("context_note"),
        "provenance": {
            "source": "entries[].reported_context.airway_and_illness",
            "reported_by": payload.get("reported_by") or "athlete",
            "validation": "typed_fields_only",
            "raw_illness_status": raw_illness_status,
            "field_sources": {
                "illness_status": (
                    "entries[].reported_context.airway_and_illness.illness_status"
                    if illness_status is not None
                    else None
                ),
                "airway_symptoms": {
                    name: (
                        "entries[].reported_context.airway_and_illness."
                        f"airway_symptoms.{name}"
                    )
                    for name in symptoms
                },
            },
        },
        "guardrail": (
            "This is explicit athlete-reported illness/airway evidence, separate from Garmin Feel. "
            "It does not diagnose disease or generalize beyond the reported session context."
        ),
    }


def _technical_execution_axis(review: dict) -> dict:
    nested = (
        review.get("technical_execution")
        if isinstance(review.get("technical_execution"), dict)
        else {}
    )
    notes = nested.get("technical_quality_notes")
    notes_source = "entries[].session_contract_review.technical_execution.technical_quality_notes"
    if notes in (None, ""):
        notes = review.get("technical_quality_notes")
        notes_source = "entries[].session_contract_review.technical_quality_notes"
    fade = nested.get("late_session_skill_fade")
    fade_source = "entries[].session_contract_review.technical_execution.late_session_skill_fade"
    if fade in (None, ""):
        fade = review.get("late_session_skill_fade")
        fade_source = "entries[].session_contract_review.late_session_skill_fade"
    raw_notes = notes
    raw_fade = fade
    notes, notes_status = _optional_observation_text(raw_notes)
    fade, fade_status = _optional_observation_text(raw_fade)
    field_statuses = {notes_status, fade_status}
    if "observed" in field_statuses:
        axis_status = "observed"
    elif "not_applicable" in field_statuses:
        axis_status = "not_applicable"
    else:
        axis_status = "unknown"
    return {
        "status": axis_status,
        "technical_quality_notes": notes,
        "late_session_skill_fade": fade,
        "field_observation_status": {
            "technical_quality_notes": notes_status,
            "late_session_skill_fade": fade_status,
        },
        "provenance": {
            "validation": "explicit_structured_fields_with_missing_sentinel_normalization",
            "field_sources": {
                "technical_quality_notes": notes_source if raw_notes not in (None, "") else None,
                "late_session_skill_fade": fade_source if raw_fade not in (None, "") else None,
            },
        },
        "guardrail": (
            "Only explicit structured technical fields populate this axis. General effort prose, Garmin "
            "Feel/RPE, speed, load, Flow, Grit, and Performance Condition cannot substitute for them."
        ),
    }


def _nested_dict(value: Any, *keys: str) -> dict:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def _first_present(records: Iterable[tuple[str, dict]], keys: Iterable[str]) -> tuple[Any, str | None]:
    for prefix, record in records:
        for key in keys:
            if isinstance(record, dict) and key in record and record.get(key) not in (None, ""):
                return record.get(key), f"{prefix}.{key}"
    return None, None


def _bounded_rpe(value: Any) -> float | None:
    number = as_number(value)
    if number is None or not 0 <= number <= 10:
        return None
    return round(number, 1)


def _entry_timestamp_key(entry: dict, position: int) -> tuple[datetime, int]:
    value = entry.get("timestamp_local") or entry.get("timestamp") or entry.get("created_at")
    if value not in (None, ""):
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.replace(tzinfo=None)
            return parsed, position
        except ValueError:
            pass
    return datetime.min, position


def _select_entry(entries: list[dict], activity_id: str | None) -> tuple[dict | None, str]:
    candidates = [(position, entry) for position, entry in enumerate(entries) if isinstance(entry, dict)]
    if activity_id is not None:
        matched = [
            (position, entry)
            for position, entry in candidates
            if str(entry.get("activity_id") or "") == activity_id
        ]
        if matched:
            return max(matched, key=lambda item: _entry_timestamp_key(item[1], item[0]))[1], "activity_id_then_latest_timestamp"
        return None, "requested_activity_id_not_found"
    if candidates:
        return max(candidates, key=lambda item: _entry_timestamp_key(item[1], item[0]))[1], "latest_timestamp"
    return None, "no_entry"


def _unavailable_response(
    *,
    target: date,
    activity_id: str | None,
    status: str,
    reason: str,
    provenance: dict,
    guardrail: str | None = None,
) -> dict:
    return {
        "artifact_type": "latest_session_response",
        "version": SESSION_RESPONSE_VERSION,
        "date": target.isoformat(),
        "status": status,
        "activity_id": activity_id,
        "global_rpe_0_to_10": None,
        "local_rpe_0_to_10": None,
        "stop_rule_outcome": None,
        "stop_rule_outcome_explicit": False,
        "symptom": {
            "distribution": "unknown",
            "character": "unknown",
            "locations": [],
            "mechanics_altered": "unknown",
            "onset": None,
            "onset_min": None,
            "post_session_intensity_0_to_10": None,
            "resolved": "unknown",
            "resolution_time_local": None,
            "resolution_duration_min": None,
        },
        "decision_use": {
            "classification": "insufficient_evidence",
            "reasons": [reason],
            "guardrail": guardrail
            or (
                "Missing feedback cannot establish that symptoms were absent or that a stop rule was not triggered; "
                "this surface may hold or downshift a call but cannot promote training by itself."
            ),
        },
        "provenance": provenance,
    }


def _evidence_text(records: Iterable[tuple[str, dict]]) -> tuple[str, list[str]]:
    keys = (
        "local_leg_symptom",
        "symptom_classification",
        "athlete_response",
        "athlete_interpretation",
        "stop_rule_interpretation",
        "mechanics_response",
        "mechanics_notes",
    )
    values: list[str] = []
    sources: list[str] = []
    for prefix, record in records:
        for key in keys:
            value = record.get(key) if isinstance(record, dict) else None
            if isinstance(value, str) and value.strip():
                values.append(value.strip())
                sources.append(f"{prefix}.{key}")
    return " ".join(values), sources


def _normalize_distribution(explicit: Any, text: str) -> str:
    value = str(explicit or "").strip().lower().replace("-", "_").replace(" ", "_")
    if value in {"bilateral", "both_sides", "diffuse_bilateral", "symmetric", "symmetrical"}:
        return "bilateral"
    if value in {"asymmetric", "asymmetrical", "unilateral", "one_sided", "left_only", "right_only"}:
        return "asymmetric"
    lower = text.lower()
    if re.search(r"\b(asymmet(?:ric|rical)|unilateral|one[- ]sided)\b", lower):
        return "asymmetric"
    if re.search(r"\b(bilateral|both sides|both legs|symmetric(?:al)?)\b", lower):
        return "bilateral"
    return "unknown"


def _normalize_character(explicit: Any, text: str) -> str:
    value = str(explicit or "").strip().lower().replace("-", "_").replace(" ", "_")
    if value in {"muscular_burn", "muscular_burning", "muscle_burn", "diffuse_muscular_burn"}:
        return "muscular_burn"
    if value in {"sharp", "focal", "sharp_or_focal", "sharp_pain", "focal_pain"}:
        return "sharp_or_focal"

    lower = text.lower()
    # Explicit negations are evidence against, not for, the sharp/focal category.
    positive_text = re.sub(
        r"\b(?:not|no|without)\s+(?:sharp(?:\s+or)?\s+focal|sharp|focal)(?:\s+pain)?\b",
        " ",
        lower,
    )
    if re.search(r"\bsharp(?:\s+pain)?\b|\bfocal(?:\s+pain)?\b", positive_text):
        return "sharp_or_focal"
    if re.search(r"\bmuscl(?:e|ar)\s+burn(?:ing)?\b|\bmuscular\s+burning\b", lower):
        return "muscular_burn"
    return "unknown"


def _normalize_yes_no_unknown(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in {"yes", "true", "altered", "changed", "1"}:
        return "yes"
    if text in {"no", "false", "unchanged", "stable", "not_altered", "0"}:
        return "no"
    return "unknown"


def _mechanics_from_text(text: str) -> str:
    lower = text.lower()
    if re.search(r"\b(?:mechanics|form|pedal(?:ling|ing)|gait)\s+(?:were\s+|was\s+)?(?:unchanged|stable|not altered)\b", lower):
        return "no"
    if re.search(r"\b(?:altered|changed|compromised)\s+(?:mechanics|form|pedal(?:ling|ing)|gait)\b", lower):
        return "yes"
    return "unknown"


def _resolution_status(explicit: Any, text: str, post_intensity: float | None) -> str:
    normalized = _normalize_yes_no_unknown(explicit)
    if normalized != "unknown":
        return normalized
    lower = text.lower()
    if re.search(r"\b(?:resolved|cleared)\s+(?:fully|completely)\b|\bresolved\s+to\s+0(?:\.0)?/10\b", lower):
        return "yes"
    if re.search(r"\b(?:persisted|persistent|still present|not resolved|did not resolve)\b", lower):
        return "no"
    if post_intensity == 0 and re.search(r"\b(?:after|post)[ -]?(?:session|ride)\b", lower):
        return "yes"
    return "unknown"


def _minute_from_onset(value: Any) -> float | None:
    number = as_number(value)
    if number is not None:
        return round(number, 1) if number >= 0 else None
    text = str(value or "")
    match = re.search(r"(?:minute\s*|approximately\s+)(\d+(?:\.\d+)?)\s*(?:minutes?|min)?", text, re.I)
    if match is None:
        match = re.search(r"(\d+(?:\.\d+)?)\s*(?:minutes?|min)\s+(?:after|into)", text, re.I)
    return round(float(match.group(1)), 1) if match else None


def _post_intensity_from_text(text: str) -> float | None:
    patterns = (
        r"(?:reported|was|resolved|resolving|fell|down)\s+(?:fully\s+)?(?:to|at)\s+(\d+(?:\.\d+)?)/10\s+(?:at|approximately|after|post)",
        r"(\d+(?:\.\d+)?)/10\s+at\s+approximately\s+\d{1,2}:\d{2}",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return _bounded_rpe(match.group(1))
    return None


def _resolution_time_from_text(text: str) -> str | None:
    patterns = (
        r"(?:resolved|resolving|reported)\b.{0,80}?\bat\s+(?:approximately\s+)?(\d{1,2}:\d{2})\b",
        r"\b0(?:\.0)?/10\s+at\s+(?:approximately\s+)?(\d{1,2}:\d{2})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1)
    return None


def _locations(text: str) -> list[str]:
    lower = text.lower()
    aliases = {
        "glutes": ("glute", "glutes", "gluteal"),
        "hamstrings": ("hamstring", "hamstrings"),
        "quadriceps": ("quad", "quads", "quadriceps"),
        "calves": ("calf", "calves"),
    }
    return [label for label, terms in aliases.items() if any(re.search(rf"\b{re.escape(term)}\b", lower) for term in terms)]


def _decision_classification(
    *,
    distribution: str,
    character: str,
    mechanics_altered: str,
    resolved: str,
    onset_min: float | None,
    duration_min: float | None,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if character == "sharp_or_focal":
        reasons.append("sharp_or_focal_character")
    if distribution == "asymmetric":
        reasons.append("asymmetric_distribution")
    if mechanics_altered == "yes":
        reasons.append("mechanics_altered")
    if resolved == "no":
        reasons.append("symptom_persisted")
    if reasons:
        return "stop_or_downshift_signal", reasons

    terminal = (
        onset_min is not None
        and duration_min is not None
        and onset_min >= max(0.0, duration_min - 10.0)
    )
    if distribution == "bilateral" and character == "muscular_burn" and resolved == "yes" and terminal:
        reasons = ["bilateral", "muscular_burn", "resolved", "onset_in_final_10_min"]
        if mechanics_altered == "unknown":
            reasons.append("mechanics_not_reported")
        return "benign_terminal_muscular_burn_candidate", reasons
    return "insufficient_evidence", reasons


def build_latest_session_response(
    root: str | Path | None = None,
    target_date: date | str | None = None,
    *,
    activity_id: str | int | None = None,
    self_evaluation: dict | None = None,
    subjective_review_policy: dict | None = None,
) -> dict:
    """Normalize the target date's latest (or activity-matched) explicit session feedback.

    This is an observability surface, not a diagnosis or automatic training promoter.
    Missing evidence stays unknown and a stop-rule outcome is never inferred from symptoms.
    """

    target = parse_date(target_date) if target_date is not None else today_local()
    if target is None:
        raise ValueError(f"Invalid target date: {target_date!r}")
    requested_activity_id = str(activity_id) if activity_id is not None else None
    garmin_subjective = _garmin_subjective_evaluation(
        self_evaluation,
        target=target,
        requested_activity_id=requested_activity_id,
    )
    policy_evaluation = _subjective_policy_evaluation(
        subjective_review_policy,
        target=target,
        requested_activity_id=requested_activity_id,
        garmin=garmin_subjective,
    )
    path = input_dir(root) / f"feedback_{target.isoformat()}.json"
    provenance = {
        "source": _relative_path(root, path),
        "target_date": target.isoformat(),
        "requested_activity_id": requested_activity_id,
    }
    payload = read_json(path, None)
    if not isinstance(payload, dict):
        return _merge_garmin_only_response(
            _unavailable_response(
                target=target,
                activity_id=requested_activity_id,
                status="feedback_missing",
                reason="feedback_missing",
                provenance=provenance,
            ),
            garmin_subjective,
            policy_evaluation=policy_evaluation,
        )

    payload_date = _safe_parse_date(payload.get("date"))
    provenance["payload_date"] = payload_date.isoformat() if payload_date else None
    provenance["payload_date_raw"] = payload.get("date")
    if payload_date != target:
        # Keep a complete but explicitly unusable surface without falling back to another date.
        return _merge_garmin_only_response(
            _unavailable_response(
                target=target,
                activity_id=requested_activity_id,
                status="feedback_wrong_date",
                reason=(
                    "payload_date_malformed"
                    if payload.get("date") not in (None, "") and payload_date is None
                    else "payload_date_mismatch"
                ),
                provenance=provenance,
                guardrail="Feedback from another or malformed date must not drive the target-date coaching call.",
            ),
            garmin_subjective,
            policy_evaluation=policy_evaluation,
        )

    entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
    eligible_entries = []
    malformed_entry_dates = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw_entry_date = entry.get("date")
        if raw_entry_date in (None, ""):
            eligible_entries.append(entry)
            continue
        entry_date = _safe_parse_date(raw_entry_date)
        if entry_date is None:
            malformed_entry_dates += 1
        elif entry_date == target:
            eligible_entries.append(entry)
    provenance["malformed_entry_date_count"] = malformed_entry_dates
    selected, selection_rule = _select_entry(eligible_entries, requested_activity_id)
    provenance["selection_rule"] = selection_rule
    provenance["eligible_entry_count"] = len(eligible_entries)
    if selected is None:
        reason = (
            "requested_activity_id_not_found"
            if selection_rule == "requested_activity_id_not_found"
            else "no_eligible_entry"
        )
        return _merge_garmin_only_response(
            _unavailable_response(
                target=target,
                activity_id=requested_activity_id,
                status="feedback_has_no_matching_entry",
                reason=reason,
                provenance=provenance,
                guardrail=(
                    "Feedback for another activity must not be attached to the requested session; "
                    "a missing explicit stop-rule outcome remains unknown."
                ),
            ),
            garmin_subjective,
            policy_evaluation=policy_evaluation,
        )

    review = _review(selected)
    reported = selected.get("reported_context") if isinstance(selected.get("reported_context"), dict) else {}
    illness_airway = _illness_airway_axis(reported)
    technical_execution = _technical_execution_axis(review)
    symptom_record = _nested_dict(review, "symptom")
    records = (
        ("entries[].session_contract_review.symptom", symptom_record),
        ("entries[].session_contract_review", review),
        ("entries[].reported_context", reported),
        ("entries[]", selected),
    )
    evidence_text, text_sources = _evidence_text(records)

    global_raw, global_source = _first_present(records, ("global_session_rpe_0_to_10", "global_rpe_0_to_10", "session_rpe_0_to_10"))
    local_raw, local_source = _first_present(records, ("local_leg_rpe_0_to_10", "local_muscular_rpe_0_to_10", "local_rpe_0_to_10"))
    distribution_raw, distribution_source = _first_present(records, ("distribution", "symptom_distribution", "local_leg_symptom_distribution"))
    character_raw, character_source = _first_present(records, ("character", "symptom_character", "local_leg_symptom_character"))
    mechanics_raw, mechanics_source = _first_present(records, ("mechanics_altered", "altered_mechanics", "form_altered"))
    onset_raw, onset_source = _first_present(records, ("onset", "local_leg_onset", "symptom_onset", "onset_min"))
    post_raw, post_source = _first_present(records, ("post_session_intensity_0_to_10", "post_ride_intensity_0_to_10", "symptom_intensity_now_0_to_10", "current_symptom_intensity_0_to_10"))
    resolved_raw, resolved_source = _first_present(records, ("resolved", "resolved_post_session", "symptom_resolved"))
    resolution_time_raw, resolution_time_source = _first_present(records, ("resolution_time_local", "resolved_at_local", "symptom_resolution_time_local"))
    resolution_duration_raw, resolution_duration_source = _first_present(records, ("resolution_duration_min", "minutes_to_resolution", "resolved_after_min"))

    distribution = _normalize_distribution(distribution_raw, evidence_text)
    character = _normalize_character(character_raw, evidence_text)
    mechanics = _normalize_yes_no_unknown(mechanics_raw)
    if mechanics == "unknown":
        mechanics = _mechanics_from_text(evidence_text)
    post_intensity = _bounded_rpe(post_raw)
    if post_intensity is None:
        post_intensity = _post_intensity_from_text(evidence_text)
    resolved = _resolution_status(resolved_raw, evidence_text, post_intensity)
    resolution_time = str(resolution_time_raw).strip() if resolution_time_raw not in (None, "") else _resolution_time_from_text(evidence_text)
    resolution_duration = as_number(resolution_duration_raw)
    if resolution_duration is not None:
        resolution_duration = round(resolution_duration, 1) if resolution_duration >= 0 else None
    duration_raw, duration_source = _first_present(records, ("actual_duration_min", "duration_min"))
    duration_min = as_number(duration_raw)
    onset_min = _minute_from_onset(onset_raw)
    classification, reasons = _decision_classification(
        distribution=distribution,
        character=character,
        mechanics_altered=mechanics,
        resolved=resolved,
        onset_min=onset_min,
        duration_min=duration_min,
    )

    explicit_stop, explicit_stop_source = _first_present(records, ("stop_rule_outcome",))
    stop_outcome, stop_audit = _normalize_stop_rule_outcome(
        explicit_stop,
        explicit_stop_source,
    )

    manual_global_rpe = _bounded_rpe(global_raw)
    garmin_global_rpe = (
        (garmin_subjective.get("garmin_perceived_effort") or {}).get(
            "global_rpe_0_to_10"
        )
        if garmin_subjective.get("status") == "available"
        else None
    )
    global_rpe = manual_global_rpe if manual_global_rpe is not None else garmin_global_rpe
    if manual_global_rpe is not None and garmin_global_rpe is not None:
        rpe_resolution = (
            "matched"
            if abs(manual_global_rpe - garmin_global_rpe) < 0.05
            else "manual_and_garmin_disagree_manual_retained"
        )
    elif manual_global_rpe is not None:
        rpe_resolution = "manual_only"
    elif garmin_global_rpe is not None:
        rpe_resolution = "garmin_only"
    else:
        rpe_resolution = "unknown"

    field_sources = {
        "global_rpe_0_to_10": (
            global_source
            if manual_global_rpe is not None
            else (garmin_subjective.get("field_sources") or {}).get("global_rpe_0_to_10")
            if garmin_global_rpe is not None
            else None
        ),
        "local_rpe_0_to_10": local_source,
        "garmin_feel": (garmin_subjective.get("field_sources") or {}).get(
            "garmin_feel_out_of_5"
        ),
        "symptom_distribution": distribution_source or (text_sources if distribution != "unknown" else None),
        "symptom_character": character_source or (text_sources if character != "unknown" else None),
        "mechanics_altered": mechanics_source or (text_sources if mechanics != "unknown" else None),
        "onset": onset_source,
        "post_session_intensity_0_to_10": post_source or (text_sources if post_intensity is not None else None),
        "resolved": resolved_source or (text_sources if resolved != "unknown" else None),
        "resolution_time_local": resolution_time_source or (text_sources if resolution_time is not None else None),
        "resolution_duration_min": resolution_duration_source,
        "actual_duration_min": duration_source,
        "stop_rule_outcome": explicit_stop_source if stop_outcome is not None else None,
    }
    provenance.update(
        {
            "selected_activity_id": str(selected.get("activity_id")) if selected.get("activity_id") is not None else None,
            "selected_timestamp_local": selected.get("timestamp_local"),
            "entry_source": selected.get("source"),
            "field_sources": field_sources,
        }
    )

    combined_subjective = {
        **garmin_subjective,
        "manual_global_rpe_0_to_10": manual_global_rpe,
        "rpe_resolution": rpe_resolution,
    }
    response = {
        "artifact_type": "latest_session_response",
        "version": SESSION_RESPONSE_VERSION,
        "date": target.isoformat(),
        "status": "available",
        "activity_id": provenance["selected_activity_id"],
        "global_rpe_0_to_10": global_rpe,
        "local_rpe_0_to_10": _bounded_rpe(local_raw),
        "subjective_evaluation": combined_subjective,
        "stop_rule_outcome": stop_outcome,
        "stop_rule_outcome_explicit": stop_outcome is not None,
        "symptom": {
            "distribution": distribution,
            "character": character,
            "locations": _locations(evidence_text),
            "mechanics_altered": mechanics,
            "onset": onset_raw,
            "onset_min": onset_min,
            "post_session_intensity_0_to_10": post_intensity,
            "resolved": resolved,
            "resolution_time_local": resolution_time,
            "resolution_duration_min": resolution_duration,
        },
        "decision_use": {
            "classification": classification,
            "reasons": reasons,
            "guardrail": (
                "This is a structured observability surface, not a diagnosis or an automatic training promoter. "
                "A terminal diffuse bilateral muscular burn that resolves promptly may be treated as a local-endurance marker; "
                "sharp/focal or asymmetric symptoms, altered mechanics, persistence, or neurological features require stop/downshift review. "
                "Unknown fields stay unknown, and stop_rule_outcome is surfaced only when explicitly recorded."
            ),
        },
        "provenance": provenance,
    }
    response["manual_feedback"] = {
        "status": "available",
        "reasons": [],
        "source": provenance.get("source"),
    }
    return _attach_response_axes(
        response,
        garmin=combined_subjective,
        policy_evaluation=policy_evaluation,
        stop_audit=stop_audit,
        illness_airway=illness_airway,
        technical_execution=technical_execution,
    )
