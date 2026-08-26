"""Verify ingest progress and bounded mailfile-level worker concurrency."""

from __future__ import annotations

import queue
import re
import threading
import time
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


def test_clamav_startup_wait_is_repeated_with_elapsed_time(capsys: pytest.CaptureFixture[str]) -> None:
    """Requirement: the main status driver reports a worker's long ClamAV startup."""
    progress = ProgressReporter()
    progress.tty = False
    progress.start()
    stop = threading.Event()

    def process(_item: int) -> None:
        path = Path("source.mbox")
        progress.record_worker(CLAMAV_START_PHASE, path, 0, 100)
        time.sleep(PROGRESS_REFRESH_SECONDS * 2.5)
        progress.record_worker("idle", path, 0, 0)

    try:
        run_file_workers((1,), 1, process, stop, progress.refresh)
    finally:
        progress.finish("completed")

    lines = [line for line in capsys.readouterr().err.splitlines() if line.startswith(CLAMAV_START_PHASE)]
    assert len(lines) >= 2
    assert any("workers=1:waiting for ClamAV startup:source.mbox" in line for line in lines)


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
