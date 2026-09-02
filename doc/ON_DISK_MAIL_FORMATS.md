# On-disk mail formats and import backends

**Status:** design decision and research snapshot, 2026-08-31. No PST/OST
import is implemented by this repository yet.

This is the single inventory for physical mail formats: what the project can
read now, what it plans to read, where test material can come from, and which
open-source components are candidates. It complements the normative archive
rules in [requirements.md](requirements.md), the plug-in contracts in
[PLUGINS.md](PLUGINS.md), and the implementation narrative in
[implementation.md](implementation.md).

## Decision summary

1. Make PST/OST the next import milestone. It is the most valuable near-term
   capability for the GUI and it supplies the data foundation for a useful,
   evidence-grounded AI finding-aid.
2. Use **libpff through its Python bindings, pypff**, behind the existing typed
   source/file-parser boundary. Do not make libratom's reconstructed message
   formatter the canonical import path.
3. Use `libpst` for an independent PST comparison where practical. Keep
   `java-libpst`, XstReader/XstReaderNext, and `pstfree` as additional
   comparison or investigation tools rather than adding their runtimes to the
   core Python installation.
4. Treat `hrbrmstr/freepst`'s three public files as private validation inputs.
   They are real mail, and the repository's fixture script deliberately does
   not commit them.
5. Generate the main acceptance corpus on the user's Windows PC from a
   deterministic seed mailbox. This is the only practical way to obtain a
   controlled, versioned PST/OST matrix without taking on unknown privacy or
   redistribution obligations.
6. Treat Outlook for Mac `.olm` as a separate future format. It is not a PST or
   OST implementation and should not be used as a substitute for Windows OST
   fixtures.

The adapter must preserve the original PST/OST file, hash it, and record its
parser/version and source-native identifiers. A PST/OST item normally does not
contain an original RFC 5322 byte stream. If the adapter reconstructs RFC 5322
from MAPI properties and body parts, the catalog must say so; reconstructed
bytes are never described as byte-identical source mail.

## Emailchemy

Emailchemy is useful as a personal evaluation and comparison tool, not as a
source of truth for this project.

The current store lists the Personal Individual edition at **$29.95** for one
person's email, with a Personal Household edition at $49.95. The Forensic
Technician edition is listed at $299. The vendor describes the personal
license as non-commercial, non-organizational, and non-professional, so the
Individual price is the relevant one only for personal use. See the
[current store](https://sites.fastspring.com/weirdkid/instant/emailchemy) and
[edition comparison](https://www.weirdkid.com/knowledge-base/which-edition-of-emailchemy/).

There is a usable trial/demo mode, but its output is deliberately throwaway:
the registration documentation says that demo data masks headers such as
sender and subject and that a paid rerun is required for usable output. The
[user manual](https://www.weirdkid.com/products/emailchemy/doc/Emailchemy_User_Manual.pdf)
also describes the product as trialware. Therefore:

* it is reasonable to buy or trial Emailchemy on the Windows machine to answer
  “what does Outlook do with this file?”;
* demo output must not be used as corpus ground truth or preservation output;
* Emailchemy should remain an optional external comparison tool, not a runtime
  dependency or required import path.

## Test-file sources

### Primary source: controlled Windows fixtures

Use classic Outlook for Windows on the PC, with a disposable test account and
messages generated from a deterministic seed corpus. Close Outlook before
copying a store, and work only on a copy. Keep the seed RFC 5322 messages,
folder/item-count manifest, Outlook version/build, file size, SHA-256, and
observed header signature together as private test evidence.

The planned matrix is:

| Fixture | How to obtain | What it tests |
|---|---|---|
| Unicode PST, small | Export a seeded mailbox from classic Outlook | Basic folder/message/attachment enumeration and reconstruction |
| Unicode PST, large | Add nested folders, duplicate Message-IDs with different bodies, non-mail items, and attachments | Streaming, item accounting, ordering, and mixed item classes |
| Password-marked PST | Set a PST password and export/copy it | Parser behavior around the PST password field; do not assume encryption |
| Outlook 2013-style 4-KiB compressed OST | Create a cached Exchange account using classic Outlook, then quiesce and copy the OST | The OST format specifically documented by libpff; compressed blocks and incomplete-cache reporting |
| Current cached OST | Create a second disposable cached profile and vary the download/cache state | Current profile metadata, partial bodies, drafts, deleted/recoverable items, and folder provenance |
| Corrupt/truncated derivatives | Make read-only byte copies of a valid fixture and damage only the derivatives | Failure reporting and recovery boundaries; never damage the source fixture |
| Legacy ANSI PST | Obtain an old Outlook-generated file or a licensed/public fixture | 32-bit ANSI support and the 2-GB-era format boundary |

The application must record the actual PFF/OFF header classification rather than
trusting a `.pst` or `.ost` suffix or an Outlook-version label. The libpff
[format specification](https://github.com/libyal/libpff/blob/main/documentation/Personal%20Folder%20File%20%28PFF%29%20format.asciidoc)
describes PST/OST header signatures and the 32-bit ANSI, 64-bit Unicode, and
4-KiB Unicode compressed forms. Those signatures are the basis for the fixture
manifest.

### Public validation files

The best public lead found is Bob Rudis's
[`hrbrmstr/freepst`](https://github.com/hrbrmstr/freepst) package. Its
`inst/extdata` directory contains:

* [`dist-list.pst`](https://github.com/hrbrmstr/freepst/raw/master/inst/extdata/dist-list.pst),
* [`example-2013.ost`](https://github.com/hrbrmstr/freepst/raw/master/inst/extdata/example-2013.ost),
* [`passworded.pst`](https://github.com/hrbrmstr/freepst/raw/master/inst/extdata/passworded.pst).

The package documents these as PST/OST examples and exposes folder/message
metadata. The newer [`pstfree` fixture script](https://github.com/sp00nznet/pstfree/blob/main/tests/fetch-fixtures.ps1)
fetches exactly those three files and says explicitly that they are real
mail-bearing stores, not synthetic committed test files. Use them only in a
private ignored fixture directory;
do not add them to Git, CI artifacts, or a public release. Before any
redistribution, review the upstream package's provenance and license.

This is a fixture lead, not a complete corpus: it does not provide the needed
version matrix, deterministic ground truth, or guaranteed redistributable
rights. The controlled Windows corpus remains primary.

### Synthetic and comparison sources

[`outlook-pst-rw`](https://docs.rs/crate/outlook-pst-rw/1.2.1) can generate
small, known-content PST files from Rust. It is valuable for deterministic
writer/reader tests, but it is not an OST reader and does not replace real
Outlook fixtures.

Public GitHub files with unknown provenance, random “sample PST” download sites,
and real personal mail are not acceptable repository fixtures. DigitalCorpora
was checked during this review and did not supply a PST/OST corpus.

## Outlook for Mac

Outlook for Mac does **not** use PST/OST as its native archive format. Microsoft
documents legacy Outlook for Mac export as `.olm`; Microsoft also documents
that Outlook for Mac cannot import ANSI 97–2002 PST and requires a Unicode PST
workaround. See Microsoft's [Mac archive export
documentation](https://support.microsoft.com/en-au/office/export-items-to-an-archive-file-in-outlook-for-mac-281a62bf-cc42-46b1-9ad5-6bda80ca3106)
and [ANSI-PST limitation](https://learn.microsoft.com/en-us/troubleshoot/outlook/import-and-export/cannot-import-ansi-pst-file).

Consequences:

* use classic Outlook for Windows to create PST and cached OST fixtures;
* add `.olm` as a separate source adapter later, with its own fixture plan;
* do not infer that a Mac Outlook archive exercises PST/OST behavior;
* an Outlook.com export can produce PST from Windows, or OLM from legacy Mac
  Outlook, but that does not make the two formats equivalent. Microsoft's
  [mailbox export guidance](https://support.microsoft.com/en-us/outlook/export-your-outlook-com-mailbox)
  describes both paths.

## Open-source library and tool inventory

“All” here means the serious open-source readers, wrappers, viewers, and
fixture/conversion tools found in this review—not every abandoned GitHub fork
or commercial converter.

| Project | Language/license | PST | OST | Role in this project |
|---|---|---:|---:|---|
| [`libpff`](https://github.com/libyal/libpff) + [`pypff`](https://github.com/libyal/libpff/tree/main/pypff) | C/Python; LGPL-3.0-or-later; project calls status alpha | Yes | Yes, including 4-KiB compressed OST | **Primary backend.** Broad format coverage and direct access to folders, items, properties, attachments, and recovery states. Keep behind our typed adapter and validate every supported fixture. |
| [`libratom`](https://github.com/libratom/libratom) | Python; MIT | Yes | Underlying pypff can reach PFF/OFF, but high-level behavior must be verified | Useful higher-level traversal/entity tooling and comparison layer. Its formatter reconstructs messages and is not the canonical byte-preserving path. |
| [`libpst`](https://github.com/pst-format/libpst) / `readpst` | C; GPL-2.0 | Yes | No clear OST contract in the project documentation | **Secondary PST oracle.** Independent implementation; output is primarily MBOX/EML-oriented, and GPL/runtime/licensing must be considered before bundling. |
| [`java-libpst`](https://github.com/rjohnsondev/java-libpst) | Java; LGPL and Apache 2.0 | Yes | Yes | Strong independent comparison reader. It adds a JVM and does not write or repair stores, so do not make it the Python runtime dependency. |
| [`XstReader`](https://github.com/Dijji/XstReader) | C#/.NET Framework; MS-PL | Yes | Yes | Useful Windows GUI and manual inspection oracle; not a core Python library. |
| [`XstReaderNext`](https://github.com/NeedsCoffee/XstReaderNext) | C#/.NET 10; see repository license | Yes | Yes | Maintained fork with a base parser and export CLI. Non-Windows runtime is not yet tested by its README; use as an optional comparison tool. |
| [`pstfree`](https://github.com/sp00nznet/pstfree) | Rust; MIT | Yes | Yes | Interesting Windows reader/repair project and the clearest pointer to the public fixture set. Use for comparison and recovery research, not as the core importer. |
| [`freepst`](https://github.com/hrbrmstr/freepst) | R/rJava; Apache-2.0 package, wrapping java-libpst | Yes | Yes | Useful fixture metadata and R convenience wrapper; not appropriate as a Python ingest dependency. |
| [`outlook-pst-rw`](https://docs.rs/crate/outlook-pst-rw/1.2.1) | Rust; inspect crate license before redistribution | Writes PST | No | Synthetic PST fixture generator only. |
| [`Ahright11/ost2pst`](https://github.com/Ahright11/ost2pst) | Python; inspect repository license/status before use | Converts | Reads OST | Experimental converter built around libpff. Useful for experiments, not as an importer because conversion can hide missing/unsupported items. |
| [`Niv2023/ost2pst`](https://github.com/Niv2023/ost2pst) | C# | Converts | Reads OST | Conversion utility with narrower stated coverage; not a general enumerator. |
| [`pstconv`](https://github.com/cjmach/pstconv) | Java; Apache-2.0 | Yes | Yes | Command-line conversion wrapper around java-libpst; not a source-native enumeration API. |

The first two rows are related, not competing at the same abstraction level:
libpff/pypff is the low-level source adapter; libratom is a higher-level
consumer of that stack. The choice is therefore **libpff/pypff first, libratom
optionally above it**, not “libratom versus libpff.”

## Proposed adapter boundary

The first implementation slice should enumerate before it publishes mail. A
PST/OST adapter should yield typed records containing:

* source file SHA-256, detected PFF/OFF kind and format signature;
* parser name/version and fixture/reader capability information;
* stable source-native folder and item identifiers, hierarchy path, item class,
  and deleted/recoverable/partial status;
* available subject, sender, recipients, dates, body variants, attachment
  metadata, and estimated sizes;
* reconstructed RFC 5322 bytes only after item accounting and provenance are
  available; and
* an explicit error or incomplete record for every item the parser cannot
  enumerate or reconstruct.

This maps to the existing `MailContainer`/`MailObject` plug-in contract. It
must remain read-only and must not ask Outlook, Exchange, or a server to repair
or complete a cache. The canonical archive may contain reconstructed RFC 5322
messages, but the catalog must retain the source hash, native identifiers,
parser version, and reconstruction status. The original PST/OST stays outside
the canonical MBOX payload unless the user separately chooses to preserve the
source file as a private, hashed source artifact.

`pffexport`, `readpst`, Emailchemy, and similar exporters may be used to compare
counts, bodies, attachments, and properties. They must not silently define the
canonical MIME representation. An exporter that chooses one body variant or
omits a MAPI property provides evidence about its output, not proof that the
source lacked that property.

## Existing format inventory

| Layer | Format/source | Status | Preservation rule |
|---|---|---|---|
| Canonical archive | Standard MBOX inside BagIt/Mailbag | Implemented | RFC 5322 bytes are canonical; SQLite/search are rebuildable derivatives. |
| Local mailbox | MBOX | Implemented | Stream and preserve message bytes with mboxrd-aware recovery. |
| Local mailbox | Emacs RMAIL Babyl | Implemented | Preserve recovered RFC 5322 message; Babyl labels are source metadata. |
| Local message | `.eml` | Implemented | Treat the file as one RFC 5322 source message. |
| Local mailbox | Maildir | Implemented | Treat `cur`/`new` messages as individual source messages. |
| Apple Mail cache | `.emlx` and `.mbox` package hierarchy | Implemented for complete `.emlx` | Preserve the declared RFC 5322 payload; package metadata is not a message. |
| Standalone document | Printed-email PDF | Partial/derived | Preserve the PDF separately; extracted messages are explicitly derived and unreviewed until reviewed. |
| Outlook local store | `.pst` | Planned; next milestone | Preserve source file/hash and native provenance; RFC 5322 is normally reconstructed. |
| Outlook cached store | `.ost` | Planned; next milestone | Read-only, report partial/deleted/unsupported items; do not fetch from Exchange. |
| Outlook Mac archive | `.olm` | Planned, separate | Separate parser and fixture matrix; not PST/OST. |
| Eudora | mailbox plus TOC/attachment conventions | Planned | Treat companion files as one source package; absence of TOC is not absence of mail. |
| Thunderbird | mbox plus `.msf`/profile hierarchy | Planned | MBOX is the message format; profile indexes are metadata and must not be trusted as complete. |
| Working IMAP cache | provider-specific local cache | Planned | Offline-only; report headers-only, evicted, partial, and detached content. |
| Gmail Takeout | MBOX | Baseline supported by MBOX path | It is an MBOX source, not a separate binary parser. |
| Live Gmail/IMAP/O365/Exchange | Remote provider/API | Planned/stubbed as documented | Remote reads require explicit authorization and provider-specific provenance; no source mutation. |

## Two-week development plan

### Week 1: fixture laboratory and parser spike

* Build the private fixture directory and a manifest schema; fetch the three
  `freepst` files without committing them.
* On Windows, generate a clean Unicode PST and a cached OST from deterministic
  seed messages. Record Outlook build, header signature, counts, hashes, and
  expected folder/item/attachment facts.
* Add a read-only pypff probe that reports file kind, header/version class,
  folder tree, item classes, item identifiers, and attachment counts. Do not
  publish canonical messages yet.
* Compare the probe with libratom and at least one independent tool on the
  public PST/OST files. Record disagreements instead of choosing whichever
  tool emits more output.

### Week 2: typed import slice and GUI proof

* Implement the PST/OST local adapter using the existing typed plug-in contract,
  including source hashes, native folder/item provenance, incomplete-item
  observations, and explicit reconstruction metadata.
* Publish one small generated PST and one generated/public OST into a copied
  test archive through the normal Makefile test path. Validate no source
  mutation, item accounting, duplicate Message-ID handling, attachment
  accounting, and rerun/idempotence.
* Add a GUI “import preview” that shows detected format, parser, folder/item
  counts, incomplete items, and the planned destination before publication.
* End with a go/no-go report for broader PST/OST support. If the adapter meets
  the acceptance criteria, start the AI finding-aid design on top of the now
  searchable, provenance-rich catalog rather than building AI against a
  partial source import.

The two-week exit criterion is not “a PST opens.” It is: a user can select a
copy of a PST or OST, see what will be imported, understand what is incomplete,
and obtain a rerunnable archive whose reconstructed records retain enough
provenance to be audited.

## References

* [libpff repository and supported PFF/OFF formats](https://github.com/libyal/libpff)
* [libpff PFF format specification](https://github.com/libyal/libpff/blob/main/documentation/Personal%20Folder%20File%20%28PFF%29%20format.asciidoc)
* [libratom repository](https://github.com/libratom/libratom)
* [libpst repository](https://github.com/pst-format/libpst)
* [java-libpst repository](https://github.com/rjohnsondev/java-libpst)
* [XstReaderNext repository](https://github.com/NeedsCoffee/XstReaderNext)
* [pstfree repository and fixture provenance](https://github.com/sp00nznet/pstfree)
* [Emailchemy store](https://sites.fastspring.com/weirdkid/instant/emailchemy)
* [Emailchemy edition guidance](https://www.weirdkid.com/knowledge-base/which-edition-of-emailchemy/)
* [Emailchemy registration/trial behavior](https://www.weirdkid.com/knowledge-base/how-do-i-register-emailchemy/)
* [Microsoft: Outlook for Mac archive export](https://support.microsoft.com/en-au/office/export-items-to-an-archive-file-in-outlook-for-mac-281a62bf-cc42-46b1-9ad5-6bda80ca3106)
* [Microsoft: ANSI PST limitation on Outlook for Mac](https://learn.microsoft.com/en-us/troubleshoot/outlook/import-and-export/cannot-import-ansi-pst-file)
