# 7he Project — coaching methods and reference checks

Revision: 21 September 2026. Start with [What coaching Clayton taught me](LESSONS.md), then [the architecture](ARCHITECTURE.md). The [main journal](https://github.com/quiezent/7he_Project/tree/main/journal) adds the platform-migration story and named case studies. `operating_model.json` is a method summary, not live athlete state.

## Run the reference checks

```bash
python -m unittest discover -s tests -v
```

Run from this directory with Python 3.10+ and its standard library. No credentials or Garmin data are required. All test identities and exercises are synthetic; test doses are not exercise guidance.

`continuity.py` checks structured contract equality, timezone-aware evidence cutoffs and activity-matched exact next-day reviews for a proposed single-lever progression. It does not evaluate health, verify the truth of reports, count weekly cost, select a safe exercise, connect to cloud services or persist coaching records. Its hash is an equality aid, not an authenticated signature.

The root exporter still copies its explicit list of method files from this directory and adds a checksum manifest. The separate journal and the historical application are not included in that minimal package. The essay now names Clayton under his permission; do not describe future exports as necessarily anonymous. Checksums do not certify privacy, clinical validity or authorship.

The 26 reference tests were rerun successfully for the journal revision using code/test files whose Git blob hashes match the retained repository objects. This is not a test of the legacy analytics suite or of live integrations. No new license was selected by this revision.
