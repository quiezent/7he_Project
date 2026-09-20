# 7he Project — accountable, persistent coaching

**Operating-model revision: 2026-09-21.** A mountain-bike coaching collaboration in which continuity is carried by explicit records, not by pretending every conversation remembers everything.

The objective is better, repeatable riding: technical execution, race pace, descending durability, recovery, and practical fueling. Software serves that objective. More metrics, more automation, and more training are not automatically better coaching.

## Start here

- [What coaching this athlete taught me](public/LESSONS.md): a reflective account of the useful lessons and the mistakes that changed the coach.
- [Current architecture](public/ARCHITECTURE.md): source ownership, dated decisions, review, and honest continuity.
- [Public reference implementation](public/README.md): small, offline integrity checks and synthetic tests.
- [Publication review](PUBLICATION_REVIEW.md): why the existing repository is **not yet cleared for public visibility**.

## The current iteration

Since the verified **2026-09-06 cloud cutover**, the private Google Drive coaching records are the single routine coaching master:

| Record | Responsibility |
| --- | --- |
| Coach Core Memory | Durable goals, preferences, constraints, and learned operating principles |
| Athlete Performance Database | Selected measurement evidence, ingestion identities, policy inputs, and formula-owned calculations |
| Coaching Decisions and Reviews | Dated prescriptions, actual delivery, response, corrections, and amendments |
| Garmin Connect, read through its connector | Live source of Garmin measurements; not authority for every aspect of technical riding |
| This repository | Methods, reference code, tests, and retained historical specialist tooling; **not a second live athlete ledger** |

Current instructions and corrections must be reconciled with the relevant records. A current goal is not a completed workout; availability is not readiness; an old measurement is not today's clearance. Public files contain no current athlete prescription or private cloud document identifiers.

The ongoing development direction is faster repeatable race execution alongside appropriate descending and strength support—not indefinite conservative practice. An actual session still needs current evidence, an explicit complete dose, and an appropriate review.

## What changed from the earlier implementation

The May–June 2026 repository staged evidence, local context, generated plans, and predictive reviews through `src/coach_sync` and `tools/`. Its useful ideas remain: modality-specific evidence, explicit session contracts, density control, sensor-quality checks, and model interpretation rather than blind rules.

Its old local-source ownership labels and dated athlete phase are **historical**, superseded for routine coaching by the September cloud cutover. Existing `config/athlete_context.json`, `config/coaching_architecture.json`, and `input/feedback_*.json` remain private historical evidence. Their contents and the earlier commits have not been deleted or rewritten.

**Compatibility boundary:** the legacy CLI still reads and may write local configuration and snapshots. It has not been converted into a cloud adapter, disabled, or fully revalidated by this update. Do not schedule it as a competing daily writer or treat a generated local plan as current coaching authority. Use it only for a named specialist question, check current cloud intent first, and return relevant findings to the authoritative private record.

## Run the new reference tests

Python 3.10+; standard library only. No Garmin account, cloud access, or athlete data is required.

```bash
cd public
python -m unittest discover -s tests -v
```

These tests exercise documentary integrity, not a clinically validated readiness model. They do not run the legacy analytics suite, test a live connector, or authorize exercise.

To create the separate allowlisted sharing package from the repository root:

```bash
python tools/export_public.py --output /tmp/7he-project-public.zip
```

The exporter copies only named files from `public/`, rejects symlinks, and includes a checksum manifest. It does not include Git history, private configuration, feedback, cloud records, or credentials. It is not a general secret scanner or a certificate that the entire repository is safe to publish.

## Publication boundary

The user requested a public project. This revision prepares the shareable methods while retaining the private history. Changing the existing repository's visibility still requires privacy review/remediation across history and repository surfaces, plus a supported administrative action. Updating a README or `.gitignore` does not sanitize old commits.

Do not claim that this repository is already public, that its complete history was audited, or that cloud coaching ownership changed again. No license has been selected merely by preparing this sharing package.
