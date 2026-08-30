# Archive integrity controls: BagIt and Mailbag interoperability

## Scope

This document defines integrity controls for the canonical message archive. It
does not define how an input source proves that a local file, Gmail account,
IMAP mailbox, O365 account, or stream is unchanged or safely resumable. Those
source-specific controls belong to the source plug-in contract described in
[PLUGINS.md](PLUGINS.md).

The per-message raw SHA-256 is the bridge between the layers: it is recorded
with the source observation when a mail object is ingested and declared as
`h2` for the recovered canonical RFC 5322 message. Source-container evidence
and archive checkpoint fixity remain distinct even when both happen to use
SHA-256.

## Status

The canonical mailarchiver directory is a native
[BagIt 1.0](https://www.rfc-editor.org/rfc/rfc8493.html) bag conforming to the
[Mailbag 1.0](https://archives.albany.edu/mailbag/spec/) profile. The archive is
appendable working storage, so its manifests describe the last published
checkpoint rather than promising that the directory is immutable.

This program is under development. Only the layout and integrity formats in
this document are supported; there is no legacy root-level MBOX layout or
sidecar migration mode.

BagIt and mailarchiver provide complementary integrity layers:

* BagIt SHA-256 manifests let any conforming implementation verify complete
  payload and tag files without understanding email.
* Mailbag metadata lets email-preservation tools enumerate messages and relate
  them to their MBOX containers.
* Mailarchiver integrity tags verify each recovered RFC 5322 message and its
  versioned semantic digest, including MBOX quoting recovery that BagIt does
  not attempt.

The installed `verify_mail_archive.py` implements all three layers using only
the Python standard library and without consulting SQLite.

## Canonical message transformation ledger

This section is the authoritative inventory of how each currently supported
source container becomes `MailObject.raw`, what is deliberately omitted, and
how the canonical MBOX representation remains reversible. The `h2` digest is
always SHA-256 of `MailObject.raw`; metadata parsing, date resolution, Sent
classification, indexing, and reporting never change those bytes.

### MBOX, including Google Takeout and envelope-prefixed Maildir files

* **Extraction:** recognize the `From ` record framing by content and use
  `mailbox.mbox.get_bytes(..., from_=False)`. The source record separator is
  container framing and is not normally part of `MailObject.raw`. The bytes
  returned by the standard-library MBOX reader, including its stored `>From `
  representation, become the message bytes.
* **Container-only records:** an exact empty Eudora MBCP status record is
  observed as `source-metadata-excluded` and is not a canonical message. A
  narrowly recognized `From XXX` status wrapper is removed, one quoting level
  is removed from its nested MBOX envelope, and the nested RFC 5322 bytes become
  `MailObject.raw`; the outer source offset remains provenance.
* **Problems and adopted solutions:** mboxrd cannot distinguish storage-added
  quoting from an original literal `>From ` line, so recovery enumerates the
  bounded interpretations and uses `h2` to select one. Unescaped body lines
  that resemble record separators can make a legacy dialect structurally
  ambiguous; unsupported dialects fail rather than silently inventing bytes,
  pending evidence-based dialect adapters.

### Emacs RMAIL Babyl

* **Extraction:** recognize a case-insensitive `BABYL OPTIONS:` signature.
  Babyl options, labels, record markers, and the redundant visible-header copy
  are container metadata. Use the original-header block, or the visible headers
  only when the original block is empty, followed by one restored blank-line
  separator using the header block's line ending and the record body. Remove
  the one line ending that belongs to the following Babyl record marker.
* **Problems and adopted solutions:** Babyl does not store the header/body
  separator independently, so the adapter restores exactly one separator with
  the selected headers' LF or CRLF convention. Some original-header blocks
  begin with a Unix `From ` line. Python's MBOX writer promotes that line to the
  canonical record separator; recovery therefore tries both payload-only and
  separator-plus-payload candidates and accepts only the `h2` match. A valid
  `0x1f` terminator before any record is an empty mailbox; EOF without a record
  or terminator is a truncation error.

### Apple EMLX

* **Extraction:** parse the leading decimal byte count and copy exactly that
  many following bytes into `MailObject.raw`. The decimal prefix and trailing
  Apple plist metadata remain source-container evidence and are not canonical
  message bytes.
* **Problems and adopted solutions:** `.partial.emlx` can omit detached
  attachment bytes, so it is rejected instead of being represented as a
  complete message. Complete EMLX files retain physical-file hashes over the
  prefix, message, and trailing metadata while `h2` covers only the declared
  RFC 5322 byte region.

### EML and structurally recognized Maildir messages

* **Extraction:** the complete physical file is `MailObject.raw`; no header,
  status flag, filename suffix, or line ending is removed. Maildir `cur`/`new`
  placement and filename flags are provenance only.
* **Problems and adopted solutions:** a Maildir child can itself contain MBOX
  framing. Packaged parser priority selects EMLX, Babyl, MBOX, then the
  single-message reader, so content-defined MBOX parsing wins. A complete EML
  beginning with `From ` uses the same separator-plus-payload `h2` recovery as
  any other source-supplied leading envelope line.

### Typed virtual providers

* **Extraction:** the strict `MailObject.raw` bytes supplied by the source
  plug-in are canonical without further source-container conversion. Opaque
  cursors, provider IDs, labels, folder paths, and source dates are provenance
  or metadata, not message bytes.
* **Problems and adopted solutions:** text values cannot be coerced to bytes,
  and a proprietary reconstruction must identify its tool and version rather
  than claim byte identity. The current Gmail, IMAP, O365, Exchange, and
  standard-input manifests are unavailable stubs and therefore perform no
  canonical transformation.

### Standalone PDFs containing printed email

* **Extraction:** the PDF is the source artifact. The focused extractor checks
  PDF magic, hashes the complete file, and obtains page-addressable text through
  a versioned extraction policy without writing the PDF. A conservative
  segmentation policy interprets qualifying pages as derived messages and
  writes them to standard MBOX outside `data/mbox/`.
* **Problems and adopted solutions:** OCR text can omit headers, contain typos,
  or include an incorrect embedded text layer. Derived records therefore use
  synthetic identity plus explicit PDF hash, page, policy, review, and
  handwriting headers. Observed Message-ID text is provenance rather than the
  synthetic record identity. Non-message pages remain accounted for, human
  correction never changes the PDF, and derived bytes are never described by
  the canonical-message `h2` contract.

### Common disposition and canonical MBOX framing

`X-Apple-Auto-Saved` and recognized source metadata are observed but produce no
canonical record. A ClamAV-positive message keeps identical `MailObject.raw`
bytes and is routed to a numbered `INFECTED` MBOX. Deduplication changes only
whether another canonical copy is written.

For every retained message, `mailbox.mbox.add(raw_bytes)` performs the common
storage transformation: it writes or adopts an outer `From ` separator,
mboxrd-quotes payload lines beginning `From `, and supplies a final LF when the
source lacks one. If `raw_bytes` begins with `From `, the writer adopts that
first source line as the separator instead of generating one. The catalogued
location covers the complete stored record.

Recovery reverses the storage representation by streaming candidates in this
order:

1. interpret the record as payload-only, then as stored separator plus payload;
2. within each interpretation, try the fully unquoted, fully stored, and then
   bounded partial `>From ` combinations; and
3. for each combination, try the complete bytes, one terminal LF removed, and
   one terminal CRLF removed when applicable.

Only the candidate matching the catalogued `h2` is accepted. Candidates are
not canonicalized, retained, or selected heuristically. No match is an
integrity failure.

## Native directory layout

```text
archive/
├── bagit.txt
├── bag-info.txt
├── mailbag.csv                 # or numbered mailbag-N.csv files
├── manifest-sha256.txt
├── tagmanifest-sha256.txt
├── verify_mail_archive.py
├── integrity/
│   ├── 2024-Archive1.mbox.integrity
│   ├── 2024-Sent1.mbox.integrity
│   └── INFECTED1.mbox.integrity
├── data/
│   └── mbox/
│       ├── 2024-Archive1.mbox
│       ├── 2024-Sent1.mbox
│       └── INFECTED1.mbox
├── status/
│   ├── ingest-20260829T120000.000000Z-run-41-pid-1234-abcd1234.json
│   └── ingest-20260829T140000.000000Z-run-42-pid-5678-efab5678.json
├── archive.sqlite3
└── search.sqlite3
```

`data/mbox/` is the BagIt payload and the Mailbag MBOX representation. It
contains the canonical byte-preserving mboxrd files and no mailarchiver
integrity metadata. Additional Mailbag representations are not part of the
current native format.

`integrity/` is a BagIt tag directory. Its files describe the payload but are
not themselves canonical email. This placement lets ordinary BagIt tools hash
the declarations through the tag manifest without presenting them to Mailbag
tools as another email representation.

`archive.sqlite3`, `search.sqlite3`, and the per-run JSON files under `status/`
are operational tag files. The catalog
is authoritative metadata and the search database is disposable, but neither
database nor the status files are listed in `tagmanifest-sha256.txt`: they can
change independently of a preservation checkpoint, and SQLite may use
transient journal files while open. Each ingest atomically replaces only its
own status file and leaves it as historical run evidence when finished. Their
internal consistency is outside BagIt fixity validation. All
canonical messages remain recoverable and independently verifiable without
them.

### Planned standalone printed-email PDF extension

Standalone PDFs containing scans of printed email require a separate payload
representation from preserved electronic mail. The planned layout is:

```text
archive/
├── bagit.txt
├── bag-info.txt
├── mailbag.csv
├── manifest-sha256.txt
├── tagmanifest-sha256.txt
├── verify_mail_archive.py
├── integrity/
│   ├── 1989-Archive1.mbox.integrity
│   ├── 1989-Sent1.mbox.integrity
│   ├── 1989-Archive-PDF1.mbox.integrity
│   └── 1989-Sent-PDF1.mbox.integrity
├── data/
│   ├── mbox/                         # preserved electronic email
│   │   ├── 1989-Archive1.mbox
│   │   └── 1989-Sent1.mbox
│   ├── pdf/                          # preserved source documents
│   │   └── <pdf-sha256>.pdf
│   └── pdf-mbox/                     # reproducible interpretations
│       ├── 1989-Archive-PDF1.mbox
│       ├── 1989-Sent-PDF1.mbox
│       └── Undated-Archive-PDF1.mbox
├── status/
├── archive.sqlite3
└── search.sqlite3
```

`data/pdf/` preserves the only surviving source artifact byte-for-byte.
`data/pdf-mbox/` contains standard MBOX records reconstructed from printed
messages for validation, interchange, viewing, and search. Those records are
explicitly derived: they do not enter `data/mbox/`, canonical electronic-message
counts, or ordinary message deduplication. Exact matches remain linked to their
PDF and page provenance even when the derived result is suppressed from the
default search listing. This extension is tracked by
[GitHub issue #18](https://github.com/simsong/mail-archiver/issues/18) and is
not part of the implemented native layout above.

## BagIt declaration and manifests

`bagit.txt` is exactly:

```text
BagIt-Version: 1.0
Tag-File-Character-Encoding: UTF-8
```

It is UTF-8 without a byte-order mark and uses LF line endings.

`manifest-sha256.txt` lists every regular payload file exactly once. Entries
use lowercase SHA-256, two spaces, a path relative to the bag root, and LF:

```text
64_LOWERCASE_HEX  data/mbox/2024-Archive1.mbox
```

The slash is the path separator. Percent, CR, and LF in pathnames use the
BagIt `%25`, `%0D`, and `%0A` encodings. Mailarchiver-generated payload names
do not contain those characters, but the validator implements the BagIt rule.
Absolute paths, `..`, symlinks, missing files, duplicate entries, unlisted
payload files, and non-payload paths are errors.

`tagmanifest-sha256.txt` is generated last. It uses the same grammar and
hashes:

* `bagit.txt`;
* `bag-info.txt`;
* `manifest-sha256.txt`;
* every current `mailbag.csv` or `mailbag-N.csv` tag;
* every `integrity/*.mbox.integrity` tag; and
* `verify_mail_archive.py` when it is installed.

The tag manifest never lists itself or a `data/` payload. The SQLite files,
`status/` history, and transient publication journal are deliberately outside
this list.

SHA-256 is the only supported BagIt checksum algorithm. BagIt permits adding a
second manifest algorithm later, but mailarchiver must implement and document
that algorithm before using it. The validator fails on an additional manifest
rather than reporting success after silently skipping its hashes.

## Mailbag metadata

### `bag-info.txt`

Every checkpoint writes the required Mailbag fields:

```text
Bag-Type: Mailbag
Mailbag-Source: mbox
Mailbag-Specification-Version: 1.0
Original-Included: False
Bagging-Timestamp: RFC3339_TIMESTAMP
Bagging-Date: YYYY-MM-DD
External-Identifier: STABLE_IDENTIFIER
Mailbag-Agent: mailarchiver
Mailbag-Agent-Version: VERSION
Payload-Oxum: BYTE_COUNT.FILE_COUNT
MBOX-Format-Details: mboxrd
MBOX-Agent: Python mailbox
Mailarchiver-Message-Newline-Policy: preserve-source; add-final-LF-for-MBOX-framing
```

The external identifier is created once and retained across checkpoints.
`Bagging-Timestamp`, `Bagging-Date`, and `Payload-Oxum` describe the current
checkpoint. `Original-Included` is `False` because the payload is a normalized,
deduplicated archive assembled from heterogeneous sources; it is not a claim
that every source container was copied unchanged. The newline-policy field
documents that MBOX framing adds a final LF when the source message has none;
it does not add or modify an RFC 5322 header. The original RFC 5322 bytes
available from each source are nevertheless recoverable from the canonical
MBOX representation and checked per message.

### `mailbag.csv`

The CSV is UTF-8 without a byte-order mark and uses RFC 4180 CRLF records. It
has the seven required Mailbag columns in their required order:

```text
Error,Mailbag-Message-ID,Message-ID,Original-File,Message-Path,Derivatives-Path,Attachments
```

There is one row per canonical message. `Original-File` is the MBOX basename,
relative to `data/mbox/`. `Message-Path` and `Derivatives-Path` are empty
because the current normalized MBOX representation does not claim to preserve
one source account's folder arrangement and has no derivative representation.
`Attachments` is the number of MIME parts treated as attachments. An absent
Message-ID is an empty CSV field.

The case-insensitively unique Mailbag Message ID is:

```text
m- + first_32_hex(SHA256(UTF8(normalized-message-id) || NUL || ASCII(raw-sha256)))
```

The composite input distinguishes identical RFC 5322 bytes retained under
different Message-IDs, while the 34-character output remains within Mailbag's
recommended 36-character limit. Generation fails rather than publishing a
case-folded collision.

At 100,000 messages or fewer the tag is `mailbag.csv`. Larger archives use
`mailbag-N.csv`, with at most 100,000 rows per file, contiguous numbering, and
enough zero padding for the highest part number. Only the first split file has
the header row, as required by Mailbag 1.0.

## Mailarchiver per-message integrity tags

Each `data/mbox/NAME.mbox` has exactly one corresponding tag:

```text
integrity/NAME.mbox.integrity
```

The tag uses a compact hybrid format: deterministic JSON Lines declarations
followed by a dense TSV message table. It remains an extension to Mailbag, not
a replacement for the BagIt payload manifest.

The UTF-8 file contains, in order:

1. one `integrity-manifest` JSON record;
2. consecutive `hash-standard` JSON records assigning local codes `h1`, `h2`,
   and `h3`;
3. one `mbox` JSON record;
4. one `message-table` JSON record;
5. the literal TSV header `ordinal<TAB>message-id-json<TAB>hashes...`; and
6. one TSV row per MBOX message in byte order.

Every digest is tagged `CODE:LOWERCASE_HEX`. A typical control section is:

```json
{"format_id":"tag:simson.net,2026:mailarchiver/integrity","format_version":1,"manifest_id":"h1:...","type":"integrity-manifest"}
{"canonicalization":{"method":"none"},"code":"h1","digest_algorithm":"sha256","hash_standard":"mbox","hash_version":1,"id":"tag:simson.net,2026:mailarchiver/hash/mbox/v1/sha256","input":"complete-mbox-file","scope":"mbox","type":"hash-standard"}
{"canonicalization":{"method":"none"},"code":"h2","digest_algorithm":"sha256","hash_standard":"raw","hash_version":1,"id":"tag:simson.net,2026:mailarchiver/hash/raw/v1/sha256","input":"recovered-rfc5322-message","scope":"message","type":"hash-standard"}
{"bytes":1234,"hashes":["h1:..."],"messages":2,"name":"2024-Archive1.mbox","type":"mbox"}
{"columns":["ordinal","message-id-json","hashes..."],"encoding":"tsv","type":"message-table"}
```

`h3` is also declared before the MBOX record and contains the complete semantic
version 1 canonicalization object. JSON is compact, key-sorted, LF-terminated,
and contains no creation time or host path. Regeneration from an unchanged
MBOX produces identical integrity-tag bytes.

TSV rows are one-based and contiguous:

```text
1<TAB>"first@example.test"<TAB>h2:...<TAB>h3:...
2<TAB>null<TAB>h2:...<TAB>h3:...
```

`message-id-json` is a JSON string scalar or `null`; it is diagnostic and is
never substituted for bytes when verifying a digest.

### `h1`: complete MBOX SHA-256

`h1` hashes every byte of the MBOX, including separator lines, quoting, record
terminators, and line endings. The digest equals the entry for that MBOX in
`manifest-sha256.txt`. This intentional duplication binds the richer
mailarchiver declaration to standard BagIt fixity and lets validators detect a
disagreement between the layers.

### `h2`: recovered raw-message SHA-256

`h2` hashes the source adapter's `MailObject.raw`, normally excluding a
generated MBOX `From ` separator and storage quoting. When the source bytes
themselves began with `From ` and the writer adopted that line as the record
separator, that source line remains part of `h2`. No header, body, line-ending,
MIME, whitespace, or character-set canonicalization is applied.

Python's MBOX writer cannot distinguish storage quoting from an original
literal `>From ` body line. It also adds a final line break when the source
message lacks one. The validator enumerates the bounded possible quoting and
terminal-line-break interpretations and accepts only a candidate matching
`h2`. This includes mapping a one-line-break payload back to a zero-byte message
only when its digest is SHA-256 of empty bytes.

Verification follows this explicit order:

1. try payload-only, then stored separator plus payload;
2. for each, enumerate the bounded mboxrd quoting interpretations;
3. hash each complete candidate, then one terminal LF removed and one terminal
   CRLF removed when applicable; and
4. fail verification if none matches `h2`.

This is not a rule that the final byte is generally ignored. If the source
message contained its final line break, the complete stored candidate matches
and that line break is part of `h2`. The complete-MBOX `h1` always includes the
stored line break regardless of which per-message candidate matches `h2`.

### `h3`: semantic-message version 1 SHA-256

`h3` is a conservative, delivery-aware identity derived from RFC 6376 DKIM
canonicalization rules. It does not use or verify a message's DKIM signature.
Its input begins with a domain separator, then DKIM-relaxed selected headers,
one CRLF, and the complete DKIM-simple encoded MIME body.

Headers are selected in this order, with repeated fields processed from the
physical bottom upward:

```text
From, Sender, Reply-To, To, Cc, Bcc, Delivered-To, Date, Message-ID,
Subject, MIME-Version, Content-Type, Content-Transfer-Encoding,
Content-Disposition
```

`Delivered-To` intentionally distinguishes deliveries. Mutable local and
transport annotations such as `Status`, `X-Status`, `Received`, `Return-Path`,
`Authentication-Results`, and `DKIM-Signature` are excluded. The entire MIME
body, including attachment encodings and nested MIME headers, is included.
`h2` remains authoritative for exact bytes because DKIM-simple body
canonicalization normalizes surplus terminal empty lines.

The declaration may add another digest algorithm through a new `hN` code and
`same_input_as`, but existing codes and meanings never change. Format version,
hash-standard version, and digest algorithm are independent.

## Checkpoint publication

During an ingest, the bag may temporarily fail validation because an MBOX has
been appended while its manifests still describe the preceding checkpoint.
No command may describe that state as valid.

A successful completion, controlled interruption, or recovery publishes in
this order:

1. flush and synchronize every changed MBOX;
2. atomically replace each complete `.mbox.integrity` tag after rereading and
   checking every catalogued message;
3. atomically replace the Mailbag CSV tag or split tags;
4. atomically replace `manifest-sha256.txt`, reusing the already computed MBOX
   `h1` values;
5. atomically replace `bag-info.txt`; and
6. compute and atomically replace `tagmanifest-sha256.txt` last.

Publishing the tag manifest last makes it the checkpoint boundary. A crash
before that step leaves mismatched fixity rather than a false valid result.
Recovery resolves the pending MBOX/catalog journal first and then regenerates
the complete checkpoint.

## Validation contract

The standalone validator takes the bag directory as its only optional
argument. With no argument, an installed copy validates its containing
directory:

```console
python3 verify_mail_archive.py /path/to/archive
```

It is strictly read-only. It validates:

* the BagIt 1.0 declaration and safe manifest paths;
* completeness and SHA-256 of every payload-manifest entry;
* completeness and SHA-256 of every required tag-manifest entry;
* required Mailbag metadata, timestamps, payload oxum, CSV grammar, row count,
  MBOX references, attachment-count syntax, and case-insensitive identifier
  uniqueness;
* the one-to-one relationship among MBOX files and integrity tags;
* deterministic integrity declarations, MBOX byte length/count, `h1`, every
  recovered-message `h2`, and every semantic `h3`; and
* orphan, missing, malformed, unsupported, duplicate, out-of-order, or
  mismatched declarations.

Unknown required standards or algorithms cause a nonzero result. A validator
must never report unqualified success after checking only the BagIt layer or
only some declared message hashes.

The checked-in `tests/data/three-message-mailbag/` fixture contains three
messages, including a MIME attachment. It is a complete database-independent
example. `make fixture-bagit` regenerates it, and `make test-bagit` verifies the
fixture plus whole-payload, tag-file, raw-message, and semantic corruption
cases.

## Security and authenticity

These hashes detect accidental corruption and inconsistent publication. They
do not authenticate an archive against an attacker who can replace both data
and manifests. Authenticity requires a separately rooted signature or digest.

The validator rejects absolute paths, traversal, unsafe manifest escapes, and
symlinks rather than following manifest entries outside the bag. It never
opens network resources or executes payload content.
