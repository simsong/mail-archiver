"""Start, health-check, use, and stop the ingest run's on-demand ClamAV daemon."""

from __future__ import annotations

import fcntl
import os
import subprocess
import tempfile
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import BinaryIO


CLAMD = os.environ.get("MAILARCHIVER_CLAMD", "/opt/homebrew/sbin/clamd")
CLAMDSCAN = os.environ.get("MAILARCHIVER_CLAMDSCAN", "/opt/homebrew/bin/clamdscan")
CLAMD_CONFIG = os.environ.get("MAILARCHIVER_CLAMD_CONFIG", "/opt/homebrew/etc/clamav/clamd.conf")
CLAMD_SOCKET = Path(os.environ.get("MAILARCHIVER_CLAMD_SOCKET", "/private/tmp/clamd.sock"))
CLAMD_START_TIMEOUT_SECONDS = 120
CLAMD_START_POLL_SECONDS = 0.25


class ClamScannerStartupError(RuntimeError):
    """The configured ClamAV daemon could not become ready."""


class ClamScanner(AbstractContextManager["ClamScanner"]):
    """Use an existing daemon or one temporary daemon for one ingest run."""

    def __init__(self, status_callback: Callable[[], None] | None = None) -> None:
        self.process: subprocess.Popen[bytes] | None = None
        self.status_callback = status_callback
        self.diagnostics: BinaryIO | None = None
        self.runtime_directory: tempfile.TemporaryDirectory[str] | None = None
        self.start_lock: BinaryIO | None = None
        self.log_path: Path | None = None
        self.configuration_path = Path(CLAMD_CONFIG)
        self.socket_path = CLAMD_SOCKET
        self.owns_socket = False

    def __enter__(self) -> "ClamScanner":
        self.start_lock = Path(CLAMD_CONFIG).open("rb")
        fcntl.flock(self.start_lock.fileno(), fcntl.LOCK_EX)
        if CLAMD_SOCKET.exists() and self.available():
            self.release_start_lock()
            return self
        CLAMD_SOCKET.unlink(missing_ok=True)
        configuration_path = self.prepare_runtime_files()
        self.configuration_path = configuration_path
        try:
            self.process = subprocess.Popen(
                [CLAMD, "--foreground", f"--config-file={configuration_path}"],
                stdout=self.diagnostics,
                stderr=self.diagnostics,
            )
        except OSError as error:
            self.__exit__()
            raise ClamScannerStartupError(f"cannot start {CLAMD}: {error}") from error
        try:
            deadline = time.monotonic() + CLAMD_START_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                if self.status_callback is not None:
                    self.status_callback()
                if self.socket_path.exists() and self.available():
                    self.owns_socket = True
                    return self
                returncode = self.process.poll()
                if returncode is not None:
                    raise self.startup_error(f"clamd exited with status {returncode}")
                time.sleep(CLAMD_START_POLL_SECONDS)
        except BaseException:
            self.__exit__()
            raise
        error = self.startup_error(f"clamd did not become ready within {CLAMD_START_TIMEOUT_SECONDS} seconds")
        self.__exit__()
        raise error

    def prepare_runtime_files(self) -> Path:
        """Create and verify private paths for one mailarchiver-owned daemon."""
        try:
            self.runtime_directory = tempfile.TemporaryDirectory(
                prefix="mailarchiver-clamd-", dir=Path(CLAMD_CONFIG).parent
            )
            runtime_path = Path(self.runtime_directory.name)
            self.log_path = runtime_path / "clamd.log"
            descriptor = os.open(self.log_path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            self.diagnostics = os.fdopen(descriptor, "w+b")
            if not self.log_path.is_file() or not os.access(self.log_path, os.W_OK):
                raise OSError(f"private clamd log is not writable: {self.log_path}")
            configuration_path = runtime_path / "clamd.conf"
            configuration = Path(CLAMD_CONFIG).read_text(encoding="utf-8")
            private_directives = {"LogFile", "LogSyslog", "PidFile"}
            lines = [
                line
                for line in configuration.splitlines()
                if not line.strip()
                or line.lstrip().startswith("#")
                or line.split(maxsplit=1)[0] not in private_directives
            ]
            configuration_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            configuration_path.chmod(0o600)
            return configuration_path
        except OSError as error:
            self.__exit__()
            raise ClamScannerStartupError(
                f"cannot create private clamd runtime files beside {CLAMD_CONFIG}: {error}"
            ) from error

    def startup_error(self, reason: str) -> ClamScannerStartupError:
        """Include clamd's startup output when it is available."""
        details = []
        if self.diagnostics is not None:
            self.diagnostics.flush()
            self.diagnostics.seek(0)
            detail = self.diagnostics.read().decode("utf-8", "replace").strip()
            if detail:
                details.append(detail[-4096:])
        if details:
            return ClamScannerStartupError(f"{reason}: {'; '.join(details)}")
        return ClamScannerStartupError(
            f"{reason}; no diagnostics were written to the private clamd log"
        )

    def __exit__(self, *_: object) -> None:
        process, self.process = self.process, None
        if process is not None:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if self.owns_socket:
            self.socket_path.unlink(missing_ok=True)
        self.configuration_path = Path(CLAMD_CONFIG)
        self.socket_path = CLAMD_SOCKET
        self.owns_socket = False
        diagnostics, self.diagnostics = self.diagnostics, None
        if diagnostics is not None:
            diagnostics.close()
        self.log_path = None
        runtime_directory, self.runtime_directory = self.runtime_directory, None
        if runtime_directory is not None:
            runtime_directory.cleanup()
        self.release_start_lock()

    def release_start_lock(self) -> None:
        """Release this process's advisory ownership of the configured socket."""
        start_lock, self.start_lock = self.start_lock, None
        if start_lock is not None:
            fcntl.flock(start_lock.fileno(), fcntl.LOCK_UN)
            start_lock.close()

    def available(self) -> bool:
        return subprocess.run(
            [CLAMDSCAN, f"--config-file={self.configuration_path}", "--ping=1"],
            check=False,
            capture_output=True,
        ).returncode == 0

    def infected(self, raw: bytes) -> bool:
        with tempfile.NamedTemporaryFile(prefix="mailarchiver-", delete=False) as handle:
            handle.write(raw)
            temporary = handle.name
        try:
            result = subprocess.run(
                [CLAMDSCAN, f"--config-file={self.configuration_path}", "--stream", temporary],
                check=False,
                capture_output=True,
            )
            if result.returncode not in (0, 1):
                raise RuntimeError(result.stderr.decode("utf-8", "replace"))
            return result.returncode == 1
        finally:
            os.unlink(temporary)
