# Release notes

## Unreleased

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
