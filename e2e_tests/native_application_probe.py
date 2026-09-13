# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Exercise the production About and document bridges in a real Cocoa process.

Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""

import os
from importlib import import_module
from pathlib import Path
from threading import Event
from time import monotonic, sleep

import webview

from mailarchiver.application import ApplicationController, ApplicationPreferencesStore, IngestJob
from mailarchiver.gui_app import GUI_DIRECTORY, PyWebViewApplication, configure_macos_application, install_macos_document_events, macos_import_picker, macos_owner_rules
from mailarchiver.loopback import LoopbackAssetServer
from mailarchiver.owner_rules import OwnerRules
from mailarchiver.document_options import DocumentOptions
from mailarchiver.writer_lock import WriterLease

AppHelper = import_module("PyObjCTools.AppHelper")
NSApplication = import_module("AppKit").NSApplication
NSModalPanelRunLoopMode = import_module("AppKit").NSModalPanelRunLoopMode
NSTextView = import_module("AppKit").NSTextView
NSRunLoop = import_module("Foundation").NSRunLoop
NSTimer = import_module("Foundation").NSTimer
PROBE_ARCHIVE_ENV = "MAILARCHIVER_NATIVE_PROBE_ARCHIVE"
PROBE_PREFERENCES_ENV = "MAILARCHIVER_NATIVE_PROBE_PREFERENCES"


def evaluate_async(window: webview.Window, expression: str):
    """Wait for the real JavaScript Promise result, not its serialized Promise object."""
    completed = Event()
    results = []

    def received(value):
        results.append(value)
        completed.set()

    window.evaluate_js(expression, callback=received)
    assert completed.wait(10), f"JavaScript request timed out: {expression}"
    return results[0]


def main() -> None:
    """Open a real extensionless fixture after About, inspect native menus, then quit."""
    configure_macos_application()
    archive = Path(os.environ[PROBE_ARCHIVE_ENV])
    server = LoopbackAssetServer(GUI_DIRECTORY)
    controller = ApplicationController(ApplicationPreferencesStore(Path(os.environ[PROBE_PREFERENCES_ENV])))
    application = PyWebViewApplication(controller, server)
    install_macos_document_events(application)
    application.create_about_window()
    about = webview.windows[0]
    failures: list[str] = []

    def check_new_search(enabled: bool) -> None:
        done = Event()

        def inspect() -> None:
            try:
                menu = NSApplication.sharedApplication().mainMenu()
                assert menu.itemWithTitle_("File").submenu().itemWithTitle_("New Search Window") is None
                item = menu.itemWithTitle_("Window").submenu().itemWithTitle_("New Search Window")
                assert item is not None and bool(item.isEnabled()) == enabled
            except Exception as error:  # pylint: disable=broad-exception-caught
                failures.append(str(error))
            finally:
                done.set()

        application._refresh_menus()  # pylint: disable=protected-access
        AppHelper.callAfter(inspect)
        assert done.wait(5), "New Search Window menu inspection timed out"

    def probe() -> None:
        try:
            assert about.events.loaded.wait(10), "About bridge failed to load"
            status = evaluate_async(about, "window.pywebview.api.status()")
            assert status["metadata"]["version"], status
            assert status["disk_free_bytes"] > 0, status
            assert not [notice for notice in status["notices"] if notice["severity"] == "error"], status
            assert about.evaluate_js("Object.keys(window.pywebview.api)") == ["status"]
            application.add_notice("warning", "Native About regression check")
            evaluate_async(about, "refresh().then(() => true)")
            assert "Native About regression check" in about.evaluate_js("document.getElementById('notices').textContent")
            assert about.evaluate_js("document.getElementById('error').hidden")
            check_new_search(False)
            # Closing the sole visible window must keep menus alive, without
            # status polling or Dock activation reopening About.
            native_about = about.native
            assert native_about is not None
            AppHelper.callAfter(native_about.performClose_, None)
            application.add_notice("warning", "Notice while About is dismissed")
            evaluate_async(about, "refresh().then(() => true)")
            dismissed = Event()

            def inspect_dismissed() -> None:
                try:
                    native = NSApplication.sharedApplication()
                    assert not native_about.isVisible(), "Dismissed About is still visible"
                    assert not about.events.closed.is_set(), "About was destroyed instead of hidden"
                    assert webview.windows == [about], f"Unexpected windows: {[window.title for window in webview.windows]}"
                    assert application.window_menu_items() == [], "Hidden About remains in Window menu"
                    native.delegate().applicationShouldHandleReopen_hasVisibleWindows_(native, False)
                    assert not native_about.isVisible(), "Dock activation reopened About"
                    menu = native.mainMenu()
                    assert menu.itemWithTitle_("File").submenu().itemWithTitle_("Open…").isEnabled(), "File/Open is disabled"
                    item = menu.itemAtIndex_(0).submenu().itemAtIndex_(0)
                    assert native.sendAction_to_from_(item.action(), item.target(), item), f"About action failed: {item.action()!r}"
                except Exception as error:  # pylint: disable=broad-exception-caught
                    failures.append(str(error))
                finally:
                    dismissed.set()

            AppHelper.callLater(1.2, inspect_dismissed)
            assert dismissed.wait(5), "About dismissal check timed out"
            reopened = Event()

            def inspect_reopened() -> None:
                try:
                    assert native_about.isVisible(), "About menu did not restore the window"
                    assert webview.windows == [about], "About must restore the retained window"
                except Exception as error:  # pylint: disable=broad-exception-caught
                    failures.append(str(error))
                finally:
                    reopened.set()

            AppHelper.callLater(0.3, inspect_reopened)
            assert reopened.wait(5), "About menu reopening check timed out"
            # Disabling command-line reinterpretation must retain genuine Finder opens.
            native = NSApplication.sharedApplication()
            AppHelper.callAfter(native.delegate().application_openFiles_, native, [str(archive)])
            deadline = monotonic() + 10
            api = application.active_api()
            while api is None and monotonic() < deadline:
                sleep(0.01)
                api = application.active_api()
            assert api is not None, "Finder document-open event did not open the archive"
            document = api.document
            assert document is not None and document.path is not None and document.display_path is not None
            assert api.search_window is not None
            assert api.window.events.loaded.wait(10), "Document bridge failed to load"
            assert evaluate_async(api.window, "window.pywebview.api.status()")['message_count'] == 1
            assert api.window.evaluate_js("document.getElementById('choose-archive') === null")
            check_new_search(True)
            application._refresh_menus()  # pylint: disable=protected-access
            done = Event()

            def inspect_menu() -> None:
                try:
                    menu = NSApplication.sharedApplication().mainMenu()
                    titles = [item.title() for item in menu.itemArray()]
                    assert titles[1:5] == ["File", "Edit", "View", "Window"], titles
                    file_menu = menu.itemWithTitle_("File").submenu()
                    assert file_menu.itemWithTitle_("Open…").keyEquivalent() == "o"
                except Exception as error:  # pylint: disable=broad-exception-caught
                    failures.append(str(error))
                finally:
                    done.set()

            AppHelper.callAfter(inspect_menu)
            assert done.wait(5), "Native menu inspection timed out"
            # GUI requirement: one source picker accepts both files and directories.
            def inspect_import_picker(timer) -> None:
                timer.invalidate()
                app = NSApplication.sharedApplication()
                modal = app.modalWindow()
                try:
                    assert modal is not None, "Import picker did not appear"
                    assert document.display_path is not None
                    assert document.display_path.name in modal.title()
                    assert str(document.display_path) in modal.message()
                    assert modal.prompt() == "Import"
                    assert modal.canChooseDirectories() and modal.canChooseFiles()
                    assert modal.allowsMultipleSelection()
                except Exception as error:  # pylint: disable=broad-exception-caught
                    failures.append(str(error))
                finally:
                    app.stopModalWithCode_(0)

            def schedule_inspection() -> None:
                timer = NSTimer.timerWithTimeInterval_repeats_block_(0.1, False, inspect_import_picker)
                NSRunLoop.mainRunLoop().addTimer_forMode_(timer, NSModalPanelRunLoopMode)

            AppHelper.callAfter(schedule_inspection)
            assert not application._import_document(api)  # pylint: disable=protected-access
            assert document.ingest_job is None, "Cancel must not start ingest"

            def accept_directory(timer) -> None:
                timer.invalidate()
                panel = NSApplication.sharedApplication().modalWindow()
                try:
                    assert panel is not None, "Directory picker did not appear"
                    assert panel.prompt() == "Import"
                    assert panel.canChooseDirectories() and panel.canChooseFiles()
                    assert Path(panel.directoryURL().path()) == archive
                    NSApplication.sharedApplication().stopModalWithCode_(1)
                except Exception as error:  # pylint: disable=broad-exception-caught
                    failures.append(str(error))
                    NSApplication.sharedApplication().stopModalWithCode_(0)

            def schedule_directory_selection() -> None:
                timer = NSTimer.timerWithTimeInterval_repeats_block_(0.2, False, accept_directory)
                NSRunLoop.mainRunLoop().addTimer_forMode_(timer, NSModalPanelRunLoopMode)

            AppHelper.callAfter(schedule_directory_selection)
            selected = macos_import_picker(
                archive, "Import directory test", "Select this entire directory",
                "Import", folders=True, multiple=True,
            )
            assert len(selected) == 1 and selected[0].samefile(archive), selected

            # GUI requirement: multiline owner entry replaces a second file picker.
            for accept in (False, True):
                def enter_owner_names(timer) -> None:
                    timer.invalidate()
                    app = NSApplication.sharedApplication()
                    modal = app.modalWindow()
                    try:
                        assert modal is not None and "Owner emails for" in modal.title()
                        views = [modal.contentView()]
                        editors = []
                        while views:
                            view = views.pop()
                            views.extend(view.subviews())
                            if isinstance(view, NSTextView) and view.isEditable():
                                editors.append(view)
                        assert len(editors) == 2, "Expected include and exclude editors"
                        for editor in editors:
                            if editor.identifier() == "owner-include":
                                assert editor.string() == "slg"
                                editor.setString_("*Simson*\nslg")
                            elif editor.identifier() == "owner-exclude":
                                assert editor.string() == "*david*"
                                editor.setString_("*David*")
                            else:
                                raise AssertionError("Unexpected owner editor")
                    except Exception as error:  # pylint: disable=broad-exception-caught
                        failures.append(str(error))
                    finally:
                        app.stopModalWithCode_(1000 if accept else 1001)

                def schedule_owner_entry() -> None:
                    timer = NSTimer.timerWithTimeInterval_repeats_block_(0.1, False, enter_owner_names)
                    NSRunLoop.mainRunLoop().addTimer_forMode_(timer, NSModalPanelRunLoopMode)

                AppHelper.callAfter(schedule_owner_entry)
                entered = macos_owner_rules(document.display_path, OwnerRules(include=["slg"], exclude=["*david*"]))
                assert entered == (OwnerRules(include=["*simson*", "slg"], exclude=["*david*"]) if accept else None)
                assert not (document.path / "owner-names.txt").exists(), "Entry alone must not save"
            assert application.open_document_options(api.document)
            options = next(window for window in webview.windows if " — Document Options — " in window.title)
            assert options.events.loaded.wait(10), "Options bridge failed to load"
            evaluate_async(options, "refreshOptions().then(() => true)")
            options.evaluate_js("document.getElementById('owner-include').value = '*Simson*, SLG'; document.getElementById('owner-exclude').value = '*David*'")
            evaluate_async(options, "saveOptions().then(() => true)")
            assert options.evaluate_js("document.getElementById('owner-include').value") == "*simson*\nslg"
            assert options.evaluate_js("document.getElementById('owner-exclude').value") == "*david*"
            store = DocumentOptions(document.path)
            owner_lease = WriterLease.acquire(document.path, document.descriptor.identity, "test", "options", "test")
            try:
                store.record_import(store.defaults(), owner_lease)
            finally:
                owner_lease.release()
            options.evaluate_js("document.getElementById('owner-include').value = 'slg'")
            evaluate_async(options, "saveOptions().then(() => true)")
            assert not options.evaluate_js("document.getElementById('changed').hidden")
            assert store.state().include == ["slg"]
            assert store.state().exclude == ["*david*"]
            options.destroy()
            assert options.events.closed.wait(5)
            assert application.open_document_options(api.document)
            options = next(window for window in webview.windows if " — Document Options — " in window.title)
            assert options.events.loaded.wait(10)
            evaluate_async(options, "refreshOptions().then(() => true)")
            assert not options.evaluate_js("document.getElementById('changed').hidden")
            assert application.open_ingest_window(document)
            ingest = next(window for window in webview.windows if " — Ingests — " in window.title)
            assert ingest.events.loaded.wait(10), "Ingest bridge failed to load"
            evaluate_async(ingest, "refreshHistory().then(() => true)")
            assert ingest.evaluate_js("document.getElementById('import-directory').textContent") == "Import Directory…"
            assert not ingest.evaluate_js("document.getElementById('import-directory').disabled")
            document_id = document.descriptor.document_id
            lease = WriterLease.acquire(document.path, document.descriptor.identity, "test", "button-test", "test")
            controller.begin_ingest(document_id, IngestJob(operation_id="button-test", owner_window_id=api.search_window.window_id), lease)
            try:
                evaluate_async(options, "refreshOptions().then(() => true)")
                assert options.evaluate_js("document.getElementById('save').disabled")
                evaluate_async(ingest, "refreshHistory().then(() => true)")
                assert ingest.evaluate_js("document.getElementById('import-directory').disabled")
                assert not evaluate_async(ingest, "window.pywebview.api.import_directory()")
            finally:
                controller.finish_ingest(document_id, "button-test", published=False)
            evaluate_async(ingest, "refreshHistory().then(() => true)")
            assert not ingest.evaluate_js("document.getElementById('import-directory').disabled")
        except Exception as error:  # pylint: disable=broad-exception-caught
            failures.append(str(error))
        finally:
            application.stop_imports_for_quit()
            for window in list(webview.windows):
                if window is not about:
                    window.destroy()
                    window.events.closed.wait(3)
            about.destroy()

    try:
        webview.start(probe, private_mode=True, http_server=False, menu=application.menu())
    finally:
        application.shutdown()
    assert not failures, failures
    print("Production About, document bridge, and menus passed")


if __name__ == "__main__":
    main()
