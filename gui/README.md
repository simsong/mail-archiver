<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Mail archive GUI prototype

The compiled replacement UI will evaluate **Dioxus Desktop and Tauri**, with Python
retaining ingest, search, and archive preservation. See
[the migration decision](../doc/DIOXUS.md). This directory documents the current
pywebview implementation; no Dioxus frontend or worker bridge exists yet.

Plan comparable trial implementations in Dioxus and Tauri before choosing a
framework. Either approach retains the Python archive engine.

This pywebview prototype searches existing mailarchiver archives. Its
platform-neutral application controller owns multiple archive documents and
multiple independent search windows; native host adapters use WKWebView on
macOS. Windows delivery will use the framework chosen after the trials. The
current application can initialize a selected new archive and run the typed
ingest service against explicitly selected local sources. Canonical writes remain inside the ingest engine and
are guarded by the shared OS writer lease.
On macOS, the native Dock and About identity use the checked-in 192-pixel PNG
derived from the shared `icons/rainbow-post.svg` project icon.

Run it from `archiver/`:

```console
uv sync
make gui ARGS="--archive /path/to/archive"
```

The archive must contain `archive.sqlite3`, `search.sqlite3`, and the canonical
MBOX files referenced by the catalog. Without `--archive`, the app opens the
last valid archive or shows the three-step setup. Use `make gui ARGS="--new"`
to show setup again, or hold Option while launching the Mac app until it appears.
Use **File → Open…** (Command-O) to open an archive in a new window, and **Window → New Search
Window** for another independently searchable view of the active archive.
**File → New** selects a permanent destination before initialization, and
**File → Import…** selects source files/directories and always shows two fields:
**Owner emails (include)** and **Exclude (applied after include)**. Bare rules
match the exact mailbox name; only explicit globs broaden matching, and only
`@` introduces a domain pattern. Confirmed rules become `config.yaml` defaults
for the next import. **File → Document Options…** edits those same lists.
Changes affect future imports; `owner-names-detected.txt` reports matching
archived senders after exclusions. Legacy source owner files only seed the
first dialog when YAML owner settings are absent.
The always-present About window reports version, disk, network, warnings, and
ingest activity. GUI assets are served only over the application's
nonce-authenticated loopback server; Python calls still use the native bridge.

This prototype revision adds attachment metadata, deterministic 18-word body
previews, and a separate text-attachment FTS table to the disposable search
database. Build the index before using the GUI; include
`--index-attachments` to populate attachment search:

```console
make run ARGS="--archive /path/to/archive refresh-index --index-attachments"
```

The prototype provides one-field CLI-compatible complete-archive search, an
explicit **Search attachments** checkbox,
date/subject/sender sorting, keyboard result navigation, MIME-part shortcuts,
message and MIME-part views, separate message windows, sanitized HTML with
remote content blocked by default, inline image and PDF previews, attachment
open/save actions, printing, exact `.eml` export, and experimental Finder
drag-out from result rows and the dedicated message-file well through one shared
implementation. Modifier-click rows to select several messages before dragging. Browsing and hovering never
creates a temporary `.eml`; the first drag prepares it and the next copies the
actual file. The native item explicitly supplies only `public.file-url`; macOS
may generate compatibility aliases such as `NSFilenamesPboardType`.

The interface is for archivists rather than inbox processing. Nonempty queries
count and search the complete collection, regardless of message age. It shows
up to 2,000 matches immediately and loads any remainder automatically in the
background. An empty query displays no results; there are no manual result-page
controls. At startup and after an empty query, the result pane shows examples
for full text, phrases, and every supported selector.

Automated tests verify search parsing, MIME selection, HTML sanitization,
remote-content blocking, exact message export, decoded attachment export, and
risky attachment classification. The complete lifecycle and interface test
design is documented in [`../doc/END_TO_END_TESTING.md`](../doc/END_TO_END_TESTING.md).
Native dialogs, external attachment opening, PDF rendering, Finder drag-out,
multiple Cocoa windows, and the macOS menu bar require XCUITest or manual macOS
acceptance. Run `make gui-smoke` on macOS to verify the real
JavaScript-to-Python bridge.
