# Clayton MTB Coaching Stack

## What This Is
- CLI-first Python stack for data-backed MTB coaching and sports nutrition.
- The software stages evidence, constraints, readiness, load, and deterministic proposals.
- The model remains the coach: it interprets the evidence and makes the final call.

## Current Status
- Clayton is cleared by Dr. Teh as of `2026-04-29` for outdoor biking, gym, grip/loading, and normal activity.
- The `2026-04-21` review is historical now.
- Current phase is `return_to_outdoor_reentry` through the first controlled post-clearance block.
- Clearance opens the door; progression still depends on symptoms, load response, Garmin freshness, and next-morning hand response.
- Sunday is Clayton's Sabbath hard-rest day: no planned exercise regardless of readiness.

## Quick Start
- Install runtime dependencies:
  `python -m pip install -r requirements.txt`
- Install dev/test dependencies:
  `python -m pip install -r requirements-dev.txt`
- Install the local package for `python -m coach_sync`:
  `python -m pip install -e .`
- Create the default context and check-in template:
  `python tools/bootstrap.py`
- Rebuild artifacts without Garmin login:
  `python tools/sync_connect.py --rebuild-only`

## Garmin Sync
- Set credentials as environment variables:
  - `GARMIN_EMAIL`
  - `GARMIN_PASSWORD`
- Main sync:
  `python tools/sync_connect.py --wellness-days 30 --activity-limit 200`
- Wellness + rebuild only:
  `python tools/sync_connect.py --wellness-days 30 --activity-limit 0`
- Rebuild only:
  `python tools/sync_connect.py --rebuild-only`

Garmin tokens are handled by the `garminconnect`/`garth` stack. Do not store passwords in repo files.

## Command Surface
Preferred direct commands:
- Current state: `python tools/current_state.py`
- Daily plan: `python tools/today_plan.py`
- Daily brief: `python tools/daily_brief.py`
- Weekly report: `python tools/weekly_report.py --days 7`
- Insight memo: `python tools/insight_memo.py --days 28`
- Review block: `python tools/review_block.py`
- Forecast: `python tools/microcycle_forecast.py --days 24`
- Garmin data inventory: `python tools/data_inventory.py`
- Wellness trends: `python tools/wellness_trends.py`
- Training status: `python tools/training_status.py`
- Activity profile: `python tools/activity_profile.py`
- Redacted activity index: `python tools/activity_index.py`
- Modality load rollups: `python tools/modality_rollups.py`
- Data quality report: `python tools/data_quality.py`
- Body Battery decision tree: `python tools/body_battery_model.py`
- Training response predictor: `python tools/training_predictor.py`
- Historical activity baselines: `python tools/historical_baselines.py`
- Coach evidence packet: `python tools/coach_packet.py`
- Controlled Garmin history backfill: `python tools/historical_backfill.py --start-date 2021-07-01 --wellness-max-days 180`
- Cleanup safe derived caches: `python tools/cleanup_derived.py --apply`

After editable install, the same stack is available through:
- `python -m coach_sync sync --wellness-days 30 --activity-limit 200`
- `python -m coach_sync rebuild`
- `python -m coach_sync state`
- `python -m coach_sync plan`
- `python -m coach_sync brief`
- `python -m coach_sync report --days 7`

## Context Updates
- Mark one gate:
  `python tools/update_context.py set-medical-clearance --gate trail --status cleared --date 2026-04-29 --source "Dr. Teh"`
- Mark multiple gates:
  `python tools/update_context.py set-medical-clearance-batch --set cardio=cleared --set grip=cleared --set loading=cleared --set trail=cleared --date 2026-04-29 --source "Dr. Teh"`
- Set goal phase:
  `python tools/update_context.py set-goal-phase --phase base_rebuild --note "Re-entry block completed cleanly."`
- Show context history:
  `python tools/update_context.py history`

## Optional Subjective Log
- Template: `input/daily_checkin.md`
- Import it:
  `python tools/log_feedback.py --from-md input/daily_checkin.md`
- Use it only for what Garmin cannot see:
  - finger/hand pain
  - swelling or inflammation
  - grip tolerance
  - next-morning response
  - travel, stress, schedule constraints, unusual session feel

Blank template fields are ignored.

## Main Artifacts
- `config/athlete_context.json`
  - canonical athlete profile, goal, medical clearance, progression rules, nutrition rules
- `snapshots/current_state.json`
  - merged coaching state
- `snapshots/readiness_YYYY-MM-DD.json`
  - day-level readiness, confidence, reasons, symptoms, data freshness
- `snapshots/training_load.json`
  - recent load, prior-week comparison, load spike flags
- `snapshots/wellness_daily.json`
  - normalized daily sleep, HRV, Body Battery, stress, SpO2, respiration, steps, calories, and intensity minutes
- `snapshots/wellness_trends.json`
  - 7-day and prior-7-day recovery trends
- `snapshots/garmin_training_status_current.json`
  - training status, ACWR, load focus, VO2 max, and acclimation
- `snapshots/activity_summary_index.json`
  - redacted activity rows without names or source paths
- `snapshots/modality_load_rollups.json`
  - 7/14/28/42-day load by modality
- `snapshots/data_quality_report.json`
  - source coverage, missing fields, and privacy/redaction checks
- `snapshots/body_battery_model_report.json`
  - simple pure-Python decision tree for good wake Body Battery
- `snapshots/body_battery_decision_tree.json`
  - interpretable split tree and leaf probabilities
- `snapshots/training_response_model_report.json`
  - bounded decision tree for next-day Garmin-derived training response
- `snapshots/training_prediction_today.json`
  - current feature vector and next-day response prediction
- `snapshots/historical_activity_baselines.json`
  - all-time, yearly, peak-window, and pre-injury MTB baselines
- `snapshots/coach_packet.json` / `snapshots/coach_packet.txt`
  - coach-facing evidence triage: trusted signals, cautions, experimental models, ignored model output, and today's call
- `config/coaching_architecture.json`
  - machine-readable coaching objective, evidence priorities, and phase plan
- `snapshots/injury_return.json`
  - post-clearance hand/trail/gym exposure and symptom context
- `snapshots/today_plan.json`
  - deterministic training and nutrition proposal
- `snapshots/daily_brief.json` / `snapshots/daily_brief.txt`
  - compact human handoff
- `activities/`
  - immutable Garmin activity evidence

## Architecture Boundary
- Code answers: what data exists, what changed, what is stale, what rules are triggered.
- Coach answers: what Clayton should actually do today and why.
- The stack should warn on stale or missing Garmin data before confident hard-session guidance.
- Scheduled Sabbath rest overrides workout selection; the plan should become rest, not a training option.
- Active medical modality overrides can block a modality even when gates are otherwise cleared.
- First-block re-entry exposure caps are enforced before adding more outdoor MTB.
- Wake Body Battery and current Body Battery are separate signals; current Body Battery is time-of-day sensitive.

## Data Boundaries
- Treat raw Garmin exports and `activities/` as preserved evidence.
- Cleanup only removes safe derived caches such as `__pycache__` and `.pytest_cache`.
- Do not commit credentials, Garmin tokens, or private exports to a public location.

## Testing
- Run:
  `python -m pytest -q`
- Current tests cover:
  - Dr. Teh clearance gates
  - Garmin nested sleep, HRV, and Body Battery normalization
  - Garmin activity training-load extraction
  - motorsport/non-training activity exclusion from load rollups
  - normalized ACWR, VO2, and load-focus training status
  - simple Body Battery decision-tree training and output
  - bounded training-response predictor and CLI output

## Modeling Rules
- ML outputs are coaching evidence, not instructions.
- More is not better; better is better. The coach packet is the preferred decision surface.
- Use long activity history for modality/load baselines and block labels.
- Use modern complete wellness only for HRV/wake Body Battery models.
- If model validation does not beat a simple baseline, mark it experimental and do not let it steer training.
  - active modality overrides
  - stale Garmin data detection
  - future-dated Garmin snapshot rejection
  - stale subjective check-in rejection
  - symptom-based hard-session blocking
  - re-entry MTB exposure caps
  - outdoor re-entry planning
  - uncleared-gate blocking
  - hard-session downshift when activity evidence is missing
  - generated state artifacts
  - cleanup boundaries
  - CLI rebuild output

## Troubleshooting
- If `python -m coach_sync` fails, run:
  `python -m pip install -e .`
- If readiness is yellow due to missing Garmin data, run a Garmin sync or accept that hard-session confidence is limited.
- If Garmin auth fails, check `GARMIN_EMAIL`, `GARMIN_PASSWORD`, and cached tokens under `~/.garminconnect`.
- If no activities appear, increase `--activity-limit` and confirm Garmin login works.

## Privacy
This repo contains personal health and training data. Keep it local and private.
