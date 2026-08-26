# BagIt and Mailbag interoperability

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

`archive.sqlite3` and `search.sqlite3` are operational tag files. The catalog
is authoritative metadata and the search database is disposable, but neither
is listed in `tagmanifest-sha256.txt`: they can change independently of a
preservation checkpoint, and SQLite may use transient journal files while
open. Their internal consistency is outside BagIt fixity validation. All
canonical messages remain recoverable and independently verifiable without
them.

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

The tag manifest never lists itself or a `data/` payload. The SQLite files and
transient publication journal are deliberately outside this list.

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

`h2` hashes the recovered original RFC 5322 bytes, excluding the MBOX `From `
separator and storage quoting. No header, body, line-ending, MIME, whitespace,
or character-set canonicalization is applied.

Python's MBOX writer cannot distinguish storage quoting from an original
literal `>From ` body line. It also adds a final line break when the source
message lacks one. The validator enumerates the bounded possible quoting and
terminal-line-break interpretations and accepts only a candidate matching
`h2`. This includes mapping a one-line-break payload back to a zero-byte message
only when its digest is SHA-256 of empty bytes.

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
