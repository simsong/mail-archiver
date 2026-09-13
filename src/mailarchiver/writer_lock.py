# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Cross-process exclusive writer lease for one archive."""

from __future__ import annotations

import errno
import json
import os
import socket
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, Self

from pydantic import BaseModel, ConfigDict, PrivateAttr

LOCK_RELATIVE_PATH = Path("status") / "archive-write.lock"


class ArchiveBusyError(RuntimeError):
    """Another live writer owns the archive lease."""


class WriterLeaseMetadata(BaseModel):
    """Human-readable diagnostics; the live OS lock remains authoritative."""

    operation: str
    operation_id: str
    process_id: int
    hostname: str
    started_at: datetime
    application_version: str
    archive_identity: str


class WriterLease(BaseModel):
    """A typed handle retaining a nonblocking OS file lock until release."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    archive_identity: str
    lock_path: Path
    metadata: WriterLeaseMetadata
    acquired: bool = True
    _handle: BinaryIO | None = PrivateAttr(default=None)

    @classmethod
    def acquire(
        cls,
        archive: Path,
        archive_identity: str,
        operation: str,
        operation_id: str,
        application_version: str,
        *,
        create: bool = False,
    ) -> WriterLease:
        if os.name == "nt":
            raise OSError("Windows archive writing is not supported; planned for v1.1.0")
        lock_path = archive / LOCK_RELATIVE_PATH
        with _archive_directory(archive, create) as archive_fd:
            try:
                os.mkdir("status", mode=0o700, dir_fd=archive_fd)
            except FileExistsError:
                pass
            status_fd = os.open("status", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=archive_fd)
            try:
                descriptor = _open_regular("archive-write.lock", status_fd)
            finally:
                os.close(status_fd)
        handle = os.fdopen(descriptor, "r+b", buffering=0)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError(f"archive writer lock is not a regular file: {lock_path}")
            _acquire_nonblocking(handle)
        except BaseException as error:
            holder = _read_holder(handle)
            handle.close()
            if _is_contention(error):
                detail = f": {holder}" if holder else ""
                raise ArchiveBusyError(f"archive is busy with another writer{detail}") from error
            raise
        try:
            metadata = WriterLeaseMetadata(
                operation=operation,
                operation_id=operation_id,
                process_id=os.getpid(),
                hostname=socket.gethostname(),
                started_at=datetime.now(UTC),
                application_version=application_version,
                archive_identity=archive_identity,
            )
            payload = metadata.model_dump_json(indent=2).encode("utf-8") + b"\n"
            handle.seek(0)
            handle.write(payload)
            handle.truncate()
            os.fsync(handle.fileno())
        except BaseException:
            _release(handle)
            handle.close()
            raise
        lease = cls(archive_identity=archive_identity, lock_path=lock_path, metadata=metadata)
        lease._handle = handle
        return lease

    def release(self) -> None:
        """Release the OS lock; the diagnostic file may safely remain."""
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            _release(handle)
        finally:
            handle.close()
            self.acquired = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


def _read_holder(handle: BinaryIO) -> str:
    try:
        handle.seek(0)
        raw = handle.read(16_384)
        metadata = WriterLeaseMetadata.model_validate_json(raw)
        return (
            f"{metadata.operation} by PID {metadata.process_id} on {metadata.hostname} "
            f"since {metadata.started_at.isoformat()}"
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return ""


def _is_contention(error: BaseException) -> bool:
    return isinstance(error, (BlockingIOError, PermissionError)) or (
        isinstance(error, OSError) and error.errno in {errno.EACCES, errno.EAGAIN}
    )


def _open_regular(name: str, directory: int) -> int:
    descriptor = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        os.close(descriptor)
        raise ValueError("archive lock must be a regular file with one link")
    return descriptor


@contextmanager
def _archive_directory(archive: Path, create: bool) -> Iterator[int]:
    """Serialize target creation under a parent lock, then pin the archive directory."""
    parent_fd = None
    guard = None
    try:
        if create:
            archive.parent.mkdir(parents=True, exist_ok=True)
            parent_fd = os.open(archive.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            guard = os.fdopen(_open_regular(".mailarchiver-create.lock", parent_fd), "r+b", buffering=0)
            try:
                _acquire_nonblocking(guard)
            except OSError as error:
                if _is_contention(error):
                    raise ArchiveBusyError("another archive creator is active in this directory") from error
                raise
            try:
                os.mkdir(archive.name, mode=0o700, dir_fd=parent_fd)
            except FileExistsError:
                pass
        descriptor = os.open(archive, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            yield descriptor
        finally:
            os.close(descriptor)
    finally:
        if guard is not None:
            guard.close()
        if parent_fd is not None:
            os.close(parent_fd)


def _acquire_nonblocking(handle: BinaryIO) -> None:
    import fcntl  # pylint: disable=import-outside-toplevel

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release(handle: BinaryIO) -> None:
    import fcntl  # pylint: disable=import-outside-toplevel

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
