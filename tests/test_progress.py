"""Verify ingest progress and bounded mailfile-level worker concurrency."""

from __future__ import annotations

import queue
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mailarchiver.__main__ import (
    ANSI_RESET,
    CLAMAV_START_PHASE,
    PROGRESS_REFRESH_SECONDS,
    TOP_LINE_STYLE,
    ProgressReporter,
    ProgressState,
    WorkerProgress,
    overall_line,
    overall_progress,
    run_file_workers,
)
from mailarchiver.plugin_api import ProgressEvent
from mailarchiver.sources import SourceInventory


def test_overall_progress_uses_concurrent_source_bytes_for_percentage_and_eta() -> None:
    """Requirement: aggregate percentage and ETA include every active source worker."""
    state = ProgressState(
        started_at=datetime.now(timezone.utc),
        started_monotonic=0,
        files_processed=2,
        source_files_total=4,
        source_bytes_completed=200,
        source_bytes_total=1000,
        inventory_complete=True,
        byte_progress_started_monotonic=10,
        workers=[
            WorkerProgress(worker=1, phase="ingesting", path="one.mbox", bytes_done=200, bytes_total=300),
            WorkerProgress(worker=2, phase="checking", path="two.mbox", bytes_done=100, bytes_total=200),
        ],
    )

    progress = overall_progress(state, now=20)

    assert progress.bytes_done == 500
    assert progress.percent == 50
    assert progress.eta == "10s"
    assert overall_line(state, now=20) == "Overall:  50.0%  500 B / 1000 B  Files 2 / 4  ETA 10s"


def test_overall_progress_reports_finalizing_before_last_checkpoint() -> None:
    """Requirement: complete byte input does not claim completion before its stable checkpoint."""
    state = ProgressState(
        started_at=datetime.now(timezone.utc),
        started_monotonic=0,
        source_files_total=1,
        source_bytes_total=100,
        inventory_complete=True,
        byte_progress_started_monotonic=10,
        workers=[
            WorkerProgress(worker=1, phase="checkpointing", path="mailbox.mbox", bytes_done=100, bytes_total=100)
        ],
    )

    assert overall_progress(state, now=20).eta == "finalizing"


def test_unknown_byte_inventory_uses_completed_containers_for_percentage() -> None:
    """Requirement: provider work with no byte estimate cannot display 100% before completion."""
    state = ProgressState(
        started_at=datetime.now(timezone.utc),
        started_monotonic=0,
        files_processed=1,
        source_files_total=4,
        source_bytes_total=0,
        inventory_complete=True,
    )

    assert overall_progress(state, now=20).percent == 25


def test_worker_file_progress_does_not_regress_during_post_ingest_hashing() -> None:
    """Requirement: a second read of one source file cannot move aggregate progress backward."""
    progress = ProgressReporter()
    progress.finish_inventory(SourceInventory(file_count=1, byte_count=100))

    def produce() -> None:
        progress.record_file(Path("mailbox.mbox"), 100, 100)
        progress.record_file(Path("mailbox.mbox"), 20, 100)

    producer = threading.Thread(name="mailfile_0", target=produce)
    producer.start()
    producer.join()
    progress.refresh()

    assert progress.state.workers[0].bytes_done == 100
    assert overall_progress(progress.state, time.monotonic()).bytes_done == 100


def test_terminal_overall_line_is_white_on_blue(capsys: pytest.CaptureFixture[str]) -> None:
    """Requirement: the terminal scoreboard top line is white text on a blue background."""
    progress = ProgressReporter()
    progress.tty = True
    progress.state.inventory_complete = True
    progress.state.source_files_total = 1
    progress.state.source_bytes_total = 100

    progress.display("ingesting")

    output = capsys.readouterr().err
    assert f"{TOP_LINE_STYLE}Overall:" in output
    assert ANSI_RESET in output


def test_redirected_overall_progress_has_no_terminal_controls(capsys: pytest.CaptureFixture[str]) -> None:
    """Requirement: redirected progress is line-oriented and contains aggregate totals."""
    progress = ProgressReporter()
    progress.tty = False
    progress.state.inventory_complete = True
    progress.state.source_files_total = 2
    progress.state.source_bytes_total = 100
    progress.state.source_bytes_completed = 25
    progress.state.files_processed = 1
    progress.state.byte_progress_started_monotonic = time.monotonic() - 1

    progress.display("checking sources")

    output = capsys.readouterr().err
    assert "overall_bytes=25 overall_total_bytes=100 overall_percent=25.0%" in output
    assert "files_processed=1 files_total=2" in output
    assert "\x1b" not in output


def test_framework_prints_each_skipped_file_and_reason_once(capsys: pytest.CaptureFixture[str]) -> None:
    """Requirement: importer diagnostics identify every unrecognized input without duplicate-pass output."""
    progress = ProgressReporter()
    progress.tty = False
    skipped = Path("/source/ignored.plist")

    progress.record_skipped_file(skipped, "no file parser recognized it")
    progress.finish_inventory(SourceInventory(file_count=0, byte_count=0, skipped_file_count=1))
    progress.finish("completed")

    output = capsys.readouterr().err
    assert output.count(f"skipped input: {skipped} (no file parser recognized it)") == 1
    assert "skipped_files=1" in output


def test_unchanged_integrity_skip_is_rendered_by_framework(capsys: pytest.CaptureFixture[str]) -> None:
    """Requirement: worker plug-ins queue skip evidence; only the status framework prints it."""
    progress = ProgressReporter()
    progress.tty = False
    source = Path("/source/archive.mbox")
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="mailfile") as pool:
        pool.submit(progress.record_unchanged_source, source, "complete SHA-256 matched").result(timeout=5)

    progress.finish("completed")

    output = capsys.readouterr().err
    assert output.count(f"skipped unchanged: {source} (complete SHA-256 matched)") == 1
    assert "unchanged_sources=1" in output


def test_framework_renders_provider_phase_and_message_progress() -> None:
    """Requirement: plug-in status is typed data rendered only by the framework."""
    progress = ProgressReporter()
    event = ProgressEvent(
        work_id="account:inbox",
        phase="fetching\nprovider messages",
        completed=25,
        total=100,
        unit="messages",
    )

    producer = threading.Thread(
        name="mailfile_0",
        target=progress.record_plugin_event,
        args=(event, Path("Provider Inbox")),
    )
    producer.start()
    producer.join()
    progress.refresh()

    worker = progress.state.workers[0]
    assert worker.phase == "fetching provider messages"
    assert (worker.activity_done, worker.activity_total, worker.activity_unit) == (25, 100, "messages")
    assert "25/100 messages" in progress._worker_line(worker, 120)


def test_clamav_preflight_is_repeated_before_workers_start(capsys: pytest.CaptureFixture[str]) -> None:
    """Requirement: the main status driver reports ClamAV readiness before worker activity."""
    progress = ProgressReporter(worker_count=2)
    progress.tty = False
    progress.start()

    try:
        progress.set_phase(CLAMAV_START_PHASE)
        time.sleep(PROGRESS_REFRESH_SECONDS)
        progress.refresh()
    finally:
        progress.finish("completed")

    lines = [line for line in capsys.readouterr().err.splitlines() if line.startswith(CLAMAV_START_PHASE)]
    assert len(lines) >= 2
    assert all("processed=0 active_workers=0 peak_workers=0" in line for line in lines)
    assert all("workers=1:idle:- 2:idle:-" in line for line in lines)


def test_file_worker_pool_runs_no_more_than_requested_mailfiles() -> None:
    """Requirement: worker count bounds simultaneous source-file tasks, not message scans."""
    started: queue.Queue[int] = queue.Queue()
    release = threading.Event()
    stop = threading.Event()
    active = 0
    maximum_active = 0
    lock = threading.Lock()

    def process(item: int) -> None:
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        started.put(item)
        assert release.wait(timeout=5)
        with lock:
            active -= 1

    with ThreadPoolExecutor(max_workers=1) as scheduler:
        result = scheduler.submit(run_file_workers, range(6), 3, process, stop, lambda: None)
        first_wave = {started.get(timeout=5) for _ in range(3)}
        with pytest.raises(queue.Empty):
            started.get(timeout=0.1)
        assert first_wave == {0, 1, 2}
        assert maximum_active == 3
        release.set()
        result.result(timeout=5)

    assert stop.is_set() is False
    assert sorted([*first_wave, *(started.get_nowait() for _ in range(3))]) == list(range(6))


def test_framework_enforces_source_plugin_concurrency_by_key() -> None:
    """Requirement: source plug-ins declare limits while the framework owns thread scheduling."""
    stop = threading.Event()
    active: Counter[str] = Counter()
    maximum: Counter[str] = Counter()
    maximum_total = 0
    lock = threading.Lock()

    def process(item: tuple[str, int]) -> None:
        nonlocal maximum_total
        key, _number = item
        with lock:
            active[key] += 1
            maximum[key] = max(maximum[key], active[key])
            maximum_total = max(maximum_total, sum(active.values()))
        time.sleep(0.02)
        with lock:
            active[key] -= 1

    items = [("account-a", number) for number in range(4)] + [("account-b", number) for number in range(4)]
    run_file_workers(
        items,
        4,
        process,
        stop,
        lambda: None,
        concurrency=lambda item: (item[0], 1 if item[0] == "account-a" else 2),
    )

    assert maximum == Counter({"account-b": 2, "account-a": 1})
    assert maximum_total == 3


def test_numbered_worker_rows_are_main_rendered_without_wrapping(capsys: pytest.CaptureFixture[str]) -> None:
    """Regression: long worker paths do not scroll repeated dashboard headings."""
    progress = ProgressReporter(worker_count=2)
    progress.tty = True
    progress.terminal_columns = 64
    progress.start()

    producer = threading.Thread(
        name="mailfile_0",
        target=progress.record_worker,
        args=("ingesting", Path("/a/very/long/source/path/that/must/not/wrap/archive05.mbox"), 50, 100),
    )
    producer.start()
    producer.join()
    progress.refresh()

    output = capsys.readouterr().err
    assert "Thread  1: [ingesting]" in output
    assert "Thread  2: [idle]" in output
    assert "\x1b[8A" in output
    rendered = [part.split("\n", 1)[0] for part in output.split("\r\x1b[2K")[1:]]
    plain = [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in rendered]
    assert plain and all(len(line) <= progress.terminal_columns for line in plain)
