# Email Servers: IMAP, Gmail, and Microsoft 365

## Decision

We will create and own the archive's provider adapters. Existing open-source
tools remain useful as migration aids, disposable acquisition front ends, and
comparative implementations, but none is adopted as the canonical ingest
engine.

The archive must preserve the original RFC 5322 bytes, retain source and
provider provenance, support safe reruns and interruption recovery, and never
mutate a source mailbox. Those requirements are stronger than merely copying
messages between two mail servers. The canonical output remains the
byte-preserving MBOX/Mailbag format defined in
[`requirements.md`](requirements.md) and [`implementation.md`](implementation.md).

The implementation should share provider-neutral acquisition and publication
contracts, while keeping separate provider adapters and checkpoints:

* Generic IMAP: enumerate folders and UIDs, use read-only fetches, preserve
  UIDVALIDITY and folder observations, and checkpoint only after durable
  canonical publication.
* Gmail: prefer the Gmail API for OAuth, raw-message retrieval, label coverage,
  and Gmail-specific history/cursor semantics. IMAP with XOAUTH2 is a useful
  fallback or compatibility path.
* Microsoft 365: prefer Microsoft Graph for OAuth, mailbox enumeration, and
  MIME retrieval. Exchange Online IMAP with XOAUTH2 is a compatibility path,
  not a substitute for Graph-specific metadata.

This is an implementation decision, not a claim that all three adapters are
already available. The current source registry contains reserved Gmail, IMAP,
and O365 stubs; live provider ingest remains future work.

## What the search found

### `imapcopy`

The original `imapcopy` is an old IMAP backup/copy/migration program. The
Debian source package is version 1.04, and the source identifies its own last
functional version as July 2009. It is therefore useful historical evidence
for basic IMAP copying, but it is not a suitable foundation for current Gmail
or Microsoft 365 authentication and provider behavior.

* [Debian package information](https://packages.debian.org/source/stable/imapcopy)
* [Debian source and version history](https://sources.debian.org/src/imapcopy/1.04-2/imapcopymain.pas)

### `imapsync`

[imapsync](https://github.com/imapsync/imapsync) is the strongest general
IMAP-to-IMAP migration candidate. It recursively transfers folders and
messages, preserves IMAP flags, avoids copying messages already present on the
destination, and has Gmail- and Office365-specific modes. It also documents
Gmail label synchronization and OAuth2 support.

Its model is still source IMAP to destination IMAP. It does not directly
publish this project's canonical MBOX/Mailbag records, and its migration
identity rules are not the same as this project's `(Message-ID, raw
SHA-256)` identity. It can be valuable for mailbox-to-mailbox migration or for
testing against a disposable IMAP server, but should not write the canonical
archive directly.

The project describes itself as free and open, but not always gratis; its
NOLIMIT Public License and distribution model should be reviewed before
embedding or bundling it.

### `mbsync` / `isync`

[mbsync/isync](https://isync.sourceforge.io/) is a mature GPLv2 IMAP and
Maildir synchronizer. It supports TLS, STARTTLS, SASL, UID-based
synchronization, mailbox collections, and disconnected operation. It is a
reasonable way to create a local Maildir staging copy from a generic IMAP
server.

The staging copy would still need to pass through our importer. We would need
tests for raw-byte preservation, folder and flag observations, duplicate
messages, UID changes, incomplete runs, and the behavior of server-side
deletions. Its synchronization semantics must not be allowed to delete or
rewrite canonical archive content.

### OfflineIMAP3

[OfflineIMAP3](https://github.com/OfflineIMAP/offlineimap3) is another GPLv2+
IMAP-to-Maildir synchronizer. It explicitly supports downloading mailboxes
for local backup and has optional keyring support. Its configuration also
documents XOAUTH2 support, particularly for Gmail.

It is a plausible generic-IMAP staging tool, but the project itself describes
the Python 3 version as an ongoing update of the older Python 2 codebase. It
should be evaluated experimentally rather than treated as the provider
abstraction for this project.

### `imap-backup`

[imap-backup](https://github.com/joeyates/imap-backup) is an MIT-licensed Ruby
tool for incremental local IMAP backups, restore, and account-to-account copy.
It supports a keep-all mode as well as a mirror mode and can export a local
backup for Thunderbird.

Its documentation says that Gmail and Office 365 require an external
`email-oauth2-proxy`. That makes it less attractive as a direct modern-provider
adapter, although it may be useful for a small generic-IMAP staging test.

### Got Your Back (GYB)

[Got Your Back](https://github.com/GAM-team/got-your-back) is Gmail-specific:
it backs up and restores Gmail and Google Workspace accounts through the
Gmail API, supports Gmail searches, and can include Spam and Trash. It also
supports restoring MBOX and EML input.

GYB is a useful reference and possible one-time Gmail acquisition tool. The
archive importer must nevertheless record the Gmail account, labels, search
scope, tool version, and any excluded folders. A GYB backup must not be
considered complete merely because it produced local files; label coverage and
raw-message hashes still require independent verification.

### Provider-specific API paths

The official APIs expose the capabilities needed for native adapters:

* Gmail `messages.get` supports `format=raw`, returning the complete message
  as base64url-encoded data. Gmail also defines XOAUTH2 for IMAP, with the
  `https://mail.google.com/` scope for IMAP access.
* Microsoft Graph can list messages and retrieve a message's MIME content with
  `GET /users/{id}/messages/{id}/$value`. Microsoft recommends Graph for new
  Exchange Online applications. Microsoft also documents XOAUTH2 for Exchange
  Online IMAP, but Basic Authentication is disabled for Exchange Online.

* [Gmail raw-message format](https://developers.google.com/workspace/gmail/api/reference/rest/v1/Format)
* [Gmail IMAP XOAUTH2](https://developers.google.com/workspace/gmail/imap/xoauth2-protocol)
* [Microsoft Graph: get message and MIME](https://learn.microsoft.com/en-us/graph/api/message-get?view=graph-rest-1.0)
* [Microsoft Graph mail API overview](https://learn.microsoft.com/en-us/graph/api/resources/mail-api-overview?view=graph-rest-1.0)
* [Microsoft Exchange Online IMAP OAuth](https://learn.microsoft.com/en-us/exchange/client-developer/legacy-protocols/how-to-authenticate-an-imap-pop-smtp-application-by-using-oauth)
* [Microsoft Exchange Online Basic Authentication retirement](https://learn.microsoft.com/en-us/lifecycle/announcements/basic-auth-deprecation-exchange-online)

## Comparison

| Tool or path | IMAP | Gmail | Microsoft 365 | Local archive output | Canonical ingest fit |
| --- | --- | --- | --- | --- | --- |
| `imapcopy` | Yes, old | Only through whatever IMAP authentication it supports | No documented modern-auth path | Copy-oriented | Historical reference only |
| `imapsync` | Yes | Yes, with provider options/OAuth support | Yes, with OAuth configuration | IMAP destination | Migration and test oracle |
| `mbsync` | Yes | Provider authentication setup required | Provider authentication setup required | Maildir | Staging candidate |
| OfflineIMAP3 | Yes | Documented XOAUTH2 configuration | Documented XOAUTH2 configuration, with compatibility caveats | Maildir | Staging candidate |
| `imap-backup` | Yes | Via OAuth proxy | Via OAuth proxy | Local backup/export | Staging candidate |
| GYB | No, Gmail API | Yes | No | Gmail backup/MBOX/EML material | Gmail one-time acquisition candidate |
| Native adapter | Yes | Gmail API | Graph API | MBOX/Mailbag | Target implementation |

The table describes capabilities documented by the projects or vendors; it is
not acceptance testing. Authentication policies, throttling, folder/label
coverage, and output details must be verified against disposable accounts and
fixtures before any real archive run.

## Why we own the implementation

The tools above solve adjacent problems:

1. Mailbox migration tools optimize for copying between live servers.
2. Mail synchronizers optimize for maintaining a local Maildir replica.
3. Gmail backup tools optimize for Gmail API backup and restore.
4. Provider APIs expose different identifiers, labels, history tokens, folder
   models, and authentication scopes.

Our problem is to create a durable, source-preserving archive. That requires
one controlled publication boundary after provider acquisition. At that
boundary we must:

* retain the exact returned RFC 5322 bytes and SHA-256;
* retain every source occurrence, including duplicate Message-IDs with
  different content;
* preserve provider-specific folder/label and identifier observations without
  confusing them with canonical identity;
* distinguish a provider cursor, a UIDVALIDITY change, and a cryptographic
  integrity result;
* survive interruption and resume only after canonical publication is durable;
* run read-only against the source mailbox; and
* route scanning, MIME parsing, decoding, deduplication, MBOX publication, and
  integrity checkpointing through the existing archive pipeline.

No external tool can be assumed to meet all of those requirements without
testing its exact output and failure behavior. Owning the adapters also avoids
making one provider's cursor model appear universal and lets the archive
continue to work if an external migration project changes direction.

## Planned evaluation order

The first implementation experiments should be deliberately small and
disposable:

1. Generic IMAP against a local disposable server, using direct read-only
   `UID FETCH ... BODY.PEEK[]` acquisition and comparing it with `mbsync` or
   OfflineIMAP3 output.
2. Gmail API acquisition of a test account, including labels, Spam, Trash,
   duplicate Message-IDs, malformed/legacy encodings, and raw-byte hashes.
3. Microsoft Graph acquisition of a test mailbox, including MIME retrieval,
   folder hierarchy, delegated versus application permissions, paging, and
   throttling.
4. Only then evaluate whether any external tool should remain as an optional
   staging/import command.

The first real canonical run requires an explicitly identified archive target
and explicit authorization. Until then, all testing must use purpose-made
fixtures or copied subsets.
