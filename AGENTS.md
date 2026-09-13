<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

<!-- BEGIN GENERATED pr-to-ready -->
## pr-to-ready

For `pr-to-ready`, `codex-to-complete`, or `codex-to-ready`, read and follow [the shared workflow](.agents/skills/pr-to-ready/SKILL.md). Apply the repository-specific rules below.
<!-- END GENERATED pr-to-ready -->

# AI contributor instructions

## Scope and source safety

This project creates a personal, long-lived email archive. Its canonical
content is standard MBOX plus SHA-256 manifests; SQLite databases and search
indexes are rebuildable derivatives.

* Never modify, delete, move, label, mark read, or otherwise mutate a source
  mailbox, Gmail account, IMAP account, Apple Mail store, or input archive.
* Preserve original RFC 5322 bytes. MIME parsing, text extraction, malformed
  MIME handling, and Unicode decoding are best-effort derived operations and
  must never cause a message to be dropped or rewritten.
* A positive ClamAV result routes a message to `INFECTED.mbox`; it is not a
  reason to delete or alter the message. Scanner errors and unscannable input
  must be recorded and retained.
* Do not ingest into a real canonical archive until the user identifies the
  target directory and explicitly authorizes that run. Develop and validate
  against purpose-made fixtures or a copied subset.
* Do not alter local machine configuration, install software, start a
  persistent service, or schedule jobs without explicit user approval. The
  ClamAV daemon is on-demand only; never enable on-access or scheduled scans.

## Requirements and documentation

Before implementing a behavior change, read `doc/requirements.md` and
`doc/implementation.md`. Update both in the same change when behavior, archive
format, recovery semantics, database schema, CLI, or source support changes.

Keep these documents factual and compact. Document the relevant requirement
next to each substantive test or test module. Never claim a feature works
merely because it compiles or emits output: confirm that validation exercises
the stated requirement and report any remaining gap.

## Python implementation

* Use Python 3.12+ and `uv`; add project dependencies through `uv`, not pip.
* All ordinary test and run workflows belong in the Makefile. Use `make check`
  for the full suite; add focused targets only when they validate a distinct
  real behavior.
* Prefer standard-library `mailbox.mbox` for MBOX reading and writing. Pass raw
  `bytes`, not reserialized `email.message.Message` objects, when writing
  archived messages.
* Use typed Pydantic models for internal data. Restrict dictionaries to
  external API boundaries and put external keys in named constants.
* Keep file and database I/O streaming; do not load an archive or attachments
  wholesale into memory.
* Use SHA-256 for canonical message and manifest hashes. Do not substitute a
  hash algorithm, data source, parser, or archive format without explicit user
  approval.

## Tests and validation

At the completion of every Codex turn in this repository, including discussion
and documentation-only turns, run `make ruff` against the current final source.
Ruff must pass with zero diagnostics; if it fails or cannot run, report that
explicitly rather than claiming a clean handoff. Do not suppress rules or ignore
failures to make a build pass. `make check` and `make dmg` must retain Ruff as a required
prerequisite; Ruff complements rather than replaces tests and Pylint.

Write only substantive tests that test a requirement or a demonstrated
regression. No coverage-only tests and no mocks unless unavoidable.

Fixtures must cover byte preservation, mboxrd quoting, malformed MIME, invalid
character encodings, missing dates, duplicate Message-ID with different
content, autosave exclusion, rollover, interruption recovery, and source
idempotence. Use EICAR with the locally configured on-demand ClamAV daemon for
the scanner integration test; use recorded typed scan results only for
unit-level routing tests.

Before reporting a phase complete, review the changed implementation and tests
against every relevant requirement, run the appropriate Makefile target, and
distinguish validated behavior from untested assumptions.

## Git and external actions

Preserve dirty worktrees and unrelated changes. Use project-local linked
worktrees under `<project-root>/.tmp/` for branch work.

Before starting branch work or creating, updating, or merging a pull request,
query GitHub for every open pull request in the repository. If any are open,
warn the user before proceeding and identify each PR's number, title, base, and
head. Check for overlapping files or commits, stale bases, and changes already
integrated or superseded by another PR; never silently duplicate or overwrite
open PR work.

Every push to a GitHub branch other than `main` must have a matching open pull
request whose head is that exact branch. Before pushing, check for that PR; if
none exists, create it as part of the same publication workflow. After pushing,
verify that the PR head matches the pushed commit. Never leave work only on a
GitHub non-`main` branch, including when a previously merged branch name is
reused.

## Authorization and identity

A request to perform **pr-to-ready** (including **codex-to-complete** or
**codex-to-ready**)
authorizes the commits, pushes, draft PR creation, review requests, review-thread
replies, fixes, and final human-review request described below. Continue through
these steps without repeatedly requesting permission. Otherwise, do not commit,
push, or modify PRs without a user request. Do not approve or merge a
PR, close an issue or superseded PR, or change remote services without explicit
user authorization for that action.

All Codex GitHub writes and browser actions use `@simsong-codex`, except
that `@simsong` is authorized solely for requesting Copilot reviews when needed.
Verify the CLI identity, SSH push identity, and browser login separately. Before committing, configure and verify author and committer as
`Codex AI Assistant <simsong+codex@acm.org>` and verify the signing key belongs to
that identity. Sign every Codex commit. Verify the result before pushing with
`git log -1 --format='%G? %GS %an <%ae> %cn <%ce>'`. To correct identity on an
existing commit, use `git commit --amend --reset-author -S`.

## pr-to-ready project requirements

Follow the shared workflow with these mail-archiver requirements:

- Request and re-request Copilot review through `gh pr edit <number>
  --add-reviewer '@copilot'`, authenticated as `simsong`. This personal-account
  exception covers only Copilot review requests; restore `simsong-codex`
  afterward, including on failure, for all other GitHub writes. Verify the
  request through reviewer or timeline evidence, not command success alone.
  Do not control the browser or post an `@copilot` comment to request review.
  Unavailable authentication or a failed request is a reported blocker; keep
  the PR draft.
- Check current-head review, threads, and CI every ten minutes. Continue the
  cycle through review fixes and re-review, using a quiet task heartbeat when
  continuation across turns is needed and available; stop it on completion.
- A documented review loop permits human handoff only when the same
  substantive finding returns in two successive review rounds after an
  evidence-backed fix or explanation, with no new actionable information.
  Record threads, commits, evidence, and disagreement. Required CI and local
  validation must still pass; missing reviews and valid unfixed defects remain
  blockers. Mark ready, request review from and assign `simsong`, explicitly
  stating that Copilot is not clear and identifying the disputed findings.
  Never represent this exceptional handoff as a successful Copilot review.

## Validation and cleanup

Use Makefile targets for Ruff and Pylint linting, then ty and Pyright type
analysis, then pytest. Keep these stages ordered in the aggregate check target,
even under parallel make. Treat all diagnostics as failures; fix the underlying
logic or precise types rather than broadly excluding files, disabling checks,
or adding blanket ignores. Add focused third-party stubs only where needed.
Update requirements, implementation documentation, and release notes when the
corresponding behavior or developer workflow changes. Report skipped tests and
external prerequisites separately from passing validation.

After a branch is merged into `origin/main`, fetch and prune, prove its work is
represented in the current main (ancestry, or explicit squash/rebase evidence),
and verify the linked worktree has no modified or untracked files. Only then
remove its linked worktree and delete its local branch. Preserve dirty,
unmerged, or uncertain worktrees. A superseded PR closed during consolidation
is not proof that its branch has reached main.

## Website screenshots

When the importing window changes, regenerate `website/static/images/importing-interface.png`
with `make website-screenshots` and visually inspect the Importing page in the
same PR. Changes to the search interface likewise require regeneration of
`search-interface.png` and inspection of both the homepage and Searching page.
Use only the purpose-made synthetic collection; never publish private mail.
