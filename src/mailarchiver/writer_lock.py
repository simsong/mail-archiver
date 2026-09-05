"""Cross-process exclusive writer lease for one archive."""

from __future__ import annotations

import errno
import json
import os
import socket
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

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
    ) -> "WriterLease":
        status = archive / LOCK_RELATIVE_PATH.parent
        status.mkdir(parents=True, exist_ok=True)
        lock_path = archive / LOCK_RELATIVE_PATH
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(lock_path, flags, 0o600)
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
                started_at=datetime.now(timezone.utc),
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

    def __enter__(self) -> "WriterLease":
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


if os.name == "nt":

    def _acquire_nonblocking(handle: BinaryIO) -> None:
        import msvcrt  # pylint: disable=import-error,import-outside-toplevel

        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _release(handle: BinaryIO) -> None:
        import msvcrt  # pylint: disable=import-error,import-outside-toplevel

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:

    def _acquire_nonblocking(handle: BinaryIO) -> None:
        import fcntl  # pylint: disable=import-outside-toplevel

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _release(handle: BinaryIO) -> None:
        import fcntl  # pylint: disable=import-outside-toplevel

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
