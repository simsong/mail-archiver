"""Framework-owned integrity controls for the canonical Mailbag archive."""

from __future__ import annotations

import hashlib
import sqlite3
from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from .bagit import initialize_bag, write_bag_checkpoint
from .plugin_api import ArchiveReference, IntegrityEvidence, ProgressEvent
from .standalone_verify import install_archive_verifier, verify_archive


BAGIT_DECLARATION_CONTROL = "mailarchiver.archive.bagit-declaration.v1"
STANDALONE_VERIFIER_CONTROL = "mailarchiver.archive.standalone-verifier.v1"
PAYLOAD_MANIFEST_CONTROL = "mailarchiver.archive.payload-manifest.v1"
TAG_MANIFEST_CONTROL = "mailarchiver.archive.tag-manifest.v1"

ArchiveIntegrityEvent = IntegrityEvidence | ProgressEvent


class ArchiveIntegrityError(ValueError):
    """One or more independent archive-integrity checks failed."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = tuple(errors)
        super().__init__("archive integrity verification failed: " + "; ".join(errors))


class ArchiveIntegrityControls(ABC):
    """Archive-format controls invoked by the framework, never by a source."""

    @abstractmethod
    def initialize(self, archive: ArchiveReference) -> Iterator[ArchiveIntegrityEvent]:
        """Initialize the archive's fixed integrity declarations."""

    @abstractmethod
    def checkpoint(
        self,
        archive: ArchiveReference,
        catalog: sqlite3.Connection,
        packaged_at: datetime | None = None,
    ) -> Iterator[ArchiveIntegrityEvent]:
        """Publish and describe one complete archive-integrity checkpoint."""

    @abstractmethod
    def verify(self, archive: ArchiveReference) -> Iterator[ArchiveIntegrityEvent]:
        """Verify one published checkpoint independently of its mail sources."""


def _file_evidence(control_id: str, archive: ArchiveReference, path: Path) -> IntegrityEvidence:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return IntegrityEvidence(
        control_id=control_id,
        subject_id=f"{archive.archive_id}:{path.relative_to(archive.root).as_posix()}",
        evidence_kind="cryptographic-fixity",
        algorithm="sha256",
        value=digest.hexdigest(),
        byte_length=path.stat().st_size,
    )


class MailbagArchiveIntegrityControls(ArchiveIntegrityControls):
    """Adapt the native BagIt/Mailbag checkpoint and standalone verifier."""

    def initialize(self, archive: ArchiveReference) -> Iterator[ArchiveIntegrityEvent]:
        yield ProgressEvent(
            work_id=archive.archive_id,
            phase="initializing archive integrity",
            completed=0,
            total=2,
            unit="controls",
        )
        initialize_bag(archive.root)
        yield _file_evidence(BAGIT_DECLARATION_CONTROL, archive, archive.root / "bagit.txt")
        verifier = install_archive_verifier(archive.root)
        yield _file_evidence(STANDALONE_VERIFIER_CONTROL, archive, verifier)
        yield ProgressEvent(
            work_id=archive.archive_id,
            phase="initializing archive integrity",
            completed=2,
            total=2,
            unit="controls",
        )

    def checkpoint(
        self,
        archive: ArchiveReference,
        catalog: sqlite3.Connection,
        packaged_at: datetime | None = None,
    ) -> Iterator[ArchiveIntegrityEvent]:
        yield ProgressEvent(
            work_id=archive.archive_id,
            phase="publishing archive integrity",
            completed=0,
            total=1,
            unit="checkpoint",
        )
        write_bag_checkpoint(archive.root, catalog, packaged_at)
        yield _file_evidence(PAYLOAD_MANIFEST_CONTROL, archive, archive.root / "manifest-sha256.txt")
        yield _file_evidence(TAG_MANIFEST_CONTROL, archive, archive.root / "tagmanifest-sha256.txt")
        yield ProgressEvent(
            work_id=archive.archive_id,
            phase="publishing archive integrity",
            completed=1,
            total=1,
            unit="checkpoint",
        )

    def verify(self, archive: ArchiveReference) -> Iterator[ArchiveIntegrityEvent]:
        yield ProgressEvent(
            work_id=archive.archive_id,
            phase="verifying archive integrity",
            completed=0,
            total=1,
            unit="checkpoint",
        )
        errors = verify_archive(archive.root)
        if errors:
            raise ArchiveIntegrityError(errors)
        yield _file_evidence(PAYLOAD_MANIFEST_CONTROL, archive, archive.root / "manifest-sha256.txt")
        yield _file_evidence(TAG_MANIFEST_CONTROL, archive, archive.root / "tagmanifest-sha256.txt")
        yield ProgressEvent(
            work_id=archive.archive_id,
            phase="verifying archive integrity",
            completed=1,
            total=1,
            unit="checkpoint",
        )
