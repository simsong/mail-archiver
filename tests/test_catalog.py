"""Verify packaged V1 schemas, indexes, and fail-closed schema handling."""

import sqlite3
from importlib import resources
from pathlib import Path

import pytest

from mailarchiver.catalog import (
    ARCHIVE_SCHEMA,
    SCHEMA_VERSION,
    SEARCH_SCHEMA,
    SEARCH_SCHEMA_VERSION,
    create_catalog,
    create_search,
    owner_tokens,
)


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


def test_packaged_v1_schema_creates_current_catalog(tmp_path: Path) -> None:
    schema = resources.files("mailarchiver").joinpath("sql", ARCHIVE_SCHEMA)
    assert schema.is_file()
    schema_text = schema.read_text(encoding="utf-8")
    assert "CREATE TABLE source_volumes" in schema_text
    assert "CREATE INDEX messages_date_message" in schema_text

    catalog = create_catalog(tmp_path / "archive.sqlite3")
    try:
        assert catalog.execute("SELECT version FROM schema_info").fetchone() == (SCHEMA_VERSION,)
        tables = {row[0] for row in catalog.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"source_volumes", "source_files", "observations", "metadata_defects"} <= tables
        indexes = {row[0] for row in catalog.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert {
            "messages_sha256",
            "messages_date_message",
            "observations_source_file_offset",
            "observations_run_observation",
            "locations_generation_offset",
        } <= indexes
        assert {
            "messages_sender_address_pk",
            "recipients_address_pk",
            "source_files_volume_path",
            "observations_message_pk",
            "observations_raw_sha256",
            "observations_semantic_sha256",
            "locations_generation_pk",
        } <= indexes
    finally:
        catalog.close()


def test_rejects_schema_version_other_than_v1(tmp_path: Path) -> None:
    path = tmp_path / "archive.sqlite3"
    incompatible = sqlite3.connect(path)
    incompatible.executescript(
        "CREATE TABLE schema_info (version INTEGER NOT NULL); INSERT INTO schema_info VALUES (2);"
    )
    incompatible.close()

    with pytest.raises(RuntimeError, match="expected 1"):
        create_catalog(path)


def test_packaged_search_schema_creates_current_disposable_index(tmp_path: Path) -> None:
    schema = resources.files("mailarchiver").joinpath("sql", SEARCH_SCHEMA)
    assert schema.is_file()
    schema_text = schema.read_text(encoding="utf-8")
    assert "CREATE VIRTUAL TABLE IF NOT EXISTS message_fts" in schema_text
    assert "message_fts_rowid INTEGER NOT NULL UNIQUE" in schema_text

    search = create_search(tmp_path / "search.sqlite3")
    try:
        assert search.execute("SELECT version FROM schema_info").fetchone() == (SEARCH_SCHEMA_VERSION,)
        tables = {row[0] for row in search.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"message_fts", "attachment_fts", "message_metadata", "message_attachments"} <= tables
        columns = {row[1] for row in search.execute("PRAGMA table_info(message_metadata)")}
        assert {"message_fts_rowid", "attachment_fts_rowid"} <= columns
    finally:
        search.close()


def test_rejects_obsolete_search_schema(tmp_path: Path) -> None:
    path = tmp_path / "search.sqlite3"
    obsolete = sqlite3.connect(path)
    obsolete.executescript(
        "CREATE TABLE message_metadata (sha256 TEXT PRIMARY KEY, fts_rowid INTEGER NOT NULL);"
    )
    obsolete.close()

    with pytest.raises(RuntimeError, match="unsupported unversioned search"):
        create_search(path)


def test_owner_tokens_ignore_whitespace_and_indented_comments(tmp_path: Path) -> None:
    """Requirement: owner aliases are normalized without treating comments as identities."""
    path = tmp_path / "owners.txt"
    path.write_text("  # explanatory comment\n SimsonG \n\nSLG@example.com\n", encoding="utf-8")

    assert owner_tokens(path) == ["simsong", "slg@example.com"]
