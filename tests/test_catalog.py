"""Requirements: fresh catalogs are versioned and incompatible schemas fail closed."""

import sqlite3
from pathlib import Path

import pytest

from mailarchiver.catalog import SCHEMA_VERSION, create_catalog


def test_rejects_unversioned_catalog(tmp_path: Path) -> None:
    path = tmp_path / "archive.sqlite3"
    incompatible = sqlite3.connect(path)
    incompatible.executescript(
        """
        CREATE TABLE messages (message_pk INTEGER PRIMARY KEY);
        """
    )
    incompatible.commit()
    incompatible.close()

    with pytest.raises(RuntimeError, match="unsupported unversioned"):
        create_catalog(path)


def test_creates_current_schema_directly(tmp_path: Path) -> None:
    catalog = create_catalog(tmp_path / "archive.sqlite3")
    try:
        assert catalog.execute("SELECT version FROM schema_info").fetchone() == (SCHEMA_VERSION,)
        tables = {row[0] for row in catalog.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"source_volumes", "source_files", "observations", "metadata_defects"} <= tables
    finally:
        catalog.close()


def test_migrates_v1_source_paths_without_losing_observations(tmp_path: Path) -> None:
    """Requirement: schema upgrades retain legacy source evidence instead of discarding it."""
    path = tmp_path / "archive.sqlite3"
    legacy = sqlite3.connect(path)
    legacy.executescript(
        """
        CREATE TABLE schema_info (version INTEGER NOT NULL);
        INSERT INTO schema_info VALUES (1);
        CREATE TABLE ingest_runs (run_pk INTEGER PRIMARY KEY, started_at TEXT NOT NULL);
        INSERT INTO ingest_runs VALUES (1, '2026-08-23T00:00:00+00:00');
        CREATE TABLE source_files (
            source_path TEXT PRIMARY KEY, modified_at_ns INTEGER NOT NULL, byte_length INTEGER NOT NULL,
            sha256 TEXT NOT NULL, checked_at TEXT NOT NULL, completed_run INTEGER NOT NULL
        );
        INSERT INTO source_files VALUES ('/Volumes/Backup/mail/inbox.mbox', 1, 2, 'file-hash', '2026-08-23T00:00:00+00:00', 1);
        CREATE TABLE observations (
            observation_pk INTEGER PRIMARY KEY, run_pk INTEGER NOT NULL, message_pk INTEGER, source_path TEXT NOT NULL,
            source_offset INTEGER NOT NULL, source_sha256 TEXT NOT NULL, disposition TEXT NOT NULL, detail TEXT NOT NULL
        );
        INSERT INTO observations VALUES (1, 1, NULL, '/Volumes/Backup/mail/inbox.mbox', 12, 'raw-hash', 'error', 'legacy');
        """
    )
    legacy.commit()
    legacy.close()

    catalog = create_catalog(path)
    try:
        assert catalog.execute("SELECT version FROM schema_info").fetchone() == (SCHEMA_VERSION,)
        assert catalog.execute(
            "SELECT source_path, raw_sha256, semantic_sha256 FROM observations JOIN source_files USING (source_file_pk)"
        ).fetchone() == ("mail/inbox.mbox", "raw-hash", None)
        assert catalog.execute("SELECT count(*) FROM source_volumes").fetchone() == (1,)
    finally:
        catalog.close()
