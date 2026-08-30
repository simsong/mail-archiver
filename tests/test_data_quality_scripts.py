"""Verify the reusable data-quality audit's read-only and format logic."""

import sqlite3
from pathlib import Path

import pytest

from scripts.data_quality.analyze_archive import normalized_date, open_catalog, source_format


def test_audit_catalog_connection_cannot_modify_archive(tmp_path: Path) -> None:
    """Requirement: data-quality audits never modify the canonical archive catalog."""
    archive = tmp_path / "archive"
    archive.mkdir()
    database = archive / "archive.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE evidence(value TEXT NOT NULL)")
        connection.execute("INSERT INTO evidence VALUES ('preserved')")

    connection = open_catalog(archive)
    try:
        assert connection.execute("SELECT value FROM evidence").fetchone()[0] == "preserved"
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("DELETE FROM evidence")
    finally:
        connection.close()

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM evidence").fetchone()[0] == "preserved"


def test_audit_recognizes_extensionless_babyl_and_normalizes_dates(tmp_path: Path) -> None:
    """Requirement: audit evidence recognizes Babyl signatures and compares dates in UTC."""
    babyl = tmp_path / "aliza"
    babyl.write_bytes(b"BaByL OpTiOnS:\nVersion: 5\n")

    assert source_format(babyl) == "babyl"
    assert normalized_date("1 Jan 1983 01:00:00 +0200").isoformat() == "1982-12-31T23:00:00+00:00"
