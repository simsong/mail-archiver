"""Document and window ownership for the cross-platform desktop application."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
from threading import RLock
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .bagit import initialize_bag
from .catalog import create_catalog, create_search, validate_catalog, validate_search
from .writer_lock import ArchiveBusyError, WriterLease


APPLICATION_PREFERENCES_VERSION = 1
RECENT_ARCHIVE_LIMIT = 10


class InvalidArchiveError(ValueError):
    """An archive path failed read-only document validation."""


class ApplicationPreferences(BaseModel):
    """Discardable, versioned state stored outside every archive."""

    version: Literal[1] = APPLICATION_PREFERENCES_VERSION
    last_archive: Path | None = None
    recent_archives: list[Path] = Field(default_factory=list, max_length=RECENT_ARCHIVE_LIMIT)


class ApplicationPreferencesStore:
    """Atomically maintain application document preferences."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or application_preferences_path()

    def read(self) -> ApplicationPreferences:
        if not self.path.exists():
            return ApplicationPreferences()
        return ApplicationPreferences.model_validate_json(self.path.read_text(encoding="utf-8"))

    def write(self, preferences: ApplicationPreferences) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=".application-preferences-",
                delete=False,
            ) as output:
                temporary = output.name
                output.write(preferences.model_dump_json(indent=2) + "\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)


class ArchiveDescriptor(BaseModel):
    """Stable identity plus the user-visible spelling of one archive path."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    identity: str
    display_path: Path | None = None
    canonical_path: Path | None = None
    untitled: bool = False


class IngestJob(BaseModel):
    """One operation shared by every window on an archive document."""

    model_config = ConfigDict(frozen=True)

    operation_id: str
    owner_window_id: str


class WindowGeometry(BaseModel):
    x: int | None = None
    y: int | None = None
    width: int = Field(default=1400, ge=320)
    height: int = Field(default=900, ge=240)


class SearchWindow(BaseModel):
    """Independent search-window state with a lifetime-stable document binding."""

    model_config = ConfigDict(validate_assignment=True)

    window_id: str = Field(default_factory=lambda: uuid4().hex)
    document_id: str
    query: str = ""
    sort_by: Literal["date", "subject", "sender"] = "date"
    sort_direction: Literal["ascending", "descending"] = "descending"
    selected_message: int | None = None
    mailbox_selections: list[str] = Field(default_factory=list)
    geometry: WindowGeometry = Field(default_factory=WindowGeometry)


class StartupResult(BaseModel):
    """Logical windows and nonfatal launch diagnostics for the GUI host."""

    windows: list[SearchWindow]
    errors: list[str] = Field(default_factory=list)


def application_preferences_path() -> Path:
    """Return a packaged-app-safe per-user preference path on each platform."""
    if sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
        application = "Mail Archiver"
    elif os.name == "nt":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        application = "Mail Archiver"
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        application = "mailarchiver"
    return root / application / "preferences.json"


def validate_archive(path: Path) -> tuple[Path, Path, str]:
    """Validate without mutation and return display path, canonical path, and identity."""
    display = Path(os.path.abspath(path.expanduser()))
    if not display.is_dir():
        raise InvalidArchiveError(f"archive does not exist or is not a directory: {display}")
    missing = [name for name in ("archive.sqlite3", "search.sqlite3") if not (display / name).is_file()]
    if missing:
        raise InvalidArchiveError(f"archive is missing {', '.join(missing)}: {display}")
    canonical = display.resolve(strict=True)
    identity = os.path.normcase(str(canonical))
    try:
        validate_catalog(canonical / "archive.sqlite3")
        validate_search(canonical / "search.sqlite3")
    except (OSError, RuntimeError, sqlite3.Error) as error:
        raise InvalidArchiveError(f"archive databases are invalid: {display}: {error}") from error
    return display, canonical, identity


def create_empty_archive(path: Path) -> "ArchiveDocument":
    """Initialize a selected new or empty destination while holding its writer lease."""
    display = Path(os.path.abspath(path.expanduser()))
    if display.is_symlink() or (display.exists() and not display.is_dir()):
        raise InvalidArchiveError(f"new archive destination is not a directory: {display}")
    if display.exists() and any(display.iterdir()):
        if (display / "archive.sqlite3").is_file() and (display / "search.sqlite3").is_file():
            raise InvalidArchiveError(f"archive already exists; use Open instead: {display}")
        raise InvalidArchiveError(f"new archive destination is not empty: {display}")
    display.mkdir(parents=False, exist_ok=True)
    canonical = display.resolve(strict=True)
    identity = os.path.normcase(str(canonical))
    lease = WriterLease.acquire(
        canonical,
        identity,
        "create archive",
        uuid4().hex,
        version("mailarchiver"),
    )
    try:
        initialize_bag(canonical)
        create_catalog(canonical / "archive.sqlite3").close()
        create_search(canonical / "search.sqlite3").close()
    finally:
        lease.release()
    return ArchiveDocument.open(display)


class ArchiveDocument:
    """Shared lifecycle and write state for every window on one archive."""

    def __init__(self, descriptor: ArchiveDescriptor) -> None:
        self.descriptor = descriptor
        self._window_ids: set[str] = set()
        self._child_window_ids: set[str] = set()
        self._ingest_job: IngestJob | None = None
        self._writer_lease: WriterLease | None = None
        self._generation = 0
        self._lock = RLock()

    @classmethod
    def open(cls, path: Path) -> "ArchiveDocument":
        display, canonical, identity = validate_archive(path)
        digest = sha256(identity.encode("utf-8")).hexdigest()[:20]
        return cls(
            ArchiveDescriptor(
                document_id=f"archive-{digest}",
                identity=identity,
                display_path=display,
                canonical_path=canonical,
            )
        )

    @classmethod
    def untitled(cls) -> "ArchiveDocument":
        identifier = uuid4().hex
        return cls(
            ArchiveDescriptor(
                document_id=f"untitled-{identifier}", identity=f"untitled:{identifier}", untitled=True
            )
        )

    @property
    def path(self) -> Path | None:
        return self.descriptor.canonical_path

    @property
    def display_path(self) -> Path | None:
        return self.descriptor.display_path

    @property
    def window_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._window_ids)

    @property
    def child_window_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._child_window_ids)

    @property
    def ingest_job(self) -> IngestJob | None:
        with self._lock:
            return self._ingest_job

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def attach_window(self, window_id: str) -> None:
        with self._lock:
            self._window_ids.add(window_id)

    def detach_window(self, window_id: str) -> None:
        with self._lock:
            self._window_ids.discard(window_id)

    def attach_child_window(self, window_id: str) -> None:
        with self._lock:
            self._child_window_ids.add(window_id)

    def detach_child_window(self, window_id: str) -> None:
        with self._lock:
            self._child_window_ids.discard(window_id)

    def begin_ingest(self, job: IngestJob, lease: WriterLease) -> None:
        """Publish an ingest only after issue 70's cross-process lease is held."""
        with self._lock:
            if self.descriptor.untitled:
                raise ValueError("save an untitled archive before importing")
            if not lease.acquired or lease.archive_identity != self.descriptor.identity:
                raise ValueError("an acquired writer lease for this archive is required")
            if self._ingest_job is not None:
                raise ArchiveBusyError(f"archive already has ingest {self._ingest_job.operation_id}")
            self._ingest_job = job
            self._writer_lease = lease

    def finish_ingest(self, operation_id: str, *, published: bool) -> tuple[str, ...]:
        """Clear the matching job and return windows that should refresh."""
        with self._lock:
            if self._ingest_job is None or self._ingest_job.operation_id != operation_id:
                raise ValueError(f"archive has no ingest {operation_id}")
            self._ingest_job = None
            lease, self._writer_lease = self._writer_lease, None
            if lease is not None:
                lease.release()
            if published:
                self._generation += 1
            return tuple(self._window_ids)

    def releasable(self) -> bool:
        with self._lock:
            return not self._window_ids and not self._child_window_ids and self._ingest_job is None


class ApplicationController:
    """Own archive sessions, logical windows, preferences, and active routing."""

    def __init__(self, preferences: ApplicationPreferencesStore | None = None) -> None:
        self.preferences_store = preferences or ApplicationPreferencesStore()
        self._documents: dict[str, ArchiveDocument] = {}
        self._documents_by_identity: dict[str, ArchiveDocument] = {}
        self._windows: dict[str, SearchWindow] = {}
        self._active_window_id: str | None = None
        self._lock = RLock()
        self.preference_error: str | None = None
        try:
            self._preferences = self.preferences_store.read()
        except (OSError, ValueError) as error:
            self._preferences = ApplicationPreferences()
            self.preference_error = f"Could not read application preferences: {error}"

    @property
    def preferences(self) -> ApplicationPreferences:
        return self._preferences.model_copy(deep=True)

    @property
    def active_window(self) -> SearchWindow | None:
        with self._lock:
            return self._windows.get(self._active_window_id or "")

    @property
    def active_document(self) -> ArchiveDocument | None:
        window = self.active_window
        return self._documents.get(window.document_id) if window else None

    def document(self, document_id: str) -> ArchiveDocument:
        try:
            return self._documents[document_id]
        except KeyError as error:
            raise ValueError(f"unknown archive document {document_id}") from error

    def new_document(self) -> ArchiveDocument:
        document = ArchiveDocument.untitled()
        with self._lock:
            self._documents[document.descriptor.document_id] = document
        return document

    def create_document(self, path: Path) -> ArchiveDocument:
        """Create a selected empty archive and register it as the current document."""
        candidate = create_empty_archive(path)
        with self._lock:
            self._documents[candidate.descriptor.document_id] = candidate
            self._documents_by_identity[candidate.descriptor.identity] = candidate
            self._record_recent(candidate)
        return candidate

    def open_document(self, path: Path) -> ArchiveDocument:
        candidate = ArchiveDocument.open(path)
        with self._lock:
            document = self._documents_by_identity.get(candidate.descriptor.identity)
            if document is None:
                document = candidate
                self._documents[document.descriptor.document_id] = document
                self._documents_by_identity[document.descriptor.identity] = document
            self._record_recent(document)
            return document

    def open_recent_document(self, path: Path) -> ArchiveDocument:
        try:
            return self.open_document(path)
        except InvalidArchiveError:
            self._forget_recent(path)
            raise

    def new_search_window(self, document: ArchiveDocument | None = None) -> SearchWindow:
        target = document or self.active_document
        if target is None:
            raise ValueError("no active archive document")
        window = SearchWindow(document_id=target.descriptor.document_id)
        with self._lock:
            self._windows[window.window_id] = window
            target.attach_window(window.window_id)
            self._active_window_id = window.window_id
        return window

    def activate_window(self, window_id: str) -> SearchWindow:
        with self._lock:
            try:
                window = self._windows[window_id]
            except KeyError as error:
                raise ValueError(f"unknown search window {window_id}") from error
            self._active_window_id = window_id
            return window

    def close_window(self, window_id: str) -> ArchiveDocument:
        with self._lock:
            try:
                window = self._windows[window_id]
            except KeyError as error:
                raise ValueError(f"unknown search window {window_id}") from error
            document = self._documents[window.document_id]
            if document.ingest_job is not None and document.ingest_job.owner_window_id == window_id:
                raise ArchiveBusyError("the window running Import cannot close until Import finishes")
            self._windows.pop(window_id)
            document.detach_window(window_id)
            if self._active_window_id == window_id:
                self._active_window_id = next(reversed(self._windows), None)
            self._release_if_unused(document)
            return document

    def can_close_window(self, window_id: str) -> bool:
        """Return false only for the search window that owns the active ingest."""
        with self._lock:
            window = self._windows.get(window_id)
            if window is None:
                return False
            document = self._documents[window.document_id]
            return document.ingest_job is None or document.ingest_job.owner_window_id != window_id

    def windows(self) -> tuple[SearchWindow, ...]:
        """Return logical search windows in stable creation order."""
        with self._lock:
            return tuple(self._windows.values())

    def documents(self) -> tuple[ArchiveDocument, ...]:
        """Return live archive documents in stable creation order."""
        with self._lock:
            return tuple(self._documents.values())

    def import_document(self) -> ArchiveDocument:
        document = self.active_document
        if document is None or document.descriptor.untitled:
            raise ValueError("save and activate an archive before importing")
        if document.ingest_job is not None:
            raise ArchiveBusyError(f"archive already has ingest {document.ingest_job.operation_id}")
        return document

    def begin_ingest(self, document_id: str, job: IngestJob, lease: WriterLease) -> None:
        self.document(document_id).begin_ingest(job, lease)

    def finish_ingest(self, document_id: str, operation_id: str, *, published: bool) -> tuple[str, ...]:
        document = self.document(document_id)
        windows = document.finish_ingest(operation_id, published=published)
        with self._lock:
            self._release_if_unused(document)
        return windows

    def attach_child_window(self, document_id: str, window_id: str) -> None:
        self.document(document_id).attach_child_window(window_id)

    def close_child_window(self, document_id: str, window_id: str) -> None:
        document = self.document(document_id)
        document.detach_child_window(window_id)
        with self._lock:
            self._release_if_unused(document)

    def handle_open_documents(self, paths: tuple[Path, ...]) -> StartupResult:
        windows: list[SearchWindow] = []
        errors: list[str] = []
        for path in paths:
            try:
                windows.append(self.new_search_window(self.open_document(path)))
            except InvalidArchiveError as error:
                errors.append(str(error))
        return StartupResult(windows=windows, errors=errors)

    def startup(self, explicit_paths: tuple[Path, ...] = ()) -> StartupResult:
        errors = [self.preference_error] if self.preference_error else []
        if explicit_paths:
            result = self.handle_open_documents(explicit_paths)
            errors.extend(result.errors)
            if result.windows:
                return StartupResult(windows=result.windows, errors=errors)
        elif self._preferences.last_archive is not None:
            try:
                document = self.open_recent_document(self._preferences.last_archive)
                return StartupResult(windows=[self.new_search_window(document)], errors=errors)
            except InvalidArchiveError as error:
                errors.append(str(error))
        document = self.new_document()
        return StartupResult(windows=[self.new_search_window(document)], errors=errors)

    def _record_recent(self, document: ArchiveDocument) -> None:
        assert document.display_path is not None
        path = document.display_path
        recent = [item for item in self._preferences.recent_archives if item != path]
        recent.insert(0, path)
        self._preferences = ApplicationPreferences(
            last_archive=path, recent_archives=recent[:RECENT_ARCHIVE_LIMIT]
        )
        self._write_preferences()

    def _forget_recent(self, path: Path) -> None:
        display = Path(os.path.abspath(path.expanduser()))
        recent = [item for item in self._preferences.recent_archives if item != display]
        last = None if self._preferences.last_archive == display else self._preferences.last_archive
        self._preferences = ApplicationPreferences(last_archive=last, recent_archives=recent)
        self._write_preferences()

    def _write_preferences(self) -> None:
        try:
            self.preferences_store.write(self._preferences)
            self.preference_error = None
        except OSError as error:
            self.preference_error = f"Could not write application preferences: {error}"

    def _release_if_unused(self, document: ArchiveDocument) -> None:
        if not document.releasable():
            return
        self._documents.pop(document.descriptor.document_id, None)
        if not document.descriptor.untitled:
            self._documents_by_identity.pop(document.descriptor.identity, None)
