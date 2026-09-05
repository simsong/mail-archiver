# End-to-end testing

The end-to-end suite answers a preservation question, not a coverage question:
can mailarchiver ingest a representative source tree, quarantine an infected
message, publish a valid archive, search the derived index, and retrieve the
verified messages through the user interface?

Run it only through the Makefile:

```console
make test-e2e
```

## Synthetic name-resolution benchmark

The privacy-safe benchmark under `benchmarks/name_resolution/` captures the
five alias shapes needed to evaluate automatic name assembly: short initials,
concatenated names, numeric suffixes, and dotted names across organization,
public-mail, academic, university, and museum-style domains. All addresses
use reserved `.test` domains and the expected identity is synthetic.

Run it with:

```console
make benchmark-name-resolution
```

The current score measures only the header-only baseline; it is a benchmark
fixture for a future resolver, not a claim that address-only names are already
inferred.

Install the pinned Chromium build once with `make install-test-browser`.
`make test-e2e` is headless and does not create or flash a desktop window.

`make check` runs the ordinary tests first and then this suite. The end-to-end
test uses the committed, non-sensitive corpus under `e2e_tests/data/source`.
`make fixture-e2e` regenerates that corpus deterministically after an intentional
fixture change.

## Archive lifecycle

The platform-independent part of the suite performs a real CLI ingest with the
configured on-demand ClamAV daemon. It checks all of these boundaries together:

* discovery of MBOX, EML, and EMLX sources;
* exact and semantic deduplication, autosave exclusion, and source provenance;
* attachment extraction and message and attachment search indexes;
* canonical MBOX publication and the documented final-newline policy;
* BagIt manifests, catalogued byte locations, and hash verification;
* final typed per-run status history outside tag-manifest fixity;
* quarantine of one EICAR test message; and
* the installed, standard-library-only standalone verifier.

The complete EICAR signature is never stored in Git. The test constructs it
from separate fragments inside its private temporary source tree, ingests it,
and deletes that generated source immediately. The quarantine copy exists only
inside the disposable test archive.

## Search-interface test layers

The interface has two distinct boundaries and should not make either one stand
in for the other.

### Browser acceptance test

The comprehensive interface test uses
[`pytest-playwright`](https://playwright.dev/python/docs/intro), Playwright's
official pytest integration. A pytest fixture constructs the real
`GuiApi` against the archive created by the lifecycle test. Playwright's
[`expose_function`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-expose-function)
binds those Python methods into the browser, and an initialization script
presents them under the same promise-returning `window.pywebview.api`
interface used by the application.

This is a test adapter, not a mock: searches, mailbox counts, message reads,
exports, filter-set persistence, and errors still execute the production Python
and SQLite code. Chromium is the required CI browser. Playwright WebKit is a
useful additional rendering-engine run, but it is not Apple's Cocoa WKWebView
host.

Playwright exercises the complete HTML interface:

* empty-query suppression, complete-archive query results, sorting, and attachment search;
* grouped substring completions, suggestion counts, address-role menus,
  subject filters, and the archive/message-count title;
* message selection, keyboard navigation, and search error display;
* MIME alternatives, raw source, safe HTML, and remote-content opt-in;
* attachment previews, risky-file confirmation, exports, and print dispatch;
* archive source and canonical-mailbox provenance;
* original-mailbox counts, selection unions, folder selection, and complete
  scoped results;
* hiding and restoring the tree, merged and explicit source volumes; and
* saving, cloning, selecting, renaming, and deleting filter sets;
* the main ingest-status line and its window action; and
* the separate ingest-history page, final statistics, and worker rows.

The test retains a Playwright trace on failure.

### Native macOS smoke test

The explicit local macOS target launches the real pywebview application in an
isolated subprocess using its Cocoa/WKWebView backend. It verifies that the
hidden window loads, pywebview injects the JavaScript-to-Python bridge, one real
search returns its highlight terms, and the application closes cleanly. This is
the boundary Chromium cannot test, and it is not run in CI/CD.

Run it explicitly with `make test-native-gui`. The native test window is created
hidden against a purpose-built one-message derived archive. It does not run
ingest or ClamAV. The smoke-only page calls the real `status()` and `search()`
bridge methods and sends one completion callback to Python. Its dedicated
bridge exposes only those three calls, and smoke mode omits the custom
application menu. Python never polls WKWebView with a synchronous JavaScript
evaluation. Secondary windows, dialogs, attachment openers, and exports are
therefore inaccessible in smoke mode. The comprehensive interaction flow
remains in headless Chromium; normal application behavior is unchanged.

`make test-native-html-find` is a separate opt-in macOS check and intentionally
briefly shows its window. It uses a static, scriptless HTML iframe to verify
WKWebView's computed active-match style and outer-pane scrolling when finder
navigation crosses from a header to an offscreen body match. It complements the
hidden bridge smoke and the Chromium interaction test; it does not exercise a
live archive part or native physical keyboard input.

The Chromium interaction test also delays a real `part()` bridge response while
the user starts Command-F/Command-G and selects another result. It proves that
the new result remains at its first finder match rather than accepting stale
keyboard work. It separately checks Command-A boundaries for result rows, plain
text, raw source, and nonselectable provenance.

The child process atomically writes a JSON report after each phase and has a
watchdog for bridge completion and Cocoa shutdown. Pytest independently bounds
the process, captures a five-second macOS process sample on an outer timeout,
and then terminates the whole process group. The Makefile retains these local
diagnostics under `.tmp/native-gui-diagnostics`. Hosted CI does not launch
AppKit or WKWebView; its required GUI coverage is the complete headless Chromium
acceptance test.

Testing window contents through Cocoa accessibility, system dialogs, Finder
drag-out, and native-menu selection requires XCUITest/XCUIAutomation in a
logged-in macOS session.
That can be a local acceptance run or a self-hosted Mac runner if it becomes a
required release gate. Appium's Mac2 driver can expose XCUITest through
WebDriver, but adds infrastructure without increasing coverage for this
application. Swift Testing is for Swift logic and does not automate the UI.

## Native application menus

Mailarchiver passes custom **File** and **Window** menus to `webview.start`.
File supplies **New**, **Open…**, launch-time **Open Recent**, **New Search
Window**, **Import…**, and **Close** actions. Window supplies **Ingests** and
the current About, search, and Ingests window inventory. Every callback
resolves the active logical search window when invoked; opening an archive
creates a new document window rather than retargeting an existing one.
pywebview's Cocoa backend also creates these native defaults:

* the application menu: About, Services, Hide, Hide Others, Show All, and Quit;
* Edit: Cut, Copy, Paste, and Select All; and
* View: Enter Full Screen.

Before Cocoa creates those menus, the application sets its process and bundle
identity to **Mail Archiver**, including version and copyright metadata and a
mail-archive system icon. Consequently the application menu and standard About
panel no longer identify the host Python interpreter.

There is currently no native Search or Help menu. Save Message, Print, mailbox
filtering, and filter-set management remain HTML toolbar controls. Command-1
through Command-9 select a displayable MIME part; Command-0 and Command-Shift-U
select raw RFC 5322 source. Those shortcuts are global JavaScript `keydown`
handlers and do not appear as native menu items.

The cross-platform `MenuAction` interface invokes Python but does not expose
keyboard equivalents or dynamic enabled state. A small AppKit adapter rebuilds
the active macOS menu when window or ingest state changes and disables **Close**
for About and for the search window that owns Import. The controller also
refuses that menu action and native close event. Standard keyboard equivalents
still need a more complete native application shell.
The HTML controls remain available so browser acceptance tests exercise the
same underlying operations.

Playwright can test the HTML controls and dispatch the Command-key events, but
it cannot inspect or select Cocoa menu items. The native smoke test establishes
that pywebview created a working application shell; it does not assert the
contents of the inherited menu. Lifecycle pytest covers document identity,
read-only rejection of invalid saved databases, destination-safe New,
active-window routing, independent search state, real subprocess writer
contention and release, nonce-authenticated loopback assets, and shared ingest
state. XCUITest and a corresponding Windows native harness still need
to verify menu titles, enabled states, keyboard equivalents, activation,
file-open events, and dispatch in packaged applications under issues 72 and 76.

## Coverage boundaries

| Behavior | Lifecycle pytest | Playwright | Native WKWebView | XCUITest |
|---|---:|---:|---:|---:|
| Ingest, ClamAV, BagIt, standalone verification | Yes | No | No | No |
| Real Python search and message services | Yes | Yes | Smoke | Optional |
| Complete HTML interaction | No | Yes | Smoke | Optional |
| Chromium rendering | No | Yes | No | No |
| Cocoa bridge injection and shutdown | No | No | Yes | Yes |
| Document identity, startup, and active-window routing | Yes | No | No | Yes |
| Independent Ingests-window content and action routing | No | Yes | No | Yes |
| Native menu inspection, dialogs, Finder drag | No | No | No | Yes |

The required end-to-end gate is successful only when the archive lifecycle and
headless browser interface pass. A browser-only pass is not proof that the
macOS application shell works; `make test-native-gui` supplies separate local
smoke evidence. Required native evidence must come from
XCUITest/XCUIAutomation in a logged-in macOS session.
