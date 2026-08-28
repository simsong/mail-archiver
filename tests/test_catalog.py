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


def test_each_database_has_one_packaged_v1_schema() -> None:
    """Requirement: no migration fragment or alternate schema competes with current V1."""
    sql = resources.files("mailarchiver").joinpath("sql")

    assert sorted(path.name for path in sql.iterdir() if path.name.endswith(".sql")) == [
        ARCHIVE_SCHEMA,
        SEARCH_SCHEMA,
    ]


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
        assert {
            "source_volumes",
            "source_files",
            "source_integrity_checks",
            "source_integrity_evidence",
            "observations",
            "metadata_defects",
        } <= tables
        indexes = {row[0] for row in catalog.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert {
            "messages_sha256",
            "messages_date_message",
            "messages_subject_message",
            "messages_category_date_message",
            "messages_category_sender_address",
            "observations_source_file_offset",
            "observations_run_observation",
            "locations_generation_offset",
            "email_addresses_lower_address",
        } <= indexes
        assert {
            "messages_sender_address_pk",
            "recipients_address_pk",
            "recipients_message_role_address",
            "source_files_volume_path",
            "source_files_volume_hierarchy",
            "source_files_hierarchy_volume",
            "observations_message_pk",
            "observations_raw_sha256",
            "observations_semantic_sha256",
            "observations_source_file_cursor",
            "source_integrity_latest",
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


def test_rejects_historical_archive_layout_that_claims_v1(tmp_path: Path) -> None:
    """Regression: the pre-consolidation V1 label cannot admit its old path-only schema."""
    path = tmp_path / "archive.sqlite3"
    historical = sqlite3.connect(path)
    historical.executescript(
        """
        CREATE TABLE schema_info (version INTEGER NOT NULL);
        INSERT INTO schema_info VALUES (1);
        CREATE TABLE source_files (source_path TEXT PRIMARY KEY, sha256 TEXT NOT NULL);
        """
    )
    historical.close()

    with pytest.raises(RuntimeError, match="unsupported archive database V1 layout"):
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
        assert {
            "message_fts",
            "attachment_fts",
            "message_metadata",
            "message_attachments",
            "address_suggestions",
            "message_address_suggestions",
            "address_suggestion_fts",
        } <= tables
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


def test_rejects_historical_search_layout_that_claims_v1(tmp_path: Path) -> None:
    """Regression: a version label does not conceal obsolete FTS row mapping columns."""
    path = tmp_path / "search.sqlite3"
    historical = sqlite3.connect(path)
    historical.executescript(
        """
        CREATE TABLE schema_info (version INTEGER NOT NULL);
        INSERT INTO schema_info VALUES (1);
        CREATE TABLE message_metadata (
            sha256 TEXT PRIMARY KEY, attachment_count INTEGER NOT NULL, preview TEXT NOT NULL
        );
        """
    )
    historical.close()

    with pytest.raises(RuntimeError, match="unsupported search database V1 layout"):
        create_search(path)


def test_owner_tokens_ignore_whitespace_and_indented_comments(tmp_path: Path) -> None:
    """Requirement: owner aliases are normalized without treating comments as identities."""
    path = tmp_path / "owners.txt"
    path.write_text("  # explanatory comment\n SimsonG \n\nSLG@example.com\n", encoding="utf-8")

    assert owner_tokens(path) == ["simsong", "slg@example.com"]


def _plan(database: sqlite3.Connection, sql: str, parameters: tuple[str | int, ...] = ()) -> list[str]:
    return [str(row[3]) for row in database.execute("EXPLAIN QUERY PLAN " + sql, parameters)]


def test_ingest_and_provenance_queries_use_targeted_indexes(tmp_path: Path) -> None:
    """Requirement: point, resume, review, and provenance lookups never scan large tables."""
    catalog = create_catalog(tmp_path / "archive.sqlite3")
    try:
        expectations = (
            (
                "SELECT address_pk FROM email_addresses WHERE address = ?",
                ("sender@example.net",),
                "sqlite_autoindex_email_addresses_1",
            ),
            (
                "SELECT source_volume_pk FROM source_volumes WHERE identity_json = ?",
                ("{}",),
                "sqlite_autoindex_source_volumes_1",
            ),
            (
                "SELECT source_file_pk, byte_length FROM source_files "
                "WHERE source_volume_pk = ? AND source_path = ?",
                (1, "mail/inbox.mbox"),
                "sqlite_autoindex_source_files_1",
            ),
            (
                "SELECT integrity_check_pk FROM source_integrity_checks "
                "WHERE source_file_pk = ? AND control_id = ? AND completed_at IS NOT NULL "
                "ORDER BY integrity_check_pk DESC LIMIT 1",
                (1, "local-file-sha256-v1"),
                "source_integrity_latest",
            ),
            (
                "SELECT messages.date_utc FROM observations JOIN messages USING (message_pk) "
                "WHERE source_file_pk = ? AND source_offset < ? ORDER BY source_offset DESC LIMIT 1",
                (1, 100),
                "observations_source_file_offset",
            ),
            (
                "SELECT observation_pk FROM observations WHERE source_file_pk = ? AND source_cursor = ?",
                (1, "provider-cursor"),
                "observations_source_file_cursor",
            ),
            (
                "SELECT message_pk FROM messages WHERE message_id_normalized = ? AND sha256 = ?",
                ("message@example", "digest"),
                "sqlite_autoindex_messages_1",
            ),
            (
                "SELECT observations.disposition FROM observations JOIN source_files USING (source_file_pk) "
                "WHERE run_pk = ? ORDER BY observation_pk",
                (1,),
                "observations_run_observation",
            ),
            (
                "SELECT source_files.source_path FROM observations JOIN source_files USING (source_file_pk) "
                "WHERE observations.message_pk = ? ORDER BY observations.observation_pk",
                (1,),
                "observations_message_pk",
            ),
            (
                "SELECT messages.sha256, locations.byte_offset FROM messages "
                "JOIN locations USING (message_pk) JOIN mbox_generations USING (generation_pk) "
                "WHERE mbox_generations.filename = ? ORDER BY locations.byte_offset",
                ("2024-Archive1.mbox",),
                "locations_generation_offset",
            ),
            (
                "SELECT generation_pk FROM mbox_generations WHERE filename = ?",
                ("2024-Archive1.mbox",),
                "sqlite_autoindex_mbox_generations_1",
            ),
            (
                "SELECT messages.sha256, locations.byte_offset FROM messages "
                "LEFT JOIN locations USING (message_pk) WHERE messages.message_pk = ?",
                (1,),
                "SEARCH messages USING INTEGER PRIMARY KEY",
            ),
        )
        for sql, parameters, expected_index in expectations:
            steps = _plan(catalog, sql, parameters)
            assert any(expected_index in step for step in steps), (sql, steps)
    finally:
        catalog.close()


def test_report_and_rebuild_queries_use_v1_category_indexes(tmp_path: Path) -> None:
    """Requirement: category reports and archive rebuilds avoid message-table scans."""
    catalog = create_catalog(tmp_path / "archive.sqlite3")
    try:
        category_plan = _plan(
            catalog,
            "SELECT COUNT(*) FROM messages WHERE category IN (?, ?)",
            ("Archive", "Sent"),
        )
        date_plan = _plan(
            catalog,
            "SELECT message_pk FROM messages WHERE category IN (?, ?) "
            "AND date_utc >= ? AND date_utc < ?",
            ("Archive", "Sent", "2024-01-01", "2025-01-01"),
        )
        owner_plan = _plan(
            catalog,
            "SELECT DISTINCT sender_address_pk FROM messages WHERE category = ?",
            ("Sent",),
        )
        rebuild_plan = _plan(
            catalog,
            "SELECT messages.sha256, mbox_generations.filename, locations.byte_offset, locations.byte_length "
            "FROM mbox_generations "
            "CROSS JOIN locations ON locations.generation_pk = mbox_generations.generation_pk "
            "JOIN messages ON messages.message_pk = locations.message_pk "
            "WHERE messages.category IN (?, ?) "
            "ORDER BY mbox_generations.filename, locations.byte_offset",
            ("Archive", "Sent"),
        )
        rebuild_count_plan = _plan(
            catalog,
            "SELECT mbox_generations.filename, COUNT(*) FROM mbox_generations "
            "CROSS JOIN locations ON locations.generation_pk = mbox_generations.generation_pk "
            "JOIN messages ON messages.message_pk = locations.message_pk "
            "WHERE messages.category IN (?, ?) GROUP BY mbox_generations.filename",
            ("Archive", "Sent"),
        )
    finally:
        catalog.close()

    assert any("messages_category_" in step for step in category_plan)
    assert any("messages_category_date_message" in step for step in date_plan)
    assert any("messages_category_sender_address" in step for step in owner_plan)
    assert any("locations_generation_offset" in step for step in rebuild_plan)
    assert not any("SCAN messages" in step or "SCAN locations" in step for step in rebuild_plan)
    assert not any("USE TEMP B-TREE" in step for step in rebuild_plan)
    assert any("locations_generation_" in step for step in rebuild_count_plan)
    assert not any("SCAN messages" in step or "SCAN locations" in step for step in rebuild_count_plan)
    assert not any("USE TEMP B-TREE" in step for step in rebuild_count_plan)
