# Project direction

## Positioning

`mailarchiver` should be an email preservation and corpus-building system, not
another mail client, migration utility, or enterprise compliance appliance.

> Harvest heterogeneous email collections into a verifiable, independently
> maintainable corpus, then derive search, research, and redacted-access
> products from it.

The distinctive problem is not merely displaying or searching email. Existing
tools already do that well. The project should make it easy to discover email
on old media, account for every source item, normalize supported content into
an open archive, verify it independently, search it as one collection, and
produce documented research or access derivatives without changing the
preserved record.

## Strategic decision

Continue conditionally, with a narrower product boundary. Current ePADD is a
credible implementation of appraisal, preservation export, restriction review,
message redaction, correspondent/entity exploration, discovery, and delivery.
This project must not reproduce those functions merely with a different stack.

The candidate gap is the layer before and beneath ePADD:

* recursive, read-only discovery across heterogeneous personal backup media;
* explicit accounting for every encountered source item;
* original-byte custody and independently specified per-message integrity;
* content-safe deduplication while retaining every source observation;
* resumable accumulation across repeated accessions and decades; and
* reproducible, privacy-aware corpus exports for ePADD and DH tools.

That gap is plausible but not yet proven. Development beyond the existing
archive foundation should pause until a comparative ePADD bake-off establishes
it. If ePADD can meet these requirements through a modest upstream change, the
correct direction is to contribute that change and use ePADD. If it cannot,
`mailarchiver` should remain a focused acquisition and corpus-foundation tool
that hands off review and access to ePADD.

## Product principles

1. **Source media is read-only.** Never modify a backup, mail client profile,
   local cache, remote mailbox, or source export.
2. **The preservation object is independently usable.** The native BagIt /
   Mailbag payload, metadata, and integrity declarations must remain readable
   and verifiable without the application or either SQLite database.
3. **Account for omissions as carefully as successes.** Every encountered item
   must have a disposition; unsupported, corrupt, incomplete, and reconstructed
   records must not disappear silently.
4. **Keep evidence, analysis, and publication separate.** Search indexes,
   entity extraction, redaction, and access renderings are derivatives.
5. **Prefer established components and standards.** Build orchestration and
   missing preservation behavior rather than new PST parsers, bag formats, NLP
   pipelines, or specialist review systems.
6. **Usability is a core preservation feature.** Installation, source
   discovery, progress, error explanations, interruption recovery, and
   verification must work for users who are not software developers.
7. **Make interoperability bidirectional.** Import established formats and
   export documented packages that other archival, research, and review tools
   can consume.

## Three-layer architecture

The system should maintain explicit boundaries among three layers:

1. **Canonical evidence**
   A native Mailbag with byte-preserving MBOX payload where original RFC 5322
   bytes exist, BagIt manifests, versioned per-message integrity declarations,
   source observations, source-native identifiers, and essential reconstruction
   provenance.
2. **Rebuildable analysis**
   Search indexes, normalized correspondents, aliases, threads, MIME and
   attachment relationships, named entities, topics, parse defects, and
   research tables. Every extractor and model is versioned.
3. **Publication derivatives**
   Separate restricted or redacted Mailbags, static access sites, data tables,
   and packages for ePADD or e-discovery systems.

```text
backup drives, exports, caches, and read-only accounts
                         |
                         v
              inventory and source adapters
                         |
                         v
          native Mailbag + .integrity + provenance
                    /              \
                   v                v
       search/research databases   redacted bags and access exports
```

An NLP model, redaction policy, UI migration, or database failure must never
change the canonical message record.

## Build on the existing ecosystem

### RATOM and libratom

[RATOM](https://bitcuratorconsortium.org/review-appraisal-and-triage-of-mail-ratom/)
produced [libratom](https://github.com/libratom/libratom), a reusable Python
library for PST and MBOX traversal, reports, SQLite output, attachment metadata,
and spaCy entity extraction. It should be the first candidate backend for
PST/OST and entity-analysis work. Its default branch has not advanced since
December 2022, so first confirm maintenance, supported platforms, and whether
BitCurator intends to resume or supersede it.

Do not fork libratom or write another PST parser without first attempting an
upstream collaboration. Contribute preservation-oriented capabilities that are
useful to both projects:

* streaming bodies and attachments;
* complete item and folder accounting;
* source-native item identifiers and properties;
* typed structured errors;
* explicit original, reconstructed, recovered, and incomplete states; and
* a stable source-adapter API.

libratom should remain a replaceable backend. Its current high-level PST
formatter combines transport headers with one selected body and does not create
a complete attachment-bearing MIME message. `mailarchiver` must consume the
source-native components, construct any required RFC 5322/MIME representation,
and record that reconstruction. It must not describe reconstructed MIME as the
original wire-format message.

The BitCurator relationship is an opportunity to coordinate roadmaps, fixtures,
and upstream changes. A concrete first step is a joint discussion about the
libratom API, PST/OST fidelity gaps, supported platforms, and a shared
conformance corpus.

### Mailbag and mailbagit

[Mailbag 1.0](https://archives.albany.edu/mailbag/spec/) supports MBOX; it does
not propose replacing it. Mailbag packages one or more representations such as
MBOX, EML, PST, MSG, PDF, or WARC inside BagIt. Its `mailbag.csv` maps messages
among representations and records folder paths, attachment counts, headers,
and errors.

Mailbag's multiple representations answer different needs:

* MBOX and EML retain structured email for computation and reuse.
* PST or MSG may be the actual received source and may contain properties lost
  during conversion.
* PDF provides a familiar document-like access representation.
* WARC can retain remote images, CSS, and other web resources.

Use Mailbag as the native working and interchange layout. Canonical MBOX files
live under `data/mbox/`; richer per-message declarations live in the BagIt tag
directory `integrity/`; `mailbag.csv` maps every message; and SHA-256 payload
and tag manifests provide standard file fixity. A published checkpoint is a
complete Mailbag even though the working directory becomes temporarily invalid
while an ingest appends data. The tag manifest is written last so interruption
cannot be mistaken for a valid new checkpoint.

Mailbag does not replace the ingestion engine: append/resume, global
deduplication, source observations, raw-byte recovery, semantic hashes, unified
search, and research tables remain mailarchiver responsibilities. Use
[mailbagit](https://github.com/UAlbanyArchives/mailbagit) as an independent
validator and for future derivative conversions only where it does not move or
reserialize canonical content. Restricted or redacted releases are separate
Mailbags with their own payload and manifests.

PDF and WARC generation is opt-in. Rendering untrusted message HTML can contact
remote resources, activate trackers, or reveal details about the processing
environment. Generate these only in a sandboxed, explicitly authorized
publication workflow.

### ePADD

[ePADD](https://github.com/ePADD/epadd) already provides archivist appraisal,
preservation export, restriction review, message redaction, correspondent and
entity exploration, cross-collection discovery, and researcher-facing access.
Its optional
[Emailchemy integration](https://www.epaddproject.org/using-epadd/emailchemy-for-epadd)
also imports PST, OST, Eudora, Apple Mail, Thunderbird, Google Takeout, and many
legacy clients. Do not recreate its specialist workflow. Export selected
corpora to ePADD with stable message identifiers, hashes, provenance, and
restriction metadata.

Current source still leaves a distinct custody gap: ePADD's duplicate signature
normally omits the body, while its preservation exporter may add headers and
reconstruct multipart MIME. Those behaviors may be acceptable for ePADD's
processing model but do not meet an exact original-byte contract. Confirm this
with black-box fixtures and discuss the result with the maintainers before
designing a competing subsystem.

The preferred relationship is one of these, in order:

1. Contribute the missing integrity, provenance, or adapter behavior directly
   to ePADD when it fits its architecture.
2. Maintain `mailarchiver` as a small companion acquisition layer that emits
   ePADD-ready MBOX plus structured provenance.
3. Implement a standalone downstream feature only when ePADD cannot support a
   demonstrated requirement and no interoperable handoff is possible.

ePADD's current tree still declares LIBSVM, but the concrete historical use was
an experimental candidate-name classifier in its older named-entity pipeline.
Current ePADD uses its own NER model path. There is no reason for this project
to adopt LIBSVM merely because it appears in ePADD's dependency list.

### Other tools

Use specialist tools as peers rather than targets for complete feature parity:

* notmuch and public-inbox are search and scale references;
* Piler and MailStore are operational UX references;
* Relativity, Purview, Nuix, and Jatheon are redaction and review references;
* Emailchemy and Aid4Mail are optional recovery or comparison paths for
  proprietary formats; and
* ePADD, EML, CSV, Parquet, GraphML, and CMIF are interoperability targets.

See [competitive_analysis.md](competitive_analysis.md) for the complete survey.

## Comparative continuation gate

Use one synthetic and one permission-cleared real corpus to compare current
ePADD, ePADD plus Emailchemy, and `mailarchiver`. Include:

* identical Message-IDs with different bodies or recipients;
* byte-identical messages observed in multiple files and folders;
* malformed MIME, invalid encodings, embedded messages, and attachments;
* MBOX, EML, Maildir, Apple Mail, PST/OST, Eudora, and a partial IMAP cache;
* changed or grown sources and an interrupted ingest; and
* filenames and headers that exercise Windows/POSIX incompatibilities.

Measure outcomes rather than feature labels:

1. Were sources left unchanged?
2. Is every encountered item reconciled as archived, duplicate, excluded,
   incomplete, unsupported, or error?
3. Can every original RFC 5322 message be recovered byte-for-byte?
4. Are reconstructed messages and parser versions identified?
5. Does deduplication distinguish different content while retaining every
   observation and folder occurrence?
6. Can the process resume safely and ingest later accessions?
7. Can a clean system independently verify and read the preservation object?
8. Can a researcher reproduce CSV/Parquet, network, and CMIF exports from a
   declared corpus without depending on hidden application state?
9. Can a nontechnical user install and run the workflow successfully?

Record the comparison as a checked-in, repeatable conformance report. A failure
is not automatically a reason to build here: first determine whether a small,
generally useful upstream ePADD or converter change would fix it.

Continue `mailarchiver` as a standalone product only if the bake-off confirms
material gaps in source reconciliation, byte custody, repeated accession, or
research reproducibility. If those requirements are already met, stop product
development and contribute the useful fixtures, integrity format, or adapter
work upstream.

## Inventory before ingestion

Add a read-only inventory mode before expanding the set of ingestion adapters:

```console
mailarchiver inventory /Volumes/OldBackup
```

It should report:

* detected mail formats, clients, profiles, accounts, and folders;
* source files, byte counts, timestamps, and complete-file hashes;
* estimated or exact item counts with a stated confidence;
* PST/OST, Eudora, Apple Mail, Thunderbird, Maildir, MBOX, EML, and supported
  IMAP cache layouts;
* detached attachment directories and companion files;
* headers-only placeholders, evicted bodies, partial downloads, and stale
  indexes;
* inaccessible, malformed, encrypted, unsupported, and ambiguous artifacts;
  and
* which adapter and version would handle each source.

Inventory establishes the denominator against which ingest completeness is
measured and lets the user correct configuration before canonical output is
created.

## Typed source-adapter contract

Every adapter should emit the same Pydantic source record, including:

* original RFC 5322 bytes when the source contains them;
* source-native store, folder, and item identifiers;
* physical source and companion-file locations;
* original, reconstructed, recovered, partial, or unsupported status;
* headers, body variants, and streaming attachments when reconstruction is
  required;
* parser or converter name, version, configuration, and diagnostics; and
* all defects and missing components.

The adapter boundary must allow more than one implementation for a format.
PST/OST should have a conformance corpus processed by at least two independent
extractors. Differences in item counts, headers, bodies, attachments, folders,
or deleted-item recovery must be reported rather than silently resolved.

Prioritize adapters in this order:

1. Compare Emailchemy's ePADD integration with libratom/libpff on PST, OST,
   Eudora, and Apple/Thunderbird sources. If Emailchemy preserves the required
   content and reports failures adequately, support it as the usability-first
   backend instead of immediately duplicating its format coverage.
2. Add open libratom/libpff adapters where independence, batch automation,
   source-native properties, or error accounting materially improve on that
   baseline.
3. Add working IMAP client caches, with explicit incomplete-item reporting;
   this is the least-served source class in the surveyed products.
4. Reuse or collaborate with Bichon or MailVault for live IMAP before writing
   another connector. Gmail and ordinary live IMAP are already well served.

## Provenance and completeness

Source accounting should be a flagship feature. For every message, the system
should be able to answer:

* Where was it observed?
* How many copies were found?
* Were those copies byte-identical?
* Which parser or converter handled them?
* Was the RFC 5322/MIME representation original or reconstructed?
* Were folders, bodies, headers, or attachments incomplete?
* Why was an encountered source item not placed in ordinary canonical mail?

For each source scope, enforce the reconciliation invariant:

```text
encountered = archived + duplicates + exclusions + incomplete
            + unsupported + errors
```

Every term must be queryable. There must be no unexplained remainder.

## Preservation acceptance criteria

The first product milestone is a trustworthy archive, not a broad analysis UI.
It requires:

* complete read-only verification and recovery commands;
* independent verification without SQLite or the installed package;
* recovery with an independent MBOX implementation;
* deterministic interruption, process-death, and disk-full recovery;
* rollover and sealed-generation behavior;
* explicit accounting for changed, grown, corrupt, and inaccessible sources;
* exact original-byte recovery from mboxrd quoting where raw bytes exist; and
* documented reconstruction when a proprietary store has no original MIME.

The central independence test is to copy the BagIt payload and manifested
Mailbag/integrity tags to a clean computer and recover and verify every message
using ordinary, independently available tools.

Sorting or compaction should create a new verified generation. It must never be
an unexplained in-place rewrite of the only canonical copy.

## Redaction direction

Use ePADD's current message-redaction, permission-label, restriction, and
discovery workflows first. Do not implement a competing review interface.
Export stable identifiers and source hashes so ePADD decisions can be related
back to the canonical corpus.

Only build a separate derivative engine if the bake-off shows that ePADD cannot
produce a required auditable or safely publishable result. In that case, the
engine should support:

* removing or replacing selected header values;
* redacting selected body spans;
* removing complete MIME parts or attachments;
* consistently pseudonymizing selected addresses;
* preview and human approval before export;
* versioned policy, reason, operator, source hash, and output hash records; and
* leakage checks covering messages, attachments, indexes, previews, filenames,
  manifests, and reports.

Automated entity or PII models may suggest redactions later. They should not
silently determine the public record. Canonical-to-redacted audit mappings may
be more sensitive than the published corpus and require separate access
controls.

## Research database direction

The digital-humanities database should be a reproducible corpus API rather than
a collection of UI-specific tables. It should represent:

* messages and every source observation;
* people, addresses, and versioned alias assertions;
* sender and recipient roles;
* threads and alternative versioned threading results;
* MIME and attachment relationships;
* resolved dates, original values, sources, and confidence;
* named entities and topics with extractor and model versions;
* restrictions, redactions, and corpus-selection policies; and
* parsing defects and incomplete evidence.

SQLite is the local query format. Documented CSV and Parquet exports support
other research environments. GraphML and node/edge CSV support network tools.
Selected, sufficiently described correspondence should export to the TEI
Correspondence SIG's
[Correspondence Metadata Interchange Format](https://correspsearch.net/en/documentation.html)
(CMIF), with authority identifiers and links back to stable message or
collection references. CMIF is a scholarly derivative, not a complete email or
preservation representation.

Every report, notebook, or export should state its corpus identity, selection
and redaction policy, database schema version, extractor versions, and known
omissions. Provide both identified and policy-governed pseudonymized views when
appropriate; record the transformation because privacy controls can change the
meaning of network and textual analysis.

Support named, reproducible corpus definitions, for example:

```text
all complete messages observed before 2026-08-22,
excluding quarantine and records restricted by donor-2026-v2
```

Derived identities, threads, entities, and topics must never replace original
message bytes or headers.

## Search and user experience

Search is necessary but not the primary differentiator. Keep it local, fast,
and familiar while focusing on preservation-specific value:

* one query across all sources and decades;
* verified retrieval from canonical MBOX;
* ordinary terms plus familiar `to:`, `from:`, `subject:`, and date filters;
* provenance, reconstruction, completeness, quarantine, and restriction
  indicators in results;
* correspondent, source, folder, thread, and attachment filters; and
* saved corpus queries reusable by research reports and exports.

The primary workflow should be understandable without archival terminology:

1. Choose a source or backup drive.
2. Review the inventory and warnings.
3. Choose or create the destination archive.
4. Ingest with clear progress and safe interruption.
5. Review the reconciliation report.
6. Verify the archive.
7. Search, report, redact, or export.

Errors should state what happened, what was safely completed, whether any item
is missing, and the next recovery action.

## Installation and distribution

Extreme usability requires a supported installation path that does not ask
ordinary users to assemble C libraries, Java runtimes, mail converters, and NLP
models manually.

Target:

* a signed macOS application or self-contained command distribution;
* an equivalent Windows distribution for Outlook backup processing;
* tested Linux packages or a container for institutional processing;
* reproducible `uv` development and source installations;
* bundled or automatically provisioned, checksum-verified parser components;
  and
* no persistent services or system configuration changes by default.

If libratom/libpff is adopted, provide tested binary bindings or a bundled
runtime. Compilation may remain a developer path, but it is not an acceptable
default user experience.

## Explicit non-goals

Do not initially pursue:

* enterprise SMTP journaling or regulatory capture;
* legal-hold certification or automated retention deletion;
* Slack, Teams, SMS, social-media, or voice archiving;
* enterprise tenancy, supervision, or complex role management;
* a hosted service or proprietary database as the archive of record;
* AI-generated summaries as canonical metadata;
* automatic destructive cleanup of sources; or
* feature parity with ePADD, Relativity, Nuix, or commercial compliance suites.

These markets are served by mature products and would distract from the unique
preservation, provenance, interoperability, and usability objectives.

## Development sequence

### 0. Continuation gate

Run and publish the ePADD/Emailchemy comparison. Meet with ePADD and BitCurator
maintainers about the verified gaps. Decide whether the next work belongs in
ePADD, libratom, a companion harvester, or this standalone project.

### 1. Trustworthy Archive v1

Deliver inventory, source reconciliation, standalone verification, recovery,
rollover/sealing, adapter contracts, and the independent-reader acceptance
test.

### 2. Legacy Harvest v1

Select PST/OST and Eudora backends from the Emailchemy/libratom bake-off,
deliver the missing offline IMAP-cache layouts, and require conformance
fixtures and explicit incomplete-item reporting for every supported backend.

### 3. Corpus v1

Deliver richer recipient roles, source folders, aliases, threads, attachment
relationships, reproducible corpus selections, and CSV, Parquet, GraphML, and
CMIF exports. Deliver the ePADD handoff here so researchers can use its mature
review and exploration interface early.

### 4. Privacy interoperability

Round-trip ePADD restriction, permission, and redaction decisions where
possible. Add a separate deterministic redaction engine only for requirements
that ePADD cannot meet, with audit manifests, separate restricted mappings, and
automated leakage validation.

### 5. Interoperability v1

Deliver generic EML export and any remaining institutional-package adapters.
Validate every interchange format with independent tooling.

### 6. Derived intelligence, only if unmet

Prefer ePADD and ordinary DH tools for entity extraction, topics,
communication networks, and review. Add new analysis only after the
preservation and reproducibility contracts are stable and a concrete scholarly
use case cannot be satisfied through interoperable exports.

## Measures of success

The project is succeeding when:

* its continuation is justified by a published comparison rather than assumed;
* a nontechnical user can install it and inventory a drive without preparing a
  development environment;
* every source item is reconciled with no unexplained loss;
* repeated ingest is safe and idempotent;
* two independent tools can recover and verify canonical mail;
* PST/OST extractor disagreements are visible and testable;
* search works across decades without becoming canonical state;
* a redacted release contains no known leakage and links cryptographically to
  its source transformations;
* a Mailbag export validates independently and opens in other tools;
* ePADD can consume a selected corpus without a bespoke manual conversion; and
* a research report or CMIF/network export can be regenerated from its corpus,
  schema, model, and policy versions.

The defensible project niche, if the continuation gate confirms it, is open,
forensic-quality harvesting and corpus normalization for long-lived personal
and scholarly email collections, offered through an unusually simple and
interoperable user experience. Without that evidence, the project should
become an upstream contribution rather than another archive product.
