# Email Collection Toolkit User Manual

This manual describes the current application. The planned compiled desktop
experience will evaluate [Dioxus and Tauri](DIOXUS.md) with the existing Python
archive engine; Windows delivery must include full ingest. This decision does
not introduce an available Windows release or change the current macOS installer.

Plan comparable trial implementations in Dioxus and Tauri before choosing a
framework. Either approach retains the Python archive engine.

This manual explains how an archivist creates and searches a mail archive.
Email Collection Toolkit reads source mail without changing it. It stores deduplicated
messages in standard MBOX files, records where every message was found, and
creates integrity information that can be checked independently.

Each archive directory uses BagIt 1.0 and Mailbag 1.0 as its native storage
format. Messages live in MBOX files under `data/mbox/`, alongside archive
metadata and SHA-256 integrity information elsewhere in the archive directory.
This is the archive the application uses, not a separate export; no conversion
step is needed to obtain a BagIt/Mailbag archive. SQLite catalogs and search
indexes are derived data.

On macOS, open the supplied DMG and drag **Email Collection Toolkit.app** to its
**Applications** shortcut. Eject the disk and open the installed app.
Python is included. Development builds are ad-hoc signed, not notarized;
see [installation and signing notes](MACOS_DISTRIBUTION.md).

Email Collection Toolkit currently reads:

* MBOX files;
* Emacs RMAIL Babyl files, including extensionless files;
* Maildir folders;
* individual `.eml` files; and
* complete Apple Mail `.emlx` files.

Babyl files are recognized from their `BABYL OPTIONS:` header, not their
filename. Both LF and CRLF RMAIL files are supported. Email Collection Toolkit reads their
original-header blocks and bodies without changing the source files. When an
old record has no original-header block, its visible headers are used instead.
RMAIL labels and redundant visible headers remain only in the source Babyl
container and are not email content.

Outlook PST and OST files and direct Gmail, Microsoft 365, and live IMAP
connections are planned but are not yet supported. Complete local Apple Mail
cache records from those providers can be imported now.
The code has inactive integration points for Gmail, IMAP, Microsoft Exchange,
and standard input containing NUL-separated messages; these are not CLI ingest
modes yet.

## Before you begin

Ask the person who installed Email Collection Toolkit to confirm that:

1. `uv` and ClamAV are installed;
2. ClamAV has a current signature database; and
3. this checkout has been prepared with `uv sync`.

Choose two locations:

* **Source mail** is the existing mail that you want to archive. Email Collection Toolkit
  does not change these files.
* **Archive directory** is where the new archive will be written. It should
  have enough free space for the mail, its indexes, and working files.

On macOS, reading Apple Mail or another protected location may require Full
Disk Access for the terminal application.

## Configure archive sources

The planned archive-wide import interface stores an ordered source list in
`archive.yaml` at the top of the archive. This workflow is not implemented in
the current release; current imports still use the explicit source paths shown
under [Create or add to an archive](#create-or-add-to-an-archive).

An archive can contain three kinds of source:

| Source kind | What it identifies |
| --- | --- |
| **FILE** | One local MBOX, EML, Babyl, EMLX, or other supported mail file |
| **LOCAL FOLDER** | A local directory recursively searched for supported mail files |
| **IMAP** | A remote account identified by server, port, and username |

Each source has a permanent ID so its observations and last successful check
remain associated with the same source after later imports. For example:

```yaml
version: 1
sources:
  - id: takeout-2026
    kind: file
    path: /Users/your.name/Downloads/takeout-mail.mbox
  - id: historical-mail
    kind: local-folder
    path: /Volumes/Archive/Old Mail
  - id: personal-imap
    kind: imap
    server: imap.example.org
    port: 993
    username: your.name@example.org
    tls: implicit
    authentication: password
    credential_ref: keyring://mail-archiver/personal-imap
    folders: all
```

`archive.yaml` contains no password or OAuth token. `credential_ref` is only
the name of an item in the operating-system keychain or configured secrets
provider. When a password is missing, an interactive import asks for it without
echoing it and stores it in that credential system. An OAuth IMAP source opens
the provider's browser authorization instead. A noninteractive import with a
missing credential stops without printing or saving the secret elsewhere.

### Import/Refresh and Import/Rebuild

Both actions visit every enabled FILE, LOCAL FOLDER, and IMAP source and are
safe to repeat. Neither action deletes or recreates archived messages.

| Action | Local files | IMAP accounts |
| --- | --- | --- |
| **Import/Refresh** | Walk folders to find new paths. Do not open or hash a known file when its modification time has not changed since its last completed import. | Use saved folder and UID checkpoints to retrieve new or changed messages. |
| **Import/Rebuild** | Ignore modification-time shortcuts and recompute the complete SHA-256 of every file. A matching hash can then skip parsing; changed files are processed again. | Perform a complete folder and UID reconciliation rather than relying only on the incremental cursor. |

Refresh intentionally trusts local modification times. If another program
changes a file but preserves its old modification time, Refresh will not find
that change; use Rebuild when that is possible or when validating a copied or
restored source. Directory traversal is still required during Refresh so new
files can be discovered.

These actions concern acquisition sources. They are different from
`refresh-index`, which reads mail already in the archive and rebuilds only the
disposable search database.

## Import Gmail

Use Google Takeout for Gmail today. It creates MBOX files without granting Email Collection Toolkit access to the account. Download every Takeout ZIP part, extract them
beneath one directory, and ingest that directory as the local source. See
[GMAIL.md](GMAIL.md) for the complete end-user procedure and the separate
developer discussion of Gmail API, OAuth, IMAP, verification, and security
assessment requirements.

Live Gmail authorization is a developer preview for an unimplemented future
adapter. End users should not run `mailarchiver-auth` or create a Google Cloud
project for ordinary Takeout ingestion.

As an interim incremental path, add the Gmail account to Apple Mail, configure
it to download attachments, allow the wanted mailboxes to synchronize, and
import its cache as described in [Use Apple Mail as a provider bridge](#use-apple-mail-as-a-provider-bridge).
This is best-effort recovery and is not a substitute for Takeout when a
completeness claim matters.

## Import Microsoft 365

Microsoft has no platform-neutral Takeout equivalent. Outlook can export PST
on Windows or OLM from legacy Outlook for Mac, but Email Collection Toolkit does not yet
ingest those formats and its Microsoft authorization adapter is only a stub.
There is currently no complete Microsoft 365 export workflow supported by Email Collection Toolkit. See [M365.md](M365.md) for the end-user status and developer design.

Apple Mail can export selected mailboxes as MBOX, and Email Collection Toolkit can read
complete messages from an Apple Mail cache. A cache can be incomplete, however;
see [APPLE_MAIL_CACHE.md](APPLE_MAIL_CACHE.md) before treating it as an
acquisition source.

## Use Apple Mail as a provider bridge

Until direct adapters are written, Apple Mail can provide local complete
messages for Gmail, Microsoft 365/Exchange Online, Outlook.com, and ordinary
IMAP accounts that have already been synchronized to this Mac. Quit Mail if
practical, set **Download Attachments** to **All**, allow synchronization to
finish, and export selected mailboxes as MBOX or stage a separate copy containing
only complete supported messages. Do not ingest the whole `~/Library/Mail` tree
when it contains `.partial.emlx` files: discovery rejects them and stops the run.
The invoking terminal may require Full Disk Access. Never alter the source cache
to prepare the staged copy.

Only complete `.emlx` payloads are accepted. `.partial.emlx`, detached
attachments, indexes, and plist metadata are not treated as messages. A cache
is therefore best-effort recovery, not evidence that every server message was
downloaded. See [APPLE_MAIL_CACHE.md](APPLE_MAIL_CACHE.md) for the measured
limitations and preflight guidance.

Ingest may be rerun whenever Apple Mail has downloaded more messages. Unchanged
containers are skipped, and messages already present under the exact archive
identity are not written again.

## Identify the archive owner

Email Collection Toolkit uses include/exclude rules to separate Sent and
Archive mail. Every GUI import shows **Owner emails (include)** and **Exclude
(applied after include)**. Enter rules on separate lines or separated by commas.
Rules ignore case and match the whole mailbox name before `@` by default:

| Rule | Matches | Does not match |
| --- | --- | --- |
| `slg` | `slg@example.org` | `3slg@example.org`, `slg+tag@example.org` |
| `*simson*` | `simsong@example.org` | `other@simson.net` |
| `*@simson.net` | `other@simson.net` | `other@example.org` |
| `slg@example.org` | That full address | `slg@other.org` |

`*` matches any sequence, `?` one character, and `[abc]` one listed character.
Exclusions use the same syntax and always win: include `*simson*` and exclude
`*david*` excludes `david_simson@example.org`. A bare `slg` also matches the legacy
sender `slg` without a domain. Display names are not owner rules.

After confirmation, the archive's `config.yaml` stores the defaults:

```yaml
version: 2
owner:
  include:
    - slg
    - '*simson*'
  exclude:
    - '*david*'
```

Quote wildcard rules when editing YAML manually. The GUI writes YAML for you.
The CLI uses these settings automatically, or accepts `--owner-names-file`
with one include rule per line (configured exclusions still apply). Blank lines
and `#` comments are ignored. Legacy files now use these exact/glob semantics,
not substring matching. Review rules before starting a fresh archive.

## Create or add to an archive

The following is the currently implemented explicit-path interface. It does
not yet read the planned `archive.yaml` source registry.

From the Email Collection Toolkit checkout, run:

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

Email Collection Toolkit:

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
their raw-message SHA-256 match. A byte-identical message found in a backup,
Takeout export, and Apple Mail cache is stored once, but every source location
is remembered. If Apple Mail rewrites the raw headers, that source variant is
preserved separately even when its semantic hash matches. Infected messages are
retained in the quarantine MBOX rather than silently discarded. Apple
`X-Apple-Auto-Saved` messages are recorded but are not copied into the
canonical mailboxes.
Exact empty Eudora MBCP metadata stubs are likewise recorded but not copied.
Legacy `From XXX` status wrappers are unwrapped and their nested email is
archived with the wrapper's source location retained. Immediate double framing
is normalized with a literal `X-From:` header; see [Advanced](#advanced) for the
exact rule and its provenance record.

Email Collection Toolkit compares a valid `Date:` with a trimmed median of all valid
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
The same status is atomically updated in a new `status/ingest-*.json` file in
the archive. That file retains final totals when the run finishes. Every later
ingest creates another file, so the archive accumulates a run history without
changing the database schema.

### Stop and continue safely

Press Control-C once for a controlled stop. Email Collection Toolkit closes its files,
commits completed messages, writes an archive checkpoint, and prints a summary.

It is safe to run the same ingest command again. The current CLI verifies an
unchanged source file with its source plug-in's complete-file control before
skipping it; this corresponds to the stronger hashing performed by the planned
Import/Rebuild action. A safely appended MBOX can
resume at its append boundary. Other changes cause the source file to be read
again; already archived messages remain deduplicated.

Source and physical file readers are manifest-loaded generators. Additional
plug-ins can be loaded with a repeatable `--plugin-dir DIRECTORY` option. That
directory contains executable Python, so use this option only with code you
trust. Gmail, IMAP, O365, Microsoft Exchange, and NUL-delimited stdin are
currently reserved names rather than working adapters. See `doc/PLUGINS.md`.

Do not edit files under `data/mbox/` while ingest is running.

## Compare Apple Mail with the archive

Run the read-only reconciliation before or after another cache ingest:

```console
make compare-apple-mail
```

The defaults are `~/Library/Mail` and `~/mail-archive`; use
`ARGS='--apple-mail /path/to/Mail --archive /path/to/archive'` for other
locations. The report separates exact raw matches, semantic-only matches,
cache-only messages, and archive-only messages. It also lists aggregate header
names that Apple added, removed, or changed, without displaying values or
message content.

The lookup uses the archive's `h3` semantic-message v1 SHA-256. Despite the
informal phrase “normalized header hash,” h3 is a **whole-message** identity:
it applies DKIM-relaxed normalization to the selected stable and delivery
headers and includes the complete canonicalized body. It deliberately ignores
mutable transport and mail-client headers. `h3` supports reconciliation; the
admission/deduplication identity remains normalized `Message-ID` plus the `h2`
raw-message SHA-256. See [INTEGRITY_CONTROLS.md](INTEGRITY_CONTROLS.md) for the
exact algorithm and [APPLE_MAIL_CACHE.md](APPLE_MAIL_CACHE.md) for measured
results from this computer.

## Extract printed email from a standalone PDF

This workflow is for a PDF that is itself the surviving source document after
email was printed and scanned. It is not for a PDF attachment inside an email.

The first implementation produces a standard, derived MBOX for human review:

```console
make extract-pdf-mail ARGS='tests/data/sipbadmin.pdf --output /tmp/sipbadmin.mbox --handwritten-page 2'
```

The command requires Poppler `pdftotext`. It reads and hashes the PDF without
changing it, classifies every page, extracts at most one printed message from
each qualifying page, refuses to overwrite the output, and reports non-message
pages. Repeat `--handwritten-page PAGE` for pages whose useful handwritten
annotations should be recorded. Their text is not extracted or indexed.

Each output record is labeled `machine-unreviewed` and records the source PDF
SHA-256, page range, extraction and segmentation policy, and handwriting flag.
After comparing the generated MBOX to the PDF, a human reviewer may correct the
derived text and change the status to `human-reviewed`. The reviewed MBOX is
still a derived interpretation; it is not original RFC 5322 source bytes.

This focused command does not yet copy PDFs into `data/pdf/`, route generated
records into `data/pdf-mbox/`, add them to search, suppress exact duplicates,
or connect the viewer to the source page. Those are the next archive-integration
steps.

## Verify the archive

Verify the archive after ingest and after copying it to new storage:

```console
make verify ARCHIVE="/path/to/mail-archive"
```

Verification reads the archive without changing it. It checks BagIt and
Mailbag structure, whole-file hashes, and the recorded hash for every message.
Investigate any reported failure before continuing to use or copy the archive.

The archive also contains `verify_mail_archive.py`. It can be copied with the
archive and run on a computer that does not have Email Collection Toolkit installed:

```console
python3 /path/to/mail-archive/verify_mail_archive.py
```

## Search with the graphical interface

Start the graphical search interface with:

```console
make gui ARGS='--archive "/path/to/mail-archive"'
```

If no archive was supplied, the application opens the last valid archive or
shows the three-step setup described below. Use **File → Open…** (Command-O) to
open an existing archive in a new window. **Window → New Search Window** opens
another independently searchable window on the active archive. Recent archives
are kept in **File → Open Recent**. A missing or invalid saved archive is
ignored, removed from recents, and reported in the About window. **File → New**
asks for a new or empty `.mailarchive` destination before initializing and
opening it. On macOS, **File → Import…** opens one picker for local files and
directories, sets up owner names, shows the destination and sources for final
confirmation, and starts import. When ClamAV is missing or unconfigured, the
import screen displays an antivirus warning. **Install ClamAV…** opens its
official download page; it does not install software automatically. You may
instead explicitly choose **Import Without Scanning**, or Cancel. The unscanned
warning is retained in import history. Installing ClamAV later does not scan
previously imported messages automatically.
The **Import Directory…** button in the Ingests window starts the same workflow
for that window's archive, opening the same source picker directly.
In the picker, select files or directories and click **Import**. You can also
navigate into a directory and import it; directories include supported mail
files and subdirectories. No separate Files/Folders choice is needed.
Every import displays both owner fields, prefilled from the archive's YAML.
Before YAML owner settings exist, `owner-names.txt` in the archive and selected
source roots can seed the fields. There is no built-in personal owner list.
After **Continue**, review the destination, sources, and rules, then confirm
**Import**. Canceling either dialog does not save rules. Source files are
unchanged. Future imports show the saved rules again; lists are rewritten only
when changed.

**File → Document Options…** edits the same two fields with **Save**. Importing
blocks edits. The panel warns when rules differ from the last recorded import.
Changes affect future imports only; rebuilding the search index does not move
existing messages between Sent and Archive. To repair old misclassification,
build a fresh archive from your original sources with reviewed rules.

`owner-names-detected.txt` is regenerated after import with the sorted matching
sender addresses, after exclusions. Review this file to check the effect of your
rules. It is never copied into the YAML. Keep `config.yaml` when preparing a
rebuild: it also remembers your last source-picker directory. Invalid YAML
produces an error rather than silently replacing your owner rules.
For a saved document, the window title shows the archive path and total number
of deduplicated, searchable messages.
On first launch, all three setup steps appear together:

1. **Select the root folder to ingest** with **Choose folder…**. Its files and
   subfolders are read without changing them.
2. **Select where your archive is stored**. Choose an existing archive or an
   empty folder you have already created outside the source tree. The Mac setup
   browsers disable **New Folder** to avoid changing input folders while browsing.
3. Click **Start import**, review the owner-name and antivirus settings, and
   follow progress in the Ingests window.

The chosen paths stay visible; either folder button can be used again. Canceling
selection keeps the previous path. Keep source and archive folders separate;
neither may contain the other. **Cancel** (or Escape) quits the application;
the button itself starts no import and makes no preference changes. It does not
undo earlier changes: startup may already have removed a missing or invalid
remembered archive from saved preferences before showing setup. If another import is running,
the normal Stop Import and Quit confirmation protects its checkpoint.
To show setup again, hold **Option (Alt)** while launching the Mac app until
setup appears, or Option-click its Dock icon while it is running. From the CLI:

```sh
make gui ARGS='--new'
```

This bypasses the last archive for this launch without clearing it from recent
archives. It also bypasses `MAIL_ARCHIVE_DIR`; do not combine `--new` with
`--archive`. About opens from the application menu when needed.
File New asks for a destination before opening its search window and offering
Import; accepting the default Untitled name works.
Command-N creates an archive and Command-W closes an eligible search window.

The About window remains available for the application run. It shows the
installed version, free disk space, live Internet reachability, startup errors,
warnings, and current or latest ingest activity. The Window menu lists it and
every search and Ingests window. During Import, the search window that started
the run cannot be closed from **File → Close** or its close box; other windows
remain searchable and independently closeable.

The status line at the bottom shows a running ingest, or summarizes the most
recent run. Click it to open the independent Ingests window. You can also use
**Window → Ingests**. The window lists all retained runs and shows the selected
run's sources, totals, failures, and every configured worker thread. It has its
own close box; opening it again while it is visible brings the same window to
the front.

Type ordinary words to search indexed headers and message text. The result
list is on the left and the selected message is on the right. The message view
also shows its canonical archive mailbox and every remembered source location.
MBOX byte locations appear as `path?offset=N` (with no suffix for offset zero).
Use the small copy icon beside a local source path to put that pathname, without
the offset, on the macOS pasteboard as both text and a file URL.
When there is spare vertical space in the message viewer, **Locations** remains
at the bottom of the pane.
Search and viewing do not modify the archive.

Ordinary search words and textual selector values are highlighted with a yellow
background wherever they appear in the selected message's displayed headers or
body. Matching is case-insensitive and works in the HTML, plain-text, and
raw-source views. Thus `from:beth` highlights `beth` in the displayed From
header. Quoted text is highlighted as one phrase; date selectors remain filters
and do not create highlights. Highlighting changes only the viewer; it never
changes canonical message bytes or the search index.

With a message selected, press Command-F to open **Find in message**. It starts
with the first textual part of the archive search—so `from:beth` starts with
`beth`—and selects that value so you can replace it. The finder highlights the
replacement text in the message. Press Command-G for the next match or
Shift-Command-G for the previous one. Selecting another message keeps the find
text but restarts at that message's first match, including an HTML message body.

Command-A follows where you last clicked. In the result list it selects every
result row. In a plain-text or raw-source message it selects that displayed
message body, not the viewer headers, attachments, or **Locations** evidence;
an HTML message uses the browser's native text selection. Use the ⧉ control in
the message toolbar to copy the visible text. For HTML, the copied text also
includes the displayed subject and message headers.

Email Collection Toolkit searches an archival collection; it is not an inbox or mail
program. A search therefore covers the collection's complete time span. Recent
messages receive no preference beyond an explicitly selected date sort, and an
archivist never has to ask the application to check older years.

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
sort the complete matching set by date, subject, or sender. The application
first counts up to 2,001 matches. If that scan ends at 2,000 or fewer, the
count is exact and every result is displayed. Otherwise it displays the first
2,000 in the selected sort order and automatically retrieves the rest. During
that work the result status is red, says **Searching in background**, and shows
the displayed count. When the background search finishes, the full result set
and exact count are shown. Starting another search supersedes the earlier
background work.

Pressing Return with an empty search field displays no results. Enter a term,
selector, search-box filter, or original-mailbox selection to define an
archival search. The result pane shows this complete search-language table and
examples when the program starts and whenever the search is empty.

Select **Search attachments** to include indexed text attachments. This works
only after an attachment index has been built:

```console
make run ARGS='--archive "/path/to/mail-archive" refresh-index --index-attachments'
```

PDF and Microsoft Office attachment extraction is not implemented yet.

## Viewing messages

Select a search result to view it beside the result list, or double-click the
result to open it in an independent window. That window has its own scrollbar,
so the complete message, attachments, and **Locations** section remain
reachable. The pull-down menu above the message
lists its displayable plain-text and HTML MIME parts and always offers **Raw
Source**, which shows the complete RFC 5322 message. Command-1 through Command-9
select the part with that numeric MIME part ID; Command-0 and Command-Shift-U
select **Raw Source**. If a message supplies more than one HTML part, Email Collection Toolkit initially displays the most substantial decoded one; every part
remains available from the menu.

Some early Netscape messages use `<x-html>...</x-html>` around an HTML body even
though their multipart declaration has no usable MIME boundaries. When the
wrapper encloses the entire body, the viewer recognizes it automatically,
offers **HTML — legacy x-html**, and selects that HTML view by default. A message
that merely mentions `<x-html>` in ordinary text is not reinterpreted. **Raw
Source** remains available, and this display recovery never changes the archived
message bytes.

All HTML views are sanitized before display. Scripts, forms, plugins, file URLs,
event handlers, and unsafe URL schemes are removed. Remote images remain blocked
unless you choose **Load Remote Content** for that message; embedded CID images
may render from the verified message. Hovering an allowed link shows its complete
destination in the bottom status bar. Clicking it shows the destination and
offers **Open Link**, **Copy Link**, and **Ignore**; a link opens only after you
choose **Open Link**.

### Date adjusted

The archive normally routes a message using its `Date:` header. When a usable
`Date:` value is more than two days from the trimmed median of the usable dates
in its `Received:` headers, the archive instead uses that median in UTC and
marks the catalog date source as `received-median`. The message then has a
red-tinted warning such as:

> **Date adjusted:** The date in the Date: header (Tue, 31 Dec 2024 12:00:00
> +0000) is more than two days from the median date of the Received: headers
> (2024-02-02T00:00:00+00:00). **Archive routing uses the computed UTC date
> (2024-02-02T00:00:00+00:00).**

The first value is the original `Date:` header exactly as decoded for display.
The second is the computed UTC median of the usable `Received:` header dates.
The third is the UTC date used to place the message in the archive. The median
and routing dates are currently the same, but both are shown so an archivist can
see the source evidence and the resulting routing decision. The original header
and canonical message remain unchanged.

Attachments and their previews appear below the body. Opening an attachment is
always explicit, with confirmation for active, unknown, or mismatched MIME/suffix
pairs. Only allowlisted matching PDF, static image, and plain-text pairs bypass
that extra confirmation.
The bottom of the message view separately lists the canonical archive mailbox
and every source volume and source or forensic path where the message was found.
**Save Message…** exports an exact, SHA-256-verified `.eml` copy without changing
the archive. To drag a message to Finder, use the message-file icon beside its
headers. The application creates no temporary mail files while you browse or
hover over results. The first drag prepares the verified disposable `.eml` file;
drag it again to transfer the ready file.

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
  as one logical mailbox rather than thousands of message filenames. A valid
  Maildir is shown at its root, not as `cur`, `new`, or individual message
  files. Apple Mail cache paths end at their `.mbox` package names and omit
  internal UUID, `Data`, bucket, `Messages`, and `.emlx` components.
  `[Gmail].mbox` observations are labeled as local Gmail cache copies. If the
  same message was acquired directly from Gmail, that provider observation is
  shown first as the preferred source; both observations remain in the catalog.
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

## Advanced

### Double-processed MBOX envelopes

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
[MBOX reading notes](MBOX_READING.md)
for compatibility evidence and limits.

### Diagnosing import failures

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

## Care of the archive

* Keep at least two independent copies on different storage systems.
* Run verification after ingest, after a copy, and periodically during storage.
* Do not edit the canonical MBOX files or integrity files by hand.
* Treat `archive.sqlite3` as private: it records source volumes and paths.
* `search.sqlite3` is disposable and may be rebuilt from the canonical mail.
* Preserve the entire archive directory, including hidden and small tag files.

## Contacts command

The read-only Contacts command is the first address-level Contacts interface:

```console
make run ARGS='--archive "/path/to/mail-archive" human-contacts --owner-address-file owner-names.txt --format table'
```

It reports each likely human direct correspondent's email address, first appearance, last
appearance, and all-header message count. The owner-address file accepts the
older identifying address fragments, separated by newlines,
commas, or semicolons; blank lines and `#` comments are ignored. It resolves
those aliases only to catalogued **Sent** sender addresses, then uses the
resulting exact addresses for meaningful-contact tests. Repeat `--owner-address`
for a temporary exact additional address. The default meaningful selection includes outgoing To/Bcc recipients
and incoming senders only when an exact owner address is in To; it excludes Cc
traffic and mailing-list mail addressed only indirectly. Use `--all` to inspect
all From/To/Cc/Bcc addresses instead, and `--format tsv` or `--format json` for
scripts. Mailing lists, automated/no-reply services, malformed addresses, and
local parts longer than 48 characters are excluded by a versioned packaged
policy. Shared role inboxes such as root, staff, support, and webmaster are
also excluded. The command only reads archive.sqlite3.
Exceptional valid human identities are explicit allow rules in that same policy;
they take precedence over the generic malformed-address checks.

### Contact-filter policy

The packaged default is `src/mailarchiver/contact_filters.yaml`. To
preserve archive-specific decisions, copy that complete file to
`/path/to/mail-archive/contact_filters.yaml` and edit the copy. The
archive copy takes precedence whenever it exists; without one, Email Collection Toolkit
uses the packaged default. Set `mode: replace` for a complete, valid versioned
replacement policy. Set `mode: extend` to add rule lists to the packaged
policy; duplicates are removed and the packaged scalar threshold remains in
effect. Invalid policies fail rather than silently falling back.

## Planned: Contacts window and geography

The forthcoming **Contacts** window will list one email address per contact,
with first appearance, last appearance, and message count. Its checked-by-
default **Meaningful** option will show direct correspondents: people you sent
to in To or Bcc, and people who sent to an owner address directly in To. Cc
traffic and messages sent only through a mailing list will not qualify.
The current `config.yaml` include/exclude rules classify sent mail. A future archive
creation dialog and **File → Properties** will maintain the exact owner
addresses used by Contacts.

The same window will support US ZCTA input such as `02139`, displaying its city,
state, and country, and later a rough straight-line radius around a place. A
contact's **located** evidence (for example, a signature address) is distinct
from an **affiliated** institution (for example, a university domain); an
affiliation is not treated as a home location.

The application will ship with a seed US geography database. `make
geography-data` and **Tools → Update Geo Database** will refresh the shared,
per-user reference data. It lives outside individual archives: on macOS in
`~/Library/Application Support/Email Collection Toolkit/geography/`; on Windows in
`%LOCALAPPDATA%\\Email Collection Toolkit\\geography\\`; and on Linux in
`$XDG_DATA_HOME/mailarchiver/geography/` (or
`~/.local/share/mailarchiver/geography/`). A future explicit command will let
you copy a geography snapshot into an archive or choose that snapshot instead
of your installed data. These features are documented design commitments and
are not in the current release.

## Configuration

The planned top-level `archive.yaml` belongs to one archive and contains that
archive's FILE, LOCAL FOLDER, and IMAP source definitions. It contains local
paths and account names but no passwords or tokens. Source credentials remain
in the operating-system keychain or configured secrets provider. Import
checkpoints and observations remain in `archive.sqlite3`; successful imports
do not rewrite checkpoint state into the YAML file.

Email Collection Toolkit's current application-level configuration is the versioned YAML
file `src/mailarchiver/configuration.yaml` in the source checkout. It currently
controls the search-highlight background used by the graphical message viewer:

```yaml
version: 1
gui:
  search_highlight_background: "#fff59d"
```

The color must be a six-digit hexadecimal CSS color beginning with `#`. The
initial value, `#fff59d`, is yellow. Stop and restart the graphical interface
after changing the file; configuration is validated and loaded once when the
application starts. Email Collection Toolkit rejects unknown settings, unsupported
versions, and invalid color values instead of passing them to the viewer.

This YAML file contains packaged application display policy. It does not
replace:

* the planned per-archive `archive.yaml` source registry;
* archive `config.yaml`, which stores owner include/exclude rules and navigation;
* `MAIL_ARCHIVE_DIR` or `--archive`, which selects an archive;
* the installed ClamAV configuration; or
* saved original-mailbox filter sets, which remain per-user preferences.

Changing the highlight color affects only derived display rendering. It does
not modify the source mail, canonical MBOX files, catalog, or search database.
Versioned policy files that affect archive-derived interpretation, such as
`contact_filters.yaml`, may instead be copied in full to the archive
root. An archive-local copy takes precedence over the packaged source copy, so
the archive retains the policy that produced its derived contact results.
Packaged runtime YAML files declare `mode: replace`; an archive-local policy
that supports `mode: extend` documents the lists it adds to that default.
