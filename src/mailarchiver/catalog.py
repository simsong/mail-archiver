"""Load the packaged SQLite catalog and disposable search schemas."""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path

SCHEMA_VERSION = 1
SEARCH_SCHEMA_VERSION = 1
ARCHIVE_SCHEMA = "V1__archive.sql"
SEARCH_SCHEMA = "V1__search.sql"


def _schema(name: str) -> str:
    return resources.files("mailarchiver").joinpath("sql", name).read_text(encoding="utf-8")


def _tables(database: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in database.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _indexes(database: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in database.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}


def _columns(database: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in database.execute(f"PRAGMA table_info({table})")}


def _require_version(
    database: sqlite3.Connection,
    tables: set[str],
    expected: int,
    database_name: str,
) -> None:
    if "schema_info" not in tables:
        raise RuntimeError(f"unsupported unversioned {database_name} database; use a new database")
    versions = database.execute("SELECT version FROM schema_info").fetchall()
    if versions != [(expected,)]:
        raise RuntimeError(f"unsupported {database_name} database schema {versions}; expected {expected}")


def _require_layout(
    database: sqlite3.Connection,
    database_name: str,
    tables: set[str],
    required_tables: tuple[str, ...],
    required_indexes: tuple[str, ...],
    required_columns: tuple[tuple[str, tuple[str, ...]], ...],
) -> None:
    valid = set(required_tables) <= tables and set(required_indexes) <= _indexes(database)
    valid = valid and all(set(columns) <= _columns(database, table) for table, columns in required_columns)
    if not valid:
        raise RuntimeError(f"unsupported {database_name} database V1 layout; use a new database")


def _require_archive_layout(database: sqlite3.Connection, tables: set[str]) -> None:
    _require_layout(
        database,
        "archive",
        tables,
        (
            "ingest_runs",
            "email_addresses",
            "messages",
            "source_volumes",
            "source_files",
            "source_integrity_checks",
            "source_integrity_evidence",
            "observations",
            "metadata_defects",
            "recipients",
            "mbox_generations",
            "locations",
        ),
        (
            "email_addresses_lower_address",
            "messages_sender_address_pk",
            "messages_sha256",
            "messages_date_message",
            "messages_subject_message",
            "messages_category_date_message",
            "messages_category_sender_address",
            "recipients_address_pk",
            "recipients_message_role_address",
            "locations_generation_pk",
            "locations_generation_offset",
            "source_files_volume_path",
            "source_files_volume_hierarchy",
            "source_files_hierarchy_volume",
            "source_integrity_latest",
            "observations_message_pk",
            "observations_raw_sha256",
            "observations_semantic_sha256",
            "observations_source_file_offset",
            "observations_source_file_cursor",
            "observations_run_observation",
        ),
        (
            (
                "source_files",
                (
                    "source_file_pk",
                    "source_volume_pk",
                    "source_plugin",
                    "work_id",
                    "source_path",
                    "hierarchy_path",
                    "metadata_json",
                    "sha256",
                ),
            ),
            (
                "source_integrity_checks",
                ("integrity_check_pk", "source_file_pk", "run_pk", "control_id", "completed_at"),
            ),
            (
                "source_integrity_evidence",
                (
                    "integrity_check_pk",
                    "ordinal",
                    "control_id",
                    "subject_id",
                    "evidence_kind",
                    "algorithm",
                    "value",
                    "byte_length",
                ),
            ),
            ("observations", ("source_file_pk", "source_cursor", "raw_sha256", "semantic_sha256")),
            ("recipients", ("message_pk", "address_pk", "role")),
            ("locations", ("generation_pk", "byte_offset", "byte_length")),
        ),
    )


def _require_search_layout(database: sqlite3.Connection, tables: set[str]) -> None:
    _require_layout(
        database,
        "search",
        tables,
        (
            "message_fts",
            "attachment_fts",
            "message_metadata",
            "message_attachments",
            "address_suggestions",
            "message_address_suggestions",
            "address_suggestion_fts",
        ),
        ("message_attachments_mime_type",),
        (
            ("message_fts", ("sha256", "content")),
            ("attachment_fts", ("sha256", "content")),
            (
                "message_metadata",
                ("sha256", "message_fts_rowid", "attachment_fts_rowid", "attachment_count", "preview"),
            ),
            ("message_attachments", ("sha256", "attachment_ordinal", "part_id", "filename", "mime_type")),
            ("address_suggestions", ("suggestion_pk", "address", "display_name", "message_count")),
            ("message_address_suggestions", ("sha256", "suggestion_pk")),
        ),
    )


def create_catalog(path: Path, *, check_same_thread: bool = True) -> sqlite3.Connection:
    database = sqlite3.connect(path, check_same_thread=check_same_thread)
    try:
        database.execute("PRAGMA foreign_keys = ON")
        tables = _tables(database)
        if tables:
            _require_version(database, tables, SCHEMA_VERSION, "archive")
            _require_archive_layout(database, tables)
        else:
            database.executescript(_schema(ARCHIVE_SCHEMA))
        return database
    except BaseException:
        database.close()
        raise


def create_search(path: Path, *, check_same_thread: bool = True) -> sqlite3.Connection:
    database = sqlite3.connect(path, check_same_thread=check_same_thread)
    try:
        tables = _tables(database)
        if tables:
            _require_version(database, tables, SEARCH_SCHEMA_VERSION, "search")
            _require_search_layout(database, tables)
        database.executescript(_schema(SEARCH_SCHEMA))
        return database
    except BaseException:
        database.close()
        raise


def owner_tokens(path: Path) -> list[str]:
    lines = (line.strip().lower() for line in path.read_text().splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def address_pk(database: sqlite3.Connection, address: str) -> int:
    database.execute("INSERT INTO email_addresses(address) VALUES (?) ON CONFLICT(address) DO NOTHING", (address,))
    row = database.execute("SELECT address_pk FROM email_addresses WHERE address = ?", (address,)).fetchone()
    assert row is not None
    return int(row[0])
