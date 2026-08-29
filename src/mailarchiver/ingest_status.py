"""Typed, append-by-run ingest status files shared by the CLI and GUI."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

STATUS_FORMAT = "tag:simson.net,2026:mailarchiver/ingest-status"
STATUS_VERSION = 1
STATUS_DIRECTORY = "status"
STATUS_PREFIX = "ingest-"
STALE_AFTER_SECONDS = 5
STATUS_REFRESH_SECONDS = 1
IngestState = Literal["running", "completed", "interrupted", "disk-full", "failed", "stale"]


class YearProgress(BaseModel):
    year: int
    messages: int = 0


class IngestCounts(BaseModel):
    archived: int = 0
    duplicates: int = 0
    autosaves: int = 0
    metadata_excluded: int = 0
    infected: int = 0
    skipped_files: int = 0
    unchanged_sources: int = 0


class IngestWorkerStatus(BaseModel):
    worker: int
    phase: str = "idle"
    path: str | None = None
    bytes_done: int = 0
    bytes_total: int = 0
    activity_done: int | None = None
    activity_total: int | None = None
    activity_unit: str | None = None
    last_path: str | None = None
    files_processed: int = 0
    messages_processed: int = 0


class IngestStatus(BaseModel):
    """Complete current or final state of one ingest process."""

    format_id: Literal[STATUS_FORMAT] = STATUS_FORMAT
    format_version: Literal[STATUS_VERSION] = STATUS_VERSION
    status_id: str = Field(pattern=r"^ingest-[A-Za-z0-9.-]+$")
    archive: str
    run_pk: int
    process_id: int
    source_roots: list[str]
    started_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    state: IngestState = "running"
    phase: str
    elapsed_seconds: float
    phase_elapsed_seconds: float
    processed_messages: int
    message_rate: float
    files_processed: int
    files_total: int
    bytes_processed: int
    bytes_total: int
    percent: float
    eta: str
    earliest_date: datetime | None = None
    latest_date: datetime | None = None
    current_year: int | None = None
    current_year_messages: int = 0
    active_workers: int
    peak_workers: int
    configured_workers: int
    workers: list[IngestWorkerStatus]
    counts: IngestCounts
    years: list[YearProgress]
    failure_detail: str | None = None

    @field_validator("started_at", "updated_at", "completed_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("ingest status timestamps must include a timezone")
        return None if value is None else value.astimezone(timezone.utc)

    def effective(self, now: datetime | None = None) -> IngestStatus:
        """Classify an abandoned running snapshot without changing its file."""
        if self.state != "running":
            return self
        checked_at = now or datetime.now(timezone.utc)
        if (checked_at - self.updated_at).total_seconds() <= STALE_AFTER_SECONDS:
            return self
        return self.model_copy(update={"state": "stale", "phase": "status heartbeat lost"})


class IngestStatusReadError(BaseModel):
    filename: str
    detail: str


class IngestHistory(BaseModel):
    statuses: list[IngestStatus]
    errors: list[IngestStatusReadError]


def status_directory(archive: Path) -> Path:
    return archive / STATUS_DIRECTORY


def new_status_id(started_at: datetime, run_pk: int, process_id: int) -> str:
    timestamp = started_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return f"{STATUS_PREFIX}{timestamp}-run-{run_pk}-pid-{process_id}-{uuid4().hex[:8]}"


def status_path(archive: Path, status_id: str) -> Path:
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-"
    if not status_id.startswith(STATUS_PREFIX) or any(
        character not in allowed for character in status_id
    ):
        raise ValueError("invalid ingest status identifier")
    return status_directory(archive) / f"{status_id}.json"


class IngestStatusFile:
    """Atomically replace one run-specific status file while preserving prior runs."""

    def __init__(self, archive: Path, status_id: str) -> None:
        self.path = status_path(archive, status_id)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.exists():
            raise FileExistsError(f"ingest status file already exists: {self.path}")

    def write(self, status: IngestStatus) -> None:
        if self.path.stem != status.status_id:
            raise ValueError("status object does not match its run-specific filename")
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.{uuid4().hex}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                output.write(status.model_dump_json(indent=2))
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)


def read_ingest_status(path: Path, now: datetime | None = None) -> IngestStatus:
    status = IngestStatus.model_validate_json(path.read_text(encoding="utf-8"))
    if path.stem != status.status_id:
        raise ValueError("status identifier does not match filename")
    return status.effective(now)


def read_ingest_history(archive: Path, now: datetime | None = None) -> IngestHistory:
    statuses: list[IngestStatus] = []
    errors: list[IngestStatusReadError] = []
    directory = status_directory(archive)
    if not directory.is_dir():
        return IngestHistory(statuses=[], errors=[])
    for path in directory.glob(f"{STATUS_PREFIX}*.json"):
        try:
            statuses.append(read_ingest_status(path, now))
        except (OSError, ValueError) as error:
            errors.append(IngestStatusReadError(filename=path.name, detail=str(error)))
    statuses.sort(key=lambda status: (status.started_at, status.status_id), reverse=True)
    errors.sort(key=lambda error: error.filename, reverse=True)
    return IngestHistory(statuses=statuses, errors=errors)


def latest_ingest_status(archive: Path, now: datetime | None = None) -> IngestStatus | None:
    directory = status_directory(archive)
    if not directory.is_dir():
        return None
    for path in sorted(directory.glob(f"{STATUS_PREFIX}*.json"), reverse=True):
        try:
            return read_ingest_status(path, now)
        except (OSError, ValueError):
            continue
    return None
