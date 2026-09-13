# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Owner routing requirements: exact mailbox globs, exclusions, durable defaults and evidence."""

import hashlib
import sqlite3
from pathlib import Path

import pytest

from mailarchiver.__main__ import IngestRequest, run_ingest
from mailarchiver.archive_config import config_path, load_archive_config
from mailarchiver.document_options import DocumentOptions
from mailarchiver.owner_rules import OwnerRules
from mailarchiver.standalone_verify import verify_archive


@pytest.mark.parametrize(("rule", "sender", "expected"), [
    ("*", "", False),
    ("slg", "SLG@example.org", True),
    ("slg", "3slg@example.org", False),
    ("slg", "person@slg.example", False),
    ("slg", "slg+tag@example.org", False),
    ("slg", "slg", True),
    ("slg@*", "slg", True),
    ("slg*", "slg+tag@example.org", True),
    ("*simson*", "david_simson@example.org", True),
    ("*simson*", "someone@simson.net", False),
    ("simson", "1simson@example.org", False),
    ("simsong", "150715simsong@legacy.example.net", False),
    ("simsong", "simsong@example.org", True),
    ("*@simson.net", "someone@simson.net", True),
    ("slg@example.org", "slg@other.org", False),
    ("slg@*.org", "slg@example.org", True),
    ("sl?", "slg@example.org", True),
    ("sl[gh]", "slh@example.org", True),
])
def test_explicit_mailbox_and_domain_matching(rule: str, sender: str, expected: bool) -> None:
    """Bare rules match the entire mailbox; only explicit globs broaden matching."""
    assert OwnerRules(include=[rule]).matches(sender) is expected


def test_exclusions_and_input_normalization() -> None:
    """Exclusions use the same address semantics and win over even an exact include."""
    rules = OwnerRules.from_text("SLG, *Simson*\nslg\nDAVID_SIMSON@EXAMPLE.ORG", "*David*, slg@blocked.org")
    assert rules.include == ["*simson*", "david_simson@example.org", "slg"]
    assert not rules.matches("david_simson@example.org")
    assert not rules.matches("slg@blocked.org")
    assert rules.matches("slg@other.org")
    assert rules.matches("simsong@david.org")
    assert not OwnerRules().matches("slg@example.org")


@pytest.mark.parametrize("value", ["Display Name", "name\x00", "name\x7f", "@domain", "name@", "a@b@c", "a;b", "<a@b>"])
def test_invalid_rules_are_rejected(value: str) -> None:
    """Do not turn malformed rules or display names into a broader owner match."""
    with pytest.raises(ValueError):
        OwnerRules(include=[value])


def test_ingest_owner_rules_persist_and_detect_without_changing_mail(tmp_path: Path) -> None:
    """Real ingest routes include/exclude correctly, reuses YAML, and emits only matching senders."""
    source = tmp_path / "source"
    source.mkdir()
    senders = ["slg@example.org", "3slg@example.org", "simsong@example.org", "david_simson@example.org", "other@simson.net"]
    digests = set()
    for index, sender in enumerate(senders):
        raw = (f"From: {sender}\nTo: recipient@example.org\nDate: Sat, 1 Jan 2000 00:00:00 +0000\n"
               f"Message-ID: <rule-{index}@example.org>\nSubject: Rule {index}\n\nBody\n").encode()
        (source / f"{index}.eml").write_bytes(raw)
        digests.add(hashlib.sha256(raw).hexdigest())
    archive = tmp_path / "archive"
    rules = OwnerRules(include=["slg", "*simson*"], exclude=["*david*"])
    run_ingest(IngestRequest(archive=archive, roots=[str(source)], owner_rules=rules, scan_policy="not-scanned"))
    assert load_archive_config(archive).owner == rules
    assert not DocumentOptions(archive).state().changed_since_import
    detected = archive / "owner-names-detected.txt"
    assert detected.read_text(encoding="utf-8") == "simsong@example.org\nslg@example.org\n"
    assert "simsong@example.org" not in config_path(archive).read_text(encoding="utf-8")
    with sqlite3.connect(archive / "archive.sqlite3") as catalog:
        rows = catalog.execute("SELECT e.address,m.category FROM messages m JOIN email_addresses e ON e.address_pk=m.sender_address_pk ORDER BY e.address").fetchall()
        assert rows == [("3slg@example.org", "Archive"), ("david_simson@example.org", "Archive"), ("other@simson.net", "Archive"), ("simsong@example.org", "Sent"), ("slg@example.org", "Sent")]
        assert {row[0] for row in catalog.execute("SELECT sha256 FROM messages")} == digests
    before = config_path(archive).stat().st_mtime_ns
    # No request rules or owner file: repeat import uses saved include AND exclude.
    run_ingest(IngestRequest(archive=archive, roots=[str(source)], scan_policy="not-scanned"))
    assert config_path(archive).stat().st_mtime_ns == before
    assert detected.read_text(encoding="utf-8") == "simsong@example.org\nslg@example.org\n"
    assert {hashlib.sha256(path.read_bytes()).hexdigest() for path in source.glob("*.eml")} == digests
    assert not verify_archive(archive)
    # A derived-list write failure must not prevent canonical checkpoint publication.
    detected.unlink()
    detected.mkdir()
    (source / "additional.eml").write_bytes(b"From: slg@example.org\nDate: Sun, 2 Jan 2000 00:00:00 +0000\nMessage-ID: <additional@example.org>\n\nAnother message\n")
    with pytest.raises(OSError):
        run_ingest(IngestRequest(archive=archive, roots=[str(source)], scan_policy="not-scanned"))
    with sqlite3.connect(archive / "archive.sqlite3") as catalog:
        assert catalog.execute("SELECT COUNT(*) FROM messages").fetchone() == (6,)
    assert not verify_archive(archive)
