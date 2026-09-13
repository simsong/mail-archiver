+++
title = "Privacy policy"
description = "How Email Collection Toolkit's planned Gmail and Microsoft 365 OAuth clients will access and handle account data."
+++
<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->


**Effective date: September 5, 2026**

Email Collection Toolkit is local software for individuals and archivists. The Gmail and
Microsoft 365 connections described here are under development and are not
available in current releases. This policy describes how those connections
will handle account data when they are made available.

## Who is responsible

The Email Collection Toolkit open-source project and Simson Garfinkel provide the OAuth
clients. Questions or requests concerning this policy may be directed through
[Simson Garfinkel's contact page](https://simson.net/page/Contact_Simson).

## Authorization and account access

Email Collection Toolkit accesses a cloud account only after the account holder or an
authorized representative completes the provider's OAuth consent process. It
does not request or store the account password. The consent screen identifies
the requested permissions, and the user may decline or revoke access.

Email Collection Toolkit will request only permissions needed for read-only archival
acquisition:

- For Gmail, this may include the account identifier and email address;
  messages and their original content; headers, bodies, attachments,
  labels, dates, thread and message identifiers; and information needed to
  enumerate the mailbox and resume an authorized acquisition.
- For Microsoft 365, this may include the account identifier and email
  address; mailbox folders; messages and their MIME content; headers, bodies,
  attachments, dates, and provider identifiers; and information needed to
  enumerate the mailbox and resume an authorized acquisition.
- OAuth access and refresh tokens may be retained so an interrupted or
  incremental acquisition can continue without asking the user to sign in for
  every request.

Email Collection Toolkit will not request permission to send mail, delete mail, alter
messages or folders, change labels, or mark messages read. It will not mutate
the source account.

## How account data is used

OAuth-acquired data is used only to provide the archival functions requested
by the user:

- preserve original message content in a user-selected local archive;
- record where each message came from;
- detect duplicate copies without discarding their source observations;
- scan, index, preview, and search the local collection;
- generate collection reports and material supporting finding aids; and
- export user-directed preservation or access packages.

Email Collection Toolkit does not use cloud-account data for advertising, marketing,
surveillance, sale, generalized artificial-intelligence model training, or
profiling unrelated to the user's archive.

Email Collection Toolkit's use of information received from Google Workspace APIs will
adhere to the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy),
including the Limited Use requirements.

## Storage and transfer

Messages, attachments, source records, search indexes, reports, and exported
packages are stored on the user's computer or in a storage location the user
selects. Except for authorization and API requests sent directly to Google or
Microsoft, Email Collection Toolkit does not transmit OAuth-acquired content to a server
operated by the project or by Simson Garfinkel.

OAuth credentials and tokens will be stored locally using the operating
system's credential protection. They will not be written into the Mailbag,
its manifests, collection reports, test data, or ordinary logs. Access to
cloud accounts will remain disabled until this storage method is implemented
and documented.

Email Collection Toolkit does not share OAuth-acquired account data with the developer or
unrelated third parties. A user may explicitly export or transfer selected
content to another system, such as ePADD or an institutional digital
repository. That user-directed transfer is governed by the receiving system's
terms and privacy practices.

## Retention, deletion, and revocation

Email Collection Toolkit creates a long-lived local archive, so acquired messages and
derived data remain in the user-selected storage location until the user or
the responsible institution deletes them under its own retention policy.
Revoking OAuth access prevents future provider access but does not silently
delete an existing preservation archive.

Users may revoke Google access through their Google Account's third-party
connections settings. Microsoft work or school users may revoke consent
through the Microsoft My Apps portal when tenant policy permits; an
administrator may need to revoke administrator-granted consent. Users may
delete local archives, exports, and search indexes using their operating
system, and may remove locally stored OAuth credentials using the removal
mechanism supplied with the completed provider integration.

The project does not retain a server-side copy that must be separately deleted.
If a user voluntarily provides diagnostic material for support, it will be
used only to address that request; users should remove private message content
and credentials before sending diagnostics.

## Security

Provider authorization and account traffic use the providers' documented
OAuth and HTTPS interfaces. Email Collection Toolkit requests read-only access and keeps
the archived messages local. Users and institutions remain responsible for
securing their computers, archive storage, backups, and exported packages.

## Changes to this policy

This policy will be updated before OAuth behavior materially changes. The
effective date above will change with each revision. Continued provider access
will not be used for a newly disclosed purpose without the authorization or
consent required by the applicable provider and law.
