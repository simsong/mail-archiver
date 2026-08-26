"""Load the packaged SQLite catalog and disposable search schemas."""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path

SCHEMA_VERSION = 1
SEARCH_SCHEMA_VERSION = 1
ARCHIVE_SCHEMA = "V1__archive.sql"
SEARCH_SCHEMA = "search.sql"


def _schema(name: str) -> str:
    return resources.files("mailarchiver").joinpath("sql", name).read_text(encoding="utf-8")


def _tables(database: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in database.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


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


def create_catalog(path: Path, *, check_same_thread: bool = True) -> sqlite3.Connection:
    database = sqlite3.connect(path, check_same_thread=check_same_thread)
    try:
        database.execute("PRAGMA foreign_keys = ON")
        tables = _tables(database)
        if tables:
            _require_version(database, tables, SCHEMA_VERSION, "archive")
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
