"""Verify ingest progress and bounded mailfile-level worker concurrency."""

from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from mailarchiver.__main__ import (
    CLAMAV_START_PHASE,
    PROGRESS_REFRESH_SECONDS,
    ProgressReporter,
    run_file_workers,
)


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
    assert "\x1b[7A" in output
    rendered = [part.split("\n", 1)[0] for part in output.split("\r\x1b[2K")[1:]]
    assert rendered and all(len(line) <= progress.terminal_columns for line in rendered)
