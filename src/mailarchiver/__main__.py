"""Run canonical mail ingest, provenance review, reports, and FTS rebuilds."""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import mailbox
import queue
import re
import shutil
import sqlite3
import sys
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, TypeVar

from pydantic import BaseModel, Field
from tabulate import tabulate

from .archive_path import add_archive_argument, require_archive
from .bagit import initialize_bag, write_bag_checkpoint
from .catalog import address_pk, create_catalog, create_search, owner_tokens
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
from .scanner import ClamScanner
from .search import QUARANTINE_MAILBOX, SEARCH_CATEGORIES, index_message, index_message_safely
from .sources import (
    IncompleteAppleMailMessageError,
    SourceFile,
    SourceInventory,
    SourceMessage,
    SourcePlan,
    has_mbox_append_boundary,
    sha256_file,
    sha256_file_with_prefix,
    source_files,
    source_inventory,
    source_messages,
)
from .standalone_verify import install_archive_verifier, semantic_bytes

DEFAULT_REPORT_TOP = 10
PROGRESS_REFRESH_SECONDS = 0.25
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


class YearProgress(BaseModel):
    year: int
    messages: int = 0


class IngestCounts(BaseModel):
    archived: int = 0
    duplicates: int = 0
    autosaves: int = 0
    infected: int = 0


class PendingScan(BaseModel):
    source: SourceMessage
    parsed: ParsedMessage


WorkerPhase = Literal[
    "idle",
    "checking",
    "ingesting",
    "deduplicating",
    "waiting for ClamAV startup",
    "scanning",
    "waiting to publish",
    "publishing",
    "checkpointing",
]


class WorkerProgress(BaseModel):
    worker: int
    phase: WorkerPhase = "idle"
    path: str | None = None
    bytes_done: int = 0
    bytes_total: int = 0


class ProgressUpdate(BaseModel):
    worker: int
    phase: WorkerPhase | None = None
    path: str | None = None
    bytes_done: int | None = None
    bytes_total: int | None = None
    message_date: datetime | None = None
    disposition: str | None = None
    file_complete: bool = False


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


def overall_progress(state: ProgressState, now: float) -> OverallProgress:
    active_bytes = sum(min(worker.bytes_done, worker.bytes_total) for worker in state.workers)
    done = min(state.source_bytes_completed + active_bytes, state.source_bytes_total)
    total = state.source_bytes_total
    percent = 0.0 if not state.inventory_complete else 100.0 if total == 0 else 100 * done / total
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

    def __init__(self, worker_count: int = 1) -> None:
        self.state = ProgressState(
            started_at=datetime.now(timezone.utc),
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

    def start(self) -> None:
        self.display(self.phase)

    def set_phase(self, phase: str) -> None:
        self._assert_driver_thread()
        self.base_phase = phase
        self.phase = phase
        self.phase_started_monotonic = time.monotonic()
        self.display(phase)

    def record(self, parsed: ParsedMessage, source: SourceMessage) -> None:
        self._send(
            "ingesting",
            source.path,
            source.bytes_done,
            source.bytes_total,
            message_date=datetime.fromisoformat(parsed.date_utc),
        )

    def record_source(self, source: SourceMessage) -> None:
        self._send("ingesting", source.path, source.bytes_done, source.bytes_total)

    def record_file(self, path: Path, bytes_done: int, bytes_total: int) -> None:
        self._send("checking", path, bytes_done, bytes_total)

    def record_worker(
        self,
        phase: WorkerPhase,
        path: Path,
        bytes_done: int,
        bytes_total: int,
    ) -> None:
        self._send(phase, path, bytes_done, bytes_total)

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
        self.state.inventory_complete = True
        self.display(DISCOVERY_PHASE)

    def completed_inventory(self) -> SourceInventory:
        self._drain_updates()
        return SourceInventory(
            file_count=self.state.files_processed,
            byte_count=self.state.source_bytes_completed,
        )

    def record_file_complete(self, path: Path, byte_count: int) -> None:
        self._send("idle", path, byte_count, byte_count, file_complete=True)

    def record_file_inactive(self, path: Path) -> None:
        self._send("idle", path, 0, 0)

    def record_disposition(self, disposition: str) -> None:
        self.updates.put(ProgressUpdate(worker=self._worker_number(), disposition=disposition))

    def _send(
        self,
        phase: WorkerPhase,
        path: Path,
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
                worker.phase = "idle"
                worker.path = None
                worker.bytes_done = 0
                worker.bytes_total = 0
            elif update.phase == "idle":
                worker.phase = "idle"
                worker.path = None
                worker.bytes_done = 0
                worker.bytes_total = 0
            else:
                if update.phase is not None:
                    worker.phase = update.phase
                if update.path is not None and update.path != worker.path:
                    worker.path = update.path
                    worker.bytes_done = 0
                if update.bytes_done is not None:
                    worker.bytes_done = max(worker.bytes_done, update.bytes_done)
                if update.bytes_total is not None:
                    worker.bytes_total = update.bytes_total
                if worker.bytes_done > 0 and self.state.byte_progress_started_monotonic is None:
                    self.state.byte_progress_started_monotonic = time.monotonic()
            if update.message_date is not None:
                self._record_message(update.message_date)
            if update.disposition == "archived":
                self.state.counts.archived += 1
            elif update.disposition == "duplicate":
                self.state.counts.duplicates += 1
            elif update.disposition == "autosave-excluded":
                self.state.counts.autosaves += 1
            elif update.disposition == "infected":
                self.state.counts.infected += 1
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

    def _worker_phase(self) -> str:
        phases = {worker.phase for worker in self.state.workers}
        if CLAMAV_START_PHASE in phases:
            return CLAMAV_START_PHASE
        if phases != {"idle"}:
            return "ingesting"
        return self.base_phase

    @staticmethod
    def _worker_line(worker: WorkerProgress, columns: int) -> str:
        prefix = f"Thread {worker.worker:>2}: [{worker.phase}]"
        if worker.phase == "idle" or worker.path is None:
            return prefix
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

    def display(self, label: str | None) -> None:
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
                f"Archived:  {state.counts.archived:,}  Seen/skipped: {state.counts.duplicates:,}  Autosaved: {state.counts.autosaves:,}  Infected: {state.counts.infected:,}",
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
                f"autosaved={state.counts.autosaves} infected={state.counts.infected}",
                file=sys.stderr,
            )
        sys.stderr.flush()

    def finish(self, status: str) -> None:
        self.display(status)


def run_file_workers(
    items: Iterable[WorkerItem],
    worker_count: int,
    process: Callable[[WorkerItem], None],
    stop: threading.Event,
    status_driver: Callable[[], None],
) -> None:
    """Process at most ``worker_count`` source files concurrently and defer discovery errors."""
    iterator = iter(items)
    pool = ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="mailfile")
    pending: set[Future[None]] = set()
    exhausted = False
    discovery_error: BaseException | None = None
    try:
        while pending or not exhausted:
            if stop.is_set():
                exhausted = True
            while not exhausted and len(pending) < worker_count:
                try:
                    item = next(iterator)
                except StopIteration:
                    exhausted = True
                except BaseException as error:
                    discovery_error = error
                    exhausted = True
                else:
                    pending.add(pool.submit(process, item))
            if not pending:
                break
            completed, pending = wait(
                pending,
                timeout=PROGRESS_REFRESH_SECONDS,
                return_when=FIRST_COMPLETED,
            )
            status_driver()
            for future in completed:
                future.result()
        if discovery_error is not None:
            raise discovery_error
    except BaseException:
        stop.set()
        for future in pending:
            future.cancel()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        status_driver()


def ingest(args: argparse.Namespace) -> None:
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
    initialize_bag(archive)
    catalog = create_catalog(catalog_path, check_same_thread=False)
    search = create_search(archive / "search.sqlite3", check_same_thread=False)
    install_archive_verifier(archive)
    recovery = recover_publication(archive, catalog, search)
    if recovery is not PublicationRecovery.NONE:
        write_bag_checkpoint(archive, catalog)
        print(f"recovered: pending message publication {recovery.value}", file=sys.stderr)
    owners = owner_tokens(Path(args.owner_names_file))
    run_pk = catalog.execute("INSERT INTO ingest_runs(started_at) VALUES (?)", (datetime.now(timezone.utc).isoformat(),)).lastrowid
    catalog.commit()
    boxes: dict[Path, mailbox.mbox] = {}
    source_file_pks: dict[Path, int] = {}
    source_volume_pks: dict[str, int] = {}
    pending_duplicate_observations: dict[tuple[str, str], list[int]] = {}
    pending_identities: set[tuple[str, str]] = set()
    publication_lock = threading.RLock()
    scanner_lock = threading.Lock()
    stop = threading.Event()
    scanner: ClamScanner | None = None
    progress = ProgressReporter(args.workers)
    succeeded = False
    interrupted = False
    disk_full = False
    failure_detail: str | None = None
    progress.start()

    def source_volume_pk(source: SourceFile) -> int:
        cached = source_volume_pks.get(source.volume.identity_json)
        if cached is not None:
            return cached
        now = datetime.now(timezone.utc).isoformat()
        catalog.execute(
            "INSERT INTO source_volumes(identity_json, metadata_json, first_observed_at, last_observed_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(identity_json) DO UPDATE SET metadata_json = excluded.metadata_json, last_observed_at = excluded.last_observed_at",
            (source.volume.identity_json, source.volume.metadata_json, now, now),
        )
        row = catalog.execute(
            "SELECT source_volume_pk FROM source_volumes WHERE identity_json = ?", (source.volume.identity_json,)
        ).fetchone()
        assert row is not None
        volume_pk = int(row[0])
        source_volume_pks[source.volume.identity_json] = volume_pk
        return volume_pk

    def register_source_file(source: SourceFile) -> None:
        volume_pk = source_volume_pk(source)
        row = catalog.execute(
            "INSERT INTO source_files(source_volume_pk, source_path, path_kind, source_kind, modified_at_ns, byte_length) "
            "VALUES (?, ?, 'file', ?, ?, ?) ON CONFLICT(source_volume_pk, source_path) DO UPDATE SET "
            "source_kind = excluded.source_kind, modified_at_ns = excluded.modified_at_ns, byte_length = excluded.byte_length "
            "RETURNING source_file_pk",
            (volume_pk, source.source_path, source.kind, source.modified_at_ns, source.byte_length),
        ).fetchone()
        assert row is not None
        source_file_pks[source.path] = int(row[0])

    def observe(source: SourceMessage, disposition: str, detail: str, sha256: str, message_pk: int | None = None) -> int:
        source_file_pk = source_file_pks[source.path]
        cursor = catalog.execute(
            "INSERT INTO observations(run_pk, message_pk, source_file_pk, source_offset, raw_sha256, semantic_sha256, disposition, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_pk, message_pk, source_file_pk, source.source_offset, sha256,
             hashlib.sha256(semantic_bytes(source.raw)).hexdigest(), disposition, detail),
        )
        return int(cursor.lastrowid)

    def checkpoint(source: SourceFile, sha256: str) -> None:
        progress.record_worker("checkpointing", source.path, source.byte_length, source.byte_length)
        current = source.path.stat()
        if current.st_size != source.byte_length or current.st_mtime_ns != source.modified_at_ns:
            raise RuntimeError(f"source changed during ingest: {source.path}")
        catalog.execute(
            "UPDATE source_files SET modified_at_ns = ?, byte_length = ?, sha256 = ?, checked_at = ?, completed_run = ? "
            "WHERE source_file_pk = ?",
            (source.modified_at_ns, source.byte_length, sha256, datetime.now(timezone.utc).isoformat(), run_pk,
             source_file_pks[source.path]),
        )
        catalog.commit()
        progress.record_file_complete(source.path, source.byte_length)

    def plan_source(source: SourceFile, prior: tuple[int | None, str | None] | None) -> SourcePlan:
        progress.record_file(source.path, 0, source.byte_length)
        report_hash = lambda done, _total: progress.record_file(source.path, done, source.byte_length)
        if prior is None:
            return SourcePlan(source=source)
        prior_length, prior_sha256 = prior
        if prior_length is None or prior_sha256 is None:
            return SourcePlan(source=source, sha256=sha256_file(source.path, progress=report_hash))
        if source.byte_length == prior_length:
            sha256 = sha256_file(source.path, progress=report_hash)
            return SourcePlan(source=source, sha256=sha256, skip=sha256 == prior_sha256)
        start_offset = 0
        if source.byte_length > prior_length:
            hashes = sha256_file_with_prefix(source.path, prior_length, report_hash)
            if hashes.prefix_sha256 == prior_sha256 and source.kind == "mbox" and has_mbox_append_boundary(source.path, prior_length):
                start_offset = prior_length
            return SourcePlan(source=source, sha256=hashes.sha256, start_offset=start_offset)
        return SourcePlan(source=source, sha256=sha256_file(source.path, progress=report_hash))

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
            catalog.executemany("INSERT INTO recipients(message_pk, address_pk) VALUES (?, ?)", ((message_pk, address_pk(catalog, address)) for address in parsed.recipients))
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
                write_bag_checkpoint(archive, catalog)
            raise
        if category in SEARCH_CATEGORIES:
            index_message_safely(catalog, search, message_pk, raw, args.index_attachments)
        progress.record_disposition("archived")
        if category == "INFECTED":
            progress.record_disposition("infected")
        return int(message_pk)

    def scan_message(source: SourceMessage) -> bool:
        nonlocal scanner
        if scanner is None:
            progress.record_worker(
                CLAMAV_START_PHASE,
                source.path,
                source.bytes_done,
                source.bytes_total,
            )
            with scanner_lock:
                if scanner is None:
                    candidate = ClamScanner()
                    candidate.__enter__()
                    scanner = candidate
        assert scanner is not None
        progress.record_worker("scanning", source.path, source.bytes_done, source.bytes_total)
        return scanner.infected(source.raw)

    def ingest_source_file(source_file: SourceFile) -> None:
        try:
            if stop.is_set():
                return
            progress.record_worker("checking", source_file.path, 0, source_file.byte_length)
            with publication_lock:
                volume_pk = source_volume_pk(source_file)
                prior = catalog.execute(
                    "SELECT byte_length, sha256 FROM source_files WHERE source_volume_pk = ? AND source_path = ?",
                    (volume_pk, source_file.source_path),
                ).fetchone()
                register_source_file(source_file)
                catalog.commit()
            plan = plan_source(source_file, prior)
            if stop.is_set():
                return
            if plan.skip:
                assert plan.sha256 is not None
                with publication_lock:
                    checkpoint(source_file, plan.sha256)
                return

            prior_date: datetime | None = None
            if plan.start_offset:
                with publication_lock:
                    row = catalog.execute(
                        "SELECT messages.date_utc FROM observations JOIN messages USING (message_pk) "
                        "JOIN source_files USING (source_file_pk) WHERE source_file_pk = ? AND source_offset < ? "
                        "ORDER BY source_offset DESC LIMIT 1",
                        (source_file_pks[source_file.path], plan.start_offset),
                    ).fetchone()
                if row is not None:
                    prior_date = datetime.fromisoformat(row[0])

            for source in source_messages(plan.source, plan.start_offset):
                if stop.is_set():
                    return
                raw = source.raw
                progress.record_source(source)
                try:
                    parsed = parse_message(raw, source.path, prior_date)
                except Exception as error:
                    digest = hashlib.sha256(raw).hexdigest()
                    with publication_lock:
                        observe(source, "error", f"{type(error).__name__}: {error}", digest)
                        catalog.commit()
                    raise RuntimeError(
                        f"failed to parse {source.path} at source offset {source.source_offset}; sha256={digest}"
                    ) from error
                prior_date = datetime.fromisoformat(parsed.date_utc)
                progress.record(parsed, source)
                if parsed.autosave:
                    progress.record_worker(
                        "publishing", source.path, source.bytes_done, source.bytes_total
                    )
                    with publication_lock:
                        observe(source, "autosave-excluded", "X-Apple-Auto-Saved", parsed.sha256)
                        catalog.commit()
                    progress.record_disposition("autosave-excluded")
                    continue

                identity = (parsed.message_id, parsed.sha256)
                progress.record_worker(
                    "deduplicating", source.path, source.bytes_done, source.bytes_total
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
                    "waiting to publish", source.path, source.bytes_done, source.bytes_total
                )
                with publication_lock:
                    progress.record_worker(
                        "publishing", source.path, source.bytes_done, source.bytes_total
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

            source_sha256 = plan.sha256 or sha256_file(
                plan.source.path,
                progress=lambda done, _total: progress.record_file(
                    plan.source.path, done, plan.source.byte_length
                ),
            )
            if stop.is_set():
                return
            with publication_lock:
                checkpoint(plan.source, source_sha256)
        except BaseException:
            stop.set()
            raise
        finally:
            progress.record_file_inactive(source_file.path)

    roots = [Path(root) for root in args.roots]

    def discovered_sources() -> Iterable[SourceFile]:
        for root in roots:
            yield from source_files(root)

    try:
        progress.set_phase(DISCOVERY_PHASE)
        inventory = source_inventory(roots, progress.record_inventory)
        progress.finish_inventory(inventory)
        progress.set_phase("checking sources")
        run_file_workers(
            discovered_sources(),
            args.workers,
            ingest_source_file,
            stop,
            progress.refresh,
        )
        if progress.completed_inventory() != inventory:
            raise RuntimeError(
                "recognized source files changed between discovery and ingest; rerun after stabilizing the source"
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
                write_bag_checkpoint(archive, catalog)
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
        progress.finish(result)
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


def refresh_index(args: argparse.Namespace) -> None:
    archive = Path(args.archive)
    temporary = archive / "search.sqlite3.tmp"
    temporary.unlink(missing_ok=True)
    catalog = sqlite3.connect(f"file:{archive / 'archive.sqlite3'}?mode=ro", uri=True)
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
            for path in mbox_directory(archive).glob("*.mbox"):
                if QUARANTINE_MAILBOX.fullmatch(path.name):
                    continue
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
            rows = catalog.execute(
                "SELECT messages.sha256, mbox_generations.filename, locations.byte_offset, locations.byte_length "
                "FROM mbox_generations "
                "CROSS JOIN locations ON locations.generation_pk = mbox_generations.generation_pk "
                "JOIN messages ON messages.message_pk = locations.message_pk "
                "WHERE messages.category IN (?, ?) ORDER BY mbox_generations.filename, locations.byte_offset",
                SEARCH_CATEGORIES,
            )
            indexed = 0
            for digest, filename, offset, length in rows:
                if QUARANTINE_MAILBOX.fullmatch(filename):
                    raise RuntimeError(f"searchable catalog message is stored in quarantine MBOX: {filename}")
                raw = read_verified_location(
                    mbox_path(archive, filename),
                    MboxLocation(byte_offset=offset, byte_length=length),
                    digest,
                )
                index_message(search, raw, args.index_attachments)
                indexed += 1
            if indexed != int(expected_row[0]):
                raise RuntimeError(
                    f"catalog has {expected_row[0]} searchable messages but only {indexed} have canonical locations"
                )
            search.commit()
        finally:
            search.close()
    finally:
        catalog.close()
    os.replace(temporary, archive / "search.sqlite3")


def main() -> int:
    parser = argparse.ArgumentParser()
    add_archive_argument(parser, "canonical archive directory")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest_parser = commands.add_parser("ingest")
    ingest_parser.add_argument("--owner-names-file", required=True)
    ingest_parser.add_argument("--clamav", action="store_true", required=True, help="scan new messages with on-demand ClamAV")
    ingest_parser.add_argument("--workers", type=positive_integer, default=min(os.cpu_count() or 1, 8), help="source mailfiles ingested concurrently (default: cores, capped at 8)")
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
    refresh_parser.set_defaults(function=refresh_index)
    args = parser.parse_args()
    args.archive = require_archive(parser, args.archive)
    try:
        args.function(args)
    except KeyboardInterrupt:
        print("interrupted: archive state committed through the last completed message", file=sys.stderr, flush=True)
        print_report(Path(args.archive), None, DEFAULT_REPORT_TOP)
        return 130
    except DiskFullError as error:
        print(f"disk full: {error}; archive stopped cleanly", file=sys.stderr, flush=True)
        return 1
    except IncompleteAppleMailMessageError as error:
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
