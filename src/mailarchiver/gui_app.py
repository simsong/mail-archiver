"""Expose the read-only archive services through a macOS-first pywebview shell."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from threading import Event, Lock, RLock, Thread
from typing import Any, Literal
from urllib.request import Request, urlopen
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

import webview
from pydantic import BaseModel, Field
from webview.menu import MenuAction

from .application import (
    ApplicationController,
    ArchiveDocument,
    IngestJob,
    SearchWindow,
)
from .__main__ import IngestRequest, run_ingest
from .configuration import GuiConfiguration, application_configuration
from .gui_service import (
    MessageView,
    MessagePreview,
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
    searchable_message_count,
    search_page,
    search_suggestions,
    write_attachment,
    write_message,
    write_messages_zip,
)
from .ingest_status import IngestHistory, IngestStatus, latest_ingest_status, read_ingest_history
from .loopback import LoopbackAssetServer
from .mailbox_tree import FilterSet, FilterSetStore, MailboxSelection, mailbox_tree
from .writer_lock import ArchiveBusyError, WriterLease

GUI_DIRECTORY = Path(__file__).parents[2] / "gui"
E2E_DRIVER = Path(__file__).parents[2] / "e2e_tests" / "gui_driver.js"
DEFAULT_PAGE_SIZE = 100
APPLICATION_NAME = "Mail Archiver"
APPLICATION_ICON = GUI_DIRECTORY / "icons" / "rainbow-post-192.png"
EXTERNAL_LINK_SCHEMES = frozenset({"http", "https", "mailto"})
INTERNET_CHECK_URL = "https://www.example.com/"
INTERNET_CHECK_INTERVAL_SECONDS = 30.0


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

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
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
    configuration: GuiConfiguration
    notices: list[ApplicationNotice] = Field(default_factory=list)


class GuiIngestOverview(BaseModel):
    status: IngestStatus | None = None


class DragExport(BaseModel):
    filename: str
    url: str
    content_type: str


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
                    detail = f"Connected ({response.status})" if online else f"HTTP {response.status}"
            except OSError as error:
                online = False
                detail = f"Offline: {error}"
            with self._lock:
                self._status = InternetStatus(
                    online=online,
                    checked_at=datetime.now(timezone.utc),
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


class IngestWindowApi:
    """Read-only bridge for the independent ingest-history window."""

    def __init__(self, archive: Path | None) -> None:
        self.archive = archive
        self.window: Any = None

    def set_window(self, window: Any) -> None:
        self.window = window

    def history(self) -> dict[str, object]:
        if self.archive is None:
            return IngestHistory(statuses=[], errors=[]).model_dump(mode="json")
        return read_ingest_history(self.archive).model_dump(mode="json")

    def close(self, *_args: object) -> None:
        self.window = None


class AboutApi:
    """Read-only bridge for persistent application health and activity."""

    def __init__(self, application: "PyWebViewApplication") -> None:
        self.application = application

    def status(self) -> dict[str, object]:
        return self.application.about_status().model_dump(mode="json")


class GuiApi:
    """Narrow API exposed to one webview window."""

    def __init__(
        self,
        archive: Path | None,
        temporary_directory: Path | None = None,
        e2e_directory: Path | None = None,
        preferences_file: Path | None = None,
        *,
        application: "PyWebViewApplication | None" = None,
        document: ArchiveDocument | None = None,
        search_window: SearchWindow | None = None,
    ) -> None:
        self.application = application
        self.document = document
        self.search_window = search_window
        self.archive = document.path if document is not None else archive
        self.window: Any = None
        self._temporary = tempfile.TemporaryDirectory(prefix="mailarchive-gui-") if temporary_directory is None else None
        self.temporary_directory = Path(self._temporary.name) if self._temporary else temporary_directory
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
        self._tree_cache: dict[bool, list[dict[str, object]]] = {}
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

    def status(self, *, opened_in_new_window: bool = False) -> dict[str, object]:
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
        ).model_dump()

    def activate(self) -> bool:
        """Route subsequent native actions through this window's current document."""
        if self.application is not None and self.search_window is not None:
            self.application.activate_window(self.search_window.window_id)
        return True

    def ingest_overview(self) -> dict[str, object]:
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
                "Mail Archiver — Ingests",
                str(GUI_DIRECTORY / f"ingests.html{selected}"),
                js_api=api,
                width=1050,
                height=700,
                min_size=(720, 440),
                text_select=True,
            )
            api.set_window(window)
            self._ingest_window_api = api

            def closed(*_args: object) -> None:
                api.close()
                with self._ingest_window_lock:
                    if self._ingest_window_api is api:
                        self._ingest_window_api = None

            window.events.closed += closed
        return True

    def choose_archive(self) -> dict[str, object]:
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
    ) -> dict[str, object]:
        page = search_page(
            self._archive(), query, offset, limit, sort_by, direction,
            search_attachments, mailbox_selections,
        )
        if self.search_window is not None:
            self.search_window.query = query
            self.search_window.sort_by = sort_by
            self.search_window.sort_direction = direction
            self.search_window.mailbox_selections = list(mailbox_selections or ())
        return page.model_dump(mode="json")

    def search_count(
        self,
        query: str,
        search_attachments: bool = False,
        mailbox_selections: list[str] | None = None,
    ) -> dict[str, object]:
        return search_count(
            self._archive(), query, search_attachments, mailbox_selections
        ).model_dump(mode="json")

    def suggestions(self, query: str, limit: int = 20) -> dict[str, object]:
        return search_suggestions(self._archive(), query, limit).model_dump(mode="json")

    def mailbox_tree(self, show_volumes: bool = False) -> list[dict[str, object]]:
        if show_volumes not in self._tree_cache:
            self._tree_cache[show_volumes] = [
                node.model_dump(mode="json") for node in mailbox_tree(self._archive(), show_volumes)
            ]
        return self._tree_cache[show_volumes]

    def saved_filter_sets(self) -> dict[str, object]:
        return self.filter_sets.read().model_dump(mode="json")

    def save_filter_set(self, name: str, show_volumes: bool, selections: list[str]) -> dict[str, object]:
        for token in selections:
            MailboxSelection.from_token(token)
        return self.filter_sets.save(
            FilterSet(name=name, show_volumes=show_volumes, selections=selections)
        ).model_dump(mode="json")

    def rename_filter_set(self, old_name: str, new_name: str) -> dict[str, object]:
        return self.filter_sets.rename(old_name, new_name).model_dump(mode="json")

    def delete_filter_set(self, name: str) -> dict[str, object]:
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

    def take_previews(self, message_pks: list[int]) -> dict[str, object]:
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
            with self._preview_lock:
                if generation == self._preview_generation:
                    self._preview_error = f"{type(error).__name__}: {error}"
        finally:
            with self._preview_lock:
                if generation == self._preview_generation:
                    self._preview_pending.difference_update(message_pks)

    def message(self, message_pk: int) -> dict[str, object]:
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
        try:
            import AppKit  # pylint: disable=import-error,import-outside-toplevel
            from Foundation import NSURL  # pylint: disable=import-error,import-outside-toplevel,no-name-in-module
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
        try:
            import AppKit  # pylint: disable=import-error,import-outside-toplevel
            from Foundation import NSURL  # pylint: disable=import-error,import-outside-toplevel,no-name-in-module
        except ImportError as error:
            raise ValueError("opening links requires macOS with PyObjC installed") from error

        url = NSURL.URLWithString_(destination)
        if url is None or not AppKit.NSWorkspace.sharedWorkspace().openURL_(url):
            raise ValueError("could not open external link")
        return destination

    def part(self, message_pk: int, part_id: int, allow_remote: bool = False) -> dict[str, object]:
        return render_part(self._archive(), message_pk, part_id, allow_remote).model_dump(mode="json")

    def attachment(self, message_pk: int, part_id: int) -> dict[str, object]:
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
        destination = Path(selected[0])
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
        destination = Path(selected[0])
        write_attachment(self._archive(), message_pk, part_id, destination)
        return str(destination)

    def prepare_drag(self, message_pks: list[int]) -> dict[str, str]:
        """Prepare one explicit Finder drag without proactively exporting mail."""
        unique = list(dict.fromkeys(message_pks))
        if not unique:
            raise ValueError("select at least one message to drag")
        archive = self._archive()
        if len(unique) == 1:
            view = describe_message(archive, unique[0])
            destination = self.temporary_directory / export_filename(view)
            write_message(archive, unique[0], destination)
            return DragExport(
                filename=destination.name, url=destination.as_uri(), content_type="message/rfc822"
            ).model_dump()
        digest = hashlib.sha256(",".join(map(str, sorted(unique))).encode()).hexdigest()[:12]
        destination = self.temporary_directory / f"messages-{digest}" / f"Mail Archiver Messages ({len(unique)}).zip"
        write_messages_zip(archive, unique, destination)
        return DragExport(
            filename=destination.name, url=destination.as_uri(), content_type="application/zip"
        ).model_dump()

    def open_attachment(self, message_pk: int, part_id: int, confirmed: bool = False) -> dict[str, object]:
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
            js_api=child_api,
            width=900,
            height=760,
            min_size=(560, 420),
            text_select=True,
            draggable=True,
        )
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

    def status(self) -> dict[str, object]:
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
    ) -> dict[str, object]:
        return self._api.search(
            query, offset, sort_by, direction, search_attachments, mailbox_selections, limit
        )

    def native_smoke_complete(self, passed: bool, error: str | None = None) -> bool:
        self._controller.complete(passed, error)
        return True


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
        self._native_search_ids: dict[str, str] = {}
        self._native_child_ids: dict[str, str] = {}
        self._about_api = AboutApi(self)
        self._about_window: Any = None
        self._notices: list[ApplicationNotice] = []
        self._connectivity: ConnectivityMonitor | None = None
        self._lock = RLock()

    def asset_url(
        self, asset: str, parameters: list[tuple[str, str]] | None = None
    ) -> str:
        if self.asset_server is not None:
            return self.asset_server.url(asset, parameters)
        suffix = f"?{urlencode(parameters)}" if parameters else ""
        return f"{GUI_DIRECTORY / asset}{suffix}"

    def create_about_window(self) -> None:
        """Create the health window that remains present for the application lifetime."""
        if self._connectivity is None:
            self._connectivity = ConnectivityMonitor()
        if self._about_window is not None:
            self._about_window.restore()
            self._about_window.show()
            return
        window = webview.create_window(
            f"About {APPLICATION_NAME}",
            self.asset_url("about.html"),
            js_api=self._about_api,
            width=620,
            height=620,
            min_size=(480, 420),
            text_select=True,
            menu=self.menu(),
        )
        self._about_window = window

        def closed(*_args: object) -> None:
            self._about_window = None
            if self._apis or self._ingest_apis:
                self.create_about_window()

        window.events.closed += closed
        self._refresh_menus()

    def add_notice(self, severity: Literal["information", "warning", "error"], message: str) -> None:
        notice = ApplicationNotice(severity=severity, message=message)
        with self._lock:
            if self._notices and self._notices[-1].severity == severity and self._notices[-1].message == message:
                return
            self._notices.append(notice)
            self._notices = self._notices[-100:]
        for api in self._search_apis():
            if api.window is not None:
                api.window.run_js(f"window.mailArchiverNotice?.({json.dumps(message)});")

    def notices(self) -> list[ApplicationNotice]:
        with self._lock:
            return [notice.model_copy(deep=True) for notice in self._notices]

    def about_status(self) -> AboutStatus:
        documents = self.controller.documents()
        disk_path = next((document.path for document in documents if document.path), Path.home())
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
            js_api=api,
            width=session.geometry.width,
            height=session.geometry.height,
            x=session.geometry.x,
            y=session.geometry.y,
            min_size=(900, 560),
            text_select=True,
            draggable=True,
            menu=self.menu(),
        )
        api.set_window(window)
        with self._lock:
            self._apis[session.window_id] = api
            self._native_search_ids[window.uid] = session.window_id
        window.events.shown += lambda *_args: self.activate_window(session.window_id)
        window.events.restored += lambda *_args: self.activate_window(session.window_id)
        window.events.closing += lambda *_args: self.controller.can_close_window(session.window_id)
        window.events.closed += lambda *_args: self._close_window(session.window_id)
        self._refresh_menus()
        return api

    def activate_window(self, window_id: str) -> None:
        self.controller.activate_window(window_id)
        self._refresh_menus()

    def active_api(self) -> GuiApi | None:
        native = webview.active_window()
        if native is not None:
            with self._lock:
                window_id = self._native_search_ids.get(native.uid)
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
        return self._create_new_document(anchor) is not None

    def _create_new_document(self, anchor: Any) -> GuiApi | None:
        selected = anchor.create_file_dialog(
            webview.FileDialog.SAVE,
            directory=str(Path.home()),
            save_filename="Untitled.mailarchive",
            file_types=("Mail Archiver archive (*.mailarchive)",),
        )
        if not selected:
            return None
        destination = Path(selected[0])
        if destination.suffix.casefold() != ".mailarchive":
            destination = destination.with_suffix(".mailarchive")
        try:
            document = self.controller.create_document(destination)
        except (OSError, ValueError) as error:
            self.add_notice("error", f"Could not create archive: {error}")
            return None
        return self.create_search_window(self.controller.new_search_window(document))

    def prompt_for_startup_archive(self) -> None:
        """Offer destination and import when startup had no usable saved archive."""
        untitled = next(
            (
                api
                for api in self._search_apis()
                if api.document is not None and api.document.descriptor.untitled
            ),
            None,
        )
        if untitled is None or untitled.window is None:
            return
        created = self._create_new_document(untitled.window)
        if created is None:
            return
        untitled.window.destroy()
        if created.search_window is not None:
            self.controller.activate_window(created.search_window.window_id)
        self._import_document(created)

    def new_search_window(self) -> bool:
        api = self.active_api()
        if api is None or api.document is None:
            return False
        self.create_search_window(self.controller.new_search_window(api.document))
        return True

    def import_active_document(self) -> bool:
        """Collect a supported local source and start typed ingest off the webview thread."""
        api = self.active_api()
        return self._import_document(api) if api is not None else False

    def _import_document(self, api: GuiApi) -> bool:
        if api.window is None or api.document is None or api.document.path is None:
            return False
        if api.search_window is not None:
            self.controller.activate_window(api.search_window.window_id)
        try:
            document = self.controller.import_document()
        except (ArchiveBusyError, ValueError) as error:
            self.add_notice("warning", str(error))
            return False
        choose_folders = api.window.create_confirmation_dialog(
            "Choose Import Source Type",
            "Choose OK to select one or more source folders. Choose Cancel to select one or more source files.",
        )
        selected_sources = api.window.create_file_dialog(
            webview.FileDialog.FOLDER if choose_folders else webview.FileDialog.OPEN,
            directory=str(document.display_path.parent if document.display_path else Path.home()),
            allow_multiple=True,
            file_types=() if choose_folders else ("Mail source files (*.*)",),
        )
        if not selected_sources:
            return False
        selected_owners = api.window.create_file_dialog(
            webview.FileDialog.OPEN,
            directory=str(Path.home()),
            file_types=("Owner names text file (*.txt)", "All files (*.*)"),
        )
        if not selected_owners:
            return False
        roots = [Path(path) for path in selected_sources]
        owner_names = Path(selected_owners[0])
        summary = "\n".join(str(root) for root in roots)
        if not api.window.create_confirmation_dialog(
            "Import Mail",
            f"Import these read-only sources into {document.display_path}?\n\n{summary}\n\n"
            f"Sent-mail owner names: {owner_names}\n\nClamAV must be installed and available.",
        ):
            return False
        return self.start_import(api, roots, owner_names)

    def start_import(self, api: GuiApi, roots: list[Path], owner_names: Path) -> bool:
        """Acquire both ingest layers before launching the shared service."""
        document = api.document
        session = api.search_window
        if document is None or document.path is None or session is None:
            return False
        operation_id = uuid4().hex
        try:
            lease = WriterLease.acquire(
                document.path,
                document.descriptor.identity,
                "GUI import",
                operation_id,
                application_metadata().version,
            )
            job = IngestJob(operation_id=operation_id, owner_window_id=session.window_id)
            self.controller.begin_ingest(document.descriptor.document_id, job, lease)
        except (OSError, ArchiveBusyError, ValueError) as error:
            if "lease" in locals():
                lease.release()
            self.add_notice("warning", f"Import did not start: {error}")
            return False
        self.add_notice("information", f"Import started for {document.display_path}")
        self._refresh_menus()
        request = IngestRequest(
            archive=document.path,
            owner_names_file=owner_names,
            roots=[str(root) for root in roots],
        )
        Thread(
            target=self._run_import,
            args=(document, operation_id, lease, request),
            name=f"mailarchiver-import-{operation_id[:8]}",
            daemon=False,
        ).start()
        return True

    def _run_import(
        self,
        document: ArchiveDocument,
        operation_id: str,
        lease: WriterLease,
        request: IngestRequest,
    ) -> None:
        error: BaseException | None = None
        try:
            run_ingest(request, lease)
        except BaseException as caught:  # pylint: disable=broad-exception-caught
            error = caught
        finally:
            try:
                refresh = self.controller.finish_ingest(
                    document.descriptor.document_id,
                    operation_id,
                    published=True,
                )
            except ValueError:
                refresh = ()
            self._refresh_search_windows(refresh)
            self._refresh_menus()
        if error is None:
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
        selected = anchor.create_file_dialog(
            webview.FileDialog.FOLDER,
            directory=str(
                api.document.display_path.parent
                if api and api.document and api.document.display_path
                else Path.home()
            ),
        )
        if not selected:
            return False
        try:
            self.open_document(Path(selected[0]))
            return True
        except (OSError, ValueError) as error:
            self.add_notice("error", f"Could not open archive: {error}")
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
        api = self.active_api()
        if api is not None and api.window is not None:
            api.window.restore()
            api.window.show()
            return []
        result = self.controller.startup()
        for session in result.windows:
            self.create_search_window(session)
        return result.errors

    def close_active_window(self) -> bool:
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
        api = self.active_api()
        return api.open_ingest_window() if api is not None else False

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
            api = IngestWindowApi(document.path)
            window = webview.create_window(
                f"{APPLICATION_NAME} — Ingests — {document.display_path}",
                self.asset_url(
                    "ingests.html",
                    [("status", status_id)] if status_id is not None else None,
                ),
                js_api=api,
                width=1050,
                height=700,
                min_size=(720, 440),
                text_select=True,
                menu=self.menu(),
            )
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
        windows.append(self._about_window)
        window = next((candidate for candidate in windows if candidate is not None and candidate.uid == uid), None)
        if window is None:
            return False
        window.restore()
        window.show()
        return True

    def menu(self) -> list[webview.Menu]:
        return application_menu(self)

    def window_menu_items(self) -> list[MenuAction]:
        items = []
        if self._about_window is not None:
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
        return items

    def shutdown(self) -> None:
        """Release non-document resources after the native event loop exits."""
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
        for window in windows:
            if window is not None:
                window.menu = menu
        self._refresh_macos_menu()

    def _refresh_macos_menu(self) -> None:
        """Refresh pywebview's process menu and its dynamic Close enabled state."""
        if sys.platform != "darwin":
            return
        active = webview.active_window()
        if active is None:
            return
        try:
            from PyObjCTools import AppHelper  # pylint: disable=import-error,import-outside-toplevel
            from webview.platforms.cocoa import BrowserView  # pylint: disable=import-error,import-outside-toplevel
        except ImportError:
            return

        def refresh() -> None:
            instance = BrowserView.instances.get(active.uid)
            if instance is None:
                return
            menu = instance._recreate_menus(active.menu)  # pylint: disable=protected-access
            BrowserView.app.setMainMenu_(menu)
            BrowserView.current_menu = active.menu
            file_item = menu.itemWithTitle_("File")
            close_item = file_item.submenu().itemWithTitle_("Close") if file_item else None
            if close_item is not None:
                with self._lock:
                    search_id = self._native_search_ids.get(active.uid)
                    child = active.uid in self._native_child_ids
                enabled = child or (
                    search_id is not None and self.controller.can_close_window(search_id)
                )
                close_item.setEnabled_(enabled)

        AppHelper.callAfter(refresh)


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


def application_menu(application: PyWebViewApplication) -> list[webview.Menu]:
    """Build native actions that resolve the active window at invocation time."""
    file_items: list[webview.Menu | MenuAction] = [
        MenuAction("New", application.new_document),
        MenuAction("Open…", application.open_archive_dialog),
    ]
    recent = [
        MenuAction(str(path), lambda selected=path: application.open_recent_document(selected))
        for path in application.controller.preferences.recent_archives
    ]
    if recent:
        file_items.append(webview.Menu("Open Recent", recent))
    file_items.extend(
        (
            MenuAction("New Search Window", application.new_search_window),
            MenuAction("Import…", application.import_active_document),
            MenuAction("Close", application.close_active_window),
        )
    )
    return [
        webview.Menu("File", file_items),
        webview.Menu(
            "Window",
            [MenuAction("Ingests", application.open_active_ingest_window), *application.window_menu_items()],
        ),
    ]


def configure_macos_application() -> None:
    """Replace the bare Python process identity before pywebview builds Cocoa menus."""
    if sys.platform != "darwin":
        return
    from AppKit import NSApplication, NSImage  # pylint: disable=import-outside-toplevel,no-name-in-module,import-error
    from Foundation import NSBundle, NSProcessInfo  # pylint: disable=import-outside-toplevel,no-name-in-module,import-error

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only graphical search of a mailarchiver archive.")
    parser.add_argument("--archive", type=Path, help="directory containing archive.sqlite3 and search.sqlite3")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--smoke-html-find", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--smoke-report", type=Path, help=argparse.SUPPRESS)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.smoke_test != (args.smoke_report is not None):
        raise SystemExit("--smoke-test and --smoke-report must be used together")
    if args.smoke_html_find and not args.smoke_test:
        raise SystemExit("--smoke-html-find requires --smoke-test")
    smoke = NativeSmokeController(args.smoke_report) if args.smoke_report else None
    if smoke:
        smoke.start_watchdog()
        smoke.mark("configuring-application")
    configure_macos_application()
    if smoke:
        smoke.mark("application-configured")
    archive_value = os.environ.get("MAIL_ARCHIVE_DIR")
    archive = args.archive or (Path(archive_value) if archive_value else None)
    asset_server = LoopbackAssetServer(GUI_DIRECTORY)
    application: PyWebViewApplication | None = None
    prompt_for_archive = False
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
        api.set_window(window)
        window.events.closed += api.close
        smoke.bind_window(window)
        window.events.loaded += lambda *_args: smoke.page_loaded()
    else:
        controller = ApplicationController()
        application = PyWebViewApplication(controller, asset_server)
        startup = controller.startup((archive,) if archive is not None else ())
        prompt_for_archive = any(
            controller.document(session.document_id).descriptor.untitled
            for session in startup.windows
        )
        for error in startup.errors:
            print(f"mailsearch-gui: {error}", file=sys.stderr)
            application.add_notice("error", error)
        application.create_about_window()
        for session in startup.windows:
            application.create_search_window(session)
    webview.settings["ALLOW_FILE_URLS"] = False
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    if smoke:
        smoke.mark("event-loop-starting")
    try:
        webview.start(
            func=(
                application.prompt_for_startup_archive
                if application is not None and prompt_for_archive
                else None
            ),
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
