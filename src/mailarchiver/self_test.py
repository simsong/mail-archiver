# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Disposable end-to-end checks for source and frozen desktop installations.

Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import sys
import tempfile
from importlib import import_module
from pathlib import Path
from threading import Event, Timer

from pydantic import BaseModel, Field

from .__main__ import IngestRequest, run_ingest
from .gui_service import SearchPage, search_page
from .ingest_status import read_ingest_history
from .mailsearch import read_message_bytes
from .standalone_verify import verify_archive


class SelfTestReport(BaseModel):
    mode: str
    frozen: bool = False
    passed: bool = False
    checks: list[str] = Field(default_factory=list)
    error: str | None = None


def exercise_core(directory: Path, report: SelfTestReport) -> Path:
    """Ingest real bytes, verify every preservation digest, query, and rerun."""
    source = directory / "source"
    source.mkdir()
    raw = (b"From: Test Owner <owner@example.invalid>\nTo: Reader <reader@example.invalid>\n"
           b"Date: Mon, 07 Sep 2026 10:00:00 +0000\nSubject: Packaging selftestneedle\n"
           b"Message-ID: <packaging@example.invalid>\n\nOriginal body without a terminal newline")
    mail = source / "message.eml"
    mail.write_bytes(raw)
    owners = directory / "owner-names.txt"
    owners.write_text("owner@example.invalid\n", encoding="utf-8")
    archive = directory / "Self Test.mailarchive"
    request = IngestRequest(archive=archive, owner_names_file=owners, roots=[str(source)],
                            workers=1, scan_policy="not-scanned")
    run_ingest(request)
    from .archive_config import load_archive_config  # pylint: disable=import-outside-toplevel
    from .owner_rules import OwnerRules  # pylint: disable=import-outside-toplevel

    if load_archive_config(archive).owner != OwnerRules(include=["owner@example.invalid"]):
        raise AssertionError("packaged owner YAML did not preserve import rules")
    if (archive / "owner-names-detected.txt").read_text(encoding="utf-8") != "owner@example.invalid\n":
        raise AssertionError("packaged owner detection did not preserve matching sender")
    report.checks.append("owner YAML defaults and detected sender export")
    report.checks.append("ingest without ClamAV")
    errors = verify_archive(archive)
    if errors:
        raise AssertionError(errors)
    report.checks.append("BagIt and original-message SHA-256 verification")
    page = search_page(archive, "selftestneedle", 0, 10, "date", "descending", False, None)
    if len(page.results) != 1:
        raise AssertionError("packaged FTS search did not return the fixture")
    if read_message_bytes(archive, page.results[0].message_pk) != raw or mail.read_bytes() != raw:
        raise AssertionError("original message bytes changed")
    with sqlite3.connect(archive / "archive.sqlite3") as catalog:
        recorded = catalog.execute("SELECT detail FROM metadata_defects WHERE field='antivirus'").fetchall()
        if not recorded or not recorded[0][0].startswith("not-scanned:"):
            raise AssertionError("unscanned message was not labeled")
    history = read_ingest_history(archive)
    if history.errors or history.statuses[0].scan_policy != "not-scanned":
        raise AssertionError("unscanned history was not retained")
    report.checks.extend(["FTS search and byte-exact retrieval", "durable not-scanned evidence"])
    before = hashlib.sha256((archive / "data/mbox/2026-Sent1.mbox").read_bytes()).hexdigest()
    request.owner_names_file = None
    run_ingest(request)
    after = hashlib.sha256((archive / "data/mbox/2026-Sent1.mbox").read_bytes()).hexdigest()
    if before != after or verify_archive(archive):
        raise AssertionError("repeat import changed canonical mail")
    report.checks.append("idempotent repeat import")
    return archive


def exercise_gui(archive: Path, directory: Path, report: SelfTestReport) -> None:
    """Show production windows and exercise the actual WKWebView promise bridge."""
    import webview  # pylint: disable=import-outside-toplevel
    from .application import ApplicationController, ApplicationPreferencesStore  # pylint: disable=import-outside-toplevel
    from .gui_app import AboutStatus, GUI_DIRECTORY, PyWebViewApplication, configure_macos_application, install_macos_document_events  # pylint: disable=import-outside-toplevel
    from .loopback import LoopbackAssetServer  # pylint: disable=import-outside-toplevel

    configure_macos_application()
    controller = ApplicationController(ApplicationPreferencesStore(directory / "preferences.json"))
    application = PyWebViewApplication(controller, LoopbackAssetServer(GUI_DIRECTORY))
    install_macos_document_events(application)
    application.create_about_window()
    api = application.open_document(archive)
    assert api.document is not None
    application.open_ingest_window(api.document)
    failures: list[str] = []

    def evaluate(window, expression):
        ready = Event()
        values = []
        def receive(value):
            values.append(value)
            ready.set()
        window.evaluate_js(expression, callback=receive)
        if not ready.wait(15):
            raise TimeoutError(f"native bridge did not complete: {expression}")
        return values[0]

    def check():
        try:
            windows = list(webview.windows)
            for window in windows:
                if not window.events.loaded.wait(20):
                    raise TimeoutError(f"window did not load: {window.title}")
            result = SearchPage.model_validate(evaluate(api.window, "window.pywebview.api.search('selftestneedle')"))
            if len(result.results) != 1:
                raise AssertionError("GUI search failed")
            ingest = next(window for window in windows if "— Ingests —" in window.title)
            evaluate(ingest, "refreshHistory().then(() => true)")
            if ingest.evaluate_js("document.getElementById('antivirus-warning').hidden"):
                raise AssertionError("missing-scanner import banner is hidden")
            if "without antivirus scanning" not in ingest.evaluate_js("document.getElementById('ingest-detail').textContent"):
                raise AssertionError("unscanned import history warning is missing")
            about = next(window for window in windows if window.title.startswith("About"))
            status = AboutStatus.model_validate(evaluate(about, "window.pywebview.api.status()"))
            if not status.metadata.version or status.disk_free_bytes <= 0:
                raise AssertionError("About bridge did not fill system details")
            startup_errors = [notice.message for notice in status.notices if notice.severity == "error"]
            if startup_errors:
                raise AssertionError(f"Unexpected startup errors: {startup_errors}")
            report.checks.append("startup has no spurious document-open errors")
            report.checks.extend(["visible About, search, and Ingests windows", "native search bridge", "missing-ClamAV banner and history warning"])
            # Exercise the real source picker and its banner without selecting any user data.
            appkit = import_module("AppKit")
            foundation = import_module("Foundation")
            AppHelper = import_module("PyObjCTools.AppHelper")

            def inspect_picker(timer):
                timer.invalidate()
                native = appkit.NSApplication.sharedApplication()
                try:
                    panel = native.modalWindow()
                    if "Antivirus unavailable" not in str(panel.accessoryView().stringValue()):
                        raise AssertionError("native import picker has no antivirus banner")
                    if panel.prompt() != "Import" or not panel.canChooseDirectories() or not panel.canChooseFiles():
                        raise AssertionError("source picker cannot import both directories and files")
                    if not native.delegate().respondsToSelector_("application:openFiles:"):
                        raise AssertionError("Finder document-open handler is missing")
                    menu = native.mainMenu()
                    if [item.title() for item in menu.itemArray()][1:5] != ["File", "Edit", "View", "Window"]:
                        raise AssertionError("native menu order is incorrect")
                    if menu.itemWithTitle_("File").submenu().itemWithTitle_("Open…").keyEquivalent() != "o":
                        raise AssertionError("Command-O is not bound to Open")
                except Exception as error:  # pylint: disable=broad-exception-caught
                    failures.append(str(error))
                finally:
                    native.stopModalWithCode_(0)

            def schedule_picker_check():
                timer = foundation.NSTimer.timerWithTimeInterval_repeats_block_(0.2, False, inspect_picker)
                foundation.NSRunLoop.mainRunLoop().addTimer_forMode_(timer, appkit.NSModalPanelRunLoopMode)

            AppHelper.callAfter(schedule_picker_check)
            if application._import_document(api):  # pylint: disable=protected-access
                raise AssertionError("canceling import started a job")
            report.checks.append("native import picker banner and cancellation; Finder handler; menu order and Command-O")
        except Exception as error:  # pylint: disable=broad-exception-caught
            failures.append(f"{type(error).__name__}: {error}")
        finally:
            application.stop_imports_for_quit()
            for window in reversed(list(webview.windows)):
                window.destroy()

    webview.settings["ALLOW_FILE_URLS"] = False
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    try:
        webview.start(func=check, http_server=False, private_mode=True, menu=application.menu())
    finally:
        application.shutdown()
    if failures:
        raise AssertionError("; ".join(failures))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--self-test", action="store_true", help="test with no UI")
    modes.add_argument("--self-test-gui", action="store_true", help="also show and test native windows")
    parser.add_argument("--report", type=Path, help="write a JSON result")
    args = parser.parse_args()
    report = SelfTestReport(mode="gui" if args.self_test_gui else "headless", frozen=bool(getattr(sys, "frozen", False)))

    def save():
        if args.report:
            args.report.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")

    def timeout():
        report.error = "Self-test exceeded 120 seconds"
        save()
        os._exit(1)  # Native event-loop failures must not hang a release build.

    watchdog = Timer(120, timeout)
    watchdog.daemon = True
    watchdog.start()
    try:
        with tempfile.TemporaryDirectory(prefix="mailarchiver-self-test-") as temporary:
            directory = Path(temporary)
            # Override only this diagnostic process; exercise missing scanner even on developer Macs.
            from . import scanner  # pylint: disable=import-outside-toplevel
            scanner.CLAMD = str(directory / "missing-clamd")
            scanner.CLAMDSCAN = str(directory / "missing-clamdscan")
            scanner.CLAMD_CONFIG = str(directory / "missing-clamd.conf")
            archive = exercise_core(directory, report)
            if args.self_test_gui:
                exercise_gui(archive, directory, report)
            elif "webview.platforms.cocoa" in sys.modules:
                raise AssertionError("headless diagnostics loaded the native GUI")
            report.passed = True
    except Exception as error:  # pylint: disable=broad-exception-caught
        report.error = f"{type(error).__name__}: {error}"
    finally:
        watchdog.cancel()
        save()
    print(report.model_dump_json(indent=2))
    return 0 if report.passed else 1
