# Mail archive normalizer requirements

## Recovered offline diagnostic boundaries

ClamAV health and message-scan subprocesses must have hard deadlines and remove
temporary plaintext even on timeout. Known incomplete EMLX records must be
reported without blocking complete messages in a directory import, while direct
selection fails explicitly.

Private h3 review exports and name-evidence databases must be new derivatives
outside source stores. Canonical message bytes are verified before extraction;
neither tool changes an archive, chooses deduplication by h3 alone, or sends
message evidence to a remote model. Edited review manifests must not redirect
reads or writes through escaping paths or symbolic links.
H3 review publication must reserve a new destination exclusively, refusing even
an empty directory created during extraction, and publish its completion manifest
last. Deferred AI correlation must reject duplicate request IDs, including
identical requests that produce the same deterministic ID.
Name-evidence publication must not replace an output created concurrently.
Both database and summary must be staged before publication; failure to publish
the summary must remove the database link created by that attempt, preserving
any competing output. The h3 comparison index must attach the source catalog
explicitly read-only with SQLite URI handling enabled.
The current prototype requires hard-link support and owner-only permissions on
its output filesystem; it must not substitute an overwriting rename.

## Purpose

Create and maintain a cleartext, personal, long-lived archive of all user
email. The archive is canonical; all databases and user interfaces are derived
from it and may be recreated. It must support unified search across decades of
mail, non-destructive redacted derivatives, and reproducible research reports
from structured metadata.

This document specifies the target system. Items explicitly marked **Planned**
are requirements whose implementation is incomplete; `README.md` and
`implementation.md` describe the current executable feature set.

The system must harvest backup drives and active sources, including Outlook
`.pst` and `.ost`, Eudora backups, Emacs RMAIL Babyl, working IMAP client-cache
directories, MBOX, EML, Maildir, Apple Mail, Gmail exports, and live read-only
IMAP accounts. It must be safe to rerun an ingest operation on the same source
without duplicating or rescanning messages already archived.

## Experimental ePADD address-book export

`make addressbook-export` reads only sender/recipient addresses referenced by
messages in the selected archive catalog and writes a private text derivative
outside the archive. The first contact contains explicitly selected exact owner
addresses or, with `--owners-from-sent`, distinct sender addresses from catalog
messages classified Sent. Recipients are never treated as owners merely because
they received Sent mail. Historical Sent classification can itself contain false
positives; it is not independently verified owner identity. All other addresses
remain separate, with no inferred display-name aliases. Single-entry contacts
preserve identifiers without `@` using the ePADD 11.1.3 reader's fallback; Sent
identifiers without `@` remain separate and are reported because they cannot be
represented as owner email aliases in this format. Unsafe contact lines are
excluded and individually reported; case normalization, totals, and SHA-256
evidence are recorded. Output must not replace existing files or mutate the
source. This is a whole-address-book replacement experiment, not a reviewed
People authority or validated live repair.

## Canonical archive layout

All deliverables reside in one archive directory. That directory is a native
BagIt 1.0 bag conforming to Mailbag 1.0. It contains `bagit.txt`,
`bag-info.txt`, `mailbag.csv` or its required numbered parts,
`manifest-sha256.txt`, `tagmanifest-sha256.txt`, a top-level `integrity/` tag
directory, and a `data/mbox/` payload directory.

* Mail is stored only under `data/mbox/` in standard MBOX files, using
  byte-preserving mboxrd quoting. Do not retain a per-message EML corpus.
* Preserve every available original `From ` record delimiter, including sender,
  timestamp, whitespace, and line ending. Carry MBOX framing separately from
  RFC message bytes so message hashes and deduplication stay unchanged except
  for the explicit double-framing normalization below. For
  duplicate RFC messages, the first published observation supplies the envelope.
  For immediate double framing, preserve the selected delimiter and convert
  the other envelope to a literal `X-From:` header under the rule below.
  Apply the canonical byte/hash and source-reconstruction contract in
  [INTEGRITY_CONTROLS.md](INTEGRITY_CONTROLS.md#verifying-and-reconstructing-normalized-records).
  The separate supported status-header `From XXX` wrapper uses its nested delimiter.
  Only synthesize a delimiter when none exists: use the latest valid timestamp
  across Date, Received timestamp suffixes, Resent-Date, and Delivery-Date,
  normalized to UTC, independently of the routing-date median. Invalid or
  implausible timestamps do not participate. Without a valid header timestamp,
  use the existing resolved source/prior/path date; a low-level writer without
  that context uses the fixed Unix epoch, never the current time. Synthesis uses
  the parsed sender when representable as one ASCII token, otherwise
  MAILER-DAEMON. Never rewrite a present envelope to conform to these rules.
* Normal mail is partitioned by resolved message year and category:
  `{YEAR}-Sent1.mbox` and `{YEAR}-Archive1.mbox`.
* A file rolls over before it reaches 3.75 GiB.  Later parts are named
  `{YEAR}-Sent2.mbox`, `{YEAR}-Sent3.mbox`, `{YEAR}-Archive2.mbox`, etc.
* Messages detected as infected are instead placed in `INFECTED1.mbox`, with
  the same numeric rollover rule if needed.  They are never discarded or
  altered.
* A noninfected message is **Sent** when its parsed `From:` address matches
  an owner include rule and no exclude rule; otherwise it is **Archive**.
  Rules are case-insensitive whole-mailbox globs (`*`, `?`, `[abc]`). A bare
  `slg` is equivalent to `slg@*`: it matches `slg@example.org` and the legacy
  sender `slg`, but not `3slg` or `slg+tag`. Only a rule containing `@` matches
  a domain. `*simson*` with exclude `*david*` excludes `david_simson@example.org`
  and never matches an unrelated mailbox just because its domain is `simson.net`.
  Neither display names nor implicit substring expansion identify owners.
* Normalize the valid RFC 5322 `Date:` and every valid timestamp suffix in a
  `Received:` header to UTC. Sort the Received dates, discard one minimum and
  one maximum when at least three exist, and compute the median (the midpoint
  for an even retained count). With only one or two valid Received dates,
  compute their untrimmed median. If `Date:` differs from that result by more
  than two days, route with the Received median and store `received-median` as
  `date_source`; otherwise retain `Date:`. If `Date:` is absent or invalid,
  use that same Received median with `received` as `date_source`. The ingest
  option `--earliest-year` defines the first plausible year for both header
  sources (default 1900); more than one year in the future is also implausible
  and invalid. When
  neither header supplies a date, use the message-specific typed
  `MailObject.source_date_utc` when present and store `source-fallback` as
  `date_source`. It must be timezone-aware and is normalized to UTC at the
  plug-in boundary. Otherwise inherit the prior resolved date in the same
  input mailbox stream. For a filesystem message with no prior resolved date,
  derive the year from a four-digit year in the source path and record that
  fallback. Never route ordinary input to a `0000` mailbox merely because its
  date is absent. If no `Received:` timestamp is usable and the `Date:` header
  is missing or has an epoch-like year through 1980, scan decoded text bodies
  for embedded `Date:` headers and configured localized quoted-date patterns.
  Use the most recent plausible candidate and record `body-embedded` as
  `date_source`, while retaining the original bytes. These patterns are
  packaged configuration, not source-code literals, so supported language
  forms can be added without changing the parser.
* `X-Apple-Auto-Saved` messages are excluded entirely.  Their source and
  exclusion reason are retained in the primary database, but no MBOX copy is
  created.
* An MBOX record whose envelope sender is exactly
  `mbcp@s.eecs.harvard.edu`, whose only headers are `X-UID`, `Status`, and
  `X-MBCP-Flags` (with both X-headers present), and whose body is empty is
  source metadata, not an email. Record a `source-metadata-excluded`
  observation and do not publish it. For double framing, apply this check to the
  selected envelope and original headers/body after the quoted delimiter; ignore
  only the generated `X-From:` field, never an original header or body text.
  An envelope sender of `XXX` is unwrapped
  only when the outer record has a nonempty, well-formed status-only header block and
  its body starts with a quoted nested delimiter with complete ctime syntax. Indented or unquoted body
  lines are never nested delimiters. Retain the outer source offset as provenance.
* A framing line copied into an RFC header must start with literal `From ` and
  contain exactly one LF- or CRLF-terminated line. Malformed outer framing is
  left unnormalized, including in the legacy status-wrapper path, and remains
  subject to normal import validation.
* Double processing is recognized when the first payload line, immediately
  after a physical MBOX delimiter, is itself a `>From ` delimiter with a sender
  and ctime-style timestamp. Keep the outer delimiter and convert the quoted
  line to a literal `X-From: sender timestamp` header. If the outer sender is
  exactly `XXX` or `???@???` and the inner sender is neither placeholder,
  instead promote the inner delimiter and convert the displaced outer line to
  `X-From:`. Real local senders, including `nobody` and `MAILER-DAEMON`, are not
  automatically bogus. Retain envelope values/line endings and all following
  message headers and body bytes, including body quoting. This explicitly
  authorized framing normalization changes canonical message bytes: canonical
  hashes describe the normalized message, while observation detail records the
  original source-payload SHA-256, exact framing bytes and rule in a versioned record.
  Do not recursively remove quotation levels or infer a producer from branding.
  Do not search later header/body lines, accept indentation, or use timestamp
  differences to infer wrapping. See [MBOX_READING.md](MBOX_READING.md) for the
  Procmail/formail, MIMEDefang and Eudora compatibility boundary.
* Each finished `data/mbox/NAME.mbox` has one
  `integrity/NAME.mbox.integrity` BagIt tag in the versioned hybrid format
  specified by [INTEGRITY_CONTROLS.md](INTEGRITY_CONTROLS.md).
  Its JSON control records declare `h1` as SHA-256 over the complete MBOX,
  `h2` as SHA-256 over each recovered original RFC 5322 message, and `h3` as
  SHA-256 over semantic-message standard version 1. Its TSV region records
  ordered message identifiers and tagged `h2:` and `h3:` digests.
* `manifest-sha256.txt` lists every payload file exactly once. Its MBOX digest
  equals that file's `h1` digest. `tagmanifest-sha256.txt` hashes the BagIt and
  Mailbag metadata, every integrity tag, the payload manifest, and the
  installed validator; it is published last.
* `mailbag.csv` has one row per canonical message and uses a stable,
  case-insensitively unique Mailbag Message ID derived from the normalized
  Message-ID and raw SHA-256. It records the containing MBOX and MIME attachment
  count. Archives over 100,000 messages use the Mailbag-required numbered CSV
  parts.
* A source message retains the SHA-256 identity of its original bytes even when
  it lacks a final line break. Because the standard MBOX writer adds a final
  line break when one is absent, direct retrieval and independent verification
  consider both representations and accept only the one matching that original
  SHA-256. This includes the zero-byte-message case.
* Because the standard-library MBOX writer does not distinguish an escaped
  source `From ` line from an original literal `>From ` line, direct retrieval
  considers the bounded possible interpretations and accepts only the one whose
  SHA-256 matches the catalogued original bytes.
* A source message may itself begin with an MBOX-style `From ` line, notably in
  an Emacs RMAIL Babyl original-header block. The standard-library writer uses
  that line as the canonical MBOX record separator. Direct retrieval therefore
  considers both payload-only and separator-plus-payload interpretations, with
  the catalogued original SHA-256 selecting the source representation.

`archive.sqlite3` and the disposable `search.sqlite3` are operational BagIt
tag files but are deliberately not listed in the tag manifest; their live
SQLite state is outside the portable preservation checkpoint. An optional
archive-local copy of geographic reference data is also operational metadata;
it is explicitly copied by the user and is never refreshed implicitly.
The top-level `status/` directory likewise contains operational, unmanifested
JSON tag files. Each ingest creates a distinct file and atomically replaces
only that file with its current typed status; the final replacement retains
the run's complete statistics as append-by-run history.

The archive lives on the encrypted laptop filesystem.  BorgBackup and
Backblaze provide independent backup; archive-internal encryption is not a
requirement.

Each new BagIt checkpoint records the installed application version as
`Mailbag-Agent-Version`. Existing historical bags are not rewritten merely
because a new software version is installed.

Read-only data-quality audit tools may create derived MBOX, CSV, and JSON
evidence from a source tree and canonical archive. Those outputs contain
private message content and metadata, must default to an ignored temporary
directory, and must never be committed. The tools must refuse to overwrite
existing evidence and must not modify source mail or the canonical archive.

## Standalone printed-email PDFs

A standalone PDF containing scans of printed email is a source document, not
an attachment and not preserved RFC 5322 bytes. It is distinct from a PDF MIME
part inside a canonical message and from OCR or quoted-message text contained
in an actual message body.

* Extraction never rewrites the PDF. It records the complete PDF SHA-256,
  byte length, page count, exact source page for every derived message, the
  extraction and segmentation policies, and every page classified as
  message or non-message.
* Page text is an interchangeable input to segmentation. Native PDF text,
  local OCR, cloud OCR, and human transcription are versioned candidates;
  the message extractor must not depend on one OCR engine.
* Standard derived MBOX is the first validation and interchange format. Its
  records use synthetic identities and explicit `X-Mailarchiver-*` provenance,
  transcription status, PDF/page, policy, and handwriting declarations.
* A generated MBOX is written atomically and never over an existing file.
  It is not placed under canonical `data/mbox/`. Planned archive integration
  preserves source PDFs under `data/pdf/` and writes their reproducible
  `Archive-PDF` and `Sent-PDF` MBOX interpretations under `data/pdf-mbox/`.
* Handwritten annotations are recorded as a boolean page/message fact but
  their text is not indexed. Human review changes transcription status and
  corrected derived text without changing or obscuring the machine extract.
* Derived messages will appear immediately in ordinary search with a textual
  PDF badge and a color distinction. Every result opens its extracted text and
  exact source PDF page. Color must not be the only distinction.
* When normalized Date, Subject, To, and From are all present and identical to
  canonical email, the derived hit may be suppressed but remains stored and
  linked to its PDF pages. Edit-distance similarity creates only a suspected
  duplicate relation and does not suppress or merge the record.

## Deduplication and provenance

On macOS, Command-Q during an active import must offer Cancel or Stop Import
and Quit. Explain that quitting stops imports, that restarting requires File →
Import with the same source, and that already archived messages are not imported
twice. Cancel leaves imports running. Confirmed quit stops all active imports
cooperatively, disallows new imports, and waits for checkpointing and writer-lease
release before terminating. Window-close restrictions during ingest remain intact.

The default pytest suite must import the entire local `tests/data/` directory
through the CLI into a disposable archive, with a ten-minute subprocess
deadline. A reviewed expectation file lists source fingerprints and every
retained email's subject and raw SHA-256, plus observation/exclusion counts.
Failures report both unexpected and missing messages. Verification must read
canonical bytes, run the installed validator, confirm source immutability, and
repeat ingest to establish idempotence. Updating expectations requires the
explicit `--update-corpus-expectations` pytest option. Tracked fixture expectations
are public; ignored local mailbox expectations remain in an ignored local
overlay. Unknown local files fail comparison until explicitly reviewed.
This test uses the real on-demand ClamAV path, as the GUI does. Its subject/hash
expectations include quarantined mail and do not depend on signature-dependent
mailbox destinations. Dedicated EICAR tests also verify infected routing.

* A duplicate is only a message with both the same normalized `Message-ID`
  and the same SHA-256 of its RFC 5322 message bytes.  Never collapse all
  messages sharing only a Message-ID.
* Messages missing Message-ID are retained and identified by their SHA-256;
  they are reported as metadata exceptions.
* A **source** is where mail was found; an **archive mailbox** is where this
  program saved a deduplicated canonical copy. Every source observation links
  to one source file and source volume, records its source/forensic path,
  offset where applicable, ingest run, raw RFC 5322 SHA-256, semantic (`h3`)
  SHA-256, and disposition (`archived`, `duplicate`, `autosave-excluded`,
  `source-metadata-excluded`, or error). One archived message can retain many
  source observations.
* A source volume has a stable identity plus normalized JSON metadata. Local
  ingest records the complete OS volume report available at ingest time,
  including label, format information when available, and current mount path.
  Cloud adapters use a provider/account origin identity plus per-container
  native ID, hierarchy, display name, and non-secret provider JSON.
  A local provider cache records a typed `cache` relationship, the upstream
  provider kind, and any non-secret local account hint while retaining its
  local volume and physical path. If the same canonical message also has a
  direct cloud-provider observation, provenance displays that direct source
  first and identifies the local observation as a retained cache copy.
  Source evidence is retained in the private archive catalog and excluded from
  redacted or public derivative packages by default.
* Idempotence is at message level.  After a raw message hash and Message-ID
  have been obtained, a matching stored identity is skipped before ClamAV,
  text extraction, and MBOX writing.  A deliberate rescan/reindex mode is
  separate from normal ingest. Repeated ingest may add newly available source
  messages. A byte-identical message found in a backup, provider export, and
  local cache has one canonical record with multiple observations. A record
  that differs in raw RFC 5322 bytes is preserved even when its semantic `h3`
  digest matches; semantic identity is reconciliation evidence, not an
  admission-time discard rule.
* Every source plug-in declares source integrity controls appropriate to its
  source. The framework executes those controls, displays their progress, and
  persists typed evidence and resume decisions. Cryptographic hashes,
  provider version tokens, immutable identifiers, and cursors are distinct
  evidence kinds and must not be mislabeled. Source integrity controls are
  separate from the canonical archive controls in `INTEGRITY_CONTROLS.md`.
  Integrity-control generators run outside the publisher lock so source
  hashing and provider I/O do not serialize unrelated workers, and their typed
  progress is forwarded as yielded. Only the catalog transaction that records
  validated evidence and marks the checkpoint complete is publisher-serialized.
* Every recognized local source file records its absolute path, nanosecond
  modification time, byte length, complete-file SHA-256, last check time, and
  completing ingest run. Those file columns are a local display cache; skip
  and resume decisions use only typed evidence from the last completed
  `source_integrity_checks` record. Reingest always verifies content: a matching full
  SHA-256 skips the file without parsing its messages.  If a file grew, ingest
  first compares the SHA-256 of the old length.  A matching MBOX prefix followed
  by a valid `From ` boundary resumes at that byte offset; all other changes
  reprocess the whole file.  The checkpoint is updated only after the selected
  region finishes and the source metadata remains stable.
  A lightweight preliminary pass counts recognized source containers and their
  available byte estimates without hashing or retaining message contents. It
  stores typed container metadata in a temporary SQLite work snapshot, prints
  every unrecognized regular file once with its path and reason, and finishes
  before any message is published. Zero-length files and paths matching the
  commented, case-insensitive globs in packaged `local_source_rules.yaml` are
  silently omitted from discovery and the unrecognized-file count. The same
  versioned YAML stores local file-probe and MBOX preamble limits and is
  strictly validated before discovery.
  Duplicate scoped container identities are
  scheduled once; conflicting definitions fail. Sources declaring stable
  inventory are verified by a second preflight discovery; live sources are
  discovered once. Worker execution is bounded by the configured count: each
  worker plans, reads, parses, scans, and checkpoints one container at a time. A new
  file's complete fingerprint is calculated before its checkpoint is committed. A later source failure or
  interruption retains committed messages; any in-flight file without a
  checkpoint remains safely rerunnable.
* Emacs RMAIL Babyl files are recognized from their case-insensitive
  `BABYL OPTIONS:` header rather than a filename extension. Both LF and CRLF
  containers are streamed read-only. Each record's original header block and
  body become one RFC 5322 message. If the original-header block is empty, the
  visible headers are the record's only headers and are used instead. Babyl
  labels and redundant visible-header copies are source-container metadata and
  are not added to the canonical message. A zero-record container ending with
  the Babyl `0x1f` end marker is a valid empty mailbox and completes normally;
  a container reaching EOF without either a record or end marker is truncated
  and fails.
* Source and physical file parsing use separate versioned, immutable plug-in
  registries. The production file registry contains MBOX, Babyl, EMLX, and
  single-message EML/Maildir generators. The production local source generator
  delegates each recognized filename to that registry. When more than one
  packaged parser recognizes a file, manifest priority selects the format;
  EMLX, Babyl, MBOX, then single-message precedence ensures that MBOX framing
  takes precedence over an enclosing Maildir `cur` or `new` path. Any
  recognition overlap involving an external parser remains fatal. Packaged
  reserved source stubs name Gmail, IMAP, O365, Microsoft Exchange, and standard input;
  the standard-input contract is RFC 5322 messages separated by a NUL byte.
  Reserved stubs must fail clearly and must not be exposed as working ingest.
* Built-in and explicitly configured trusted plug-in directories use API-v1
  manifests. All manifests are validated before any external Python is
  imported; duplicate kinds, incompatible APIs, unsafe entrypoints, ambiguous
  source selection, and external file-recognition ambiguity fail before
  workers start.
  Plug-in registries are frozen before inventory. Mail source trees and the
  archive are never searched for executable plug-ins. Typed boundaries are
  strict: in particular, text cannot be coerced into RFC 5322 bytes.

## Malware handling

* Each new message is streamed to ClamAV unless the user explicitly chooses
  an unscanned import. Missing or failed scanning must never silently mean clean.
  The CLI requires exactly one of `--clamav` or `--no-scan`; the latter records
  `not-scanned` in run status and an antivirus metadata defect on each new message.
  Earlier run status without this field is unknown, not presumed scanned.
  Repeat imports do not retroactively scan previously archived messages.
* The `--clamav` switch starts one
  foreground `clamd` on the main ingest thread when the configured local socket
  is not healthy, waits for a successful health probe before starting mailfile
  workers, reuses a healthy existing daemon without stopping it, and never
  enables on-access or scheduled scanning. A daemon started by mailarchiver
  must capture its output in a verified, mode-`0600` log in a unique
  mode-`0700` per-run directory and remove the installed configuration's
  `LogFile`, `LogSyslog`, and `PidFile`; the owned foreground subprocess needs
  no PID file. Mailarchiver-owned daemons sharing one configured `LocalSocket`
  must be serialized by an advisory lock held for the daemon's complete
  lifetime. A healthy external daemon is reused without holding that lock or
  stopping or unlinking its socket. Owned private files are removed after the
  daemon stops.
* `ingest --workers N` controls the number of source containers ingested
  simultaneously. Its default is the detected CPU count capped at eight, and
  `N` must be positive. Each worker reads and parses its mailfile and submits
  one ClamAV request at a time. Duplicate admission, SQLite commits, the
  publication journal, and canonical MBOX appends remain serialized through
  one publisher. A source plug-in may declare a lower concurrency limit for
  each source-native `concurrency_key`; the framework enforces it and fairly
  interleaves captured keys. A shared plug-in instance must be reentrant. A
  resume decision is accepted only from a source declaring resumable support.
* Mailfile workers send typed status updates to a main-thread status driver;
  worker threads never write progress output. The driver writes standard error
  at startup, every 250 milliseconds, and completion. An interactive terminal
  receives a redraw-in-place scoreboard with a numbered row for every
  configured worker and a white-on-blue top line reporting aggregate byte and file
  percentage and ETA; redirected output reports the same aggregate fields in
  line-oriented text without terminal controls. Both report the main-thread
  `waiting for ClamAV startup` preflight and worker phases including `checking`,
  `ingesting`, `scanning`, `waiting to publish`, `publishing`, `checkpointing`,
  and `idle`. They also report elapsed time, processed message and completed
  source-file counts, average message rate, resolved date range, current
  year/current-year count, active and peak worker counts, source file, and byte
  or provider-message progress. Unknown-byte sources use completed containers
  rather than reporting 100% prematurely. Lines must be fitted to the terminal width so redraws
  never accumulate wrapped headings. It separately reports archived, duplicate
  previously-seen duplicates, autosave-excluded, source-metadata-excluded,
  infected, unrecognized-file, and integrity-skipped-container counts. A
  worker never prints a skipped-container diagnostic itself; the main status
  driver prints each queued path and reason once.
  While the main thread waits for ClamAV to load virus definitions, every
  refresh explicitly identifies that wait and shows its increasing startup
  elapsed time instead of a stale source-file status. A newly started daemon is
  ready only after the configured scanner health probe succeeds, not merely when
  its socket appears. Every scanner health-check subprocess has a five-second
  caller-enforced deadline, and every message scan has a five-minute deadline.
  A missing, non-executable, or otherwise unlaunchable health-check helper
  means the scanner is unavailable, not a missing mail source or a clean scan.
  Execution failures must abort startup before removing an existing daemon
  socket or launching a new daemon, and release the startup lock.
  A timeout is a scanner failure, never a clean or infected result, and plaintext
  temporary message bytes are removed after every outcome.
* Control-C is a graceful stop: close scanner and MBOX resources, commit
  completed messages and observations, publish a complete BagIt/Mailbag
  checkpoint, report interruption,
  print the standard archive report for the completed partial run, and return
  exit status 130 without a traceback.  An `ENOSPC` MBOX append
  must be rolled back to the prior file size where possible, reported, and
  stopped without silently treating the message as archived.
* Every ingest run records its completion time, result, and failure detail.
  Failure detail retains traceback filenames and source-code line numbers,
  including underlying Pydantic validator exceptions. For an available message,
  include its source reference, neutrally labelled native cursor (byte offset for
  local MBOX), SHA-256,
  length, and an escaped prefix of up to 4,096 input bytes plus up to 512 bytes
  per selected/original/quoted envelope. Limit each source identity field and
  cursor to 1,024 characters plus a truncation marker; omit arbitrary source
  provenance and hierarchy from failure reports. Limit exception summaries to
  2,048 characters, each exception note to 32,768 characters, and the complete
  rendered failure to 65,536 characters, plus truncation markers. Pydantic summaries and chained tracebacks omit input-value dumps so
  they cannot bypass these preview limits. These local diagnostics may contain
  private mail; they are not public telemetry. A failure between messages identifies the container without
  attributing it to the previously yielded message. Persist the same detail in
  the run catalog and Ingests status, without changing source or canonical mail.
  An unexpected parser failure preserves earlier published messages, closes
  resources, refreshes the BagIt/Mailbag checkpoint for committed MBOX changes, and leaves a
  rerunnable error observation containing the source cursor (and numeric offset
  when available) plus raw SHA-256.
* The main status driver writes the same typed state used by terminal rendering
  to one run-specific `status/ingest-*.json` file. The JSON contract records a
  format/version identifier, archive and run identity, process and source
  roots, timestamps, state, phase, aggregate progress, dates, rates,
  disposition totals, and every configured worker's current and cumulative
  statistics. Updates use same-directory atomic replacement. A later ingest
  creates a new file and never replaces an earlier run's final status.
* Before each MBOX append, ingest durably records the target, prior length,
  message identity, and whether the target already existed. Catalog changes
  remain uncommitted until the append is complete. On an exception or the next
  startup after process death, an uncatalogued append is removed; a catalogued
  append is hash-validated, retained, and its journal is cleared.
* Completed and interrupted ingests print the archive report.  Reports always
  include year totals and default to the top 10 senders and recipients for the
  selected scope.  All report sections are aligned tables with right-aligned,
  comma-grouped numeric columns. Correspondent tables include the first and
  last message dates for each address. Addresses identified by Sent classification are suppressed
  from these correspondent lists only; they remain in the catalog and yearly
  people counts.  `--top 0` suppresses correspondent lists.
  Year ranges must be ascending and correspondent limits must be nonnegative.
* ClamAV outcomes are `clean`, `infected`, `unscannable`, or `scanner-error`,
  with scanner version, signature database version, and diagnostic retained.
* Only a positive detection routes a message to `INFECTED1.mbox`; an
  unscannable attachment or scanner failure does not destroy or silently
  exclude a message.
* Infected and malformed quarantine mail remains catalogued but is excluded
  from the disposable search index, ordinary search listings, reports, and
  correspondent statistics. Its canonical MBOX content remains available for
  an explicit future quarantine-review workflow.

## Primary metadata database

`archive.sqlite3` is authoritative metadata, not the authoritative message
content.  It tracks at least:

* logical message identity: raw and normalized Message-ID, message SHA-256,
  date and date source, category, byte length, ClamAV result;
* parsed sender and recipient identity foreign keys, and decoded/unfolded
  Subject headers; sender identity uses a valid `From:` address, then a valid
  `From:` inside a quoted nested-MBOX record, then a valid RFC `Sender:`
  address. A narrowly recognized Google Chat event with none of these
  uses its embedded full name suffixed by `(Google Chat)`; all other missing
  senders remain empty in metadata and display as `(missing sender)` in reports.
  Every parser-provided header value is normalized to text,
  malformed fields fall back independently without affecting preservation,
  and their exception types and diagnostics are recorded;
* each final MBOX filename, message byte offset, byte length, and archive
  generation; and
* sources, ingest runs, exclusions, duplicates, validation results, and
  integrity metadata. Source volumes are distinct from source files: many
  paths may be found on one volume, and observations connect those paths to
  logical archive messages.

Logical messages and physical locations are separate relations.  MBOX offsets
are generation-specific and must be replaced atomically when a file is
sorted or repacked.

Email address text is normalized into `email_addresses(address_pk, address)`.
`messages.sender_address_pk` and `recipients.address_pk` reference that table;
`recipients.role` retains To, Cc, or Bcc while header order is not retained. The address
table also stores explicitly labeled non-email Google Chat identities.

## Contacts and geographic reference data

The CLI and policy below are implemented; the Contacts window and geography
features remain planned.

Contacts are address-level records derived from `From`, `To`, `Cc`, and `Bcc`
headers; they are not authoritative People records. Each address occurs at
most once per message in all-header counts and date ranges. The Contacts view
shall offer a default-checked **Meaningful** filter. Meaningful means a direct
To or outgoing Bcc recipient of owner-sent mail, or an incoming sender where an
exact configured owner address occurs in `To`. Cc recipients are not
meaningful; multiple To recipients are. A mailing-list message counts only
when that direct-owner-in-To condition is met.
The include/exclude owner rules classify ingest; a separate reviewed exact
owner-address set for contact reconciliation remains planned;
meaningful-contact semantics use those exact addresses, never a name fragment.
The read-only `human-contacts` command shall provide this initial address-level
projection in table, TSV, and JSON forms before the Contacts window exists.
It opens the catalog read-only using a platform-correct file URI, including archive
paths with spaces, Unicode, and URI-sensitive characters; it never creates a
missing catalog. It shall accept a reusable owner-alias file whose values are separated by newlines,
commas, or semicolons; blank lines and comment lines are ignored. Aliases resolve
only to catalogued Sent sender addresses, and those resulting exact addresses
drive the meaningful-contact predicate.
It shall suppress mailing-list, automated-service, and malformed identities
using a versioned, explainable packaged policy. The human-contact local-part
limit is 48 characters and is configurable; the RFC address limit is not itself
a claim that every shorter address is human.
Classification reasons distinguish invalid-domain rules from invalid-local-part
rules; when both match, the local-part reason takes precedence. An empty domain
is `invalid-domain`, after evaluating local-part rules.
The packaged `contact_filters.yaml` may be copied to an archive root. Its
required `mode` is `replace` for a complete replacement policy or `extend` to
add only rule lists to the packaged policy. Extension preserves packaged order,
appends new rules, and removes duplicates; scalar thresholds remain packaged.
A malformed archive copy shall fail the command rather than silently changing
its classification. Validate every regex when the policy loads, including unused
rules and empty catalogs. YAML and regex errors identify the policy file and
offending rule without a traceback, partial output, or archive writes.

Geographic evidence shall preserve source, observation date, confidence, and
whether it is **located** (contact-specific evidence, such as a signature) or
**affiliated** (an institutional/domain relationship). Affiliation shall not
be presented as a person's location. The initial United States lookup accepts
a five-digit ZCTA and displays its city, state, and country. Radius lookup is
straight-line distance from representative latitude/longitude.

The installed application shall include a seed US ZCTA reference database with
representative latitude/longitude, city, state, and country. `make
geography-data` and the future **Tools → Update Geo Database** command shall
use the same verified bulk-data update path. No public per-contact geocoding or
domain lookup is permitted in this phase. Installation-level geography data is
per-user, not per archive; an archive snapshot may be copied or read only
through an explicit user action. See
[CONTACTS_AND_GEOGRAPHY.md](CONTACTS_AND_GEOGRAPHY.md).

## Search database

`search.sqlite3` is a separate, disposable SQLite FTS5 database.  It indexes
normal Sent and Archive message SHA-256, normalized headers, `text/plain` body text when present,
otherwise rendered `text/html`, otherwise safe single-part message text.  It
also maintains a replaceable trigram index for email-address substring
completion; ordinary mapping rows provide deduplicated message counts and
last-seen dates. Display
names are retained as suggestion metadata but are not trigram-indexed. Subject
completion reads the canonical subject column and requires no second copy.
It parses XML-looking content declared as `text/html` with the same forgiving HTML
rules without emitting parser diagnostics. Body/header text and attachment
text occupy separate FTS5 tables so callers can exclude attachments from the
default search. It does not index attachment bytes by default.
`--index-attachments` populates the separate table with decoded text
attachments. Binary attachment extraction through Apache Tika is not yet
implemented; the optional installer downloads, SHA-512 verifies, and unpacks
the complete Tika 4 `tika-app` distribution (JAR plus `lib/` directory).
It rejects checksum metadata unless its first token is exactly 128 hexadecimal
characters, and removes the private temporary extraction directory after any
failed extraction, layout validation, or rename.
The search database must be fully rebuildable from the
canonical MBOX files and `archive.sqlite3`, and is not backed up as a required
preservation object.
It excludes `INFECTED` and `MALFORMED` quarantine categories even when
attachment indexing is requested. `refresh-index` applies the same exclusion
when rebuilding from canonical MBOX files.
Text extraction must decode MIME payload bytes before introducing replacement
characters. A valid declared charset, including legacy labels such as
`ks_c_5601-1987`, takes precedence. If that declaration is absent, unknown, or
cannot decode the bytes, extraction may choose a bounded, quality-scored
candidate from common legacy encodings using `charset-normalizer`, with detector
ordering retained ahead of universal single-byte fallbacks. Candidate quality
is scored only on a bounded sample, which is reused for candidate discovery;
the complete part is strictly decoded only after ranking. Only a final
unrecoverable fallback may use UTF-8 replacement. `ftfy` encoding repair is
applied to already-decoded derived text to correct clear mojibake. These
repairs affect only the disposable index and display; the original RFC 5322
bytes, MIME headers, and raw-message hash remain unchanged. Recovery is
per-part and automatic; a count threshold for `U+FFFD` is not a safe trigger
because replacement has already discarded the evidence needed for recovery.
Malformed legacy headers that contain raw 8-bit bytes instead of RFC 2047
encoded words use the declared body charset through that same recovery path for
derived catalog, search, and viewer text; canonical header bytes remain exact.
For every indexed message, the disposable database records an attachment count
and one ordered metadata row per MIME attachment containing its MIME-walk part
ID, decoded filename, and normalized MIME type. This metadata is derived during
the same MIME parse as body indexing and is rebuilt by `refresh-index`; it does
not make attachment payload bytes canonical database content.
The message metadata also stores a deterministic, whitespace-collapsed preview
of the first 18 words of the preferred non-attachment body. An ellipsis marks a
truncated body.
The SHA-256 primary key in ordinary message metadata maps each message to its
message-body and optional attachment FTS row IDs. Updating or removing indexed
content must resolve SHA-256 through that ordinary index and address FTS rows
by row ID; it must never scan an FTS table by its unindexed SHA-256 column.
The canonical catalog separately indexes message SHA-256 for FTS-to-message
lookups. Unfiltered bounded date-ordered listings must use the date/message index to
select the requested page before recipient aggregation. Year-scoped reports
must express their bounds as indexed `date_utc` ranges rather than applying a
function to every stored date.
This is an archive search system, not a mail client. A GUI query must search the
complete selected collection without favoring recent messages or requiring an
archivist to request older results. The GUI directly materializes the first
2,000 headers in the selected stable order; it does not run a pre-count query,
because that would delay the first visible results and SQLite FTS5 has no
reliable approximate cardinality for arbitrary full-text/filter combinations.
When more rows exist, the GUI immediately displays that ordered prefix,
automatically retrieves the complementary remainder in the background, and
then displays the complete result set and exact count. During that continuation,
the result status is red and says **Searching in background** with the displayed
count. A newer
query supersedes the continuation; a failure retains the first 2,000 results
and reports that the background search failed. The GUI has no **Find older
ones**, **Load more**, or **Load all** interaction. Limiting an unordered FTS
hit list is forbidden because it can omit results required by the selected
ordering. Submitting an empty query displays no results and prompts for a
search. The empty result pane at application startup and after an empty query
must show the complete search language, AND and phrase behavior, every
supported operator, and concrete examples.
Index extraction and insertion happen after canonical MBOX/catalog publication.
An indexing failure is recorded as a metadata defect and does not reject mail;
`refresh-index` repairs missing disposable content.

## Desktop application documents and windows

The compiled desktop experience will evaluate Dioxus Desktop and Tauri with the
system webview (WKWebView on macOS, WebView2 on Windows). Rust work is limited to the desktop/UI layer;
ingest, search, scanner orchestration, archive locking, and preservation remain
in Python. Windows with full ingest is the next platform priority; Linux/snap
delivery is deferred. [DIOXUS.md](DIOXUS.md) defines the trial plan and migration,
typed local worker boundary, packaging, and native acceptance requirements.
The worker protocol and both candidate frontends remain unimplemented. End users must
receive the compiled UI and bundled Python dependencies together.

Plan comparable trial implementations in Dioxus and Tauri before choosing a
framework. Either approach retains the Python archive engine.

The current pywebview application uses HTML/CSS/JavaScript and its native Python
bridge. The following asset-server rules describe that implementation and its
security baseline; they do not prescribe the unimplemented worker transport.
An application-owned HTTP server binds only to
`127.0.0.1` on an ephemeral port and serves only packaged GUI assets. Each
window starts with an unlogged, one-use cryptographic nonce that establishes a
session-only `HttpOnly`, `SameSite=Strict` cookie and redirects to a clean URL;
every asset request requires that cookie and the exact loopback host, and any
request carrying an `Origin` header must name that exact origin.
Bootstrap redirects explicitly declare an empty body. Asset HEAD responses
report the GET content length without reading or returning the asset body.
The server sends no permissive CORS response and stops with the application.
Any future state-changing HTTP API must additionally require an explicit CSRF
header. The applications must be packaged
with Python and all application dependencies. Users must not need Python,
`pip`, `pipx`, `uv`, or a source checkout.

An `ApplicationController` owns application preferences, archive-document
sessions, active-window routing, recent archives, and operating-system open
and reopen events. Native menu callbacks resolve the active window when they
are invoked. Discardable, versioned preferences are outside every archive in
the platform-appropriate per-user application directory. They contain the
last archive and at most ten recent archive paths, but no archive content or
credentials.

An `ArchiveDocument` represents one archive. Opening validates the directory
and the versioned layout and SQLite readable state of both databases without
creating or modifying anything. A missing or invalid saved archive is removed
from recent preferences and reported in the persistent About window. The document
retains the user's absolute display path and also uses a canonical,
filesystem device/inode pair as its process-local identity. Windows opened through
aliases of the same archive share the document's ingest state, child windows,
and publication generation. The document remains alive while any search
window, child window, or ingest operation uses it.

Every search window has a lifetime-stable document binding and independent
query, sort, selection, mailbox-filter, and geometry state. **New Search
Window** in the **Window** menu attaches another window to the active document;
it is disabled when no saved archive search window is active. **Open** and an
operating-system open event create a window for the requested document; they
must not silently retarget an existing search window.

Startup opens explicit document paths first, otherwise the last valid archive.
When neither is available, show a single setup window with all three numbered
boxes visible: **1. Select the root folder to ingest**, **2. Select where your
archive is stored**, and **3. Start import**. Each folder button opens a native
folder browser and displays its accepted path in a selectable, read-only text
field. Reopening a picker starts at the previous choice; cancel preserves that
choice. The archive may be an existing valid archive or a pre-created empty
directory outside the source. macOS setup pickers must disable New Folder,
including before source selection, so browsing cannot create input directories.
No archive is initialized by
selection alone. Disable Start import until both folders are selected and while
any setup dialog is pending. Reject equal or nested source/destination folders,
including symlink, Unicode, and case aliases, before creating or opening a
destination. Start
import reuses the selected root without asking for it again, retains owner email rule
setup and antivirus confirmation, and opens the Ingests progress window after
starting the worker. Cancellation of import settings keeps the setup choices available for retry.
A **Cancel** button beside Start import (also Escape) quits the application.
The Cancel action itself must not create an archive, start import, or write
preferences. It does not undo earlier writes: normal startup may already have
removed a missing or invalid remembered archive from saved preferences before
showing setup.
If another window is importing, use the normal Stop Import and Quit confirmation
and retain its writer lease until checkpoint completion. It and native File → Close are disabled while a setup operation or dialog is pending.
The Close lock applies globally, including Cancel and native modal focus falling
back to an existing search window; a queued Close action must also refuse closure.
Folder pickers must clear any warning accessory left by a previous import dialog.
Cancel must atomically reserve a job-free quit against import publication; if a
job wins that race, present the normal stop confirmation and wait for its checkpoint.

When launched through `mailsearch-gui`, macOS must not reinterpret the Python
launcher or command-line option values as documents. Explicit `--archive`
handling and genuine Finder document-open events remain supported. Configure
this behavior for the running process without writing system or user defaults.

`mailsearch-gui --new` forces this setup for one launch, bypassing both remembered
and environment-selected archives without clearing preferences or altering old
archives. `--new` and `--archive` are mutually exclusive. On macOS, holding
Option (Alt) while launching the app invokes the same setup; keep it held until
the window appears. Option-clicking its running Dock icon also opens setup.
Only one setup window is shown per process. A missing or invalid last archive
is removed from recent state and reported, never recreated. Closing setup
leaves About and File New/Open available. New asks for a permanent destination
before opening a search window, then offers Import. Accepting the default
Untitled name must work. Native save results may be strings or path sequences;
neither form may truncate the path. File New/Open/Close have Command-N/O/W
shortcuts on macOS. Search windows have no Open Archive toolbar button.
The archive path appears in the native title bar, without a duplicate toolbar label.
The native menu order is Application, File, Edit, View, Window. Existing archive
directories do not require an extension. Opening checks SQLite schema and layout
read-only, without scanning every database page; this is not a full corruption
audit. Open failures appear in About and stderr even if a document cannot open.

The About window is retained hidden at startup and opens through the application
menu. Closing it dismisses it
until the user chooses the application menu's About command; status updates and
Dock activation must not reopen it. Closing the last visible window keeps the
application running with About and File New/Open available. It displays the installed version, current
free space on the active archive's filesystem (or the user's home filesystem),
live Internet reachability, startup errors, warnings, and each open archive's
latest ingest status.
About populates through the real native status bridge and polls once per second.
Its script policy must permit pywebview's dynamic bridge functions, as the search
and ingest pages do. A delayed bridge retries and clears its warning on recovery.
Only explicitly selected window methods are exposed to JavaScript; application
controllers, documents, and native window objects must not be recursively exposed.

**File → New** asks for a new or empty permanent `.mailarchive` destination
before creating its blank search window. It initializes BagIt, both databases,
and operational status state, and refuses to overwrite an existing archive or
nonempty invalid directory. **File → Import…** collects one or more supported
local files or directories, owner names, explicit final
confirmation, and starts the same typed ingest service used by the CLI on a
worker thread. ClamAV is optional and separately installed. Missing executable
or configuration files produce a warning banner in the Ingests window and
macOS source picker. Final confirmation defaults to Cancel and offers
Import Without Scanning or Install ClamAV; the latter opens the official
download page, without installing software or starting a persistent service.
Configured scanners must pass the existing startup check; errors stop import,
never silently switch to unscanned mode. About displays scanner configuration
availability, and import history retains a visible unscanned warning.
The Ingests window provides **Import Directory…**, bound to its own archive even
when another archive is active. It opens the source picker directly, then
uses the same owner-names setup, confirmation, and writer lease as File Import.
The button is disabled during an import or while its dialogs are pending.
On macOS, one source picker accepts files and directories together with an
**Import** action, without a preliminary source-type question. Its title names
the destination archive and its message shows the full destination path.
Selecting an entire directory, including the currently displayed directory,
uses recursive discovery. Cancel dismisses the picker without starting ingest.
Final confirmation shows the Email Collection Toolkit icon and destination heading/path.
Both scanned and explicitly unscanned import confirmations use a 560-point-wide,
selectable message area so archive and source paths need less wrapping, while
retaining their existing buttons and keyboard defaults.
Every import shows two multiline fields: **Owner emails (include)** and
**Exclude (applied after include)**. Newlines or commas separate rules; blank
and comment lines are ignored. At least one include rule is required to import.
The fields default to the archive's saved `config.yaml` owner lists. Until YAML
owner settings exist, legacy `owner-names.txt` in the archive and the top level
of selected source directories may seed the fields using the new exact/glob
semantics. No checkout or launch-directory defaults are used. A saved empty
list is intentional and must not resurrect legacy defaults. Invalid settings
are reported without overwriting them.
After final confirmation and acquisition of the writer lease, changed lists
are atomically saved in archive `config.yaml` as `owner.include` and
`owner.exclude`. Canceling either dialog saves no rules and starts no ingest.
A stale dialog must not overwrite settings changed since it opened. Source
files remain untouched. The packaged source rules continue to ignore a source
root's legacy `owner-names.txt`.

The version-2 archive `config.yaml` stores owner rules and the last source-picker
directory; version-1 navigation-only files remain readable. Successful imports
remember the source directory while preserving owner lists. Unchanged settings
are not rewritten. Missing configuration starts with defaults; malformed YAML
blocks editing/import with an error, since it may contain owner policy.
This file is operational and excluded from preservation tag manifests, but
should be retained when preparing a rebuild.

**File → Document Options…** opens one window per saved archive with the same
two rule fields and a **Save** button. Saving obtains the writer lease and
checks the content revision. Import blocks edits; background status refreshes
must preserve unsaved input. Ingest records its rules in
`status/owner-rules-used.yaml`. Options compares this snapshot with current
rules and identifies older imports whose rules were not recorded.
After a completed or orderly interrupted import, atomically regenerate
`owner-names-detected.txt` as sorted, unique matching archived sender addresses,
including senders from prior imports and applying exclusions. Detected addresses
are derived evidence, never expanded into YAML or reused as implicit owner rules.
Existing Sent/Archive classifications remain unchanged by rule edits or
reindexing; correcting historical misclassification requires a fresh rebuild.

Only a saved document holding the matching cross-process writer lease may
start ingest. The process-local document registry prevents duplicate UI jobs
but is not the operating-system lock. Different documents may ingest
independently. Successful publication increments the document generation and
identifies all attached search windows for refresh.
The OS lease is a nonblocking exclusive lock on
`status/archive-write.lock`; diagnostic JSON in that file records the operation,
PID, host, start time, version, and archive identity, but file presence never
determines ownership and a process crash releases the lock. CLI ingest and
search-index rebuild use the same lease. During Import, **File → Close** does
nothing for the owning search window and that window's close box is refused;
other search and child windows remain independently closeable. The Window menu
lists the About, search, and Ingests windows and brings a selected window forward.

`mailsearch` is a read-only command-line consumer of both databases.  It
accepts ordinary full-text terms plus `any:ADDRESS`, `from:ADDRESS`,
role-specific `to:ADDRESS`, `cc:ADDRESS`, and `bcc:ADDRESS`, `subject:TEXT`,
`date:YYYY-MM-DD`, `before:YYYY-MM-DD`, and
`after:YYYY-MM-DD` filters, intersecting every supplied term. `date:` selects
the specified UTC calendar day; `before:` and `after:` exclude the specified
day. Results default to ten
one-line headers, prefixed by the stable `messages.message_pk`; `--limit 0`
prints all matches.  Supplying one such number prints the original RFC 5322
message bytes from canonical MBOX storage.  The current implementation finds
that message through a catalogued generation-specific MBOX location and
verifies the recovered bytes against its SHA-256 before printing. Every ingest
records the location as part of the same publication as the message catalog row.
Within one result set, message numbers are right-aligned to that set's widest
number. Interactive terminals render subjects in ANSI bold; redirected output
contains no terminal control codes.

For a numbered message, the default display shows `To`, `From`, `Cc`,
`Subject`, and `Date` headers plus all non-attachment `text/plain` parts. If
no plain-text part exists, it renders non-attachment `text/html` parts to
plain text with Beautiful Soup. `--headers` includes every header; `--html`
prints decoded HTML parts; `--mime` prints the original RFC 5322/MIME source,
including all MIME parts and attachment encodings.

`mailsearch-gui` is a macOS-first, read-only pywebview consumer of the same
search and verified-message retrieval functions.  A single search field uses
the CLI selectors and ordinary ANDed terms; shell-style quotes group spaces,
so `subject:"annual report"` is one selector while `subject:annual report`
retains the CLI meaning of a subject selector plus a free-text term. A nonempty
query follows the complete-archive count and automatic result-loading contract
above. A newer request must supersede an in-progress background search, and the
status must show its accumulated result count while it runs.

GUI query syntax errors (empty selector values, invalid dates, and unclosed
quotes) must appear as inline search feedback without a Python bridge exception.
Search pages and counts must choose filtering indexes before ordering indexes:
address selectors resolve matching distinct addresses before looking up messages;
date selectors use indexed UTC ranges under every sort order; body/attachment
terms use FTS MATCH and catalog hash lookups; mailbox selections start with
indexed source paths/volumes and observations. Preserve role, AND semantics,
complete results, and stable ordering. Literal subject substrings may scan the
subject expression index, but must fetch full message rows only for matches.
Regression tests must compile typed queries through the production parser and
SQL builders, bind their unchanged parameters to `EXPLAIN QUERY PLAN`, and
check filtering searches rather than accepting any mention of an index. Execute
the same statements to verify results and bound work on sparse large fixtures.

After three characters and a 120-millisecond debounce, the GUI suggests at most
20 matching addresses and 20 matching subjects with deduplicated message
counts. Stale responses are discarded. Addresses rank by message count, then
most recent message date. Email-address substrings use the disposable
trigram accelerator; display-name and subject substring matching do not. Selecting an
address creates a removable filter whose menu scopes it to Any, From, To, Cc,
or Bcc; recipient roles are the original RFC header roles retained at ingest.
Selecting a subject creates a removable subject filter. The native window title
contains the active archive path and total deduplicated searchable-message
count.
Selecting a result shows it beside the list; double-clicking opens an
independent message window whose message pane scrolls through the complete
message, attachments, and source-location evidence. The result list can sort by date, subject, or
sender in either direction. When it has keyboard focus, Up Arrow and Down
Arrow move the selection and display the newly selected message. Result rows
show the indexed attachment count with a paperclip.
The single result column is not user-resizable. A draggable divider reallocates
width between the result list and preview without introducing a horizontal
result-list scrollbar or changing the current selection. It also supports
Left/Right Arrow (Shift for larger steps) and Home/End while focused, respects
minimum pane widths, and adapts to window resizing and the optional folder tree.
Each search window keeps its own split for its lifetime; standalone message
windows and printing have no divider. Browser acceptance tests exercise these
layout and selection invariants.
An unchecked **Search attachments** control searches only headers and message
bodies. When checked, the same ordinary full-text expression also matches the
separate indexed text-attachment table; metadata selectors retain their normal
meaning. The control does not extract attachment content on demand.
The GUI paints each result page from header metadata first, then requests its
indexed body previews on a background worker and fills a reserved third line
without blocking the initial result display. The Tabulator result table retains
complete result metadata client-side but uses its virtual DOM to paint only
visible rows; preview work is requested when a row is painted. Result rows
disable native text selection. A single selected row opens its message; modifier
clicks select multiple rows and replace the message with a selected-message count.
Dragging a selected row or the message-file icon uses the same export path: one
message becomes an `.eml`, and multiple messages become a ZIP whose entries
preserve their RFC 5322 bytes. Dragging an unselected row exports that row alone.
Message HTML links and recognized `http`, `https`, or `mailto` links in rendered
plain-text parts are never opened directly. Hovering an allowed destination
shows its complete destination in the bottom status bar. Clicking it presents
that destination with **Open Link**, **Copy Link**, and **Ignore** choices;
only **Open Link** invokes the system handler, and **Copy Link** writes the
destination as text and a URL to the pasteboard. Raw Source remains literal.
For a local file source whose stored volume-relative path begins `Users/`, the
viewer displays `/Users/...` so it remains an absolute filesystem path.
Literal, case-insensitive occurrences of every ordinary free-text query term
and every textual selector value (`any:`, `from:`, `to:`, `cc:`, `bcc:`, and
`subject:`) must be highlighted in the selected message's displayed headers and
body. Date-selector values do not create highlights.
Highlighting applies to plain text, sanitized HTML, and raw-source views without
changing canonical bytes or weakening the HTML sandbox. Its background color
comes from the strictly validated, versioned packaged `configuration.yaml`;
the initial value is yellow (`#fff59d`).
With a message selected, Command-F opens an in-message finder seeded with the
first textual archive-search term (for example, `from:beth` seeds `beth`) and
selects that term for replacement. The finder highlights its replacement text
in the displayed message; Command-G advances to the next match, and
Shift-Command-G moves to the previous match. Changing the selected message
retains the finder text but restarts at that message's first match, including
within a sanitized HTML part. The active HTML target must be visibly distinct
from ordinary archive-search highlights and scroll into view. If the finder is
closed, Command-G opens it and selects the first match.
Command-A respects the active pane: in the result list it selects every result
row, while in a plain-text or raw-source message it selects that displayed
message's text, excluding viewer headers, attachments, and provenance. HTML
keeps its native document selection behavior. A copy control copies the visible
body text; for HTML it also copies the displayed message subject and headers.

The bottom of the main GUI contains a clickable ingest-status line. During a
run it shows live completion, message count, active/configured workers, and ETA;
otherwise it summarizes the latest run. Clicking it opens a separate native
Ingests window with its own close box. The **Window → Ingests** menu opens the
same window. That window browses every retained status file and shows the
selected run's aggregate statistics, sources, failure detail, and all worker
threads. If it is already visible, either action brings it to the front and
selects the requested run instead of creating a duplicate window.

The GUI lists every non-attachment `text/plain` and `text/html` MIME part and
allows the user to select among them. When multiple HTML parts exist, it opens
the most substantive decoded part by default. HTML is isolated and sanitized. Scripts,
forms, plugins, file URLs, and remote resources are blocked by default; remote
HTTP(S) images may load only after an explicit action for that HTML MIME part. Embedded
CID images may render from the verified message. Once the sanitized local HTML
document is available, its text remains displayable while permitted remote
resources resolve; a slow remote resource must not blank or delay the part.
Attachments appear in a list,
safe images and PDFs can be previewed inline, and opening any attachment is an
explicit action with confirmation for active, unknown, or mismatched MIME/suffix
pairs. Only allowlisted matching PDF, static image, and plain-text pairs bypass
that extra confirmation.
For a non-multipart message whose complete raw body, apart from surrounding
ASCII whitespace, is enclosed by case-insensitive `<x-html>` and `</x-html>`
tags, the GUI exposes the enclosed content as a preferred **HTML — legacy
x-html** view. This recovery also applies when a malformed multipart declaration
has no usable boundaries and therefore parses as non-multipart. An inline
mention of `<x-html>` does not trigger it, valid MIME parts take precedence, and
the recovered view passes through the same sanitizer and remote-content policy
as MIME HTML. **Raw Source** remains selectable and unchanged.
At the bottom of every message view, the GUI displays the archive mailbox path
separately from every source volume and source/forensic path where the message
was found. A nonzero MBOX byte offset is displayed as `?offset=N`, rather than
as part of the pathname; an absent or zero offset is omitted. Each local source
path has a copy control that writes that path, without any offset, to the macOS
pasteboard both as plain text and as a file URL.
When a message pane is taller than its displayed content, the source-location
section remains at the bottom of that pane; it never floats immediately after a
short message body.
When `date_source` is `received-median`, the GUI shows a warning banner across
the message and gives the message well a slight red tint. The original `Date:`
header remains visible and unchanged. The banner identifies the original
`Date:` header value, the computed UTC median of the usable `Received:` header
dates, and the computed UTC date used for archive routing. The latter two values
are currently equal but are reported separately so the archival decision is
explicit.
Command-1 through Command-9 select the MIME part having that numeric part ID;
Command-0 and Command-Shift-U select the raw RFC 5322 source.

Saving a message creates a disposable `.eml` copy containing the exact
SHA-256-verified RFC 5322 bytes; it never creates or changes canonical archive
content. Message-list rows and the separate message-file icon well share the
same drag implementation, which creates that copy only when a drag starts, never
while browsing, selecting, or hovering
over a result. Because the pywebview bridge is asynchronous, the first drag
prepares the disposable file and the next drag copies the actual `.eml` file to
Finder or the Desktop. Multiple messages transfer as an actual ZIP file. Neither
operation may create a `.fileloc`/`.webloc` shortcut or expose link/text URL drag
representations supplied by the application. The application explicitly writes
only `public.file-url`, the modern file-path transfer type; macOS may add its own
compatibility aliases. Each drag export is isolated from attachment exports and later
drags; closing the viewer serializes with preparation and revokes every token.
Backends without the macOS file-drag adapter hide and reject this drag control.
The result table must finish initializing before startup clears or populates it.
Queued clicks must not select rows discarded by a subsequent search.
Message headers remain selectable text. Printing prints the displayed headers
and selected MIME part through the system print panel. Temporary message and attachment exports are removed when the GUI exits.

`summarize` is an optional macOS command that reads nonempty UTF-8 text from
standard input and prints only a one-sentence Apple Intelligence summary of at
most 30 words to standard
output. It uses Apple's on-device Foundation Models framework, never writes the
input to the archive, and reports a clear error when the device is ineligible,
Apple Intelligence is disabled, or the model is not ready.

All archive commands use `MAIL_ARCHIVE_DIR` as their default archive directory.
`--archive DIRECTORY` overrides that environment variable. If neither is set,
the command fails before reading or writing an archive.

The Python GUI identifies itself as **Email Collection Toolkit** and uses the
source-controlled rainbow-envelope icon in its native application identity.
The required continuous-integration gate exercises the archive lifecycle and
complete HTML interface in headless Chromium with disposable fixtures. Native
Cocoa/WKWebView smoke testing is an explicit local macOS development check and
must not run in CI/CD. It uses a purpose-built one-message derived archive,
reports JavaScript-to-Python bridge completion once, exposes only `status`,
`search`, and `native_smoke_complete`, and omits the normal application menu.
It persists timestamped phases atomically and has independent child and parent
watchdogs. The first failure remains the primary report error even when
shutdown records additional failure phases. A required native-application gate
would instead need a logged-in Mac and XCUITest/XCUIAutomation.
The project also publishes a Zola-generated GitHub Pages site at
`https://simsong.github.io/email-collection-toolkit/`. The site links to the README,
release notes, GitHub releases, the current stable `v1.2.3`-shaped tag and
current beta `v1.2.3-beta1`-shaped tag when present, and project discussions.
The site uses the Email Collection Toolkit identity and shared stacked-envelope
SVG and derived PNG icons. Site validation rejects malformed Zola TOML, unreadable configuration, and
invalid UTF-8 with a path-qualified diagnostic instead of a traceback, before
checking for other missing website files.
Decorative homepage icons are hidden from assistive technology.
The home-page cover uses joined, diverging rainbow streaks, not repeating
rainbow arcs. Its title, subtitle, description, and tagline are selectable
HTML text over text-free artwork. The tagline reads "Email has a history. Keep it".
At narrow widths and browser zoom, the text reflows below the artwork and
remains available to assistive technology and when images are unavailable.
The banner's HTML dimensions match its displayed aspect ratio.
Banner sizing does not require container-query-unit support. The three desktop
tagline lines have no extra blank lines, and the mobile sentence retains
copyable spaces between words.
Provide text alternatives and readable introductory text and actions on mobile.
The horizontal keyboard photograph flows below the home-page story text at
all widths, preserves the complete image, and includes a linked Flickr credit.
Its home page gives equal prominence to individuals consolidating personal
exports and archivists curating donor collections. A separate use-cases page
describes both workflows, including an institutional digital-estate scenario,
native BagIt/Mailbag archive storage, MBOX handoff to ePADD, and explicit boundaries between
implemented and planned sources. It also provides a clearly labeled index of
digital-email-curation reports and related organizations; it does not imply
that planned application features are implemented. Every site page links to a
public privacy policy covering planned Gmail and Microsoft 365 OAuth access and
to a rights page stating the software's current GPL distribution, copyright,
and availability of non-GPL versions. The curation section renders a
responsive summary of the program's local file discovery, read-only ingest,
archive creation, search and reporting, verification, and sharing functions,
followed by its reports and organizations. Public website copy uses language
for archivists, avoids software-development jargon, and labels unavailable
functions as planned work.
The site's About page identifies Simson Garfinkel, summarizes his work with a
link to his personal website, and links to his GitHub profile and the project
repository. About links to a dated website changelog that records website
changes separately from software release notes. Website and documentation
must describe BagIt/Mailbag as native archive storage, not a separate export.
The Pages build pins its Zola release and verifies the downloaded archive
against a source-controlled SHA-256 digest before execution.

All primary navigation links remain visible at narrow widths and after
reordering; the header wraps instead of hiding positional links. Long code
examples scroll within their block without widening the mobile page. At widths
of 650 pixels or less, the page shell keeps 15-pixel side margins. Icon
regeneration closes its browser on success and failure.
Release assembly verifies the annotated tag's signature and package version
before installing project dependencies, building artifacts, or executing their
entry points. Tag/version validation must not install the project itself.

## Remote account authorization

`mailarchiver-auth ACCOUNT` authorizes a remote account independently of an
archive or ingest run. It accepts exactly one mailbox address and detects
consumer Gmail directly, Google Workspace from provider-specific MX records,
and Microsoft 365 from provider-specific MX or Autodiscover records. Detection
is bounded and explainable. An inconclusive result fails closed and identifies
the `--gmail` override; it does not guess from generic gateways or unrelated
domain-verification records. `--detect-only` reports the evidence without
authorizing or changing external state. Microsoft 365 authorization is a
recognized but unavailable stub.

Gmail authorization requests only `gmail.readonly`, opens Google's installed
application flow in the system browser, and verifies the returned Gmail profile
against the command-line account before retaining the token. The refresh token
is stored under that account in the operating-system credential store, never in
the archive, client configuration, terminal output, logs, fixtures, or reports.
A release provides one public Google Desktop-client configuration registered by
the Email Collection Toolkit maintainer. End users do not create Cloud projects, configure
consent, obtain client IDs, or supply client files. A build without that
configuration fails with a distributor-facing error; it must not route an end
user into registration. Account-specific `--client-secrets` remains a developer
override. The downloaded configuration is Pydantic-validated and restricted to
Google client IDs, OAuth endpoints, and loopback redirects.

`--register-client` is the explicit one-time maintainer workflow. It uses an
installed Google Cloud CLI to authenticate the named owner account, then creates
one project and enables only the Gmail API after explicit confirmation. It does
not change the CLI's active account or default project. Without that CLI, it
opens project creation and Gmail API pages and asks for the resulting project
ID. Because Google has no supported general API for External consent-screen and
Desktop-client creation, setup opens project-scoped Branding, Audience, Data
Access, and Client pages in order and imports Google's download. It must not
scrape a browser profile, capture a Google password, or automate the Console
DOM.

The project and Desktop client registration persist. In Google's Testing state,
listed test users reauthorize after seven days; the maintainer does not
re-register the program. In production, users need not be individually listed.
An unverified personal-use app warns users and is limited to 100 new users until
verification. The end-user manual and website explain this distinction with
generic account examples. Separate maintainer help pages contain the illustrated
one-time registration procedure and tell readers to use their own account.

## Per-archive sources and import modes

This source registry and Refresh/Rebuild workflow are planned. The current
`archive.yaml` stores document preferences, not this registry.

Each archive has a versioned top-level `archive.yaml` operational configuration
containing an ordered `sources` list. Every source has a stable, unique `id`
and exactly one of these kinds:

* `file` names one local file;
* `local-folder` names one recursively discovered local directory; and
* `imap` names one remote IMAP account and records its server, port, username,
  TLS mode, authentication mode, folder selection, and a non-secret credential
  reference.

Local paths and IMAP usernames are private operational metadata. Passwords,
app passwords, OAuth access tokens, and OAuth refresh tokens must never appear
in `archive.yaml`, either SQLite database, status files, logs, reports,
manifests, or fixtures. The credential reference identifies an entry in the
operating-system keychain or configured secrets provider. When that entry is
absent, an interactive import prompts securely for a password or starts the
configured OAuth authorization flow; a noninteractive import fails without
printing or persisting a secret outside the credential store.

**Import/Refresh** visits every enabled configured source. It walks each local
folder to discover new files, but a known local file whose recorded nanosecond
modification time is unchanged since its last completed import is not opened,
hashed, or parsed. A configured `file` source uses the same fast path. This is
an intentional performance tradeoff: a file changed while retaining its prior
modification time is not detected by Refresh. New paths and paths with changed
modification times are processed normally. IMAP sources use their native
folder/UID and version checkpoints to retrieve new or changed messages without
marking messages read or changing server state.

**Import/Rebuild** also visits every enabled source, but ignores the local
modification-time shortcut and recomputes the complete SHA-256 of every local
source file. An unchanged digest may skip parsing after hashing; a changed
digest is reprocessed. For IMAP, Rebuild performs a complete folder and UID
reconciliation rather than relying only on the incremental cursor. Both modes
remain message-idempotent, retain source observations, and never clear or
recreate canonical mail. `refresh-index` is unrelated: it rebuilds only the
disposable search database from already archived messages.

The user manual and the website importing page must show the three source
kinds, the secret-storage boundary, and a side-by-side explanation of
Import/Refresh and Import/Rebuild. Until the archive configuration and live
IMAP adapter are implemented, those pages must label this workflow as planned
and retain the current explicit-path CLI instructions.

## Ingest sources

* Recursive local-directory ingest recognizes MBOX streams, Apple Mail MBOX
  packages, Maildir, individual RFC 5322 files, and Apple `.emlx` files.
  MBOX may have a terminal-capture or MMDF control preamble when its first
  classic envelope appears within the first 16 physical lines, has a valid
  ctime-style timestamp, and is followed by an RFC header block. MMDF control
  delimiters frame source records and are excluded from the RFC 5322 bytes.
  The probe byte and line limits come from `local_source_rules.yaml`, rather
  than Python constants.
  A Maildir is recognized structurally: a message must be directly below
  `cur` or `new`, and their parent must contain `cur`, `new`, and `tmp`
  directories. The Maildir root is the logical mailbox; `cur`, `new`, and the
  physical message filename are provenance, not mailbox-name components. A
  Maildir at a mounted volume root uses the mount directory name, or `Maildir`
  for an unnamed filesystem root, so its logical mailbox remains selectable.
  Local source-file fingerprints and append checkpoints use the physical file
  bytes, including `.emlx` trailing metadata even though it is not message data.
  Files whose exact basename is `Info.plist` or `table_of_contents`, or whose
  suffix is case-insensitively `.toc`, are known mailbox-container metadata and
  are silently omitted from discovery; they do not produce skipped-input
  reports or counts.
  Directory traversal must surface missing paths and permission failures rather
  than silently treating an unreadable mailbox as empty. Direct access to
  `~/Library/Mail` may require Full Disk Access for the invoking application.
* `.emlx` input uses its leading decimal byte count to select exactly the
  RFC 5322 message; Apple's trailing plist metadata is not part of the
  message hash or output. Apple `.partial.emlx` input is rejected because its
  attachment payloads are detached and reconstructing a message would not
  preserve the original RFC 5322 bytes. Apple Mail databases, plist files,
  attachment directories, and `.emlxpart` fragments are not separate messages.
  For an Apple Mail cache, the logical mailbox path ends at the deepest
  `.mbox` package, with each `.mbox` suffix removed. Account and parent mailbox
  components remain in the path; internal UUID, `Data`, numeric bucket,
  `Messages`, and `.emlx` filename components do not.
* Generic read-only IMAP is the first planned cloud importer and covers Gmail,
  Microsoft 365/Outlook.com, and conventional IMAP services through
  provider-specific authentication profiles. A later Gmail API adapter may
  provide richer label and history metadata and supports a rolling `--days N`
  mode using Gmail's `newer_than:Nd` query. Google Takeout MBOX is supported as an offline,
  one-time baseline input and is the current end-user path; personal Takeout is
  not assumed to be programmatically triggerable. Direct multi-part Takeout ZIP
  ingestion remains future work, so current users extract every part and ingest
  their common parent directory. `doc/GMAIL.md` separately identifies end-user
  instructions and developer-only OAuth, verification, assessment, and IMAP
  decisions.
* IMAP ingest supports TLS and authenticated account configuration, records
  account/folder/UID provenance, and retrieves RFC 5322 bytes without marking
  messages read or modifying the remote mailbox.
* Outlook `.pst` and `.ost` ingest does not require Outlook to modify or export
  the source. The adapter records the parser/converter and version, enumerates
  every encountered store item, preserves folder and item identifiers as
  provenance, and reports corrupt, deleted, partial, or unsupported records
  rather than silently omitting them. The current backend decision, fixture
  matrix, and format limitations are maintained in
  [ON_DISK_MAIL_FORMATS.md](ON_DISK_MAIL_FORMATS.md).
  Microsoft 365 has no platform-neutral Takeout equivalent. Outlook PST export
  on Windows and OLM export from legacy Outlook for Mac are recognized future
  acquisition paths, but PST, OST, OLM, Graph, and Exchange Online IMAP are not
  current end-user sources. `doc/M365.md` must keep that boundary explicit.
* Eudora ingest recognizes mailbox files together with their table-of-contents,
  attachment, and embedded-content conventions. It records which companion
  files were present and never treats an absent or stale index as proof that a
  message or attachment does not exist.
* Working IMAP client-cache ingest is an offline, read-only source distinct from
  live IMAP. It recognizes supported cache/profile layouts, records account and
  folder context when recoverable, and explicitly reports placeholders,
  evicted bodies, partial downloads, and detached parts. It does not contact a
  server unless the user separately configures and authorizes live IMAP ingest.
  A live Apple Mail cache is best-effort evidence rather than proof of complete
  provider acquisition. Complete `.emlx` records are accepted, known partial
  records are rejected, and a cache-completeness preflight must report partial
  records and attachment policy before a completeness claim. The observed
  machine-specific access boundary and preflight are maintained in
  `doc/APPLE_MAIL_CACHE.md`. Until direct Gmail, Microsoft 365, and IMAP
  adapters are implemented, separately staged complete Apple Mail cache records are a supported
  local-file bridge for accounts synchronized through those providers; the
  bridge must not be represented as provider-complete acquisition.
* The Apple Mail/archive comparator is strictly read-only. It indexes complete
  `.emlx` records in a disposable database, opens the archive catalog
  read-only, and classifies exact raw, semantic-only, cache-only, archive-only,
  and ambiguous matches using semantic-message version 1 (`h3`). It compares
  header names and DKIM-relaxed values only for semantic-only pairs, outputs no
  header values or message content, reports excluded partial and unreadable
  records, and warns when the active Envelope Index WAL changes during the
  scan. Provider metadata must be read from a private database/WAL snapshot:
  SQLite must not open the source Envelope Index or create/change its sidecars.
  Changes detected while copying the snapshot must fail with a retry diagnostic.
  Its privacy-preserving service breakdown classifies Gmail from the
  special mailbox hierarchy, Microsoft Exchange from Apple's EWS scheme, and
  retains other IMAP, POP, local, and unknown stores separately without
  reporting account addresses or opaque identifiers. It must never treat `h3`
  alone as authorization to merge or delete a canonical source variant.
* The ambiguous-h3 review exporter selects a requested number of distinct
  equivalence classes deterministically by descending archive-variant count,
  descending cache-occurrence count, and h3. For each selected class it copies
  every matching complete cache occurrence and every matching hash-verified
  canonical representation without changing either source. It refuses to
  overwrite an output directory and writes private owner-only EML files,
  per-case manifests, a root manifest, and a CSV/Markdown index. Reports assign
  identical raw `h2` values to explicit equivalence groups, identify groups
  shared across cache and archive, compare the canonicalized `Date` and
  `Subject` components used by `h3`, and report `X-Apple-Auto-Saved` per file.
  Existing reports can be refreshed only after every exported file passes both
  its recorded `h2` and case `h3`; source messages are not needed for refresh.
  Private
  messages belong only in the gitignored project `.tmp` area and must never be
  committed as fixtures or documentation.
* Every source adapter emits original RFC 5322 bytes where the source contains
  them. When a proprietary store requires reconstruction or conversion, the
  observation records that fact and the responsible tool/version; reconstructed
  output is never represented as byte-identical to a source RFC 5322 record.

## Public validation datasets

* Validation definitions are strict, versioned TOML files, one per anonymously
  downloadable public corpus. Each records its source URL, preprocessing mode,
  extraction bound, and EC2 sizing. Account-gated, institution-only, and
  unavailable bulk exports are excluded. Fixed cross-era samples are identified
  as samples rather than represented as complete list archives.
* All validation work is derived under the repository-local ignored `data/`
  directory. Downloads are immutable inputs: acquisition records their byte
  length and SHA-256 in a source manifest, reuses only a hash-checked cached
  file when an expected digest is configured, and never changes an upstream
  mailbox or downloaded artifact.
* Tar, ZIP, gzip, and 7z preprocessing rejects path traversal, links and special
  archive members, and expansion beyond the dataset's configured bound. Normal
  RFC 5322 and MBOX inputs are hard-linked or copied byte-for-byte. A Unix MBOX
  envelope on an individual-message corpus is removed with the MBOX parser;
  SF-LOVERS Babyl records are likewise an explicitly derived conversion. The
  original downloaded files and their source manifest remain unchanged.
* A local validation run acquires and preprocesses a dataset, invokes the normal
  ingest CLI with on-demand ClamAV, runs the installed standard-library verifier,
  and creates a ZIP of the verified Mailbag plus a typed run report. A run over
  all datasets is sequential so local resource use stays bounded.
* The AWS mode deploys a SAM control plane without creating the result bucket.
  The existing long-lived S3 bucket is a deployment parameter. Starting all
  datasets launches exactly one independent EC2 worker per dataset. Workers have
  encrypted delete-on-termination storage, required IMDSv2, no inbound security
  group rules, and write-only access beneath the configured bucket prefix.
  Each worker downloads its own corpus, runs the same Makefile workflow, uploads
  status and logs plus any report and Mailbag ZIP, and terminates on shutdown,
  including after failure.

## Redaction and research derivatives

* Redaction never edits canonical MBOX files. A redacted export identifies its
  source message hashes, policy and policy version, selected fields or byte
  ranges, reasons, operator, and creation time. Verification distinguishes an
  authorized transformation from corruption while preventing removed content
  from remaining in the public derivative or its ordinary indexes.
* Redaction policies can address headers, addresses, body passages, MIME parts,
  attachments, and derived entities. Decisions can be reviewed before export;
  access to the canonical-to-derivative audit mapping is controlled separately
  from access to the redacted corpus.
* The structured research database is derived and reproducible. It supports at
  least correspondents and aliases, dates, threads, message and attachment
  relationships, source provenance, and parse defects. Future entity and topic
  extraction records the extractor and version so reports can be reproduced or
  recomputed without changing canonical mail.
* Research reports state their corpus selection, exclusion/redaction policy,
  data and extractor versions, and known incompleteness. Derived facts never
  replace original headers or message bytes.

## Sorting, validation, and recovery

* **Planned sorting:** At the end of an ingest run, touched normal MBOX files are
  sorted by resolved timestamp and then message SHA-256 for deterministic ties.
* **Planned sorting:** Sorting writes a same-directory temporary replacement and
  preserves the prior file as a backup.
* **Planned sorting:** The replacement and backup are parsed end-to-end. Their
  unordered sets of `(Message-ID, SHA-256)` must match exactly before the backup
  is deleted.
* The MBOX byte hash, integrity tags, Mailbag CSV, payload manifest, tag
  manifest, locations, and metadata database updates are published in the
  documented checkpoint order. Interrupted runs leave either the preceding
  verified checkpoint or a detectably invalid, recoverable partial update;
  they never report a partial archive as valid.
* Ingest installs `verify_mail_archive.py` in the archive. This single-file,
  standard-library-only tool is strictly read-only: it verifies BagIt payload
  and tag manifests, required Mailbag structure, and every declared
  complete-MBOX, raw-message, and semantic-message digest without consulting
  SQLite. It exits nonzero after reporting any mismatch.
  Its source header and `--help` explain prerequisites, macOS/Linux and Windows
  commands, the default archive directory, checks performed, exit status, and
  read-only limitations. Instructions travel with every installed copy.
* The MBOX container's required separator newline is not part of a source
  message that lacked a terminal newline. Catalog retrieval and standalone
  verification select the candidate matching the recorded source SHA-256.
* Mailbag CSV uses CRLF record endings. Folded source headers used as CSV
  metadata are unfolded to single-line values; canonical message bytes are
  unchanged.
* A future integrated `verify` command is strictly read-only: it reparses every canonical MBOX,
  checks integrity files, offsets, and database rows, and reports duplicate-policy
  violations.
* `refresh-index` rebuilds the disposable FTS database in a temporary file,
  verifies every normal MBOX message against the catalog and the total
  searchable-message count, and replaces the prior index only after those
  checks succeed. It visibly reports a progress bar, completion count, and
  ETA weighted by catalogued message counts for mailbox verification and
  message indexing. It announces before starting that `Ctrl-C` discards the
  incomplete replacement and retains the existing search index. Its
  `--workers` option defaults to the available CPU count (or two if it cannot
  be determined), and parallelizes bounded verified-MBOX read/hash/MIME work while
  retaining one ordered SQLite writer. While it reads each verified message, it also refreshes the
  derived catalog subject from canonical header bytes, repairing newly supported
  legacy charset recovery without changing canonical mail. `review` queries the
  committed source-observation log by run, source, and disposition. Derived
  catalog fields and canonical locations are created correctly during ingest.

## Windows development environment

[WINDOWS.md](WINDOWS.md) documents native Windows setup with x64 CPython managed
by uv, MSYS2 build utilities, Rust/MSVC/Dioxus tooling, WebView2, and existing
Makefile checks. It covers ARM64 and x64 uv installation separately from the
application Python target. Setup must distinguish installed tools from validated
application support. Windows delivery
requires full ingest, preservation, recovery, and native desktop validation;
WSL/Linux results do not establish Windows compatibility. Windows writer and
scanner portability remain implementation work, not shipped features.

## macOS desktop delivery

Ruff must pass with zero diagnostics before validation or packaging succeeds.
`make ruff` checks the repository using the locked development dependency;
`make check`, `make dmg`, and release builds must enforce it without ignoring
its exit status. Ruff is development tooling, not a bundled runtime dependency.

`make syntax-check` must compile all Python source, scripts, and tests without
executing them. Both `make check` and `make dmg` require this check so a syntax
error in a build-only script cannot escape ordinary validation.

`make dmg` builds a self-contained, native-architecture PyInstaller `.app` and
a compressed DMG containing it, an Applications shortcut, and drag-to-install
instructions.
The Finder window must present a large app icon on the left and the real
Applications shortcut on the right, with an arrow and drag-to-install
instructions in the background. Only those two items are visible; instructions
must not require opening a separate text file. The mounted test verifies saved
icon positions, background, icon size, and absence of extra visible items.
The rendered Retina background must keep its title, arrow, and both instruction
lines within their intended regions without overlapping the icon locations or
clipping at the window edges. Validate rendered pixels as well as Finder metadata.

Python, native extension libraries, GUI assets, packaged schemas,
plug-in manifests, and the standalone verifier source travel inside the app.
ClamAV and experimental command-line tools (Tika/Java, PDF OCR, Apple Intelligence)
are not prerequisites of the supported local-mail GUI and are not bundled.
When both `APPLE_CERTIFICATE_P12_BASE64` and `APPLE_CERTIFICATE_PASSWORD`
are present on an isolated GitHub-hosted runner, the build must import the
PKCS#12 identity temporarily, sign the app and DMG with Developer ID, verify
their seals, and remove the imported key.
If either secret is absent, continue with an ad-hoc-signed app and unsigned
DMG, emit a GitHub Actions `::warning::`, and append `_UNSIGNED` before `.dmg`.
Invalid configured credentials and signing failures must fail rather than
silently downgrade. Secret values must not appear in error output or artifacts;
restore the prior keychain search list on completion or failure. Explicit local
`--signing-identity` remains supported, including `-` to force unsigned output.
Explicit unsigned output must identify the override rather than report missing
credentials. Reject automatic PKCS#12 import on local and self-hosted runners:
`security` password arguments remain visible to other processes in the job.
Before project commands or Apple secrets are used, verify the release tag's
OpenPGP signature against only the public keys configured by administrators in
`RELEASE_SIGNING_PUBLIC_KEYS`, without automatic key retrieval. Missing/invalid
keys or an unlisted signer must fail. Administrators must protect the workflow
and release tags separately; a modified workflow could remove this gate.
Release assembly must include the tested DMG from the same commit as the source
archive and checksum the final image. Signing is not notarization: neither
build is automatically notarized, and no Gatekeeper bypass is performed. The archive extension is declared
in the bundle's document-type metadata.

The build must mount its DMG read-only, verify the bundle seal, run a headless
self-test and a visible native self-test using the mounted executable, and
detach the volume even on test failure. Tests use disposable fixtures and
preferences, never the last real archive. They exercise no-ClamAV ingest,
source-byte preservation, search, BagIt verification, repeat-import idempotence,
native bridge startup, and the missing-antivirus banner. A failed check prevents
replacement of a prior DMG. JSON reports accompany the successful artifact.
The bundled-library audit must distinguish `LC_ID_DYLIB` metadata from actual
load commands; a binary's own install name is not an imported dependency.
Unresolved imported libraries must still fail validation.

## Scope boundaries

Schema files retain the Flyway naming convention
`V<version>__<description>.sql` (double underscore), such as
`V1__archive.sql`. This is a filename convention only, not a dependency on
Flyway, Java, or JDBC. Future catalog upgrades will use developer-written SQL
migrations run automatically by the application through SQLite; users must
not need a separate migration tool or manual SQL commands. The planned runner
must record applied versions and checksums, take a consistent SQLite backup,
hold the archive writer lease and pause affected windows, and commit each
migration with its history record atomically. It must reject unsupported newer
schemas and report failures without changing canonical MBOX bytes. Upgrade and
interruption tests are required before shipping migration support.

The first release is a local command-line normalizer and verifier. Packaged
configuration holds archive and scanner policy; each archive's `config.yaml`
holds navigation state and explicit include/exclude owner rules. Legacy
`owner-names.txt` can seed those rules before the first confirmed import. A local
special-purpose search and message-viewing interface is a consumer of
the two SQLite databases, not a reason to depend on Thunderbird or FoxTrot.
No source mailbox is modified by this program.
The complete current catalog DDL is the packaged `sql/V1__archive.sql` resource
and is created only for a fresh archive. An unversioned catalog or any version
other than V1 is rejected rather than migrated. The separate disposable search
database likewise has exactly one packaged `sql/V1__search.sql`; before ingest
workers start, an obsolete search database is rebuilt from catalogued canonical
MBOX into a temporary file and atomically replaced, not migrated in place. A
failed rebuild preserves the prior database. A fresh catalog is also
refused beside existing canonical MBOX or `.mbox.integrity` output because that
would defeat deduplication. Those outputs are detected in `data/mbox/` and
`integrity/`; unsupported root-level legacy output is never imported.

The live appendable archive itself is the Mailbag interchange and preservation
package. A redacted or otherwise restricted release is a separate BagIt bag
with its own payload, manifests, Mailbag identifiers, and audit mapping. PDF
and WARC representations remain opt-in, sandboxed publication derivatives and
must not make remote requests without explicit authorization.

## Developer validation gates

Copilot review requests must use `gh` with the authorized `simsong` identity,
not browser control. That exception is review-request-only; all other Codex
GitHub writes retain `simsong-codex`. A successful command alone does not prove
that review was requested.

Before pr-to-ready handoff, intended task changes and local-only commits in
every task checkout must be reconciled, validated, and published in the delivery
PR. Moving or backing up a dirty checkout is not integration. Unrelated or
ambiguous work requires an explicit disposition rather than silent inclusion
or deletion.

The pr-to-ready workflow must retain a quiet post-handoff merge check and clean
up its task-owned `.tmp` checkout only after the human merges the PR. Removal
requires current-main ancestry or patch-equivalence evidence and a clean tree;
ignored private evidence is not disposable. Dirty, unmerged, or uncertain
checkouts must be retained and reported, not forcibly deleted.

`make check` runs Ruff and Pylint (`make lint`), then ty and Pyright
(`make types`), then pytest, Chromium end-to-end tests, and website validation.
The stages run sequentially even with parallel make and stop on failure.
Both type checkers cover source, scripts, tests, end-to-end tests, and the AWS
launcher. Project development dependencies and type stubs are locked with uv;
static analysis must produce no errors or warnings. Focused `make ruff`,
`make pylint`, `make ty`, `make pyright`, and `make test` targets remain available.

### Desktop review follow-up

The portability audit reads each Mach-O LC_RPATH command, expands loader and
executable-relative paths, rejects search paths outside the bundle, and requires
non-system dependencies to resolve to bundled files. Missing antivirus uses a
platform-neutral confirmation on non-macOS hosts. Source-picker navigation is
saved only after successful ingest while the writer lease is retained; a failed
import leaves the previous directory unchanged.

### Writer and desktop review boundary

Current archive writing is supported on POSIX. Windows writing fails before
creating an archive or lock metadata; the secure no-reparse-point implementation
and native Windows subprocess validation are deferred to v1.1.0. The former
untested msvcrt branch is removed; no Windows locking guarantee is claimed.
POSIX acquisition pins the archive/status directories and opens lock files
relative to directory descriptors without following links. Lock files must be
regular, single-link files. New targets are created under a parent-directory
creation lock before acquiring the archive lease; creation diagnostics may leave
`.mailarchiver-create.lock` in the parent. Its presence alone does not lock anything.
GUI creation rechecks destination emptiness under the lease. File New proceeds
into Import, About reports the active archive volume, and publication refreshes
mailbox-only queries as well as text queries.

### PR review validation follow-up

Archive documents identify directories by filesystem device/inode and reject
symbolic or hard-linked database entries before opening. GUI ingest records
publication evidence even when a later step fails; only published changes
advance the shared generation. Progress can write status files without a
terminal stream, including windowed builds with no stderr. Ingest child windows
route document actions to an attached search window. Informational notices stay
in About instead of appearing as errors. Closing an import owner offers waiting
or keeping the window open. Native macOS Quit offers Cancel or Stop Import and Quit while an import is
active. Confirmed quit signals all imports, retains their leases and windows
until checkpoint completion, and then exits. Shutdown joins tracked workers
before releasing resources. Makefile Ruff checks select this checkout's configuration explicitly. Git
selects tracked and non-ignored new `.py` and `.pyi` files, so linked worktrees
are checked without descending into ignored generated directories.

An Ingests child window retains document routing after all search windows close.
New Search and Ingests use that document directly; Import creates a new search
owner when needed.

Integrity hash-standard versions must be JSON integers. The standalone verifier
rejects boolean, floating-point, and string alternatives without coercion.

## Current acquisition boundaries

No release Desktop OAuth client is bundled yet. The shared-client end-user
flow remains deferred until a maintainer supplies and validates that public
configuration in release artifacts. Current authorization requires a developer
client override. Installing that override validates and writes the same bytes.
Known consumer domains need no DNS lookup; transient DNS and token-refresh
transport failures are disclosed as errors rather than negative detection or
fresh consent. Credentials are stored only after the profile matches.

Directory import reports and skips `.partial.emlx` records while retaining
complete supported records. Direct selection of a partial record is rejected,
including zero-byte partial records; the generic empty-file shortcut does not
silence them.
This is not a complete mailbox acquisition: detached attachment bytes are not
reconstructed. Do not modify the source cache; export mail through Apple Mail
when a complete MBOX source is required.

## Toolkit identity and website illustrations

All public product text uses Email Collection Toolkit, and repository/Pages
links use `simsong/email-collection-toolkit`. Existing macOS/Windows preferences
and OAuth directories remain readable under their prior directory name; new
installations use the current product name. A file, empty directory, or directory containing only incidental metadata at the new path
must not hide an existing legacy settings directory. No source mailbox or canonical
archive is renamed. Python package and CLI identifiers remain compatible.

The homepage links Collect to Importing, Search to Searching, and preservation
formats to their specifications, with an ePADD project link. Searching documents
the implemented query forms and viewer controls, with planned features labeled.
Homepage and Searching share a real synthetic-data search capture; Importing
shows the real import-history interface. Interface changes require screenshot
regeneration through the Makefile. External clipart has visible author, source,
and license attribution. Gmail setup diagrams are labeled as illustrations,
not screenshots of a live third-party account.

The macOS bundle dependency audit shall distinguish a Mach-O library's own
`LC_ID_DYLIB` from actual dylib load commands. Its own install name need not
resolve as another bundled file; actual non-system dependencies must resolve
inside the app. A compiled-library regression shall exercise both cases.
