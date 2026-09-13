+++
title = "Archive Gmail"
description = "Import a Gmail snapshot using Google Takeout and MBOX."
+++
<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->


## END USER

Use [Google Takeout](https://takeout.google.com/) today. This does not grant Email Collection Toolkit access to Gmail and does not require an OAuth client.

1. Choose **Deselect all**, then select **Mail**.
2. Retain all mail data for the strongest completeness evidence.
3. Choose a one-time ZIP export and **Create export**.
4. Download every part when Google emails you, then extract the files beneath
   one directory.
5. Import the extracted directory into Email Collection Toolkit.

Google exports message content, headers, attachments, and labels. Takeout is a
snapshot rather than an incremental service, and large exports may be divided
into several ZIP files. Keep the ZIP files until the resulting archive passes
verification.

For best-effort updates between Takeout snapshots, an account already
synchronized with Apple Mail can be ingested through its local cache. This can
be rerun as more complete messages arrive, but it is not proof of server
completeness. See [Advanced](../advanced/) for cache limits, exact
deduplication, and semantic-message reconciliation.

## DEVELOPER

The canonical [Gmail design and operations document](https://github.com/simsong/email-collection-toolkit/blob/main/doc/GMAIL.md)
covers Takeout ingestion, the future Gmail API adapter, shared Desktop OAuth
clients, Testing versus production, personal-use exceptions, restricted-scope
verification, annual assessments when server-side data handling makes one
necessary, and why IMAP is not an authentication shortcut.

The illustrated [one-time registration reference](../oauth-client-registration/)
remains available for maintainers experimenting with the future API adapter.

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
