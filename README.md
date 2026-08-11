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
- Technical progression also depends on CNS readiness: brain fog, decision speed, HRV/stress/RHR context, and weak-feel/low-RPE mismatches can cap trail consequence even when load is low.
- Every planned source is constrained before execution: explicit coach-authored sessions override weekly intent, but Sabbath, red readiness, stale evidence, Garmin downshifts, and CNS ceilings can still replace the session.
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
- Same-day coaching fast path:
  `python tools/sync_connect.py --wellness-days 3 --activity-limit 5 --decision-only`

## Garmin Sync
- Set credentials as environment variables:
  - `GARMIN_EMAIL`
  - `GARMIN_PASSWORD`
- Main sync:
  `python tools/sync_connect.py --wellness-days 30 --activity-limit 200`
- Full sync preserves rich Garmin detail plus the original FIT/ZIP for up to three recent key MTB, indoor-bike, or gym sessions by default. Set the bound explicitly with `--key-detail-limit <N>`; the decision-only path skips this heavier collection.
- Same-day decision sync:
  `python tools/sync_connect.py --wellness-days 3 --activity-limit 5 --decision-only`
- Sync creates a current-week plan when one is missing; daily planning then uses that matching weekly intent unless `input/planned_session_YYYY-MM-DD.json` provides an explicit coach adjustment.
- When dated coach adjustments are mutually exclusive fallback dates, give each session the same `bike_touch_counting: {"mode": "mutually_exclusive", "group": "<stable-group>"}` block so the weekly plan shows both candidates but counts at most one bike touch.
- Wellness + rebuild only:
  `python tools/sync_connect.py --wellness-days 30 --activity-limit 0`
- Rebuild only:
  `python tools/sync_connect.py --rebuild-only`
- Quick rebuild without live Garmin fetch:
  `python tools/sync_connect.py --rebuild-only --decision-only`

Garmin tokens are handled by the `garminconnect`/`garth` stack. Do not store passwords in repo files.

## Command Surface
Preferred direct commands:
- Current state: `python tools/current_state.py`
- Current state fast path: `python tools/current_state.py --decision-only`
- Daily plan: `python tools/today_plan.py`
- Daily brief: `python tools/daily_brief.py`
- Weekly plan: `python tools/weekly_plan.py`
- Weekly report: `python tools/weekly_report.py --days 7`
- Insight memo: `python tools/insight_memo.py --days 28`
- Review block: `python tools/review_block.py`
- Forecast: `python tools/microcycle_forecast.py --days 24`
- Garmin data inventory: `python tools/data_inventory.py`
- Garmin surface manifest: `python tools/garmin_surface_manifest.py`
- Wellness trends: `python tools/wellness_trends.py`
- Wellness verification: `python tools/wellness_verification.py --date <YYYY-MM-DD>`
- Wear-state coverage: `python tools/wearable_coverage.py --date <YYYY-MM-DD>`
- Training status: `python tools/training_status.py`
- Garmin Training Readiness: `python tools/training_readiness.py --date <YYYY-MM-DD>`
- Activity profile: `python tools/activity_profile.py`
- N-of-1 adaptation profile: `python tools/adaptation_profile.py --all`
- Training hypothesis tests: `python tools/training_hypotheses.py --date <YYYY-MM-DD>`
- Athlete profile question audit: `python tools/athlete_questions.py --date <YYYY-MM-DD>`
- Clayton-specific training architecture: `python tools/training_architecture.py --date <YYYY-MM-DD>`
- Gear audit: `python tools/gear_audit.py --date <YYYY-MM-DD>`
- Device audit: `python tools/device_audit.py --date <YYYY-MM-DD>`
- Self evaluation: `python tools/self_evaluation.py --date <YYYY-MM-DD>`
- CNS readiness: `python tools/cns_readiness.py --date <YYYY-MM-DD>`
- Redacted activity index: `python tools/activity_index.py`
- Manual lap/loop load and within-lap action analysis:
  `python tools/loop_load.py --activity-id <GARMIN_ID> --date <YYYY-MM-DD> --loops 1,2 3,4`
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
- `python -m coach_sync garmin-surface-manifest --date <YYYY-MM-DD>`
- `python -m coach_sync training-readiness --date <YYYY-MM-DD>`
- `python -m coach_sync gear-audit --date <YYYY-MM-DD>`
- `python -m coach_sync device-audit --date <YYYY-MM-DD>`
- `python -m coach_sync self-evaluation --date <YYYY-MM-DD>`
- `python -m coach_sync predictive-training --date <YYYY-MM-DD>`
- `python -m coach_sync predictive-review --date <YYYY-MM-DD>`
- `python -m coach_sync predictive-backtest`

## Predictive Workflow
- Use this before a planned key session to store a testable expectation in `snapshots/`, not in this README.
- If `input/planned_session_YYYY-MM-DD.json` exists, the predictive loop uses that coach-authored session contract before the matching weekly intent or generic `today_plan`.
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
- A prediction records separate state-basis, action, and next-day response dates. If target-date Garmin wellness does not exist yet, it states the prior wellness basis while still simulating the action on its planned date; the next Garmin sync remains the final gate.
- After the session and next-day Garmin sync, run:
  `python tools/predictive_review.py --date <YYYY-MM-DD>`
- A full calibration requires more than a matched Garmin load: modality, session count, and duration must match; the schema v3 review fields must be complete; stop-rule outcome must be explicit; and MTB sessions must document technical quality and late-session skill fade. The review keeps a physiology-only match separate from a full calibration sample.
- If a stored session explicitly has `optional: true`, performing no session is `allowed_optional_skip`, not adherence drift; because no action-response pair exists, it remains non-calibratable. Legacy reviews may recover an omitted optional flag only from an exact same-date match to the recorded `input_planned_session` source, without mutating the stored prediction.
- Record the manual evidence either at the top level of `input/feedback_YYYY-MM-DD.json` or in a matching `entries[]` item. A minimal MTB example is:

```json
{
  "date": "YYYY-MM-DD",
  "session_contract_review": {
    "stop_rule_outcome": "not_triggered",
    "technical_quality_notes": "Braking and line choice stayed deliberate through the final quality descent.",
    "late_session_skill_fade": "none",
    "fueling_carbs_g_per_hour": 45,
    "fluid_ml_per_hour": 650,
    "sodium_mg_per_hour": 600
  }
}
```

- Use `triggered_and_stopped`, `triggered_and_downshifted`, or `triggered_but_continued` when a stop rule fires. The last value is an unsafe/uncalibratable session, not a successful completion.
- For an athlete-confirmed nap or quiet rest window, record exact local timing and the subjective response. Garmin `napTimeSeconds=0` or a missing nap label does not disprove the report:

```json
{
  "date": "YYYY-MM-DD",
  "sleep_work_timing_review": {
    "nap_status": "completed",
    "nap_source": "athlete_report",
    "nap_start_local": "10:20",
    "nap_end_local": "11:50",
    "time_in_bed_minutes": 90,
    "estimated_sleep_minutes": 75,
    "sleep_inertia_minutes": 10,
    "post_nap_clarity_10_range": [7, 8],
    "illness_status": "none"
  }
}
```

- For a completed intentional Fenix-off interval, report exact target-date timing. A usual Sunday dress-watch habit is context only and does not create a dated interval:

```json
{
  "date": "YYYY-MM-DD",
  "wearable_context": {
    "off_wrist_windows": [
      {
        "status": "confirmed",
        "start_local": "09:00",
        "end_local": "12:30",
        "reason_category": "church_dress_watch",
        "replacement_device_category": "dress_watch"
      }
    ]
  }
}
```

The wear-state layer never fills missing samples or interprets them as rest. It keeps endpoint health, optical-HR measurement availability, physical wear state, and cause attribution separate. Direct `get_heart_rates` sample transitions can confirm that wrist-HR measurement was unavailable, but cannot prove the watch was physically removed or distinguish dress-watch use, a loose strap, showering, charging, or another cause. Exact dated athlete reports may assign physical removal and cause only to their matching interval. The layer cannot raise readiness, CNS status, technical consequence, or the written session. Positive low-stress use additionally requires dense valid target-date samples from near midnight through a declared cutoff at or after 18:00, with credible Garmin cadence and no uncovered tail. A recurring habit remains context only and does not soften an unexplained gap.

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
- `snapshots/cns_readiness.json` / `snapshots/cns_readiness_YYYY-MM-DD.json`
  - CNS and technical-consequence readiness, including brain fog, HRV/stress/RHR/Body Battery, self-evaluation, recent MTB cost, and session ceiling
- `snapshots/training_load.json`
  - recent load, prior-week comparison, load spike flags
- `snapshots/wellness_daily.json`
  - normalized daily sleep, HRV, Body Battery, stress, separately sourced daily/sleep Pulse Ox, respiration coverage, monitoring altitude, steps, calories, and intensity minutes; valid and negative-sentinel counts keep unavailable activity-time physiology explicit
- `snapshots/wellness_trends.json`
  - 7-day and prior-7-day recovery trends
- `snapshots/wellness_verification.json` / `snapshots/wellness_verification_YYYY-MM-DD.json`
  - checks Garmin sleep and Body Battery summary against the raw Body Battery series, including post-wake recharge after interrupted sleep
- `snapshots/rest_recharge_window.json` / `snapshots/rest_recharge_window_YYYY-MM-DD.json`
  - joins an athlete-confirmed nap/rest window to Garmin all-day stress and Body Battery series, recharge latency, primary-sleep shortfall proxy, illness context, preceding 48-hour load, sleep inertia, and clarity; classifies the window as restorative, quiet-but-non-restorative, discordant, or insufficient evidence
- `snapshots/wearable_coverage.json` / `snapshots/wearable_coverage_YYYY-MM-DD.json`
  - separates endpoint health, stress/Body Battery availability, optical-HR measurement availability, physical wear state, and cause attribution; blocks optimistic low-stress use without imputing physiology
- `snapshots/garmin_training_status_current.json`
  - training status, ACWR, load focus, VO2 max, and acclimation
- `snapshots/garmin_training_readiness_current.json`
  - separate Garmin Training Readiness feed with endpoint, date, freshness, and device-capability provenance; context only, never a replacement for physical or CNS readiness gates
- `snapshots/garmin_surface_manifest.json`
  - endpoint-by-endpoint collection state, raw/normalized field coverage, contiguous data eras and gaps, units, lineage, privacy verification, and downstream coaching use
- `snapshots/last_live_sync_status.json` / `snapshots/sync_run_ledger.json`
  - durable last live-contact result plus bounded rebuild/live history; rebuilds do not erase the last live-sync outcome
- `snapshots/activity_summary_index.json`
  - redacted activity rows without names or source paths
- `snapshots/activity_loop_load_current.json` / `snapshots/activity_loop_load_YYYY-MM-DD_<activity_id>.json`
  - manual lap/loop analysis for MTB files, including official-load redistribution, moving/stopped timeline, long-rest detection, boundary-HR carryover flags, and within-lap action-terrain categories such as punchy climb pedaling, flat pedaling, downhill pedaling, downhill coasting, and stopped/resting
- `snapshots/activity_gear_index.json`
  - recent Garmin activity Gear metadata fetched from `get_activity_gear`
- `snapshots/gear_audit.json` / `snapshots/gear_audit.txt`
  - bike/source mismatch audit, including coverage status so a fast partial index is never presented as clear
- `snapshots/activity_device_index.json`
  - recent Garmin Devices & Apps metadata fetched from `get_activity`
- `snapshots/device_audit.json` / `snapshots/device_audit.txt`
  - HR source confidence audit, including MTB rides without an external HEART_RATE sensor and partial-index coverage
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
- `snapshots/current_state.json:latest_session_evidence`
  - bounded latest-session block joining raw timing, terrain, load, power, environment, Gear, HR source, self-evaluation, Garmin gym set/rep/rest detail, and matching/recent loop evidence with explicit provenance and confidence
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
  - post-session comparison of expected versus actual Garmin load, action alignment, self-evaluation, technical/stop-rule evidence, next-day response, and calibration eligibility
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
- `snapshots/weekly_plan.json` / `snapshots/weekly_plan.txt`
  - Monday weekly intent layer: objective, target load range, MTB exposure cap, daily gates, and schema v3 session contracts; the matching session becomes the daily template before same-day constraints are applied
- `config/coaching_architecture.json`
  - schema v3 machine-readable coaching objective, integrated coaching model, session contract, density governor, session library, evidence priorities, and phase plan
- `snapshots/today_plan.json`
  - deterministic training and nutrition proposal
- `snapshots/daily_brief.json` / `snapshots/daily_brief.txt`
  - compact human handoff
- `activities/`
  - immutable Garmin activity evidence
  - `activities/details/` retains bounded rich API detail and `activities/fit/` retains original Garmin FIT/ZIP downloads; neither is treated as another longitudinal activity row or removed by derived-cache cleanup

## Architecture Boundary
- Code answers: what data exists, what changed, what is stale, what rules are triggered.
- Coach answers: what Clayton should actually do today and why.
- CNS readiness answers: whether technical consequence, speed, jumps, enduro simulation, novelty, and setup testing should be capped even if physiological load looks manageable.
- Constraint resolution answers: whether a weekly or explicit session must be replaced because Sabbath, physical readiness, data freshness, Garmin arbitration, or CNS readiness sets a lower ceiling.
- Predictive loop answers: what response was expected from the prescribed session, what actually happened, and whether the miss was execution, external stress, or model error.
- Surface manifest answers: what Garmin endpoint was configured, attempted, empty, failed, unsupported, or not attempted; what fields survived normalization; and which coaching surface consumes them.
- Latest-session evidence answers: what the most recent load physically represented, while keeping weather units, HR source, Garmin water estimates, loop estimates, steep-hike moving-time plausibility, and old power values inside their stated confidence boundaries.
- Rest/recharge evidence answers whether a reported intraday rest window worked. It never rewrites the wake anchor, treats a missing Garmin nap label as proof of no nap, or raises physical, CNS, technical, or written-session ceilings.
- Weekly planner answers: what the week is trying to buy before daily readiness gates adjust execution.
- Session contract answers: what adaptation the session is buying, what the dose is, when to stop, and what must be reviewed afterward.
- Density governor answers: whether the ideal week should be downshifted so Friday/Saturday trail quality is protected.
- Lap/loop analysis answers: what happened inside manual MTB laps, separating inherited HR, rest time, moving work, pedaling/coasting state, and terrain context before coaching from lap max HR or average load.
- Predictive prescriptions now carry two branches: the written plan and the execution-drift stress test when similar Clayton sessions historically became longer or harder.
- The stack should warn on stale or missing Garmin data before confident hard-session guidance.
- Scheduled Sabbath rest overrides workout selection; the plan should become rest, not a training option.
- Wake Body Battery and current Body Battery are separate signals; current Body Battery is time-of-day sensitive.
- Interrupted sleep can make Garmin's wake Body Battery incomplete; the verifier checks the raw series before readiness scoring trusts the wake value.
- Physical readiness and CNS readiness are separate. Body Battery can rebound while HRV, brain fog, weak feel, decision speed, or weighted 48-hour MTB neural cost still block high-consequence MTB work.
- Read `config/coaching_architecture.json` before block planning, phase changes, race preparation, or major training recommendations.

## Clayton-Specific Coaching Rules
- Every major prescription should follow the schema v3 session contract: purpose, dose, adaptation hypothesis, execution rules, expected result, stop rules, and post-session review fields.
- Bike-specific continuity is the highest-yield lever. Maintenance floor is about 2 bike touches/week; the active rebuild target is 5-6 unique bike days when recovery and calendar allow.
- Keep only 2-3 sessions meaningfully costly. The remaining 2-3 bike touches are low-cost Z1/Z2 or primers; a hard run counts toward the cost cap.
- Protect 2 MTB exposures/week when possible: one quality/skill day and one durability/enduro-volume day.
- Allow up to 3 MTB exposures/week when readiness, logistics, and load density support it; more than 3 is an event/race block, not a default build week.
- Use the density governor: build frequency through low-cost touches, protect 2 key MTB exposures, and do not stack threshold, repeatability, and two hard MTB days unless recovery is clearly green.
- Use CNS readiness as the technical-quality governor: `impaired` or `compromised` replaces technical, structured, or high-consequence work with low-consequence recovery rather than merely adding a caution label.
- Use one structured indoor tempo/torque session per week and progress `3x8 -> 3x10 -> 3x12` before raising watts.
- Current Garmin operational cycling FTP is `211 W` from `2026-07-25`, backed by the activity `maxFtp` surface, ANT+ bike power, athlete-confirmed auto-detection, and prior preserved FIT update evidence. Use it with RPE/HR validation; `222 W` remains historical P20, not FTP.
- Elliptical is recovery/support during bike-performance blocks, not the backbone unless constraints require it.
- Gym is useful only if it supports trail quality; reduce or move it if it creates DOMS before key rides.
- In Kuala Lumpur heat, fuel skill quality early: late sloppy braking, weak pumping, timid jumps, or poor line choice can be under-fuelling or heat load.
- For MTB sessions of 90-150 minutes, the canonical range is 45-75 g carbohydrate, 500-900 ml fluid, and 600-1000 mg sodium per hour. For rides over 150 minutes or explicit race practice, use 60-90 g, 650-1000 ml, and 800-1200 mg per hour. Select within the range from same-day conditions, sweat response, consequence, and gut tolerance; prior-session weather and Garmin heat acclimation never justify reducing the target by themselves.
- Garmin Gear is the bike/source truth layer. Flag MTB activities tagged with `Elite Suito` so the Gear field can be corrected.
- Garmin Devices & Apps is the HR-source truth layer. MTB activities without an external `HEART_RATE` sensor should have lower-confidence HR/load interpretation because Fenix wrist HR can under-read during technical riding. Partial Gear/device coverage remains partial evidence.
- Daily/sleep Pulse Ox and respiration are contextual wellness evidence, not activity traces. Never infer exercise oxygenation from a sleep average, treat a negative activity-period sentinel as a normal reading, or use wrist Pulse Ox as a medical diagnosis or training-clearance signal.
- Garmin `movingDuration` can undercount slow vertical hiking. When timer and elapsed duration agree but reported moving time is implausibly small, preserve the Garmin value as raw, flag it as unreliable, and do not manufacture stopped time from subtraction.
- For MTB manual-lap interpretation, do not coach from max HR alone. Check the lap timeline for `max_hr_likely_boundary_carryover`, long stops, `first_moving_after_longest_stop`, and `action_terrain_summary` before deciding whether a lap was a hard effort, recovery, technical descent, or enduro-specific standing/punchy pedaling work.

## Data Boundaries
- Treat raw Garmin exports and `activities/` as preserved evidence.
- Preserve `activities/details/` and `activities/fit/` as private raw evidence. They are intentionally excluded from summary activity counts and ordinary cleanup.
- Store session-specific predictions and reviews in `snapshots/predictive_session_*.json`, `snapshots/predictive_session_review_*.json`, and `snapshots/predictive_training.json`.
- Do not store session-specific predictions, reviews, or daily coaching decisions in `README.md` or `AGENTS.md`.
- Use the private GitHub repository as the durable backup for code, tests, documentation, coaching architecture, `config/athlete_context.json`, and curated `input/feedback_*.json` notes.
- Do not use normal GitHub commits as the backup for raw Garmin downloads, generated snapshots, archives, credentials, or transient `input/daily_checkin.md`; those remain ignored and should be redownloaded, rebuilt, or backed up separately if needed.
- Cleanup only removes safe derived caches such as `__pycache__` and `.pytest_cache`.
- Do not commit credentials, Garmin tokens, or private exports to a public location.
- `.gitignore` excludes common Garmin token-store directories and token filenames in addition to environment files.

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
  - CNS readiness parser/scoring and coach-packet integration
  - predictive training prescription and post-session calibration review
  - weekly-intent daily handoff, explicit-plan precedence, and CNS/Garmin constraint enforcement
  - causal action/state prediction dates and weekly MTB/tempo modality classification
  - failed Garmin payload freshness rejection, BOM-tolerant JSON reads, and partial Gear/device audit coverage
  - manual lap/loop load scaling, boundary-HR carryover detection, and within-lap action-terrain categorization
  - Clayton-specific training architecture generation, including schema v3 integrated coaching model, session contract, density governor, and Dinding setup session type
  - Garmin Gear audit and Elite Suito MTB mismatch flagging
  - Garmin Devices & Apps audit and wrist-HR MTB confidence flagging

## Modeling Rules
- ML outputs are coaching evidence, not instructions.
- Use the coaching-adjusted response for the practical prescription call; keep the raw tree output visible for audit.
- Only complete contract-quality sessions calibrate the digital twin: matched load, action alignment, explicit stop-rule outcome, complete relevant review fields, clean technical outcome where applicable, and next-day Garmin response. Physiology-only matches remain useful evidence but do not update calibration confidence.
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
