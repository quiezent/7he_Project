"""Reference integrity checks for externally persisted coaching records.

Standard library only. No connector access, physiological model, workout
prescription, persistence writes, or clinical/technical clearance is provided.
A clean result means specified RECORD checks passed, not that training is safe.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any


class RecordError(ValueError):
    """A record is malformed or cannot support the requested comparison."""


CONTRACT_FIELDS = (
    "decision_id", "version", "athlete_key", "created_at", "evidence_cutoff",
    "session_date", "status", "purpose", "dose", "adaptation_hypothesis",
    "execution_rules", "expected_response", "stop_rules", "review_fields",
)


def timestamp(value: str) -> datetime:
    """Parse an ISO timestamp; naive times cannot establish an evidence cutoff."""
    if not isinstance(value, str):
        raise RecordError("timestamp must be an ISO string")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RecordError("invalid timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise RecordError("timestamp requires an explicit timezone")
    return result


def calendar_date(value: str) -> date:
    """Dates refer to the athlete's local calendar, not a UTC-day guess."""
    if not isinstance(value, str):
        raise RecordError("calendar date must be YYYY-MM-DD")
    try:
        result = date.fromisoformat(value)
    except ValueError as exc:
        raise RecordError("invalid calendar date") from exc
    if value != result.isoformat():
        raise RecordError("calendar date must be YYYY-MM-DD")
    return result


def canonical(record: Mapping[str, Any]) -> str:
    try:
        return json.dumps(record, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise RecordError("record must contain finite JSON values") from exc


def validate_contract(contract: Mapping[str, Any]) -> None:
    if not isinstance(contract, Mapping):
        raise RecordError("contract must be an object")
    missing = [key for key in CONTRACT_FIELDS if key not in contract]
    if missing:
        raise RecordError("missing contract fields: " + ", ".join(missing))
    for key in ("decision_id", "athlete_key", "purpose", "adaptation_hypothesis",
                "expected_response"):
        if not isinstance(contract[key], str) or not contract[key].strip():
            raise RecordError(key + " must be nonempty text")
    if type(contract["version"]) is not int or contract["version"] < 1:
        raise RecordError("version must be a positive integer")
    if contract["status"] not in ("candidate", "active", "superseded"):
        raise RecordError("invalid contract status")
    if timestamp(contract["evidence_cutoff"]) > timestamp(contract["created_at"]):
        raise RecordError("evidence cannot come from after contract creation")
    calendar_date(contract["session_date"])
    if not isinstance(contract["dose"], Mapping) or not contract["dose"]:
        raise RecordError("dose must be a nonempty object with explicit units")
    for key in ("execution_rules", "stop_rules", "review_fields"):
        values = contract[key]
        if not isinstance(values, list) or not values or any(
            not isinstance(v, str) or not v.strip() for v in values
        ):
            raise RecordError(key + " must be a nonempty list of text")
    canonical(contract)


def contract_digest(contract: Mapping[str, Any]) -> str:
    """Hash all fields, including optional work; this is NOT a digital signature."""
    validate_contract(contract)
    return hashlib.sha256(canonical(contract).encode("utf-8")).hexdigest()


def require_same_contract(visible: Mapping[str, Any], saved: Mapping[str, Any]) -> str:
    """Compare structured representations of communicated and saved prescriptions.

    This cannot independently verify what was said in chat or displayed on a watch.
    The caller must construct both representations honestly from those surfaces.
    """
    expected = contract_digest(visible)
    if expected != contract_digest(saved):
        raise RecordError("communicated and saved contracts differ")
    return expected


def evidence_known_by(record: Mapping[str, Any], cutoff: str) -> bool:
    """Unknown/malformed timestamps are errors, never affirmative evidence."""
    try:
        observed = timestamp(record["observed_at"])
        known = timestamp(record["known_at"])
    except KeyError as exc:
        raise RecordError("evidence requires observed_at and known_at") from exc
    if observed > known:
        raise RecordError("evidence cannot be known before it was observed")
    return known <= timestamp(cutoff)


def progression_record_issues(
    contract: Mapping[str, Any],
    delivery: Mapping[str, Any],
    response: Mapping[str, Any],
    *,
    decision_cutoff: str,
    proposed_levers: Sequence[str],
) -> tuple[str, ...]:
    """Audit a proposed one-lever progression against matched reviewed records.

    Empty output means only that these documentary prerequisites were satisfied.
    Fresh function, symptoms, sleep coverage, actual weekly cost, venue, equipment,
    and suitability of the lever remain separate coaching decisions. Reports are
    not independently verified by this function. 'Mixed' retains its components;
    it is not a diagnosis, blanket regression, or proof of positive absorption.
    """
    validate_contract(contract)
    cutoff = timestamp(decision_cutoff)
    if not isinstance(delivery, Mapping) or not isinstance(response, Mapping):
        raise RecordError("delivery and response must be objects")
    if isinstance(proposed_levers, (str, bytes)) or not isinstance(proposed_levers, Sequence):
        raise RecordError("proposed_levers must be a sequence of names")
    issues: list[str] = []
    if len(proposed_levers) != 1 or any(
        not isinstance(v, str) or not v.strip() for v in proposed_levers
    ):
        issues.append("select exactly one progression lever")
    if timestamp(contract["created_at"]) > cutoff:
        issues.append("contract was authored after this decision cutoff")
    if len(proposed_levers) == 1 and delivery.get("reviewed_lever") != proposed_levers[0]:
        issues.append("positive outcome is not matched to the proposed lever")
    if contract["status"] != "active":
        issues.append("candidate or superseded contract is not the executed authority")
    for key in ("athlete_key", "decision_id", "version", "session_date"):
        if delivery.get(key) != contract[key]:
            issues.append("delivery identity mismatch: " + key)
    activity_id = delivery.get("activity_id")
    if not isinstance(activity_id, str) or not activity_id.strip():
        issues.append("delivery lacks an activity/session identifier")
    if delivery.get("reviewed") is not True:
        issues.append("delivery has not been explicitly reviewed")
    if delivery.get("dose_matches") is not True:
        issues.append("actual dose is not confirmed to match the contract")
    if delivery.get("stop_outcome") != "no_trigger_reported":
        issues.append("stop-boundary evidence does not support promotion")
    if delivery.get("lever_outcome") != "positive":
        issues.append("the selected lever lacks a positive reviewed outcome")
    for label, record in (("delivery", delivery), ("response", response)):
        try:
            if not evidence_known_by(record, decision_cutoff):
                issues.append(label + " became known after this decision cutoff")
        except RecordError:
            issues.append(label + " has missing or invalid evidence timestamps")
    if response.get("athlete_key") != contract["athlete_key"]:
        issues.append("response belongs to a different or unknown athlete")
    if not activity_id or response.get("activity_id") != activity_id:
        issues.append("response is not linked to the delivered activity/session")
    next_day = calendar_date(contract["session_date"]) + timedelta(days=1)
    if response.get("response_date") != next_day.isoformat():
        issues.append("response is not for the exact next local-calendar day")
    if response.get("reviewed") is not True or response.get("absorption") != "positive":
        issues.append("matched positive absorption is not established")
    return tuple(issues)
