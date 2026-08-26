# End-to-end testing

The end-to-end suite answers a preservation question, not a coverage question:
can mailarchiver ingest a representative source tree, quarantine an infected
message, publish a valid archive, search the derived index, and retrieve the
verified messages through the user interface?

Run it only through the Makefile:

```console
make test-e2e
```

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

* initial search, sorting, pagination, and attachment search;
* grouped substring completions, suggestion counts, address-role menus,
  subject filters, and the archive/message-count title;
* message selection, keyboard navigation, and search error display;
* MIME alternatives, raw source, safe HTML, and remote-content opt-in;
* attachment previews, risky-file confirmation, exports, and print dispatch;
* archive source and canonical-mailbox provenance;
* original-mailbox counts, selection unions, folder selection, and filtering
  before pagination;
* hiding and restoring the tree, merged and explicit source volumes; and
* saving, cloning, selecting, renaming, and deleting filter sets.

The test retains a Playwright trace on failure.

### Native macOS smoke test

A macOS job still launches the real pywebview application using its
Cocoa/WKWebView backend. It verifies that the window loads, pywebview injects
the JavaScript-to-Python bridge, one real search completes, and the application
closes cleanly. This is the boundary Chromium cannot test.

Run it explicitly with `make test-native-gui`. The native test window is created
hidden, so it does not flash during local or hosted execution. The driver still
exercises the interface through the real WKWebView, but E2E mode validates the
double-click handler and message lookup without creating a second OS window.
Normal application behavior is unchanged.

Testing actual multiwindow Cocoa lifecycle, system dialogs, Finder drag-out,
and native menus requires XCUITest/XCUIAutomation in a logged-in macOS session.
That can be a local acceptance run or a self-hosted Mac runner if it becomes a
required release gate. Appium's Mac2 driver can expose XCUITest through
WebDriver, but adds infrastructure without increasing coverage for this
application. Swift Testing is for Swift logic and does not automate the UI.

## macOS menu bar

Mailarchiver does not currently pass a custom menu to `webview.start`.
pywebview's Cocoa backend creates these native defaults:

* the application menu: About, Services, Hide, Hide Others, Show All, and Quit;
* Edit: Cut, Copy, Paste, and Select All; and
* View: Enter Full Screen.

Before Cocoa creates those menus, the application sets its process and bundle
identity to **Mail Archiver**, including version and copyright metadata and a
mail-archive system icon. Consequently the application menu and standard About
panel no longer identify the host Python interpreter.

There is currently no native File, Search, Window, or Help menu. Choose Archive,
Save Message, Print, mailbox filtering, and filter-set management are HTML
toolbar controls. Command-1 through Command-9 select a displayable MIME part;
Command-0 and Command-Shift-U select raw RFC 5322 source. Those shortcuts are
global JavaScript `keydown` handlers and do not appear as native menu items.

pywebview can add native menu items whose callbacks invoke Python, but its
current cross-platform `MenuAction` interface does not expose keyboard
equivalents or dynamic enabled state. A conventional File or Message menu with
standard macOS shortcuts would therefore need a small AppKit adapter (or a more
complete native application shell) that delegates to the existing application
actions. The HTML controls should remain available so browser acceptance tests
exercise the same underlying operations.

Playwright can test the HTML controls and dispatch the Command-key events, but
it cannot inspect or select Cocoa menu items. The native smoke test establishes
that pywebview created a working application shell; it does not assert the
contents of the inherited menu. If mailarchiver adds application-specific
native menu commands, their callbacks should delegate to the same tested
application actions, and XCUITest should verify their titles, enabled states,
keyboard equivalents, and dispatch.

## Coverage boundaries

| Behavior | Lifecycle pytest | Playwright | Native WKWebView | XCUITest |
|---|---:|---:|---:|---:|
| Ingest, ClamAV, BagIt, standalone verification | Yes | No | No | No |
| Real Python search and message services | Yes | Yes | Smoke | Optional |
| Complete HTML interaction | No | Yes | Smoke | Optional |
| Chromium rendering | No | Yes | No | No |
| Cocoa bridge injection and shutdown | No | No | Yes | Yes |
| Native menus, dialogs, Finder drag, multiple windows | No | No | No | Yes |

The end-to-end gate is successful only when the archive lifecycle passes and
the required interface layers complete. A browser-only pass must never be
reported as proof that the macOS application shell works.
