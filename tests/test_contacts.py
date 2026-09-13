# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Requirements: Contact CLI is read-only and meaningful only for direct correspondence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from mailarchiver.catalog import address_pk, create_catalog
from mailarchiver.contacts import _connection


def _message(
    database: sqlite3.Connection,
    sender: int,
    date: str,
    category: str,
    recipients: tuple[tuple[int, str], ...] = (),
) -> None:
    cursor = database.execute(
        "INSERT INTO messages(message_id_normalized, sha256, sender_address_pk, subject, date_utc, date_source, category) "
        "VALUES (?, ?, ?, '', ?, 'date', ?)",
        (date, hashlib.sha256(date.encode()).hexdigest(), sender, date, category),
    )
    for address, role in recipients:
        database.execute("INSERT INTO recipients(message_pk, address_pk, role) VALUES (?, ?, ?)", (cursor.lastrowid, address, role))


def _archive(tmp_path: Path) -> Path:
    archive = tmp_path / "archive"
    archive.mkdir()
    database = create_catalog(archive / "archive.sqlite3")
    try:
        owner = address_pk(database, "owner@example.org")
        alice = address_pk(database, "alice@example.org")
        bob = address_pk(database, "bob@example.org")
        hidden = address_pk(database, "hidden@example.org")
        copy = address_pk(database, "copy@example.org")
        list_sender = address_pk(database, "list@example.org")
        _message(database, alice, "2024-01-01T00:00:00+00:00", "Archive", ((owner, "to"),))
        _message(database, owner, "2024-01-02T00:00:00+00:00", "Sent", ((bob, "to"), (copy, "cc"), (hidden, "bcc")))
        _message(database, alice, "2024-01-03T00:00:00+00:00", "Archive", ((copy, "cc"),))
        _message(database, list_sender, "2024-01-04T00:00:00+00:00", "Archive", ((owner, "cc"),))
        _message(database, list_sender, "2024-01-05T00:00:00+00:00", "Archive", ((owner, "to"),))
        database.commit()
    finally:
        database.close()
    return archive


def _contacts(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "mailarchiver", *arguments], text=True, capture_output=True, check=False)


def test_contacts_meaningful_filter_uses_direct_to_and_bcc_only(tmp_path: Path) -> None:
    """Requirement: direct correspondence excludes Cc and list mail without owner in To."""
    archive = _archive(tmp_path)
    owners = tmp_path / "owners.txt"
    owners.write_text("# archive owner\nowner, unused; another-unused\n", encoding="utf-8")
    result = _contacts(
        "--archive", str(archive), "human-contacts", "--owner-address-file", str(owners), "--format", "json"
    )

    assert result.returncode == 0, result.stderr
    rows = {row["address"]: row for row in json.loads(result.stdout)}
    assert set(rows) == {"alice@example.org", "bob@example.org", "hidden@example.org", "list@example.org"}
    assert rows["bob@example.org"] == {
        "address": "bob@example.org",
        "first_seen": "2024-01-02",
        "last_seen": "2024-01-02",
        "message_count": 1,
    }
    assert rows["alice@example.org"]["message_count"] == 2
    assert rows["list@example.org"]["message_count"] == 2
    assert "copy@example.org" not in rows


def test_contacts_all_lists_every_header_address_without_owner_configuration(tmp_path: Path) -> None:
    """Requirement: all-header Contact statistics are available without meaningful classification."""
    archive = _archive(tmp_path)
    result = _contacts("--archive", str(archive), "human-contacts", "--all", "--format", "tsv")

    assert result.returncode == 0, result.stderr
    rows = {line.split("\t")[0]: line.split("\t") for line in result.stdout.splitlines()[1:]}
    assert rows["copy@example.org"][1:] == ["2024-01-02", "2024-01-03", "2"]
    assert "owner@example.org" in rows


def test_contacts_reports_unmatched_owner_alias_without_a_traceback(tmp_path: Path) -> None:
    """Regression: an owner-name file incompatible with an archive is a normal CLI error."""
    archive = _archive(tmp_path)
    owners = tmp_path / "owners.txt"
    owners.write_text("not-an-owner", encoding="utf-8")

    result = _contacts("--archive", str(archive), "human-contacts", "--owner-address-file", str(owners))

    assert result.returncode == 2
    assert "owner aliases did not match a Sent sender address" in result.stderr
    assert "Traceback" not in result.stderr


def test_contacts_special_path_is_read_only(tmp_path: Path) -> None:
    """Requirement: URI-sensitive archive names open the correct catalog without write access."""
    archive = _archive(tmp_path).rename(tmp_path / "mail #100% café")
    catalog = archive / "archive.sqlite3"
    original = catalog.read_bytes()
    result = _contacts("--archive", str(archive), "human-contacts", "--all", "--format", "tsv")
    assert result.returncode == 0, result.stderr
    assert "alice@example.org" in result.stdout
    database = _connection(archive)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            database.execute("DELETE FROM messages")
    finally:
        database.close()
    assert catalog.read_bytes() == original


def test_contacts_missing_catalog_is_not_created(tmp_path: Path) -> None:
    """Requirement: read-only Contacts never creates an absent source database."""
    result = _contacts("--archive", str(tmp_path), "human-contacts", "--all")
    assert result.returncode != 0
    assert not (tmp_path / "archive.sqlite3").exists()


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize(
    "policy",
    (
        "version: 1\nmode: extend\nallow_address_patterns: ['.*', '[']\n",
        "version: 1\nmode: extend\nservice: {domain_patterns: ['[']}\n",
        "version: 1\nmode: replace\nmax_human_local_part_length: 48\n"
        "allow_address_patterns: []\nmailing_list: {}\nservice: {}\n"
        "bogus_local_part_patterns: []\nbogus_domain_patterns: ['[']\n",
        "version: 1\nmode: extend\nservice: [\n",
    ),
)
def test_contacts_rejects_invalid_policy_without_traceback(tmp_path: Path, policy: str, empty: bool) -> None:
    """Requirement: all policy rules validate before querying, with actionable non-mutating errors."""
    if empty:
        archive = tmp_path / "archive"
        archive.mkdir()
        create_catalog(archive / "archive.sqlite3").close()
    else:
        archive = _archive(tmp_path)
    policy_path = archive / "contact_filters.yaml"
    policy_path.write_text(policy, encoding="utf-8")
    catalog = archive / "archive.sqlite3"
    original = catalog.read_bytes()

    result = _contacts("--archive", str(archive), "human-contacts", "--all")

    assert result.returncode == 2
    assert str(policy_path) in result.stderr
    assert "invalid contact-filter policy" in result.stderr
    if "'['" in policy:
        assert "invalid regular expression '['" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""
    assert catalog.read_bytes() == original
