# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Requirement: ClamAV health checks and message scans have hard subprocess deadlines."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="ClamScanner uses POSIX fcntl locking and these deadline fixtures require /bin/sh",
)


def sleeping_executable(path: Path) -> Path:
    """Create a real subprocess that does not finish before a short test deadline."""
    path.write_text("#!/bin/sh\nexec sleep 10\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_clamav_health_probe_has_a_hard_deadline(tmp_path: Path) -> None:
    """Requirement: a stuck health probe cannot consume the daemon startup deadline."""
    from mailarchiver.scanner import ClamScanner

    scanner = ClamScanner(
        clamdscan=str(sleeping_executable(tmp_path / "sleeping-clamdscan")),
        ping_timeout_seconds=0.05,
    )

    assert not scanner.available()


@pytest.mark.parametrize("helper_state", ["missing", "not-executable", "invalid-executable"])
def test_clamav_health_probe_execution_failure_is_unavailable(tmp_path: Path, helper_state: str) -> None:
    """Requirement: helper execution failure is distinct from an unready daemon."""
    from mailarchiver.scanner import ClamScanner, ClamScannerStartupError

    helper = tmp_path / "clamdscan"
    if helper_state != "missing":
        helper.write_text("not an executable format\n", encoding="utf-8")
        helper.chmod(0o755 if helper_state == "invalid-executable" else 0o644)

    with pytest.raises(ClamScannerStartupError, match="cannot execute scanner health probe"):
        ClamScanner(clamdscan=str(helper)).available()


@pytest.mark.parametrize("existing_socket", [False, True])
def test_probe_execution_failure_does_not_start_daemon_or_unlink_socket(tmp_path: Path, existing_socket: bool) -> None:
    """Requirement: unlaunchable probes abort startup without changing another daemon's socket."""
    config = tmp_path / "clamd.conf"
    config.write_text("# fixture\n", encoding="utf-8")
    daemon = tmp_path / "clamd"
    marker = tmp_path / "daemon-started"
    daemon.write_text('#!/bin/sh\ntouch "$MAILARCHIVER_TEST_DAEMON_MARKER"\n', encoding="utf-8")
    daemon.chmod(0o755)
    # Keep the Unix socket path below macOS's length limit, independent of pytest's root.
    with tempfile.TemporaryDirectory(prefix="clam-test-", dir="/tmp") as runtime, socket.socket(socket.AF_UNIX) as server:
        socket_path = Path(runtime) / "s"
        if existing_socket:
            server.bind(str(socket_path))
        environment = os.environ.copy()
        environment.update(
            MAILARCHIVER_CLAMD=str(daemon),
            MAILARCHIVER_CLAMDSCAN=str(tmp_path / "missing-clamdscan"),
            MAILARCHIVER_CLAMD_CONFIG=str(config),
            MAILARCHIVER_CLAMD_SOCKET=str(socket_path),
            MAILARCHIVER_TEST_DAEMON_MARKER=str(marker),
        )
        result = subprocess.run(
            [sys.executable, "-c", """
import fcntl
from mailarchiver.scanner import CLAMD_CONFIG, ClamScanner, ClamScannerStartupError
scanner = ClamScanner()
try:
    scanner.__enter__()
except ClamScannerStartupError as error:
    assert 'cannot execute scanner health probe' in str(error)
else:
    raise AssertionError('startup unexpectedly succeeded')
assert scanner.start_lock is None
assert scanner.process is None
with open(CLAMD_CONFIG, 'rb') as lock:
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
"""],
            env=environment, capture_output=True, text=True, check=False, timeout=10,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert socket_path.exists() == existing_socket
        assert not marker.exists()


def test_clamav_scan_timeout_cleans_up_message_bytes(tmp_path: Path) -> None:
    """Requirement: a stuck scan fails and removes its plaintext temporary message."""
    from mailarchiver.scanner import ClamScanner

    scan_directory = tmp_path / "scan"
    scan_directory.mkdir()
    scanner = ClamScanner(
        clamdscan=str(sleeping_executable(tmp_path / "sleeping-clamdscan")),
        scan_timeout_seconds=0.05,
        scan_temporary_directory=scan_directory,
    )

    with pytest.raises(RuntimeError, match=r"clamdscan timed out after 0\.05 seconds"):
        scanner.infected(b"From: sender@example.test\n\nprivate body\n")

    assert list(scan_directory.iterdir()) == []
