<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Apple Mail cache acquisition

## Conclusion

Email Collection Toolkit can import complete `.emlx` messages found below an Apple Mail
store, but a live `~/Library/Mail` directory cannot be assumed to contain a
complete copy of every server message or attachment. It is suitable as a
best-effort offline source and may be the easiest route for mail already fully
downloaded by Apple Mail. Until direct Gmail, Microsoft 365, and IMAP adapters
exist, it can serve as a read-only bridge for any of those accounts after the
account has been synchronized with Apple Mail. It is not, by itself, proof of
a complete provider acquisition.

For a deliberately complete export, Apple's supported **Mailbox > Export
Mailbox** command produces MBOX packages. That is preferable to relying on
undocumented live-cache layout when Apple Mail has fully synchronized the
mailboxes of interest.

References:

- [Import or export Apple Mail mailboxes](https://support.apple.com/en-ie/guide/mail/mlhlp1030/mac)
- [Apple Mail attachment-download settings](https://support.apple.com/en-gb/guide/mail/cpmlprefacctinfo/mac)

## This computer

Full Disk Access permitted a read-only structural inspection on September 6,
2026. The Mail store was live—Mail and its Spotlight extension were running,
and the Envelope Index had an active WAL—so these are point-in-time counts, not
a transactionally consistent snapshot.

| Measure | Observed result |
| --- | ---: |
| Mail-store layout | V10; 15 account directories; 169 `.mbox` packages |
| Store disk usage | 12,464,196 KiB (about 11.9 GiB) |
| Complete `.emlx` | 102,192 |
| `.partial.emlx` | 99,663 |
| `.emlxpart` | 1 |
| Other files below attachment directories | 23,526 |
| Envelope Index `messages` rows | 196,950 |
| Envelope Index `server_messages` rows | 189,084 |

All 201,855 complete and partial records had numeric length prefixes, and no
file was physically shorter than its declared payload. This validates the
physical framing of complete `.emlx` records; it does not make a record that
Mail names `.partial.emlx` complete.

Google-style `[Gmail]` paths contained 191,816 records: 93,531 complete and
98,285 partial. Paths named `All Mail.mbox` contained 189,180 records: 91,220
complete and 97,960 partial. Comparing the opaque account-directory and numeric
record identifiers found no partial record with a complete cached counterpart
elsewhere. The local cache would therefore omit just over half of the observed
Google-style records if Email Collection Toolkit accepted only byte-complete input, as it
must.

During that initial audit, no message header, body, subject, address, or
attachment content was read. It read only path/type/size metadata, EMLX numeric
first lines, aggregate SQLite counts in read-only/query-only mode, and process
state. It did not copy, change, or ingest any source data.

The practical conclusion is that this store is useful for best-effort recovery
of 102,192 complete records, but direct whole-cache import cannot produce a
complete provider archive. Prefer Google Takeout or a provider export. If the
cache must be used, quit Mail, configure **Download Attachments: All**, allow
synchronization to finish, rerun the preflight, and ingest only with an
explicitly chosen archive target.

## Current importer behavior

The local-source importer already:

- discovers complete `.emlx` files recursively;
- uses each file's leading byte count to isolate the RFC 5322 message;
- excludes Apple trailing plist metadata from the canonical message bytes;
- reports and skips `.partial.emlx` during directory import, and rejects direct
  selection, because detached payloads cannot be reconstructed byte-for-byte;
- ignores Mail databases, plist files, attachment directories, and
  `.emlxpart` fragments as independent messages; and
- derives logical mailbox names from the containing `.mbox` package hierarchy
  instead of exposing internal UUID and numeric-bucket paths.

These rules prevent known partial messages from being silently treated as
complete. They cannot prove that Apple Mail downloaded every server message.

## Compare the cache with an existing archive

The read-only comparison command answers three different questions:

```console
make compare-apple-mail
```

It defaults to `~/Library/Mail` and `~/mail-archive`. Pass alternate paths with
`ARGS='--apple-mail /path/to/Mail --archive /path/to/archive'`. The command
never writes either source. It builds a temporary SQLite index containing only
relative cache paths and hashes, excludes `.partial.emlx`, and reports header
names and aggregate counts without printing header values or message content.

The comparison uses the archive's **h3 semantic-message v1 SHA-256**. This is
not a header-only hash: it applies DKIM-relaxed normalization to a selected set
of stable and delivery headers, combines them with the complete body under
DKIM-simple-style canonicalization, and hashes the result. It ignores mutable
or transport-specific fields such as `Status`, `X-Status`, `Received`, and
`Return-Path`. See [INTEGRITY_CONTROLS.md](INTEGRITY_CONTROLS.md) for the exact
ordered field list and byte algorithm.

The September 6, 2026 comparison on this computer found:

| Classification | Count |
| --- | ---: |
| Complete Apple Mail records compared | 102,193 |
| Exact raw-byte matches | 12,426 |
| h3 matches with different raw bytes | 20,046 |
| Complete cache records with no archive h3 match | 69,721 |
| Canonical archive messages represented by a cache h3 | 43,066 |
| Canonical archive messages with no cache h3 | 1,157,725 |

Of the 20,046 semantic-only pairs, 19,537 differed only in header formatting.
The provider-stratified run found five populated categories: Gmail, Microsoft
Exchange/EWS, other IMAP, POP, and local mail; no account was unknown. Its
complete EMLX counts were 93,531, 7,273, 1,084, 101, and 204 respectively.
The aggregate report found 508 occurrences each of Apple-only `Received`,
`Return-Path`, and `X-Mailer`, and 508 occurrences of archive-only
`X-Universally-Unique-Identifier`. One pair lacked `X-GM-THRID` and
`X-Gmail-Labels` in Apple Mail. No selected header had a changed normalized
value. Some h3 values identify more than one canonical archive record, so the
tool reports ambiguous matches and chooses the candidate with the smallest
header delta only for aggregate header analysis. The Envelope Index WAL
changed during the scan, and one new complete Gmail record appeared between
the initial and provider-stratified runs, so these remain point-in-time results.

## Rerunning ingest and duplicate identity

Ingest is idempotent and is intended to be rerun as Apple Mail downloads more
complete messages. An unchanged source is skipped; a changed source is scanned
again, and a message with the same normalized `Message-ID` and raw RFC 5322
SHA-256 is not stored twice. Thus a byte-identical message found in both a
backup and the Apple Mail cache has one canonical copy with two retained source
observations.

The h3 comparison does **not** change that admission rule. If Apple Mail adds,
removes, refolds, or otherwise rewrites headers, its raw SHA-256 differs and the
current importer preserves the variant as a separate canonical record. The h3
hash records the semantic relationship for reconciliation; it is deliberately
not used to discard source variants.

## Required read-only preflight

Before treating a Mail cache as a substantive source, a preflight should:

1. Record the macOS Mail-store version and source-directory metadata.
2. Count complete `.emlx`, `.partial.emlx`, `.emlxpart`, and external attachment
   files without displaying subjects, correspondents, or message contents.
3. Validate every `.emlx` length prefix and report truncation.
4. Record mailbox-package structure and aggregate counts from a consistent,
   read-only view of the Envelope Index where feasible.
5. Warn when Apple Mail's **Download Attachments** setting is not **All**.
6. Require Apple Mail to be quiescent during canonical ingest or detect and
   stop on source changes.
7. Compare cache counts against a provider export or provider API when a claim
   of completeness matters.

No real canonical archive should be populated from this Mail store until the
target archive is identified and the ingest is explicitly authorized.

## Current acquisition boundaries

No release Desktop OAuth client is bundled yet. The shared-client end-user
flow remains deferred until a maintainer supplies and validates that public
configuration in release artifacts. Current authorization requires a developer
client override. Installing that override validates and writes the same bytes.
Known consumer domains need no DNS lookup; transient DNS and token-refresh
transport failures are disclosed as errors rather than negative detection or
fresh consent. Credentials are stored only after the profile matches.

Directory import reports and skips `.partial.emlx` records while retaining
complete supported records. Direct selection of a partial record is rejected.
This is not a complete mailbox acquisition: detached attachment bytes are not
reconstructed. Do not modify the source cache; export mail through Apple Mail
when a complete MBOX source is required.
