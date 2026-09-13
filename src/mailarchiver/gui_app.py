"""Expose the read-only archive services through a macOS-first pywebview shell."""

from __future__ import annotations


import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from importlib import import_module
from importlib.metadata import version
from pathlib import Path
from threading import Event, Lock, RLock, Thread, current_thread
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

import webview
from pydantic import BaseModel, Field
from webview.menu import Menu, MenuAction, MenuSeparator

from .file_drag import FILE_DRAGS, install_file_drag
from .identity import APPLICATION_NAME
from .__main__ import IngestInterrupted, IngestOutcome, IngestRequest, run_ingest
from .application import (
    ApplicationController,
    ArchiveDocument,
    IngestJob,
    SearchWindow,
    SetupSelection,
)
from .scanner import CLAMAV_DOWNLOAD_URL, UNSCANNED_WARNING, ScannerAvailability, scanner_availability
from .configuration import GuiConfiguration, application_configuration
from .document_options import DocumentOptions, OwnerRulesState
from .owner_rules import OwnerRules
from .archive_config import (
    import_directory as configured_import_directory,
    remember_import_directory,
)
from .gui_service import (
    MessagePreview,
    MessageView,
    PreviewBatch,
    attachment_content,
    attachment_descriptor,
    describe_message,
    export_filename,
    is_risky,
    message_locations,
    message_previews,
    render_part,
    safe_filename,
    search_count,
    search_page,
    search_suggestions,
    searchable_message_count,
    write_attachment,
    write_message,
    write_messages_zip,
)
from .ingest_status import (
    IngestHistory,
    IngestStatus,
    latest_ingest_status,
    read_ingest_history,
)
from .loopback import LoopbackAssetServer
from .mailbox_tree import FilterSet, FilterSetStore, MailboxSelection, mailbox_tree
from .writer_lock import ArchiveBusyError, WriterLease

GUI_DIRECTORY = (Path(getattr(sys, "_MEIPASS")) if getattr(sys, "frozen", False) else Path(__file__).parents[2]) / "gui"
E2E_DRIVER = Path(__file__).parents[2] / "e2e_tests" / "gui_driver.js"
DEFAULT_PAGE_SIZE = 100
APPLICATION_ICON = GUI_DIRECTORY / "icons" / "rainbow-post-192.png"
EXTERNAL_LINK_SCHEMES = frozenset({"http", "https", "mailto"})
INTERNET_CHECK_URL = "https://www.example.com/"
INTERNET_CHECK_INTERVAL_SECONDS = 30.0


def owner_rules_text(include: str, exclude: str) -> OwnerRules:
    rules = OwnerRules.from_text(include, exclude)
    if not rules.include:
        raise ValueError("Enter at least one owner mailbox name or email pattern.")
    return rules


def external_link_destination(value: str) -> str:
    """Validate a message link before it reaches the system link handler."""
    if not value or value != value.strip() or any(ord(character) < 32 for character in value):
        raise ValueError("invalid external link")
    parsed = urlsplit(value)
    scheme = parsed.scheme.casefold()
    if scheme not in EXTERNAL_LINK_SCHEMES:
        raise ValueError("unsupported external link scheme")
    if scheme in {"http", "https"} and not parsed.netloc:
        raise ValueError("web link has no host")
    if scheme == "mailto" and not parsed.path:
        raise ValueError("mail link has no recipient")
    return value


class ApplicationMetadata(BaseModel):
    name: str
    version: str
    copyright: str


class ApplicationNotice(BaseModel):
    """One startup, warning, import, or failure message retained for About."""

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    severity: Literal["information", "warning", "error"]
    message: str


class GuiStatus(BaseModel):
    archive: str | None
    ready: bool
    message_count: int = 0
    document_id: str | None = None
    untitled: bool = False
    generation: int = 0
    opened_in_new_window: bool = False
    file_drag_supported: bool = sys.platform == "darwin"
    configuration: GuiConfiguration
    notices: list[ApplicationNotice] = Field(default_factory=list)


class GuiIngestOverview(BaseModel):
    status: IngestStatus | None = None


class DragExport(BaseModel):
    filename: str
    token: str


class OpenResult(BaseModel):
    opened: bool = False
    requires_confirmation: bool = False
    filename: str


class GuiE2EClientResult(BaseModel):
    passed: bool
    checks: list[str]
    error: str | None = None


class InternetStatus(BaseModel):
    online: bool | None = None
    checked_at: datetime | None = None
    detail: str = "Checking…"


class AboutIngestStatus(BaseModel):
    archive: str
    owner_window_id: str | None = None
    operation_id: str | None = None
    status: IngestStatus | None = None


class AboutStatus(BaseModel):
    metadata: ApplicationMetadata
    disk_path: str
    disk_free_bytes: int
    internet: InternetStatus
    notices: list[ApplicationNotice]
    ingests: list[AboutIngestStatus]
    antivirus: ScannerAvailability


class ConnectivityMonitor:
    """Refresh a bounded live Internet reachability probe away from GUI threads."""

    def __init__(self, url: str = INTERNET_CHECK_URL) -> None:
        self.url = url
        self._status = InternetStatus()
        self._lock = RLock()
        self._stop = Event()
        self._thread = Thread(target=self._run, name="mailarchiver-connectivity", daemon=True)
        self._thread.start()

    def status(self) -> InternetStatus:
        with self._lock:
            return self._status.model_copy(deep=True)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                request = Request(self.url, method="HEAD")
                with urlopen(request, timeout=3) as response:  # nosec B310 - fixed HTTPS health URL
                    online = 200 <= response.status < 500
                    detail = "Connected" if online else f"HTTP {response.status}"
            except OSError as error:
                online = False
                detail = f"Offline: {error}"
            with self._lock:
                self._status = InternetStatus(
                    online=online,
                    checked_at=datetime.now(UTC),
                    detail=detail,
                )
            self._stop.wait(INTERNET_CHECK_INTERVAL_SECONDS)


class NativeSmokePhase(BaseModel):
    name: str
    elapsed_seconds: float


class NativeSmokeReport(BaseModel):
    completed: bool = False
    passed: bool = False
    error: str | None = None
    phases: list[NativeSmokePhase]


class NativeSmokeController:
    """Persist smoke-test progress and bound the Cocoa process lifetime."""

    RESULT_TIMEOUT_SECONDS = 15.0
    SHUTDOWN_TIMEOUT_SECONDS = 10.0
    HARD_TIMEOUT_SECONDS = 60.0

    def __init__(self, report_path: Path) -> None:
        self.report_path = report_path
        self.started = time.monotonic()
        self.completed = False
        self.passed = False
        self.error: str | None = None
        self.phases: list[NativeSmokePhase] = []
        self._lock = Lock()
        self._result = Event()
        self._window_ready = Event()
        self._page_loaded = Event()
        self._event_loop_stopped = Event()
        self._window: Any = None
        self.mark("process-started")

    def mark(self, name: str) -> None:
        with self._lock:
            self.phases.append(NativeSmokePhase(name=name, elapsed_seconds=time.monotonic() - self.started))
            self._write_locked()
        print(f"native-smoke: {name}", file=sys.stderr, flush=True)

    def bind_window(self, window: Any) -> None:
        self._window = window
        self._window_ready.set()
        self.mark("window-created")

    def page_loaded(self) -> None:
        """Record that Cocoa reported the hidden page as loaded."""
        self.mark("window-loaded")
        self._page_loaded.set()

    def complete(self, passed: bool, error: str | None = None) -> None:
        with self._lock:
            if self.completed:
                return
            self.completed = True
            self.passed = passed
            self.error = error
            self.phases.append(
                NativeSmokePhase(name="bridge-passed" if passed else "bridge-failed", elapsed_seconds=time.monotonic() - self.started)
            )
            self._write_locked()
            self._result.set()

    def start_watchdog(self) -> None:
        Thread(target=self._watch, name="native-smoke-watchdog", daemon=True).start()
        Thread(target=self._hard_stop, name="native-smoke-hard-stop", daemon=True).start()

    def event_loop_returned(self) -> NativeSmokeReport:
        self.mark("event-loop-returned")
        self._event_loop_stopped.set()
        if not self.completed:
            self.complete(False, "native window closed before the bridge reported a result")
        return self.report()

    def report(self) -> NativeSmokeReport:
        with self._lock:
            return self._report_locked()

    def wait_for_result(self, timeout: float) -> bool:
        """Wait until JavaScript or the watchdog records a bridge result."""
        return self._result.wait(timeout)

    def _watch(self) -> None:
        if not self._result.wait(self.RESULT_TIMEOUT_SECONDS):
            self.complete(False, f"native bridge did not finish within {self.RESULT_TIMEOUT_SECONDS:g} seconds")
        self._window_ready.wait(2)
        self._page_loaded.wait(2)
        self.mark("destroy-requested")
        if self._window is not None:
            try:
                self._window.destroy()
            except Exception as error:  # pylint: disable=broad-exception-caught
                logging.getLogger(__name__).debug("Best-effort operation failed", exc_info=True)
                self._abort(f"native window destroy failed: {error}")
        if not self._event_loop_stopped.wait(self.SHUTDOWN_TIMEOUT_SECONDS):
            self._abort(f"Cocoa event loop did not stop within {self.SHUTDOWN_TIMEOUT_SECONDS:g} seconds")

    def _hard_stop(self) -> None:
        if not self._event_loop_stopped.wait(self.HARD_TIMEOUT_SECONDS):
            self._abort(f"native smoke process exceeded its {self.HARD_TIMEOUT_SECONDS:g}-second hard limit")
            os._exit(1)  # Smoke-only process; the report is already atomically durable.

    def _abort(self, error: str) -> None:
        with self._lock:
            self.completed = True
            self.passed = False
            if self.error is None:
                self.error = error
            self.phases.append(NativeSmokePhase(name="watchdog-abort", elapsed_seconds=time.monotonic() - self.started))
            self._write_locked()
            self._result.set()

    def _report_locked(self) -> NativeSmokeReport:
        return NativeSmokeReport(
            completed=self.completed,
            passed=self.passed,
            error=self.error,
            phases=list(self.phases),
        )

    def _write_locked(self) -> None:
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.report_path.with_suffix(self.report_path.suffix + ".tmp")
        temporary.write_text(self._report_locked().model_dump_json(indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.report_path)


def application_icon_path() -> Path:
    """Return the source-controlled icon used by the native application."""
    return APPLICATION_ICON


IMPORT_CONFIRMATION_WIDTH = 560
QUIT_IMPORT_MESSAGE = (
    "Quitting will stop all active imports. To restart an import, reopen Email Collection Toolkit "
    "and use File → Import to select the same source again. Messages already archived "
    "will not be imported twice.\n\n"
    "The application will quit after the current work has stopped and the archive has been checkpointed."
)


def create_macos_alert(title: str, message: str, buttons: tuple[str, ...], *, body_width: int | None = None) -> Any:
    """Construct a native alert on the main thread without showing it."""
    if sys.platform != "darwin":
        raise RuntimeError("native dialogs require macOS")
    from AppKit import NSAlert, NSImage, NSTextField  # pylint: disable=import-outside-toplevel,no-name-in-module,import-error

    alert = NSAlert.alloc().init()
    icon = NSImage.alloc().initWithContentsOfFile_(str(application_icon_path()))
    if icon is not None:
        alert.setIcon_(icon)
    alert.setMessageText_(title)
    if body_width is None:
        alert.setInformativeText_(message)
    else:
        body = NSTextField.wrappingLabelWithString_(message)
        body.setSelectable_(True)
        size = body.cell().cellSizeForBounds_(((0, 0), (body_width, 10000)))
        body.setFrame_(((0, 0), (body_width, size.height)))
        alert.setAccessoryView_(body)
    alert.window().setTitle_(title)
    for index, label in enumerate(buttons):
        button = alert.addButtonWithTitle_(label)
        if label == "Cancel":
            button.setKeyEquivalent_("\x1b")
        elif index == 0:
            button.setKeyEquivalent_("\r")
    return alert


def macos_alert(title: str, message: str, buttons: tuple[str, ...], *, body_width: int | None = None) -> int:
    """Present an explicitly branded Cocoa alert; return the selected button index."""
    if sys.platform != "darwin":
        raise RuntimeError("native dialogs require macOS")
    from Foundation import NSThread  # pylint: disable=import-outside-toplevel,no-name-in-module,import-error
    from PyObjCTools import AppHelper  # pylint: disable=import-outside-toplevel,import-error

    result: Future[int] = Future()

    def present() -> None:
        try:
            alert = create_macos_alert(title, message, buttons, body_width=body_width)
            result.set_result(int(alert.runModal()) - 1000)
        except Exception as error:  # pylint: disable=broad-exception-caught
            result.set_exception(error)

    if NSThread.isMainThread():
        present()
    else:
        AppHelper.callAfter(present)
    return result.result()


def macos_import_picker(
    directory: Path, title: str, message: str, prompt: str, *, folders: bool = False,
    multiple: bool = False, warning: str | None = None,
    files: bool = True, create_directories: bool = False,
) -> tuple[Path, ...]:
    """Choose files, optionally allowing whole directories in the same panel."""
    if sys.platform != "darwin":
        raise RuntimeError("native dialogs require macOS")
    from AppKit import NSOpenPanel  # pylint: disable=import-outside-toplevel,no-name-in-module,import-error
    from Foundation import NSThread, NSURL  # pylint: disable=import-outside-toplevel,no-name-in-module,import-error
    from PyObjCTools import AppHelper  # pylint: disable=import-outside-toplevel,import-error

    result: Future[tuple[Path, ...]] = Future()

    def present() -> None:
        try:
            panel = NSOpenPanel.openPanel()
            panel.setTitle_(title)
            panel.setMessage_(message)
            panel.setAccessoryView_(None)
            if warning:
                appkit = import_module("AppKit")
                banner = appkit.NSTextField.wrappingLabelWithString_(warning)
                banner.setFrame_(((0, 0), (520, 64)))
                banner.setTextColor_(appkit.NSColor.systemRedColor())
                panel.setAccessoryView_(banner)
            panel.setPrompt_(prompt)
            panel.setCanChooseDirectories_(folders)
            panel.setCanChooseFiles_(files)
            panel.setTreatsFilePackagesAsDirectories_(folders and not files)
            panel.setAllowsMultipleSelection_(multiple)
            panel.setCanCreateDirectories_(create_directories)
            panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(directory)))
            selected = tuple(Path(url.path()) for url in panel.URLs()) if panel.runModal() == 1 else ()
            result.set_result(selected)
        except Exception as error:  # pylint: disable=broad-exception-caught
            result.set_exception(error)

    if NSThread.isMainThread():
        present()
    else:
        AppHelper.callAfter(present)
    return result.result()


def macos_owner_rules(destination: Path, defaults: OwnerRules) -> OwnerRules | None:
    """Always review include/exclude rules on the Cocoa main thread before import."""
    if sys.platform != "darwin":
        raise RuntimeError("native dialogs require macOS")
    from AppKit import NSAlert, NSImage, NSScrollView, NSTextView, NSTextField, NSView  # pylint: disable=import-outside-toplevel,no-name-in-module,import-error
    from Foundation import NSThread  # pylint: disable=import-outside-toplevel,no-name-in-module,import-error
    from PyObjCTools import AppHelper  # pylint: disable=import-outside-toplevel,import-error

    result: Future[OwnerRules | None] = Future()

    def present() -> None:
        try:
            alert = NSAlert.alloc().init()
            alert.setIcon_(NSImage.alloc().initWithContentsOfFile_(str(application_icon_path())))
            title = f"Owner emails for {destination.name}"
            alert.setMessageText_(title)
            alert.window().setTitle_(title)
            instructions = (
                "One rule per line, or separate rules with commas. Exclude rules win.\n"
                "slg matches slg@any-domain, not 3slg. Use * or ? for wildcards.\n"
                "A domain is matched only when the rule contains @.\n\n"
                f"Defaults are saved after confirmation to: {destination / 'config.yaml'}"
            )
            alert.setInformativeText_(instructions)
            accessory = NSView.alloc().initWithFrame_(((0, 0), (560, 290)))
            editors = []
            for label, identifier, values, y in (
                ("Owner emails (include)", "owner-include", defaults.include, 155),
                ("Exclude (applied after include)", "owner-exclude", defaults.exclude, 10),
            ):
                heading = NSTextField.labelWithString_(label)
                heading.setFrame_(((0, y + 105), (560, 24)))
                accessory.addSubview_(heading)
                editor = NSTextView.alloc().initWithFrame_(((0, 0), (550, 100)))
                editor.setIdentifier_(identifier)
                editor.setRichText_(False)
                editor.setAutomaticQuoteSubstitutionEnabled_(False)
                editor.setAutomaticDashSubstitutionEnabled_(False)
                editor.setString_("\n".join(values))
                scroll = NSScrollView.alloc().initWithFrame_(((0, y), (560, 100)))
                scroll.setHasVerticalScroller_(True)
                scroll.setDocumentView_(editor)
                accessory.addSubview_(scroll)
                editors.append(editor)
            alert.setAccessoryView_(accessory)
            alert.addButtonWithTitle_("Continue").setKeyEquivalent_("")
            alert.addButtonWithTitle_("Cancel").setKeyEquivalent_("\x1b")
            alert.window().setInitialFirstResponder_(editors[0])
            while alert.runModal() == 1000:
                try:
                    result.set_result(owner_rules_text(str(editors[0].string()), str(editors[1].string())))
                    return
                except ValueError as error:
                    alert.setInformativeText_(f"{error}\n\n{instructions}")
            result.set_result(None)
        except Exception as error:  # pylint: disable=broad-exception-caught
            result.set_exception(error)

    if NSThread.isMainThread():
        present()
    else:
        AppHelper.callAfter(present)
    return result.result()


class IngestWindowApi:
    """History and explicit import actions bound to one archive."""

    def __init__(
        self, archive: Path | None, *, application: "PyWebViewApplication | None" = None,
        document: ArchiveDocument | None = None,
    ) -> None:
        self.archive = archive
        self.window: Any = None
        self._application = application
        self._document = document

    def set_window(self, window: Any) -> None:
        self.window = window

    def history(self) -> dict[str, Any]:
        if self.archive is None:
            return IngestHistory(statuses=[], errors=[]).model_dump(mode="json")
        return read_ingest_history(self.archive).model_dump(mode="json")

    def antivirus(self) -> dict[str, object]:
        return scanner_availability().model_dump(mode="json")

    def install_antivirus(self) -> bool:
        """User action opens the official download page; never silently installs software."""
        import webbrowser  # pylint: disable=import-outside-toplevel
        return webbrowser.open(CLAMAV_DOWNLOAD_URL)

    def close(self, *_args: object) -> None:
        self.window = None

    def can_import_directory(self) -> bool:
        return bool(
            self._application and self._document and self._document.path
            and self._document.ingest_job is None and self.window is not None
        )

    def import_directory(self) -> bool:
        if not self.can_import_directory():
            return False
        assert self._application is not None and self._document is not None
        return self._application.import_directory(self._document, self.window)


class AboutApi:
    """Read-only bridge for persistent application health and activity."""

    def __init__(self, application: "PyWebViewApplication") -> None:
        self._application = application

    def status(self) -> dict[str, Any]:
        return self._application.about_status().model_dump(mode="json")


class DocumentOptionsApi:
    """Explicit options bridge permanently bound to one document."""

    def __init__(self, document: ArchiveDocument) -> None:
        if document.path is None:
            raise ValueError("Document options require a saved archive")
        self._document = document
        self._archive = document.path
        self.window: Any = None

    def status(self) -> dict[str, Any]:
        state = DocumentOptions(self._archive).state()
        state.editable = self._document.ingest_job is None
        return state.model_dump(mode="json")

    def update(self, include: str, exclude: str, revision: str) -> dict[str, Any]:
        document = self._document
        store = DocumentOptions(self._archive)
        lease = WriterLease.acquire(
            self._archive, document.descriptor.identity, "Document options", uuid4().hex,
            application_metadata().version,
        )
        try:
            return store.save(OwnerRules.from_text(include, exclude), lease, revision).model_dump(mode="json")
        finally:
            lease.release()


class OwnerRulesPrompt:
    """Portable two-field import prompt; confirmation alone does not save configuration."""

    def __init__(self, defaults: OwnerRules) -> None:
        self.defaults = defaults
        self.result: Future[OwnerRules | None] = Future()
        self.window: Any = None

    def status(self) -> dict[str, Any]:
        return OwnerRulesState(
            include=self.defaults.include, exclude=self.defaults.exclude,
            revision="", changed_since_import=False, import_rules_known=False,
        ).model_dump(mode="json")

    def update(self, include: str, exclude: str, _revision: str) -> dict[str, Any]:
        rules = owner_rules_text(include, exclude)
        if not self.result.done():
            self.result.set_result(rules)
        self.cancel()
        return self.status()

    def cancel(self, *_args: object) -> None:
        if not self.result.done():
            self.result.set_result(None)
        window, self.window = self.window, None
        if window is not None:
            window.destroy()


class WindowBridge:
    """Expose only the selected methods, never traverse application/native object state."""

    def __init__(self, api: Any, methods: tuple[str, ...]) -> None:
        self._api = api
        self._methods = methods

    def __dir__(self) -> list[str]:
        return list(self._methods)

    def __getattr__(self, name: str) -> Any:
        if name not in self._methods:
            raise AttributeError(name)
        return getattr(self._api, name)


SEARCH_BRIDGE_METHODS = (
    "status", "activate", "search", "suggestions", "ingest_overview", "open_ingest_window",
    "mailbox_tree", "saved_filter_sets", "save_filter_set", "rename_filter_set", "delete_filter_set",
    "open_message_window", "request_previews", "take_previews", "message", "part", "attachment",
    "copy_source_path", "copy_visible_text", "copy_link", "open_link", "open_attachment",
    "save_message", "save_attachment", "prepare_drag",
)


class GuiApi:
    """Narrow API exposed to one webview window."""

    def __init__(
        self,
        archive: Path | None,
        temporary_directory: Path | None = None,
        e2e_directory: Path | None = None,
        preferences_file: Path | None = None,
        *,
        application: PyWebViewApplication | None = None,
        document: ArchiveDocument | None = None,
        search_window: SearchWindow | None = None,
    ) -> None:
        self.application = application
        self.document = document
        self.search_window = search_window
        self.archive = document.path if document is not None else archive
        self.window: Any = None
        self._drag_tokens: set[str] = set()
        self._drag_lock = Lock()
        self._drag_closed = False
        self._temporary = tempfile.TemporaryDirectory(prefix="mailarchive-gui-") if temporary_directory is None else None
        if self._temporary is not None:
            self.temporary_directory: Path = Path(self._temporary.name)
        else:
            assert temporary_directory is not None
            self.temporary_directory = temporary_directory
        self.e2e_directory = e2e_directory
        self.filter_sets = FilterSetStore(preferences_file)
        if self.temporary_directory is not None:
            self.temporary_directory.mkdir(parents=True, exist_ok=True)
        self.children: list[GuiApi] = []
        self._preview_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mail-preview")
        self._preview_lock = Lock()
        self._preview_cache: dict[int, MessagePreview] = {}
        self._preview_pending: set[int] = set()
        self._preview_error: str | None = None
        self._preview_generation = 0
        self._tree_cache: dict[bool, list[dict[str, Any]]] = {}
        self._message_count: int | None = None
        self._ingest_window_lock = Lock()
        self._ingest_window_api: IngestWindowApi | None = None

    def set_window(self, window: Any) -> None:
        self.window = window

    def invalidate_archive(self) -> None:
        """Discard derived view caches after a writer may have published."""
        with self._preview_lock:
            self._preview_generation += 1
            self._preview_cache.clear()
            self._preview_pending.clear()
            self._preview_error = None
        self._tree_cache.clear()
        self._message_count = None

    # JSON bridge results come from validated Pydantic models; values can be
    # nested objects, arrays, or scalars at this external JavaScript boundary.
    def status(self, *, opened_in_new_window: bool = False) -> dict[str, Any]:
        ready = self.archive is not None and _is_archive(self.archive)
        if ready and self._message_count is None:
            self._message_count = searchable_message_count(self._archive())
        return GuiStatus(
            archive=(
                str(self.document.display_path)
                if self.document and self.document.display_path
                else str(self.archive)
                if self.archive
                else None
            ),
            ready=ready,
            message_count=self._message_count or 0,
            document_id=self.document.descriptor.document_id if self.document else None,
            untitled=self.document.descriptor.untitled if self.document else self.archive is None,
            generation=self.document.generation if self.document else 0,
            opened_in_new_window=opened_in_new_window,
            configuration=application_configuration().gui,
            notices=self.application.notices() if self.application else [],
        ).model_dump(mode="json")

    def activate(self) -> bool:
        """Route subsequent native actions through this window's current document."""
        if self.application is not None and self.search_window is not None:
            self.application.activate_window(self.search_window.window_id)
        return True

    def ingest_overview(self) -> dict[str, Any]:
        status = latest_ingest_status(self._archive()) if self.archive and _is_archive(self.archive) else None
        return GuiIngestOverview(status=status).model_dump(mode="json")

    def open_ingest_window(self, status_id: str | None = None) -> bool:
        """Open or focus the ingest browser shared by this archive document."""
        if self.e2e_directory is not None:
            return True
        if self.application is not None and self.document is not None:
            return self.application.open_ingest_window(self.document, status_id)
        with self._ingest_window_lock:
            api = self._ingest_window_api
            if api is not None and api.window is not None:
                api.archive = self.archive
                if status_id is not None:
                    api.window.run_js(f"window.selectIngest({json.dumps(status_id)});")
                api.window.restore()
                api.window.show()
                return True
            api = IngestWindowApi(self.archive)
            selected = "" if status_id is None else f"?status={status_id}"
            window = webview.create_window(
                "Email Collection Toolkit — Ingests",
                str(GUI_DIRECTORY / f"ingests.html{selected}"),
                js_api=WindowBridge(api, ("history", "can_import_directory", "import_directory", "antivirus", "install_antivirus")),
                width=1050,
                height=700,
                min_size=(720, 440),
                text_select=True,
            )
            if window is None:
                raise RuntimeError("pywebview failed to create a window")
            api.set_window(window)
            self._ingest_window_api = api

            def closed(*_args: object) -> None:
                api.close()
                with self._ingest_window_lock:
                    if self._ingest_window_api is api:
                        self._ingest_window_api = None

            window.events.closed += closed
        return True

    def choose_archive(self) -> dict[str, Any]:
        if self.e2e_directory is not None:
            return self.status()
        if self.application is not None:
            opened = self.application.open_archive_dialog()
            return self.status(opened_in_new_window=opened)
        selected = self.window.create_file_dialog(webview.FileDialog.FOLDER, directory=str(Path.home()))
        if selected:
            archive = Path(selected[0])
            if not _is_archive(archive):
                raise ValueError(f"{archive} must contain archive.sqlite3 and search.sqlite3")
            self.archive = archive
            with self._preview_lock:
                self._preview_generation += 1
                self._preview_cache.clear()
                self._preview_pending.clear()
                self._preview_error = None
            self._tree_cache.clear()
            self._message_count = None
            if self._ingest_window_api is not None:
                self._ingest_window_api.archive = archive
                if self._ingest_window_api.window is not None:
                    self._ingest_window_api.window.run_js("window.refreshHistory();")
        status = self.status()
        self.window.set_title(_window_title(self.archive, int(status["message_count"])))
        return status

    def search(
        self,
        query: str,
        offset: int = 0,
        sort_by: str = "date",
        direction: str = "descending",
        search_attachments: bool = False,
        mailbox_selections: list[str] | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> dict[str, Any]:
        page = search_page(
            self._archive(), query, offset, limit, sort_by, direction,
            search_attachments, mailbox_selections,
        )
        if page.error is not None:
            return page.model_dump(mode="json")
        if self.search_window is not None:
            self.search_window.query = query
            if sort_by not in ("date", "subject", "sender") or direction not in ("ascending", "descending"):
                raise ValueError("invalid search ordering")
            self.search_window.sort_by = sort_by
            self.search_window.sort_direction = direction
            self.search_window.mailbox_selections = list(mailbox_selections or ())
        return page.model_dump(mode="json")

    def search_count(
        self,
        query: str,
        search_attachments: bool = False,
        mailbox_selections: list[str] | None = None,
    ) -> dict[str, Any]:
        return search_count(
            self._archive(), query, search_attachments, mailbox_selections
        ).model_dump(mode="json")

    def suggestions(self, query: str, limit: int = 20) -> dict[str, Any]:
        return search_suggestions(self._archive(), query, limit).model_dump(mode="json")

    def mailbox_tree(self, show_volumes: bool = False) -> list[dict[str, Any]]:
        if show_volumes not in self._tree_cache:
            self._tree_cache[show_volumes] = [
                node.model_dump(mode="json") for node in mailbox_tree(self._archive(), show_volumes)
            ]
        return self._tree_cache[show_volumes]

    def saved_filter_sets(self) -> dict[str, Any]:
        return self.filter_sets.read().model_dump(mode="json")

    def save_filter_set(self, name: str, show_volumes: bool, selections: list[str]) -> dict[str, Any]:
        for token in selections:
            MailboxSelection.from_token(token)
        return self.filter_sets.save(
            FilterSet(name=name, show_volumes=show_volumes, selections=selections)
        ).model_dump(mode="json")

    def rename_filter_set(self, old_name: str, new_name: str) -> dict[str, Any]:
        return self.filter_sets.rename(old_name, new_name).model_dump(mode="json")

    def delete_filter_set(self, name: str) -> dict[str, Any]:
        return self.filter_sets.delete(name).model_dump(mode="json")

    def request_previews(self, message_pks: list[int]) -> bool:
        if not message_pks or len(message_pks) > DEFAULT_PAGE_SIZE:
            raise ValueError(f"request between 1 and {DEFAULT_PAGE_SIZE} message previews")
        with self._preview_lock:
            requested = set(message_pks)
            missing = requested - self._preview_cache.keys() - self._preview_pending
            self._preview_pending.update(missing)
            self._preview_error = None
        if missing:
            archive = self._archive()
            generation = self._preview_generation
            self._preview_executor.submit(self._load_previews, archive, sorted(missing), generation)
        return True

    def take_previews(self, message_pks: list[int]) -> dict[str, Any]:
        requested = set(message_pks)
        with self._preview_lock:
            previews = [self._preview_cache[message_pk] for message_pk in message_pks if message_pk in self._preview_cache]
            batch = PreviewBatch(
                previews=previews,
                pending=bool(requested & self._preview_pending),
                error=self._preview_error,
            )
        return batch.model_dump(mode="json")

    def _load_previews(self, archive: Path, message_pks: list[int], generation: int) -> None:
        try:
            previews = message_previews(archive, message_pks)
            with self._preview_lock:
                if generation == self._preview_generation and archive == self.archive:
                    self._preview_cache.update((preview.message_pk, preview) for preview in previews)
        except Exception as error:  # pylint: disable=broad-exception-caught
            logging.getLogger(__name__).debug("Best-effort operation failed", exc_info=True)
            with self._preview_lock:
                if generation == self._preview_generation:
                    self._preview_error = f"{type(error).__name__}: {error}"
        finally:
            with self._preview_lock:
                if generation == self._preview_generation:
                    self._preview_pending.difference_update(message_pks)

    def message(self, message_pk: int) -> dict[str, Any]:
        message = describe_message(self._archive(), message_pk)
        if self.search_window is not None:
            self.search_window.selected_message = message_pk
        return message.model_dump(mode="json")

    def copy_source_path(self, message_pk: int, source_location_index: int) -> str:
        """Copy one local source pathname as text and a macOS file URL."""
        _archive_path, locations = message_locations(self._archive(), message_pk)
        try:
            path = locations[source_location_index].copy_path
        except IndexError as error:
            raise ValueError("unknown source location") from error
        if path is None:
            raise ValueError("source location has no local filesystem path")
        if sys.platform != "darwin":
            raise ValueError("copying source paths requires macOS with PyObjC installed")
        try:
            import AppKit  # pylint: disable=import-error,import-outside-toplevel
            from Foundation import (
                NSURL,  # pylint: disable=import-error,import-outside-toplevel,no-name-in-module
            )
        except ImportError as error:
            raise ValueError("copying source paths requires macOS with PyObjC installed") from error

        pasteboard = AppKit.NSPasteboard.generalPasteboard()
        pasteboard.clearContents()
        pasteboard.setString_forType_(path, AppKit.NSPasteboardTypeString)
        pasteboard.setString_forType_(
            NSURL.fileURLWithPath_(path).absoluteString(), AppKit.NSPasteboardTypeFileURL
        )
        pasteboard.setPropertyList_forType_([path], AppKit.NSFilenamesPboardType)
        return path

    def copy_visible_text(self, text: str) -> str:
        """Copy the user-visible message text to the macOS pasteboard."""
        if sys.platform != "darwin":
            raise ValueError("copying visible text requires macOS with PyObjC installed")
        try:
            import AppKit  # pylint: disable=import-error,import-outside-toplevel
        except ImportError as error:
            raise ValueError("copying visible text requires macOS with PyObjC installed") from error

        pasteboard = AppKit.NSPasteboard.generalPasteboard()
        pasteboard.clearContents()
        pasteboard.setString_forType_(text, AppKit.NSPasteboardTypeString)
        return text

    def copy_link(self, destination: str) -> str:
        """Copy an approved message link as both text and a macOS URL."""
        destination = external_link_destination(destination)
        if sys.platform != "darwin":
            raise ValueError("copying links requires macOS with PyObjC installed")
        try:
            import AppKit  # pylint: disable=import-error,import-outside-toplevel
        except ImportError as error:
            raise ValueError("copying links requires macOS with PyObjC installed") from error

        pasteboard = AppKit.NSPasteboard.generalPasteboard()
        pasteboard.clearContents()
        pasteboard.setString_forType_(destination, AppKit.NSPasteboardTypeString)
        pasteboard.setString_forType_(destination, AppKit.NSPasteboardTypeURL)
        return destination

    def open_link(self, destination: str) -> str:
        """Open an approved message link only after an explicit viewer action."""
        destination = external_link_destination(destination)
        if self.e2e_directory is not None:
            return destination
        if sys.platform != "darwin":
            raise ValueError("opening links requires macOS with PyObjC installed")
        try:
            import AppKit  # pylint: disable=import-error,import-outside-toplevel
            from Foundation import (
                NSURL,  # pylint: disable=import-error,import-outside-toplevel,no-name-in-module
            )
        except ImportError as error:
            raise ValueError("opening links requires macOS with PyObjC installed") from error

        url = NSURL.URLWithString_(destination)
        if url is None or not AppKit.NSWorkspace.sharedWorkspace().openURL_(url):
            raise ValueError("could not open external link")
        return destination

    def part(self, message_pk: int, part_id: int, allow_remote: bool = False) -> dict[str, Any]:
        return render_part(self._archive(), message_pk, part_id, allow_remote).model_dump(mode="json")

    def attachment(self, message_pk: int, part_id: int) -> dict[str, Any]:
        return attachment_content(self._archive(), message_pk, part_id).model_dump(mode="json")

    def save_message(self, message_pk: int) -> str | None:
        view = describe_message(self._archive(), message_pk)
        if self.e2e_directory is not None:
            destination = self.e2e_directory / f"saved-{export_filename(view)}"
            write_message(self._archive(), message_pk, destination)
            return str(destination)
        selected = self.window.create_file_dialog(
            webview.FileDialog.SAVE,
            directory=str(Path.home() / "Desktop"),
            save_filename=export_filename(view),
            file_types=("Email message (*.eml)",),
        )
        if not selected:
            return None
        destination = dialog_paths(selected)[0]
        if destination.suffix.casefold() != ".eml":
            destination = destination.with_suffix(".eml")
        write_message(self._archive(), message_pk, destination)
        return str(destination)

    def save_attachment(self, message_pk: int, part_id: int) -> str | None:
        attachment = attachment_descriptor(self._archive(), message_pk, part_id)
        if self.e2e_directory is not None:
            destination = self.e2e_directory / f"saved-{safe_filename(attachment.filename, part_id, attachment.content_type)}"
            write_attachment(self._archive(), message_pk, part_id, destination)
            return str(destination)
        selected = self.window.create_file_dialog(
            webview.FileDialog.SAVE,
            directory=str(Path.home() / "Desktop"),
            save_filename=attachment.filename,
            file_types=("All files (*.*)",),
        )
        if not selected:
            return None
        destination = dialog_paths(selected)[0]
        write_attachment(self._archive(), message_pk, part_id, destination)
        return str(destination)

    def prepare_drag(self, message_pks: list[int]) -> dict[str, str]:
        """Prepare one explicit Finder drag without proactively exporting mail."""
        with self._drag_lock:
            if self._drag_closed:
                raise ValueError("message viewer is closed")
            unique = list(dict.fromkeys(message_pks))
            if not unique:
                raise ValueError("select at least one message to drag")
            archive = self._archive()
            directory = self.temporary_directory / "drags" / uuid4().hex
            if len(unique) == 1:
                view = describe_message(archive, unique[0])
                destination = directory / export_filename(view)
                write_message(archive, unique[0], destination)
            else:
                destination = directory / f"Email Collection Toolkit Messages ({len(unique)}).zip"
                write_messages_zip(archive, unique, destination)
            token = FILE_DRAGS.register(destination)
            self._drag_tokens.add(token)
            return DragExport(filename=destination.name, token=token).model_dump()

    def open_attachment(self, message_pk: int, part_id: int, confirmed: bool = False) -> dict[str, Any]:
        descriptor = attachment_descriptor(self._archive(), message_pk, part_id)
        risky = is_risky(descriptor.filename, descriptor.content_type)
        if risky and not confirmed:
            return OpenResult(filename=descriptor.filename, requires_confirmation=True).model_dump()
        destination = self.temporary_directory / safe_filename(
            descriptor.filename, part_id, descriptor.content_type
        )
        write_attachment(self._archive(), message_pk, part_id, destination)
        subprocess.Popen(["/usr/bin/open", str(destination)], close_fds=True)
        return OpenResult(filename=descriptor.filename, opened=True).model_dump()

    def open_message_window(self, message_pk: int, highlight_terms: list[str] | None = None) -> bool:
        view: MessageView = describe_message(self._archive(), message_pk)
        if self.e2e_directory is not None:
            return True
        base_url = str(self.window.get_current_url()).split("?", 1)[0]
        parameters = [("message", str(message_pk)), ("standalone", "1")]
        parameters.extend(("highlight", term) for term in highlight_terms or [])
        child_api = GuiApi(
            self._archive(), self.temporary_directory, self.e2e_directory, self.filter_sets.path
        )
        child = webview.create_window(
            view.subject,
            f"{base_url}?{urlencode(parameters)}",
            js_api=WindowBridge(child_api, SEARCH_BRIDGE_METHODS),
            width=900,
            height=760,
            min_size=(560, 420),
            text_select=True,
            draggable=True,
        )
        if child is None:
            raise RuntimeError("pywebview failed to create a window")
        child_api.set_window(child)
        self.children.append(child_api)

        def close_child(*_args: object) -> None:
            child_api.close()
            if child_api in self.children:
                self.children.remove(child_api)

        child.events.closed += close_child
        return True

    def close(self, *_args: object) -> None:
        self._preview_executor.shutdown(wait=False, cancel_futures=True)
        if self.application is None:
            with self._ingest_window_lock:
                ingest_api, self._ingest_window_api = self._ingest_window_api, None
            if ingest_api is not None and ingest_api.window is not None:
                ingest_api.window.destroy()
                ingest_api.close()
        for child in tuple(self.children):
            if child.window is not None:
                child.window.destroy()
            child.close()
        self.children.clear()
        with self._drag_lock:
            self._drag_closed = True
            FILE_DRAGS.discard(self._drag_tokens)
            self._drag_tokens.clear()
            if self._temporary:
                self._temporary.cleanup()

    def _archive(self) -> Path:
        if self.archive is None or not _is_archive(self.archive):
            raise ValueError("choose an archive containing archive.sqlite3 and search.sqlite3")
        return self.archive


class NativeSmokeApi:
    """Expose only the three bridge calls required by the hidden smoke page."""

    def __init__(self, api: GuiApi, controller: NativeSmokeController) -> None:
        self._api = api
        self._controller = controller

    def status(self) -> dict[str, Any]:
        return self._api.status()

    def search(
        self,
        query: str,
        offset: int = 0,
        sort_by: str = "date",
        direction: str = "descending",
        search_attachments: bool = False,
        mailbox_selections: list[str] | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> dict[str, Any]:
        return self._api.search(
            query, offset, sort_by, direction, search_attachments, mailbox_selections, limit
        )

    def native_smoke_complete(self, passed: bool, error: str | None = None) -> bool:
        self._controller.complete(passed, error)
        return True


class SetupApi:
    """Expose folder selection and the existing confirmed import workflow."""

    def __init__(self, application: PyWebViewApplication) -> None:
        self.application = application
        self.window: Any = None
        self._source: Path | None = None
        self._destination: Path | None = None
        self._lock = Lock()

    def choose_source(self) -> str | None:
        selected = self._choose_folder(self._source, destination=False)
        if selected is not None:
            self._source = selected
        return str(self._source) if self._source is not None else None

    def choose_destination(self) -> str | None:
        selected = self._choose_folder(self._destination, destination=True)
        if selected is not None:
            self._destination = selected
        return str(self._destination) if self._destination is not None else None

    def _choose_folder(self, previous: Path | None, *, destination: bool) -> Path | None:
        self._lock.acquire()
        try:
            self.application._refresh_menus()
            return self._pick_folder(previous, destination=destination)
        finally:
            self._lock.release()
            self.application._refresh_menus()

    def _pick_folder(self, previous: Path | None, *, destination: bool) -> Path | None:
        directory = previous or Path.home()
        if sys.platform == "darwin":
            selected = macos_import_picker(
                directory,
                "Select archive folder" if destination else "Select root folder to ingest",
                "Choose an existing archive or a pre-created empty folder outside the source tree."
                if destination else "Mail files and subfolders will be read without changing them.",
                "Select", folders=True, files=False, create_directories=False,
            )
        else:
            selected = self.window.create_file_dialog(
                webview.FileDialog.FOLDER, directory=str(directory), allow_multiple=False,
            )
        return dialog_paths(selected)[0] if selected else None

    def start_import(self) -> bool:
        if not self._lock.acquire(blocking=False):
            return False
        try:
            self.application._refresh_menus()
            if self._source is None or self._destination is None:
                raise ValueError("Select both folders before starting import.")
            selection = SetupSelection(source=self._source, destination=self._destination)
            selection.validate_paths()
            controller = self.application.controller
            document = (
                controller.open_document(selection.destination)
                if _is_archive(selection.destination)
                else controller.create_document(selection.destination)
            )
            api = next((item for item in self.application._search_apis() if item.document is document), None)
            if api is None:
                api = self.application.create_search_window(controller.new_search_window(document))
            started = self.application._import_document(
                api, dialog_window=self.window, selected_roots=[selection.source],
            )
            if started:
                self.application.open_ingest_window(document)
                self._dismiss()
            return started
        finally:
            self._lock.release()
            self.application._refresh_menus()

    def cancel(self) -> bool:
        """Quit after this bridge thread has delivered its response to JavaScript."""
        if not self._lock.acquire(blocking=False):
            return False
        try:
            self.application._refresh_menus()
            reply_thread = current_thread()

            def quit_after_reply() -> None:
                reply_thread.join()
                self.application.request_quit()

            Thread(target=quit_after_reply, name="mailarchiver-setup-quit", daemon=True).start()
            return True
        finally:
            self._lock.release()
            self.application._refresh_menus()

    def _dismiss(self) -> None:
        # Keep the webview alive until pywebview delivers this bridge reply.
        self._source = self._destination = None
        self.application._setup_visible = False
        self.window.hide()
        self.application._refresh_menus()


class PyWebViewApplication:
    """Bind the platform-neutral application controller to pywebview windows."""

    def __init__(
        self,
        controller: ApplicationController,
        asset_server: LoopbackAssetServer | None = None,
    ) -> None:
        self.controller = controller
        self.asset_server = asset_server
        self._apis: dict[str, GuiApi] = {}
        self._ingest_apis: dict[str, IngestWindowApi] = {}
        self._options_apis: dict[str, DocumentOptionsApi] = {}
        self._native_search_ids: dict[str, str] = {}
        self._native_child_ids: dict[str, str] = {}
        self._about_api = AboutApi(self)
        self._about_window: Any = None
        self._setup_api: SetupApi | None = None
        self._setup_visible = False
        self._about_visible = False
        self._notices: list[ApplicationNotice] = []
        self._connectivity: ConnectivityMonitor | None = None
        self._menu_observer: Any = None
        self._lock = RLock()
        self._quitting = False
        self._import_threads: list[Thread] = []

    def asset_url(
        self, asset: str, parameters: list[tuple[str, str]] | None = None
    ) -> str:
        if self.asset_server is not None:
            return self.asset_server.url(asset, parameters)
        suffix = f"?{urlencode(parameters)}" if parameters else ""
        return f"{GUI_DIRECTORY / asset}{suffix}"

    def create_about_window(self, *, hidden: bool = False) -> None:
        """Retain the application anchor; show health on an explicit About request."""
        if self._connectivity is None:
            self._connectivity = ConnectivityMonitor()
        if self._about_window is not None:
            self._about_visible = True
            self._about_window.restore()
            self._about_window.show()
            self._refresh_menus()
            return
        window = webview.create_window(
            f"About {APPLICATION_NAME}",
            self.asset_url("about.html"),
            js_api=WindowBridge(self._about_api, ("status",)),
            width=620,
            height=620,
            hidden=hidden,
            min_size=(480, 420),
            text_select=True,
            menu=self.menu(),
        )
        if window is None:
            raise RuntimeError("pywebview failed to create a window")
        self._about_window = window
        self._about_visible = not hidden
        window.events.shown += lambda *_args: self._refresh_menus()

        def closing(*_args: object) -> bool:
            if self._quitting:
                return True
            # Keep a hidden native window so closing the last visible window does
            # not end pywebview's event loop or remove File/New/Open and About.
            self._about_visible = False
            window.hide()
            self._refresh_menus()
            return False

        def closed(*_args: object) -> None:
            self._about_window = None
            self._about_visible = False
            self._refresh_menus()

        window.events.closing += closing
        window.events.closed += closed
        self._refresh_menus()

    def add_notice(self, severity: Literal["information", "warning", "error"], message: str) -> None:
        notice = ApplicationNotice(severity=severity, message=message)
        with self._lock:
            if self._notices and self._notices[-1].severity == severity and self._notices[-1].message == message:
                return
            self._notices.append(notice)
            self._notices = self._notices[-100:]
        if severity != "information":
            for api in self._search_apis():
                if api.window is not None:
                    api.window.run_js(f"window.mailArchiverNotice?.({json.dumps(message)});")

    def notices(self) -> list[ApplicationNotice]:
        with self._lock:
            return [notice.model_copy(deep=True) for notice in self._notices]

    def about_status(self) -> AboutStatus:
        documents = self.controller.documents()
        active = self.controller.active_document
        disk_path = (active.path if active else None) or next(
            (document.path for document in documents if document.path), Path.home()
        )
        assert disk_path is not None
        try:
            free = shutil.disk_usage(disk_path).free
        except OSError as error:
            free = 0
            self.add_notice("warning", f"Could not read free disk space for {disk_path}: {error}")
        ingests = []
        for document in documents:
            if document.path is None:
                continue
            job = document.ingest_job
            ingests.append(
                AboutIngestStatus(
                    archive=str(document.display_path or document.path),
                    owner_window_id=job.owner_window_id if job else None,
                    operation_id=job.operation_id if job else None,
                    status=latest_ingest_status(document.path),
                )
            )
        return AboutStatus(
            metadata=application_metadata(),
            disk_path=str(disk_path),
            disk_free_bytes=free,
            internet=self._connectivity.status() if self._connectivity else InternetStatus(),
            notices=self.notices(),
            ingests=ingests,
            antivirus=scanner_availability(),
        )

    def create_search_window(self, session: SearchWindow) -> GuiApi:
        document = self.controller.document(session.document_id)
        api = GuiApi(
            document.path,
            application=self,
            document=document,
            search_window=session,
        )
        status = api.status()
        window = webview.create_window(
            _window_title(document, int(status["message_count"])),
            self.asset_url("index.html"),
            js_api=WindowBridge(api, SEARCH_BRIDGE_METHODS),
            width=session.geometry.width,
            height=session.geometry.height,
            x=session.geometry.x,
            y=session.geometry.y,
            min_size=(900, 560),
            text_select=True,
            draggable=True,
            menu=self.menu(),
        )
        if window is None:
            raise RuntimeError("pywebview failed to create a window")
        api.set_window(window)
        with self._lock:
            self._apis[session.window_id] = api
            self._native_search_ids[window.uid] = session.window_id
        window.events.shown += lambda *_args: self.activate_window(session.window_id)
        window.events.restored += lambda *_args: self.activate_window(session.window_id)
        window.events.closing += lambda *_args: self._can_close_search_window(session.window_id, window)
        window.events.closed += lambda *_args: self._close_window(session.window_id)
        self._refresh_menus()
        return api

    def activate_window(self, window_id: str) -> None:
        self.controller.activate_window(window_id)
        self._refresh_menus()

    def document_for_native_window(self, uid: str) -> ArchiveDocument | None:
        """Resolve child windows even after the last search window has closed."""
        with self._lock:
            document_id = self._native_child_ids.get(uid)
            if document_id is not None:
                return self.controller.document(document_id)
            window_id = self._native_search_ids.get(uid)
            api = self._apis.get(window_id) if window_id is not None else None
            return api.document if api is not None else None

    def active_document(self) -> ArchiveDocument | None:
        native = webview.active_window()
        return self.document_for_native_window(native.uid) if native is not None else self.controller.active_document

    def active_api(self) -> GuiApi | None:
        native = webview.active_window()
        if native is not None:
            with self._lock:
                window_id = self._native_search_ids.get(native.uid)
                document_id = self._native_child_ids.get(native.uid)
                if window_id is None and document_id is not None:
                    window_id = next((key for key, value in self._apis.items() if value.document is self.controller.document(document_id)), None)
                api = self._apis.get(window_id) if window_id else None
            if window_id is not None:
                self.controller.activate_window(window_id)
            return api
        session = self.controller.active_window
        with self._lock:
            return self._apis.get(session.window_id) if session else None

    def new_document(self) -> bool:
        anchor = self._dialog_window()
        if anchor is None:
            return False
        created = self._create_new_document(anchor)
        if created is None:
            return False
        self._import_document(created)
        return True

    def _create_new_document(self, anchor: Any) -> GuiApi | None:
        selected = anchor.create_file_dialog(
            webview.FileDialog.SAVE,
            directory=str(Path.home()),
            save_filename="Untitled.mailarchive",
            file_types=("Email Collection Toolkit archive (*.mailarchive)",),
        )
        if not selected:
            return None
        try:
            destination = archive_destination(selected)
            document = self.controller.create_document(destination)
        except (OSError, ValueError) as error:
            self.add_notice("error", f"Could not create archive: {error}")
            return None
        return self.create_search_window(self.controller.new_search_window(document))

    def show_setup(self) -> None:
        """Show all three setup steps together, retaining a single setup window."""
        with self._lock:
            if self._setup_api is not None:
                self._setup_visible = True
                self._setup_api.window.restore()
                self._setup_api.window.show()
                self._refresh_menus()
                return
            api = SetupApi(self)
            window = webview.create_window(
                f"{APPLICATION_NAME} — Get started", self.asset_url("setup.html"),
                js_api=WindowBridge(api, ("choose_source", "choose_destination", "start_import", "cancel")),
                width=760, height=650, min_size=(560, 580), text_select=True, menu=self.menu(),
            )
            if window is None:
                raise RuntimeError("pywebview failed to create a setup window")
            api.window = window
            self._setup_api = api
            self._setup_visible = True

            def closed(*_args: object) -> None:
                with self._lock:
                    self._setup_api = None
                    self._setup_visible = False
                self._refresh_menus()

            window.events.closing += lambda *_args: not api._lock.locked()
            window.events.closed += closed
        self._refresh_menus()

    def new_search_window(self) -> bool:
        document = self.active_document()
        if document is None:
            return False
        self.create_search_window(self.controller.new_search_window(document))
        return True

    def import_active_document(self) -> bool:
        """Collect a supported local source and start typed ingest off the webview thread."""
        api = self.active_api()
        if api is None and (document := self.active_document()) is not None:
            api = self.create_search_window(self.controller.new_search_window(document))
        return self._import_document(api) if api is not None else False

    def import_directory(self, document: ArchiveDocument, anchor: Any) -> bool:
        """Use the Ingests window's document, independently of the active search window."""
        if document.path is None or document.ingest_job is not None:
            return False
        api = next((item for item in self._search_apis() if item.document is document), None)
        if api is None:
            api = self.create_search_window(self.controller.new_search_window(document))
        return self._import_document(api, directory_only=True, dialog_window=anchor)

    def _import_document(
        self, api: GuiApi, *, directory_only: bool = False, dialog_window: Any = None,
        selected_roots: list[Path] | None = None,
    ) -> bool:
        if api.window is None or api.document is None or api.document.path is None:
            return False
        if api.search_window is not None:
            self.controller.activate_window(api.search_window.window_id)
        try:
            document = self.controller.import_document()
        except (ArchiveBusyError, ValueError) as error:
            self.add_notice("warning", str(error))
            return False
        anchor = dialog_window or api.window
        if document.path is None:
            raise ValueError("Import requires a saved archive")
        destination = document.display_path or document.path
        title = f"Import into {destination.name}"
        antivirus = scanner_availability()
        if selected_roots is None:
            try:
                picker_directory = configured_import_directory(document.path)
            except ValueError as error:
                self.add_notice("warning", str(error))
                picker_directory = destination.parent
            if sys.platform == "darwin":
                selected_sources = macos_import_picker(
                    picker_directory, title,
                    f"Destination archive: {destination}\n"
                    "Choose mail files or directories. Directories include supported mail files and subdirectories.",
                    "Import", folders=True, multiple=True,
                    warning=None if antivirus.configured else UNSCANNED_WARNING,
                )
            else:
                choose_folders = directory_only or anchor.create_confirmation_dialog(
                    title, f"Destination archive: {destination}\n\nChoose OK for folders or Cancel for files.",
                )
                selected_sources = anchor.create_file_dialog(
                    webview.FileDialog.FOLDER if choose_folders else webview.FileDialog.OPEN,
                    directory=str(picker_directory), allow_multiple=True,
                    file_types=() if choose_folders else ("Mail source files (*.*)",),
                )
            if not selected_sources:
                return False
            roots = list(dialog_paths(selected_sources))
        else:
            roots = selected_roots
        try:
            store = DocumentOptions(document.path)
            owner_revision = store.state().revision
            defaults = store.defaults(roots)
            if sys.platform == "darwin":
                owner_rules = macos_owner_rules(destination, defaults)
            else:
                prompt = OwnerRulesPrompt(defaults)
                window = webview.create_window(
                    f"Owner emails for {destination.name}", self.asset_url("options.html", [("import", "1")]),
                    js_api=WindowBridge(prompt, ("status", "update", "cancel")), width=660, height=620,
                )
                if window is None:
                    raise RuntimeError("Could not open owner email editor")
                prompt.window = window
                window.events.closed += prompt.cancel
                owner_rules = prompt.result.result()
            if owner_rules is None:
                return False
        except (OSError, ValueError) as error:
            self.add_notice("error", f"Could not load owner email rules: {error}")
            if sys.platform == "darwin":
                macos_alert(title, str(error), ("OK",))
            return False
        summary = "\n".join(str(root) for root in roots)
        confirmation = (
            f"Destination archive: {destination}\n\nRead-only sources:\n{summary}\n\n"
            f"Owner emails: {', '.join(owner_rules.include)}\n"
            f"Exclude: {', '.join(owner_rules.exclude) or '(none)'}\n\n{antivirus.detail}"
        )
        if not antivirus.configured:
            choice = (
                macos_alert(title, confirmation, ("Cancel", "Import Without Scanning", "Install ClamAV…"),
                            body_width=IMPORT_CONFIRMATION_WIDTH)
                if sys.platform == "darwin" else
                (1 if anchor.create_confirmation_dialog(title, confirmation + "\n\nImport WITHOUT antivirus scanning?") else 0)
            )
            if choice == 2:
                import webbrowser  # pylint: disable=import-outside-toplevel
                webbrowser.open(CLAMAV_DOWNLOAD_URL)
                return False
            if choice != 1:
                return False
            return self.start_import(api, roots, owner_rules=owner_rules, owner_revision=owner_revision, scan_policy="not-scanned")
        confirmed = (
            macos_alert(title, confirmation, ("Import", "Cancel"), body_width=IMPORT_CONFIRMATION_WIDTH) == 0
            if sys.platform == "darwin" else anchor.create_confirmation_dialog(title, confirmation)
        )
        if not confirmed:
            return False
        return self.start_import(api, roots, owner_rules=owner_rules, owner_revision=owner_revision)

    def start_import(
        self, api: GuiApi, roots: list[Path], owner_names: Path | None = None, *, owner_rules: OwnerRules | None = None,
        owner_revision: str | None = None,
        scan_policy: Literal["clamav", "not-scanned"] = "clamav",
    ) -> bool:
        """Acquire both ingest layers before launching the shared service."""
        if self._quitting:
            return False
        document = api.document
        session = api.search_window
        if document is None or document.path is None or session is None:
            return False
        operation_id = uuid4().hex
        lease: WriterLease | None = None
        try:
            lease = WriterLease.acquire(
                document.path,
                document.descriptor.identity,
                "GUI import",
                operation_id,
                application_metadata().version,
            )
            if owner_rules is not None:
                DocumentOptions(document.path).save(owner_rules, lease, owner_revision)
            job = IngestJob(operation_id=operation_id, owner_window_id=session.window_id)
            with self._lock:
                if self._quitting:
                    raise ValueError("The application is stopping imports and quitting.")
                self.controller.begin_ingest(document.descriptor.document_id, job, lease)
        except (OSError, ArchiveBusyError, ValueError) as error:
            if lease is not None:
                lease.release()
            self.add_notice("warning", f"Import did not start: {error}")
            return False
        self.add_notice("information", f"Import started for {document.display_path}")
        if scan_policy == "not-scanned":
            self.add_notice("warning", f"{document.display_path}: importing WITHOUT antivirus scanning.")
        self._refresh_menus()
        request = IngestRequest(
            archive=document.path,
            owner_names_file=owner_names,
            owner_rules=owner_rules,
            roots=[str(root) for root in roots],
            scan_policy=scan_policy,
        )
        def import_worker() -> None:
            try:
                self._run_import(document, operation_id, lease, request, job.stop)
            finally:
                job.finished.set()

        worker = Thread(
            target=import_worker,
            name=f"mailarchiver-import-{operation_id[:8]}",
            daemon=False,
        )
        with self._lock:
            self._import_threads = [thread for thread in self._import_threads if thread.is_alive()]
            self._import_threads.append(worker)
            worker.start()
        return True

    def stop_imports_for_quit(self) -> tuple[IngestJob, ...]:
        """Called only after confirmation; prevent new imports and stop all existing jobs."""
        with self._lock:
            self._quitting = True
            jobs = tuple(job for document in self.controller.documents() if (job := document.ingest_job) is not None)
            for job in jobs:
                job.stop.set()
            return jobs

    def _run_import(
        self,
        document: ArchiveDocument,
        operation_id: str,
        lease: WriterLease,
        request: IngestRequest,
        stop: Event,
    ) -> None:
        error: BaseException | None = None
        outcome = IngestOutcome()
        try:
            run_ingest(request, lease, stop_event=stop, outcome=outcome, terminal=False)
            try:
                remember_import_directory(request.archive, [Path(root) for root in request.roots])
            except (OSError, ValueError) as config_error:
                self.add_notice("warning", f"Import completed but the source directory could not be saved: {config_error}")
        except BaseException as caught:  # pylint: disable=broad-exception-caught
            logging.getLogger(__name__).debug("Best-effort operation failed", exc_info=True)
            error = caught
        finally:
            try:
                refresh = self.controller.finish_ingest(
                    document.descriptor.document_id,
                    operation_id,
                    published=outcome.published,
                )
            except ValueError:
                refresh = ()
            self._refresh_search_windows(refresh)
            self._refresh_menus()
        if isinstance(error, IngestInterrupted):
            self.add_notice("information", f"Import stopped for {document.display_path}; import the same source again to continue.")
        elif error is None:
            self.add_notice("information", f"Import completed for {document.display_path}")
        else:
            self.add_notice(
                "error",
                f"Import failed for {document.display_path}: {type(error).__name__}: {error}",
            )

    def open_archive_dialog(self) -> bool:
        anchor = self._dialog_window()
        if anchor is None:
            return False
        api = self.active_api()
        directory = api.document.display_path.parent if api and api.document and api.document.display_path else Path.home()
        selected = (
            macos_import_picker(directory, "Open Mail Archive", "Select a .mailarchive package or an existing archive directory.", "Open", folders=True)
            if sys.platform == "darwin" else anchor.create_file_dialog(webview.FileDialog.FOLDER, directory=str(directory))
        )
        if not selected:
            return False
        try:
            self.add_notice("information", f"Opening archive: {dialog_paths(selected)[0]}")
            self.open_document(dialog_paths(selected)[0])
            return True
        except (OSError, ValueError) as error:
            message = f"Could not open archive: {error}"
            print(f"mailsearch-gui: {message}", file=sys.stderr)
            self.add_notice("error", message)
            return False

    def open_document(self, path: Path, *, recent: bool = False) -> GuiApi:
        document = (
            self.controller.open_recent_document(path)
            if recent
            else self.controller.open_document(path)
        )
        return self.create_search_window(self.controller.new_search_window(document))

    def open_recent_document(self, path: Path) -> bool:
        try:
            self.open_document(path, recent=True)
            return True
        except (OSError, ValueError) as error:
            self.add_notice("error", f"Could not open recent archive: {error}")
            return False

    def handle_open_documents(self, paths: tuple[Path, ...]) -> list[str]:
        result = self.controller.handle_open_documents(paths)
        for session in result.windows:
            self.create_search_window(session)
        for error in result.errors:
            self.add_notice("error", error)
        return result.errors

    def reopen(self) -> list[str]:
        if self._setup_api is not None and self._setup_visible:
            self.show_setup()
            return []
        api = self.active_api()
        if api is not None and api.window is not None:
            api.window.restore()
            api.window.show()
            return []
        result = self.controller.startup()
        for session in result.windows:
            if self.controller.document(session.document_id).descriptor.untitled:
                self.controller.close_window(session.window_id)
                self.show_setup()
            else:
                self.create_search_window(session)
        return result.errors

    def close_active_window(self) -> bool:
        if self._setup_api is not None and self._setup_api._lock.locked():
            return False
        native = webview.active_window()
        if native is None or native is self._about_window:
            return False
        with self._lock:
            search_id = self._native_search_ids.get(native.uid)
        if search_id is not None and not self.controller.can_close_window(search_id):
            return False
        native.destroy()
        return True

    def open_active_ingest_window(self) -> bool:
        document = self.active_document()
        return self.open_ingest_window(document) if document is not None else False

    def open_document_options(self, document: ArchiveDocument | None = None) -> bool:
        if document is None:
            native = webview.active_window()
            child_id = self._native_child_ids.get(native.uid) if native else None
            api = self.active_api()
            document = self.controller.document(child_id) if child_id else (api.document if api else None)
        if document is None or document.path is None:
            return False
        document_id = document.descriptor.document_id
        with self._lock:
            options = self._options_apis.get(document_id)
            if options is not None:
                options.window.restore()
                options.window.show()
                return True
            options = DocumentOptionsApi(document)
            window = webview.create_window(
                f"{APPLICATION_NAME} — Document Options — {document.display_path}",
                self.asset_url("options.html"),
                js_api=WindowBridge(options, ("status", "update")),
                width=640, height=600, min_size=(480, 420), menu=self.menu(),
            )
            if window is None:
                raise RuntimeError("pywebview failed to create a window")
            options.window = window
            self._options_apis[document_id] = options
            self._native_child_ids[window.uid] = document_id
            self.controller.attach_child_window(document_id, window.uid)

            def closed(*_args: object) -> None:
                with self._lock:
                    self._options_apis.pop(document_id, None)
                    self._native_child_ids.pop(window.uid, None)
                self.controller.close_child_window(document_id, window.uid)
                self._refresh_menus()

            window.events.closed += closed
        self._refresh_menus()
        return True

    def open_ingest_window(self, document: ArchiveDocument, status_id: str | None = None) -> bool:
        if document.path is None:
            return False
        document_id = document.descriptor.document_id
        with self._lock:
            api = self._ingest_apis.get(document_id)
            if api is not None and api.window is not None:
                if status_id is not None:
                    api.window.run_js(f"window.selectIngest({json.dumps(status_id)});")
                api.window.restore()
                api.window.show()
                return True
            api = IngestWindowApi(document.path, application=self, document=document)
            window = webview.create_window(
                f"{APPLICATION_NAME} — Ingests — {document.display_path}",
                self.asset_url(
                    "ingests.html",
                    [("status", status_id)] if status_id is not None else None,
                ),
                js_api=WindowBridge(api, ("history", "can_import_directory", "import_directory", "antivirus", "install_antivirus")),
                width=1050,
                height=700,
                min_size=(720, 440),
                text_select=True,
                menu=self.menu(),
            )
            if window is None:
                raise RuntimeError("pywebview failed to create a window")
            api.set_window(window)
            self._ingest_apis[document_id] = api
            self._native_child_ids[window.uid] = document_id
            self.controller.attach_child_window(document_id, window.uid)

            def closed(*_args: object) -> None:
                api.close()
                with self._lock:
                    if self._ingest_apis.get(document_id) is api:
                        self._ingest_apis.pop(document_id)
                    self._native_child_ids.pop(window.uid, None)
                self.controller.close_child_window(document_id, window.uid)
                self._refresh_menus()

            window.events.closed += closed
        self._refresh_menus()
        return True

    def _close_window(self, window_id: str) -> None:
        with self._lock:
            api = self._apis.pop(window_id, None)
            if api is not None and api.window is not None:
                self._native_search_ids.pop(api.window.uid, None)
        if api is not None:
            api.close()
        try:
            self.controller.close_window(window_id)
        except ValueError:
            pass
        self._refresh_menus()

    def focus_window(self, uid: str) -> bool:
        windows = [api.window for api in self._search_apis()]
        windows.extend(api.window for api in self._ingest_apis.values())
        windows.extend(api.window for api in self._options_apis.values())
        windows.append(self._about_window)
        if self._setup_api is not None:
            windows.append(self._setup_api.window)
        window = next((candidate for candidate in windows if candidate is not None and candidate.uid == uid), None)
        if window is None:
            return False
        window.restore()
        window.show()
        return True

    def menu(self) -> list[Menu]:
        return application_menu(self)

    def window_menu_items(self) -> list[MenuAction]:
        items = []
        if self._setup_api is not None and self._setup_visible:
            items.append(MenuAction("Get started", self.show_setup))
        if self._about_window is not None and self._about_visible:
            items.append(MenuAction(f"About {APPLICATION_NAME}", lambda: self.focus_window(self._about_window.uid)))
        for index, api in enumerate(self._search_apis(), 1):
            if api.window is None:
                continue
            document = api.document
            name = document.display_path.name if document and document.display_path else "Untitled"
            items.append(
                MenuAction(
                    f"{name} — Search {index}",
                    lambda uid=api.window.uid: self.focus_window(uid),
                )
            )
        for document_id, api in tuple(self._ingest_apis.items()):
            if api.window is None:
                continue
            document = self.controller.document(document_id)
            name = document.display_path.name if document.display_path else "Untitled"
            items.append(
                MenuAction(
                    f"{name} — Ingests",
                    lambda uid=api.window.uid: self.focus_window(uid),
                )
            )
        for api in tuple(self._options_apis.values()):
            items.append(MenuAction(api.window.title, lambda uid=api.window.uid: self.focus_window(uid)))
        return items

    def _can_close_search_window(self, window_id: str, window: Any) -> bool:
        if self.controller.can_close_window(window_id):
            return True
        if not window.create_confirmation_dialog(
            "Import is running",
            "Wait for the import to finish before closing? Cancel keeps this window open.",
        ):
            return False
        with self._lock:
            workers = tuple(self._import_threads)
        for worker in workers:
            worker.join()
        return self.controller.can_close_window(window_id)

    def request_quit(self) -> None:
        """Use the normal stop-and-checkpoint policy before closing every window."""
        # Reserve a job-free quit under the same lock used to publish imports.
        if not self.prepare_quit():
            anchor = self._dialog_window()
            confirmed = (
                macos_alert("Stop importing and quit?", QUIT_IMPORT_MESSAGE,
                            ("Cancel", "Stop Import and Quit"), body_width=IMPORT_CONFIRMATION_WIDTH) == 1
                if sys.platform == "darwin" else
                bool(anchor and anchor.create_confirmation_dialog("Stop importing and quit?", QUIT_IMPORT_MESSAGE))
            )
            if not confirmed:
                return
            for job in self.stop_imports_for_quit():
                job.finished.wait()
        for window in tuple(webview.windows):
            window.destroy()

    def prepare_quit(self) -> bool:
        """Keep windows and services alive until every import has completed."""
        with self._lock:
            if any(document.ingest_job for document in self.controller.documents()):
                return False
            self._quitting = True
            return True

    def shutdown(self) -> None:
        """Release non-document resources after the native event loop exits."""
        with self._lock:
            workers = tuple(self._import_threads)
        for worker in workers:
            worker.join()
        if sys.platform == "darwin" and self._menu_observer is not None:
            from Foundation import NSNotificationCenter  # pylint: disable=import-outside-toplevel,no-name-in-module,import-error
            NSNotificationCenter.defaultCenter().removeObserver_(self._menu_observer)
            self._menu_observer = None
        if self._connectivity is not None:
            self._connectivity.close()
        if self.asset_server is not None:
            self.asset_server.close()

    def _dialog_window(self) -> Any:
        return webview.active_window() or self._about_window or next(
            (api.window for api in self._search_apis() if api.window is not None), None
        )

    def _search_apis(self) -> tuple[GuiApi, ...]:
        with self._lock:
            return tuple(self._apis.values())

    def _refresh_search_windows(self, window_ids: tuple[str, ...]) -> None:
        with self._lock:
            apis = [self._apis[item] for item in window_ids if item in self._apis]
        for api in apis:
            api.invalidate_archive()
            if api.window is not None:
                api.window.run_js("window.archiveDidChange?.();")

    def _refresh_menus(self) -> None:
        menu = self.menu()
        windows = [api.window for api in self._search_apis()]
        windows.extend(api.window for api in self._ingest_apis.values())
        windows.append(self._about_window)
        if self._setup_api is not None:
            windows.append(self._setup_api.window)
        for window in windows:
            if window is not None:
                window.menu = menu
        self._refresh_macos_menu()

    def _refresh_macos_menu(self) -> None:
        """Refresh pywebview's process menu and its dynamic Close enabled state."""
        if sys.platform != "darwin":
            return
        try:
            from PyObjCTools import AppHelper  # pylint: disable=import-error,import-outside-toplevel
            from webview.platforms.cocoa import BrowserView  # pylint: disable=import-error,import-outside-toplevel
            from Foundation import NSNotificationCenter  # pylint: disable=import-error,import-outside-toplevel,no-name-in-module
        except ImportError:
            return

        def refresh() -> None:
            # A launched/background app or native modal panel may have no webview key window.
            # Build the menu anyway, resolving Cocoa focus only on its main thread.
            active = webview.active_window()
            if active is None:
                session = self.controller.active_window
                api = self._apis.get(session.window_id) if session else None
                active = api.window if api is not None else self._about_window
            if active is None:
                return
            if self._menu_observer is None:
                self._menu_observer = NSNotificationCenter.defaultCenter().addObserverForName_object_queue_usingBlock_(
                    "NSWindowDidBecomeKeyNotification", None, None,
                    lambda _notification: self._refresh_macos_menu(),
                )
            for native in BrowserView.instances.values():
                native.menu = active.menu
            instance = BrowserView.instances.get(active.uid)
            if instance is None:
                return
            menu = instance._recreate_menus(active.menu)  # pylint: disable=protected-access
            BrowserView.app.setMainMenu_(menu)
            about_item = menu.itemAtIndex_(0).submenu().itemAtIndex_(0)
            about_item.setTarget_(BrowserView.app.delegate())
            about_item.setAction_("showMailArchiverAbout:")
            setattr(BrowserView, "current_menu", active.menu)
            for index, title in enumerate(("File", "Edit", "View", "Window"), 1):
                item = next(
                    (entry for entry in menu.itemArray()
                     if entry.title() == title or (entry.submenu() and entry.submenu().title() == title)),
                    None,
                )
                if item is not None:
                    item.setTitle_(title)
                    menu.removeItem_(item)
                    menu.insertItem_atIndex_(item, index)
            file_item = menu.itemWithTitle_("File")
            if file_item is not None:
                for title, key in (("New", "n"), ("Open…", "o"), ("Close", "w")):
                    item = file_item.submenu().itemWithTitle_(title)
                    if item is not None:
                        item.setKeyEquivalent_(key)
                        item.setKeyEquivalentModifierMask_(1 << 20)
            close_item = file_item.submenu().itemWithTitle_("Close") if file_item else None
            window_item = menu.itemWithTitle_("Window")
            if window_item is not None:
                submenu = window_item.submenu()
                submenu.setAutoenablesItems_(False)
                new_search = submenu.itemWithTitle_("New Search Window")
                if new_search is not None:
                    with self._lock:
                        search_id = self._native_search_ids.get(active.uid)
                        search_api = self._apis.get(search_id) if search_id is not None else None
                    new_search.setEnabled_(bool(search_api and search_api.document and search_api.document.path))
            if close_item is not None:
                with self._lock:
                    search_id = self._native_search_ids.get(active.uid)
                    child = active.uid in self._native_child_ids
                setup = self._setup_api
                busy = setup is not None and setup._lock.locked()
                enabled = child or (setup is not None and active is setup.window) or (
                    search_id is not None and self.controller.can_close_window(search_id)
                )
                close_item.setEnabled_(enabled and not busy)

        AppHelper.callAfter(refresh)


def dialog_paths(selected: str | tuple[str | Path, ...] | list[str]) -> tuple[Path, ...]:
    """Normalize native SAVE strings and OPEN/FOLDER sequences without splitting strings."""
    return tuple(Path(item) for item in ((selected,) if isinstance(selected, str) else selected))


def archive_destination(selected: str | tuple[str, ...] | list[str]) -> Path:
    """Validate a save choice before adding the archive extension."""
    destination = dialog_paths(selected)[0]
    if not destination.name:
        raise ValueError("Choose an archive name, not a filesystem root")
    if destination.suffix.casefold() != ".mailarchive":
        destination = destination.with_name(destination.name + ".mailarchive")
    return destination


def _is_archive(path: Path) -> bool:
    return path.is_dir() and (path / "archive.sqlite3").is_file() and (path / "search.sqlite3").is_file()


def _window_title(document: ArchiveDocument | Path | None, message_count: int = 0) -> str:
    if isinstance(document, ArchiveDocument):
        if document.descriptor.untitled:
            return f"Untitled — {APPLICATION_NAME}"
        path = document.display_path
    else:
        path = document
    if path is None:
        return APPLICATION_NAME
    return f"{APPLICATION_NAME} — {path} ({message_count:,} messages)"


def application_metadata() -> ApplicationMetadata:
    """Return the identity shown by the native application menu and About panel."""
    return ApplicationMetadata(
        name=APPLICATION_NAME,
        version=version("mailarchiver"),
        copyright="Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.",
    )


def application_menu(application: PyWebViewApplication) -> list[Menu]:
    """Build native actions that resolve the active window at invocation time."""
    file_items: list[Menu | MenuAction | MenuSeparator] = [
        MenuAction("New", application.new_document),
        MenuAction("Open…", application.open_archive_dialog),
    ]
    recent: list[Menu | MenuAction | MenuSeparator] = [
        MenuAction(str(path), lambda selected=path: application.open_recent_document(selected))
        for path in application.controller.preferences.recent_archives
    ]
    if recent:
        file_items.append(Menu("Open Recent", recent))
    file_items.extend(
        (
            MenuAction("Import…", application.import_active_document),
            MenuAction("Document Options…", application.open_document_options),
            MenuAction("Close", application.close_active_window),
        )
    )
    return [
        Menu("File", file_items),
        Menu(
            "Window",
            [MenuAction("New Search Window", application.new_search_window),
             MenuAction("Ingests", application.open_active_ingest_window), *application.window_menu_items()],
        ),
    ]


COCOA_OPEN_ARGUMENTS_DEFAULT = "NSTreatUnknownArgumentsAsOpen"


def configure_macos_application() -> None:
    """Replace the bare Python process identity before pywebview builds Cocoa menus."""
    if sys.platform != "darwin":
        return
    from AppKit import (  # pylint: disable=import-outside-toplevel,no-name-in-module,import-error
        NSApplication,
        NSImage,
    )
    from Foundation import (  # pylint: disable=import-outside-toplevel,no-name-in-module,import-error
        NSBundle,
        NSProcessInfo,
        NSUserDefaults,
    )

    # Cocoa otherwise opens the Python launcher and option values as documents.
    # This registration is process-local; Cocoa requires the string "NO" here.
    NSUserDefaults.standardUserDefaults().registerDefaults_({COCOA_OPEN_ARGUMENTS_DEFAULT: "NO"})
    metadata = application_metadata()
    bundle = NSBundle.mainBundle()
    info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
    info["CFBundleName"] = metadata.name
    info["CFBundleDisplayName"] = metadata.name
    info["CFBundleShortVersionString"] = metadata.version
    info["CFBundleVersion"] = metadata.version
    info["NSHumanReadableCopyright"] = metadata.copyright
    NSProcessInfo.processInfo().setProcessName_(metadata.name)
    icon = NSImage.alloc().initWithContentsOfFile_(str(application_icon_path()))
    if icon is None:
        icon = NSImage.imageWithSystemSymbolName_accessibilityDescription_("archivebox", metadata.name)
    if icon is not None:
        NSApplication.sharedApplication().setApplicationIconImage_(icon)
    install_file_drag()


def install_macos_document_events(application: PyWebViewApplication) -> None:
    """Extend pywebview's delegate without replacing its quit/ingest safeguards."""
    if sys.platform != "darwin":
        return
    from webview.platforms.cocoa import BrowserView  # pylint: disable=import-outside-toplevel

    install_file_drag()

    class MailArchiverDelegate(BrowserView.AppDelegate):
        def applicationShouldTerminate_(self, app):
            import AppKit  # pylint: disable=import-outside-toplevel,import-error
            import objc  # pylint: disable=import-outside-toplevel,import-error
            AppHelper = import_module("PyObjCTools.AppHelper")

            if application._quitting:
                return AppKit.NSTerminateLater
            if not any(document.ingest_job for document in application.controller.documents()):
                application._quitting = True
                decision = objc.super(MailArchiverDelegate, self).applicationShouldTerminate_(app)
                if decision == AppKit.NSTerminateCancel:
                    application._quitting = False
                return decision
            if macos_alert("Stop importing and quit?", QUIT_IMPORT_MESSAGE,
                           ("Cancel", "Stop Import and Quit"), body_width=IMPORT_CONFIRMATION_WIDTH) != 1:
                return AppKit.NSTerminateCancel
            jobs = application.stop_imports_for_quit()

            def finish_quit():
                for job in jobs:
                    job.finished.wait()
                AppHelper.callAfter(app.replyToApplicationShouldTerminate_, True)

            Thread(target=finish_quit, name="mailarchiver-quit", daemon=True).start()
            return AppKit.NSTerminateLater

        def application_openFiles_(self, sender, filenames):
            paths = tuple(Path(str(filename)) for filename in filenames)

            def open_documents():
                AppHelper = import_module("PyObjCTools.AppHelper")
                try:
                    errors = application.handle_open_documents(paths)
                    AppHelper.callAfter(sender.replyToOpenOrPrint_, 1 if errors else 0)
                except Exception as error:  # pylint: disable=broad-exception-caught
                    application.add_notice("error", f"Could not open archive: {error}")
                    AppHelper.callAfter(sender.replyToOpenOrPrint_, 1)

            Thread(target=open_documents, name="mailarchiver-open-documents", daemon=True).start()

        def showMailArchiverAbout_(self, _sender):
            Thread(target=application.create_about_window, name="mailarchiver-about", daemon=True).start()

        def applicationShouldHandleReopen_hasVisibleWindows_(self, _sender, _visible):
            if macos_option_pressed():
                Thread(target=application.show_setup, name="mailarchiver-setup", daemon=True).start()
            return True

    setattr(BrowserView, "AppDelegate", MailArchiverDelegate)


def macos_option_pressed() -> bool:
    """Read the launch/reopen modifier without requiring accessibility permission."""
    if sys.platform != "darwin":
        return False
    appkit = import_module("AppKit")
    return bool(appkit.NSEvent.modifierFlags() & appkit.NSEventModifierFlagOption)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only graphical search of a mailarchiver archive.")
    launch = parser.add_mutually_exclusive_group()
    launch.add_argument("--archive", type=Path, help="directory containing archive.sqlite3 and search.sqlite3")
    launch.add_argument("--new", action="store_true", help="show the three-step setup without reopening the last archive")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--smoke-html-find", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--smoke-report", type=Path, help=argparse.SUPPRESS)
    return parser


def main() -> int:
    if "--self-test" in sys.argv[1:] or "--self-test-gui" in sys.argv[1:]:
        from .self_test import main as test_main  # pylint: disable=import-outside-toplevel
        return test_main()
    args = build_parser().parse_args()
    if args.smoke_test != (args.smoke_report is not None):
        raise SystemExit("--smoke-test and --smoke-report must be used together")
    if args.smoke_html_find and not args.smoke_test:
        raise SystemExit("--smoke-html-find requires --smoke-test")
    smoke = NativeSmokeController(args.smoke_report) if args.smoke_report else None
    if smoke:
        smoke.start_watchdog()
        smoke.mark("configuring-application")
    new_setup = args.new or (not smoke and macos_option_pressed())
    configure_macos_application()
    if smoke:
        smoke.mark("application-configured")
    archive_value = os.environ.get("MAIL_ARCHIVE_DIR")
    archive = args.archive or (Path(archive_value) if archive_value else None)
    asset_server = LoopbackAssetServer(GUI_DIRECTORY)
    application: PyWebViewApplication | None = None
    if smoke:
        if archive is None or not _is_archive(archive):
            raise SystemExit("mailsearch-gui: a valid --archive is required for a smoke test")
        api = GuiApi(archive)
        bridge = NativeSmokeApi(api, smoke)
        initial_status = api.status()
        parameters = [("native-smoke", "1")]
        if args.smoke_html_find:
            parameters.append(("native-html-find-smoke", "1"))
        window = webview.create_window(
            _window_title(archive, int(initial_status["message_count"])),
            asset_server.url("index.html", parameters),
            js_api=bridge,
            width=1400,
            height=900,
            min_size=(900, 560),
            hidden=not args.smoke_html_find,
            text_select=True,
            draggable=True,
        )
        if window is None:
            raise RuntimeError("pywebview failed to create a window")
        api.set_window(window)
        window.events.closed += api.close
        smoke.bind_window(window)
        window.events.loaded += lambda *_args: smoke.page_loaded()
    else:
        controller = ApplicationController()
        application = PyWebViewApplication(controller, asset_server)
        install_macos_document_events(application)
        startup = controller.startup((archive,) if archive is not None else (), new=new_setup)
        prompt_for_archive = any(
            controller.document(session.document_id).descriptor.untitled
            for session in startup.windows
        )
        for error in startup.errors:
            print(f"mailsearch-gui: {error}", file=sys.stderr)
            application.add_notice("error", error)
        application.create_about_window(hidden=True)
        if prompt_for_archive:
            application.show_setup()
        for session in startup.windows:
            if controller.document(session.document_id).descriptor.untitled:
                controller.close_window(session.window_id)
            else:
                application.create_search_window(session)
    webview.settings["ALLOW_FILE_URLS"] = False
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    if smoke:
        smoke.mark("event-loop-starting")
    try:
        webview.start(
            http_server=False,
            private_mode=True,
            menu=application.menu() if application is not None else [],
        )
    finally:
        if application is not None:
            application.shutdown()
        else:
            asset_server.close()
    if smoke:
        report = smoke.event_loop_returned()
        print("GUI bridge smoke test passed" if report.passed else f"GUI bridge smoke test failed: {report.error}")
        return 0 if report.passed else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
