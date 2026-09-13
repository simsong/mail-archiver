"""Exercise the real three-step setup, Cocoa panels, and import worker on disposable mail."""

from collections.abc import Callable
import faulthandler
import hashlib
from importlib import import_module
import os
from pathlib import Path
import sqlite3
from threading import Event, Thread
import time
import traceback

import webview

from mailarchiver.application import ApplicationController, ApplicationPreferencesStore, IngestJob
from mailarchiver.gui_app import GUI_DIRECTORY, PyWebViewApplication, configure_macos_application, macos_import_picker
from mailarchiver.document_options import DocumentOptions
from mailarchiver.ingest_status import read_ingest_history
from mailarchiver.loopback import LoopbackAssetServer
from mailarchiver.scanner import scanner_availability
from mailarchiver.writer_lock import WriterLease

from e2e_tests.native_application_probe import evaluate_async


class ObservedSetupApplication(PyWebViewApplication):
    """Inspect real native menu state at both Cancel lock transitions."""

    def __init__(self, controller: ApplicationController, server: LoopbackAssetServer) -> None:
        super().__init__(controller, server)
        self.before_quit: Callable[[], None] | None = None
        self.observe_cancel = False
        self.cancel_menu_states: list[bool] = []
        self.cancel_menu_errors: list[str] = []

    def prepare_quit(self) -> bool:
        # Place a real competing job just before the atomic quit decision.
        before, self.before_quit = self.before_quit, None
        if before is not None:
            before()
        return super().prepare_quit()

    def _refresh_menus(self) -> None:
        super()._refresh_menus()
        if not self.observe_cancel or self._setup_api is None:
            return
        # Keep each transition observable until its real queued refresh completes.
        locked = self._setup_api._lock.locked()
        inspected = Event()

        def inspect() -> None:
            try:
                native = import_module("AppKit").NSApplication.sharedApplication()
                close = native.mainMenu().itemWithTitle_("File").submenu().itemWithTitle_("Close")
                self.cancel_menu_states.append(bool(close.isEnabled()))
                assert close.isEnabled() != locked, "Cancel menu state did not follow its lock"
                if locked:
                    assert not self.close_active_window(), "Close accepted while Cancel held the setup lock"
            except Exception:  # pylint: disable=broad-exception-caught
                self.cancel_menu_errors.append(traceback.format_exc())
            finally:
                inspected.set()
        import_module("PyObjCTools.AppHelper").callAfter(inspect)
        assert inspected.wait(5), "Cancel menu inspection timed out"


def main() -> None:
    """Drive accepted/cancelled native selections and import through production JS handlers."""
    faulthandler.dump_traceback_later(90)
    appkit = import_module("AppKit")
    foundation = import_module("Foundation")
    app_helper = import_module("PyObjCTools.AppHelper")
    fixture = Path(os.environ["MAILARCHIVER_SETUP_FIXTURE"])
    action = os.environ.get("MAILARCHIVER_SETUP_ACTION", "import")
    cancel_only = action.startswith("cancel")
    quit_race = action == "cancel-race"
    source = fixture / "Mail source ü"
    source.mkdir()
    alternate = fixture / "Other source"
    alternate.mkdir()
    destination = fixture / "New archive.mailarchive"
    destination.mkdir()
    raw = b"From: owner@example.org\nDate: Thu, 10 Sep 2026 12:00:00 +0000\nSubject: Setup fixture\n\nPreserve me.\n"
    message = source / "message.eml"
    message.write_bytes(raw)
    (source / "owner-names.txt").write_text("owner@example.org\n", encoding="utf-8")
    controller = ApplicationController(ApplicationPreferencesStore(fixture / "preferences.json"))
    server = LoopbackAssetServer(GUI_DIRECTORY)
    configure_macos_application()
    application = ObservedSetupApplication(controller, server)
    application.create_about_window(hidden=True)
    application.show_setup()
    setup = webview.windows[-1]
    errors: list[str] = []
    race_job: IngestJob | None = None
    race_worker: Thread | None = None
    quit_confirmed = Event()
    preferences_before_cancel: bytes | None = None

    def schedule_panel(directory: Path | None, *, destination_picker: bool = False, accept: bool = True, warning: str | None = None) -> None:
        # Seed only the native browser's initial location with a disposable folder.
        # Accepted results still pass through NSOpenPanel and the real JS bridge.
        api = application._setup_api  # pylint: disable=protected-access
        assert api is not None
        if directory is not None:
            if destination_picker:
                api._destination = directory  # pylint: disable=protected-access
            else:
                api._source = directory  # pylint: disable=protected-access

        def inspect(timer) -> None:
            timer.invalidate()
            native = appkit.NSApplication.sharedApplication()
            panel = native.modalWindow()
            try:
                assert panel is not None
                accessory = panel.accessoryView()
                if warning is None:
                    assert accessory is None, "Folder picker retained the previous warning"
                    close = native.mainMenu().itemWithTitle_("File").submenu().itemWithTitle_("Close")
                    assert not close.isEnabled(), "Close remained enabled during setup selection"
                    assert not api.cancel(), "Cancel accepted while the picker held the setup lock"
                else:
                    assert accessory is not None and accessory.stringValue() == warning
                assert panel.canChooseDirectories() and not panel.canChooseFiles()
                assert not panel.allowsMultipleSelection()
                assert panel.treatsFilePackagesAsDirectories()
                assert not panel.canCreateDirectories(), "Setup picker can mutate a browsed source tree"
                if directory is not None:
                    assert Path(panel.directoryURL().path()).samefile(directory)
                native.stopModalWithCode_(1 if accept else 0)
            except Exception as error:  # pylint: disable=broad-exception-caught
                errors.append(str(error))
                native.stopModalWithCode_(0)

        def schedule() -> None:
            timer = foundation.NSTimer.timerWithTimeInterval_repeats_block_(0.3, False, inspect)
            foundation.NSRunLoop.mainRunLoop().addTimer_forMode_(timer, appkit.NSModalPanelRunLoopMode)
        app_helper.callAfter(schedule)

    def check_close_enabled() -> None:
        inspected = Event()

        def inspect() -> None:
            try:
                native = appkit.NSApplication.sharedApplication()
                assert native.mainMenu().itemWithTitle_("File").submenu().itemWithTitle_("Close").isEnabled()
            except Exception:  # pylint: disable=broad-exception-caught
                errors.append(traceback.format_exc())
            finally:
                inspected.set()
        application._refresh_menus()  # pylint: disable=protected-access
        app_helper.callAfter(inspect)
        assert inspected.wait(5)
        assert not errors, errors

    def click(identifier: str) -> None:
        setup.evaluate_js(f"document.getElementById('{identifier}').click()")
        evaluate_async(setup, "new Promise(resolve => {const t = setInterval(() => {if (!busy) {clearInterval(t); resolve(true);}}, 25);})")

    def probe() -> None:
        nonlocal race_job, race_worker, preferences_before_cancel
        try:
            assert setup.events.loaded.wait(10)
            assert setup.evaluate_js("Object.keys(window.pywebview.api).sort()") == ["cancel", "choose_destination", "choose_source", "start_import"]
            evaluate_async(setup, "initialize(); Promise.resolve(true)")
            assert setup.evaluate_js("document.getElementById('start-import').disabled")
            anchor = webview.windows[0].native
            assert anchor is not None and not anchor.isVisible()
            application.show_setup()
            assert len(webview.windows) == 2, "Repeated setup duplicated the window"
            check_close_enabled()
            # Creation stays disabled even before any source selection is known.
            schedule_panel(source, destination_picker=True, accept=False)
            click("choose-destination")
            warning = "Fixture: previously unscanned import"
            schedule_panel(source, accept=False, warning=warning)
            assert not macos_import_picker(source, "Fixture", "Fixture", "Select", folders=True, files=False, warning=warning)
            for selected in (alternate, source):
                schedule_panel(selected)
                click("choose-source")
                check_close_enabled()
                actual = setup.evaluate_js("document.getElementById('source-path').value")
                assert Path(actual).samefile(selected), (actual, str(selected))
            schedule_panel(None, accept=False)
            click("choose-source")
            assert setup.evaluate_js("document.getElementById('source-path').value") == actual
            assert not controller.preferences_store.path.exists()
            schedule_panel(source, destination_picker=True)
            click("choose-destination")
            click("start-import")
            check_close_enabled()
            assert "separate" in setup.evaluate_js("document.getElementById('error').textContent")
            assert not controller.preferences_store.path.exists()
            schedule_panel(destination, destination_picker=True)
            click("choose-destination")
            schedule_panel(None, destination_picker=True, accept=False)
            click("choose-destination")
            assert Path(setup.evaluate_js("document.getElementById('destination-path').value")).samefile(destination)
            assert not list(destination.iterdir()), "Selecting a destination initialized it"

            if cancel_only:
                if quit_race:
                    document = controller.create_document(fixture / "Other archive.mailarchive")
                    session = controller.new_search_window(document)
                    search = application.create_search_window(session)
                    assert search.window.events.loaded.wait(10)
                    application.show_setup()
                    preferences_before_cancel = controller.preferences_store.path.read_bytes()
                    assert document.path is not None
                    job = IngestJob(operation_id="quit-race", owner_window_id=session.window_id)
                    race_job = job
                    lease = WriterLease.acquire(document.path, document.descriptor.identity, "test", job.operation_id, "test")
                    registered = Event()

                    def worker() -> None:
                        try:
                            with application._lock:  # pylint: disable=protected-access
                                controller.begin_ingest(document.descriptor.document_id, job, lease)
                            registered.set()
                            assert job.stop.wait(15), "Quit never stopped the competing import"
                            assert lease.acquired, "Writer lease released before stop/checkpoint"
                        except Exception:  # pylint: disable=broad-exception-caught
                            errors.append(traceback.format_exc())
                        finally:
                            if document.ingest_job is job:
                                controller.finish_ingest(document.descriptor.document_id, job.operation_id, published=False)
                            else:
                                lease.release()
                            job.finished.set()

                    race_worker = Thread(target=worker, name="setup-quit-race")

                    def publish_job() -> None:
                        assert race_worker is not None
                        race_worker.start()
                        assert registered.wait(5), "Competing job did not register"
                    application.before_quit = publish_job

                    def confirm_quit(timer) -> None:
                        native = appkit.NSApplication.sharedApplication()
                        if native.modalWindow() is not None:
                            timer.invalidate()
                            quit_confirmed.set()
                            native.stopModalWithCode_(1001)

                    def schedule_quit_confirmation() -> None:
                        timer = foundation.NSTimer.timerWithTimeInterval_repeats_block_(0.1, True, confirm_quit)
                        foundation.NSRunLoop.mainRunLoop().addTimer_forMode_(timer, appkit.NSModalPanelRunLoopMode)
                    app_helper.callAfter(schedule_quit_confirmation)
                application.observe_cancel = not quit_race
                windows_before_cancel = tuple(webview.windows)
                setup.evaluate_js("document.getElementById('cancel').click()")
                for window in windows_before_cancel:
                    assert window.events.closed.wait(10), f"Cancel did not close {window.title}"
                if not quit_race:
                    assert application.cancel_menu_states == [False, True], application.cancel_menu_states
                    assert not application.cancel_menu_errors, application.cancel_menu_errors
                application.observe_cancel = False
                if quit_race:
                    assert quit_confirmed.is_set(), "Cancel silently abandoned quit after a concurrent import"
                    assert race_job is not None and race_job.stop.is_set() and race_job.finished.is_set()
                return

            owners_confirmed = Event()
            confirmed = Event()

            def confirm(timer) -> None:
                native = appkit.NSApplication.sharedApplication()
                modal = native.modalWindow()
                if modal is None:
                    return
                try:
                    if "Owner emails for" in modal.title():
                        assert not owners_confirmed.is_set(), "Owner dialog repeated"
                        views = [modal.contentView()]
                        editors = []
                        while views:
                            view = views.pop()
                            views.extend(view.subviews())
                            if isinstance(view, appkit.NSTextView) and view.isEditable():
                                editors.append(view)
                        assert len(editors) == 2
                        for editor in editors:
                            if editor.identifier() == "owner-include":
                                assert editor.string() == "owner@example.org"
                            elif editor.identifier() == "owner-exclude":
                                editor.setString_("excluded@example.org")
                            else:
                                raise AssertionError("Unexpected owner editor")
                        owners_confirmed.set()
                        native.stopModalWithCode_(1000)
                    else:
                        assert owners_confirmed.is_set(), "Import skipped owner rule confirmation"
                        timer.invalidate()
                        native.stopModalWithCode_(1000 if scanner_availability().configured else 1001)
                        confirmed.set()
                except Exception:  # pylint: disable=broad-exception-caught
                    errors.append(traceback.format_exc())
                    timer.invalidate()
                    native.stopModalWithCode_(1001)
                    confirmed.set()

            def schedule_confirmation() -> None:
                timer = foundation.NSTimer.timerWithTimeInterval_repeats_block_(0.2, True, confirm)
                foundation.NSRunLoop.mainRunLoop().addTimer_forMode_(timer, appkit.NSModalPanelRunLoopMode)
            app_helper.callAfter(schedule_confirmation)
            click("start-import")
            assert confirmed.wait(10)
            assert not errors, errors
            native_setup = setup.native
            assert native_setup is not None and not native_setup.isVisible()
            assert setup.evaluate_js("document.getElementById('source-path').value") == ""
            assert setup.evaluate_js("document.getElementById('destination-path').value") == ""
            ingest = next(window for window in webview.windows if " — Ingests — " in window.title)
            assert ingest.events.loaded.wait(10)
            deadline = time.monotonic() + 70
            while any(doc.ingest_job for doc in controller.documents()) and time.monotonic() < deadline:
                time.sleep(0.1)
            history = read_ingest_history(destination)
            assert history.statuses[0].state == "completed", history
            with sqlite3.connect(destination / "archive.sqlite3") as catalog:
                assert catalog.execute("SELECT sha256 FROM messages").fetchall() == [(hashlib.sha256(raw).hexdigest(),)]
            saved_rules = DocumentOptions(destination).state()
            assert saved_rules.include == ["owner@example.org"]
            assert saved_rules.exclude == ["excluded@example.org"]
            assert message.read_bytes() == raw
            saved = controller.preferences.last_archive
            assert saved is not None and saved.samefile(destination)
            assert not errors, errors
        except Exception:  # pylint: disable=broad-exception-caught
            errors.append(traceback.format_exc())
        finally:
            if race_job is not None:
                race_job.stop.set()
            if race_worker is not None and race_worker.ident is not None:
                race_worker.join(10)
                assert not race_worker.is_alive()
            application.shutdown()
            for window in tuple(webview.windows):
                window.destroy()

    webview.start(probe, http_server=False, private_mode=True, menu=application.menu())
    assert not errors, errors
    if cancel_only:
        assert not list(destination.iterdir())
        if preferences_before_cancel is None:
            assert not controller.preferences_store.path.exists()
        else:
            assert controller.preferences_store.path.read_bytes() == preferences_before_cancel
    assert sorted(path.name for path in source.iterdir()) == ["message.eml", "owner-names.txt"]
    assert message.read_bytes() == raw
    faulthandler.cancel_dump_traceback_later()
    print("Native setup import passed")


if __name__ == "__main__":
    main()
