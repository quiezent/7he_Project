from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from .evidence import as_number
from .io import read_json
from .paths import input_dir, repo_root
from .time_utils import parse_date, today_local


SESSION_RESPONSE_VERSION = "session_response_v1"


def _relative_path(root: str | Path | None, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root(root)).as_posix()
    except (OSError, ValueError):
        return path.name


def _review(entry: dict) -> dict:
    value = entry.get("session_contract_review")
    return value if isinstance(value, dict) else {}


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
) -> dict:
    """Normalize the target date's latest (or activity-matched) explicit session feedback.

    This is an observability surface, not a diagnosis or automatic training promoter.
    Missing evidence stays unknown and a stop-rule outcome is never inferred from symptoms.
    """

    target = parse_date(target_date) if target_date is not None else today_local()
    if target is None:
        raise ValueError(f"Invalid target date: {target_date!r}")
    requested_activity_id = str(activity_id) if activity_id is not None else None
    path = input_dir(root) / f"feedback_{target.isoformat()}.json"
    provenance = {
        "source": _relative_path(root, path),
        "target_date": target.isoformat(),
        "requested_activity_id": requested_activity_id,
    }
    payload = read_json(path, None)
    if not isinstance(payload, dict):
        return _unavailable_response(
            target=target,
            activity_id=requested_activity_id,
            status="feedback_missing",
            reason="feedback_missing",
            provenance=provenance,
        )

    payload_date = parse_date(payload.get("date"))
    provenance["payload_date"] = payload_date.isoformat() if payload_date else None
    if payload_date != target:
        # Keep a complete but explicitly unusable surface without falling back to another date.
        return _unavailable_response(
            target=target,
            activity_id=requested_activity_id,
            status="feedback_wrong_date",
            reason="payload_date_mismatch",
            provenance=provenance,
            guardrail="Feedback from another date must not drive the target-date coaching call.",
        )

    entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
    eligible_entries = [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and (
            parse_date(entry.get("date")) is None
            or parse_date(entry.get("date")) == target
        )
    ]
    selected, selection_rule = _select_entry(eligible_entries, requested_activity_id)
    provenance["selection_rule"] = selection_rule
    provenance["eligible_entry_count"] = len(eligible_entries)
    if selected is None:
        reason = (
            "requested_activity_id_not_found"
            if selection_rule == "requested_activity_id_not_found"
            else "no_eligible_entry"
        )
        return _unavailable_response(
            target=target,
            activity_id=requested_activity_id,
            status="feedback_has_no_matching_entry",
            reason=reason,
            provenance=provenance,
            guardrail=(
                "Feedback for another activity must not be attached to the requested session; "
                "a missing explicit stop-rule outcome remains unknown."
            ),
        )

    review = _review(selected)
    reported = selected.get("reported_context") if isinstance(selected.get("reported_context"), dict) else {}
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
    stop_outcome = explicit_stop.strip() if isinstance(explicit_stop, str) and explicit_stop.strip() else explicit_stop
    if stop_outcome in (None, ""):
        stop_outcome = None

    field_sources = {
        "global_rpe_0_to_10": global_source,
        "local_rpe_0_to_10": local_source,
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

    return {
        "artifact_type": "latest_session_response",
        "version": SESSION_RESPONSE_VERSION,
        "date": target.isoformat(),
        "status": "available",
        "activity_id": provenance["selected_activity_id"],
        "global_rpe_0_to_10": _bounded_rpe(global_raw),
        "local_rpe_0_to_10": _bounded_rpe(local_raw),
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
