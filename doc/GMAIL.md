<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Gmail

## END USER

### Recommended today: Google Takeout

Google Takeout creates a read-only snapshot without granting Email Collection Toolkit
access to a Google account. It is the supported Gmail acquisition path today.

1. Open [Google Takeout](https://takeout.google.com/).
2. Choose **Deselect all**, then select **Mail**.
3. Under **All Mail data included**, retain every mailbox needed for the
   archive. Selecting all mail gives the best completeness evidence.
4. Choose **Next step**, a one-time export, ZIP format, and an archive-part
   size appropriate for the available disk and download connection. Google
   permits sizes up to 50 GB, which reduces the number of parts.
5. Choose **Create export**. Google emails the account when the files are
   ready; generation can take from minutes to days.
6. Download every archive part before its link expires, then extract all parts
   beneath one source directory. Keep the downloaded ZIP files until the
   Email Collection Toolkit import has been verified.
7. Import the extracted directory. The current command-line interface is:

   ```console
   MAIL_ARCHIVE_DIR="/path/to/archive" uv run mailarchiver ingest --owner-names-file owner-names.txt --clamav "/path/to/extracted-takeout"
   ```

   Direct drag-and-drop of Takeout ZIP files is planned but is not implemented.

Takeout does not delete or modify Gmail. The export contains message content,
headers, attachments, and Gmail labels. Google writes labels into the
`X-Gmail-Labels` message header; Email Collection Toolkit preserves the source message
bytes, although it does not yet expose those labels as structured search
fields.

Takeout is a snapshot, not an incremental synchronization service. Google does
not currently offer mail export by date range. Large or repeated exports can
therefore be expensive in time and disk space. Google can schedule an export
every two months for one year, but each result still has to be downloaded and
imported. Reimporting overlapping MBOX content is safe because Email Collection Toolkit
records source observations and deduplicates only messages with the same
normalized Message-ID and raw-message SHA-256.

### Interim incremental recovery through Apple Mail

Until a direct Gmail adapter exists, a user may synchronize Gmail with Apple
Mail and ingest the complete `.emlx` records in `~/Library/Mail`. This can add
messages downloaded since the last Takeout import. It is best-effort recovery:
Apple Mail may retain `.partial.emlx` placeholders or omit detached attachment
content, so the cache cannot prove completeness. Rerunning ingest is safe.
Byte-identical copies in Takeout and the cache share one canonical record and
retain both observations. If Apple Mail rewrites raw headers, the variant is
preserved separately and can be reconciled with the `h3` semantic-message
hash. See [APPLE_MAIL_CACHE.md](APPLE_MAIL_CACHE.md).

Google Workspace administrators can restrict whether organizational users may
export data. A generated Takeout archive expires after about seven days and is
limited to five downloads; this has nothing to do with OAuth registration.

References:

- [Download Google data](https://support.google.com/accounts/answer/3024190)
- [Export Gmail data](https://support.google.com/mail/answer/10016932)

## DEVELOPER

### Acquisition strategy

Google Takeout is the baseline acquisition path. A future Gmail API adapter may
provide richer label and history metadata after the baseline. Generic IMAP is
the first planned live cloud importer because the same read-only acquisition
engine can serve Gmail, Microsoft 365, and conventional IMAP accounts. It does
not avoid Google OAuth.

| Path | End-user registration | Authorization | Intended role |
| --- | --- | --- | --- |
| Takeout MBOX | None | User signs into Google Takeout | Supported offline baseline |
| Gmail IMAP | None | Shared OAuth client with `https://mail.google.com/` | First planned live import |
| Gmail API | None | Shared Desktop OAuth client with `gmail.readonly` | Later provider-specific metadata |
| IMAP app password | User creates a 16-digit password | Password-like credential | Limited fallback, not a distribution strategy |

Apple Mail cache acquisition is an implemented local-file bridge, not a Gmail
API or IMAP adapter. Its provider relationship is provenance; it neither
contacts Google nor establishes that the local cache is complete.

The Takeout importer must eventually accept all ZIP parts directly, reject
archive traversal, links, special files, and decompression bombs, stream files
without loading an export into memory, discover every MBOX, and produce a
completeness report. ZIP extraction is derived staging; the downloaded Takeout
parts and their hashes should remain available until canonical Mailbag
verification succeeds.

### Shared Gmail API client

A distributed OAuth build uses one Google Cloud project and one Desktop client
registered by the Email Collection Toolkit maintainer. End users do not create projects,
obtain client IDs, or register their copies of the program. The installed-app
client ID and nominal client secret are public client configuration, not a
confidential credential; each user's refresh token remains private in that
user's operating-system credential store.

The project and client registration do not expire every seven days:

- In **Testing**, the maintainer lists test accounts and each test user's
  authorization expires after seven days. The program is not re-registered.
- An **In production** unverified project does not require a test-user list,
  but users see Google's warning and the project has a lifetime limit of 100
  new users.
- Public distribution beyond the personal-use exception requires brand and
  restricted-scope verification.

The illustrated [client-registration procedure](OAUTH_CLIENT_REGISTRATION.md)
is for release maintainers only.

### Verification and security assessment

`gmail.readonly` is a restricted scope. A public application that does not
qualify for an exception must complete Google's restricted-scope review. Google
says an independent security assessment is required when an application can
access restricted data from or through a third-party server. Email Collection Toolkit's
intended architecture is entirely local: neither messages nor tokens pass
through developer-operated infrastructure. That makes an assessment exemption
likely, but Google makes the determination during verification.

If Google requires an assessment, it must be performed by a Google-empanelled
assessor and renewed every 12 months from the prior Letter of Validation. The
annual reassessment is comprehensive even when the application has not changed;
it is not required separately for each ordinary software release. New
restricted scopes can require scope re-verification and possibly an expanded
assessment. Changes to the consent-screen name, logo, redirect URI, homepage,
or privacy-policy URL can trigger brand re-verification. Google does not publish
a fixed assessment price; the developer obtains quotes from approved assessors.

References:

- [Restricted-scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)
- [Annual recertification](https://support.google.com/cloud/answer/13463816)
- [Changes to an approved app](https://support.google.com/cloud/answer/13464018)
- [Personal-use exception](https://support.google.com/cloud/answer/13464323)

### IMAP still requires broad Google authorization

Gmail no longer permits a generally distributed client to authenticate using
the user's ordinary password. OAuth IMAP requires the full
`https://mail.google.com/` restricted scope, which authorizes broader mailbox
access than this read-only archive needs. Google explicitly recommends the
Gmail API when an application does not require that full scope. App passwords
require two-step verification, are unavailable for some Workspace,
security-key-only, and Advanced Protection accounts, and are discouraged by
Google. Choosing IMAP first reduces implementation duplication; it does not
make the Gmail authorization or verification boundary smaller.

References:

- [Gmail IMAP OAuth](https://developers.google.com/workspace/gmail/imap/xoauth2-protocol)
- [Gmail third-party clients](https://support.google.com/mail/answer/7126229)
- [Google app passwords](https://support.google.com/mail/answer/185833)

### Current implementation boundary

Local MBOX ingestion is implemented and byte-preserving. Automatic Takeout
creation and download, direct Takeout ZIP ingestion, structured
`X-Gmail-Labels` indexing, and live Gmail API acquisition are not implemented.
The `mailarchiver-auth` command exercises the proposed shared-client
authorization boundary but does not ingest Gmail.

## Current acquisition boundaries

No release Desktop OAuth client is bundled yet. The shared-client end-user
flow remains deferred until a maintainer supplies and validates that public
configuration in release artifacts. Current authorization requires a developer
client override. Installing that override validates and writes the same bytes.
Known consumer domains need no DNS lookup; transient DNS and token-refresh
transport failures are disclosed as errors rather than negative detection or
fresh consent. Credentials are stored only after the profile matches.

A whole Apple Mail cache containing `.partial.emlx` files cannot currently be
ingested: discovery rejects those files and stops the run. Only a separately
staged copy containing complete supported records is an available local-file
bridge. Do not modify the source cache to prepare that copy; the comparator is
read-only and does not imply whole-cache ingest support.
