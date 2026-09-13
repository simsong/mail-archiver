# MBOXRD storage and MBOX input

## Storage dialect

Use **mboxrd** for new canonical and derived MBOX records. The
[Library of Congress MBOXRD format description](https://www.loc.gov/preservation/digital/formats/fdd/fdd000385.shtml)
documents reversible From-line quoting: prepend one `>` to every payload line
matching `^>*From `, including malformed header lines. Decode by removing
exactly one `>` from `^>+From `. The separate `From ` record separator is not
quoted. Thus `From `, `>From ` and `>>From ` become `>From `, `>>From ` and
`>>>From `; their original distinctions survive. LF/CRLF, MIME encodings and
all other payload bytes are retained. A framing LF added to an unterminated
message is distinguished using its original h2, not discarded unconditionally.

[Python's mailbox documentation](https://docs.python.org/3/library/mailbox.html#mbox)
explicitly identifies `mailbox.mbox` as **mboxo**, not mboxrd. Its escaping of
bare From lines alone is ambiguous when a source already contains `>From `.
Mail Archiver now prequotes payload bytes with `mboxrd.quote()` before passing
the envelope and bytes to the standard writer. No bare payload `From ` remains
for Python to quote a second time. Never pass parsed/reserialized messages to
the canonical writer, or apply quoting twice.

Existing archives are not converted. Their records can use the older mboxo
encoding despite old `bag-info.txt` declarations saying mboxrd. A continued
archive can therefore contain both encodings. Updated bag metadata discloses
this possibility. Retrieval first tries exactly one mboxrd unquote, then the
old bounded mboxo alternatives; it returns only bytes matching the recorded h2.
The standalone verifier implements this independently. Mboxrd recovery has no
quote-depth/ambiguous-line cap; the old mboxo combinatorial fallback remains
bounded to 12 ambiguous lines. Hashes cannot repair arbitrary pre-existing
corruption or reconstruct unknown original quoting without an expected digest.

## Input dialect declaration

The local source parser accepts `.mboxrd` as an explicit dialect declaration
after content-based MBOX recognition. It removes one quoting level before
computing message hashes and bypasses historical double-framing guesses.
Validation-corpus MBOX-to-EML preparation follows the same declaration.
For `.mbox` or other content-recognized inputs, the dialect is unknown: retain
physical payload quoting except for the specific legacy transformations below.
An ordinary `>From ` line does not identify a dialect. Do not simply rename
an unknown or old mixed archive `.mboxrd`; that asserts information we do not
have. Canonical archive reads use recorded offsets and h2 verification, not
the unknown-source parser. A generic reimport of a canonical `.mbox` is not
equivalent to that verified read.

The planned [executable importer protocol](PST_DUAL_READER.md) declares stdout
to be mboxrd. Its receiver must decode once before h2, and canonical publication
then encodes once. That receiver and PST importers are not implemented yet.

## Code audit (2026-09-12)

| Path | Storage/read contract |
| --- | --- |
| `src/mailarchiver/mbox.py` | Canonical publication prequotes; search, GUI, refresh, checkpoint and export readers use h2-verified recovery |
| `src/mailarchiver/standalone_verify.py` | Independent mboxrd plus legacy recovery; physical `get_file()` bytes avoid `get_bytes()` newline translation |
| `src/mailarchiver/sources.py` | Declared mboxrd decode; unknown dialect preservation; separate explicit legacy wrapper normalization |
| `src/mailarchiver/validation.py` | Declared mboxrd-to-EML decode without MIME serialization |
| `src/mailarchiver/pdf_mail.py` | Derived PDF transcription output prequotes generated RFC bytes |
| `scripts/data_quality/analyze_archive.py` | Derived sample output prequotes h2-verified message bytes |
| `tests/generate_bagit_fixture.py`, `e2e_tests/generate_corpus.py` | Generated MBOX payloads use the same quoting rule |
| Historical input fixtures and count-only `mailbox.mbox` uses | Deliberately retained dialect examples; counts do not decode payloads |

`make test-mboxrd` exercises quote depths beyond the legacy cap, mixed old/new
records, LF/CRLF, final-newline variants, malformed bytes, declared sources,
derived exports, and the installed standalone verifier. Native Windows ingest
remains unqualified: Python's platform newline conversion, locking and directory
fsync require the Windows work described in `WINDOWS.md`; these macOS tests do
not establish byte preservation on Windows.

## Reading double-processed legacy MBOX records

When the first payload
line is exactly `>From ` followed by a sender and a ctime-style timestamp,
write it as a literal `X-From: sender timestamp` header, retaining the outer
delimiter. If the outer sender is exactly `XXX` or Eudora's `???@???` and the
inner sender is neither placeholder, use the inner envelope instead and write
the displaced outer value as `X-From:`. Other senders, including `foo@bar`,
`nobody` and `MAILER-DAEMON`, are not classified as bogus merely because they
look generic. Keep all remaining header and body bytes; never change sources.
The outer delimiter must itself be one complete literal From line with LF or
CRLF termination; embedded line breaks and malformed prefixes prevent
normalization. Only one immediate quoted line participates. Blank lines, indentation, ordinary
RFC headers, invalid delimiter syntax and additional `>` levels do not trigger
this rule. There is no scan into the body and no global removal of `>`.

For example:

```text
From XXX Thu Apr 15 04:21:10 2004
>From sender@example.test Thu Apr 15 00:20:49 2004
From: sender@example.test
Subject: Example

>From an intentional quotation
```

The normalized RFC message has the following framing and contents before
mboxrd storage quoting (its literal body `>From ` is stored as `>>From `):

```text
From sender@example.test Thu Apr 15 00:20:49 2004
X-From: XXX Thu Apr 15 04:21:10 2004
From: sender@example.test
Subject: Example

>From an intentional quotation
```

Canonical SHA-256 covers the normalized RFC message, including `X-From:`.
Observation detail records typed normalization evidence: its rule, original
source-payload SHA-256, and exact original/quoted envelope bytes encoded as
base64. The original payload is reconstructable by replacing the inserted
first X-From line with the recorded quoted envelope. The original physical
envelope is separately retained in that evidence and, for a promotion, in the
literal header. This is an explicit exception to unchanged-message-byte import;
envelope values, line endings, remaining headers and body quoting are preserved.
This rule does not establish which program performed the double processing,
whether the mailbox was emailed, or which envelope timestamp is more accurate.

Exact empty MBCP metadata stubs remain excluded after normalization: the check
uses the selected envelope and original headers/body after the quoted delimiter.
Only the generated X-From is ignored; an existing X-From or nonempty body still
prevents exclusion. Failure history previews both original framing lines, even
when validation fails before an observation is stored.

## Compatibility evidence and limits

* **Procmail/formail:** the [upstream formail manual distributed by Debian](https://manpages.debian.org/trixie/procmail/formail.1.en.html)
  documents mailbox formatting and From-line escaping, a `foo@bar` fallback
  sender, and special handling of immediately following `>From` lines.
  It does not establish `XXX` as this file's producer signature. Fixtures cover
  standard and double-framed records without depending on a placeholder sender.
* **MIMEDefang/Sendmail:** the [MIMEDefang filter manual](https://www.mimedefang.org/man_mimedefang-filter.html)
  documents an operation that prepends a From-line for an MBOX-form scanner
  input. Such a line does not by itself justify removing any message content.
  Fixtures cover ordinary added framing and the selected double-framing pattern;
  `Received` and `X-Scanned-By` fields do not change parsing rules.
* **Eudora:** the [Eudora2Unix converter's format notes](https://eudora2unix.sourceforge.net/details.html)
  document `???@???` envelope senders and tested Eudora versions. Fixtures retain
  that sender and exercise LF/CRLF double framing. An `X-Mailer: Eudora` header
  identifies a composing client, not necessarily the tool that wrote a mailbox.

These are structural compatibility tests, not executions of those historical
products or proof of support for every variant. Detached Eudora attachments,
arbitrary Content-Length framing and restoration of ambiguous historical body
quoting are outside this rule. [RFC 4155](https://www.rfc-editor.org/rfc/rfc4155.html)
describes the variation among MBOX implementations and quoting conventions.

The older, explicit status-header `From XXX` wrapper remains a distinct format:
only a nonempty, valid status-only outer header block followed by a quoted delimiter at
its body boundary can be unwrapped. Malformed headers cannot cause that parser
to skip the real message headers and reinterpret an indented body line.

`make test-envelopes` checks unchanged body/header bytes, envelope precedence, literal
headers/MIME, source idempotence, independent archive verification, and failure
provenance. Existing archives are not rewritten; changed normalization may require a
separately authorized catalog rebuild or reconciliation of prior observations.
