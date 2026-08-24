from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .io import read_json
from .paths import input_dir
from .time_utils import parse_date


SESSION_CONTRACT_FIELDS = (
    "purpose",
    "dose",
    "adaptation_hypothesis",
    "execution_rules",
    "expected_result",
    "stop_rules",
    "post_session_review_fields",
)

RACE_MODALITIES = {
    "bike_outdoor",
    "cycling_outdoor",
    "mtb",
    "mtb_race",
}
RACE_INTENSITIES = {
    "competition",
    "hard",
    "moderate_hard",
    "race",
    "very_hard",
}
RACE_DISCIPLINES = {
    "downhill",
    "downhill_mtb",
    "enduro",
    "mountain_bike_downhill",
    "mtb_downhill",
}


def scheduled_rest_rule(context: dict[str, Any], target_date: date) -> dict | None:
    for rule in context.get("training_rules", {}).get("weekly_rest_days", []):
        if int(rule.get("weekday", -1)) == target_date.weekday():
            return dict(rule)
    return None


def _has_complete_session_contract(session: dict[str, Any]) -> bool:
    return bool(
        session.get("schema_version") == 3
        and all(session.get(field) for field in SESSION_CONTRACT_FIELDS)
    )


def _duration_min(session: dict[str, Any]) -> int | None:
    try:
        value = int(session.get("duration_min"))
    except (TypeError, ValueError):
        return None
    return value


def _planned_session_source(planned_session: dict[str, Any], target_date: date) -> str:
    source = planned_session.get("source")
    if isinstance(source, dict) and source.get("path"):
        return str(source["path"])
    return f"input/planned_session_{target_date.isoformat()}.json"


def _base_exception_is_valid(
    exception: dict[str, Any],
    target_date: date,
) -> bool:
    return bool(
        parse_date(exception.get("date")) == target_date
        and target_date.weekday() == 6
        and str(exception.get("authorized_by") or "").lower() == "athlete"
        and exception.get("explicit_one_off") is True
        and exception.get("recurring_rule_unchanged") is True
    )


def _validated_indoor_exception(
    exception: dict[str, Any],
    session: dict[str, Any],
    target_date: date,
) -> dict[str, Any] | None:
    duration = _duration_min(session)
    if not (
        exception.get("scope") == "indoor_low_aerobic_only"
        and str(session.get("modality") or "").lower() == "bike_indoor"
        and str(session.get("intensity") or "").lower() in {"easy", "recovery"}
        and duration is not None
        and 0 < duration <= 60
        and _has_complete_session_contract(session)
    ):
        return None
    return {
        **exception,
        "exception_type": "athlete_authorized_low_aerobic",
        "status": "validated_exact_date_low_consequence_exception",
        "authorization": {
            "authorized_by": "athlete",
            "explicit_one_off": True,
            "date": target_date.isoformat(),
        },
    }


def _validated_race_exception(
    exception: dict[str, Any],
    session: dict[str, Any],
    target_date: date,
) -> dict[str, Any] | None:
    event = exception.get("event")
    replacement_date = parse_date(exception.get("replacement_sabbath_date"))
    duration = _duration_min(session)
    session_type = str(session.get("type") or "").lower()
    modality = str(session.get("modality") or "").lower()
    intensity = str(session.get("intensity") or "").lower()
    discipline = str((event or {}).get("discipline") or "").lower()
    event_venue = str(
        (event or {}).get("venue") or (event or {}).get("venue_key") or ""
    ).strip()
    valid = bool(
        exception.get("scope") == "named_race_event_only"
        and str(exception.get("authorization_source") or "").strip()
        and isinstance(event, dict)
        and str(event.get("name") or "").strip()
        and event_venue
        and parse_date(event.get("date")) == target_date
        and discipline in RACE_DISCIPLINES
        and replacement_date == target_date + timedelta(days=1)
        and replacement_date.weekday() == 0
        and session.get("race_event") is True
        and "race" in session_type
        and modality in RACE_MODALITIES
        and intensity in RACE_INTENSITIES
        and duration is not None
        and 30 <= duration <= 480
        and _has_complete_session_contract(session)
    )
    if not valid:
        return None
    return {
        **exception,
        "exception_type": "athlete_authorized_race_event",
        "status": "validated_exact_date_race_event_exception",
        "authorization": {
            "authorized_by": "athlete",
            "explicit_one_off": True,
            "date": target_date.isoformat(),
            "source": exception.get("authorization_source")
            or "coach-authored planned-session record",
        },
        "replacement_sabbath": {
            "date": replacement_date.isoformat(),
            "status": "hard_no_exercise",
            "reason": (
                f"Replacement Sabbath after {str(event.get('name')).strip()} race day; "
                "no planned exercise."
            ),
        },
    }


def validate_sabbath_exception(
    planned_session: dict[str, Any] | None,
    target_date: date,
    scheduled_rest: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Validate only an exact, athlete-authorized exception to recurring Sabbath.

    Missing or malformed fields intentionally fail closed. A normal Sunday remains a
    hard rest day and neither an event calendar entry nor a race-like session can infer
    authorization.
    """
    if not planned_session or not scheduled_rest:
        return None
    exception = planned_session.get("sabbath_exception")
    session = planned_session.get("session")
    if not isinstance(exception, dict) or not isinstance(session, dict):
        return None
    if not _base_exception_is_valid(exception, target_date):
        return None

    validated = _validated_indoor_exception(exception, session, target_date)
    if validated is None:
        validated = _validated_race_exception(exception, session, target_date)
    if validated is None:
        return None

    return {
        **validated,
        "scheduled_rest_label": scheduled_rest.get("label"),
        "provenance": {
            "source_type": "coach_authored_planned_session",
            "source_path": _planned_session_source(planned_session, target_date),
            "validation": "exact_date_explicit_fields_fail_closed",
        },
    }


def _load_planned_session(root: str | Path | None, target_date: date) -> dict | None:
    path = input_dir(root) / f"planned_session_{target_date.isoformat()}.json"
    payload = read_json(path, {})
    if not isinstance(payload, dict) or not isinstance(payload.get("session"), dict):
        return None
    payload_date = parse_date(payload.get("date"))
    if payload_date is not None and payload_date != target_date:
        return None
    return {
        "session": payload["session"],
        "sabbath_exception": payload.get("sabbath_exception"),
        "source": {
            "type": "input_planned_session",
            "path": f"input/planned_session_{target_date.isoformat()}.json",
        },
    }


def replacement_sabbath_rule(
    root: str | Path | None,
    context: dict[str, Any],
    target_date: date,
) -> dict[str, Any] | None:
    """Return a hard replacement-Sabbath rule only from a validated prior-day race."""
    if target_date.weekday() != 0:
        return None
    race_date = target_date - timedelta(days=1)
    recurring_rule = scheduled_rest_rule(context, race_date)
    planned_session = _load_planned_session(root, race_date)
    validated = validate_sabbath_exception(
        planned_session,
        race_date,
        recurring_rule,
    )
    if not validated or validated.get("exception_type") != "athlete_authorized_race_event":
        return None
    replacement = validated.get("replacement_sabbath") or {}
    if parse_date(replacement.get("date")) != target_date:
        return None
    event = validated.get("event") or {}
    return {
        "weekday": target_date.weekday(),
        "label": "Replacement Sabbath",
        "status": "hard_rest",
        "reason": replacement.get("reason"),
        "replacement_for": {
            "date": race_date.isoformat(),
            "event_name": event.get("name"),
            "event_venue": event.get("venue") or event.get("venue_key"),
        },
        "provenance": validated.get("provenance"),
        "source_exception_status": validated.get("status"),
    }
