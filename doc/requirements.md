# Mail archive normalizer requirements

## Purpose

Create and maintain a cleartext, personal, long-lived archive of all user
email. The archive is canonical; all databases and user interfaces are derived
from it and may be recreated. It must support unified search across decades of
mail, non-destructive redacted derivatives, and reproducible research reports
from structured metadata.

The system must harvest backup drives and active sources, including Outlook
`.pst` and `.ost`, Eudora backups, Emacs RMAIL Babyl, working IMAP client-cache
directories, MBOX, EML, Maildir, Apple Mail, Gmail exports, and live read-only
IMAP accounts. It must be safe to rerun an ingest operation on the same source
without duplicating or rescanning messages already archived.

## Canonical archive layout

All deliverables reside in one archive directory. That directory is a native
BagIt 1.0 bag conforming to Mailbag 1.0. It contains `bagit.txt`,
`bag-info.txt`, `mailbag.csv` or its required numbered parts,
`manifest-sha256.txt`, `tagmanifest-sha256.txt`, a top-level `integrity/` tag
directory, and a `data/mbox/` payload directory.

* Mail is stored only under `data/mbox/` in standard MBOX files, using
  byte-preserving mboxrd quoting. Do not retain a per-message EML corpus.
* Normal mail is partitioned by resolved message year and category:
  `{YEAR}-Sent1.mbox` and `{YEAR}-Archive1.mbox`.
* A file rolls over before it reaches 3.75 GiB.  Later parts are named
  `{YEAR}-Sent2.mbox`, `{YEAR}-Sent3.mbox`, `{YEAR}-Archive2.mbox`, etc.
* Messages detected as infected are instead placed in `INFECTED1.mbox`, with
  the same numeric rollover rule if needed.  They are never discarded or
  altered.
* A message's category is **Sent** if the parsed `From:` address contains,
  case-insensitively, one of names in owner-names.txt; otherwise it is **Archive**.  The aliases are
  configurable, and the matched address is retained.
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
  date is absent.
* `X-Apple-Auto-Saved` messages are excluded entirely.  Their source and
  exclusion reason are retained in the primary database, but no MBOX copy is
  created.
* An MBOX record whose envelope sender is exactly
  `mbcp@s.eecs.harvard.edu`, whose only headers are `X-UID`, `Status`, and
  `X-MBCP-Flags` (with both X-headers present), and whose body is empty is
  source metadata, not an email. Record a `source-metadata-excluded`
  observation and do not publish it. An envelope sender of `XXX` is unwrapped
  only when the outer record contains only status headers and its body starts
  with a quoted nested MBOX envelope; publish the nested RFC 5322 message while
  retaining the outer source offset as provenance.
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

`archive.sqlite3` and the disposable `search.sqlite3` are operational BagIt
tag files but are deliberately not listed in the tag manifest; their live
SQLite state is outside the portable preservation checkpoint.
The top-level `status/` directory likewise contains operational, unmanifested
JSON tag files. Each ingest creates a distinct file and atomically replaces
only that file with its current typed status; the final replacement retains
the run's complete statistics as append-by-run history.

The archive lives on the encrypted laptop filesystem.  BorgBackup and
Backblaze provide independent backup; archive-internal encryption is not a
requirement.

Read-only data-quality audit tools may create derived MBOX, CSV, and JSON
evidence from a source tree and canonical archive. Those outputs contain
private message content and metadata, must default to an ignored temporary
directory, and must never be committed. The tools must refuse to overwrite
existing evidence and must not modify source mail or the canonical archive.

## Deduplication and provenance

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
  separate from normal ingest.
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

* Each new message is streamed to ClamAV before normal archiving.
* The local CLI currently requires the `--clamav` switch.  This starts one
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
  its socket appears.
* Control-C is a graceful stop: close scanner and MBOX resources, commit
  completed messages and observations, publish a complete BagIt/Mailbag
  checkpoint, report interruption,
  print the standard archive report for the completed partial run, and return
  exit status 130 without a traceback.  An `ENOSPC` MBOX append
  must be rolled back to the prior file size where possible, reported, and
  stopped without silently treating the message as archived.
* Every ingest run records its completion time, result, and failure detail.
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
implemented; the optional Tika installer only downloads and verifies the JAR.
The search database must be fully rebuildable from the
canonical MBOX files and `archive.sqlite3`, and is not backed up as a required
preservation object.
It excludes `INFECTED` and `MALFORMED` quarantine categories even when
attachment indexing is requested. `refresh-index` applies the same exclusion
when rebuilding from canonical MBOX files.
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
lookups. Bounded date-ordered listings must use the date/message index to
select the requested page before recipient aggregation. Year-scoped reports
must express their bounds as indexed `date_utc` ranges rather than applying a
function to every stored date.
Index extraction and insertion happen after canonical MBOX/catalog publication.
An indexing failure is recorded as a metadata defect and does not reject mail;
`refresh-index` repairs missing disposable content.

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
retains the CLI meaning of a subject selector plus a free-text term.  It loads
the newest 100 results at a time without changing the CLI's ten-result default.
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
independent message window. The result list can sort by date, subject, or
sender in either direction. When it has keyboard focus, Up Arrow and Down
Arrow move the selection and display the newly selected message. Result rows
show the indexed attachment count with a paperclip.
An unchecked **Search attachments** control searches only headers and message
bodies. When checked, the same ordinary full-text expression also matches the
separate indexed text-attachment table; metadata selectors retain their normal
meaning. The control does not extract attachment content on demand.
The GUI paints each result page from header metadata first, then requests its
indexed body previews on a background worker and fills a reserved third line
without blocking the initial result display.

The bottom of the main GUI contains a clickable ingest-status line. During a
run it shows live completion, message count, active/configured workers, and ETA;
otherwise it summarizes the latest run. Clicking it opens a separate native
Ingests window with its own close box. The **Windows → Ingest** menu opens the
same window. That window browses every retained status file and shows the
selected run's aggregate statistics, sources, failure detail, and all worker
threads. If it is already visible, either action brings it to the front and
selects the requested run instead of creating a duplicate window.

The GUI lists every non-attachment `text/plain` and `text/html` MIME part and
allows the user to select among them.  HTML is isolated and sanitized. Scripts,
forms, plugins, file URLs, and remote resources are blocked by default; remote
HTTP(S) images may load only after an explicit per-message action. Embedded
CID images may render from the verified message. Attachments appear in a list,
safe images and PDFs can be previewed inline, and opening any attachment is an
explicit action with an additional warning for executable or container types.
At the bottom of every message view, the GUI displays the archive mailbox path
separately from every source volume and source/forensic path where the message
was found.
When `date_source` is `received-median`, the GUI shows a warning banner across
the message and gives the message well a slight red tint. The original `Date:`
header remains visible and unchanged.
Command-1 through Command-9 select the MIME part having that numeric part ID;
Command-0 and Command-Shift-U select the raw RFC 5322 source.

Saving or dragging a message creates a disposable `.eml` copy containing the
exact SHA-256-verified RFC 5322 bytes; it never creates or changes canonical
archive content. Message headers remain selectable text. Dragging is confined
to a separate message-file icon well and is initially a macOS Finder
integration. Printing
prints the displayed headers and selected MIME part through the system print
panel. Temporary message and attachment exports are removed when the GUI exits.

`summarize` is an optional macOS command that reads nonempty UTF-8 text from
standard input and prints only a one-sentence Apple Intelligence summary of at
most 30 words to standard
output. It uses Apple's on-device Foundation Models framework, never writes the
input to the archive, and reports a clear error when the device is ineligible,
Apple Intelligence is disabled, or the model is not ready.

All archive commands use `MAIL_ARCHIVE_DIR` as their default archive directory.
`--archive DIRECTORY` overrides that environment variable. If neither is set,
the command fails before reading or writing an archive.

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
* Gmail ingest uses OAuth and the Gmail API for incremental acquisition of
  raw messages and labels.  It supports a rolling `--days N` mode using
  Gmail's `newer_than:Nd` query.  Google Takeout MBOX is supported as an offline,
  one-time baseline input; personal Takeout is not assumed to be
  programmatically triggerable.
* IMAP ingest supports TLS and authenticated account configuration, records
  account/folder/UID provenance, and retrieves RFC 5322 bytes without marking
  messages read or modifying the remote mailbox.
* Outlook `.pst` and `.ost` ingest does not require Outlook to modify or export
  the source. The adapter records the parser/converter and version, enumerates
  every encountered store item, preserves folder and item identifiers as
  provenance, and reports corrupt, deleted, partial, or unsupported records
  rather than silently omitting them.
* Eudora ingest recognizes mailbox files together with their table-of-contents,
  attachment, and embedded-content conventions. It records which companion
  files were present and never treats an absent or stale index as proof that a
  message or attachment does not exist.
* Working IMAP client-cache ingest is an offline, read-only source distinct from
  live IMAP. It recognizes supported cache/profile layouts, records account and
  folder context when recoverable, and explicitly reports placeholders,
  evicted bodies, partial downloads, and detached parts. It does not contact a
  server unless the user separately configures and authorizes live IMAP ingest.
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

* At the end of an ingest run, touched normal MBOX files are sorted by
  resolved timestamp and then message SHA-256 for deterministic ties.
* Sorting writes a same-directory temporary replacement and preserves the
  prior file as a backup.
* The replacement and backup are parsed end-to-end.  Their unordered sets of
  `(Message-ID, SHA-256)` must match exactly before the backup is deleted.
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
  checks succeed. `review` queries the
  committed source-observation log by run, source, and disposition. Derived
  catalog fields and canonical locations are created correctly during ingest.

## Scope boundaries

The first release is a local command-line normalizer and verifier.  Its TOML
configuration holds archive and scanner policy; `owner-names.txt` remains a
separate, one-name-per-line reusable classification input.  A local
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
