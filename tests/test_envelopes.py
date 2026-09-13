# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Requirements: preserve source envelopes; synthesize delivery dates without changing h2."""

import hashlib
import mailbox
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mailarchiver.__main__ import IngestRequest, run_ingest
from mailarchiver.gui_service import describe_message, parsed_message
from mailarchiver.mailsearch import read_message_bytes, render_message
from mailarchiver.mbox import add_message, read_verified_location, synthetic_envelope
from mailarchiver.mbox_framing import MboxNormalization
from mailarchiver.plugin_api import MailContainer, MailObject, SourceSpec
from mailarchiver.plugin_loader import load_plugins
from mailarchiver.sources import _unwrap_xxx_record, source_files, source_messages
from mailarchiver.standalone_verify import verify_archive


@pytest.mark.parametrize("outer_sender", [b"XXX", b"foo@bar", b"nobody", b"???@???"])
@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_double_framing_writes_x_from_and_preserves_body(tmp_path: Path, outer_sender: bytes, newline: bytes) -> None:
    """Requirement: normalize framing, prefer real envelopes over known placeholders, preserve body/provenance."""
    envelope = b"From " + outer_sender + b" Thu Apr 15 04:21:10 2004" + newline
    raw = (b">From forwarder@example.test Thu Apr 15 00:20:49 2004\n"
           b"From: forwarder@example.test\nDate: Thu, 15 Apr 2004 00:20:45 -0400\n"
           b"Message-ID: <forwarded@example.test>\nSubject: Forwarded message\n\n"
           b"\tFrom quoted@example.test  Thu Apr 15 00:16:08 2004\n"
           b"\tFrom: quoted@example.test\n\tSubject: Quoted message\n\n\tbody\n"
           b">From literal body\n>>From another literal body\n").replace(b"\n", newline)
    source = tmp_path / "source.mbox"
    original = envelope + raw + b"\n"
    source.write_bytes(original)
    promote = outer_sender in {b"XXX", b"???@???"}
    expected_envelope = b"From forwarder@example.test Thu Apr 15 00:20:49 2004" + newline if promote else envelope
    displaced = outer_sender + b" Thu Apr 15 04:21:10 2004" if promote else b"forwarder@example.test Thu Apr 15 00:20:49 2004"
    normalized_raw = b"X-From: " + displaced + newline + raw.partition(b"\n")[2]
    plugin = load_plugins().source("file-folder").implementation
    container = next(item for item in plugin.discover(SourceSpec(locator=str(source))) if isinstance(item, MailContainer))
    message = next(item for item in plugin.messages(container, None) if isinstance(item, MailObject))
    assert message.raw == normalized_raw
    assert message.mbox_envelope == expected_envelope
    assert message.cursor == "0"
    owners = tmp_path / "owners.txt"
    owners.write_text("owner@example.test\n", encoding="utf-8")
    archive = tmp_path / "archive"
    request = IngestRequest(archive=archive, roots=[str(source)], owner_names_file=owners, scan_policy="not-scanned")
    run_ingest(request)
    assert not verify_archive(archive)
    destination = archive / "data/mbox/2004-Archive1.mbox"
    published = destination.read_bytes()
    assert published.startswith(expected_envelope + b"X-From: " + displaced + newline)
    with sqlite3.connect(archive / "archive.sqlite3") as catalog:
        assert catalog.execute("SELECT message_id_normalized, sha256, subject FROM messages").fetchall() == [
            ("forwarded@example.test", hashlib.sha256(normalized_raw).hexdigest(), "Forwarded message"),
        ]
        message_pk = catalog.execute("SELECT message_pk FROM messages").fetchone()[0]
        detail = catalog.execute("SELECT detail FROM observations WHERE disposition='archived'").fetchone()[0]
    evidence = MboxNormalization.model_validate_json(detail.split("\nMBOX normalization: ", 1)[1])
    assert evidence.original_envelope == envelope
    assert evidence.source_raw_sha256 == hashlib.sha256(raw).hexdigest()
    assert evidence.quoted_envelope + normalized_raw.partition(b"\n")[2] == raw
    assert read_message_bytes(archive, message_pk) == normalized_raw
    _, parsed = parsed_message(archive, message_pk)
    assert parsed["X-From"] == displaced.decode("ascii")
    view = describe_message(archive, message_pk)
    assert any(header.name == "X-From" for header in view.headers)
    rendered = render_message(normalized_raw, full_headers=True, html=False)
    assert "X-From: " + displaced.decode("ascii") in rendered
    assert "\tFrom quoted@example.test" in rendered
    assert ">From literal body" in rendered
    assert ">>From another literal body" in rendered
    run_ingest(request)
    assert destination.read_bytes() == published
    assert source.read_bytes() == original


@pytest.mark.parametrize("outer_sender", [b"XXX", b"mbcp@s.eecs.harvard.edu"])
@pytest.mark.parametrize("extra", [b"", b"X-From: original header\n", b"\nretained body\n"])
def test_double_framed_mbcp_stub_excludes_only_metadata(tmp_path: Path, outer_sender: bytes, extra: bytes) -> None:
    """Requirement: generated framing cannot hide metadata, or erase original headers/body as metadata."""
    source = tmp_path / "2004" / "source.mbox"
    source.parent.mkdir()
    original = (b"From " + outer_sender + b" Thu Apr 15 04:21:10 2004\n"
                b">From mbcp@s.eecs.harvard.edu Thu Apr 15 00:20:49 2004\n"
                b"X-UID: 123\nStatus: O\nX-MBCP-Flags: $NotJunk\n" + extra)
    source.write_bytes(original)
    record, = source_messages(next(source_files(source)))
    expected = "Eudora MBCP metadata stub" if not extra else None
    assert record.exclusion_reason == expected
    assert record.raw.startswith(b"X-From: ")
    assert record.raw.partition(b"\n")[2] == original.split(b"\n", 2)[2]
    archive = tmp_path / "archive"
    run_ingest(IngestRequest(
        archive=archive, roots=[str(source)], scan_policy="not-scanned",
        owner_names_file=Path(__file__).parent / "fixtures" / "owner-names.txt",
    ), terminal=False)
    with sqlite3.connect(archive / "archive.sqlite3") as catalog:
        assert catalog.execute("SELECT count(*) FROM messages").fetchone() == (int(bool(extra)),)
        disposition, detail = catalog.execute("SELECT disposition, detail FROM observations").fetchone()
        assert disposition == ("source-metadata-excluded" if not extra else "archived")
        assert "MBOX normalization:" in detail
    assert source.read_bytes() == original


@pytest.mark.parametrize("prefix", [b"\tFrom ", b" From ", b"> From ", b"From "])
def test_xxx_status_record_does_not_unwrap_unquoted_or_indented_body(prefix: bytes) -> None:
    """Requirement: only an explicitly quoted envelope establishes a nested message."""
    # Direct parser input avoids an unquoted body From line being a physical MBOX delimiter.
    envelope = b"From XXX Thu Apr 15 04:21:10 2004\n"
    raw = b"Status: O\n\n" + prefix + b"quoted@example.test Thu Apr 15 00:16:08 2004\n\tbody\n"
    assert _unwrap_xxx_record(raw, envelope) == (raw, envelope)


@pytest.mark.parametrize("raw", [
    b"Status: O\n\n>From prose about email\nKeep this body.\n",
    b"Status: O\n\n>From sender Thu Apr 15 00:20:49 2004\rbroken\nKeep this body.\n",
    b"\n\n>From sender Thu Apr 15 00:20:49 2004\nKeep this body.\n",
])
def test_xxx_wrapper_requires_status_headers_and_complete_delimiter(raw: bytes) -> None:
    """Requirement: ambiguous body text must not lose bytes through legacy unwrapping."""
    envelope = b"From XXX Thu Apr 15 04:21:10 2004\n"
    assert _unwrap_xxx_record(raw, envelope) == (raw, envelope)


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_mbox_envelope_survives_plugin_ingest_and_verification(tmp_path: Path, newline: bytes) -> None:
    """Keep envelope sender/date/line ending, RFC hash, duplicate identity and source bytes."""
    envelope = b"From original@example.test Sat Jan  1 03:04:05 2000" + newline
    raw = (b"From: author@example.test\nDate: Sun, 2 Jan 2000 00:00:00 +0000\n"
           b"Message-ID: <preserve@example.test>\nSubject: Envelope\n\nbody\n>From literal\n")
    source = tmp_path / "source.mbox"
    source.write_bytes(envelope + raw + b"\n")
    original = source.read_bytes()
    plugin = load_plugins().source("file-folder").implementation
    container = next(item for item in plugin.discover(SourceSpec(locator=str(source))) if isinstance(item, MailContainer))
    message = next(item for item in plugin.messages(container, None) if isinstance(item, MailObject))
    assert message.mbox_envelope == envelope
    assert message.raw == raw
    owners = tmp_path / "owners.txt"
    owners.write_text("owner@example.test\n", encoding="utf-8")
    archive = tmp_path / "archive"
    request = IngestRequest(archive=archive, roots=[str(source)], owner_names_file=owners, scan_policy="not-scanned")
    run_ingest(request)
    destination = archive / "data/mbox/2000-Archive1.mbox"
    assert destination.read_bytes().startswith(envelope)
    assert not verify_archive(archive)
    with sqlite3.connect(archive / "archive.sqlite3") as catalog:
        assert catalog.execute("SELECT sha256 FROM messages").fetchall() == [(hashlib.sha256(raw).hexdigest(),)]
    published = destination.read_bytes()
    run_ingest(request)
    assert destination.read_bytes() == published
    duplicate = tmp_path / "duplicate.mbox"
    duplicate.write_bytes(b"From other@example.test Sun Jan  2 04:05:06 2000\n" + raw + b"\n")
    request.roots = [str(duplicate)]
    run_ingest(request)
    assert destination.read_bytes() == published
    with sqlite3.connect(archive / "archive.sqlite3") as catalog:
        assert catalog.execute("SELECT COUNT(*) FROM messages").fetchone() == (1,)
        assert catalog.execute("SELECT COUNT(*) FROM observations WHERE disposition='duplicate'").fetchone() == (1,)
    assert source.read_bytes() == original


def test_synthetic_envelope_uses_latest_header_instant_and_keeps_bytes(tmp_path: Path) -> None:
    """Latest Received wins over Date/median, offsets normalize to UTC, body dates are excluded."""
    raw = (b"From: author@example.test\nDate: Sat, 1 Jan 2000 10:00:00 +0000\n"
           b"Received: by final;\n Tue, 4 Jan 2000 02:00:00 -0500\n"
           b"Received: by second; Mon, 3 Jan 2000 06:00:00 +0000\n"
           b"Received: by first; Sun, 2 Jan 2000 06:00:00 +0000\n"
           b"Received: invalid\nMessage-ID: <delivery@example.test>\n\n"
           b"Date: Wed, 5 Jan 2000 00:00:00 +0000\nFrom body\n>From literal\nno final newline")
    envelope = b"From author@example.test Tue Jan  4 07:00:00 2000\n"
    assert synthetic_envelope(raw, sender="author@example.test") == envelope
    path = tmp_path / "output.mbox"
    box = mailbox.mbox(path, create=True)
    try:
        location = add_message(box, path, raw, sender="author@example.test")
    finally:
        box.close()
    assert path.read_bytes().startswith(envelope)
    assert read_verified_location(path, location, hashlib.sha256(raw).hexdigest()) == raw


def test_synthetic_envelope_fallbacks_are_deterministic() -> None:
    """Missing/invalid headers use explicit provenance or epoch, never the wall clock."""
    raw = b"Date: broken\n\nbody"
    assert synthetic_envelope(raw) == b"From MAILER-DAEMON Thu Jan  1 00:00:00 1970\n"
    assert synthetic_envelope(raw, datetime(1990, 2, 3, tzinfo=UTC)) == b"From MAILER-DAEMON Sat Feb  3 00:00:00 1990\n"
    assert synthetic_envelope(b"Date: Sat, 1 Jan 2000 00:00:00 +0000\n\n") == b"From MAILER-DAEMON Sat Jan  1 00:00:00 2000\n"


def test_source_envelope_in_raw_is_not_replaced(tmp_path: Path) -> None:
    """Babyl/EML leading envelopes remain part of the original hash interpretation."""
    raw = b"From legacy Sat Jan  1 00:00:00 2000\nFrom: author@example.test\n\nbody"
    path = tmp_path / "output.mbox"
    box = mailbox.mbox(path, create=True)
    try:
        location = add_message(box, path, raw)
    finally:
        box.close()
    assert path.read_bytes().startswith(raw.partition(b"\n")[0] + b"\n")
    assert read_verified_location(path, location, hashlib.sha256(raw).hexdigest()) == raw


def test_eml_ingest_uses_delivery_envelope_independently_of_routing(tmp_path: Path) -> None:
    """An unframed message gets its latest header timestamp through real publication."""
    raw = (b"From: author@example.test\nDate: Sat, 1 Jan 2000 10:00:00 +0000\n"
           b"Received: by final; Tue, 2 Jan 2001 02:00:00 -0500\n"
           b"Received: by middle; Sun, 2 Jan 2000 06:00:00 +0000\n"
           b"Received: by first; Sat, 1 Jan 2000 06:00:00 +0000\n"
           b"Message-ID: <synthetic@example.test>\nSubject: Delivered\n\nbody")
    source = tmp_path / "source.eml"
    source.write_bytes(raw)
    owners = tmp_path / "owners.txt"
    owners.write_text("owner@example.test\n", encoding="utf-8")
    archive = tmp_path / "archive"
    run_ingest(IngestRequest(archive=archive, roots=[str(source)], owner_names_file=owners, scan_policy="not-scanned"))
    destination = archive / "data/mbox/2000-Archive1.mbox"
    assert destination.read_bytes().startswith(b"From author@example.test Tue Jan  2 07:00:00 2001\n")
    assert not verify_archive(archive)
    with sqlite3.connect(archive / "archive.sqlite3") as catalog:
        assert catalog.execute("SELECT sha256 FROM messages").fetchall() == [(hashlib.sha256(raw).hexdigest(),)]
    assert source.read_bytes() == raw


def test_synthetic_envelope_honors_configured_date_plausibility() -> None:
    """The delimiter must honor the same earliest-year setting as source date parsing."""
    raw = b"Date: Wed, 1 Jan 1890 00:00:00 +0000\nReceived: by final; Thu, 1 Jan 1891 00:00:00 +0000\n\n"
    assert synthetic_envelope(raw, earliest_year=1800) == b"From MAILER-DAEMON Thu Jan  1 00:00:00 1891\n"
    assert synthetic_envelope(raw) == b"From MAILER-DAEMON Thu Jan  1 00:00:00 1970\n"
