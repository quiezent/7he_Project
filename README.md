# Clayton MTB Coaching Stack

## What This Is
- CLI-first Python stack for data-backed MTB coaching and sports nutrition.
- The software stages evidence, constraints, readiness, load, and deterministic proposals.
- The model remains the coach: it interprets the evidence and makes the final call.
- This README is the human/operator guide. Do not store session-specific prescriptions, predictions, or coaching logs here.

## Current Status
- Current phase is `base_rebuild`: the training focus is enduro repeatability.
- Historical context: Clayton broke his left pinky in Aug 2025; this is retained as history only and is not a current decision gate.
- Progression depends on readiness, load response, bike specificity, Garmin freshness, and subjective session quality.
- Sunday is Clayton's Sabbath hard-rest day: no planned exercise regardless of readiness.
- Highest-impact coaching path: bike-specific continuity first, enduro repeatability second, expert skill execution under fatigue third.
- Coaching architecture schema v3 combines directive enduro coaching with Garmin/digital-twin verification through a required session contract and density governor.

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
The Python file count is intentionally split by role:
- `src/coach_sync/` contains package logic.
- `tools/*.py` are thin compatibility wrappers around `python -m coach_sync ...` commands.
- `tests/` protects safety, artifact behavior, and coaching architecture rules.

Tool authority is tiered:
- Decision surface: `coach-packet`, `state`, `plan`, and `brief`.
- Core evidence builders: readiness, wellness, training status, activity index, modality rollups, gear audit, device audit, and self-evaluation.
- Architecture/context builders: training architecture, adaptation profile, training hypotheses, athlete questions, historical baselines, data quality, and data inventory.
- Predictive/experimental tools: Body Battery model, training predictor, predictive training, predictive review, and predictive backtest. These inform questions and reviews unless validation earns stronger authority.
- Low-authority reports or unconfigured sources: weekly report, insight memo, review block, forecast, local estimates, intraday trends, and weather snapshot. Treat these as supporting context unless they are upgraded with real data and tests.

Promotion rule: a tool or artifact is useful only if it is current, tested, athlete-specific, and changes readiness, dose, stop rules, fueling, sensor confidence, or post-session review. More is not better; better is better.

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
- Wellness verification: `python tools/wellness_verification.py --date <YYYY-MM-DD>`
- Training status: `python tools/training_status.py`
- Activity profile: `python tools/activity_profile.py`
- N-of-1 adaptation profile: `python tools/adaptation_profile.py --all`
- Training hypothesis tests: `python tools/training_hypotheses.py --date <YYYY-MM-DD>`
- Athlete profile question audit: `python tools/athlete_questions.py --date <YYYY-MM-DD>`
- Clayton-specific training architecture: `python tools/training_architecture.py --date <YYYY-MM-DD>`
- Gear audit: `python tools/gear_audit.py --date <YYYY-MM-DD>`
- Device audit: `python tools/device_audit.py --date <YYYY-MM-DD>`
- Self evaluation: `python tools/self_evaluation.py --date <YYYY-MM-DD>`
- Redacted activity index: `python tools/activity_index.py`
- Modality load rollups: `python tools/modality_rollups.py`
- Data quality report: `python tools/data_quality.py`
- Body Battery decision tree: `python tools/body_battery_model.py`
- Training response predictor: `python tools/training_predictor.py`
- Predictive training loop: `python tools/predictive_training.py --date <YYYY-MM-DD>`
- Predictive review for a completed prescription: `python tools/predictive_review.py --date <YYYY-MM-DD>`
- Predictive 10-date backtest: `python tools/predictive_backtest.py`
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
- `python -m coach_sync training-architecture --date <YYYY-MM-DD>`
- `python -m coach_sync gear-audit --date <YYYY-MM-DD>`
- `python -m coach_sync device-audit --date <YYYY-MM-DD>`
- `python -m coach_sync self-evaluation --date <YYYY-MM-DD>`
- `python -m coach_sync predictive-training --date <YYYY-MM-DD>`
- `python -m coach_sync predictive-review --date <YYYY-MM-DD>`
- `python -m coach_sync predictive-backtest`

## Predictive Workflow
- Use this before a planned key session to store a testable expectation in `snapshots/`, not in this README.
- For a generic deterministic proposal:
  `python tools/predictive_training.py --date <YYYY-MM-DD>`
- For a specific coached session, call `build_predictive_training(..., plan=<custom plan>)` with the schema v3 session contract fields:
  - `purpose`
  - `dose`
  - `adaptation_hypothesis`
  - `execution_rules`
  - `expected_result`
  - `stop_rules`
  - `post_session_review_fields`
- If target-date Garmin wellness does not exist yet, the prediction should state the basis date and use the next Garmin sync as the final gate.
- After the session and next-day Garmin sync, run:
  `python tools/predictive_review.py --date <YYYY-MM-DD>`

## Context Updates
- Set goal phase:
  `python tools/update_context.py set-goal-phase --phase base_rebuild --note "Training focus updated."`
- Show context history:
  `python tools/update_context.py history`

## Optional Subjective Log
- Template: `input/daily_checkin.md`
- Import it:
  `python tools/log_feedback.py --from-md input/daily_checkin.md`
- Use it only for what Garmin cannot see:
  - next-morning response
  - ride purpose and skill focus
  - trail condition, rain, mud, wet roots/rocks, and heat feel
  - braking fatigue, upper-body fatigue, late-ride skill drop, and aggression/confidence
  - carbs, sodium, fluid, caffeine, and gut tolerance
  - travel, stress, schedule constraints, unusual session feel

Blank template fields are ignored.

## Main Artifacts
- `config/athlete_context.json`
  - canonical athlete profile, historical context, goal, progression rules, nutrition rules
- `snapshots/current_state.json`
  - merged coaching state
- `snapshots/readiness_YYYY-MM-DD.json`
  - day-level readiness, confidence, reasons, subjective response, data freshness
- `snapshots/training_load.json`
  - recent load, prior-week comparison, load spike flags
- `snapshots/wellness_daily.json`
  - normalized daily sleep, HRV, Body Battery, stress, SpO2, respiration, steps, calories, and intensity minutes
- `snapshots/wellness_trends.json`
  - 7-day and prior-7-day recovery trends
- `snapshots/wellness_verification.json` / `snapshots/wellness_verification_YYYY-MM-DD.json`
  - checks Garmin sleep and Body Battery summary against the raw Body Battery series, including post-wake recharge after interrupted sleep
- `snapshots/garmin_training_status_current.json`
  - training status, ACWR, load focus, VO2 max, and acclimation
- `snapshots/activity_summary_index.json`
  - redacted activity rows without names or source paths
- `snapshots/activity_gear_index.json`
  - recent Garmin activity Gear metadata fetched from `get_activity_gear`
- `snapshots/gear_audit.json` / `snapshots/gear_audit.txt`
  - bike/source mismatch audit, including MTB activities still tagged with Elite Suito
- `snapshots/activity_device_index.json`
  - recent Garmin Devices & Apps metadata fetched from `get_activity`
- `snapshots/device_audit.json` / `snapshots/device_audit.txt`
  - HR source confidence audit, including MTB rides without an external HEART_RATE sensor
- `snapshots/activity_self_evaluation_index.json`
  - recent Garmin post-activity feel and RPE metadata fetched from activity details
- `snapshots/self_evaluation_report.json` / `snapshots/self_evaluation_report.txt`
  - recent self-evaluation rows for interpreting whether load felt costly, sustainable, or misleading
  - `directWorkoutFeel`: 0 very weak, 50 normal, 100 very strong
  - `directWorkoutRpe`: 10 very light, 50 hard, 90 extremely hard, 100 maximum
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
- `snapshots/predictive_session_plan.json` / `snapshots/predictive_session_YYYY-MM-DD.json`
  - pre-session digital-twin expectation for planned duration, load, RPE, next-day response, execution-risk drift, and coaching-adjusted strain response
- `snapshots/predictive_session_review.json` / `snapshots/predictive_session_review_YYYY-MM-DD.json`
  - post-session comparison of expected versus actual Garmin load, self-evaluation, next-day response, and calibration eligibility
- `snapshots/predictive_training.json`
  - current predictive loop: today's prescription plus the latest completed-session calibration review
- `snapshots/predictive_backtest_10_dates.json`
  - historical pre-session replay against actual session load, self-evaluation, and next-day Garmin recovery
- `snapshots/historical_activity_baselines.json`
  - all-time, yearly, peak-window, and pre-injury MTB baselines
- `snapshots/adaptation_profile.json` / `snapshots/adaptation_profile.txt`
  - N-of-1 training adaptation profile, detraining flags, and personal rules
- `snapshots/training_hypothesis_tests.json` / `snapshots/training_hypothesis_tests.txt`
  - tested training hypotheses against Garmin history
- `snapshots/athlete_question_audit.json` / `snapshots/athlete_question_audit.txt`
  - targeted answers for bike floor, substitution blocks, FTP staleness, MTB labels, heat, gym, nutrition, and limiter ranking
- `snapshots/training_architecture.json` / `snapshots/training_architecture.txt`
  - dated generated copy of the Clayton-specific training architecture
- `snapshots/coach_packet.json` / `snapshots/coach_packet.txt`
  - coach-facing evidence triage: trusted signals, cautions, experimental models, ignored model output, and today's call
- `config/coaching_architecture.json`
  - schema v3 machine-readable coaching objective, integrated coaching model, session contract, density governor, session library, evidence priorities, and phase plan
- `snapshots/today_plan.json`
  - deterministic training and nutrition proposal
- `snapshots/daily_brief.json` / `snapshots/daily_brief.txt`
  - compact human handoff
- `activities/`
  - immutable Garmin activity evidence

## Architecture Boundary
- Code answers: what data exists, what changed, what is stale, what rules are triggered.
- Coach answers: what Clayton should actually do today and why.
- Predictive loop answers: what response was expected from the prescribed session, what actually happened, and whether the miss was execution, external stress, or model error.
- Session contract answers: what adaptation the session is buying, what the dose is, when to stop, and what must be reviewed afterward.
- Density governor answers: whether the ideal week should be downshifted so Friday/Saturday trail quality is protected.
- Predictive prescriptions now carry two branches: the written plan and the execution-drift stress test when similar Clayton sessions historically became longer or harder.
- The stack should warn on stale or missing Garmin data before confident hard-session guidance.
- Scheduled Sabbath rest overrides workout selection; the plan should become rest, not a training option.
- Wake Body Battery and current Body Battery are separate signals; current Body Battery is time-of-day sensitive.
- Interrupted sleep can make Garmin's wake Body Battery incomplete; the verifier checks the raw series before readiness scoring trusts the wake value.
- Read `config/coaching_architecture.json` before block planning, phase changes, race preparation, or major training recommendations.

## Clayton-Specific Coaching Rules
- Every major prescription should follow the schema v3 session contract: purpose, dose, adaptation hypothesis, execution rules, expected result, stop rules, and post-session review fields.
- Bike-specific continuity is the highest-yield lever. Maintenance floor is about 2 bike touches/week; rebuild target is 3 bike touches/week.
- Protect 2 MTB exposures/week when possible: one quality/skill day and one durability/enduro-volume day.
- Use the density governor: start from 3 good bike touches, do not stack threshold, repeatability, and two hard MTB days unless recovery is clearly green.
- Use one structured indoor tempo/torque session per week and progress `3x8 -> 3x10 -> 3x12` before raising watts.
- Treat `222 W` as historical P20 from `2024-04-24`, not current FTP.
- Elliptical is recovery/support during bike-performance blocks, not the backbone unless constraints require it.
- Gym is useful only if it supports trail quality; reduce or move it if it creates DOMS before key rides.
- Clayton responds best to direct standards, clear constraints, measurable outputs, and honest review; do not substitute generic encouragement for specific coaching.
- Garmin manages the physiological dashboard; the coach must still judge technical execution, descending stress, heat distortion, variable control, and whether the ride improved the rider.
- Key rides should use one physiological target, one technical target, one constraint, and one review question.
- In Kuala Lumpur heat, fuel skill quality early: late sloppy braking, weak pumping, timid jumps, or poor line choice can be under-fuelling or heat load.
- Garmin Gear is the bike/source truth layer. Flag MTB activities tagged with `Elite Suito` so the Gear field can be corrected.
- Garmin Devices & Apps is the HR-source truth layer. MTB activities without an external `HEART_RATE` sensor should have lower-confidence HR/load interpretation because Fenix wrist HR can under-read during technical riding.

## Data Boundaries
- Treat raw Garmin exports and `activities/` as preserved evidence.
- Store session-specific predictions and reviews in `snapshots/predictive_session_*.json`, `snapshots/predictive_session_review_*.json`, and `snapshots/predictive_training.json`.
- Do not store session-specific predictions, reviews, or daily coaching decisions in `README.md` or `AGENTS.md`.
- Use the private GitHub repository as the durable backup for code, tests, documentation, coaching architecture, `config/athlete_context.json`, and curated `input/feedback_*.json` notes.
- Do not use normal GitHub commits as the backup for raw Garmin downloads, generated snapshots, archives, credentials, or transient `input/daily_checkin.md`; those remain ignored and should be redownloaded, rebuilt, or backed up separately if needed.
- Cleanup only removes safe derived caches such as `__pycache__` and `.pytest_cache`.
- Do not commit credentials, Garmin tokens, or private exports to a public location.

## Testing
- Run:
  `python -m pytest -q`
- Current tests cover:
  - historical finger context retained without current training gates
  - Garmin nested sleep, HRV, and Body Battery normalization
  - Garmin activity training-load extraction
  - motorsport/non-training activity exclusion from load rollups
  - normalized ACWR, VO2, and load-focus training status
  - simple Body Battery decision-tree training and output
  - bounded training-response predictor and CLI output
  - predictive training prescription and post-session calibration review
  - Clayton-specific training architecture generation, including schema v3 integrated coaching model, session contract, density governor, and Dinding setup session type
  - Garmin Gear audit and Elite Suito MTB mismatch flagging
  - Garmin Devices & Apps audit and wrist-HR MTB confidence flagging

## Modeling Rules
- ML outputs are coaching evidence, not instructions.
- Use the coaching-adjusted response for the practical prescription call; keep the raw tree output visible for audit.
- Only matched-load sessions calibrate the digital twin. Drifted sessions update execution-risk rules before model blame.
- More is not better; better is better. The coach packet is the preferred decision surface.
- Use long activity history for modality/load baselines and block labels.
- Use modern complete wellness only for HRV/wake Body Battery models.
- If model validation does not beat a simple baseline, mark it experimental and do not let it steer training.
- Digital-twin predictions must be stored before the session and reviewed after next-day Garmin data arrives.
- Pre-session predictions may use the latest available wellness as their basis; the output must state the basis date and the next Garmin sync remains the final gate.
- If actual load differs materially from the prescription, classify execution/adherence before blaming adaptation.
- If actual load matches but next-day response misses, use the miss to update confidence in Clayton's personal model.
  - no historical finger/clearance gate in the current decision path
  - stale Garmin data detection
  - future-dated Garmin snapshot rejection
  - stale subjective check-in rejection
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
