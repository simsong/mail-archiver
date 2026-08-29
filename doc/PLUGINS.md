# Ingest plug-ins

Mail Archiver has two independent generator plug-in layers:

1. a **source plug-in** enumerates mail containers and streams mail objects from
   a source system; and
2. the built-in local source delegates each recognized filename to a
   **file-parser plug-in**.

Plug-ins do not create threads, render status, scan messages, open the archive
catalog, deduplicate mail, or publish canonical MBOX. The framework owns those
operations. This keeps the same source plug-in usable with one worker or many
workers and with either the terminal dashboard or redirected logging.

The implemented flow is:

```text
packaged + explicitly trusted plug-in directories
    |
    v
validate every plugin.toml, then import code and freeze registries
    |
    v
SourcePlugin.discover(SourceSpec)
    -> MailContainer | ProgressEvent | SkippedInput
    |
    | framework snapshots, deduplicates, verifies stable inventories,
    | and fairly orders concurrency keys
    v
framework integrity plan
    |
    v
SourcePlugin.messages(MailContainer, resume_cursor)
    -> MailObject | ProgressEvent
    |
    | local source delegates to FileParserPlugin.messages(...)
    v
parse -> ClamAV -> deduplicate -> publish -> integrity completion and checkpoint
```

## Framework responsibilities

The framework owns:

* plug-in discovery, manifest validation, deterministic ordering, and registry
  freezing before inventory;
* source selection and rejection of ambiguous matches;
* inventory, the global worker pool, cancellation, and per-source concurrency
  limits;
* status aggregation and all terminal or log output;
* execution and persistence of source-selected integrity controls;
* SHA-256 of each transferred RFC 5322 message, deduplication, and ClamAV
  routing;
* catalog transactions and source-integrity checkpoint commits;
* canonical MBOX publication; and
* the archive format's fixed BagIt, Mailbag, `h1`, `h2`, and `h3` integrity
  controls.

A plug-in is read-only with respect to its source. It yields typed values and
may read its source to produce those values. It must not print, start workers,
or write to the archive.

## Versioned contracts

The public API is in `mailarchiver.plugin_api` and currently has API version 1.
All boundary values are immutable Pydantic models with unknown fields rejected.
The principal models are:

* `PluginManifest` and `PluginCapabilities`;
* `SourceSpec`, `SourceReference`, and `FileProbe`;
* `MailContainer`, `MailObject`, `ProgressEvent`, and `SkippedInput`;
* `IntegrityDecision` and `IntegrityEvidence`; and
* `ArchiveReference`.

`MailContainer` is a bounded scheduling and recovery unit. It contains a stable
`work_id`, a source reference, optional message and byte estimates, a
`concurrency_key`, and optional `plugin_data_json`. The last field is private to
the plug-in and must contain only a plug-in-specific Pydantic model serialized
as JSON. It must never contain credentials. A work ID is stable within its
source account; the framework scopes it by plug-in kind and source ID.

`SourceReference.provenance_json` may carry a source-specific Pydantic model
containing non-secret provider provenance. The framework stores it with the
source container; access tokens and credentials are forbidden.

`MailObject` is one streamed message. `raw` is the provider's original RFC 5322
representation. `work_id` binds it to its container, while `cursor` is an
opaque source-native record locator. Optional message and byte fields report
progress without coupling the generator to the status display. The framework
stores the opaque cursor and, when it is numeric, also stores a sortable source
position. A provider may supply a message-specific `source_date_utc` only as a
fallback when neither `Date:` nor `Received:` resolves a date. The typed field
must be timezone-aware and is normalized to UTC; when used, the catalog records
`date_source='source-fallback'`. Header consensus takes precedence over this
field; the previous-message and local path-year fallbacks follow it. The
framework applies the ingest run's configured earliest plausible year to every
candidate date regardless of its source.

`ProgressEvent` is data, not output. The framework sanitizes and renders its
phase and byte or message progress in the assigned worker row. Unknown-byte
sources use completed-container counts for overall percentage. A `SkippedInput`
similarly identifies an unrecognized item; the framework prints each skipped
path and reason once.

## Source plug-in contract

A source plug-in implements:

```python
class SourcePlugin(ABC):
    capabilities: PluginCapabilities
    integrity_controls: SourceIntegrityControls

    def recognizes(self, source: SourceSpec) -> bool: ...

    def discover(
        self, source: SourceSpec
    ) -> Iterator[MailContainer | ProgressEvent | SkippedInput]: ...

    def messages(
        self, container: MailContainer, resume_cursor: str | None
    ) -> Iterator[MailObject | ProgressEvent]: ...
```

The framework requires exactly one source plug-in to recognize each command-line
source. It writes discovery output to a temporary SQLite work snapshot before
ClamAV or archive publication. Duplicate `(plugin kind, source ID, work ID)`
containers are scheduled once; conflicting definitions are fatal.

For `stable_inventory=True`, the framework calls `discover()` a second time and
compares the complete container definitions before starting workers. For
`stable_inventory=False`, it calls `discover()` exactly once and processes that
captured worklist even if the live provider changes afterward. The snapshot is
read in round-robin concurrency-key order so a long account inventory cannot
hide later accounts behind its own limit.

`PluginCapabilities.max_concurrency` is enforced by the framework for each
container's `concurrency_key`; a plug-in never owns a private thread pool. For
example, a provider can use an account ID as the key so several accounts run in
parallel while requests to one account remain bounded.

One source-plug-in instance and its integrity-control instance are shared by all
framework workers. `messages()` and integrity-control methods must therefore be
reentrant or protect mutable provider-client state. The framework rejects a
`resume` decision unless `resumable=True` and a cursor is present.

## Local source and file parsers

The production `file-folder` source recursively walks local files in sorted
directory and filename order. It probes each regular file against the frozen
file registry. No match yields a `SkippedInput`. Multiple packaged matches are
resolved by manifest priority; any overlap involving an external plug-in is a
fatal ambiguity. Known incomplete or malformed formats remain fatal rather than
being reported as harmless skips.

For every match, the source yields a `MailContainer` holding an opaque serialized
`LocalContainerData`. When a worker consumes it, the local source calls the
selected file parser and converts its records to source-neutral `MailObject`
values.

Recognition receives the filename in `FileProbe`; record generation receives
the resulting `MailContainer` rather than a second bare filename. That small
difference from a filename-only interface keeps the framework-selected source
identity, provenance, estimates, and integrity boundary attached to the work,
and lets the same scheduler handle local files and virtual provider containers.

The packaged file parsers are:

| Kind | Recognition | Behavior |
|---|---|---|
| `emlx` | `.emlx` suffix | Reads the declared RFC 5322 length; rejects partial EMLX |
| `babyl` | case-insensitive `BABYL OPTIONS:` signature | Streams Emacs RMAIL Babyl records, including extensionless files |
| `mbox` | initial `From ` separator | Streams MBOX records with numeric offsets and safe append resume |
| `message` | `.eml` suffix or a direct `cur`/`new` child in a `cur`/`new`/`tmp` Maildir, after higher-priority packaged formats | Streams one RFC 5322 message |

Packaged precedence is EMLX, Babyl, MBOX, then `message`. In particular, an
MBOX envelope signature wins when a one-message MBOX file resides under a
Maildir `cur` or `new` directory. Any overlap involving an external file
plug-in remains a fatal ambiguity.

Babyl parsing handles LF and CRLF containers, uses the original-header block
when present, falls back to visible headers when needed, and omits Babyl labels
and redundant visible-header metadata from the yielded message. It never
changes the source file.

The older `FileParser`, `SourceFile`, `SourceMessage`, and explicit registration
functions remain as a compatibility facade for the built-in physical parsers
and direct tests. Production discovery is manifest-driven and frozen before
workers start.

## Integrity controls

Integrity has three distinct layers.

### Source integrity

Every source plug-in supplies `SourceIntegrityControls`:

```python
class SourceIntegrityControls(ABC):
    control_id: str

    def plan(
        self,
        container: MailContainer,
        prior: tuple[IntegrityEvidence, ...],
    ) -> Iterator[IntegrityDecision | IntegrityEvidence | ProgressEvent]: ...

    def complete(
        self,
        container: MailContainer,
        planned: tuple[IntegrityEvidence, ...],
    ) -> Iterator[IntegrityEvidence | ProgressEvent]: ...
```

Planning emits exactly one `read`, `skip`, or `resume` decision. The framework
records the attempt before message processing and marks it complete only after
`complete()` succeeds. A failed attempt therefore cannot replace the last safe
checkpoint. Evidence is stored in `source_integrity_checks` and
`source_integrity_evidence`; cryptographic evidence must name its algorithm,
while provider version tokens, immutable identifiers, cursors, and metadata
must not claim a hash algorithm.

The framework consumes `complete()` outside the publication lock, forwarding
each `ProgressEvent` as it is yielded. This permits source hashing or provider
I/O to proceed independently across workers. After the generator finishes and
its evidence is validated, the framework acquires the publication lock only to
persist the final evidence and completed checkpoint atomically.

The local source control calculates complete and prefix SHA-256 evidence. A
matching complete digest skips an unchanged file. A grown MBOX resumes only
when the old-length prefix digest matches and the next bytes form an MBOX
message boundary. Truncated files, changed files, unsafe appends, Babyl, EMLX,
and single-message files are fully read. Completion checks the discovered file
size and nanosecond modification time before committing evidence.

Provider controls use provider semantics. Gmail history IDs, IMAP UIDVALIDITY
and UIDs, and Microsoft Graph ETags, change keys, or delta links are version or
cursor evidence—not cryptographic fixity.

API version 1 commits provider integrity evidence only after the whole bounded
container completes. It does not advertise or silently discard per-message
checkpoints. Provider containers must therefore be replayable and reasonably
bounded; interruption re-reads the incomplete container and normal
deduplication handles already committed messages.

### Message-transfer integrity

The framework computes SHA-256 over every `MailObject.raw`, stores it on the
source observation, and uses `(Message-ID, SHA-256)` for deduplication. This
binds a source record to the archived message but does not replace a container
or provider integrity control.

### Archive integrity

Source plug-ins cannot select, weaken, or disable archive integrity controls.
`MailbagArchiveIntegrityControls` wraps the current archive implementation. It
initializes BagIt declarations and the standalone verifier, publishes the
Mailbag metadata and payload/tag manifests after canonical MBOX publication,
and exposes independent verification. The canonical `h1` complete-MBOX, `h2`
recovered-message, and `h3` semantic-message controls are described in
[INTEGRITY_CONTROLS.md](INTEGRITY_CONTROLS.md).

## Directory discovery

Packaged plug-ins live below:

```text
mailarchiver/plugins/
  sources/<kind>/plugin.toml
  files/<kind>/plugin.toml
```

Additional roots are loaded only when explicitly named with repeatable
`ingest --plugin-dir DIRECTORY` options. Each such root has the same `sources/`
and `files/` layout. Mail source trees, the current directory, environment
variables, and the archive directory are never searched implicitly.

Directory membership authorizes Python code execution. Only use
`--plugin-dir` for code you trust.

A manifest contains:

```toml
api_version = 1
plugin_type = "source"       # or "file"
kind = "example"
name = "Example source"
implementation_version = "1"
priority = 100
entrypoint = "plugin:create_plugin"
```

For external plug-ins the entry module must resolve inside that plug-in's own
directory. Absolute paths, `..`, escaping symlinks, missing modules, invalid
API versions, type/directory mismatches, and duplicate `(plugin_type, kind)`
pairs are rejected. All manifests are parsed and validated before any external
module is imported, so one invalid manifest prevents all dynamic code execution
for that startup.

After validation, candidates are sorted by `(priority, kind)`. File plug-ins
are instantiated first. Source factories may accept a read-only `PluginContext`
containing the frozen file registry, which is how the local source delegates to
directory-discovered file parsers. Zero-argument factories are also supported.
The completed source and file registries are immutable.

## Built-in source status

| Kind | Status |
|---|---|
| `file-folder` | production |
| `gmail` | reserved stub; no account access |
| `imap` | reserved stub; no server access |
| `o365` | reserved stub; no Microsoft Graph access |
| `microsoft-exchange` | reserved stub; no Exchange access |
| `stdin` | reserved stub; intended for NUL-delimited records |

The reserved plug-ins recognize their explicit URI schemes and fail clearly.
They do not imply that remote or stream ingest works. A production adapter must
retrieve original raw MIME, define stable source identities and hierarchy,
implement provider-appropriate container integrity and resume evidence, and
pass provider-local integration tests.

The generic provider path itself is implemented and tested: a dynamically
loaded source can emit a virtual container and opaque cursor, use a provider
version-token control, run through the framework worker/status/ClamAV/archive
pipeline, and skip the unchanged container on a later run.

## Catalog representation

The catalog retains the historical table names `source_volumes` and
`source_files`, but they represent source origins and containers. A local row
has `path_kind='file'`; a provider row has `path_kind='provider'`.
`source_plugin`, source-scoped `work_id`, the source-native path or ID,
per-container display/hierarchy/provenance metadata, and the opaque observation
cursor preserve enough information to interpret provenance without reopening
the source. `hierarchy_path` drives mailbox-tree filtering. Opaque cursors have
a nullable numeric projection, so a provider cursor is not confused with byte
offset zero. Provider credentials are never catalog metadata.

Source integrity attempts and their ordered typed evidence are append-only per
run. `source_files.sha256`, `checked_at`, and `completed_run` remain local
display/cache fields; skip and resume decisions use only the most recent
completed typed integrity check.
