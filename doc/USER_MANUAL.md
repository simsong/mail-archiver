# Mail Archiver User Manual

This manual explains how an archivist creates and searches a mail archive.
Mail Archiver reads source mail without changing it. It stores deduplicated
messages in standard MBOX files, records where every message was found, and
creates integrity information that can be checked independently.

Mail Archiver currently reads:

* MBOX files;
* Emacs RMAIL Babyl files, including extensionless files;
* Maildir folders;
* individual `.eml` files; and
* complete Apple Mail `.emlx` files.

Babyl files are recognized from their `BABYL OPTIONS:` header, not their
filename. Both LF and CRLF RMAIL files are supported. Mail Archiver reads their
original-header blocks and bodies without changing the source files. When an
old record has no original-header block, its visible headers are used instead.
RMAIL labels and redundant visible headers remain only in the source Babyl
container and are not email content.

Outlook PST and OST files, Gmail, Microsoft 365, and live IMAP accounts are
planned but are not yet supported.
The code has inactive integration points for Gmail, IMAP, Microsoft Exchange,
and standard input containing NUL-separated messages; these are not CLI ingest
modes yet.

## Before you begin

Ask the person who installed Mail Archiver to confirm that:

1. `uv` and ClamAV are installed;
2. ClamAV has a current signature database; and
3. this checkout has been prepared with `uv sync`.

Choose two locations:

* **Source mail** is the existing mail that you want to archive. Mail Archiver
  does not change these files.
* **Archive directory** is where the new archive will be written. It should
  have enough free space for the mail, its indexes, and working files.

On macOS, reading Apple Mail or another protected location may require Full
Disk Access for the terminal application.

## Identify the archive owner

Mail Archiver separates sent and received messages. It needs a short text file
containing names or address fragments that identify the archive owner. Put one
lowercase value on each line. Blank lines and lines beginning with `#` are
ignored.

For example:

```text
# Names and address fragments used by the archive owner
jane.example
jexample
```

Review this file before ingest. A message is classified as sent when its
parsed `From:` address contains one of these values, without regard to case.

## Create or add to an archive

From the Mail Archiver checkout, run:

```console
make run ARGS='--archive "/path/to/mail-archive" ingest --owner-names-file owner-names.txt --clamav "/path/to/source-mail"'
```

You may supply more than one source at the end of the command:

```console
make run ARGS='--archive "/path/to/mail-archive" ingest --owner-names-file owner-names.txt --clamav "/Volumes/Backup1/Mail" "/Volumes/Backup2/Mail"'
```

You can instead set the archive location once in the terminal session:

```console
export MAIL_ARCHIVE_DIR="/path/to/mail-archive"
make run ARGS='ingest --owner-names-file owner-names.txt --clamav "/path/to/source-mail"'
```

### What happens during ingest

Mail Archiver:

1. loads the frozen plug-in registries, captures and deduplicates recognized
   source containers, totals their available sizes, and prints every
   unrecognized file and reason;
2. checks that ClamAV is ready before starting the mail workers;
3. reads, parses, and scans messages;
4. stores one canonical copy of each message;
5. records every source container, typed source-integrity check, and message
   observation in the private catalog;
6. updates the BagIt, Mailbag, and message-integrity information; and
7. prints a summary by year when ingest finishes.

Two messages are duplicates only when both their normalized `Message-ID` and
their raw-message SHA-256 match. A message found in several source mailboxes is
stored once, but every source location is remembered. Infected messages are
retained in the quarantine MBOX rather than silently discarded. Apple
`X-Apple-Auto-Saved` messages are recorded but are not copied into the
canonical mailboxes.
Exact empty Eudora MBCP metadata stubs are likewise recorded but not copied.
Legacy `From XXX` status wrappers are unwrapped and their nested email is
archived with the wrapper's source location retained.

Mail Archiver compares a valid `Date:` with a trimmed median of all valid
`Received:` dates after normalizing them to UTC. If they differ by more than
two days, the median controls catalog date and year routing. The original
header and message bytes remain unchanged. The graphical viewer identifies
these messages with a banner and a subtle red background.

If you know the first plausible year for an archive, add
`--earliest-year YEAR` to `ingest`. The default is 1900. For an archive whose
email history begins in 1983, use `--earliest-year 1983`; earlier `Date:` and
`Received:` values are rejected and the remaining source, stream, or path-year
fallbacks apply without rewriting the message.

The progress display shows overall bytes or completed unknown-size containers,
processed messages, estimated time remaining, provider phases, and each active
worker. Publication to the canonical MBOX files and catalog remains
single-writer.

### Stop and continue safely

Press Control-C once for a controlled stop. Mail Archiver closes its files,
commits completed messages, writes an archive checkpoint, and prints a summary.

It is safe to run the same ingest command again. An unchanged source file is
verified by its source plug-in's complete-file control and skipped; its path
and reason are printed. A safely appended MBOX can
resume at its append boundary. Other changes cause the source file to be read
again; already archived messages remain deduplicated.

Source and physical file readers are manifest-loaded generators. Additional
plug-ins can be loaded with a repeatable `--plugin-dir DIRECTORY` option. That
directory contains executable Python, so use this option only with code you
trust. Gmail, IMAP, O365, Microsoft Exchange, and NUL-delimited stdin are
currently reserved names rather than working adapters. See `doc/PLUGINS.md`.

Do not edit files under `data/mbox/` while ingest is running.

## Verify the archive

Verify the archive after ingest and after copying it to new storage:

```console
make verify ARCHIVE="/path/to/mail-archive"
```

Verification reads the archive without changing it. It checks BagIt and
Mailbag structure, whole-file hashes, and the recorded hash for every message.
Investigate any reported failure before continuing to use or copy the archive.

The archive also contains `verify_mail_archive.py`. It can be copied with the
archive and run on a computer that does not have Mail Archiver installed:

```console
python3 /path/to/mail-archive/verify_mail_archive.py
```

## Search with the graphical interface

Start the graphical search interface with:

```console
make gui ARGS='--archive "/path/to/mail-archive"'
```

If no archive was supplied, choose one with **Choose Archive…**.
The window title shows the archive path and the total number of deduplicated,
searchable messages.

Type ordinary words to search indexed headers and message text. The result
list is on the left and the selected message is on the right. The message view
also shows its canonical archive mailbox and every remembered source location.
Search and viewing do not modify the archive.

After three characters, the search box suggests matching addresses and
subjects. Each suggestion shows the number of deduplicated messages in which
it occurs. Address matching includes display names and email addresses, though
only email-address substrings have a dedicated accelerator. Subject matching
finds the characters anywhere in the subject, so `beth` also finds `ELISABETH`.
Use the arrow keys and Return, or click a suggestion.

Selecting an address creates a filter in the search box. Its pop-up menu
controls where that address must occur:

* **Any** searches From, To, Cc, and Bcc;
* **From** searches senders;
* **To**, **Cc**, and **Bcc** search only that header role.

Select **×** or press Delete with an empty search field to remove the last
filter. Selecting a subject completion creates a removable **Subject** filter.

Useful search forms include:

| Search | Meaning |
|---|---|
| `budget` | indexed headers and message text contain budget |
| `any:alice@example.org` | sender, To, Cc, or Bcc contains this address |
| `from:alice@example.org` | sender address contains this value |
| `to:bob@example.org` | a To recipient contains this value |
| `cc:bob@example.org` | a Cc recipient contains this value |
| `bcc:bob@example.org` | a Bcc recipient contains this value |
| `subject:"annual report"` | subject contains this phrase |
| `date:2024-03-15` | message date is this UTC calendar day |
| `before:2024-01-01` | message is earlier than this date |
| `after:2024-01-01` | message is later than this date |

All supplied terms must match. Use the sort controls above the result list to
sort by date, subject, or sender.

Select **Search attachments** to include indexed text attachments. This works
only after an attachment index has been built:

```console
make run ARGS='--archive "/path/to/mail-archive" refresh-index --index-attachments'
```

PDF and Microsoft Office attachment extraction is not implemented yet.

## Filter by original mailbox

Select **Show original folder structure** to display the **Original
mailboxes** tree before the result list.

![Approved original-mailbox filter design](images/original-mailbox-tree-search-v2.png)

The tree behaves as follows:

* Counts are distinct, deduplicated canonical messages, not source
  occurrences.
* Select the checkbox beside a folder to select everything below it. A dash in
  a folder checkbox means that only some contents are selected. Several
  folders or mailboxes can be selected at the same time.
* Selected branches are combined: a result may come from any selected branch.
  The result itself still appears only once.
* A directory whose contents are all single-message EML or EMLX files is shown
  as one logical mailbox rather than thousands of message filenames. Maildir
  `cur` and `new` directories are handled the same way.
* Source volumes are hidden by default. Matching logical paths are merged, so
  `Professional` from `/Volumes/Backup1` and `/Volumes/Backup2` appears once.
  The message viewer continues to list the actual source volumes and paths.
* **Show source volumes** separates the tree by source volume when that detail
  is needed.
* Hiding the tree keeps its selection but disables the original-mailbox
  filter. Showing the tree again restores the selection and filtering. A
  hidden tree never filters results.

### Filter sets

The **Filter set** pop-up replaces a clear-selection button:

* **None** is permanent and means that no original-mailbox filter is applied.
  **Current selection** appears while a selection has not yet been saved.
* A named filter set restores its saved mailbox and folder selections.
* **Save...** asks for a name and saves the current selection. It can also clone
  an existing filter set under a new name.
* The **...** button opens the filter-set manager. The manager lists every saved
  set and allows it to be renamed or deleted. **None** cannot be renamed or
  deleted.

Filter sets are preferences for the current computer. They are stored in the
operating system's standard per-user preferences location (`Library/Preferences`
on macOS, roaming application data on Windows, or the XDG configuration folder
on Linux), not inside the mail archive. They therefore do not change the archive
or travel with it unless the preferences are copied separately.

## Search from the command line

Use command-line search for scripts, remote sessions, or plain-text output:

```console
make search ARGS='--archive "/path/to/mail-archive" to:alice@example.org budget'
make search ARGS='--archive "/path/to/mail-archive" subject:"annual report" after:2024-01-01'
```

The default is ten results. Use `--limit 0` to print every match:

```console
make search ARGS='--archive "/path/to/mail-archive" --limit 0 budget'
```

Every result begins with a stable message number. Supply that number by itself
to display the message:

```console
make search ARGS='--archive "/path/to/mail-archive" 42'
```

Add `--headers` for all headers, `--html` for decoded HTML, or `--mime` for the
complete RFC 5322/MIME source.

## Rebuild the search index

The search database is derived data. Rebuild it from the canonical messages if
it is missing or damaged:

```console
make run ARGS='--archive "/path/to/mail-archive" refresh-index'
```

This does not change the canonical MBOX files. Add `--index-attachments` to
include supported text attachments.

## Care of the archive

* Keep at least two independent copies on different storage systems.
* Run verification after ingest, after a copy, and periodically during storage.
* Do not edit the canonical MBOX files or integrity files by hand.
* Treat `archive.sqlite3` as private: it records source volumes and paths.
* `search.sqlite3` is disposable and may be rebuilt from the canonical mail.
* Preserve the entire archive directory, including hidden and small tag files.
