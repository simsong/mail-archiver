"""Exercise the framework-owned canonical archive integrity controls."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mailarchiver.archive_integrity import (
    ArchiveIntegrityError,
    MailbagArchiveIntegrityControls,
    PAYLOAD_MANIFEST_CONTROL,
    TAG_MANIFEST_CONTROL,
)
from mailarchiver.catalog import create_catalog
from mailarchiver.plugin_api import ArchiveReference, IntegrityEvidence, ProgressEvent


def test_mailbag_controls_publish_and_independently_verify_a_checkpoint(tmp_path: Path) -> None:
    """Requirement: the framework applies archive controls without consulting a mail source."""
    archive = ArchiveReference(format_id="mailbag-1.0", archive_id="empty-test", root=tmp_path)
    controls = MailbagArchiveIntegrityControls()
    initialized = list(controls.initialize(archive))
    catalog = create_catalog(tmp_path / "archive.sqlite3")
    try:
        published = list(controls.checkpoint(archive, catalog, datetime(2026, 8, 28, tzinfo=timezone.utc)))
    finally:
        catalog.close()

    verified = list(controls.verify(archive))
    evidence = [item for item in published if isinstance(item, IntegrityEvidence)]

    assert isinstance(initialized[0], ProgressEvent)
    assert (tmp_path / "verify_mail_archive.py").is_file()
    assert {item.control_id for item in evidence} == {PAYLOAD_MANIFEST_CONTROL, TAG_MANIFEST_CONTROL}
    for item in evidence:
        path = tmp_path / item.subject_id.split(":", 1)[1]
        assert item.value == hashlib.sha256(path.read_bytes()).hexdigest()
        assert item.byte_length == path.stat().st_size
    assert isinstance(verified[-1], ProgressEvent)
    assert verified[-1].completed == verified[-1].total == 1

    (tmp_path / "bagit.txt").write_bytes(b"damaged\n")
    with pytest.raises(ArchiveIntegrityError, match="unsupported BagIt declaration"):
        list(controls.verify(archive))
