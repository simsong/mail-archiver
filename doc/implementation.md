# Mail archive normalizer implementation

## Technology decision

Implement the normalizer in Python 3.12+.  It needs reliable streaming I/O,
RFC 5322/MIME parsing, SQLite/FTS5, Gmail OAuth/API support, IMAP, and
ClamAV-process integration; Python provides all of these with the standard
library plus small, well-supported dependencies.  The observed local corpus
(about 52 GB and 1.47 million input messages) is a streaming workload.

Use the standard-library `mailbox.mbox` reader and writer.  Pass raw `bytes`,
not parsed `Message` objects, to `mbox.add()` so that MIME serialization is
never rewritten; `mailbox.mbox` handles mboxrd quoting, locking, and rewrite
recovery.  Capture the pre-append and post-flush file offsets for the future
reader.  Use the standard `email` package only for header/MIME parsing while
retaining original RFC 5322 bytes for identity hashing and output.

Use SHA-256 as the canonical hash.  A local OpenSSL 3.6.3 benchmark on this
Apple Silicon host measured 2.74 GB/s for SHA-256 on 16 KiB blocks, versus
1.61 GB/s for SHA-512 and 0.98 GB/s for SHA3-256.  Do not require BLAKE3: it
would add a dependency and is less portable for long-term verification.

## Native Mailbag storage

[Mailbag 1.0](https://archives.albany.edu/mailbag/spec/) is the native archive
layout, implemented directly without a `mailbagit` runtime dependency. The bag
root contains operational SQLite tag files, while canonical mboxrd files are
the only payload under `data/mbox/`. Rich per-message declarations live in the
top-level BagIt tag directory `integrity/`. The exact interoperability contract
is specified in [INTEGRITY_CONTROLS.md](INTEGRITY_CONTROLS.md).

BagIt manifests describe complete files; mailarchiver declarations add exact
RFC 5322 recovery and semantic hashes for each ordered message. The MBOX
SHA-256 is deliberately present in both `manifest-sha256.txt` and the
corresponding integrity tag. `mailbag.csv` connects each message to its MBOX,
records its attachment count, and assigns a stable package identifier derived
from normalized Message-ID plus raw SHA-256. It splits at Mailbag's 100,000-row
boundary.

The archive is appendable, so a write can temporarily invalidate the preceding
manifest. `write_bag_checkpoint()` closes that interval by writing integrity
tags and Mailbag CSV first, the payload manifest from already computed MBOX
hashes, `bag-info.txt`, and the tag manifest last. Successful completion,
controlled interruption, and publication recovery all use this path. A
validator never treats an in-progress mismatch as valid.

`archive.sqlite3` and `search.sqlite3` remain outside the tag manifest. This is
intentional: SQLite journal state is operational rather than portable BagIt
fixity, and the search database is disposable. The tag manifest instead covers
all BagIt/Mailbag metadata, every integrity tag, and the installed verifier.

`mailbagit` remains useful as an independent interoperability check and for
future derivative packaging when it can operate without moving or
reserializing canonical files. It is not the ingestion engine. Restricted or
redacted releases are separate bags; PDF and WARC derivatives remain opt-in
and sandboxed because rendering message HTML can contact remote resources.

RATOM's [libratom](https://github.com/libratom/libratom) should be evaluated as
the first PST/OST and entity-extraction backend. Reuse it behind the typed
source-adapter boundary and contribute missing preservation behavior upstream
rather than forking or writing another PST parser. Its lower-level
`PffArchive` exposes folder/message traversal and attachment metadata, but its
current high-level message formatter selects one body and does not construct a
complete attachment-bearing MIME message. The adapter must therefore consume
source-native components, account for every item, construct any necessary MIME
with explicit reconstruction provenance, and remain replaceable by another
backend. For extreme setup simplicity, supported releases need tested binary
libpff bindings or a bundled runtime; requiring users to compile C tooling is
not an acceptable default installation experience.

## Target package shape

```text
archiver/
  pyproject.toml
  src/mailarchiver/
    cli.py              ingest, verify, review, refresh-index, repack
    config.py           TOML policy and owner-names loading
    model.py            pydantic records and enums
    layout.py           native data/mbox and integrity tag paths
    bagit.py            Mailbag CSV and BagIt checkpoint publication
    mbox.py             streaming reader, mboxrd encoder, verifier
    message.py          header/MIME/date/classification parsing
    catalog.py          packaged schema loading and database helpers
    sql/
      V1__archive.sql   authoritative archive.sqlite3 V1 schema
      V1__search.sql    disposable search.sqlite3 V1 schema
    search.py           disposable search.sqlite3 and FTS5 rebuild
    clamav.py           clamdscan/clamscan adapter and result parsing
    ingest/
      directory.py      recursive type detection and provenance
      emlx.py           length-prefixed Apple Mail reader
      outlook.py        read-only PST/OST adapter
      eudora.py         Eudora mailbox and companion-file adapter
      imap_cache.py     supported offline IMAP client-cache layouts
      imap.py            read-only IMAP fetcher
      gmail.py           OAuth Gmail API incremental fetcher
    normalize.py        dedupe, routing, staging, publishing, sorting
    verify.py            end-to-end integrity checks
  gui/                  pywebview HTML, CSS, and JavaScript assets
  tests/
```

## Current acceptance implementation

The first implementation supports recursive local MBOX, `.eml`, Maildir, and
`.emlx` ingest, owner-token Sent classification, exact `(Message-ID, SHA-256)`
deduplication, autosave exclusion, Date/Received/path-year date fallback, a temporary on-demand `clamd`,
`data/mbox/INFECTED1.mbox`, SQLite catalog/FTS files, native BagIt/Mailbag
metadata, versioned `integrity/*.mbox.integrity` tags,
per-run observation review, and year/correspondent reports.  The top-level
`MAIL_ARCHIVE_DIR` selects the archive for every command by default; the
`--archive` option overrides it. `ingest` takes one
or more source roots as positional arguments and `--owner-names-file` selects
the reusable owner-token list.  Ingest currently requires `--clamav`: it starts
a foreground daemon only when no healthy configured socket is available, then
removes the daemon's stale socket on exit; it never enables persistent or
on-access scanning. Workers enqueue typed phase/path/offset updates; the main
thread drains them and redraws the stderr scoreboard every 250 milliseconds.
Before starting workers, a lightweight read-only discovery pass counts every
recognized source file and its current byte length without hashing or retaining
the tree. The main thread then starts or validates ClamAV and waits for its
health probe before creating the mailfile worker pool. This gives the scoreboard
a stable overall byte and file percentage and ETA; the terminal highlights that
aggregate line in white on blue, while
redirected output reports the same fields without terminal controls. Each
configured worker has a stable numbered row, following the bulk_extractor
status model, and worker threads never print directly. Lines are truncated from
the left of long paths to the current terminal width before a dynamic cursor
rewind, preventing wrapped paths from accumulating old headings. The title
reports the main-thread `waiting for ClamAV startup` preflight and derives
`ingesting` from worker messages. It
shows active and peak concurrency, per-worker checking/ingesting/scanning/
publishing/checkpointing/idle state, streaming source byte offsets, and
completion percentage, and reports processed/total source-file plus
archived/previously-seen/autosave/infected counts.  Control-C commits completed work, closes the
temporary scanner, publishes a BagIt/Mailbag checkpoint, reports a controlled interruption, and
prints the partial-run archive report before returning 130.  An `ENOSPC` append is truncated back to the prior MBOX size where
possible and reports a controlled nonzero stop.  Acceptance coverage includes
the checked-in MBOX/EMLX corpus, source checkpoints, append resumption,
malformed metadata, publication recovery, and disposable-index failure.
After metadata discovery, source files are streamed into an ingest pool bounded
by `--workers`; discovery does not pre-hash or retain file contents. Each pool
task owns one source mailfile through planning, streaming parse and scan, and
checkpoint. A never-seen file is
ingested before its complete fingerprint is calculated; that fingerprint is
still required before its checkpoint is committed. ClamAV readiness is an ingest
precondition, so no worker reads or parses mail until the daemon is healthy.
Modern Apple Mail package traversal recognizes complete
`Data/.../Messages/*.emlx` payloads and ignores MailData, plist, and detached
attachment files. It reports missing paths and macOS Full Disk Access failures
instead of treating them as empty input. `.partial.emlx` is rejected because
the cached RFC 5322 representation omits detached attachment bytes; use Apple
Mail's mailbox export to obtain a complete MBOX source.

Successful ingests also print the archive report after finalization.  The
report shows per-year totals plus the top 10 senders and recipients by default;
all sections use aligned tables with right-aligned, comma-grouped numbers, and
the correspondent tables show each address's first and last message dates.
Addresses identified by a `Sent` message are filtered from correspondent lists
only, not from stored metadata or yearly people totals.  `report --top 0`
suppresses those lists.

`ingest --workers N` defaults to `min(os.cpu_count(), 8)` and means at most `N`
source mailfiles in flight. Each file worker hashes, streams, parses, and sends
one ClamAV request at a time, allowing independent mailfiles to use concurrent
scanner clients. A single publisher lock serializes duplicate admission,
observations, SQLite transactions, publication-journal updates, MBOX appends,
and FTS writes. A discovery failure occurs before message publication, and a
file/byte-total mismatch between the two passes fails the run for a stable
rerun. The parser rejects nonpositive worker counts before starting an ingest. A spawned
daemon must pass `clamdscan --ping` after its socket appears before any message
scan is submitted.

Rollover, date sorting/repacking, complete recipient metadata, `verify`, richer
text extraction, Outlook PST/OST, Eudora, working IMAP cache directories, live
IMAP, Gmail, redaction, and research-oriented metadata remain planned work. The delivered
`mailsearch` command reads both databases without writing:
it applies `to:`/`from:`/`subject:` catalog filters, UTC calendar-day
`date:`/`before:`/`after:` filters, and ANDed FTS5 terms; it prints stable
`message_pk` header lines and reads a numbered message directly from its
catalogued MBOX byte location, validating its SHA-256 before output.

The authoritative current catalog schema is packaged as `sql/V1__archive.sql`.
It is initialized only for an empty database; unversioned databases and schema
versions other than V1 are rejected rather than migrated. Because an earlier
development schema also used the V1 label, startup validates the required
tables, columns, and named indexes before accepting an existing database. This
deliberately supports database replacement while there are no users.
`locations` and
`mbox_generations` are written as part of each message publication.

Header parsing decodes and unfolds RFC 2047 Subject values before catalog and
FTS insertion. `mailsearch` displays that catalog value directly.
Its result formatter determines number width from the returned `message_pk`
values and emits ANSI bold only for a terminal subject field.
Email-policy `compat32` header objects, including raw 8-bit `Received:`
and recipient fields, are converted to text before metadata parsing. Each
derived header field has an independent exception boundary. Broken RFC 2047
subjects retain their unfolded source text, and metadata defects are catalogued.
Sender resolution prefers a valid `From:` address, falls back to the RFC
`From:` inside a quoted nested-MBOX record and then the RFC `Sender:`
header. It recognizes Google Chat event payloads only when their Gmail thread
header and event fields are present. Chat actors are stored as
`Full Name (Google Chat)` so they cannot be mistaken for email addresses.
Reports render the remaining empty sender identity as `(missing sender)`.
Parsed dates outside 1900 through the next calendar year are rejected before
the normal Received/previous-message/path fallbacks.

Numbered-message display parses the verified raw bytes with the standard
library email parser. It renders the principal headers and prefers
non-attachment `text/plain` parts; it uses Beautiful Soup when only HTML is
available. XML-looking XHTML declared as `text/html` uses the forgiving HTML
parser while locally suppressing only Beautiful Soup's XML-as-HTML warning.
`--headers`, `--html`, and `--mime` select full headers, decoded
HTML parts, and exact original MIME source respectively.

The `gui/` prototype uses pywebview's Cocoa/WKWebView backend on macOS.  Its
Python API delegates query parsing, SQLite reads, and direct MBOX retrieval to
the same typed functions used by `mailsearch`.  Search pages request 101 rows
to return 100 plus a `has_more` indicator.  The UI is conventional: a search
toolbar above a result list and message pane. Independent message windows load
the same static application with a message-number parameter.

Result ordering is a server-side SQL whitelist over date, case-folded subject,
or case-folded sender with a stable message-number tie break. The listbox owns
keyboard focus after a pointer selection and implements Up/Down selection.
Result paperclips use attachment counts joined from the disposable search
metadata without rereading MBOX content. Each row reserves a third line; after
the header rows are painted, JavaScript queues one page of preview IDs through
the Python bridge. A single-worker executor reads the indexed 18-word previews,
and JavaScript polls the typed result batch until it can fill the rows. Global macOS Command-key handlers
select numeric MIME part IDs or raw source.
The toolbar's **Search attachments** checkbox passes an explicit boolean to the
typed search service. Ordinary terms search `message_fts` by default; when the
box is selected they search the union of `message_fts` and `attachment_fts`.
Metadata selectors are unchanged.

The optional original-mailbox explorer is built from volume-relative
`source_files.source_path` values. Pydantic node identifiers encode a normalized
logical path and, only in explicit-volume mode, the stable source-volume
identity; browser input never supplies SQL. MBOX files are leaves, while
Maildir `cur`/`new` contents and directories containing only direct EML/EMLX
files collapse into logical-mailbox leaves. Exact node counts use
`COUNT(DISTINCT observations.message_pk)` with the path/volume and
`observations_source_file_offset` indexes. Hidden-volume trees merge identical
volume-relative paths. The two tree modes are cached for the active archive.

Selected branches become one correlated `EXISTS` predicate inside the
materialized candidate query, before its `LIMIT` and `OFFSET`. The predicate
uses `observations_message_pk`, so multiple source observations provide union
semantics without duplicating a canonical result. Hiding the explorer sends no
selection while retaining its browser state. Versioned Pydantic filter sets
are fsynced to a temporary file and atomically replaced in the platform's
per-user preferences directory; the archive is never written.

MIME descriptions and API responses are Pydantic models.  Body content is
loaded only for the selected part.  HTML parsing removes active elements,
event handlers, file URLs, and unsafe URL schemes, replaces image CID references
with message-local data, and injects a restrictive CSP.  The HTML is displayed
inside a sandboxed iframe. Remote image URLs are omitted unless the user
explicitly enables them for that view. Individual image and PDF attachments
are base64-transferred only on an explicit preview action; other attachment
payloads are written to a private temporary directory before macOS opens them.
The viewer also reads the archive mailbox location and linked source
observations from the catalog, then displays archive path, source-volume label,
and source or forensic path at the bottom without treating an archive mailbox
as a source.

`search.sqlite3` contains separate `message_fts` and `attachment_fts` virtual
tables so message text remains searchable without attachment matches.
`message_metadata`, keyed by message SHA-256, contains an
indexed mapping to the message and optional attachment FTS row IDs, an
attachment count, and deterministic 18-word body preview. FTS updates and
publication recovery resolve SHA-256 in this ordinary table and delete virtual
table rows by row ID, avoiding a full FTS scan. The `message_attachments` table
is keyed by SHA-256 and attachment ordinal, with the MIME-walk part ID, decoded
filename, and normalized MIME type.
Indexing parses each message once for FTS body text and attachment metadata;
`--index-attachments` additionally writes decoded text attachments to
`attachment_fts`.
The tables are derived and are replaced together with FTS by `refresh-index`.

`.eml` export writes the bytes returned by hash-verified direct retrieval.
Finder dragging uses a temporary `.eml` file URL and the Cocoa webview's native
file/link drag support. Only the message-file icon well is draggable; the
header region remains normal selectable text. `window.print()` is handled by pywebview's WKWebView
print operation. Temporary exports live only for the application process.

The shell page permits `unsafe-eval` only for local scripts because pywebview
constructs its typed Python API wrappers with JavaScript `Function`. Message
HTML remains isolated in a sandboxed frame with its own restrictive CSP. The
`gui-smoke` Makefile target verifies that Cocoa injects the bridge and that
JavaScript can call the Python `status()` API.

`aisummarize.py` implements the `summarize` console entry point. It reads stdin
before doing any native work and invokes a content-addressed Swift helper built
into `~/Library/Caches/mailarchiver` from the packaged `apple_summary.swift`
source. The helper uses `SystemLanguageModel.default`, checks model
availability, and asks for one faithful abstractive sentence of at most 30 words while
treating input text as untrusted content rather than instructions. Neither the
input nor output is canonical archive data.

Use `uv` for dependencies and every test/run target through the repository
Makefile.  Typed Pydantic structures carry all message metadata and external
API responses; dictionaries are confined to API-boundary decoding.

`standalone_verify.py` is itself limited to the Python standard library. At
ingest startup it copies its own source atomically to
`verify_mail_archive.py` in the bag root. The installed script accepts only the
native format in [INTEGRITY_CONTROLS.md](INTEGRITY_CONTROLS.md). It validates
safe BagIt payload and tag manifests, required Mailbag metadata and CSV rows,
then streams the JSON declarations, complete-MBOX `h1` hashes,
recovered-message `h2` hashes, and semantic-message `h3` hashes. It returns
nonzero on missing, orphaned, malformed, unsupported, unsafe, unlisted, or
mismatched files. It neither imports the package nor reads SQLite.

`write_bag_checkpoint()` streams catalog locations in MBOX byte order through
`write_integrity_files()` and
uses each catalogued raw SHA-256 to resolve mboxrd `>From ` ambiguity. It then
atomically writes deterministic JSON control records followed by the TSV table.
The initial declarations are `h1` (complete MBOX, SHA-256), `h2` (recovered
RFC 5322 bytes, SHA-256), and `h3` (semantic-message version 1, SHA-256).
Semantic version 1 applies DKIM relaxed header and simple body canonicalization
to the selected stable/delivery headers documented in `INTEGRITY_CONTROLS.md`;
it includes `Delivered-To` and excludes mutable `Status` and `X-Status` fields.
The same pass emits RFC 4180 Mailbag rows through a Pydantic `MailbagRow`, so
generation does not reread the corpus to count MIME attachments. The payload
manifest reuses the computed complete-MBOX hashes. `bag-info.txt` retains its
external identifier while refreshing its timestamp and payload oxum, and the
tag manifest is written last.

## Database design

`archive.sqlite3` uses foreign keys, explicit transactions, and a schema
version table. Its complete V1 DDL lives in the
packaged `sql/V1__archive.sql` resource rather than an inline Python string.
Principal relations are:

```text
email_addresses(address_pk, address UNIQUE)
messages(message_pk, message_id_normalized, sha256, sender_address_pk, subject,
         date_utc, date_source, category,
         UNIQUE(message_id_normalized, sha256))
recipients(message_pk, address_pk)
mbox_generations(generation_pk, filename, sha256, message_count, byte_count)
locations(message_pk, generation_pk, byte_offset, byte_length)
source_volumes(source_volume_pk, identity_json, metadata_json,
               first_observed_at, last_observed_at)
source_files(source_file_pk, source_volume_pk, source_path, path_kind,
             source_kind, modified_at_ns, byte_length, sha256, checked_at,
             completed_run)
observations(observation_pk, source_file_pk, source_offset, raw_sha256,
             semantic_sha256, message_pk, disposition, run_pk, detail)
metadata_defects(message_pk, field, detail)
ingest_runs(run_pk, started_at, completed_at, result, detail)
```

`source_volumes.identity_json` is a canonical stable identity and
`metadata_json` retains the complete current OS/provider report. Local files
use a volume-relative path; future cloud adapters use provider/account/container
identities and forensic adapters may use a forensic path. `message_pk` is
nullable in `observations` so malformed and autosave-excluded source records
are still reviewable. Each observation directly stores raw (`h2`) and semantic
(`h3`) SHA-256 values for fast forensic lookup. The deduplication lookup is indexed on `(message_id_normalized, sha256)`;
`messages.sha256` has a separate index for the missing-Message-ID exception and
FTS result lookup.  Do not make
Message-ID unique.  `email_addresses.address`, `messages.sender_address_pk`,
and `recipients.address_pk` are indexed; recipient role and ordering are not
preserved. The catalog also indexes `(date_utc DESC, message_pk DESC)` for
bounded search pages, `(source_file_pk, source_offset DESC)` for ingest resume,
`(run_pk, observation_pk)` for run review, and
`(generation_pk, byte_offset, byte_length)` for ordered, covering location
reads. Category/date and category/sender indexes support reports, rebuilds, and
owner-address suppression. Expression indexes on case-folded subject and email
address support alphabetical result pages. Earlier single-column and
forensic-hash indexes remain present.

Query-plan acceptance tests cover ingest identity and checkpoint lookups,
resume, run review, provenance, MBOX integrity traversal, category/date reports,
all three result sort modes, and FTS-to-catalog SHA-256 joins. Search explicitly
selects the matching date, subject, sender, or SHA-256 index for its bounded
candidate stage. Index rebuild walks the unique mailbox-name index and covering
location-order index, avoiding a message scan and temporary sort. Full scans
remain only where the command intentionally consumes the whole result set,
such as unfiltered review, complete checkpoint generation, and aggregate
reports; grouping those complete results may still require temporary B-trees.
Leading-wildcard `to:`, `from:`, and `subject:` substring predicates cannot use
a selective ordinary B-tree, but their bounded traversal and relational joins
remain indexed.

`search.sqlite3` has its own packaged, versioned `sql/V1__search.sql` schema and
does not use cross-database foreign keys. Existing unversioned or incompatible
search databases are rejected and may be removed or rebuilt with
`refresh-index`. Its main FTS5 table includes an unindexed `sha256` column plus
searchable headers and selected body text: `text/plain` first, otherwise rendered
`text/html`, otherwise a safe single-part fallback. A second FTS5 table stores
text-attachment content only when requested, allowing the GUI to include it
without changing default body-search semantics. Binary attachment bytes are
excluded. Ordinary `message_metadata.sha256` is the indexed lookup key for the
corresponding FTS row IDs; updates and recovery delete FTS rows by row ID rather
than filtering the virtual tables on their unindexed SHA-256 columns. The
Makefile's `install-mac` and `install-linux` targets
download Apache Tika's checksum-verified application JAR to the ignored
project-local `.tools/tika/<version>/` directory; Tika remains an optional
future extractor for PDF and Office attachments, not a service.  Rebuild the
index in a temporary database,
validate row identities against `archive.sqlite3`, then atomically replace the
old search database. Live indexing and `refresh-index` both exclude
`INFECTED` and the reserved `MALFORMED` quarantine category; rebuild recognizes
numbered quarantine MBOX filenames.
Ordinary `mailsearch` listings and reports select only `Sent` and `Archive`;
the authoritative catalog still retains every quarantine record.
Bounded date-sorted listings materialize an indexed, ordered candidate page
before joining recipient rows. Year-scoped reports use half-open ISO 8601
`date_utc` ranges so SQLite can use the date index.
Normal ingest publishes canonical MBOX/catalog state first, then attempts the
disposable FTS insertion for normal Sent and Archive mail. Extraction or indexing failure records a
`search-index` metadata defect without rolling back canonical mail.

## Ingest pipeline

An ingest run executes these steps:

1. Make a lightweight pass over all roots to count recognized source files and
   bytes without hashing them. Fail before message publication if any root is
   missing or unusable. Then discover the files again into at most `--workers`
   concurrent tasks; verify the completed file/byte totals match the inventory.
   Each worker fully processes and checkpoints one file. For a never-seen file,
   ingest messages before calculating the complete source SHA-256. For a known file,
   fingerprint first to skip a complete match; for a grown MBOX, compare the
   old-length prefix and resume only at a validated appended-message boundary.
   Calculate any deferred fingerprint and publish the updated file checkpoint
   at every completed file boundary.
2. Stream exactly the RFC 5322 bytes from its source adapter.  An `.emlx`
   adapter reads the decimal length prefix, then exactly that many bytes.
3. Hash the raw RFC 5322 bytes and parse only headers needed for identity,
   classification, and exclusion.  Resolve dates from `Date:`, then
   `Received:`, then the prior resolved message date in the same input stream.
   A singleton source file may instead derive its year from a four-digit year
   in the source path; record every fallback source in the catalog.
   An unexpected parser exception records the source path, byte offset, raw
   hash, and exception before stopping; earlier messages published by any file
   worker remain committed.
4. If `X-Apple-Auto-Saved` exists, commit an `autosave-excluded` observation
   and continue.  Do not write an MBOX record.
5. Look up `(normalized Message-ID, SHA-256)`.  If it exists, commit a
   `duplicate` observation and continue without antivirus or text extraction.
6. Stream the raw message to ClamAV.  A positive result routes it to
   `INFECTED`; all other nonfatal outcomes retain it in its normal category
   while recording the result.
7. Durably journal the target MBOX, its prior size/existence, and the message
   identity, then append and flush the mboxrd-encoded raw bytes.
8. Keep message, recipient, defect, observation, and location rows in one
   catalog transaction until the append succeeds. Commit the authoritative
   catalog and clear the journal. An exception or the next ingest startup
   truncates an uncatalogued append and refreshes the BagIt/Mailbag checkpoint; a catalogued append
   is validated and retained. Index disposable search content afterward only
   for normal Sent and Archive mail.

Deduplication is deliberately before ClamAV: a known archived message has
already been scanned and classified.  `--rescan` explicitly revisits stored
messages when virus definitions or policy changes.

## MBOX mechanics and sorting

Input detection must validate a stream rather than trust filename extensions.
The MBOX reader recognizes separator lines, handles mboxrd `>From ` escaping,
and reports malformed boundaries without silently merging messages.

Writers produce an envelope `From ` line plus mboxrd-escaped message bytes under
`data/mbox/`.
They track the byte offset and byte length of each complete record.  Output
currently uses the first numbered file for each year/category; the required
3.75 GiB rollover selection remains planned. The directory contains no nested
per-message files.
For any original message lacking a final line break, standard MBOX contributes
one before its record separator. Direct retrieval considers the stored form and
the form with one writer-added final line break removed, selecting only the
candidate matching the catalogued original SHA-256. This includes the
zero-byte-message case. The implementation hashes the complete stored candidate
first, then tries removing one terminal LF and one terminal CRLF in that order;
it fails closed if no candidate has the expected hash. The MBOX-level hash still
covers every stored byte.
The standard-library writer's `>From ` representation is ambiguous when the
source already contained a literal `>From ` line. The reader enumerates a
bounded set of quote interpretations and selects only the candidate matching
the authoritative raw-message SHA-256. Candidates are yielded once and not
retained as a second in-memory copy of the message set; unresolved
high-ambiguity input fails closed. The installed stdlib verifier uses the same
bounded interpretation order independently.

Planned run-completion sorting will order each touched normal mailbox by
`(resolved_date_utc, sha256)`. The sorter will write a new MBOX under
`data/mbox/` and its integrity tag
under `integrity/`,
scan both end-to-end, compare the unordered identity sets, then atomically
update the relevant `mbox_generations`/`locations` rows. It must retain the old
file until validation succeeds and delete it only then. `INFECTED` and
`MALFORMED` quarantine mail is not FTS-extracted and is not moved into a normal
mailbox.

## Antivirus and text extraction

Homebrew installed these commands:

```text
/opt/homebrew/bin/clamscan
/opt/homebrew/bin/clamdscan
/opt/homebrew/bin/freshclam
/opt/homebrew/sbin/clamd
```

`clamd` is the normal on-demand scanner: it loads the signature database once
and accepts scans through `/private/tmp/clamd.sock`; the archiver starts it
for a run when needed and stops it after the run unless an operator has
already started it.  `clamscan` remains a diagnostic fallback.  Neither an
on-access scanner, a login service, nor a scheduled scan is enabled.  Run
`freshclam` only when an operator explicitly wants new signatures.
`MAILARCHIVER_CLAMD`, `MAILARCHIVER_CLAMDSCAN`, `MAILARCHIVER_CLAMD_CONFIG`,
and `MAILARCHIVER_CLAMD_SOCKET` override the macOS Homebrew defaults for a
separately configured local environment such as CI.

Current MIME traversal uses the standard-library parser without explicit size,
recursion, time, or decompression limits. Plain text and rendered HTML are
indexed first for normal mail; optional extraction covers decoded text
attachments only. Future binary attachment adapters need explicit bounds and
must record failures without affecting preservation. Quarantine categories are
omitted from FTS entirely.

## Planned remote sources

Gmail will use least-privilege OAuth where the required raw-message read scope is
available, paginates message IDs, fetches raw bytes and labels, and records
Gmail ID/thread ID/labels as provenance.  Incremental Gmail sync stores the
last successfully committed history checkpoint, with a complete-list fallback
when history has expired.

IMAP will use TLS and read-only SELECT/EXAMINE where supported. It will enumerate
folders and UIDs, fetches RFC 5322 bytes without setting `\\Seen`, and stores
UIDVALIDITY plus UID so server reset/reuse is detectable.

The `--days N` option uses `newer_than:Nd` on `messages.list`; `--after`
accepts an epoch for timezone-precise collection.  Google Takeout is an MBOX directory input.  The program does not automate
personal Takeout creation or download.

## Planned source adapters and derivatives

PST/OST, Eudora, and working IMAP caches are local read-only adapters, not
remote-source modes. Each adapter produces a typed source record containing
the available RFC 5322 bytes, source-native identity and folder context,
completeness state, and extraction provenance. A proprietary-store parser or
converter is isolated behind that interface and its name and version are
stored with every run. Acceptance fixtures include corrupt and partial stores
so item-accounting and error reporting are tested, not merely successful
conversion. Candidate third-party components must be evaluated for byte
fidelity, maintained format coverage, licensing, streaming behavior, and
repeatable output before selection.

The Eudora adapter treats mailbox data, table-of-contents files, attachment
directories, and embedded-content directories as one source package while
retaining the physical origin of every recovered component. The IMAP-cache
adapter has layout-specific readers and emits explicit incomplete records for
headers-only placeholders, evicted bodies, and detached parts. It never falls
through to a network fetch.

Redaction is a separate derivative pipeline over hash-verified canonical
messages. A versioned Pydantic policy selects header values, body spans, MIME
parts, attachments, or derived entities; the exporter writes a new corpus plus
an access-controlled audit manifest linking each output to its canonical hash
and transformation decisions. The canonical archive and catalog remain
unchanged. Research tables likewise remain rebuildable and carry extractor,
schema, and policy versions so correspondent, thread, entity, attachment, and
provenance reports can declare how they were produced.

## Validation and tests

Tests use small, hand-authored MBOX and EMLX fixtures covering mboxrd quoting,
bad dates, missing IDs, same-ID/different-content messages, autosaves,
duplicate source trees, interruption recovery, and infected routing. The EICAR
signature is assembled from fragments only in a temporary test source and that
file is deleted immediately after ingest; the repository contains only a safe
message template. Tests assert message identities and bytes, not only record
counts. Rollover and typed
unscannable/scanner-error outcomes remain uncovered because those behaviors are
not implemented. The separately runnable `make test-e2e` target starts a fresh
CLI ingest with the real configured on-demand `clamd`, includes a source message
without a final newline, requires checkpoint publication, and invokes the
installed standard-library-only verifier under isolated Python.

`make test` runs the ordinary test tree, while `make check` runs it followed by
the separate end-to-end suite. The tracked source corpus has enough messages to
exercise real result pagination and rich MIME behavior. On macOS, the same target drives the actual
pywebview/WKWebView application through its Python bridge and tests all shipped
search-interface behavior. Linux runs the complete ingest and verification
portion and explicitly skips the native macOS UI portion. `make test-bagit`
validates the database-independent three-message fixture and corruption cases.
The installed `verify_mail_archive.py
DIRECTORY` performs read-only validation of a supplied bag. The first acceptance run is against a copied
small subset of `SLG Mail`, followed by a full read-only inventory comparison
before any canonical archive is published.

## Delivery sequence

1. Create the package, configuration model, versioned fresh schema, MBOX/EMLX
   readers/writer, and `verify` command.
2. Implement recursive local ingest, exact dedupe, autosave exclusion,
   integrity files, sorting, recovery, and ClamAV routing.
3. Add `review`, `refresh-index`, FTS5 rebuilding, and conservative body/HTML extraction.
4. Add IMAP and Gmail importers with resumable checkpoints.
5. Build the local search/view interface on the stable database and MBOX
   retrieval API.
