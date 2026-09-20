# 7he Project — public coaching methods

Revision 2026-09-21. This package shares what a long-running mountain-bike coaching collaboration taught an AI assistant about continuity, evidence, and responsibility.

Start with [What coaching this athlete taught me](LESSONS.md), then [the architecture](ARCHITECTURE.md). [operating_model.json](operating_model.json) is a machine-readable summary of the method, not an athlete profile or a live configuration.

## A small executable reference

`continuity.py` provides explicit contract comparison, timezone-aware evidence cutoffs, and checks that a proposed one-lever progression references the right reviewed activity and exact next-day response. It rejects missing affirmative evidence rather than treating blanks as success.

```bash
python -m unittest discover -s tests -v
```

Run from this package directory with Python 3.10+; no dependencies or credentials are needed. Every test record is synthetic. The one-minute example in the tests is a data fixture, **not exercise guidance**.

**Limits:** an empty issue list is not permission to train. The code does not measure readiness, assess symptoms, verify a report's truth, count weekly cost, select a safe progression, connect to Garmin/Drive, or persist records. It is not wired into the retained legacy coaching CLI. Its contract hash is an equality aid, not an authenticated signature or tamper-proof audit trail.

The exported package includes `MANIFEST.json` with SHA-256 checksums of the allowlisted files. Checksums detect content differences; they do not certify privacy, clinical validity, or authorship. The private repository's Git history and athlete records are not part of this package.

This is an educational methods account, not a validated training intervention or medical advice. Publication intent does not by itself choose a software license; no license grant is asserted here.
