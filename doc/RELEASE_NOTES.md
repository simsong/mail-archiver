# Release notes

## Unreleased

* Implement Rust `mdti-validator` and `mcti-generator` for MCT Importer API 1.0,
  with `make rust-programs`, individual build targets, locked dependencies,
  Clippy/format checks and real-process stream tests. The validator discards
  stdin, reports errors and counts valid complete messages at EOF. The generator
  emits counted RFC/MIME text messages with mboxrd quoting and provenance.
  H2/h3 suffice; no h4 is introduced. Rust is required to build these tools and
  the planned PST helper; PST extraction remains unimplemented.

* Correct canonical and derived MBOX writers to use reversible mboxrd quoting;
  Python's default writer uses mboxo. Decode declared `.mboxrd` sources once,
  preserve unknown-source quoting and retain hash-verified legacy recovery.
  Document the Library of Congress reference, the code audit, all hash purposes
  and added message headers. Existing archives are not rewritten.
* Specify filename-to-stdout-mboxrd ingest executables with separate URI, importer
  name and version headers, starting with a Rust PST adapter candidate and
  optional additional passes. All headers remain in h2; h3 already includes the
  body. PST import and full Windows ingest are beta requirements. The runner,
  PST adapter, cross-importer dedup policy, Windows installer and Snap remain
  planned. Document Microsoft sources for possible MIME reconstruction.

* Normalize an immediate quoted MBOX delimiter into a literal `X-From:` header.
  Promote a meaningful inner envelope when the outer sender is `XXX` or
  `???@???`; retain the displaced outer value as `X-From:`. Preserve body
  quoting and record original source-payload hashes/framing as provenance. Fix
  `From XXX` wrapper detection misreading indented forwarded body text as a
  delimiter. The normalized archive is readable by ordinary RFC/MIME readers.
  Import failures now retain source-code tracebacks, validator origins, source
  references/cursors, message hashes, and bounded input previews in local run
  history, including both original framing previews. Bound source identity and
  cursor previews, exception messages/notes and total failure reports; reject
  malformed outer lines before header normalization. Double-framed Eudora
  metadata stubs remain excluded. Existing archives are not rewritten.
* Require an administrator-configured OpenPGP release signer allowlist before
  running release code with Apple secrets. Restrict automatic PKCS#12 import to
  GitHub-hosted runners and document process-argument visibility. Correct the
  certificate setup repository and distinguish deliberate unsigned builds from
  missing credentials. Existing local keychain identities remain supported.

* Build macOS DMGs in the GitHub release workflow. Sign the app and DMG when
  both optional PKCS#12 secrets are configured; otherwise warn in Actions and
  publish an explicitly named `_UNSIGNED.dmg`. Preserve failure on invalid
  configured credentials. Document secret setup and temporary keychain cleanup;
  signing does not yet include automatic notarization. Correct the native
  dependency audit to distinguish a library's own install name from an import.

* Use one file-drag path for message-list rows and the message-file icon,
  explicitly writing only `public.file-url` to copy `.eml` files (or a ZIP for
  multiple selected messages) to Finder/Desktop. Modifier-click selects several
  rows; dragging exports them instead of extending a selection range.
  Isolate prepared files from attachment exports, revoke tokens safely on close,
  wait for the result table to finish building before startup clears it, and
  discard queued clicks from a replaced result set.

* Fix source GUI startup treating the `mailsearch-gui` launcher and option
  values as archive paths. Preserve explicit archive selection and Finder opens;
  validate the actual launcher with the GUI self-test.

* Correct the macOS dependency audit to distinguish library install names from
  actual dependencies, avoiding a false failure for bundled pydantic-core.

* Fix false Sent classification from implicit owner-name substring matching.
  Every import now reviews owner include/exclude fields, with exclusions taking
  precedence and bare names matching only the exact mailbox name. Save defaults
  in archive `config.yaml` and regenerate `owner-names-detected.txt` separately.
  Existing archives need a fresh rebuild to correct historical classifications.
* Plan comparable Dioxus Desktop and Tauri trials before choosing the compiled UI;
  retain ingest, search, and archive preservation in Python. Prioritize Windows
  full ingest and defer Linux/snap delivery. This records the migration decision;
  the Dioxus frontend, worker protocol, and Windows installer are not implemented.
  The framework decision remains pending trial results.

* Add a clean-Windows development setup guide covering ARM64/x64 uv installation,
  x64 Python on ARM VMs, and the remaining full-ingest and packaging work.
  The procedure has not yet been executed in a clean Windows VM; this is not
  a Windows support announcement.

* Preserve original MBOX `From ` delimiters through import. When absent,
  synthesize a delimiter from the latest valid header timestamp instead of
  import time, with deterministic documented fallbacks. Existing archives are
  not automatically repaired.


* Complete the Email Collection Toolkit display-name and repository-link rename,
  preserving access to existing preferences and OAuth configuration. Add the
  Searching guide, refreshed website navigation, real interface screenshots,
  and credited Wikimedia keyboard clipart.

* Introduce the Email Collection Toolkit website identity and stacked-envelope
  application icons; preserve Gmail setup and Advanced navigation, responsive
  access, and equal personal and archival use cases. Validate Zola TOML before
  builds and report read/decode failures without a traceback.
- Add an experimental read-only ePADD address-book exporter in
  `dev/addressbook-exporter.py`, with explicit owner addresses, separate
  address-level contacts, private output, and exclusion/hash reporting.
  It avoids address-shaped display-name aliases; live ePADD repair remains
  unvalidated.

- Audit all query selectors with production-SQL EXPLAIN and execution-budget tests.
  Fix Any-address searches, sender counts, date filters under alternate sorts,
  attachment-inclusive counts, mailbox filtering, and subject candidate scans
  to use their filtering indexes before result ordering.

- Show invalid search syntax inline instead of raising a pywebview exception.
- Use the existing recipient address index for To/Cc/Bcc substring searches,
  avoiding per-message recipient probes and forced full-catalog sort scans.

* Inspect Apple Mail provider metadata using private database/WAL copies, avoiding
  source shared-memory writes. Abort on scanner helper execution errors before
  removing a daemon socket or launching a replacement.

* Reject empty-domain contacts and report zero-byte partial EMLX files instead
  of silently skipping them; direct selection of partial records still fails.

* Reject duplicate deferred AI request IDs and reserve h3 review destinations
  exclusively so a late-created empty directory is not overwritten.

* Explicitly attach h3 review catalogs read-only, stage name-evidence summaries
  before publication, and roll back database publication if the summary link
  fails. Restore the CSS-injection regression's intended validation path.

* Treat missing, non-executable, and invalid-format ClamAV health-check helpers
  as unavailable instead of allowing OS execution errors to escape the probe.

* Validate all contact-filter regexes at policy load time and report YAML or
  regex typos as path-qualified CLI errors, without tracebacks or archive writes.

* Use platform-correct read-only catalog URIs for Contacts. The standalone
  message scrolling regression now guarantees overflow and verifies actual
  scrolling to source locations, independent of platform font metrics.

* Distinguish bogus-domain contact-filter diagnostics from bogus local parts
  without changing which addresses are excluded.

* Use installed package metadata for BagIt writer versions, verify release tags
  before project installation/artifact execution, and keep all mobile navigation
  links visible regardless of their order.

* Use `gh` with the authorized review-request-only identity for Copilot requests
  instead of controlling the browser.

* Resolve committed cleanup-conflict markers and restore generated skill
  wrappers. Require pr-to-ready to integrate stranded task work before handoff
  and perform verified post-merge checkout cleanup. Retain dirty, unmerged,
  and private evidence-bearing worktrees until their disposition is settled.

* Limit Ruff discovery to tracked and non-ignored new Python files, avoiding
  generated directories while supporting project-local linked worktrees.

* Clarify installed verifier usage and make GUI asset HEAD/redirect response
  lengths explicit without reading asset bodies for HEAD.

- Standardize the agent PR workflow as `pr-to-ready`, retain
  `codex-to-complete` and `codex-to-ready` aliases, and add shared
  Codex, Claude, and Copilot implementer/reviewer instructions.


* Add the desktop document controller, archive writer leases, document options,
  coordinated import/quit handling, and macOS application packaging.
* Add the Gmail authorization developer preview and read-only Apple Mail cache
  comparison. Direct Gmail and Microsoft 365 ingestion remain unavailable.

* Tighten plug-in method contracts, native-window failure handling, and integrity
  version validation while adding complete ty and Pyright coverage.

* Require Ruff and Pylint, followed by ty and Pyright, before tests in
  `make check`, with locked development dependencies and ordered stages.

* Document the browser-driven pr-to-ready workflow, ten-minute review
  checks, signed Codex identity, and explicit human handoff for review loops.

* Make `refresh-index` observable and safe to interrupt: it now reports
  message-weighted verification/indexing progress bars with ETA, announces its
  Ctrl-C safety before work begins, and discards an incomplete replacement
  on **Ctrl-C**, retaining the existing search index. Its bounded all-core
  default (or two workers if core detection is unavailable) now parallelizes
  verified MBOX reads, SHA-256 checks, and MIME parsing
  while one ordered SQLite writer builds the replacement safely.
* Stop opening message links immediately. The message viewer now shows an
  allowed link destination in the bottom status bar on hover and requires an
  explicit **Open Link**, **Copy Link**, or **Ignore** choice on click.
* Make graphical searches complete across the full archival time span instead
  of favoring the newest 10,000 catalog rows. Probe at most 2,001 matches,
  display all sets up to 2,000, and automatically load larger remainders with
  a red background-search status. Remove manual result paging and show the
  complete search language with examples at startup and after an empty query.
* Add the rainbow-envelope identity to the Python GUI and create the Zola
  `envelope-rainbow` GitHub Pages site. The site links to project documents,
  curation resources, discussions, and tag-derived stable/beta release data.
  Add a pinned draft-release workflow modeled on bulk_extractor's signed-tag,
  source-artifact, checksum, and draft-publication flow.
* Add an OCR-engine-independent standalone printed-email PDF extractor. It
  streams page-addressable native text through Poppler, accounts for message
  and non-message pages, records PDF/page/policy and handwriting provenance,
  and atomically writes a standard derived MBOX without modifying the source.
  A focused real-PDF pytest compares the six-page SIPBADMIN fixture with its
  human-reviewed MBOX ground truth.
* Persist each ingest's typed progress and final statistics in its own
  atomically updated `status/ingest-*.json` operational BagIt tag file. Add a
  bottom GUI status line plus a singleton **Windows → Ingest** history window
  that displays every retained run and all configured worker threads without a
  catalog schema change.
* Preserve the read-only data-quality investigation as maintained scripts and
  documentation. Generated message exports and metadata evidence remain private
  ignored artifacts and are not retained in the repository.
* Treat complete `cur`/`new`/`tmp` Maildir structures and Apple Mail `.mbox`
  package chains as logical mailboxes while retaining every physical message
  file as provenance. Within Maildir, a structural single-message match yields
  to exactly one content parser, fixing envelope-prefixed messages that also
  match MBOX without using priority to hide genuine parser ambiguity. Mark
  Apple `[Gmail].mbox` observations as local Gmail caches and prefer a retained
  direct-provider observation in provenance displays when both exist.
* Rank address completions by deduplicated message count and then by the most
  recent matching message. Retain per-message suggestion dates so replacement
  and index maintenance recalculate recency correctly, while preserving the
  three-character threshold, 120 ms debounce, 20-result limit, and stale-query
  suppression in the graphical interface.
* Add read-only ingest for extensionless Emacs RMAIL Babyl files. Detection is
  based on the case-insensitive `BABYL OPTIONS:` header, and the streaming
  reader supports both LF and CRLF containers, falls back to visible headers
  when a record has no original-header block, and excludes Babyl labels and
  redundant visible-header blocks from reconstructed RFC 5322 messages.
* Compare `Date:` with a UTC-normalized, outlier-trimmed median of all valid
  `Received:` timestamps. Differences greater than two days use the computed
  date, store `received-median` in the catalog, and show a warning banner plus
  a subtle red message background in the graphical viewer. Add
  `--earliest-year` so an archive can reject earlier epoch-like header dates
  and use the same source/stream/path fallbacks; the default remains 1900.
* Add API-v1, manifest-discovered source and physical-file plug-ins. The loader
  validates every packaged and explicitly trusted `--plugin-dir` manifest
  before importing external code, rejects duplicate or ambiguous ownership,
  orders deterministically, and freezes both registries before inventory. The
  local source yields typed containers, delegates to MBOX, Babyl, EMLX, EML,
  Maildir, or external file generators, and returns typed mail objects without
  owning threads or status output. A timezone-aware source timestamp can retain
  undated provider mail with a documented `source-fallback` catalog tag. Gmail,
  IMAP, O365, Microsoft Exchange, and NUL-delimited stdin are explicit
  unavailable source stubs.
* Move local complete-file and MBOX-prefix SHA-256 behind source integrity
  controls. The framework persists append-only typed decisions and evidence;
  only completed checks can drive a later skip or resume. Provider version
  tokens and cursors are represented without being mislabeled as hashes. A
  separate archive-integrity adapter owns BagIt/Mailbag and `h1`/`h2`/`h3`
  initialization, checkpointing, and verification.
* Print every unrecognized input filename and reason once, and separately
  report unchanged containers skipped by source integrity. Add source-neutral
  cursors, provider containers, per-source concurrency limits, and a temporary
  deduplicated discovery snapshot. Stable inventories are verified before
  ClamAV; live providers are captured once; concurrency keys are fairly
  interleaved. Preserve per-container hierarchy/provenance, nullable numeric
  cursor projections, provider phases, and unknown-byte progress. Acceptance
  coverage runs a directory-loaded multi-account provider through common
  workers, status, ClamAV, catalog, hierarchy display, and canonical publication.
* Replace the unreleased development V1 catalog layout with source plug-in,
  work-ID, opaque-cursor, and typed source-integrity tables. Existing
  development archives using the earlier V1 layout are intentionally rejected
  and must be re-imported into a new archive directory.
* Suppress exact empty Eudora MBCP metadata stubs with a retained
  `source-metadata-excluded` observation, and unwrap narrowly recognized
  `From XXX` status containers so the nested RFC 5322 message supplies its
  actual sender and metadata.

### Checkout reconciliation — 2026-09-08

Recovered work from historical development checkouts:

- Read-only human-contact reports and archive-local filtering policies.
- Provider-stratified Apple Mail comparison and private, hash-verified h3 review exports.
- Experimental name/signature evidence extraction, kept separate from production matching.
- Bounded ClamAV subprocesses and confirmation for active, unknown, or mismatched attachments.
- Directory imports report partial EMLX records and continue with complete records.
- Wheel/sdist installation checks, website-build CI, and retained browser failure traces.

Contacts/geography GUI, live IMAP sources, and Refresh/Rebuild are documented
plans, not newly implemented features. No source mailbox or real archive is
changed by reconciliation or its fixture tests.
