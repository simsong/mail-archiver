# MCT Importer API Version 1.0

Version 1.0 defines a read-only executable interface: a production importer
accepts one filename and emits mboxrd messages on stdout. The Rust test tools
`mcti-generator` and `mdti-validator` implement its stream contract. The PST
adapter and Python executable host remain planned. The validator name is
intentionally `mdti-validator`, as requested; the generator is `mcti-generator`.

## Build and run

Rust and Cargo are required to build these programs and the planned Rust PST
importer. Use the installed stable Rust toolchain, including rustfmt and Clippy.
This change was developed with Rust 1.98.1. Other compiler versions have not
been qualified locally. `Cargo.lock` pins dependencies for repeatable builds.
This adds no Rust dependency to existing Python-only local-mail importing.
Packaged PST importing will require its compiled Rust helper, not a Rust
compiler installed by the end user. No PST importer is shipped yet.

```sh
make rust-toolchain       # show compiler and Cargo versions
make rust-programs        # build both optimized executables
make mdti-validator       # build just the validator
make mcti-generator       # build just the generator
make rust-check           # formatting, Clippy with warnings fatal, Rust tests
make rust-smoke COUNT=10  # generated messages piped directly into validator
```

Executables are `target/release/mdti-validator` and
`target/release/mcti-generator` (`.exe` on Windows). `RUST_TARGET_DIR` overrides
the output directory. Each supports `--help`, `--version` and `--api-version`;
the last prints `MCT Importer API 1.0` and exits without consuming/emitting mail.
Tool versions and API versions are distinct. Run ordinary workflows through
Makefile targets. `make rust-fmt` applies formatting; `make rust-lock` explicitly
refreshes dependency resolution. Normal builds/tests use `--locked`.

For direct shell use after building:

```sh
set -o pipefail
./target/release/mcti-generator 10 | ./target/release/mdti-validator
```

Check both process statuses. A normal shell pipeline can otherwise hide a
producer failure. Use a byte-preserving pipe, such as Bash/Git Bash or Windows
`cmd.exe`; avoid shell pipelines that decode/re-encode native output as text.
The Rust tools use binary byte I/O without platform newline conversion.

## Stream and headers

Stdout contains only mboxrd records: no banner, JSON, logging or summary.
Diagnostics belong on stderr. A successful empty stream is permitted.

Each record comprises:

1. One LF- or CRLF-terminated `From sender weekday month day HH:MM:SS year`
   separator, using English ctime-style date fields.
2. RFC message bytes with one mboxrd quoting level: add `>` before every
   payload line matching `^>*From `, including existing quote levels.
3. One **additional blank line** after the LF/CRLF-terminated message payload,
   including the final record. This supplies a detectable incomplete-tail rule.

The receiver removes the extra blank line and exactly one mboxrd quoting level.
LF and CRLF input are accepted without reserialization. A literal unquoted
`From ` always starts another record; quoting omission is inherently ambiguous
if the following bytes happen to form a valid record. The validator cannot
infer how many quoting levels existed in an unknown original message.

The first three physical header lines, in this order, are generated metadata:

```text
X-Imported-URI: file:///C:/Mail/archive.pst#item=12345
X-Importer-Name: example-pst-importer
X-Importer-Version: 1.0.0
```

Header names compare case-insensitively. Values are nonempty single-line ASCII;
URI characters requiring escaping must be percent-encoded. The URI must be
absolute (file and HTTPS are examples); validation never dereferences it.
Names/versions are opaque values. Retain same-named original source headers
later in the header block. API version is discovered through `--api-version`,
not inferred from `X-Importer-Version`. The host must record the executable
actually launched separately from claims inside its output.

Top-level messages require one Date, one From, MIME-Version 1.0 and an explicit
Content-Type. Multiple From mailboxes require Sender. Message-ID is optional;
when present it must be a single angle-bracketed address-like identifier. The
generator supplies Date, From, To, unique-per-counter Message-ID, Subject,
MIME-Version, Content-Type and Content-Transfer-Encoding. It uses CRLF RFC
headers and one 7bit text/plain part, with a 1-based counter and From-like lines
that exercise reversible quoting. Counts are unsigned 64-bit integers; zero
emits nothing. Output is deterministic, including identifiers on repeated runs.

## Validator behavior

On normal invocation, `mdti-validator` immediately prints this warning on stderr:

```text
WARNING: mdti-validator discards all input; no messages will be saved.
```

It reads stdin until EOF, keeps at most one bounded message, and writes the
first detected error for each rejected record to stderr with its record number.
It resumes at the next `From ` separator; data before the first separator is
also reported. It neither creates files nor contacts sources or an archive.
After EOF stdout reports, for example:

```text
Complete messages received: 10
Rejected records: 0
Validation errors: 0
```

“Complete” counts records that pass the implemented checks, including the final
record at EOF. Rejected records do not contribute. Exit status is 0 for valid
input (including empty input), 1 for validation errors, and 2 for usage/input
I/O errors. The generator uses 2 for invalid arguments and 1 for output errors,
including a broken pipe. I/O failures are not normal EOF/completion.

The validator checks framing, required provenance, header field syntax and
folding, ASCII/control bytes, header line length (998 bytes excluding newline),
Date/From/Sender parsing, basic Message-ID syntax, MIME version/type and
transfer encoding. It checks multipart boundaries and closing delimiters,
recurses into parts and message/rfc822 bodies, and validates base64 and strict
quoted-printable decoding. 7bit/8bit bodies must obey their byte/line limits;
binary transfer encoding permits binary bytes. It never decodes text charsets
or rewrites the input.

Resource limits are 64 MiB of decoded mboxrd payload per record by default
(override with `--max-message-bytes N`), 1 MiB per physical stream line,
256 KiB per entity header block, and 32 MIME nesting levels. Oversized input is
drained with bounded storage and rejected, not partially accepted. These are
validator resource limits, not claims that larger source messages are invalid
or may be discarded from a real archive.

This is a stream/profile validator, not a complete RFC conformance oracle,
malware scanner, or proof of source completeness. Address parsing uses mailparse,
date validation uses Chrono, and Message-ID validation is deliberately basic. MIME parameter and
obsolete header grammar are not exhaustively checked. Original-source quote
depth, omitted attachments/messages, producer exit status, and a body truncated
exactly at a legal ending cannot be established from stdin alone. The future
host must independently observe process success and preserve partial-run evidence.

## Integrity and deduplication

No h4 is introduced. H2 protects all accepted RFC bytes, including generated
headers. H3 covers its existing selected headers and encoded MIME body, excluding
top-level importer annotations. Use h3 for comparison across importer passes
and retain every observation; h2 retains exact variant fixity. Changing archive
duplicate suppression is separate from these test programs. Neither computes
new hashes or writes an archive. Existing hash meanings remain unchanged.

## Validation evidence and sources

`make rust-check` tests real process pipelines, deterministic generation,
zero/large counts, malformed headers/URIs/encodings, folding and inherited
provenance fields, missing final framing, size limits, multipart closure and
recovery after invalid records. `make rust-smoke` exercises release binaries.
CI defines native Linux/macOS/Windows Rust checks; a matrix definition is not
evidence those hosted jobs have run. Current local qualification is macOS.

The framing rule follows [Library of Congress MBOXRD](https://www.loc.gov/preservation/digital/formats/fdd/fdd000385.shtml).
Header/body structure follows [RFC 2822](https://www.rfc-editor.org/rfc/rfc2822)
and MIME [RFC 2045](https://www.rfc-editor.org/rfc/rfc2045) /
[RFC 2046](https://www.rfc-editor.org/rfc/rfc2046).
[mailparse](https://docs.rs/mailparse/0.17.0/mailparse/) supplies header/address
parsing; [Chrono validates RFC dates](https://docs.rs/chrono/0.4.45/chrono/struct.DateTime.html#method.parse_from_rfc2822).
Protocol/framing, resource limits and MIME closure checks are explicit
in `rust/mct-importer/src/lib.rs`.
