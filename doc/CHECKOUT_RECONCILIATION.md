<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Historical checkout reconciliation — 2026-09-08

Base: `origin/main` at `a4e98bd7cc58c036692582fe7c69196b0f7561e2`
(merged PR #94). Delivery branch: `codex/reconcile-checkouts`.

This is content reconciliation, not permission to discard dirty work. The
original uncommitted source changes below were recorded as signed commits
after review; private ignored evidence was not staged. The delivery tree ports
missing behavior and retains newer main fixes. Its history-resolution merge
records the listed tips only after the content dispositions below.

| Checkout under `.tmp/` | Recorded source tip | Disposition |
| --- | --- | --- |
| `copilot-to-complete` | `4913f656abfdac0a8770f232cc26b69ce40629a0` | Generated skill instructions and regular Claude wrappers already present in PR #94. Reverse-patch check confirms the instruction changes; no older workflow restored. |
| `desktop-delivery-publication` | `181894943f35c2d2fc2205b225233cabaf0680ac` | Secure writer lease, OAuth handling, File New/Import, active-volume About status, and mailbox-only refresh plus browser tests already in main. Restore partial-EMLX directory continuation and development version `0.1.0.dev1`; retain newer type/native fixes. |
| `dev-contacts` | `7ce91c6aee1aeb407837b0d43a439bdf33c93a34` | Recover contacts CLI, strict filtering policy/extension, tests, Makefile integration, and design/manual/plugin documentation. Geography and Contacts GUI remain planned. Supersede obsolete Flyway-runtime plan with current Python-managed SQL policy. |
| `gmail-auth-helper` | `65f9ad275e68ef2bb0d49b226990aadc68ff9936` | Recover provider-stratified Apple comparison, h3 review exporter/tests, import planning and website page. Preserve newer Ruff versions/type fixes and ordered checks. Require explicit export arguments instead of a Make target with real-mail defaults; harden export confinement and refresh. |
| `codex-repo-audit-20260903` | `a416bd8dfba6f79eec9fdbf755714c8a6ecc4e6e` | Recover `210399c` scanner deadlines, attachment allowlist, artifact/site CI, trace retention, source audit and documentation. The dated audit preserves its deferred findings; those are not represented as newly fixed. |
| `name-matcher-research` | `f30b1c622d2c79dd0916f96000126591d2f2c745` | Recover `49c20f9` research documents, typed observations/signature extraction, SQLite schema, tests, deferred AI boundary, and Make targets. Retain no-network/no-production-matcher boundary; add URI-safe read-only catalog opening and source/output separation. |
| `pr83-review-fixes` | `7288ebf8ebb8c390e021e021bb64996a95898c7a` | OAuth and lint/type changes from `8c9ef0e` already have newer implementations in main. Recover missing partial-EMLX continuation with its directory-import/idempotence test and mobile navigation wrapping. Preserve newer locked dependencies. |
| `stash-3-assessment` | `0e2603db8b5d674868afbed5e37441e42506e8a1` | Email-server document and synthetic name benchmark/data already present. Benchmark differences are newer UTF-8/type cleanup. Old decoder superseded by current bounded detector-ranked decoding and encoding tests; do not restore it. Research updates supersede the old premature Dedupe selection. |
| `issue-77-copyright-notices` | `922350cfe122c9176eb59e66d1c953fd3d87451c` plus uncommitted changes | **Blocked on owner decision:** proposed COPYRIGHT denies redistribution, whereas current website says GPL. Headers, notices, and license-audit tooling remain preserved in this checkout; not silently integrated under contradictory terms. |

## Validation and remaining work

Recovery is tested through `make check`, `make test-reconciliation`,
`make test-gui`, `make website-build-check`, and `make distribution-check`.
Test results belong to the tested delivery revision, not the historical snapshot
commits. The research and h3 fixtures prove canonical-byte preservation; no real
archive import or private research extraction was run for this reconciliation.

Before final handoff, complete the copyright decision, current-head CI and
Copilot review, and resolve any valid findings. Do not call this ledger or a
backup commit completed publication.

PR #95's first review found three integration inconsistencies: release artifact
execution preceded tag validation, BagIt writer metadata retained an obsolete
version, and mobile navigation hid links by position. The follow-up moves the
release gates before project installation, reads installed writer metadata, and
keeps navigation visible with wrapping. Regression checks exercise checkpoint
output, release step order, and actual browser layout with reordered links.
The second review's body identified an additional contact-filter diagnostic:
domain exclusions were labelled as local-part failures. Separate reasons now
identify the matched component; exclusions and local-part precedence are
unchanged. The four regression cases and the reconciliation tests pass.
The third review identified non-portable Contacts URI construction; it now uses
the same `Path.as_uri()` conversion as the catalog validators. Read-only
special-path and missing-catalog regressions cover the connection behavior.
CI also exposed a standalone-scroll test that cleared its forced overflow before
checking for a scrollbar. The test now retains explicit tall content and checks
that scrolling actually brings source locations into view.
The fourth review identified invalid contact-filter regexes escaping as
tracebacks. Patterns now validate on load, with path-qualified YAML/regex errors
and CLI regressions covering empty/populated catalogs and replace/extend rules.
The fifth review identified a Windows-invalid `?` in a research fixture path.
The fixture now exercises spaces, `#`, `%`, and Unicode instead, retaining
URI-escaping coverage without that filename restriction. Native Windows
execution remains unvalidated locally.
The sixth review identified POSIX-only scanner test executables and an
indentation-sensitive Pages release-trigger assertion. Scanner deadline tests
now explicitly skip Windows before importing the POSIX scanner; the Pages
assertion reads the YAML trigger structure, including PyYAML's `on`/`True` quirk.
The seventh review suggested replacing exclusive hard-link publication with
rename. That suggestion does not preserve no-overwrite behavior on Unix:
`os.rename()` can replace a destination created after the initial existence
check. Keep the exclusive link, and document the prototype's hard-link and
owner-only-permission filesystem prerequisites rather than add an unsafe fallback.
The eighth review identified OS execution errors escaping the scanner health
probe. Missing, non-executable, and invalid-format helpers now report unavailable;
real subprocess regressions exercise all three cases without starting a daemon.
The user-requested balanced review found missing URI enablement in the h3
exporter, a stranded database when summary publication failed, a CSS-injection
fixture rejected for missing `mode`, and an incorrect release-notes heading.
The exporter now uses an explicitly read-only attachment; evidence pairs are
staged before linking with rollback on second-link failure. Real SQLite and
filesystem tests verify write refusal, collision preservation and retry.
The GUI fixture supplies `mode: replace` and asserts the highlight-color field
causes rejection; reconciliation notes remain within the Unreleased section.
The next balanced review found duplicate deterministic AI request IDs being
collapsed and h3 publication replacing late-created empty directories. Correlation
now rejects duplicate requests; h3 publication reserves a new private directory
and moves the completion manifest last. Regressions exercise identical requests
and late-created empty directories, populated directories and files.
The following review's body found empty-domain contacts and zero-byte partial
EMLX files bypassing validation. Empty domains are now rejected after local-part
checks. Empty partial EMLX records reach the same direct-error/directory-skip
path as nonempty ones, with discovery and full import/idempotence regressions.
The next review identified source-side SQLite shared-memory writes and scanner
probe errors reaching socket replacement. Apple provider metadata now uses
private checked database/WAL copies; actual WAL fixtures verify metadata and
unchanged source sidecars. Helper execution errors now abort scanner startup
and release its lock before socket removal/daemon launch; isolated subprocess
tests cover both an existing Unix socket and no socket. The preceding CI run
failed before tests because Google's apt index returned a hash mismatch; no
integrity checks were bypassed or source changes made to hide that external failure.

After human merge, fetch/prune and prove each source tip is represented in
current main before removing its exact clean worktree and local branch.
Two checkouts contain ignored private research evidence (Gmail h3 review and
name-matching database). Retain those artifacts locally outside any checkout
being retired; never add them to Git or treat them as disposable caches.
Retire the old copilot checkout's exact shared-skill distribution entry so a
later sync does not recreate it. The root checkout must remain.

Cleanup is pending, not complete, until the GitHub merge, evidence preservation,
worktree removal, and branch-ref checks have been verified.
