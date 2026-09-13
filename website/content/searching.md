+++
title = "Searching"
description = "Find messages across your collection by words, people, dates, subjects, attachments, and original folders."
+++
<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->


Email Collection Toolkit searches the full time span of your local archive.
Search and viewing leave the preserved messages unchanged. Start with a word
or phrase, then narrow the collection by person, date, subject, or source folder.

![The search interface with results and a selected demonstration message](../images/search-interface.png)

*The working interface with a synthetic collection. No private email is shown.*

## Words, phrases, and selectors

Enter words to search indexed headers and message text. Put a phrase in double
quotes to keep its words together. Combine terms and selectors to narrow a
search: every supplied term must match. The empty search screen includes a
built-in reference and examples; submitting an empty query does not list the
whole archive.

| Search | Finds |
| --- | --- |
| `exhibition` | The word in indexed headers or message text |
| `"community archive"` | An indexed phrase |
| `any:alex@example.org` | The address in From, To, Cc, or Bcc |
| `from:alex@example.org` | A matching sender |
| `to:sam@example.org` | A matching To recipient |
| `cc:sam@example.org` | A matching Cc recipient |
| `bcc:sam@example.org` | A matching Bcc recipient |
| `subject:"annual report"` | A phrase in the subject |
| `date:2024-05-01` | Messages on that UTC calendar day |
| `before:2024-05-01` | Messages earlier than that date |
| `after:2024-05-01` | Messages later than that date |
| `from:alex subject:exhibition after:2024-01-01` | Messages matching all three filters |

Address and subject selectors match text within the corresponding fields.
Invalid search syntax is shown as an inline error so you can correct the query.

## Suggestions and search filters

After three characters, the search box suggests addresses and subjects with
message counts. Use the arrow keys and Return, or click a suggestion.
An address becomes a removable filter whose menu selects **Any**, **From**,
**To**, **Cc**, or **Bcc**. Subject suggestions become **Subject** filters.
Click **×** to remove a filter, or press Delete in an empty input to remove
the last one.

## Sort and read results

Sort the complete matching set by **date**, **subject**, or **sender**, in either
direction. Large searches show an initial group of 2,000 results and retrieve
the rest automatically. **Searching in background** indicates more results
are arriving; the final count is exact when that work finishes. There is no
manual next-page control, and starting a new query supersedes the old search.

Click a result to read it beside the list. Use Up and Down to move through
messages, double-click to open a separate message window, or drag the divider
to resize the panes. Each message includes its original headers and source
locations. Search terms are highlighted in headers and body text.

The body menu offers available text and HTML parts plus **Raw Source**.
On macOS, Command-1 through Command-9 selects numbered MIME parts;
Command-0 or Command-Shift-U opens Raw Source. HTML is sanitized and remote
images remain blocked until you choose **Load Remote Content**. Message links
show their destination and offer **Open Link**, **Copy Link**, or **Ignore**.
Attachments can be saved; available preview and open actions depend on their type.

## Find within a message

With a message selected, Command-F opens **Find in message**, initially filled
with the textual part of your archive query. Command-G advances to the next
match; Shift-Command-G goes back. The find text stays when you select another
message. This works with text, HTML, and raw-source views.

Command-A selects results when the result list has focus, or displayed message
text when the message body has focus. The copy-text control copies visible
message text. Save and print controls operate on the selected message.

## Include attachment text

Enable **Search attachments** to include indexed text attachments. The archive
must first have been indexed with attachment support. PDF and Microsoft Office
attachment-text extraction are not currently implemented; the checkbox does
not add those capabilities.

```console
make run ARGS='--archive "/path/to/collection" refresh-index --index-attachments'
```

## Original folders and saved filter sets

Enable **Show original folder structure** to reveal the **Original mailboxes**
tree. Select one or more folders to search their combined contents. Folder
counts represent distinct messages, and duplicate source copies still appear
once in the results. Parent checkboxes include their descendants; a dash means
only part of a branch is selected.

By default, matching logical folders from different backup volumes are combined.
**Show source volumes** separates them when needed. Source paths remain visible
with the message. Hiding the tree disables its filter without forgetting the
selection; showing it restores the filter.

Use **Filter set → Save...** to name a folder selection. Select its name to
restore it, or **None** for no folder filter. The **...** manager renames and
deletes saved sets. These are local preferences, not changes to the archive.

## Independent windows and command-line search

**Window → New Search Window** gives the same collection another independent
query and selection. You can also search from a terminal:

```console
make search ARGS='--archive "/path/to/collection" subject:"annual report" after:2024-01-01'
```

See the [user manual](https://github.com/simsong/email-collection-toolkit/blob/main/doc/USER_MANUAL.md)
for command-line options, search-index rebuilding, keyboard controls, and the
full message-viewing workflow. Semantic search and live provider ingestion are
planned work, not features of the current search interface.
