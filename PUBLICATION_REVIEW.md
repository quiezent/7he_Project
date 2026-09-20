# Publication review — 2026-09-21

**Status: BLOCKED for changing the existing repository to public.** The new `public/` package is deliberately scoped for sharing; that does not clear the repository's existing files, metadata, or history.

## What was actually inspected

Repository metadata and the returned May–June 2026 commit history; root/tree listings; the earlier README and agent handoff; selected coaching architecture, context code, and historical feedback; current private Core and decision/review records; and selected recent live Garmin activity summaries. This is a targeted implementation review, not a complete secret scan or all-ref publication audit.

The available connector reports write access and supports commits/ref updates. It does not expose a repository visibility update. No visibility-changing operation, history rewrite, credential rotation, or public-access verification has been performed by this revision.

## Known exposure requiring a decision

The retained configuration and feedback contain athlete-specific health, performance, equipment, location, and life context. Earlier README/AGENTS versions and commit metadata also require review. This note intentionally does not reproduce the sensitive values. No live credential has been established as present by the targeted review; absence of a finding is not a completed secret audit.

The old files are preserved as private historical evidence. New ignore rules only reduce future accidental additions; they neither untrack already tracked files nor remove earlier commits. Changing the default branch's prose is not historical sanitization.

## The sharing package

`tools/export_public.py` includes only its fixed, reviewed list under `public/`, plus a generated hash manifest. It excludes the Git object database, old files, private cloud identifiers, raw measurements, and private source URLs by construction. Tests use synthetic identities and dates. Inspect the exported files before distributing a future modified version; the exporter does not understand arbitrary sensitive text added to an allowed file.

The package is an immediately available alternative artifact for sharing the method. It does **not** fulfill a request to change the original repository's visibility, and it is not a sanitized replacement of the complete legacy application.

## Requirements before the original repository becomes public

Preserve a verified private backup first. Review all branches/tags and reachable history, commit identities/messages, issue/PR discussions and attachments, releases, workflow logs/artifacts, and any wiki or Pages content. Confirm rights for third-party material and select any intended license explicitly. Do not assume these surfaces are empty because they were not read in this review.

Remove or deliberately approve the personal material at the appropriate scope. Rotate any active secrets discovered. A clean-history publication needs a coordinated plan for old references and copies; do not force-push as though that alone guarantees deletion. Retain private coaching source ownership and do not publish live document access links.

Only after that review/remediation, use an authorized administrative capability to change visibility and verify both repository metadata and unauthenticated access. This update leaves that action outstanding.

## Primary references checked

- GitHub, [Setting repository visibility](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility): a public transition exposes code and Actions history/logs and permits public forks.
- GitHub, [Removing sensitive data from a repository](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository): history cleanup has consequences; old clones, references, and cached views can retain material. Rotate compromised secrets rather than relying on history edits alone.
