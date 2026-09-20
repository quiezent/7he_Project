# Historical local coaching stack

This directory preserves the implementation present at commit `7345d9110275d9c5828ff98395c998efb336bc4d` (8 June 2026). It is a source archive, not the current coaching master, not a September snapshot, and not a current prescription.

The `src/`, `tests/`, `tools/`, `config/`, and `input/` trees and dependency files are retained without changing their original bytes. The former front-page documents are preserved as [README.original.md](README.original.md) and [AGENTS.historical.md](AGENTS.historical.md). Their ownership labels, phase, session suggestions, proposed equipment changes and numerical assumptions are historical. The latter file is evidence, not active agent instructions.

## Why retain this

The old implementation makes the evolution inspectable: local athlete context, Garmin normalization, source-quality audits, session contracts, generated proposals and experimental prediction. Deleting it would make the current lessons less accountable. Clayton has authorized retaining relevant personal coaching history for publication; that does not turn old observations into current facts.

## Read before running

Read the [current architecture](../public/ARCHITECTURE.md) and [platform story](../journal/2026-09-21-moving-platforms.md). Since the verified 6 September 2026 cutover, the private cloud records are the single routine coaching master. This old CLI can still write local configuration and derived files. Do not enable a competing daily writer or use its generated plans as current advice.

Relocation is archival, not a runtime modernization. The complete legacy suite has not been rerun in the publication environment. The original installation instructions may be examined for specialist work from this directory, but no credentials, live Garmin sync, automatic training or production compatibility is implied. Any old assumptions about working directories, dependencies, source availability and dates must be checked for the specific analysis.

The separate `../public/` reference checks and root exporter are newer work; their tests do not validate this historical application.
