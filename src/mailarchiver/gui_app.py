"""Expose the read-only archive services through a macOS-first pywebview shell."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import version
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any
from urllib.parse import urlencode

import webview
from pydantic import BaseModel
from webview.menu import MenuAction

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
    message_previews,
    render_part,
    safe_filename,
    searchable_message_count,
    search_page,
    search_suggestions,
    write_attachment,
    write_message,
)
from .ingest_status import IngestHistory, IngestStatus, latest_ingest_status, read_ingest_history
from .mailbox_tree import FilterSet, FilterSetStore, MailboxSelection, mailbox_tree

GUI_DIRECTORY = Path(__file__).parents[2] / "gui"
E2E_DRIVER = Path(__file__).parents[2] / "e2e_tests" / "gui_driver.js"
DEFAULT_PAGE_SIZE = 100
APPLICATION_NAME = "Mail Archiver"
APPLICATION_ICON = GUI_DIRECTORY / "icons" / "rainbow-post-192.png"


class ApplicationMetadata(BaseModel):
    name: str
    version: str
    copyright: str


class GuiStatus(BaseModel):
    archive: str | None
    ready: bool
    message_count: int = 0
    configuration: GuiConfiguration


class GuiIngestOverview(BaseModel):
    status: IngestStatus | None = None


class DragExport(BaseModel):
    filename: str
    url: str


class OpenResult(BaseModel):
    opened: bool = False
    requires_confirmation: bool = False
    filename: str


class GuiE2EClientResult(BaseModel):
    passed: bool
    checks: list[str]
    error: str | None = None


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


class GuiApi:
    """Narrow API exposed to one webview window."""

    def __init__(
        self,
        archive: Path | None,
        temporary_directory: Path | None = None,
        e2e_directory: Path | None = None,
        preferences_file: Path | None = None,
        native_smoke: NativeSmokeController | None = None,
    ) -> None:
        self.archive = archive
        self.window: Any = None
        self._temporary = tempfile.TemporaryDirectory(prefix="mailarchive-gui-") if temporary_directory is None else None
        self.temporary_directory = Path(self._temporary.name) if self._temporary else temporary_directory
        self.e2e_directory = e2e_directory
        self.native_smoke = native_smoke
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

    def status(self) -> dict[str, object]:
        ready = self.archive is not None and _is_archive(self.archive)
        if ready and self._message_count is None:
            self._message_count = searchable_message_count(self._archive())
        return GuiStatus(
            archive=str(self.archive) if self.archive else None,
            ready=ready,
            message_count=self._message_count or 0,
            configuration=application_configuration().gui,
        ).model_dump()

    def ingest_overview(self) -> dict[str, object]:
        status = latest_ingest_status(self._archive()) if self.archive and _is_archive(self.archive) else None
        return GuiIngestOverview(status=status).model_dump(mode="json")

    def open_ingest_window(self, status_id: str | None = None) -> bool:
        """Open one ingest browser, or focus and retarget the existing window."""
        if self.e2e_directory is not None:
            return True
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
        find_older: bool = False,
    ) -> dict[str, object]:
        return search_page(
            self._archive(), query, offset, DEFAULT_PAGE_SIZE, sort_by, direction,
            search_attachments, mailbox_selections, find_older,
        ).model_dump(mode="json")

    def suggestions(self, query: str, limit: int = 20) -> dict[str, object]:
        return search_suggestions(self._archive(), query, limit).model_dump(mode="json")

    def native_smoke_complete(self, passed: bool, error: str | None = None) -> bool:
        """Accept the one-shot result from the hidden native smoke page."""
        if self.native_smoke is None:
            raise RuntimeError("native smoke callback is unavailable")
        self.native_smoke.complete(passed, error)
        return True

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
        return describe_message(self._archive(), message_pk).model_dump(mode="json")

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

    def prepare_drag(self, message_pk: int) -> dict[str, str]:
        view = describe_message(self._archive(), message_pk)
        destination = self.temporary_directory / export_filename(view)
        write_message(self._archive(), message_pk, destination)
        return DragExport(filename=destination.name, url=destination.as_uri()).model_dump()

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


def _is_archive(path: Path) -> bool:
    return path.is_dir() and (path / "archive.sqlite3").is_file() and (path / "search.sqlite3").is_file()


def _window_title(archive: Path | None, message_count: int = 0) -> str:
    if archive is None:
        return APPLICATION_NAME
    return f"{APPLICATION_NAME} — {archive} ({message_count:,} messages)"


def application_metadata() -> ApplicationMetadata:
    """Return the identity shown by the native application menu and About panel."""
    return ApplicationMetadata(
        name=APPLICATION_NAME,
        version=version("mailarchiver"),
        copyright="Copyright © 2026 The Mail Archiver contributors.",
    )


def application_menu(api: GuiApi) -> list[webview.Menu]:
    """Build the native menu entry for the singleton ingest browser."""
    return [webview.Menu("Windows", [MenuAction("Ingest", api.open_ingest_window)])]


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
    parser.add_argument("--smoke-report", type=Path, help=argparse.SUPPRESS)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.smoke_test != (args.smoke_report is not None):
        raise SystemExit("--smoke-test and --smoke-report must be used together")
    smoke = NativeSmokeController(args.smoke_report) if args.smoke_report else None
    if smoke:
        smoke.start_watchdog()
        smoke.mark("configuring-application")
    configure_macos_application()
    if smoke:
        smoke.mark("application-configured")
    archive_value = os.environ.get("MAIL_ARCHIVE_DIR")
    archive = args.archive or (Path(archive_value) if archive_value else None)
    if archive is not None and not _is_archive(archive):
        raise SystemExit(f"mailsearch-gui: {archive} must contain archive.sqlite3 and search.sqlite3")
    api = GuiApi(archive, native_smoke=smoke)
    initial_status = api.status()
    url = str(GUI_DIRECTORY / "index.html")
    if smoke:
        url += "?native-smoke=1"
    window = webview.create_window(
        _window_title(archive, int(initial_status["message_count"])),
        url,
        js_api=api,
        width=1400,
        height=900,
        min_size=(900, 560),
        hidden=args.smoke_test,
        text_select=True,
        draggable=True,
    )
    api.set_window(window)
    window.events.closed += api.close
    if smoke:
        smoke.bind_window(window)
        window.events.loaded += lambda *_args: smoke.page_loaded()
    webview.settings["ALLOW_FILE_URLS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    if smoke:
        smoke.mark("event-loop-starting")
    webview.start(http_server=True, private_mode=True, menu=application_menu(api))
    if smoke:
        report = smoke.event_loop_returned()
        print("GUI bridge smoke test passed" if report.passed else f"GUI bridge smoke test failed: {report.error}")
        return 0 if report.passed else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
