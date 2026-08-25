"""SQLite catalog and disposable search-index setup."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 2


def create_catalog(path: Path) -> sqlite3.Connection:
    database = sqlite3.connect(path)
    database.execute("PRAGMA foreign_keys = ON")
    tables = {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if tables and "schema_info" not in tables:
        database.close()
        raise RuntimeError("unsupported unversioned archive database; use a new empty archive directory")
    version = database.execute("SELECT version FROM schema_info").fetchone() if "schema_info" in tables else None
    if version == (1,):
        _migrate_v1(database)
    elif version is not None and version != (SCHEMA_VERSION,):
        database.close()
        raise RuntimeError(f"unsupported archive database schema {version}; expected {SCHEMA_VERSION}")
    _create_schema(database)
    return database


def _create_schema(database: sqlite3.Connection) -> None:
    database.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS schema_info (version INTEGER NOT NULL);
        INSERT INTO schema_info(version) SELECT {SCHEMA_VERSION} WHERE NOT EXISTS (SELECT 1 FROM schema_info);
        CREATE TABLE IF NOT EXISTS ingest_runs (
            run_pk INTEGER PRIMARY KEY, started_at TEXT NOT NULL,
            completed_at TEXT, result TEXT, detail TEXT
        );
        CREATE TABLE IF NOT EXISTS email_addresses (
            address_pk INTEGER PRIMARY KEY, address TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS messages (
            message_pk INTEGER PRIMARY KEY, message_id_normalized TEXT NOT NULL, sha256 TEXT NOT NULL,
            sender_address_pk INTEGER NOT NULL REFERENCES email_addresses(address_pk),
            subject TEXT NOT NULL, date_utc TEXT NOT NULL, date_source TEXT NOT NULL,
            category TEXT NOT NULL, UNIQUE(message_id_normalized, sha256)
        );
        CREATE TABLE IF NOT EXISTS source_volumes (
            source_volume_pk INTEGER PRIMARY KEY,
            identity_json TEXT NOT NULL UNIQUE,
            metadata_json TEXT NOT NULL,
            first_observed_at TEXT NOT NULL,
            last_observed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS source_files (
            source_file_pk INTEGER PRIMARY KEY,
            source_volume_pk INTEGER NOT NULL REFERENCES source_volumes(source_volume_pk),
            source_path TEXT NOT NULL,
            path_kind TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            modified_at_ns INTEGER,
            byte_length INTEGER,
            sha256 TEXT,
            checked_at TEXT,
            completed_run INTEGER REFERENCES ingest_runs(run_pk),
            UNIQUE(source_volume_pk, source_path)
        );
        CREATE TABLE IF NOT EXISTS observations (
            observation_pk INTEGER PRIMARY KEY, run_pk INTEGER NOT NULL REFERENCES ingest_runs(run_pk),
            message_pk INTEGER REFERENCES messages(message_pk), source_file_pk INTEGER NOT NULL REFERENCES source_files(source_file_pk),
            source_offset INTEGER NOT NULL DEFAULT 0, raw_sha256 TEXT NOT NULL DEFAULT '', semantic_sha256 TEXT,
            disposition TEXT NOT NULL, detail TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS metadata_defects (
            message_pk INTEGER NOT NULL REFERENCES messages(message_pk),
            field TEXT NOT NULL,
            detail TEXT NOT NULL,
            PRIMARY KEY (message_pk, field, detail)
        );
        CREATE TABLE IF NOT EXISTS recipients (
            message_pk INTEGER NOT NULL REFERENCES messages(message_pk),
            address_pk INTEGER NOT NULL REFERENCES email_addresses(address_pk),
            PRIMARY KEY (message_pk, address_pk)
        );
        CREATE TABLE IF NOT EXISTS mbox_generations (
            generation_pk INTEGER PRIMARY KEY,
            filename TEXT NOT NULL UNIQUE,
            sha256 TEXT NOT NULL,
            message_count INTEGER NOT NULL,
            byte_count INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS locations (
            message_pk INTEGER PRIMARY KEY REFERENCES messages(message_pk),
            generation_pk INTEGER NOT NULL REFERENCES mbox_generations(generation_pk),
            byte_offset INTEGER NOT NULL,
            byte_length INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS messages_sender_address_pk ON messages(sender_address_pk);
        CREATE INDEX IF NOT EXISTS recipients_address_pk ON recipients(address_pk);
        CREATE INDEX IF NOT EXISTS locations_generation_pk ON locations(generation_pk);
        CREATE INDEX IF NOT EXISTS source_files_volume_path ON source_files(source_volume_pk, source_path);
        CREATE INDEX IF NOT EXISTS observations_message_pk ON observations(message_pk);
        CREATE INDEX IF NOT EXISTS observations_raw_sha256 ON observations(raw_sha256);
        CREATE INDEX IF NOT EXISTS observations_semantic_sha256 ON observations(semantic_sha256);
        """
    )


def _migrate_v1(database: sqlite3.Connection) -> None:
    """Preserve legacy source paths while replacing path strings with normalized relations."""
    old_files = list(database.execute(
        "SELECT source_path, modified_at_ns, byte_length, sha256, checked_at, completed_run FROM source_files"
    ))
    old_observations = list(database.execute(
        "SELECT observation_pk, run_pk, message_pk, source_path, source_offset, source_sha256, disposition, detail FROM observations"
    ))
    database.commit()
    database.execute("PRAGMA foreign_keys = OFF")
    try:
        database.execute("BEGIN")
        database.execute("ALTER TABLE source_files RENAME TO source_files_v1")
        database.execute("ALTER TABLE observations RENAME TO observations_v1")
        _create_schema(database)
        source_file_pks: dict[str, int] = {}
        file_rows = {str(row[0]): row[1:] for row in old_files}
        paths = sorted({str(row[0]) for row in old_files} | {str(row[3]) for row in old_observations})
        for source_path in paths:
            volume_pk, relative = _legacy_volume(database, source_path)
            values = file_rows.get(source_path, (None, None, None, None, None))
            cursor = database.execute(
                "INSERT INTO source_files(source_volume_pk, source_path, path_kind, source_kind, modified_at_ns, byte_length, sha256, checked_at, completed_run) "
                "VALUES (?, ?, 'file', 'legacy', ?, ?, ?, ?, ?)",
                (volume_pk, relative, *values),
            )
            source_file_pks[source_path] = int(cursor.lastrowid)
        database.executemany(
            "INSERT INTO observations(observation_pk, run_pk, message_pk, source_file_pk, source_offset, raw_sha256, semantic_sha256, disposition, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)",
            (
                (row[0], row[1], row[2], source_file_pks[str(row[3])], row[4], row[5], row[6], row[7])
                for row in old_observations
            ),
        )
        database.execute("DROP TABLE observations_v1")
        database.execute("DROP TABLE source_files_v1")
        database.execute("UPDATE schema_info SET version = ?", (SCHEMA_VERSION,))
        database.commit()
    except BaseException:
        database.rollback()
        raise
    finally:
        database.execute("PRAGMA foreign_keys = ON")


def _legacy_volume(database: sqlite3.Connection, source_path: str) -> tuple[int, str]:
    path = Path(source_path)
    parts = path.parts
    mount = Path(*parts[:3]) if len(parts) >= 3 and parts[1] == "Volumes" else Path(path.anchor or "/")
    relative = path.relative_to(mount).as_posix() if path.is_absolute() else source_path
    identity = _json({"kind": "legacy-local-volume", "mount_path": str(mount)})
    metadata = _json({"format": "mailarchiver/source-volume/v1", "kind": "legacy-local-volume", "current_mount_path": str(mount)})
    now = datetime.now(timezone.utc).isoformat()
    database.execute(
        "INSERT INTO source_volumes(identity_json, metadata_json, first_observed_at, last_observed_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(identity_json) DO NOTHING",
        (identity, metadata, now, now),
    )
    row = database.execute("SELECT source_volume_pk FROM source_volumes WHERE identity_json = ?", (identity,)).fetchone()
    assert row is not None
    return int(row[0]), relative


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def create_search(path: Path) -> sqlite3.Connection:
    database = sqlite3.connect(path)
    database.execute("CREATE VIRTUAL TABLE IF NOT EXISTS message_fts USING fts5(sha256 UNINDEXED, content)")
    database.execute("CREATE VIRTUAL TABLE IF NOT EXISTS attachment_fts USING fts5(sha256 UNINDEXED, content)")
    database.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE IF NOT EXISTS message_metadata (
            sha256 TEXT PRIMARY KEY,
            attachment_count INTEGER NOT NULL CHECK (attachment_count >= 0),
            preview TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS message_attachments (
            sha256 TEXT NOT NULL REFERENCES message_metadata(sha256) ON DELETE CASCADE,
            attachment_ordinal INTEGER NOT NULL CHECK (attachment_ordinal > 0),
            part_id INTEGER NOT NULL CHECK (part_id >= 0),
            filename TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            PRIMARY KEY (sha256, attachment_ordinal)
        );
        CREATE INDEX IF NOT EXISTS message_attachments_mime_type ON message_attachments(mime_type);
        """
    )
    return database


def owner_tokens(path: Path) -> list[str]:
    return [line.strip().lower() for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")]


def address_pk(database: sqlite3.Connection, address: str) -> int:
    database.execute("INSERT INTO email_addresses(address) VALUES (?) ON CONFLICT(address) DO NOTHING", (address,))
    row = database.execute("SELECT address_pk FROM email_addresses WHERE address = ?", (address,)).fetchone()
    assert row is not None
    return int(row[0])
