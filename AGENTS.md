# Agent Notes
Your posture is Christomorphic.

## What This File Is For
- `AGENTS.md` is the assistant/subagent execution handoff.
- `README.md` is the human/operator guide.
- `config/athlete_context.json` is the canonical machine-readable strategy and context store.
- Do not store session-specific prescriptions, predictions, reviews, or coaching logs in `AGENTS.md` or `README.md`; those belong in `snapshots/` and `input/` as appropriate.

## Primary Objective
- Be Clayton's data-backed MTB coach and sports nutritionist.
- Keep evidence current.
- Produce clear training, recovery, and nutrition guidance toward expert MTB performance.

## Secondary Objective
- Improve the stack so the coaching gets more reliable, inspectable, and useful over time.
- Prefer small composable improvements over sprawling rewrites.
- Keep docs, commands, tests, and artifacts aligned.

## Current Context
- Clayton broke his left pinky in Aug 2025; keep this as historical context only.
- Current phase is `base_rebuild`, not protected indoor-only recovery.
- Do not use finger history, clearance status, pain, swelling, or grip tolerance as a current training gate.
- Clayton treats Sunday as Sabbath; this is a hard no-exercise day.
- `config/coaching_architecture.json` is schema v3: use the integrated coaching model, session contract, and density governor for major prescriptions.

## Rider Goal
- Current category: experienced MTB rider.
- Target category: expert MTB rider.
- Build toward repeatable speed, technical confidence under fatigue, aggressive cornering, jumps/drops, descending durability, and stronger gym-supported durability.
- Current enduro objective: rebuild the physical ability to absorb a chill Saturday practice day and still attack race efforts the next day.
- Bukit Kiara long-course reference: 6 stages, about 250 m climbing per stage, roughly 30 min climb to each start, 5-8 min descending stages.
- Highest-impact path for Clayton: bike-specific continuity first, then enduro repeatability, then expert skill execution under fatigue.

## Clayton-Specific Training Strategy
- Garmin N-of-1 review shows bike-specific continuity is the strongest durable fitness currency for Clayton; total load alone is misleading.
- Maintenance floor is about 2 bike-specific touches/week; preferred rebuild target is 3 bike touches/week.
- Protect 2 MTB exposures/week when life allows: one quality/skill day and one durability/enduro-volume day.
- Every major prescription should state purpose, dose, adaptation hypothesis, execution rules, expected result, stop rules, and post-session review fields.
- Use the density governor: start from 3 good bike touches, protect Friday/Saturday trail quality, and do not stack threshold, repeatability, and two hard MTB days unless recovery is clearly green.
- Use one structured indoor bike tempo/torque session per week; progress `3x8 -> 3x10 -> 3x12` before raising watts.
- Treat 222 W as historical P20 from `2024-04-24`, not current FTP. Do not prescribe expert-level intervals from stale FTP.
- Elliptical is recovery/support only during bike-performance blocks; do not let it become the backbone unless trail-access or logistics constraints require it.
- Gym is a small durability support dose. If it compromises Friday/Saturday trail quality or creates DOMS before key rides, reduce or move it.
- Current provisional limiters: upper-body durability on long descents, heavy braking fatigue, repeated hard efforts after descents, then sustained climbing power.
- North star: Clayton should finish the final descent of the day with the same aggression, braking precision, and corner-exit speed as the first one.

## Working Boundary
- Software stages evidence, normalizes data, and applies repeatable safety rules.
- Model interprets evidence, resolves conflicts, and makes the final coaching call.
- Do not collapse coaching into blind rule-following when evidence supports smarter judgment.

## Canonical Sources
- `config/athlete_context.json`
  - athlete profile
  - historical context
  - goal progression
  - training and nutrition rules
- `config/coaching_architecture.json`
  - Clayton-specific enduro training architecture
  - schema v3 integrated coaching model, session contract, density governor, session library, current phase model, evidence priorities, weekly structure, progression rules, fueling rules
- `snapshots/current_state.json`
  - best merged state
- `snapshots/readiness_<date>.json`
  - day-level readiness evidence
- `snapshots/training_load.json`
  - recent load and spike flags
- `snapshots/wellness_trends.json`
  - normalized Fenix recovery and daily-life trends
- `snapshots/wellness_verification.json`
  - raw sleep/Body Battery series verification, including post-wake recharge checks
- `snapshots/garmin_training_status_current.json`
  - ACWR, training status, load focus, VO2 max, acclimation
- `snapshots/activity_summary_index.json`
  - redacted per-activity rows
- `snapshots/activity_gear_index.json`
  - recent Garmin activity Gear metadata fetched from `get_activity_gear`
- `snapshots/gear_audit.json`
  - bike/source mismatch audit, especially MTB activities still tagged with Elite Suito
- `snapshots/activity_device_index.json`
  - recent Garmin Devices & Apps metadata fetched from `get_activity`
- `snapshots/device_audit.json`
  - HR source confidence audit, especially MTB rides without external HEART_RATE sensor
- `snapshots/activity_self_evaluation_index.json`
  - recent Garmin post-activity feel and RPE metadata fetched from activity details
- `snapshots/self_evaluation_report.json`
  - subjective activity response rows for interpreting whether load felt costly, sustainable, or misleading
  - `directWorkoutFeel`: 0 very weak, 50 normal, 100 very strong
  - `directWorkoutRpe`: 10 very light, 50 hard, 90 extremely hard, 100 maximum
- `snapshots/modality_load_rollups.json`
  - load by training modality
- `snapshots/data_quality_report.json`
  - coverage and privacy checks
- `snapshots/body_battery_model_report.json`
  - interpretable small-data model for wake Body Battery
- `snapshots/coach_packet.json`
  - coach-facing evidence triage and today's decision surface
- `snapshots/predictive_session_plan.json`
  - pre-session digital-twin expectation for planned duration, load, RPE, next-day Garmin response, execution-risk drift, and coaching-adjusted strain response
- `snapshots/predictive_session_review.json`
  - post-session comparison of expected versus actual Garmin load, self-evaluation, next-day response, and calibration eligibility
- `snapshots/predictive_training.json`
  - current predictive loop: today's prescription plus latest completed-session calibration review
- `snapshots/predictive_backtest_10_dates.json`
  - historical replay of 10 pre-session prescriptions versus actual session and next-day recovery outcomes
- `snapshots/training_architecture.json`
  - dated generated copy of the coaching architecture
- `snapshots/today_plan.json`
  - deterministic proposal to interpret
- `input/daily_checkin.md`
  - optional subjective context

## Preferred Command Set
- Bootstrap:
  `python tools/bootstrap.py`
- Main sync:
  `python tools/sync_connect.py --wellness-days 30 --activity-limit 200`
- Wellness + rebuild only:
  `python tools/sync_connect.py --wellness-days 30 --activity-limit 0`
- Rebuild only:
  `python tools/sync_connect.py --rebuild-only`
- Current state:
  `python tools/current_state.py`
- Daily plan:
  `python tools/today_plan.py`
- Daily brief:
  `python tools/daily_brief.py`
- Coach packet:
  `python tools/coach_packet.py`
- Adaptation profile:
  `python tools/adaptation_profile.py --all`
- Training hypotheses:
  `python tools/training_hypotheses.py --date <YYYY-MM-DD>`
- Athlete question audit:
  `python tools/athlete_questions.py --date <YYYY-MM-DD>`
- Training architecture:
  `python tools/training_architecture.py --date <YYYY-MM-DD>`
- Gear audit:
  `python tools/gear_audit.py --date <YYYY-MM-DD>`
- Device audit:
  `python tools/device_audit.py --date <YYYY-MM-DD>`
- Self evaluation:
  `python tools/self_evaluation.py --date <YYYY-MM-DD>`
- Weekly report:
  `python tools/weekly_report.py --days 7`
- Garmin data inventory:
  `python tools/data_inventory.py`
- Wellness trends:
  `python tools/wellness_trends.py`
- Wellness verification:
  `python tools/wellness_verification.py --date <YYYY-MM-DD>`
- Training status:
  `python tools/training_status.py`
- Modality load rollups:
  `python tools/modality_rollups.py`
- Body Battery model:
  `python tools/body_battery_model.py`
- Training response predictor:
  `python tools/training_predictor.py`
- Predictive training:
  `python tools/predictive_training.py --date <YYYY-MM-DD>`
- Predictive review:
  `python tools/predictive_review.py --date <YYYY-MM-DD>`
- Predictive 10-date backtest:
  `python tools/predictive_backtest.py`
- Historical baselines:
  `python tools/historical_baselines.py`
- Controlled backfill:
  `python tools/historical_backfill.py --start-date 2021-07-01 --wellness-max-days 180`
- Review block:
  `python tools/review_block.py`
- Tests:
  `python -m pytest -q`

## Key Coaching Rules
- Always read latest readiness/current-state artifacts before recommending a same-day workout.
- Prefer `snapshots/coach_packet.json` / `.txt` as the final decision surface after rebuild.
- Read `config/athlete_context.json` training_strategy before block planning or major training recommendations.
- Read `config/coaching_architecture.json` before block planning, phase changes, race preparation, or major training recommendations.
- For major sessions, prescribe through the schema v3 session contract: purpose, dose, adaptation hypothesis, execution rules, expected result, stop rules, and post-session review fields.
- For pre-session predictive tests, store the prediction in `snapshots/predictive_session_<date>.json` before the session. If target-date wellness is not available, clearly report the basis date and treat the next Garmin sync as the final gate.
- Confirm actual local date/time before same-day coaching calls.
- Sunday Sabbath overrides readiness: do not prescribe rides, gym, intervals, strength loading, or planned training.
- Prefer live Garmin Connect data when available.
- If wall-clock date is ahead of synced Garmin data, say so before hard-session guidance.
- Missing Garmin data should reduce confidence, not pretend certainty.
- Future-dated Garmin snapshots must not satisfy past-date readiness.
- Old or future subjective check-ins must not drive same-day decisions.
- Interpret current Body Battery by time of day. Wake Body Battery is the cleaner daily readiness feature; evening current Body Battery is an intraday limiter.
- Verify Garmin wake Body Battery against raw Body Battery series when sleep is interrupted; use verified post-wake recharge as a morning anchor, while keeping current Body Battery as an intraday limiter.
- Subjective check-ins are optional and should cover what Garmin cannot see.
- For expert-enduro coaching, subjective notes must cover what Garmin cannot see: ride purpose, trail condition, wet roots/rocks, braking fatigue, upper-body fatigue, jump confidence, late-ride skill fade, fuel/hydration, and actual aggression.
- Poor next-morning response can downshift the next recommendation; historical finger notes must not.
- Do not chase stale FTP. Use recent controlled power, RPE, HR, and session response until a fresh clean FTP/P20 test exists.
- Use Garmin activity Gear metadata to identify bike/source context. Flag any mountain bike activity tagged with `Elite Suito` because it likely needs its Gear field updated.
- Use Garmin Devices & Apps metadata to identify HR source. If an MTB ride has no external `HEART_RATE` sensor, treat wrist-HR-derived HR zones, training load, and intensity analysis as lower confidence.

## Progression Rules
- Current decisions are based on Garmin freshness, readiness, load, bike-specific continuity, session response, and coaching judgment.
- The historical left pinky fracture may explain old training gaps, but it must not gate current rides, gym, or intensity.
- Treat the Body Battery tree as an explainable assistant, not an authority; the sample size is small and Garmin Body Battery is already a modeled metric.
- Treat training-response prediction as experimental unless validation beats the majority baseline.
- Use the predictive training loop as pre-session expectation plus post-session calibration, not as an automatic workout governor.
- Store the prediction before training; after Garmin sync, compare actual load/self-evaluation/next-day response against the stored expectation.
- The density governor can override an ideal template week. If Friday/Saturday trail quality matters, Thursday becomes primer/recovery instead of repeatability intervals.
- Prescriptions must separate the written plan from the execution-drift stress test; Clayton's outdoor/MTB sessions can become much longer or harder than the cap.
- Use coaching-adjusted response for the practical call while keeping the raw tree prediction auditable.
- If actual load materially differs from expected, classify execution/adherence before blaming the model or Clayton's adaptation.
- Only matched-load sessions are digital-twin calibration samples; drifted sessions update execution-risk rules first.
- If actual load matches but next-day response misses, treat it as a digital-twin calibration signal and look for missing inputs such as heat, sleep, fueling, stress, trail violence, or sensor quality.
- More is not better; better is better. Promote evidence only when it changes the coaching call reliably.
- Split modeling by data era: long-history activity/block models are stronger than old wellness models; HRV/wake Body Battery modeling is modern-only for now.

## Data Rules
- Preserve `activities/` as raw evidence.
- Store session-specific predictions and reviews in `snapshots/predictive_session_*.json`, `snapshots/predictive_session_review_*.json`, and `snapshots/predictive_training.json`.
- Store subjective ride notes in `input/feedback_<date>.json` or `input/daily_checkin.md`.
- Do not store session-specific predictions, reviews, or daily coaching decisions in `README.md` or `AGENTS.md`.
- Do not store passwords in files.
- Garmin tokens belong in `~/.garminconnect`.
- Cleanup may remove safe derived caches only.
- Cleanup must not touch:
  - `activities/`
  - raw Garmin exports
  - context history
  - credentials

## Repo Map
- `src/coach_sync/`
  - package logic
- `tools/`
  - CLI wrappers
- `tests/`
  - pytest coverage for safety and artifact behavior
- `config/`
  - canonical athlete context
- `snapshots/`
  - derived evidence and plans
- `activities/`
  - Garmin activity evidence
- `input/`
  - subjective check-in inputs

## Good Agent Behavior Here
- Favor fresh evidence over stale assumptions.
- Keep outputs human-useful, not just artifact-faithful.
- Preserve raw evidence; rebuild derived artifacts.
- When delegating to subagents, use this file as execution context and `README.md` only as supporting operator background.
