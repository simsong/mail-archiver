+++
title = "Email Collection Toolkit for curation"
description = "Functions and file formats for processing an email collection."
sort_by = "weight"

[extra]
eyebrow = "For archivists"

[[extra.stages]]
number = "01"
title = "Identify email files"
description = "Scan selected local directories for MBOX, EML, Maildir, Emacs RMAIL Babyl, and complete Apple Mail .emlx messages."

[[extra.stages]]
number = "02"
title = "Read the sources"
description = "Read supported files without changing the source files and record files that were skipped or could not be processed."

[[extra.stages]]
number = "03"
title = "Create the archive"
description = "Store the original message data in MBOX files, identify duplicates, and record where each message was found."

[[extra.stages]]
number = "04"
title = "Search and report"
description = "Search the collection, preview messages and attachments, and report counts by year, sender, and recipient."

[[extra.stages]]
number = "05"
title = "Verify and share"
description = "Verify the archive's SHA-256 hashes or transfer its MBOX files to ePADD. The archive already uses BagIt 1.0 and Mailbag 1.0 as its native storage format."
+++
<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->


## What the program does

Email Collection Toolkit reads supported email files into a local archive. It stores the
original message data in MBOX files, records the source of each message, and
identifies duplicate messages. It builds a local search index for the archive.

The viewer lets library staff search messages and preview messages and
attachments. Reports provide message counts by year, sender, and recipient.
These reports can provide data for a finding aid.

Email Collection Toolkit uses BagIt 1.0, conforming to Mailbag 1.0, as the native storage
format for each email archive, not as a separate export format. The archive
contains MBOX files, Mailbag metadata, source records, and SHA-256
hashes. Its MBOX files can be transferred to ePADD for additional appraisal,
review, and access work.

The [personal and institutional use cases](@/use-cases.md) show how this
workflow applies to decades of personal exports and to a donor's digital
estate.

## What is not yet available

Current releases do not connect directly to Gmail or Microsoft 365 accounts.
They also do not read PST or OST files, import Mailbag packages, transfer files
to ePADD automatically, redact messages, or produce a complete finding aid.

The pages below link to project reports and other digital-preservation
organizations and projects.
