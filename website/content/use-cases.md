+++
title = "Use cases"
description = "Examples of personal and institutional email archive workflows."
+++
<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->


## For Individuals

An individual may have email exports from several accounts stored on old
computers and backup drives. Email Collection Toolkit can read the supported files into
one local archive and build a search index for the combined collection.

Email Collection Toolkit does not change the source files. It stores the original message
data in MBOX files, records where each copy was found, identifies duplicate
messages, and creates a new search index when requested.

The program can:

- search messages across decades, accounts, and backup sets;
- report message counts by year, sender, and recipient;
- record each source in which a duplicate message was found; and
- verify files and messages with SHA-256 hashes.

## For Archivists

A library may receive a donor's digital files from several sources, including:

- two laptops;
- ten external hard drives;
- a Gmail account that the family has authorized the library to access; and
- an export of the donor's university Microsoft 365 account.

Email Collection Toolkit can read supported email files from the computers, drives, and
account exports into one archive. Its local viewer lets library staff search
the collection and preview messages and attachments. Collection reports list
years, senders, and recipients and can provide data for a finding aid. The
archive records the file and storage source in which each message was found.

Each email archive is stored natively as a BagIt 1.0 package conforming to
Mailbag 1.0; there is no separate BagIt/Mailbag export step. Email is stored
in MBOX files under `data/mbox/`, with Mailbag metadata, source
records, and SHA-256 hashes. The MBOX files can be transferred to ePADD for
additional appraisal, review, and access work. ePADD does not currently open a
Mailbag package directly.

## Available and planned source support

Current releases read local MBOX, EML, Maildir, Emacs RMAIL Babyl, and complete
Apple Mail `.emlx` messages. A Gmail or Microsoft 365 export can be read if it
uses one of these formats.

Direct access to Gmail and Microsoft 365 accounts is planned but is not yet
available. PST and OST files, Mailbag import, automatic transfer to ePADD,
redaction, and more detailed finding-aid output are also planned.

## File and source handling

Email Collection Toolkit uses the same process for personal and institutional collections:

- source mailboxes, accounts, stores, and exports are read-only;
- the original RFC 5322 message data is stored in MBOX files;
- every location containing a duplicate message is recorded;
- search indexes can be created again from the archived messages; and
- the archive uses MBOX, BagIt 1.0, Mailbag 1.0, and SHA-256.

Email Collection Toolkit is not an email client or a hosted service. See the
[README](https://github.com/simsong/email-collection-toolkit/blob/main/README.md) for
installation and operating instructions.
