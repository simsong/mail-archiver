"""Exercise the real cross-process archive writer lease."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from mailarchiver.catalog import create_catalog, create_search
from mailarchiver.writer_lock import ArchiveBusyError, WriterLease


def acquire(archive: Path, operation_id: str) -> WriterLease:
    identity = os.path.normcase(str(archive.resolve()))
    return WriterLease.acquire(archive, identity, "test writer", operation_id, "test")


def test_writer_lease_excludes_another_process_and_reports_holder(tmp_path: Path) -> None:
    """Requirement: a live OS lock excludes another process and reports diagnostic ownership."""
    archive = tmp_path / "archive"
    archive.mkdir()
    script = (
        "import os,sys\n"
        "from pathlib import Path\n"
        "from mailarchiver.writer_lock import WriterLease\n"
        "p=Path(sys.argv[1]); i=os.path.normcase(str(p.resolve()))\n"
        "lease=WriterLease.acquire(p,i,'child import','child-operation','test')\n"
        "print('locked',flush=True)\n"
        "sys.stdin.readline()\n"
        "lease.release()\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(archive)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "locked"
        with pytest.raises(ArchiveBusyError, match=r"child import by PID \d+"):
            acquire(archive, "parent-operation")
    finally:
        assert child.stdin is not None
        child.stdin.write("release\n")
        child.stdin.flush()
        child.wait(timeout=10)
    assert child.returncode == 0, child.stderr.read() if child.stderr else ""


def test_stale_lock_file_does_not_block_and_aliases_contend(tmp_path: Path) -> None:
    """Requirement: file presence is harmless, while aliases share the live archive lock."""
    archive = tmp_path / "archive"
    archive.mkdir()
    first = acquire(archive, "first")
    first.release()

    second = acquire(archive / ".." / archive.name, "second")
    try:
        with pytest.raises(ArchiveBusyError):
            acquire(archive, "contender")
    finally:
        second.release()

    third = acquire(archive, "third")
    third.release()


def test_writer_lease_releases_after_forced_process_death(tmp_path: Path) -> None:
    """Requirement: a crashed writer cannot leave the archive permanently locked."""
    archive = tmp_path / "archive"
    archive.mkdir()
    script = (
        "import os,sys,time\n"
        "from pathlib import Path\n"
        "from mailarchiver.writer_lock import WriterLease\n"
        "p=Path(sys.argv[1]); i=os.path.normcase(str(p.resolve()))\n"
        "lease=WriterLease.acquire(p,i,'crash test','crash','test')\n"
        "print('locked',flush=True)\n"
        "time.sleep(60)\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(archive)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert child.stdout is not None
    assert child.stdout.readline().strip() == "locked"
    child.kill()
    child.wait(timeout=10)

    lease = acquire(archive, "after-crash")
    lease.release()


def test_cli_refresh_index_uses_the_same_writer_lease(tmp_path: Path) -> None:
    """Requirement: a command-line search-index writer fails clearly instead of waiting."""
    archive = tmp_path / "archive"
    archive.mkdir()
    create_catalog(archive / "archive.sqlite3").close()
    create_search(archive / "search.sqlite3").close()
    lease = acquire(archive, "gui-import")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "mailarchiver", "--archive", str(archive), "refresh-index"],
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        lease.release()

    assert result.returncode == 1
    assert "archive busy" in result.stderr


def test_different_archives_do_not_contend(tmp_path: Path) -> None:
    """Requirement: independent archive writers may hold leases concurrently."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    first_lease = acquire(first, "first")
    second_lease = acquire(second, "second")
    second_lease.release()
    first_lease.release()
