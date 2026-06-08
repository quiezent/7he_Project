from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from .briefing import build_daily_brief
from .body_battery_model import build_body_battery_model
from .backfill import historical_backfill
from .checkin import import_checkin, write_checkin_template
from .cleanup import cleanup_derived
from .coach_packet import build_coach_packet
from .context import handle_context_command, load_context, save_context
from .activity_profile import build_activity_profile
from .athlete_questions import build_athlete_question_audit
from .adaptation_profile import build_adaptation_profile
from .data_quality import build_data_quality_report
from .data_inventory import build_data_inventory
from .device_audit import build_device_audit
from .garmin_sync import sync_connect
from .gear_audit import build_gear_audit
from .historical_baselines import build_historical_baselines
from .load_model import build_activity_summary_index, build_modality_load_rollups
from .loop_load import build_loop_load, parse_lap_groups
from .planning import build_today_plan
from .predictive_backtest import build_predictive_backtest
from .predictive_training import build_predictive_review, build_predictive_training
from .readiness import build_readiness
from .reports import (
    insight_memo,
    intraday_trends,
    local_estimates,
    microcycle_forecast,
    review_block,
    weather_snapshot,
    weekly_report,
)
from .self_evaluation import build_self_evaluation_report
from .state import build_current_state
from .training_status import build_training_status_current
from .training_predictor import build_training_predictor
from .training_hypotheses import build_training_hypotheses
from .training_architecture import build_training_architecture
from .wellness import build_wellness_trends
from .wellness_verification import build_wellness_verification


def _emit(data: Any) -> int:
    print(json.dumps(data, indent=2, sort_keys=True))
    return 0


def _add_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", default=None, help="Repository root. Defaults to current package root.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coach_sync")
    sub = parser.add_subparsers(dest="command", required=True)

    bootstrap = sub.add_parser("bootstrap", help="Create default context and input template.")
    _add_root(bootstrap)

    sync = sub.add_parser("sync", help="Fetch Garmin data when possible, then rebuild snapshots.")
    _add_root(sync)
    sync.add_argument("--wellness-days", type=int, default=30)
    sync.add_argument("--activity-limit", type=int, default=200)
    sync.add_argument("--rebuild-only", action="store_true")
    sync.add_argument("--cleanup-derived", action="store_true")

    rebuild = sub.add_parser("rebuild", help="Rebuild derived artifacts without live Garmin fetch.")
    _add_root(rebuild)

    for name, help_text in (
        ("readiness", "Build today's readiness snapshot."),
        ("state", "Build current merged state."),
        ("plan", "Build today's deterministic training/nutrition proposal."),
        ("brief", "Build daily brief JSON and text."),
        ("wellness-trends", "Build normalized Garmin wellness trends."),
        ("wellness-verification", "Verify Garmin sleep and Body Battery summary against raw series."),
        ("activity-profile", "Build normalized Garmin activity profile."),
        ("data-inventory", "Build Garmin data availability inventory."),
        ("training-status", "Build normalized Garmin training-status snapshot."),
        ("activity-index", "Build redacted per-activity summary index."),
        ("modality-rollups", "Build training-load rollups by modality."),
        ("data-quality", "Build data quality and coverage report."),
        ("body-battery-model", "Build simple interpretable Body Battery decision tree."),
        ("training-predictor", "Build bounded next-day training response predictor."),
        ("historical-baselines", "Build historical activity baseline artifact."),
        ("coach-packet", "Build coach-facing evidence triage packet."),
        ("gear-audit", "Audit Garmin activity Gear metadata for bike/source mismatches."),
        ("device-audit", "Audit Garmin Devices & Apps metadata for HR source confidence."),
        ("self-evaluation", "Summarize Garmin post-activity feel and RPE self evaluations."),
        ("predictive-training", "Build predictive session plan plus latest calibration review."),
        ("predictive-review", "Compare a dated predictive prescription with actual Garmin response."),
        ("predictive-backtest", "Replay predictive prescriptions on historical dates and verify outcomes."),
    ):
        child = sub.add_parser(name, help=help_text)
        _add_root(child)
        child.add_argument("--date", default=None)

    adaptation = sub.add_parser("adaptation-profile", help="Build N-of-1 training adaptation profile.")
    _add_root(adaptation)
    adaptation.add_argument("--date", default=None)
    adaptation.add_argument("--days", type=int, default=365, help="Lookback window; use 0 with --all for all available activities.")
    adaptation.add_argument("--all", action="store_true", help="Use the maximum available local Garmin activity range.")

    hypotheses = sub.add_parser("training-hypotheses", help="Test coaching hypotheses against Garmin history.")
    _add_root(hypotheses)
    hypotheses.add_argument("--date", default=None)

    athlete_questions = sub.add_parser("athlete-questions", help="Build targeted answers for athlete-profile questions.")
    _add_root(athlete_questions)
    athlete_questions.add_argument("--date", default=None)

    architecture = sub.add_parser("training-architecture", help="Rebuild Clayton-specific training architecture.")
    _add_root(architecture)
    architecture.add_argument("--date", default=None)

    loop_load = sub.add_parser("loop-load", help="Estimate Garmin-equivalent load by manual lap groups.")
    _add_root(loop_load)
    loop_load.add_argument("--activity-id", required=True)
    loop_load.add_argument("--date", default=None)
    loop_load.add_argument("--loops", nargs="+", required=True, help="Lap groups, for example: 1,2 3,4 5,6")
    loop_load.add_argument("--labels", nargs="*", default=None)
    loop_load.add_argument("--no-fetch-live", action="store_true")

    log = sub.add_parser("log", help="Import subjective feedback.")
    _add_root(log)
    log.add_argument("--from-md", required=True)

    report = sub.add_parser("report", help="Build weekly report.")
    _add_root(report)
    report.add_argument("--days", type=int, default=7)

    weekly = sub.add_parser("weekly-report", help="Build weekly report.")
    _add_root(weekly)
    weekly.add_argument("--days", type=int, default=7)

    memo = sub.add_parser("insight-memo", help="Build insight memo.")
    _add_root(memo)
    memo.add_argument("--days", type=int, default=28)

    review = sub.add_parser("review-block", help="Build recent training review block.")
    _add_root(review)
    review.add_argument("--days", type=int, default=14)

    forecast = sub.add_parser("forecast", help="Build rolling microcycle forecast.")
    _add_root(forecast)
    forecast.add_argument("--days", type=int, default=24)

    for name in ("local-estimates", "intraday-trends", "weather-snapshot"):
        child = sub.add_parser(name)
        _add_root(child)

    cleanup = sub.add_parser("cleanup-derived", help="Remove safe derived caches.")
    _add_root(cleanup)
    cleanup.add_argument("--apply", action="store_true")

    backfill = sub.add_parser("historical-backfill", help="Controlled Garmin historical backfill.")
    _add_root(backfill)
    backfill.add_argument("--start-date", default="2021-07-01")
    backfill.add_argument("--end-date", default=None)
    backfill.add_argument("--activities", action=argparse.BooleanOptionalAction, default=True)
    backfill.add_argument("--wellness", action=argparse.BooleanOptionalAction, default=True)
    backfill.add_argument("--wellness-max-days", type=int, default=120)
    backfill.add_argument("--page-size", type=int, default=100)
    backfill.add_argument("--max-pages", type=int, default=None)
    backfill.add_argument("--delay-seconds", type=float, default=0.4)
    backfill.add_argument("--skip-existing", action=argparse.BooleanOptionalAction, default=True)

    context = sub.add_parser("context", help="Update or inspect athlete context.")
    _add_root(context)
    context_sub = context.add_subparsers(dest="context_command", required=True)

    history = context_sub.add_parser("history")
    history.set_defaults(context_command="history")

    event = context_sub.add_parser("set-event-date")
    event.add_argument("--label", required=True)
    event.add_argument("--date", required=True)
    event.add_argument("--type", default=None)

    phase = context_sub.add_parser("set-goal-phase")
    phase.add_argument("--phase", required=True)
    phase.add_argument("--note", default="")

    return parser


def run(args: argparse.Namespace) -> Any:
    if args.command == "bootstrap":
        context = load_context(args.root)
        save_context(context, args.root)
        checkin = write_checkin_template(args.root)
        return {"context": "config/athlete_context.json", "daily_checkin": str(checkin)}
    if args.command == "sync":
        return sync_connect(
            args.root,
            wellness_days=args.wellness_days,
            activity_limit=args.activity_limit,
            rebuild_only=args.rebuild_only,
            cleanup_after=args.cleanup_derived,
        )
    if args.command == "rebuild":
        return sync_connect(args.root, rebuild_only=True)
    if args.command == "readiness":
        return build_readiness(args.root, args.date)
    if args.command == "state":
        return build_current_state(args.root, args.date)
    if args.command == "plan":
        return build_today_plan(args.root, args.date)
    if args.command == "brief":
        return build_daily_brief(args.root, args.date)
    if args.command == "wellness-trends":
        return build_wellness_trends(args.root, args.date)
    if args.command == "wellness-verification":
        return build_wellness_verification(args.root, args.date)
    if args.command == "activity-profile":
        return build_activity_profile(args.root, args.date)
    if args.command == "data-inventory":
        return build_data_inventory(args.root)
    if args.command == "training-status":
        return build_training_status_current(args.root, args.date)
    if args.command == "activity-index":
        return build_activity_summary_index(args.root, args.date)
    if args.command == "modality-rollups":
        return build_modality_load_rollups(args.root, args.date)
    if args.command == "data-quality":
        return build_data_quality_report(args.root)
    if args.command == "body-battery-model":
        return build_body_battery_model(args.root, args.date)
    if args.command == "training-predictor":
        return build_training_predictor(args.root, args.date)
    if args.command == "adaptation-profile":
        return build_adaptation_profile(args.root, args.date, days=None if args.all else args.days)
    if args.command == "training-hypotheses":
        return build_training_hypotheses(args.root, args.date)
    if args.command == "athlete-questions":
        return build_athlete_question_audit(args.root, args.date)
    if args.command == "training-architecture":
        return build_training_architecture(args.root, args.date)
    if args.command == "loop-load":
        return build_loop_load(
            args.root,
            args.activity_id,
            parse_lap_groups(args.loops),
            date=args.date,
            labels=args.labels,
            fetch_live=not args.no_fetch_live,
        )
    if args.command == "historical-baselines":
        return build_historical_baselines(args.root, args.date)
    if args.command == "coach-packet":
        return build_coach_packet(args.root, args.date)
    if args.command == "gear-audit":
        return build_gear_audit(args.root, args.date)
    if args.command == "device-audit":
        return build_device_audit(args.root, args.date)
    if args.command == "self-evaluation":
        return build_self_evaluation_report(args.root, args.date)
    if args.command == "predictive-training":
        return build_predictive_training(args.root, args.date)
    if args.command == "predictive-review":
        return build_predictive_review(args.root, args.date)
    if args.command == "predictive-backtest":
        dates = [item.strip() for item in str(args.date).split(",") if item.strip()] if args.date else None
        return build_predictive_backtest(args.root, dates)
    if args.command == "log":
        return import_checkin(args.from_md, args.root)
    if args.command in {"report", "weekly-report"}:
        return weekly_report(args.root, args.days)
    if args.command == "insight-memo":
        return insight_memo(args.root, args.days)
    if args.command == "review-block":
        return review_block(args.root, args.days)
    if args.command == "forecast":
        return microcycle_forecast(args.root, args.days)
    if args.command == "local-estimates":
        return local_estimates(args.root)
    if args.command == "intraday-trends":
        return intraday_trends(args.root)
    if args.command == "weather-snapshot":
        return weather_snapshot(args.root)
    if args.command == "cleanup-derived":
        return cleanup_derived(args.root, args.apply)
    if args.command == "historical-backfill":
        return historical_backfill(
            args.root,
            start_date=args.start_date,
            end_date=args.end_date,
            activities=args.activities,
            wellness=args.wellness,
            wellness_max_days=args.wellness_max_days,
            page_size=args.page_size,
            max_pages=args.max_pages,
            delay_seconds=args.delay_seconds,
            skip_existing=args.skip_existing,
        )
    if args.command == "context":
        return handle_context_command(args)
    raise ValueError(f"Unknown command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return _emit(run(args))


def main_for(command: str) -> Callable[[list[str] | None], int]:
    def _main(argv: list[str] | None = None) -> int:
        return main([command, *(argv or [])])

    return _main
