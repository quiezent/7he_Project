from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from typing import Iterable

from .defaults import clone_default_context
from .io import read_json, write_json
from .paths import context_path, ensure_layout
from .time_utils import DEFAULT_TIMEZONE, iso_now, parse_date


VALID_GATES = {"cardio", "grip", "loading", "trail"}
VALID_STATUSES = {"cleared", "pending", "blocked", "unknown"}


def load_context(root: str | Path | None = None, create: bool = True) -> dict:
    ensure_layout(root)
    path = context_path(root)
    if not path.exists():
        if not create:
            raise FileNotFoundError(path)
        context = clone_default_context()
        write_json(path, context)
        return context
    return read_json(path)


def save_context(context: dict, root: str | Path | None = None) -> dict:
    tz = context.get("athlete", {}).get("timezone", DEFAULT_TIMEZONE)
    context["updated_at"] = iso_now(tz)
    write_json(context_path(root), context)
    return context


def append_history(context: dict, event_type: str, **fields: object) -> None:
    tz = context.get("athlete", {}).get("timezone", DEFAULT_TIMEZONE)
    context.setdefault("history", []).append(
        {"timestamp": iso_now(tz), "type": event_type, **fields}
    )


def set_clearance(
    context: dict,
    gate: str,
    status: str,
    effective_date: str,
    source: str = "manual",
    note: str = "",
) -> dict:
    gate = gate.strip().lower()
    status = status.strip().lower()
    if gate not in VALID_GATES:
        raise ValueError(f"Unknown clearance gate: {gate}")
    if status not in VALID_STATUSES:
        raise ValueError(f"Unknown clearance status: {status}")
    context.setdefault("medical", {}).setdefault("clearance_gates", {})[gate] = {
        "status": status,
        "date": effective_date,
        "source": source,
        "note": note,
    }
    append_history(
        context,
        "medical_clearance_updated",
        gate=gate,
        status=status,
        date=effective_date,
        source=source,
        note=note,
    )
    return context


def set_clearance_batch(
    context: dict,
    updates: Iterable[str],
    effective_date: str,
    source: str = "manual",
    note: str = "",
) -> dict:
    for item in updates:
        if "=" not in item:
            raise ValueError(f"Expected gate=status, got: {item}")
        gate, status = item.split("=", 1)
        set_clearance(context, gate, status, effective_date, source, note)
    return context


def add_modality_override(
    context: dict,
    status: str,
    modalities: list[str],
    reason: str,
    effective_from: str,
    label: str = "manual_override",
) -> dict:
    override = {
        "label": label,
        "status": status,
        "modalities": modalities,
        "reason": reason,
        "effective_from": effective_from,
    }
    context.setdefault("medical", {}).setdefault("modality_overrides", []).append(override)
    append_history(context, "medical_modality_override_added", **override)
    return context


def set_event_date(
    context: dict,
    label: str,
    event_date: str,
    event_type: str | None = None,
    post_clearance_ramp_days: int | None = None,
) -> dict:
    event = {"label": label, "date": event_date}
    if event_type:
        event["type"] = event_type
    if post_clearance_ramp_days is not None:
        event["post_clearance_ramp_days"] = post_clearance_ramp_days
    history = context.setdefault("medical", {}).setdefault("history", [])
    for existing in history:
        if existing.get("label") == label:
            existing.update(event)
            break
    else:
        history.append(event)
    append_history(context, "event_date_updated", **event)
    return context


def clearance_summary(context: dict) -> dict:
    gates = context.get("medical", {}).get("clearance_gates", {})
    missing = sorted(VALID_GATES - set(gates))
    statuses = {gate: gates.get(gate, {"status": "unknown"}) for gate in sorted(VALID_GATES)}
    all_cleared = all(statuses[gate].get("status") == "cleared" for gate in VALID_GATES)
    return {"all_cleared": all_cleared, "missing": missing, "gates": statuses}


def active_modality_overrides(context: dict, target_date: str) -> list[dict]:
    active = []
    target = parse_date(target_date)
    for override in context.get("medical", {}).get("modality_overrides", []):
        effective = parse_date(override.get("effective_from"))
        if effective and target and effective <= target:
            active.append(override)
    return active


def handle_context_command(args: Namespace) -> dict | list:
    context = load_context(args.root)
    if args.context_command == "history":
        return context.get("history", [])
    if args.context_command == "set-medical-clearance":
        set_clearance(context, args.gate, args.status, args.date, args.source, args.note)
    elif args.context_command == "set-medical-clearance-batch":
        set_clearance_batch(context, args.set, args.date, args.source, args.note)
    elif args.context_command == "set-medical-modality-override":
        add_modality_override(
            context,
            args.status,
            args.modalities,
            args.reason,
            args.effective_from,
            args.label,
        )
    elif args.context_command == "set-event-date":
        set_event_date(context, args.label, args.date, args.type, args.post_clearance_ramp_days)
    elif args.context_command == "set-goal-phase":
        context.setdefault("goal_progression", {})["current_phase"] = args.phase
        append_history(context, "goal_phase_updated", phase=args.phase, note=args.note)
    else:
        raise ValueError(f"Unknown context command: {args.context_command}")
    return save_context(context, args.root)
