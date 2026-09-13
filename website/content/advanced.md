+++
title = "Advanced"
description = "Identity, repeatable ingest, Apple Mail cache recovery, MBOX envelope repair, failure diagnostics, comparison, and integrity details."
+++
<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->


Email Collection Toolkit preserves source evidence while making repeated acquisition safe.
These details matter when the same mail appears in provider exports, backups,
and working mail-client caches.

## Planned compiled desktop experience

The compiled desktop UI will evaluate Dioxus Desktop and Tauri with the system webview.
Ingest, search, and canonical archive preservation remain in Python. Windows
with full ingest is the next platform priority; Linux/snap delivery is deferred.
The current application remains Python/pywebview, and this decision is not a
Windows release announcement. See the
[architecture decision](https://github.com/simsong/email-collection-toolkit/blob/main/doc/DIOXUS.md)
for migration and validation requirements.

Plan comparable trial implementations in Dioxus and Tauri before choosing a
framework. Either approach retains the Python archive engine.

## Apple Mail as a temporary provider adapter

Until direct Gmail, Microsoft 365, and IMAP adapters are implemented, Email Collection Toolkit can read complete Apple Mail `.emlx` records from `~/Library/Mail`.
This works for any account Apple Mail has synchronized, including Gmail,
Exchange Online, Outlook.com, and ordinary IMAP.

Only complete `.emlx` payloads are accepted. `.partial.emlx`, detached
attachments, Mail indexes, and plist metadata are not treated as complete
messages. Set Apple Mail's **Download Attachments** option to **All**, let it
synchronize, and quit Mail before acquisition when practical. The terminal may
need Full Disk Access. A live cache is useful recovery evidence, but it cannot
prove that every server message was downloaded.

## Safe reruns and exact deduplication

Ingest is idempotent and may be rerun after Apple Mail downloads more messages
or after another backup is found. Unchanged sources are skipped. A message is
an exact duplicate only when both its normalized `Message-ID` and raw RFC 5322
SHA-256 match. A byte-identical message found in a backup, Google Takeout, and
the Apple Mail cache has one canonical copy while every source observation is
retained.

This exact rule is intentionally conservative. If a mail client adds, removes,
or refolds a header, the raw bytes differ and Email Collection Toolkit preserves that
variant instead of silently discarding evidence.

## Double-processed MBOX envelopes

Some mailbox conversions add a new `From ` delimiter and quote the old one as
`>From `. Left ahead of the real email headers, that quoted line can prevent
ordinary mail readers from recognizing the sender, subject, and MIME structure.
The importer repairs this specific pattern in the archived copy:

- When a `From ` delimiter is immediately followed by a valid `>From ` delimiter,
  keep the outer envelope and convert the quoted line to a literal `X-From:`
  header.
- If the outer sender is exactly `XXX` or `???@???`, and the inner sender is
  neither placeholder, use the inner envelope instead. Store the displaced outer
  value in the literal `X-From:` header.

For example, this source framing:

```text
From XXX Thu Apr 08 23:43:48 2004
>From sender@example.test Thu Apr  8 19:43:31 2004
From: sender@example.test
Subject: Example
```

becomes this in the archive:

```text
From sender@example.test Thu Apr  8 19:43:31 2004
X-From: XXX Thu Apr 08 23:43:48 2004
From: sender@example.test
Subject: Example
```

Only the immediate, unindented, singly quoted line with a sender and complete
ctime-style timestamp qualifies. The outer delimiter must also be one complete
`From ` line; malformed outer framing is left unnormalized for validation. An intervening header or blank line, malformed
delimiter, or additional `>` prevents this repair. Quoted `From ` lines in the
body remain unchanged. The separate older `From XXX` status wrapper is unwrapped
only with a nonempty, valid status-only header block and a valid quoted delimiter
at the start of its body. Exact empty Eudora metadata stubs remain excluded
when double-framed; original `X-From:` headers or body content prevent that
metadata exclusion.

The source mailbox is never changed. The archive's message hash covers the
normalized bytes, including the new `X-From:` header. The private catalog's source
observation also retains the original payload's SHA-256 and both original framing
lines, allowing reconstruction of the source record. All remaining headers and
body bytes are retained. Existing archives are not automatically repaired;
duplicate-skipping reimport is not a repair procedure.

These rules recognize framing patterns associated with procmail/formail,
MIMEDefang/Sendmail, and Eudora conversions. They do not identify which program
created a particular file or guarantee every historical variant. A timestamp
difference alone does not establish which envelope is correct. See the
[MBOX reading notes](https://github.com/simsong/email-collection-toolkit/blob/main/doc/MBOX_READING.md)
for compatibility evidence and limits.

## Diagnosing import failures

Select a failed run in **Ingests** to inspect its failure details. The saved run
history includes a traceback with source-code filenames and line numbers and,
when available, the original validator's traceback. Message context includes the
source identity, cursor (a byte offset for local MBOX), message SHA-256 and size,
and escaped previews of up to 4,096 message bytes and 512 envelope bytes.
Normalization context also identifies the original source-payload hash and
previews both original framing lines (up to 512 bytes each). These details help locate the exact source email without copying the
whole message into the error report. A failure between messages identifies the
source without attributing the error to the preceding email.

The same details are retained locally in the archive's `status/ingest-*.json`
run history and catalog. They may contain private email text; inspect them before
sharing a failure report. Source identity fields and cursors are limited to
1,024 characters each, with truncation identified; arbitrary source metadata is
omitted from these error details. Exception summaries are limited to 2,048
characters, individual notes to 32,768, and the complete failure report to
65,536, with truncation indicated.

## Raw and semantic message hashes

The archive records two per-message SHA-256 identities:

- **h2 raw-message** hashes the archived RFC 5322 bytes exactly, including any
  literal `X-From:` normalization described above.
- **h3 semantic-message v1** applies DKIM-relaxed normalization to an ordered
  set of stable and delivery headers, combines those headers with the complete
  body under DKIM-simple-style canonicalization, and hashes the result.

Although h3 is sometimes called the “normalized message header hash,” it is
not header-only: the complete body is included. Selected fields include
`From`, `Sender`, `Reply-To`, `To`, `Cc`, `Bcc`, `Delivered-To`, `Date`,
`Message-ID`, `Subject`, `MIME-Version`, `Content-Type`,
`Content-Transfer-Encoding`, and `Content-Disposition`. Mutable fields such as
`Status`, `X-Status`, `Received`, and `Return-Path` are excluded. The exact
byte algorithm is in the [integrity controls](https://github.com/simsong/email-collection-toolkit/blob/main/doc/INTEGRITY_CONTROLS.md).

h3 is used for reconciliation and forensic lookup. It does not authorize the
importer to merge or delete raw variants.

## Compare Apple Mail with an archive

From the source checkout, run:

```console
make compare-apple-mail
```

The command compares `~/Library/Mail` with `~/mail-archive` read-only. Alternate
locations may be supplied through `ARGS`. It reports exact raw matches,
semantic-only matches, cache-only and archive-only messages, ambiguous h3
matches, excluded partial records, and aggregate header names that Apple added,
removed, or changed. It never prints message content or header values and keeps
its path/hash index in a disposable temporary database.

On the September 6, 2026 comparison, 12,426 complete cache records were exact
raw matches and 20,046 were h3 matches with different raw bytes. Of the latter,
19,537 differed only in formatting. The aggregate report found 508 occurrences
each of Apple-only `Received`, `Return-Path`, and `X-Mailer`, and 508 of
archive-only `X-Universally-Unique-Identifier`; one Gmail pair lacked
`X-GM-THRID` and `X-Gmail-Labels` in Apple Mail. These are point-in-time
results from a live cache.

## Source controls and archive verification

Source-file fingerprints and append checkpoints make normal reruns efficient.
A changed source is read again, but exact canonical identities remain
deduplicated. Every observation retains its physical origin even when several
observations refer to one message.

The canonical archive uses standard MBOX plus BagIt and Mailbag metadata.
`h1` hashes each complete MBOX, `h2` hashes each recovered raw message, and
`h3` supports semantic reconciliation. SQLite catalogs and search indexes are
derived and rebuildable. Run `make verify ARCHIVE=/path/to/archive` after
ingest or transfer; verification is read-only.

The [user manual](https://github.com/simsong/email-collection-toolkit/blob/main/doc/USER_MANUAL.md)
and [Apple Mail cache report](https://github.com/simsong/email-collection-toolkit/blob/main/doc/APPLE_MAIL_CACHE.md)
provide the complete operational guidance.
