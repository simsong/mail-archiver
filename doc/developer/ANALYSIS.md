<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Historical repository audit

Recovered during checkout reconciliation on 2026-09-08. Findings and validation
below describe the pinned 2026-09-03/04 revisions, not current release status.
See ../implementation.md for current behavior; writer locking and native APIs
have since changed.

Date: 2026-09-03

Audited commit: `499ae6fc01c6c07d6ea0efcaef47221251a66a4f` (`origin/main`)

Scope: all 188 tracked files, including Python, JavaScript, Swift, SQL, documentation,
tests, validation tooling, packaging, and GitHub Actions.

This audit is a source review, not a claim that every platform, public corpus, or
external service was exercised. It distinguishes demonstrated defects from risks
that still need a focused reproduction. The hosted native-GUI failure already
tracked through pull request #67 was inspected only to establish current status;
its root cause is outside this audit's scope.

## Executive summary

The core archive design is unusually careful about canonical bytes, transaction
recovery, independently installed verification, and derived-data rebuildability.
The local automated suite is substantive and passed on the audited commit. The
test layers are complementary rather than redundant.

The highest-priority defects are around release packaging and public-corpus
validation rather than the main ingest happy path:

1. The release source distribution advertises commands whose required assets are
   absent from the artifact. In particular, an installed `mailsearch-gui` cannot
   find the top-level `gui/` tree.
2. A validation rerun deletes the preceding result ZIP and report before the new
   run has acquired or verified anything, contrary to the documented baseline
   retention policy.
3. Validation download, Git, and extraction caches are not bound to the configured
   source identity. A changed URL, moving Git ref, or changed archive can silently
   reuse stale bytes or an old extraction.

These should be fixed before treating a GitHub release or a longitudinal public-
corpus comparison as reproducible.

## Findings

Priority meanings: **P1** is release- or evidence-blocking; **P2** should be fixed
before relying on the affected boundary; **P3** is bounded maintainability or
documentation debt.

Disposition on 2026-09-04: findings 1-5 and 8 remain documented for later work.
Findings 6, 7, 9, and 10 are resolved in this audit branch, except that the new
distribution gate intentionally cannot exercise the deferred missing GUI and
validation runtime resources from finding 1.

### P1, deferred: the release artifact omits files required by installed commands

`pyproject.toml:17-24` publishes `mailsearch-gui` and
`mailarchiver-validation`. `gui_app.py:47-51,680` locates `gui/index.html`, icons,
and the E2E driver by walking from the source module to top-level checkout
directories. The package-data declaration at `pyproject.toml:33-40` does not
include those directories.

The release workflow builds only a source distribution (`release.yml:45-48`). A
fresh build of that artifact contained the Python package, selected package data,
and tests, but no `gui/`, `e2e_tests/gui_driver.js`, or `validation/datasets/`.
Consequences:

* `mailsearch-gui` starts with paths that do not exist in an installed artifact;
* its application icon is also absent; and
* `mailarchiver-validation` defaults to `validation/datasets`, which is not
  installed, so its default invocation cannot list or run the shipped corpus
  definitions.

Move runtime GUI assets under `src/mailarchiver/` and access them through
`importlib.resources`. Either package validation definitions similarly or stop
advertising the validation entry point as installed end-user functionality.
Build both sdist and wheel, install each into a clean environment, and smoke every
console entry point before creating a release. This audit branch now performs
clean installation and non-runtime entry-point smoke checks, but the missing
runtime resources themselves remain deferred under this finding.

### P1, deferred: validation reruns destroy the prior baseline before success

`validation.py:563-573` unlinks the prior result ZIP and JSON report before
acquisition, extraction, preparation, ingest, or verification. It then removes
the prior Mailbag before the new ingest. Any later exception leaves the last
successful evidence missing.

This directly contradicts `doc/REGRESSION.md:143-160`, which requires preserving
the preceding baseline until a difference is classified and an intentional
replacement is recorded. It also makes ordinary transient download, ClamAV, disk,
or verifier failures destructive.

Create a unique run directory, build and verify all new evidence there, and
atomically update a `latest` reference only after success. Keep the prior report
and ZIP addressable by run ID. Add a test that seeds a valid preceding result,
forces a real subprocess or acquisition failure, and verifies every preceding
artifact byte-for-byte.

### P1, deferred: validation caches can silently represent the wrong corpus

There are three independent stale-cache paths:

* `download()` (`validation.py:210-240`) accepts an existing ordinal/filename
  without proving it came from the configured URL. Changing a URL while keeping
  its filename can record the new URL alongside old bytes. Fifteen of the twenty
  configured HTTP sources have no expected SHA-256 to catch this.
* `acquire_git()` (`validation.py:243-255`) clones only when the mirror is absent;
  it never fetches an existing mirror before resolving a ref. A moving ref can
  therefore resolve against stale local refs.
* `extract()` (`validation.py:351-385`) treats a presence-only `.complete` marker
  as sufficient. The marker is not bound to the acquired artifact digest,
  extraction rules, or extractor version.

The source manifest records what the process observed, but cannot repair these
identity errors after stale input has been accepted. Make acquisition content-
addressed by URL plus expected/observed digest, fetch Git refs explicitly, and
store the artifact digest and extraction-policy version in the completion marker.
Tests should change each identity component while reusing the same data directory
and prove that old content is never returned as new.

### P2, deferred: AWS validation is not reproducible by default

`validation/template.yaml:20-25` defaults `RepositoryRef` to mutable `main`.
Each worker independently fetches that ref (`validation/aws/launcher/app.py:92-97`),
while `RunReport` (`validation.py:119-127`) records neither the resolved commit nor
the worker environment. Workers in one launch-all sweep can therefore execute
different commits if the branch advances. The documentation says a published
ref is required, but the template accepts and defaults to the opposite.

The worker also installs `uv` by piping the current network installer into a root
shell (`launcher/app.py:84-90`) without a version or checksum. That is both a
supply-chain risk and an uncontrolled experimental variable.

Require a full commit SHA (or resolve a signed tag once in the launcher), pass the
resolved SHA to every worker, record it in every report, and pin and verify the
tool installer. The report should also capture Python, uv, OS image, ClamAV
engine/signature, configuration, and relevant dependency-lock identity.

### P2, deferred: the native smoke adapter no longer matches the real search API

`GuiApi.search()` takes `limit` as its seventh argument
(`gui_app.py:346-359`). `NativeSmokeApi.search()` still names that position
`find_older` and passes `False` (`gui_app.py:588-600`; `gui/app.js:116`). Because
zero means unlimited in `search_page()` (`gui_service.py:183-209`), smoke mode
runs an unbounded search rather than the production default page of 100. The
one-message fixture cannot expose that behavioral difference.

Make the adapter signature identical to `GuiApi.search`, or have it call the real
method with named arguments. Add more than 100 matching messages and assert the
same pagination contract through both APIs. A signature-contract test would also
catch future positional drift.

Current hosted status is easy to misread: the `native-gui-e2e` job for the audited
commit is green because its smoke step has `continue-on-error: true`, but the step
itself failed with `native bridge did not finish within 15 seconds`. This is the
known PR #67 area and is not diagnosed further here.

### Resolved: ClamAV subprocesses now have hard timeouts

The startup loop formerly had a deadline while the health probe and per-message
scan used unbounded `subprocess.run()`. `scanner.py` now gives health probes a
five-second caller deadline and message scans a five-minute deadline. A timed-out
health probe reports unavailable; a timed-out message scan fails ingest, and its
`finally` path removes the plaintext temporary message. `tests/test_scanner.py`
uses real sleeping subprocesses to prove both deadlines and cleanup.

The separate typed scanner-error/unscannable result and scanner-version evidence
requirement remains deferred; the real EICAR/daemon lifecycle coverage remains.

### Resolved: attachment-open warnings use an inert allowlist

The former suffix denylist missed common containers. Opening now bypasses
confirmation only when both normalized MIME type and suffix match the inert
allowlist for PDF, common static images, or plain text. Everything unknown,
mismatched, executable, container-like, or active requires confirmation. The UI
warning now says “active or unrecognized” rather than only “executable.”

Table-driven tests cover TAR, gzip, 7z, RAR, macro-enabled documents, CSV, shell
scripts, ambiguous binaries, double extensions, MIME mismatches, PDF, image, and
plain-text cases.

### P2, deferred: two ingest processes are not excluded from the same archive

Within one process, `publication_lock` correctly serializes worker publication.
No archive-wide OS lock is acquired before opening `archive.sqlite3`, MBOX files,
the publication journal, or BagIt manifests. Two CLI processes targeting the same
archive could therefore interleave recovery and checkpoint activity even though
the user documentation says publication is single-writer.

This is a code-path risk, not a reproduced corruption. Make the contract explicit:
either acquire a nonblocking archive-wide advisory lock for the whole ingest or
document and detect unsupported concurrent writers. A real two-subprocess test
should prove that the second writer fails cleanly without changing canonical or
integrity files.

### Resolved with finding 1 exception: CI validates distributions and the complete website

CI now has a separate `distribution-and-site` job. It builds and installs both
sdist and wheel in clean environments, verifies declared package resources, and
invokes every console entry point without launching the GUI or an external
service. The deliberate limitation is finding 1: runtime GUI and validation data
remain absent, so this gate cannot yet exercise those resources.

The same job downloads the pinned, checksum-verified Zola binary and runs
`make website-build-check` on pull requests. Linux test failures upload retained
Playwright traces for seven days. Publishing a GitHub release now triggers Pages,
so release links refresh without an unrelated website commit.

### Resolved: documentation distinguishes implemented and planned behavior

`requirements.md` now says explicitly that it specifies the target system and
marks incomplete source adapters and sorting as planned. It records the current
`review --run` boundary and describes CLI/environment plus packaged YAML rather
than a nonexistent first-release operator TOML file. Scanner and attachment
behavior are updated with the new policies.

`README.md` now points to this repository audit as current and labels
`source-code-audit.md` a 2026-08-25 snapshot. That older file now describes its
30-file scope and manual native-GUI status as facts at the audited revision,
rather than claims about the present tree. `USER_MANUAL.md` and
`implementation.md` describe the attachment allowlist.

### P3: maintenance boundaries need tightening

* `src/mailarchiver/__main__.py` is 1,939 lines and combines CLI construction,
  progress/status presentation, source discovery, scheduling, publication,
  recovery, review, reporting, and reindex orchestration. Split along transaction
  boundaries, not merely by file size: CLI parsing, ingest application service,
  publication transaction, and reporting are natural seams.
* MBOX handling relies on private stdlib internals (`mbox.py:135` uses
  `mailbox.mbox._lookup`; `sources.py:302` reads `_toc`). Those APIs can change
  across Python releases. Isolate them behind one compatibility module and test
  the minimum and newest supported Python versions.
* CI tests only Python 3.12 even though the declared support is `>=3.12`. Add at
  least 3.12 and the newest supported CPython to the non-ClamAV unit matrix, while
  retaining one full ClamAV job.
* Pylint reports 10.00/10, but the project disables all convention, refactor, and
  warning classes plus `no-member` (`pyproject.toml:54-55`). That score is useful
  for enabled errors, not evidence that structural debt is absent. Enable selected
  high-signal rules explicitly or add a separate narrow debt job.
* `scripts/data_quality/summarize_evidence.py` carries CSV rows as dictionaries
  with repeated literal keys. This is an external-data boundary, but named key
  constants or a typed row model would prevent schema drift and match the rest of
  the codebase.

Do not remove the independent logic in `standalone_verify.py` merely because it
resembles production MBOX/BagIt logic. Independence is the verifier's trust
boundary; sharing the same implementation would make correlated defects harder
to detect. Likewise, the delayed import between `sources` and `source_integrity`
is worth simplifying when those modules change, but it is not currently shown to
be an initialization failure.

## Test reliability and recommended extensions

No existing test was found to be redundant enough to delete. Apparent overlap is
intentional:

* unit and integration tests exercise parsing, transactions, recovery, and source
  identity without a browser;
* `tests/test_end_to_end.py` validates the ingest/ClamAV/BagIt/standalone-verifier
  lifecycle;
* Playwright validates the real Python service plus complete HTML interaction;
* native smoke validates the WKWebView bridge and Cocoa shutdown boundary; and
* the standalone verifier duplicates selected logic deliberately so it can catch
  producer defects.

Do not replace lifecycle ingest with a cached archive shared across suites. That
would erase the producer/consumer boundary each suite is meant to validate. The
Playwright fixture is already session-scoped within its own suite.

Highest-value additions, in order:

1. package the deferred GUI and validation runtime resources and extend the clean
   artifact smoke through those runtime paths;
2. validation rerun failure preservation and content-addressed cache invalidation;
3. archive-wide two-process writer exclusion;
4. typed scanner outcomes and scanner-version evidence;
5. native adapter signature/pagination parity on more than one page; and
6. a disposable-copy GUI test for real SQLite corruption/locking or I/O failure.

The existing browser suite is not happy-path-only: it already asserts that an
invalid query displays its error (`e2e_tests/gui_driver.js:276-277`). Prefer a
real disposable corrupt/read-only archive over a mocked backend response for the
remaining GUI failure coverage. A 10,000-message “load all” browser test is not a
good default gate; first add deterministic page-boundary and memory/time budgets,
then place larger benchmarks outside required CI.

## CI/CD review

Strengths:

* workflow permissions are explicit and least-privilege;
* the release job verifies an annotated tag, its GitHub signature, and the project
  version before creating a draft;
* `uv sync --locked`/`--frozen` is used in CI and validation workers;
* ClamAV is exercised as a real on-demand service in Linux CI;
* native phase reports and process diagnostics are uploaded on every hosted run;
  and
* the hosted native runner is pinned and intentionally advisory.

Corrections to the preceding analysis:

* `astral-sh/setup-uv` already enables GitHub Actions cache automatically; there
  is no demonstrated missing uv-cache configuration.
* The current action versions are real; the relevant hardening choice is mutable
  tag versus immutable commit SHA, not whether those releases exist.
* A green advisory job does not mean the native smoke passed. Read the step
  outcome and uploaded phase report.

Recommended order of CI/CD work:

1. extend the new artifact gate through the deferred runtime resources;
2. test Python compatibility separately from the expensive ClamAV job;
3. pin third-party actions by reviewed commit SHA if that maintenance policy is
   acceptable; and
4. make advisory failures visible in branch/PR summaries without representing
   them as passed tests.

## Validation performed

On the pinned worktree:

* `make test`: **249 passed, 1 skipped**. The skip is the optional local 86 MiB
  encoding corpus.
* `make test-e2e`: **5 passed, 1 skipped**. The skip is the explicitly enabled
  native macOS WKWebView run.
* `make pylint`: **10.00/10** under the configured reduced rule set.
* `make website-check`: passed.
* `make website-build-check`: passed with Zola.
* `make distribution-check`: built, inspected, clean-installed, and smoke-tested
  both sdist and wheel. The deliberately unexercised runtime-resource paths remain
  documented under finding 1.
* GitHub checks for the audited `main` commit: pytest and pylint passed.
  The native wrapper concluded success, but its advisory smoke step failed as
  described above.

Not exercised: a real canonical archive, any source mailbox, the full optional
encoding corpus, live public-corpus downloads, live AWS/S3/SAM deployment, a
release publication, or local native GUI launch. No source mailbox or canonical
archive was modified.
