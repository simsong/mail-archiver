"""Run canonical mail ingest, provenance review, reports, and FTS rebuilds."""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import mailbox
import queue
import re
import signal
import shutil
import sqlite3
import sys
import threading
import time
from collections import Counter, deque
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Literal, TypeVar
from urllib.parse import quote

from pydantic import BaseModel, Field
from tabulate import tabulate

from .archive_integrity import MailbagArchiveIntegrityControls
from .archive_path import add_archive_argument, require_archive
from .catalog import (
    UnsupportedSearchSchemaError,
    address_pk,
    create_catalog,
    create_search,
    owner_tokens,
)
from .ingest_status import (
    IngestCounts,
    IngestState,
    IngestStatus,
    IngestStatusFile,
    IngestWorkerStatus as WorkerProgress,
    STATUS_REFRESH_SECONDS,
    YearProgress,
    new_status_id,
)
from .layout import mbox_directory, mbox_path
from .message import ParsedMessage, parse_message
from .mbox import (
    DiskFullError,
    MboxLocation,
    PendingPublication,
    PublicationRecovery,
    add_message,
    clear_publication_journal,
    journal_publication,
    mailbox_name,
    read_verified_location,
    recover_publication,
)
from .scanner import ClamScanner, ClamScannerStartupError
from .search import (
    QUARANTINE_MAILBOX,
    SEARCH_CATEGORIES,
    PreparedSearchMessage,
    index_message_safely,
    prepare_search_message,
    write_prepared_search_message,
)
from .plugin_api import (
    ArchiveReference,
    IntegrityDecision,
    IntegrityEvidence,
    LoadedPlugin,
    MailContainer,
    MailObject,
    ProgressEvent,
    SkippedInput,
    SourceContainerMetadata,
    SourceSpec,
)
from .plugin_loader import PluginDiscoveryError, load_plugins
from .sources import (
    IncompleteAppleMailMessageError,
    LocalSourcePlugin,
    SourceFile,
    SourceInventory,
    local_hierarchy_path,
)
from .standalone_verify import semantic_bytes

DEFAULT_REPORT_TOP = 10
PROGRESS_REFRESH_SECONDS = 0.25
REFRESH_INDEX_DEFAULT_WORKERS = os.cpu_count() or 2
REFRESH_INDEX_MAX_IN_FLIGHT_BYTES = 256 * 1024 * 1024
CLAMAV_START_PHASE = "waiting for ClamAV startup"
DISCOVERY_PHASE = "discovering sources"
TOP_LINE_STYLE = "\x1b[37;44m"
ANSI_RESET = "\x1b[0m"
WorkerItem = TypeVar("WorkerItem")


def positive_integer(value: str) -> int:
    """Parse a command-line integer that must be greater than zero."""
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def nonnegative_integer(value: str) -> int:
    """Parse a command-line integer that may be zero but not negative."""
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be zero or positive")
    return number


class PendingScan(BaseModel):
    source: MailObject
    parsed: ParsedMessage


class ContainerWork(BaseModel):
    plugin: LoadedPlugin
    container: MailContainer


class ContainerIntegrityResult(BaseModel):
    decision: IntegrityDecision
    evidence: tuple[IntegrityEvidence, ...]


class SourceOriginIdentity(BaseModel):
    plugin_kind: str
    source_id: str


class SourceOriginMetadata(BaseModel):
    plugin_kind: str
    volume_label: str


class ProgressUpdate(BaseModel):
    worker: int
    phase: str | None = None
    path: str | None = None
    bytes_done: int | None = None
    bytes_total: int | None = None
    activity_done: int | None = None
    activity_total: int | None = None
    activity_unit: str | None = None
    message_date: datetime | None = None
    disposition: str | None = None
    file_complete: bool = False
    notice_kind: Literal["skipped input", "skipped unchanged"] | None = None
    notice_path: str | None = None
    notice_reason: str | None = None


class ProgressState(BaseModel):
    started_at: datetime
    started_monotonic: float
    processed: int = 0
    files_processed: int = 0
    earliest_date: datetime | None = None
    latest_date: datetime | None = None
    current_year: int | None = None
    current_year_messages: int = 0
    source_files_total: int = 0
    source_bytes_completed: int = 0
    source_bytes_total: int = 0
    skipped_files_total: int = 0
    inventory_complete: bool = False
    byte_progress_started_monotonic: float | None = None
    peak_active_files: int = 0
    workers: list[WorkerProgress] = Field(default_factory=list)
    counts: IngestCounts = Field(default_factory=IngestCounts)
    years: list[YearProgress] = Field(default_factory=list)


class OverallProgress(BaseModel):
    bytes_done: int
    bytes_total: int
    percent: float
    eta: str


class RefreshIndexProgress(BaseModel):
    """A single, observable phase of the disposable-index rebuild."""

    phase: str
    total: int
    unit: str
    started_monotonic: float = Field(default_factory=time.monotonic)
    completed: int = 0


def refresh_index_line(progress: RefreshIndexProgress, now: float, width: int = 24) -> str:
    """Render bounded rebuild progress without relying on terminal controls."""
    completed = min(progress.completed, progress.total)
    percent = 100 if progress.total == 0 else 100 * completed / progress.total
    elapsed = max(now - progress.started_monotonic, 0.001)
    if completed >= progress.total:
        eta = "0s"
    elif completed == 0:
        eta = "calculating"
    else:
        eta = formatted_duration((progress.total - completed) * elapsed / completed)
    filled = round(width * percent / 100)
    bar = "#" * filled + "-" * (width - filled)
    rate = completed / elapsed
    return (
        f"{progress.phase}: [{bar}] {percent:5.1f}% {completed:,}/{progress.total:,} "
        f"{progress.unit}  {rate:,.0f} {progress.unit}/s  ETA {eta}"
    )


class RefreshIndexReporter:
    """Show low-noise, terminal-friendly progress for a refresh-index run."""

    def __init__(self, phase: str, total: int, unit: str) -> None:
        self.progress = RefreshIndexProgress(phase=phase, total=total, unit=unit)
        self.tty = sys.stderr.isatty()
        self.last_display_monotonic: float | None = None

    def display(self, *, force: bool = False) -> None:
        now = time.monotonic()
        interval = PROGRESS_REFRESH_SECONDS if self.tty else 5.0
        if not force and self.last_display_monotonic is not None and now - self.last_display_monotonic < interval:
            return
        line = refresh_index_line(self.progress, now)
        if self.tty:
            sys.stderr.write(f"\r\x1b[2K{line}")
        else:
            print(line, file=sys.stderr)
        sys.stderr.flush()
        self.last_display_monotonic = now

    def advance(self, amount: int = 1) -> None:
        self.progress.completed += amount
        self.display()

    def finish(self) -> None:
        self.progress.completed = self.progress.total
        self.display(force=True)
        if self.tty:
            sys.stderr.write("\n")
            sys.stderr.flush()


class RefreshIndexInterrupted(KeyboardInterrupt):
    """Report whether Ctrl-C arrived before or after atomic search publication."""

    def __init__(self, published: bool) -> None:
        super().__init__()
        self.published = published


class RefreshIndexWork(BaseModel):
    """One immutable verified-MBOX read assigned to a refresh worker."""

    message_pk: int
    sha256: str
    date_utc: str
    filename: str
    location: MboxLocation


class PreparedRefreshIndexMessage(BaseModel):
    """The ordered worker result consumed by the sole SQLite writer."""

    message_pk: int
    date_utc: str
    indexed: PreparedSearchMessage


class RefreshIndexPublication:
    """Make the final paired derived-data publication uninterruptible."""

    def __enter__(self) -> None:
        self.previous_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)

    def __exit__(self, exception_type: type[BaseException] | None, _value: BaseException | None, _traceback: TracebackType | None) -> None:
        signal.signal(signal.SIGINT, self.previous_handler)


def formatted_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{int(amount)} B" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    raise AssertionError("unreachable")


def formatted_duration(seconds: float) -> str:
    remaining = max(0, math.ceil(seconds))
    hours, remaining = divmod(remaining, 3600)
    minutes, seconds = divmod(remaining, 60)
    fields = ([f"{hours}h"] if hours else []) + ([f"{minutes}m"] if hours or minutes else []) + [f"{seconds}s"]
    return " ".join(fields)


def safe_status_text(value: str) -> str:
    """Collapse untrusted plug-in status text to one bounded printable line."""
    return " ".join(value.split())[:80] or "working"


def overall_progress(state: ProgressState, now: float) -> OverallProgress:
    active_bytes = sum(min(worker.bytes_done, worker.bytes_total) for worker in state.workers)
    done = min(state.source_bytes_completed + active_bytes, state.source_bytes_total)
    total = state.source_bytes_total
    if not state.inventory_complete:
        percent = 0.0
    elif total == 0:
        percent = (
            100.0
            if state.source_files_total == 0
            else 100 * state.files_processed / state.source_files_total
        )
    else:
        percent = 100 * done / total
        if state.files_processed < state.source_files_total:
            percent = min(percent, 99.9)
    if not state.inventory_complete:
        eta = "calculating"
    elif total == 0:
        eta = "0s" if state.files_processed >= state.source_files_total else "finalizing"
    elif done == 0 or state.byte_progress_started_monotonic is None:
        eta = "calculating"
    elif done >= total:
        eta = "0s" if state.files_processed >= state.source_files_total else "finalizing"
    else:
        elapsed = max(now - state.byte_progress_started_monotonic, 0.001)
        eta = formatted_duration((total - done) * elapsed / done)
    return OverallProgress(bytes_done=done, bytes_total=total, percent=percent, eta=eta)


def overall_line(state: ProgressState, now: float) -> str:
    if not state.inventory_complete:
        return f"Overall: discovering  {state.source_files_total:,} files  {formatted_bytes(state.source_bytes_total)} found"
    progress = overall_progress(state, now)
    return (
        f"Overall: {progress.percent:5.1f}%  {formatted_bytes(progress.bytes_done)} / "
        f"{formatted_bytes(progress.bytes_total)}  Files {state.files_processed:,} / "
        f"{state.source_files_total:,}  ETA {progress.eta}"
    )


class ProgressReporter:
    """Receive worker updates and let the main thread render ingest status."""

    def __init__(
        self,
        worker_count: int = 1,
        *,
        status_file: IngestStatusFile | None = None,
        archive: Path | None = None,
        run_pk: int | None = None,
        source_roots: list[str] | None = None,
        started_at: datetime | None = None,
    ) -> None:
        self.state = ProgressState(
            started_at=started_at or datetime.now(timezone.utc),
            started_monotonic=time.monotonic(),
            workers=[WorkerProgress(worker=worker) for worker in range(1, worker_count + 1)],
        )
        self.updates: queue.SimpleQueue[ProgressUpdate] = queue.SimpleQueue()
        self.driver_thread = threading.get_ident()
        self.tty = sys.stderr.isatty()
        self.terminal_columns = max(shutil.get_terminal_size((120, 24)).columns, 20)
        self.rendered_lines = 0
        self.base_phase = "started"
        self.phase = "started"
        self.phase_started_monotonic = self.state.started_monotonic
        self.last_display_monotonic = self.state.started_monotonic
        self.notices: set[tuple[str, str, str]] = set()
        self.emitted_notices: set[tuple[str, str, str]] = set()
        self.status_file = status_file
        self.status_archive = archive
        self.status_run_pk = run_pk
        self.status_source_roots = source_roots or []
        self.status_write_error: str | None = None
        self.last_status_monotonic: float | None = None

    def start(self) -> None:
        self.display(self.phase)

    def set_phase(self, phase: str) -> None:
        self._assert_driver_thread()
        self.base_phase = phase
        self.phase = phase
        self.phase_started_monotonic = time.monotonic()
        self.display(phase)

    def record(self, parsed: ParsedMessage, source: MailObject) -> None:
        self._record_source_progress(source, datetime.fromisoformat(parsed.date_utc))

    def record_source(self, source: MailObject) -> None:
        self._record_source_progress(source)

    def _record_source_progress(self, source: MailObject, message_date: datetime | None = None) -> None:
        path = source.source.display_name
        if source.completed_bytes is not None or source.total_bytes is not None:
            self._send(
                "ingesting",
                path,
                source.completed_bytes or 0,
                source.total_bytes or 0,
                message_date=message_date,
            )
            return
        self._send_activity(
            "ingesting",
            path,
            source.completed_messages,
            source.total_messages,
            "messages",
            message_date=message_date,
        )

    def record_file(self, path: Path | str, bytes_done: int, bytes_total: int) -> None:
        self._send("checking", path, bytes_done, bytes_total)

    def record_worker(
        self,
        phase: str,
        path: Path | str,
        bytes_done: int,
        bytes_total: int,
    ) -> None:
        self._send(phase, path, bytes_done, bytes_total)

    def record_plugin_event(self, event: ProgressEvent, path: Path | str) -> None:
        """Render a typed plug-in event without letting plug-ins print directly."""
        phase = safe_status_text(event.phase)
        if event.unit == "bytes" and event.completed is not None and event.total is not None:
            self._send(phase, path, event.completed, event.total)
            return
        self._send_activity(phase, path, event.completed, event.total, event.unit)

    def record_inventory(self, file_count: int, byte_count: int) -> None:
        self._assert_driver_thread()
        self.state.source_files_total = file_count
        self.state.source_bytes_total = byte_count
        now = time.monotonic()
        if now - self.last_display_monotonic >= PROGRESS_REFRESH_SECONDS:
            self.display(DISCOVERY_PHASE)

    def finish_inventory(self, inventory: SourceInventory) -> None:
        self._assert_driver_thread()
        self.state.source_files_total = inventory.file_count
        self.state.source_bytes_total = inventory.byte_count
        self.state.skipped_files_total = inventory.skipped_file_count
        self.state.inventory_complete = True
        self._emit_notices({"skipped input"})
        self.display(DISCOVERY_PHASE)

    def completed_inventory(self) -> SourceInventory:
        self._drain_updates()
        return SourceInventory(
            file_count=self.state.files_processed,
            byte_count=self.state.source_bytes_completed,
            skipped_file_count=self.state.skipped_files_total,
        )

    def record_skipped_file(self, path: Path | str, reason: str) -> None:
        """Record one unrecognized discovery candidate for main-thread rendering."""
        self._assert_driver_thread()
        notice = ("skipped input", str(path), reason)
        if notice not in self.notices:
            self.notices.add(notice)
            self.state.counts.skipped_files += 1

    def record_unchanged_source(self, path: Path | str, reason: str) -> None:
        """Queue one integrity skip from a worker without printing from that worker."""
        self.updates.put(
            ProgressUpdate(
                worker=self._worker_number(),
                disposition="unchanged-source",
                notice_kind="skipped unchanged",
                notice_path=str(path),
                notice_reason=reason,
            )
        )

    def record_file_complete(self, path: Path | str, byte_count: int) -> None:
        self._send("idle", path, byte_count, byte_count, file_complete=True)

    def record_file_inactive(self, path: Path | str) -> None:
        self._send("idle", path, 0, 0)

    def record_disposition(self, disposition: str) -> None:
        self.updates.put(ProgressUpdate(worker=self._worker_number(), disposition=disposition))

    def _send(
        self,
        phase: str,
        path: Path | str,
        bytes_done: int,
        bytes_total: int,
        *,
        message_date: datetime | None = None,
        file_complete: bool = False,
    ) -> None:
        self.updates.put(
            ProgressUpdate(
                worker=self._worker_number(),
                phase=phase,
                path=str(path),
                bytes_done=bytes_done,
                bytes_total=bytes_total,
                message_date=message_date,
                file_complete=file_complete,
            )
        )

    def _send_activity(
        self,
        phase: str,
        path: Path | str,
        completed: int | None,
        total: int | None,
        unit: str | None,
        *,
        message_date: datetime | None = None,
    ) -> None:
        self.updates.put(
            ProgressUpdate(
                worker=self._worker_number(),
                phase=phase,
                path=str(path),
                activity_done=completed,
                activity_total=total,
                activity_unit=unit,
                message_date=message_date,
            )
        )

    @staticmethod
    def _worker_number() -> int:
        name = threading.current_thread().name
        match = re.fullmatch(r"mailfile_(\d+)", name)
        if match is None:
            raise RuntimeError(f"progress update sent outside a mailfile worker: {name}")
        return int(match.group(1)) + 1

    def _assert_driver_thread(self) -> None:
        if threading.get_ident() != self.driver_thread:
            raise RuntimeError("only the main status driver may render progress")

    def _drain_updates(self) -> None:
        self._assert_driver_thread()
        while True:
            try:
                update = self.updates.get_nowait()
            except queue.Empty:
                break
            worker = self.state.workers[update.worker - 1]
            if update.file_complete:
                self.state.files_processed += 1
                self.state.source_bytes_completed += update.bytes_total or 0
                worker.last_path = update.path or worker.path
                worker.files_processed += 1
                worker.phase = "idle"
                worker.path = None
                worker.bytes_done = 0
                worker.bytes_total = 0
                worker.activity_done = None
                worker.activity_total = None
                worker.activity_unit = None
            elif update.phase == "idle":
                worker.phase = "idle"
                worker.path = None
                worker.bytes_done = 0
                worker.bytes_total = 0
                worker.activity_done = None
                worker.activity_total = None
                worker.activity_unit = None
            else:
                if update.phase is not None:
                    worker.phase = update.phase
                if update.path is not None and update.path != worker.path:
                    worker.path = update.path
                    worker.bytes_done = 0
                    worker.activity_done = None
                    worker.activity_total = None
                    worker.activity_unit = None
                if update.bytes_done is not None:
                    worker.activity_done = None
                    worker.activity_total = None
                    worker.activity_unit = None
                    worker.bytes_done = max(worker.bytes_done, update.bytes_done)
                if update.bytes_total is not None:
                    worker.bytes_total = update.bytes_total
                if worker.bytes_done > 0 and self.state.byte_progress_started_monotonic is None:
                    self.state.byte_progress_started_monotonic = time.monotonic()
                if update.activity_done is not None:
                    worker.activity_done = update.activity_done
                if update.activity_total is not None:
                    worker.activity_total = update.activity_total
                if update.activity_unit is not None:
                    worker.activity_unit = update.activity_unit
            if update.message_date is not None:
                self._record_message(update.message_date)
                worker.messages_processed += 1
            if update.disposition == "archived":
                self.state.counts.archived += 1
            elif update.disposition == "duplicate":
                self.state.counts.duplicates += 1
            elif update.disposition == "autosave-excluded":
                self.state.counts.autosaves += 1
            elif update.disposition == "source-metadata-excluded":
                self.state.counts.metadata_excluded += 1
            elif update.disposition == "infected":
                self.state.counts.infected += 1
            elif update.disposition == "unchanged-source":
                self.state.counts.unchanged_sources += 1
            if update.notice_kind is not None:
                assert update.notice_path is not None and update.notice_reason is not None
                self.notices.add((update.notice_kind, update.notice_path, update.notice_reason))
            active = sum(item.phase != "idle" for item in self.state.workers)
            self.state.peak_active_files = max(self.state.peak_active_files, active)

    def _record_message(self, date: datetime) -> None:
        self.state.processed += 1
        self.state.earliest_date = date if self.state.earliest_date is None else min(self.state.earliest_date, date)
        self.state.latest_date = date if self.state.latest_date is None else max(self.state.latest_date, date)
        progress = next((entry for entry in self.state.years if entry.year == date.year), None)
        if progress is None:
            progress = YearProgress(year=date.year)
            self.state.years.append(progress)
        progress.messages += 1
        self.state.current_year = date.year
        self.state.current_year_messages = progress.messages

    def refresh(self) -> None:
        self.display(None)

    def _emit_notices(self, kinds: set[str] | None = None) -> None:
        self._assert_driver_thread()
        selected = [
            notice
            for notice in self.notices - self.emitted_notices
            if kinds is None or notice[0] in kinds
        ]
        if not selected:
            return
        if self.tty and self.rendered_lines:
            sys.stderr.write("\n")
            self.rendered_lines = 0
        for kind, path, reason in sorted(selected):
            print(f"{kind}: {path} ({reason})", file=sys.stderr)
        self.emitted_notices.update(selected)

    def _worker_phase(self) -> str:
        phases = {worker.phase for worker in self.state.workers}
        if phases != {"idle"}:
            return "ingesting"
        return self.base_phase

    @staticmethod
    def _worker_line(worker: WorkerProgress, columns: int) -> str:
        prefix = f"Thread {worker.worker:>2}: [{worker.phase}]"
        if worker.phase == "idle" or worker.path is None:
            return prefix
        if worker.activity_unit is not None:
            amount = "" if worker.activity_done is None else f" {worker.activity_done:,}"
            total = "" if worker.activity_total is None else f"/{worker.activity_total:,}"
            suffix = f"{amount}{total} {worker.activity_unit}".rstrip()
        else:
            percent = 0 if worker.bytes_total == 0 else 100 * worker.bytes_done / worker.bytes_total
            suffix = f" ({percent:.1f}%)"
        available = max(columns - len(prefix) - len(suffix) - 2, 1)
        path = worker.path
        if len(path) > available:
            path = "…" if available == 1 else "…" + path[-(available - 1):]
        return f"{prefix} {path}{suffix}"

    @staticmethod
    def _fit(line: str, columns: int) -> str:
        if len(line) <= columns:
            return line
        return line[: max(columns - 1, 0)] + "…"

    def display(
        self,
        label: str | None,
        ingest_state: IngestState = "running",
        failure_detail: str | None = None,
    ) -> None:
        self._drain_updates()
        now = time.monotonic()
        self.last_display_monotonic = now
        display_label = label or self._worker_phase()
        if display_label != self.phase:
            self.phase = display_label
            self.phase_started_monotonic = now
        state = self.state.model_copy(deep=True)
        elapsed = max(now - state.started_monotonic, 0.001)
        phase_elapsed = max(now - self.phase_started_monotonic, 0.0)
        overall = overall_progress(state, now)
        dates = "none" if state.earliest_date is None else f"{state.earliest_date.date()}..{state.latest_date.date()}"
        year = "none" if state.current_year is None else str(state.current_year)
        if display_label == CLAMAV_START_PHASE:
            display_label = f"{CLAMAV_START_PHASE}: {phase_elapsed:.1f}s"
        active = sum(worker.phase != "idle" for worker in state.workers)
        if self.tty:
            top_line = self._fit(overall_line(state, now), self.terminal_columns).ljust(self.terminal_columns)
            lines = [
                f"{TOP_LINE_STYLE}{top_line}{ANSI_RESET}",
                f"mailarchiver ingest  [{display_label}]",
                f"Processed: {state.processed:,} messages in {state.files_processed:,} files  "
                f"Rate: {state.processed / elapsed:.2f} messages/s  Elapsed: {elapsed:.0f}s",
                f"Workers:   {active:,} active; peak {state.peak_active_files:,}; {len(state.workers):,} configured",
                *(self._worker_line(worker, self.terminal_columns) for worker in state.workers),
                f"Dates:     {dates}  Current year: {year} ({state.current_year_messages:,} messages)",
                f"Archived:  {state.counts.archived:,}  Seen/skipped: {state.counts.duplicates:,}  "
                f"Autosaved: {state.counts.autosaves:,}  Metadata: {state.counts.metadata_excluded:,}  "
                f"Infected: {state.counts.infected:,}  Files skipped: {state.counts.skipped_files:,}  "
                f"Unchanged: {state.counts.unchanged_sources:,}",
            ]
            lines = [lines[0], *(self._fit(line, self.terminal_columns) for line in lines[1:])]
            rewind = f"\x1b[{self.rendered_lines}A" if self.rendered_lines else ""
            sys.stderr.write(rewind + "\n".join(f"\r\x1b[2K{line}" for line in lines) + "\n")
            self.rendered_lines = len(lines)
        else:
            workers = " ".join(
                f"{worker.worker}:{worker.phase}:{Path(worker.path).name if worker.path else '-'}"
                for worker in state.workers
            )
            print(
                f"{display_label}: overall_bytes={overall.bytes_done} overall_total_bytes={overall.bytes_total} "
                f"overall_percent={overall.percent:.1f}% files_processed={state.files_processed} "
                f"files_total={state.source_files_total} eta={overall.eta.replace(' ', '')} "
                f"processed={state.processed} "
                f"active_workers={active} peak_workers={state.peak_active_files} "
                f"rate={state.processed / elapsed:.2f}/s "
                f"workers={workers} dates={dates} current_year={year} year_messages={state.current_year_messages} "
                f"archived={state.counts.archived} seen_skipped={state.counts.duplicates} "
                f"autosaved={state.counts.autosaves} metadata_excluded={state.counts.metadata_excluded} "
                f"infected={state.counts.infected} skipped_files={state.counts.skipped_files} "
                f"unchanged_sources={state.counts.unchanged_sources}",
                file=sys.stderr,
            )
        self._write_status(
            state,
            display_label,
            elapsed,
            phase_elapsed,
            overall,
            active,
            ingest_state,
            failure_detail,
        )
        sys.stderr.flush()

    def _write_status(
        self,
        state: ProgressState,
        phase: str,
        elapsed: float,
        phase_elapsed: float,
        overall: OverallProgress,
        active: int,
        ingest_state: IngestState,
        failure_detail: str | None,
    ) -> None:
        if self.status_file is None:
            return
        now = state.started_monotonic + elapsed
        if (
            ingest_state == "running"
            and self.last_status_monotonic is not None
            and now - self.last_status_monotonic < STATUS_REFRESH_SECONDS
        ):
            return
        assert self.status_archive is not None and self.status_run_pk is not None
        updated_at = datetime.now(timezone.utc)
        status = IngestStatus(
            status_id=self.status_file.path.stem,
            archive=str(self.status_archive.resolve()),
            run_pk=self.status_run_pk,
            process_id=os.getpid(),
            source_roots=self.status_source_roots,
            started_at=state.started_at,
            updated_at=updated_at,
            completed_at=None if ingest_state == "running" else updated_at,
            state=ingest_state,
            phase=phase,
            elapsed_seconds=elapsed,
            phase_elapsed_seconds=phase_elapsed,
            processed_messages=state.processed,
            message_rate=state.processed / elapsed,
            files_processed=state.files_processed,
            files_total=state.source_files_total,
            bytes_processed=overall.bytes_done,
            bytes_total=overall.bytes_total,
            percent=overall.percent,
            eta=overall.eta,
            earliest_date=state.earliest_date,
            latest_date=state.latest_date,
            current_year=state.current_year,
            current_year_messages=state.current_year_messages,
            active_workers=active,
            peak_workers=state.peak_active_files,
            configured_workers=len(state.workers),
            workers=[WorkerProgress.model_validate(worker) for worker in state.workers],
            counts=state.counts,
            years=state.years,
            failure_detail=failure_detail,
        )
        try:
            self.status_file.write(status)
            self.last_status_monotonic = now
        except OSError as error:
            self.status_write_error = f"cannot update {self.status_file.path}: {error}"
            self.status_file = None
            if self.tty and self.rendered_lines:
                sys.stderr.write("\n")
                self.rendered_lines = 0
            print(f"ingest status disabled: {self.status_write_error}", file=sys.stderr)

    def finish(self, status: IngestState, failure_detail: str | None = None) -> None:
        self._drain_updates()
        self._emit_notices()
        self.display(status, status, failure_detail)


def run_file_workers(
    items: Iterable[WorkerItem],
    worker_count: int,
    process: Callable[[WorkerItem], None],
    stop: threading.Event,
    status_driver: Callable[[], None],
    concurrency: Callable[[WorkerItem], tuple[str, int | None]] | None = None,
) -> None:
    """Run framework workers with optional source-declared per-key concurrency limits."""
    iterator = iter(items)
    pool = ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="mailfile")
    pending: dict[Future[None], str | None] = {}
    active: Counter[str] = Counter()
    waiting_items: deque[tuple[WorkerItem, str | None, int | None]] = deque()
    lookahead = max(worker_count * 4, worker_count)
    exhausted = False
    discovery_error: tuple[BaseException, TracebackType | None] | None = None
    try:
        while pending or waiting_items or not exhausted:
            if stop.is_set():
                exhausted = True
                waiting_items.clear()
            while not exhausted and len(waiting_items) < lookahead:
                try:
                    item = next(iterator)
                except StopIteration:
                    exhausted = True
                except BaseException as error:
                    discovery_error = (error, error.__traceback__)
                    exhausted = True
                else:
                    key = None
                    limit = None
                    if concurrency is not None:
                        key, limit = concurrency(item)
                        if limit is not None and limit < 1:
                            raise ValueError(f"invalid concurrency limit for {key}: {limit}")
                    waiting_items.append((item, key, limit))
            while len(pending) < worker_count and waiting_items:
                selected = None
                for _ in range(len(waiting_items)):
                    candidate = waiting_items.popleft()
                    _item, key, limit = candidate
                    if key is None or limit is None or active[key] < limit:
                        selected = candidate
                        break
                    waiting_items.append(candidate)
                if selected is None:
                    break
                item, key, _limit = selected
                if key is not None:
                    active[key] += 1
                pending[pool.submit(process, item)] = key
            if not pending:
                if exhausted:
                    break
                continue
            completed, _unfinished = wait(
                pending.keys(),
                timeout=PROGRESS_REFRESH_SECONDS,
                return_when=FIRST_COMPLETED,
            )
            status_driver()
            for future in completed:
                key = pending.pop(future)
                try:
                    future.result()
                finally:
                    if key is not None:
                        active[key] -= 1
        if discovery_error is not None:
            error, traceback = discovery_error
            raise error.with_traceback(traceback)
    except BaseException:
        stop.set()
        for future in pending:
            future.cancel()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        status_driver()


def open_ingest_search(
    archive: Path,
    catalog: sqlite3.Connection,
    index_attachments: bool,
) -> tuple[sqlite3.Connection, PublicationRecovery]:
    """Open the disposable index, rebuilding an obsolete layout before ingest."""
    path = archive / "search.sqlite3"
    try:
        search = create_search(path, check_same_thread=False)
    except UnsupportedSearchSchemaError as error:
        print(f"rebuilding obsolete search index: {error}", file=sys.stderr)
        recovery_path = archive / "search.sqlite3.recovery.tmp"
        recovery_path.unlink(missing_ok=True)
        recovery_search = create_search(recovery_path)
        try:
            recovery = recover_publication(archive, catalog, recovery_search)
        finally:
            recovery_search.close()
            recovery_path.unlink(missing_ok=True)
        rebuild_search_index(archive, index_attachments)
        return create_search(path, check_same_thread=False), recovery
    try:
        return search, recover_publication(archive, catalog, search)
    except BaseException:
        search.close()
        raise


def ingest(args: argparse.Namespace) -> None:
    plugins = load_plugins(args.plugin_dir)
    source_specs = [SourceSpec(locator=root) for root in args.roots]
    selected_sources: list[tuple[SourceSpec, LoadedPlugin]] = []
    selected_source_keys: set[tuple[str, str, str | None]] = set()
    for source_spec in source_specs:
        matches = [
            plugin
            for plugin in plugins.sources
            if plugin.implementation.recognizes(source_spec)
        ]
        if not matches:
            raise ValueError(f"no source plug-in recognized {source_spec.locator}")
        if len(matches) > 1:
            kinds = ", ".join(plugin.manifest.kind for plugin in matches)
            raise ValueError(f"ambiguous source plug-ins for {source_spec.locator}: {kinds}")
        key = (matches[0].manifest.kind, source_spec.locator, source_spec.configuration_json)
        if key not in selected_source_keys:
            selected_sources.append((source_spec, matches[0]))
            selected_source_keys.add(key)
    archive = Path(args.archive)
    archive.mkdir(parents=True, exist_ok=True)
    catalog_path = archive / "archive.sqlite3"
    existing_output = (
        any(archive.glob("*.mbox"))
        or any(mbox_directory(archive).glob("*.mbox"))
        or (archive / "search.sqlite3").exists()
        or any(archive.glob("*.mbox.integrity"))
        or any((archive / "integrity").glob("*.mbox.integrity"))
    )
    if not catalog_path.exists() and existing_output:
        raise RuntimeError("cannot create a fresh catalog beside existing archive output; use a new empty archive directory")
    archive_reference = ArchiveReference(
        format_id="mailbag-1.0",
        archive_id=str(archive.resolve()),
        root=archive,
    )
    archive_integrity = MailbagArchiveIntegrityControls()
    catalog = create_catalog(catalog_path, check_same_thread=False)
    try:
        list(archive_integrity.initialize(archive_reference))
    except BaseException:
        catalog.close()
        raise
    progress_started = False

    def checkpoint_archive() -> None:
        for event in archive_integrity.checkpoint(archive_reference, catalog):
            if not isinstance(event, ProgressEvent) or not progress_started:
                continue
            if re.fullmatch(r"mailfile_\d+", threading.current_thread().name):
                progress.record_plugin_event(event, archive)
            else:
                progress.display(safe_status_text(event.phase))

    try:
        search, recovery = open_ingest_search(archive, catalog, args.index_attachments)
    except BaseException:
        catalog.close()
        raise
    if recovery is not PublicationRecovery.NONE:
        checkpoint_archive()
        print(f"recovered: pending message publication {recovery.value}", file=sys.stderr)
    owners = owner_tokens(Path(args.owner_names_file))
    started_at = datetime.now(timezone.utc)
    run_pk = catalog.execute(
        "INSERT INTO ingest_runs(started_at) VALUES (?)", (started_at.isoformat(),)
    ).lastrowid
    assert run_pk is not None
    catalog.commit()
    try:
        status_file = IngestStatusFile(
            archive,
            new_status_id(started_at, int(run_pk), os.getpid()),
        )
    except BaseException as error:
        catalog.execute(
            "UPDATE ingest_runs SET completed_at = ?, result = 'failed', detail = ? WHERE run_pk = ?",
            (
                datetime.now(timezone.utc).isoformat(),
                f"{type(error).__name__}: {error}",
                run_pk,
            ),
        )
        catalog.commit()
        search.close()
        catalog.close()
        raise
    progress = ProgressReporter(
        args.workers,
        status_file=status_file,
        archive=archive,
        run_pk=int(run_pk),
        source_roots=[source.locator for source, _plugin in selected_sources],
        started_at=started_at,
    )
    boxes: dict[Path, mailbox.mbox] = {}
    source_file_pks: dict[tuple[str, str, str], int] = {}
    source_volume_pks: dict[str, int] = {}
    pending_duplicate_observations: dict[tuple[str, str], list[int]] = {}
    pending_identities: set[tuple[str, str]] = set()
    publication_lock = threading.RLock()
    stop = threading.Event()
    scanner: ClamScanner | None = None
    discovery = sqlite3.connect("")
    discovery.executescript(
        "CREATE TABLE containers ("
        "sequence INTEGER PRIMARY KEY, plugin_kind TEXT NOT NULL, source_id TEXT NOT NULL, "
        "work_id TEXT NOT NULL, concurrency_key TEXT NOT NULL, stable INTEGER NOT NULL, "
        "payload_json TEXT NOT NULL, UNIQUE(plugin_kind, source_id, work_id));"
        "CREATE TABLE stable_verification ("
        "plugin_kind TEXT NOT NULL, source_id TEXT NOT NULL, work_id TEXT NOT NULL, "
        "payload_json TEXT NOT NULL, UNIQUE(plugin_kind, source_id, work_id));"
    )
    succeeded = False
    interrupted = False
    disk_full = False
    failure_detail: str | None = None

    def source_origin_pk(identity_json: str, metadata_json: str) -> int:
        cached = source_volume_pks.get(identity_json)
        if cached is not None:
            return cached
        now = datetime.now(timezone.utc).isoformat()
        catalog.execute(
            "INSERT INTO source_volumes(identity_json, metadata_json, first_observed_at, last_observed_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(identity_json) DO UPDATE SET metadata_json = excluded.metadata_json, last_observed_at = excluded.last_observed_at",
            (identity_json, metadata_json, now, now),
        )
        row = catalog.execute(
            "SELECT source_volume_pk FROM source_volumes WHERE identity_json = ?", (identity_json,)
        ).fetchone()
        assert row is not None
        volume_pk = int(row[0])
        source_volume_pks[identity_json] = volume_pk
        return volume_pk

    def local_source_file(work: ContainerWork) -> SourceFile | None:
        implementation = work.plugin.implementation
        return implementation.source_file(work.container) if isinstance(implementation, LocalSourcePlugin) else None

    def register_source_file(work: ContainerWork) -> int:
        source = local_source_file(work)
        if source is None:
            identity_json = SourceOriginIdentity(
                plugin_kind=work.plugin.manifest.kind,
                source_id=work.container.source.source_id,
            ).model_dump_json()
            metadata_json = SourceOriginMetadata(
                plugin_kind=work.plugin.manifest.kind,
                volume_label=work.container.source.source_id,
            ).model_dump_json()
            container_metadata_json = SourceContainerMetadata(
                display_name=work.container.source.display_name,
                hierarchy=work.container.source.hierarchy,
                provenance_json=work.container.source.provenance_json,
                relationship=work.container.source.relationship,
            ).model_dump_json()
            source_path = work.container.source.native_id
            hierarchy_path = (
                "/".join(work.container.source.hierarchy)
                if work.container.source.hierarchy
                else quote(work.container.source.native_id, safe="")
            )
            path_kind = "provider"
            source_kind = work.container.parser_kind or work.plugin.manifest.kind
            modified_at_ns = None
            byte_length = work.container.estimated_bytes
        else:
            identity_json = source.volume.identity_json
            metadata_json = source.volume.metadata_json
            container_metadata_json = SourceContainerMetadata(
                display_name=work.container.source.display_name,
                hierarchy=work.container.source.hierarchy,
                provenance_json=work.container.source.provenance_json,
                relationship=work.container.source.relationship,
            ).model_dump_json()
            source_path = source.source_path
            hierarchy_path = local_hierarchy_path(source)
            path_kind = "file"
            source_kind = source.kind
            modified_at_ns = source.modified_at_ns
            byte_length = source.byte_length
        volume_pk = source_origin_pk(identity_json, metadata_json)
        row = catalog.execute(
            "INSERT INTO source_files(source_volume_pk, source_plugin, work_id, source_path, hierarchy_path, "
            "metadata_json, path_kind, source_kind, modified_at_ns, byte_length) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(source_volume_pk, source_path) DO UPDATE SET source_plugin = excluded.source_plugin, "
            "work_id = excluded.work_id, hierarchy_path = excluded.hierarchy_path, "
            "metadata_json = excluded.metadata_json, source_kind = excluded.source_kind, "
            "modified_at_ns = excluded.modified_at_ns, byte_length = excluded.byte_length, "
            "path_kind = excluded.path_kind "
            "RETURNING source_file_pk",
            (
                volume_pk,
                work.plugin.manifest.kind,
                work.container.work_id,
                source_path,
                hierarchy_path,
                container_metadata_json,
                path_kind,
                source_kind,
                modified_at_ns,
                byte_length,
            ),
        ).fetchone()
        assert row is not None
        source_file_pk = int(row[0])
        source_file_pks[
            (
                work.plugin.manifest.kind,
                work.container.source.source_id,
                work.container.work_id,
            )
        ] = source_file_pk
        return source_file_pk

    def observe(source: MailObject, disposition: str, detail: str, sha256: str, message_pk: int | None = None) -> int:
        source_file_pk = source_file_pks[
            (source.source.plugin_kind, source.source.source_id, source.work_id)
        ]
        try:
            source_offset = int(source.cursor)
        except ValueError:
            source_offset = None
        cursor = catalog.execute(
            "INSERT INTO observations(run_pk, message_pk, source_file_pk, source_offset, source_cursor, raw_sha256, "
            "semantic_sha256, disposition, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_pk, message_pk, source_file_pk, source_offset, source.cursor, sha256,
             hashlib.sha256(semantic_bytes(source.raw)).hexdigest(), disposition, detail),
        )
        return int(cursor.lastrowid)

    def prior_source_evidence(source_file_pk: int, control_id: str) -> tuple[IntegrityEvidence, ...]:
        row = catalog.execute(
            "SELECT integrity_check_pk FROM source_integrity_checks WHERE source_file_pk = ? AND control_id = ? "
            "AND completed_at IS NOT NULL ORDER BY integrity_check_pk DESC LIMIT 1",
            (source_file_pk, control_id),
        ).fetchone()
        if row is None:
            return ()
        return tuple(
            IntegrityEvidence(
                control_id=item[0],
                subject_id=item[1],
                evidence_kind=item[2],
                algorithm=item[3],
                value=item[4],
                byte_length=item[5],
            )
            for item in catalog.execute(
                "SELECT control_id, subject_id, evidence_kind, algorithm, value, byte_length "
                "FROM source_integrity_evidence WHERE integrity_check_pk = ? ORDER BY ordinal",
                (row[0],),
            )
        )

    def evaluate_integrity(work: ContainerWork, prior: tuple[IntegrityEvidence, ...]) -> ContainerIntegrityResult:
        decision: IntegrityDecision | None = None
        evidence: list[IntegrityEvidence] = []
        controls = work.plugin.implementation.integrity_controls
        for event in controls.plan(work.container, prior):
            if isinstance(event, ProgressEvent):
                progress.record_plugin_event(event, work.container.source.display_name)
            elif isinstance(event, IntegrityEvidence):
                evidence.append(event)
            elif isinstance(event, IntegrityDecision) and decision is None:
                decision = event
            elif isinstance(event, IntegrityDecision):
                raise RuntimeError(f"source integrity control emitted multiple decisions for {work.container.work_id}")
            else:
                raise TypeError(
                    f"source integrity control emitted unsupported {type(event).__name__}"
                )
        if decision is None:
            raise RuntimeError(f"source integrity control emitted no decision for {work.container.work_id}")
        if decision.action == "resume" and not work.plugin.implementation.capabilities.resumable:
            raise RuntimeError(
                f"non-resumable source plug-in {work.plugin.manifest.kind} emitted a resume decision"
            )
        if decision.action in {"skip", "resume"} and not any(
            item.control_id == controls.control_id for item in evidence
        ):
            raise RuntimeError(
                f"source integrity control emitted {decision.action} without current evidence "
                f"for {work.container.work_id}"
            )
        subjects = {item.subject_id for item in evidence if item.control_id == controls.control_id}
        if len(subjects) > 1:
            raise RuntimeError(
                f"source integrity control emitted inconsistent subjects for {work.container.work_id}"
            )
        return ContainerIntegrityResult(decision=decision, evidence=tuple(evidence))

    def begin_integrity_check(source_file_pk: int, work: ContainerWork, result: ContainerIntegrityResult) -> int:
        controls = work.plugin.implementation.integrity_controls
        subject = next(
            (
                item.subject_id
                for item in result.evidence
                if item.control_id == controls.control_id
            ),
            f"{work.plugin.manifest.kind}:{work.container.source.source_id}:{work.container.work_id}",
        )
        cursor = catalog.execute(
            "INSERT INTO source_integrity_checks(source_file_pk, run_pk, control_id, subject_id, action, resume_cursor, "
            "reason, started_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                source_file_pk,
                run_pk,
                controls.control_id,
                subject,
                result.decision.action,
                result.decision.resume_cursor,
                result.decision.reason,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        integrity_check_pk = int(cursor.lastrowid)
        record_integrity_evidence(integrity_check_pk, result.evidence)
        catalog.commit()
        return integrity_check_pk

    def record_integrity_evidence(
        integrity_check_pk: int, evidence: Iterable[IntegrityEvidence]
    ) -> None:
        row = catalog.execute(
            "SELECT COALESCE(MAX(ordinal), -1) FROM source_integrity_evidence WHERE integrity_check_pk = ?",
            (integrity_check_pk,),
        ).fetchone()
        ordinal = int(row[0]) + 1
        catalog.executemany(
            "INSERT INTO source_integrity_evidence(integrity_check_pk, ordinal, control_id, subject_id, "
            "evidence_kind, algorithm, value, byte_length) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    integrity_check_pk,
                    ordinal + index,
                    item.control_id,
                    item.subject_id,
                    item.evidence_kind,
                    item.algorithm,
                    item.value,
                    item.byte_length,
                )
                for index, item in enumerate(evidence)
            ),
        )

    def checkpoint(
        work: ContainerWork,
        source_file_pk: int,
        integrity_check_pk: int,
        planned: tuple[IntegrityEvidence, ...],
    ) -> None:
        path = work.container.source.display_name
        catalog_byte_length = work.container.estimated_bytes
        progress_length = catalog_byte_length or 0
        progress.record_worker("checkpointing", path, progress_length, progress_length)
        controls = work.plugin.implementation.integrity_controls
        evidence: list[IntegrityEvidence] = []
        for item in controls.complete(work.container, planned):
            if isinstance(item, ProgressEvent):
                progress.record_plugin_event(item, path)
            elif isinstance(item, IntegrityEvidence):
                evidence.append(item)
            else:
                raise TypeError(
                    f"source integrity completion emitted unsupported {type(item).__name__}"
                )
        subjects = {item.subject_id for item in evidence if item.control_id == controls.control_id}
        if not subjects:
            raise RuntimeError(
                f"source integrity control emitted no completion evidence for {work.container.work_id}"
            )
        if len(subjects) > 1:
            raise RuntimeError(
                f"source integrity completion emitted inconsistent subjects for {work.container.work_id}"
            )
        planned_subjects = {
            item.subject_id for item in planned if item.control_id == controls.control_id
        }
        if planned_subjects and planned_subjects != subjects:
            raise RuntimeError(
                f"source integrity completion changed subject for {work.container.work_id}"
            )
        local = local_source_file(work)
        modified_at_ns = None if local is None else local.modified_at_ns
        digest = None
        if local is not None:
            catalog_byte_length = local.byte_length
            progress_length = local.byte_length
            digest = next(
                (
                    item.value
                    for item in reversed(evidence)
                    if item.control_id == controls.control_id
                    and item.evidence_kind == "cryptographic-digest"
                    and item.algorithm == "sha256"
                    and item.byte_length == local.byte_length
                ),
                None,
            )
        with publication_lock:
            record_integrity_evidence(integrity_check_pk, evidence)
            catalog.execute(
                "UPDATE source_integrity_checks SET subject_id = ?, completed_at = ? WHERE integrity_check_pk = ?",
                (next(iter(subjects)), datetime.now(timezone.utc).isoformat(), integrity_check_pk),
            )
            catalog.execute(
                "UPDATE source_files SET modified_at_ns = ?, byte_length = ?, sha256 = ?, checked_at = ?, "
                "completed_run = ? WHERE source_file_pk = ?",
                (
                    modified_at_ns,
                    catalog_byte_length,
                    digest,
                    datetime.now(timezone.utc).isoformat(),
                    run_pk,
                    source_file_pk,
                ),
            )
            catalog.commit()
        progress.record_file_complete(path, progress_length)

    def archive_scanned(candidate: PendingScan, infected: bool) -> int:
        raw, parsed = candidate.source.raw, candidate.parsed
        category = "INFECTED" if infected else ("Sent" if any(token in parsed.sender.lower() for token in owners) else "Archive")
        destination = mbox_path(archive, mailbox_name(parsed, category))
        file_existed = destination.exists()
        publication = PendingPublication(
            filename=destination.name,
            prior_size=destination.stat().st_size if destination.exists() else 0,
            file_existed=file_existed,
            message_id=parsed.message_id,
            sha256=parsed.sha256,
        )
        box: mailbox.mbox | None = None
        try:
            catalog.execute("BEGIN")
            sender_pk = address_pk(catalog, parsed.sender)
            message_pk = catalog.execute("INSERT INTO messages(message_id_normalized, sha256, sender_address_pk, subject, date_utc, date_source, category) VALUES (?, ?, ?, ?, ?, ?, ?)", (parsed.message_id, parsed.sha256, sender_pk, parsed.subject, parsed.date_utc, parsed.date_source, category)).lastrowid
            catalog.executemany(
                "INSERT INTO recipients(message_pk, address_pk, role) VALUES (?, ?, ?)",
                (
                    (message_pk, address_pk(catalog, recipient.address), recipient.role.value)
                    for recipient in parsed.recipients
                ),
            )
            catalog.executemany(
                "INSERT OR IGNORE INTO metadata_defects(message_pk, field, detail) VALUES (?, ?, ?)",
                ((message_pk, defect.field, defect.detail) for defect in parsed.defects),
            )
            box = boxes.get(destination)
            if box is None:
                box = mailbox.mbox(destination, create=True)
                boxes[destination] = box
            journal_publication(archive, publication)
            location = add_message(box, destination, raw)
            generation = catalog.execute(
                "INSERT INTO mbox_generations(filename, sha256, message_count, byte_count) VALUES (?, '', 0, 0) "
                "ON CONFLICT(filename) DO UPDATE SET filename = excluded.filename RETURNING generation_pk",
                (destination.name,),
            ).fetchone()
            assert generation is not None
            catalog.execute(
                "INSERT INTO locations(message_pk, generation_pk, byte_offset, byte_length) VALUES (?, ?, ?, ?)",
                (message_pk, generation[0], location.byte_offset, location.byte_length),
            )
            observe(candidate.source, "archived", category, parsed.sha256, message_pk)
            catalog.commit()
            clear_publication_journal(archive)
        except BaseException:
            catalog.rollback()
            if box is not None:
                box.close()
                boxes.pop(destination, None)
            if recover_publication(archive, catalog, search) is not PublicationRecovery.NONE:
                checkpoint_archive()
            raise
        if category in SEARCH_CATEGORIES:
            index_message_safely(
                catalog, search, message_pk, raw, args.index_attachments, date_utc=parsed.date_utc
            )
        progress.record_disposition("archived")
        if category == "INFECTED":
            progress.record_disposition("infected")
        return int(message_pk)

    def scan_message(source: MailObject) -> bool:
        assert scanner is not None
        progress.record_worker(
            "scanning",
            source.source.display_name,
            source.completed_bytes or 0,
            source.total_bytes or 0,
        )
        return scanner.infected(source.raw)

    def ingest_container(work: ContainerWork) -> None:
        source_plugin = work.plugin.implementation
        source_file = local_source_file(work)
        display_path = work.container.source.display_name
        byte_length = work.container.estimated_bytes or 0
        try:
            if stop.is_set():
                return
            progress.record_worker("checking", display_path, 0, byte_length)
            controls = source_plugin.integrity_controls
            with publication_lock:
                source_file_pk = register_source_file(work)
                prior = prior_source_evidence(source_file_pk, controls.control_id)
                catalog.commit()
            progress.record_file(display_path, 0, byte_length)
            integrity_result = evaluate_integrity(work, prior)
            decision = integrity_result.decision
            with publication_lock:
                integrity_check_pk = begin_integrity_check(source_file_pk, work, integrity_result)
            if stop.is_set():
                return
            if decision.action == "skip":
                checkpoint(work, source_file_pk, integrity_check_pk, integrity_result.evidence)
                progress.record_unchanged_source(display_path, decision.reason)
                return

            prior_date: datetime | None = None
            numeric_resume = None
            if decision.action == "resume" and decision.resume_cursor is not None:
                try:
                    numeric_resume = int(decision.resume_cursor)
                except ValueError:
                    numeric_resume = None
            if numeric_resume is not None:
                with publication_lock:
                    row = catalog.execute(
                        "SELECT messages.date_utc FROM observations JOIN messages USING (message_pk) "
                        "JOIN source_files USING (source_file_pk) WHERE source_file_pk = ? AND source_offset < ? "
                        "ORDER BY source_offset DESC LIMIT 1",
                        (source_file_pk, numeric_resume),
                    ).fetchone()
                if row is not None:
                    prior_date = datetime.fromisoformat(row[0])

            for item in source_plugin.messages(work.container, decision.resume_cursor):
                if stop.is_set():
                    return
                if isinstance(item, ProgressEvent):
                    progress.record_plugin_event(item, display_path)
                    continue
                if not isinstance(item, MailObject):
                    raise TypeError(
                        f"source plug-in {work.plugin.manifest.kind} yielded unsupported "
                        f"{type(item).__name__}"
                    )
                source = item
                if source.work_id != work.container.work_id or source.source != work.container.source:
                    raise RuntimeError(
                        f"source plug-in {work.plugin.manifest.kind} yielded a mail object for the wrong container"
                    )
                raw = source.raw
                progress.record_source(source)
                if source.exclusion_reason is not None:
                    digest = hashlib.sha256(raw).hexdigest()
                    with publication_lock:
                        observe(source, "source-metadata-excluded", source.exclusion_reason, digest)
                        catalog.commit()
                    progress.record_disposition("source-metadata-excluded")
                    continue
                try:
                    parsed = parse_message(
                        raw,
                        None if source_file is None else source_file.path,
                        prior_date,
                        source.source_date_utc,
                        args.earliest_year,
                    )
                except Exception as error:
                    digest = hashlib.sha256(raw).hexdigest()
                    with publication_lock:
                        observe(source, "error", f"{type(error).__name__}: {error}", digest)
                        catalog.commit()
                    raise RuntimeError(
                        f"failed to parse {source.source.display_name} at source offset {source.cursor}; sha256={digest}"
                    ) from error
                prior_date = datetime.fromisoformat(parsed.date_utc)
                progress.record(parsed, source)
                if parsed.autosave:
                    progress.record_worker(
                        "publishing",
                        source.source.display_name,
                        source.completed_bytes or 0,
                        source.total_bytes or 0,
                    )
                    with publication_lock:
                        observe(source, "autosave-excluded", "X-Apple-Auto-Saved", parsed.sha256)
                        catalog.commit()
                    progress.record_disposition("autosave-excluded")
                    continue

                identity = (parsed.message_id, parsed.sha256)
                progress.record_worker(
                    "deduplicating",
                    source.source.display_name,
                    source.completed_bytes or 0,
                    source.total_bytes or 0,
                )
                with publication_lock:
                    existing = catalog.execute(
                        "SELECT message_pk FROM messages WHERE message_id_normalized = ? AND sha256 = ?", identity
                    ).fetchone()
                    if existing is not None or identity in pending_identities:
                        message_pk = None if existing is None else existing[0]
                        detail = (
                            "same Message-ID and SHA-256"
                            if existing is not None
                            else "same Message-ID and SHA-256 pending scan"
                        )
                        if message_pk is not None:
                            catalog.executemany(
                                "INSERT OR IGNORE INTO metadata_defects(message_pk, field, detail) VALUES (?, ?, ?)",
                                ((message_pk, defect.field, defect.detail) for defect in parsed.defects),
                            )
                        observation_pk = observe(source, "duplicate", detail, parsed.sha256, message_pk)
                        if message_pk is None:
                            pending_duplicate_observations.setdefault(identity, []).append(observation_pk)
                        catalog.commit()
                        progress.record_disposition("duplicate")
                        continue
                    pending_identities.add(identity)

                candidate = PendingScan(source=source, parsed=parsed)
                infected = scan_message(source)
                if stop.is_set():
                    return
                progress.record_worker(
                    "waiting to publish",
                    source.source.display_name,
                    source.completed_bytes or 0,
                    source.total_bytes or 0,
                )
                with publication_lock:
                    progress.record_worker(
                        "publishing",
                        source.source.display_name,
                        source.completed_bytes or 0,
                        source.total_bytes or 0,
                    )
                    message_pk = archive_scanned(candidate, infected)
                    catalog.executemany(
                        "UPDATE observations SET message_pk = ? WHERE observation_pk = ?",
                        (
                            (message_pk, observation_pk)
                            for observation_pk in pending_duplicate_observations.pop(identity, [])
                        ),
                    )
                    catalog.commit()
                    pending_identities.remove(identity)

            if stop.is_set():
                return
            checkpoint(work, source_file_pk, integrity_check_pk, integrity_result.evidence)
        except BaseException:
            stop.set()
            raise
        finally:
            progress.record_file_inactive(display_path)

    def capture_discovery(
        selections: Iterable[tuple[SourceSpec, LoadedPlugin]],
        table: str,
        *,
        report_skipped: bool,
    ) -> SourceInventory:
        inventory = SourceInventory()
        for source_spec, plugin in selections:
            for item in plugin.implementation.discover(source_spec):
                if isinstance(item, MailContainer):
                    if item.source.plugin_kind != plugin.manifest.kind:
                        raise RuntimeError(
                            f"source plug-in {plugin.manifest.kind} yielded a container "
                            f"owned by {item.source.plugin_kind}"
                        )
                    payload = item.model_dump_json()
                    columns = "plugin_kind, source_id, work_id, payload_json"
                    parameters: tuple[object, ...]
                    if table == "containers":
                        columns += ", concurrency_key, stable"
                        parameters = (
                            plugin.manifest.kind,
                            item.source.source_id,
                            item.work_id,
                            payload,
                            item.concurrency_key,
                            int(plugin.implementation.capabilities.stable_inventory),
                        )
                    else:
                        parameters = (
                            plugin.manifest.kind,
                            item.source.source_id,
                            item.work_id,
                            payload,
                        )
                    try:
                        discovery.execute(
                            f"INSERT INTO {table}({columns}) VALUES ({', '.join('?' for _ in parameters)})",
                            parameters,
                        )
                    except sqlite3.IntegrityError as error:
                        existing = discovery.execute(
                            f"SELECT payload_json FROM {table} WHERE plugin_kind = ? AND source_id = ? AND work_id = ?",
                            (plugin.manifest.kind, item.source.source_id, item.work_id),
                        ).fetchone()
                        if existing is None or existing[0] != payload:
                            raise RuntimeError(
                                f"conflicting duplicate container identity: {plugin.manifest.kind}:"
                                f"{item.source.source_id}:{item.work_id}"
                            ) from error
                        continue
                    inventory.file_count += 1
                    inventory.byte_count += item.estimated_bytes or 0
                    if table == "containers":
                        progress.record_inventory(inventory.file_count, inventory.byte_count)
                elif isinstance(item, SkippedInput):
                    if report_skipped:
                        progress.record_skipped_file(item.source.display_name, item.detail)
                elif isinstance(item, ProgressEvent):
                    progress.display(safe_status_text(item.phase))
                else:
                    raise TypeError(
                        f"source plug-in {plugin.manifest.kind} yielded unsupported {type(item).__name__}"
                    )
        inventory.skipped_file_count = progress.state.counts.skipped_files
        return inventory

    def verify_stable_discovery() -> None:
        stable_sources = (
            selection
            for selection in selected_sources
            if selection[1].implementation.capabilities.stable_inventory
        )
        capture_discovery(stable_sources, "stable_verification", report_skipped=False)
        mismatch = discovery.execute(
            "SELECT c.plugin_kind, c.source_id, c.work_id FROM containers AS c WHERE c.stable = 1 "
            "AND NOT EXISTS (SELECT 1 FROM stable_verification AS v WHERE v.plugin_kind = c.plugin_kind "
            "AND v.source_id = c.source_id AND v.work_id = c.work_id AND v.payload_json = c.payload_json) "
            "UNION ALL SELECT v.plugin_kind, v.source_id, v.work_id FROM stable_verification AS v "
            "WHERE NOT EXISTS (SELECT 1 FROM containers AS c WHERE c.stable = 1 "
            "AND c.plugin_kind = v.plugin_kind AND c.source_id = v.source_id "
            "AND c.work_id = v.work_id AND c.payload_json = v.payload_json) LIMIT 1"
        ).fetchone()
        if mismatch is not None:
            raise RuntimeError(
                "stable source inventory changed during preflight; rerun after stabilizing the source"
            )

    def snapshotted_containers() -> Iterable[ContainerWork]:
        plugin_by_kind = {plugin.manifest.kind: plugin for plugin in plugins.sources}
        rows = discovery.execute(
            "SELECT plugin_kind, payload_json FROM containers ORDER BY "
            "row_number() OVER (PARTITION BY plugin_kind, concurrency_key ORDER BY sequence), sequence"
        )
        for plugin_kind, payload_json in rows:
            yield ContainerWork(
                plugin=plugin_by_kind[str(plugin_kind)],
                container=MailContainer.model_validate_json(payload_json),
            )

    progress.start()
    progress_started = True
    try:
        progress.set_phase(DISCOVERY_PHASE)
        inventory = capture_discovery(selected_sources, "containers", report_skipped=True)
        verify_stable_discovery()
        progress.finish_inventory(inventory)
        progress.set_phase(CLAMAV_START_PHASE)
        scanner = ClamScanner(progress.refresh)
        scanner.__enter__()
        progress.set_phase("checking sources")
        run_file_workers(
            snapshotted_containers(),
            args.workers,
            ingest_container,
            stop,
            progress.refresh,
            concurrency=lambda work: (
                f"{work.plugin.manifest.kind}:{work.container.concurrency_key}",
                work.plugin.implementation.capabilities.max_concurrency,
            ),
        )
        catalog.commit()
        search.commit()
        succeeded = True
    except KeyboardInterrupt as error:
        interrupted = True
        failure_detail = type(error).__name__
        catalog.commit()
        search.commit()
        raise
    except DiskFullError as error:
        disk_full = True
        failure_detail = f"{type(error).__name__}: {error}"
        catalog.rollback()
        search.rollback()
        raise
    except BaseException as error:
        failure_detail = f"{type(error).__name__}: {error}"
        catalog.rollback()
        search.rollback()
        raise
    finally:
        stop.set()
        if scanner is not None:
            scanner.__exit__()
        discovery.close()
        for box in boxes.values():
            box.close()
        result = "completed" if succeeded else "interrupted" if interrupted else "disk-full" if disk_full else "failed"
        catalog.execute(
            "UPDATE ingest_runs SET completed_at = ?, result = ?, detail = ? WHERE run_pk = ?",
            (datetime.now(timezone.utc).isoformat(), result, failure_detail, run_pk),
        )
        catalog.commit()
        integrity_error: Exception | None = None
        if boxes or succeeded or interrupted:
            try:
                checkpoint_archive()
                catalog.commit()
            except Exception as error:
                integrity_error = error
                if failure_detail is None:
                    failure_detail = f"{type(error).__name__}: {error}"
                else:
                    print(f"integrity refresh also failed: {error}", file=sys.stderr)
        if integrity_error is not None:
            result = "failed"
            catalog.execute(
                "UPDATE ingest_runs SET result = ?, detail = ? WHERE run_pk = ?",
                (result, failure_detail, run_pk),
            )
            catalog.commit()
        catalog.close()
        search.close()
        progress.finish(result, failure_detail)
        if integrity_error is not None and succeeded:
            raise integrity_error


def review(args: argparse.Namespace) -> None:
    catalog = sqlite3.connect(Path(args.archive) / "archive.sqlite3")
    try:
        query = "SELECT observations.disposition, observations.detail, source_files.source_path FROM observations JOIN source_files USING (source_file_pk)"
        parameters: tuple[int, ...] = () if args.run is None else (args.run,)
        if args.run is not None:
            query += " WHERE run_pk = ?"
        for disposition, detail, path in catalog.execute(query + " ORDER BY observation_pk", parameters):
            print(f"{disposition}\t{detail}\t{path}")
    finally:
        catalog.close()


def report_years(value: str | None) -> tuple[int, int] | None:
    if value is None:
        return None
    match = re.fullmatch(r"(19|20)\d{2}(?:-(19|20)\d{2})?", value)
    if match is None:
        raise ValueError("--year must be YYYY or YYYY-YYYY")
    years = value.split("-")
    first, last = int(years[0]), int(years[-1])
    if first > last:
        raise ValueError("--year range must be ascending")
    return first, last


def print_report(archive: Path, years: tuple[int, int] | None, top: int | None) -> None:
    catalog = sqlite3.connect(archive / "archive.sqlite3")
    try:
        conditions = ["category IN (?, ?)"]
        parameters: tuple[str | int, ...] = SEARCH_CATEGORIES
        if years is not None:
            conditions.append("date_utc >= ? AND date_utc < ?")
            parameters += (f"{years[0]:04d}-01-01T00:00:00+00:00", f"{years[1] + 1:04d}-01-01T00:00:00+00:00")
        clause = " WHERE " + " AND ".join(conditions)
        rows = catalog.execute(
            "WITH relevant AS (SELECT * FROM messages" + clause + "), "
            "people AS (SELECT substr(relevant.date_utc, 1, 4) AS year, email_addresses.address "
            "FROM relevant JOIN email_addresses ON email_addresses.address_pk = relevant.sender_address_pk "
            "UNION SELECT substr(relevant.date_utc, 1, 4), email_addresses.address FROM recipients "
            "JOIN relevant USING (message_pk) JOIN email_addresses USING (address_pk)), "
            "summary AS (SELECT substr(date_utc, 1, 4) AS year, SUM(category = 'Sent') AS sent, "
            "SUM(category != 'Sent') AS received FROM relevant GROUP BY year) "
            "SELECT summary.year, sent, received, COUNT(people.address) FROM summary "
            "LEFT JOIN people USING (year) GROUP BY summary.year ORDER BY summary.year",
            parameters,
        )
        print(
            tabulate(
                rows,
                headers=("year", "sent", "received", "people"),
                tablefmt="simple",
                intfmt=",",
                colalign=("right", "right", "right", "right"),
            )
        )
        if top is not None and top > 0:
            heading = lambda text: f"\033[1m{text}\033[0m" if sys.stdout.isatty() else text
            owner_addresses = (
                "owner_addresses AS (SELECT DISTINCT sender_address_pk FROM messages WHERE category = 'Sent') "
            )
            print()
            print(heading("top senders"))
            senders = catalog.execute(
                "WITH relevant AS (SELECT * FROM messages" + clause + "), "
                + owner_addresses
                + "SELECT COALESCE(NULLIF(email_addresses.address, ''), '(missing sender)'), COUNT(*), "
                "MIN(substr(relevant.date_utc, 1, 10)), "
                "MAX(substr(relevant.date_utc, 1, 10)) FROM relevant "
                "JOIN email_addresses ON email_addresses.address_pk = relevant.sender_address_pk "
                "LEFT JOIN owner_addresses ON owner_addresses.sender_address_pk = relevant.sender_address_pk "
                "WHERE owner_addresses.sender_address_pk IS NULL "
                "GROUP BY email_addresses.address ORDER BY COUNT(*) DESC, email_addresses.address LIMIT ?",
                (*parameters, top),
            )
            print(
                tabulate(
                    senders,
                    headers=("sender", "messages", "first", "last"),
                    tablefmt="simple",
                    intfmt=",",
                    colalign=("left", "right", "left", "left"),
                )
            )
            print()
            print(heading("top recipients"))
            recipients = catalog.execute(
                "WITH relevant AS (SELECT * FROM messages" + clause + "), "
                + owner_addresses
                + "SELECT email_addresses.address, COUNT(*), MIN(substr(relevant.date_utc, 1, 10)), "
                "MAX(substr(relevant.date_utc, 1, 10)) FROM recipients "
                "JOIN relevant USING (message_pk) JOIN email_addresses USING (address_pk) "
                "LEFT JOIN owner_addresses ON owner_addresses.sender_address_pk = email_addresses.address_pk "
                "WHERE owner_addresses.sender_address_pk IS NULL "
                "GROUP BY email_addresses.address ORDER BY COUNT(*) DESC, email_addresses.address LIMIT ?",
                (*parameters, top),
            )
            print(
                tabulate(
                    recipients,
                    headers=("recipient", "messages", "first", "last"),
                    tablefmt="simple",
                    intfmt=",",
                    colalign=("left", "right", "left", "left"),
                )
            )
    finally:
        catalog.close()


def report(args: argparse.Namespace) -> None:
    print_report(Path(args.archive), report_years(args.year), args.top)


def prepare_refresh_index_message(
    archive: Path, index_attachments: bool, work: RefreshIndexWork
) -> PreparedRefreshIndexMessage:
    """Verify and parse one canonical message away from the SQLite writer."""
    raw = read_verified_location(
        mbox_path(archive, work.filename),
        work.location,
        work.sha256,
    )
    return PreparedRefreshIndexMessage(
        message_pk=work.message_pk,
        date_utc=work.date_utc,
        indexed=prepare_search_message(raw, index_attachments, sha256=work.sha256),
    )


def rebuild_search_index(
    archive: Path, index_attachments: bool, workers: int = REFRESH_INDEX_DEFAULT_WORKERS
) -> None:
    """Build and validate a replacement search database before publishing it."""
    temporary = archive / "search.sqlite3.tmp"
    temporary.unlink(missing_ok=True)
    catalog = create_catalog(archive / "archive.sqlite3")
    published = False
    try:
        search = create_search(temporary)
        try:
            expected_row = catalog.execute(
                "SELECT COUNT(*) FROM messages WHERE category IN (?, ?)", SEARCH_CATEGORIES
            ).fetchone()
            assert expected_row is not None
            expected_by_file = dict(
                catalog.execute(
                    "SELECT mbox_generations.filename, COUNT(*) FROM mbox_generations "
                    "CROSS JOIN locations ON locations.generation_pk = mbox_generations.generation_pk "
                    "JOIN messages ON messages.message_pk = locations.message_pk "
                    "WHERE messages.category IN (?, ?) GROUP BY mbox_generations.filename",
                    SEARCH_CATEGORIES,
                )
            )
            mailboxes = [
                path
                for path in sorted(mbox_directory(archive).glob("*.mbox"))
                if not QUARANTINE_MAILBOX.fullmatch(path.name)
            ]
            mailbox_progress = RefreshIndexReporter(
                "Checking canonical mailboxes", int(expected_row[0]), "messages"
            )
            mailbox_progress.display(force=True)
            for path in mailboxes:
                box = mailbox.mbox(path, factory=None, create=False)
                try:
                    actual = len(box)
                finally:
                    box.close()
                expected = int(expected_by_file.get(path.name, 0))
                if actual != expected:
                    raise RuntimeError(
                        f"canonical MBOX/catalog count mismatch for {path.name}: {actual} records, {expected} catalogued"
                    )
                mailbox_progress.advance(expected)
            mailbox_progress.finish()
            rows = catalog.execute(
                "SELECT messages.message_pk, messages.sha256, messages.date_utc, mbox_generations.filename, "
                "locations.byte_offset, locations.byte_length "
                "FROM mbox_generations "
                "CROSS JOIN locations ON locations.generation_pk = mbox_generations.generation_pk "
                "JOIN messages ON messages.message_pk = locations.message_pk "
                "WHERE messages.category IN (?, ?) ORDER BY mbox_generations.filename, locations.byte_offset",
                SEARCH_CATEGORIES,
            )
            catalog.execute("BEGIN")
            indexed = 0
            message_progress = RefreshIndexReporter(
                f"Refreshing search index ({workers} workers)", int(expected_row[0]), "messages"
            )
            message_progress.display(force=True)
            next_row = next(rows, None)
            pending: deque[tuple[Future[PreparedRefreshIndexMessage], int]] = deque()
            in_flight_bytes = 0
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="refresh-index") as pool:
                while next_row is not None or pending:
                    while next_row is not None and len(pending) < workers:
                        message_pk, digest, date_utc, filename, offset, length = next_row
                        if QUARANTINE_MAILBOX.fullmatch(filename):
                            raise RuntimeError(f"searchable catalog message is stored in quarantine MBOX: {filename}")
                        work = RefreshIndexWork(
                            message_pk=message_pk,
                            sha256=digest,
                            date_utc=date_utc,
                            filename=filename,
                            location=MboxLocation(byte_offset=offset, byte_length=length),
                        )
                        if pending and in_flight_bytes + work.location.byte_length > REFRESH_INDEX_MAX_IN_FLIGHT_BYTES:
                            break
                        pending.append(
                            (pool.submit(prepare_refresh_index_message, archive, index_attachments, work), work.location.byte_length)
                        )
                        in_flight_bytes += work.location.byte_length
                        next_row = next(rows, None)
                    future, byte_length = pending.popleft()
                    prepared = future.result()
                    in_flight_bytes -= byte_length
                    write_prepared_search_message(search, prepared.indexed, date_utc=prepared.date_utc)
                    catalog.execute(
                        "UPDATE messages SET subject = ? WHERE message_pk = ? AND subject <> ?",
                        (prepared.indexed.subject, prepared.message_pk, prepared.indexed.subject),
                    )
                    indexed += 1
                    message_progress.advance()
            if indexed != int(expected_row[0]):
                raise RuntimeError(
                    f"catalog has {expected_row[0]} searchable messages but only {indexed} have canonical locations"
                )
            message_progress.finish()
            with RefreshIndexPublication():
                search.commit()
                catalog.commit()
                os.replace(temporary, archive / "search.sqlite3")
                published = True
        finally:
            search.close()
    except KeyboardInterrupt as error:
        if catalog.in_transaction:
            catalog.rollback()
        raise RefreshIndexInterrupted(published) from error
    finally:
        catalog.close()
        temporary.unlink(missing_ok=True)


def refresh_index(args: argparse.Namespace) -> None:
    print(
        "refresh-index: Ctrl-C discards the incomplete replacement; the existing search index remains unchanged",
        file=sys.stderr,
        flush=True,
    )
    rebuild_search_index(Path(args.archive), args.index_attachments, args.workers)


def main() -> int:
    parser = argparse.ArgumentParser()
    add_archive_argument(parser, "canonical archive directory")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest_parser = commands.add_parser("ingest")
    ingest_parser.add_argument("--owner-names-file", required=True)
    ingest_parser.add_argument(
        "--earliest-year",
        type=positive_integer,
        default=1900,
        help="reject earlier message dates and use normal fallbacks (default: 1900)",
    )
    ingest_parser.add_argument("--clamav", action="store_true", required=True, help="scan new messages with on-demand ClamAV")
    ingest_parser.add_argument("--workers", type=positive_integer, default=min(os.cpu_count() or 1, 8), help="source mailfiles ingested concurrently (default: cores, capped at 8)")
    ingest_parser.add_argument(
        "--plugin-dir",
        action="append",
        type=Path,
        default=[],
        help="trusted plug-in root to load (repeatable; Python code in this directory will execute)",
    )
    ingest_parser.add_argument("--index-attachments", action="store_true", help="index text attachments; non-text attachments require the planned Tika extractor")
    ingest_parser.add_argument("roots", nargs="+", metavar="ROOT")
    ingest_parser.set_defaults(function=ingest)
    review_parser = commands.add_parser("review")
    review_parser.add_argument("--run", type=int, help="only show this ingest run's observations")
    review_parser.set_defaults(function=review)
    report_parser = commands.add_parser("report")
    report_parser.add_argument("--year", help="year or inclusive year range, for example 2016 or 2010-2020")
    report_parser.add_argument("--top", type=nonnegative_integer, default=DEFAULT_REPORT_TOP, help="top senders and recipients to show (default: 10; use 0 to suppress)")
    report_parser.set_defaults(function=report)
    refresh_parser = commands.add_parser("refresh-index")
    refresh_parser.add_argument("--index-attachments", action="store_true", help="include text attachments; non-text attachments require the planned Tika extractor")
    refresh_parser.add_argument(
        "--workers",
        type=positive_integer,
        default=REFRESH_INDEX_DEFAULT_WORKERS,
        help="verified-message read/hash/MIME workers (default: detected cores, or 2)",
    )
    refresh_parser.set_defaults(function=refresh_index)
    args = parser.parse_args()
    args.archive = require_archive(parser, args.archive)
    try:
        args.function(args)
    except RefreshIndexInterrupted as error:
        message = (
            "interrupted: the rebuilt search index was already published; no partial index exists"
            if error.published
            else "interrupted: discarded incomplete replacement; the existing search index is unchanged"
        )
        print(f"\n{message}\n", file=sys.stderr, flush=True)
        return 130
    except KeyboardInterrupt:
        print("interrupted: archive state committed through the last completed message", file=sys.stderr, flush=True)
        print_report(Path(args.archive), None, DEFAULT_REPORT_TOP)
        return 130
    except DiskFullError as error:
        print(f"disk full: {error}; archive stopped cleanly", file=sys.stderr, flush=True)
        return 1
    except ClamScannerStartupError as error:
        print(f"ClamAV startup failed: {error}", file=sys.stderr, flush=True)
        return 1
    except PluginDiscoveryError as error:
        print(f"plug-in discovery failed: {error}", file=sys.stderr, flush=True)
        return 1
    except IncompleteAppleMailMessageError as error:
        print(f"unsupported source: {error}", file=sys.stderr, flush=True)
        return 1
    except NotImplementedError as error:
        print(f"unsupported source: {error}", file=sys.stderr, flush=True)
        return 1
    except PermissionError as error:
        print(
            f"source access denied: {error}; grant Full Disk Access to the terminal or application running mailarchiver",
            file=sys.stderr,
            flush=True,
        )
        return 1
    except FileNotFoundError as error:
        print(f"source not found: {error}", file=sys.stderr, flush=True)
        return 1
    if args.command == "ingest":
        print_report(Path(args.archive), None, DEFAULT_REPORT_TOP)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
