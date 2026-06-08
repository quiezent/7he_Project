from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from .defaults import clone_default_context
from .io import read_json, write_json
from .paths import context_path, ensure_layout
from .time_utils import DEFAULT_TIMEZONE, iso_now


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


def set_event_date(
    context: dict,
    label: str,
    event_date: str,
    event_type: str | None = None,
) -> dict:
    event = {"label": label, "date": event_date}
    if event_type:
        event["type"] = event_type
    history = context.setdefault("medical", {}).setdefault("history", [])
    for existing in history:
        if existing.get("label") == label:
            existing.update(event)
            break
    else:
        history.append(event)
    append_history(context, "event_date_updated", **event)
    return context


def handle_context_command(args: Namespace) -> dict | list:
    context = load_context(args.root)
    if args.context_command == "history":
        return context.get("history", [])
    if args.context_command == "set-event-date":
        set_event_date(context, args.label, args.date, args.type)
    elif args.context_command == "set-goal-phase":
        context.setdefault("goal_progression", {})["current_phase"] = args.phase
        append_history(context, "goal_phase_updated", phase=args.phase, note=args.note)
    else:
        raise ValueError(f"Unknown context command: {args.context_command}")
    return save_context(context, args.root)
