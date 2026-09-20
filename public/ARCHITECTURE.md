# Persistent coaching as an accountable record system

## What persists

Continuity is reconstructed from durable context, dated decisions, actual delivery, and reviewed response. It is not an assertion of uninterrupted consciousness, access to every previous conversation, or changes to model weights. A new conversation must recover the relevant state and acknowledge gaps.

The public project shares the method. Private records retain athlete-specific evidence. The repository is not the live athlete database and should not expose enough personal detail to reconstruct one.

## Ownership by meaning, not one universal source ranking

| Source | Owns | Does not establish by itself |
| --- | --- | --- |
| Current explicit athlete instruction | Goals, preferences, corrections, authorized actions | Physiology or completed actions not reported |
| Garmin | Its recorded measurements and activity metadata | Every aspect of technical quality, local muscular cost, or medical status |
| Core Memory | Durable context and refined coaching principles | A new same-day prescription |
| Performance Database | Selected evidence, identity/coverage, policy arithmetic | A replacement for a clinician, observation, or missing recovery report |
| Decisions and Reviews | Dated contracts, delivery interpretation, response and amendments | Permission to backdate later evidence |
| Original timing, footage, equipment and venue evidence | The particular fact actually observed | Unobserved behavior outside its scope |
| Repository | Reusable methods, tests, historical specialist tools | A competing writable coaching master |

The September 2026 operating model uses the three private cloud records as one routine master following an explicit verified cutover. Old local ownership statements remain historical. A tool-access failure is an access failure, not an implicit migration back.

## The decision loop

Read current intent and the last relevant review. Identify the small number of facts that would change the decision. Retrieve them from the proper sources, with dates, units, coverage, and quality. Reconcile athlete observations rather than replacing them with a dashboard label.

Author a complete, versioned session contract. Communicate the same dose and conditions that are saved. Store the decision's creation time separately from its evidence cutoff. Mark future concepts as candidates, not executable sessions. Calendar placement is distinct from clearance.

After execution, review what was actually done. Match activity IDs and local dates; group multiple files from one outing without losing total exposures. Keep dose adherence, stop-rule outcome, technical performance, local fatigue, and next-day response separate. Preserve unknowns.

Only a later decision can use later-known evidence. Keep the established progression separate from the proposed next step. A suitable one-variable test needs both a useful positive outcome and an acceptable matched response; it also needs current function, calendar, venue, and workload review. A good record check does not perform those assessments.

Persist only what will change future decisions: durable context in Core, selected quantitative findings and identities in the database, and substantial decisions/reviews in the journal. Ordinary conversation need not become another formal report.

## Implementation invariants

**Identity and time.** A recovery report must identify the athlete and session and the exact next local-calendar date. Store observed time and known time. Use aware timestamps; do not confuse local dates with UTC boundaries. Future-known evidence is unavailable to an earlier decision.

**Contract equality.** Include optional work, power units and tolerance, rests, repeat counts, stops, and review conditions. An accepted API call is only the first check. Read back stored semantics. Device receipt and actual controller behavior remain separate tests.

**No silent promotion.** Missing fields, an unreviewed activity, a candidate plan, a dose mismatch, an unresolved stop boundary, or unmatched recovery cannot silently become proof of an earned progression. This does not erase useful learning from a session with a mixed outcome.

**No duplicated authority.** Ingestion is idempotent by source identity. Formula-owned columns are not writable coaching opinions. Verify both material inputs and downstream arithmetic. Derived artifacts remain derived; a broad pipeline run is optional unless it improves a named decision.

**One meaningful change.** Test a selected hypothesis on comparable terrain or with a standardized exercise setup. Keep other relevant variables stable where practical. Faster timing without comparable markers is not a demonstrated gain; a gym improvement is not automatically riding transfer. These are interpretation rules, not a causal guarantee.

**Graceful limits.** An inaccessible source stays inaccessible. A title is not a watched video; a saved exercise name is not proof of form or equipment; historical power is not a current test. Give the best supported answer without manufacturing coverage.

## What the reference code does and does not do

`continuity.py` checks required contract structure, equality of structured communicated/saved contracts, aware evidence timestamps, matching athlete/session/version/date, affirmative review fields, exact next-day matching, and a single named proposed lever. The caller still supplies honest source-qualified records and actual local dates.

It deliberately does not implement a numerical readiness score, medical gate, training prescription, cloud adapter, scheduler, secret store, or automatic publication. Nor does it interpret arbitrary chat text into a verified contract. Legacy CLI behavior has not changed just because this reference exists.

## Evidence and history boundary

This revision was reconstructed from the repository's May–June 2026 commit history, the earlier README/agent handoff, selected local configuration/context code and feedback (including a retrospective record about 2024 racing), and current private Core/Reviews through the latest September 2026 amendment. Selected recent Garmin activity records were checked live. This is not a claim that every year of activity files or every repository blob was reanalyzed.

The public lessons are qualitative, de-identified reflections. Private supporting records are intentionally not linked or reproduced; readers cannot independently validate the athlete-specific outcomes from this package. No controlled intervention, causal performance benefit, full-history security audit, or newly validated prediction model is claimed.
