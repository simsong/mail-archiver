# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Requirement: ePADD export preserves address boundaries without changing source data."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "dev" / "addressbook-exporter.py"


def fixture_archive(path: Path) -> Path:
    path.mkdir()
    catalog = path / "archive.sqlite3"
    with sqlite3.connect(catalog) as database:
        database.executescript(
            "CREATE TABLE email_addresses(address_pk INTEGER PRIMARY KEY,address TEXT);"
            "CREATE TABLE messages(message_pk INTEGER PRIMARY KEY,sender_address_pk INTEGER,category TEXT);"
            "CREATE TABLE recipients(message_pk INTEGER,address_pk INTEGER);"
        )
        values = (
            "owner@example.test", "OWNER@example.test", "notifications@example.test",
            "other@example.test", "host!legacy", "unused@example.test", "--bad@example.test",
            "bad\nline@example.test", "zoë@example.test", "&quot;undisclosed-recipient:;&quot;",
            '"undisclosed-recipient:;"',
        )
        database.executemany("INSERT INTO email_addresses VALUES (?,?)", enumerate(values, start=1))
        database.execute("INSERT INTO messages VALUES(1,1,'Sent')")
        database.executemany("INSERT INTO recipients VALUES(1,?)", ((key,) for key in (2, 3, 4, 5, 7, 8, 9, 10, 11)))
    return catalog


def run_export(archive: Path, output: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--archive", str(archive), "--output", str(output),
         "--owner", "owner@example.test", *extra], check=False, capture_output=True, text=True,
    )


def test_export_keeps_addresses_separate_and_source_unchanged(tmp_path: Path) -> None:
    """Owner first, no inferred aliases, legacy support, case dedupe, rejection accounting."""
    archive = tmp_path / "source"
    catalog = fixture_archive(archive)
    before = hashlib.sha256(catalog.read_bytes()).hexdigest()
    output = tmp_path / "export.txt"
    result = run_export(archive, output)
    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="utf-8") == (
        "-- Archive owner\nArchive owner\nowner@example.test\n"
        "--\nhost!legacy\n--\nnotifications@example.test\n--\nother@example.test\n--\nzoë@example.test\n"
    )
    report = json.loads(output.with_suffix(".txt.report.json").read_text(encoding="utf-8"))
    assert report["referenced_catalog_addresses"] == 10
    assert report["exported_addresses"] == report["contacts"] == 5
    assert report["normalized_duplicates"] == 1
    assert len(report["rejected"]) == 4
    assert report["output_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert hashlib.sha256(catalog.read_bytes()).hexdigest() == before
    assert list(archive.iterdir()) == [catalog]
    assert output.stat().st_mode & 0o777 == 0o600


def test_export_refuses_source_output_and_existing_files(tmp_path: Path) -> None:
    """Neither source paths nor existing exports may be replaced."""
    archive = tmp_path / "source"
    catalog = fixture_archive(archive)
    assert run_export(archive, archive / "export.txt").returncode != 0
    output = tmp_path / "export.txt"
    output.write_text("retain me", encoding="utf-8")
    assert run_export(archive, output).returncode != 0
    assert output.read_text(encoding="utf-8") == "retain me"
    alias = tmp_path / "alias"
    alias.symlink_to(archive, target_is_directory=True)
    assert run_export(archive, alias / "export.txt").returncode != 0
    assert list(archive.iterdir()) == [catalog]


def test_export_requires_reviewed_present_owner_and_stable_catalog(tmp_path: Path) -> None:
    """Fail before publication for an absent owner or potentially active SQLite writer."""
    archive = tmp_path / "source"
    catalog = fixture_archive(archive)
    output = tmp_path / "export.txt"
    assert run_export(archive, output, "--owner", "absent@example.test").returncode != 0
    assert not output.exists()
    Path(str(catalog) + "-wal").touch()
    assert run_export(archive, output).returncode != 0
    assert not output.exists()


def test_export_groups_only_explicit_owner_aliases(tmp_path: Path) -> None:
    """A reviewed owner alias moves into the first block, without appearing twice."""
    archive = tmp_path / "source"
    fixture_archive(archive)
    output = tmp_path / "export.txt"
    result = run_export(archive, output, "--owner", "other@example.test", "--owner-name", "Example Owner")
    assert result.returncode == 0, result.stderr
    text = output.read_text(encoding="utf-8")
    assert text.startswith("-- Archive owner\nExample Owner\nother@example.test\nowner@example.test\n--\n")
    assert text.count("other@example.test") == 1


def test_sent_owner_selection_uses_senders_not_recipients(tmp_path: Path) -> None:
    """Sent sender addresses define ownership; recipients and legacy addresses stay separate."""
    archive = tmp_path / "source"
    catalog = fixture_archive(archive)
    with sqlite3.connect(catalog) as database:
        database.execute("INSERT INTO messages VALUES(2,4,'Sent')")
        database.execute("INSERT INTO messages VALUES(3,5,'Sent')")
        database.execute("INSERT INTO messages VALUES(4,3,'Archive')")
    output = tmp_path / "export.txt"
    result = run_export(archive, output, "--owners-from-sent")
    assert result.returncode == 0, result.stderr
    text = output.read_text(encoding="utf-8")
    assert text.startswith("-- Archive owner\nArchive owner\nother@example.test\nowner@example.test\n--\n")
    assert "--\nnotifications@example.test\n" in text
    assert "--\nhost!legacy\n" in text
    report = json.loads(output.with_suffix(".txt.report.json").read_text(encoding="utf-8"))
    assert report["ungrouped_non_at_owner_addresses"] == ["host!legacy"]
