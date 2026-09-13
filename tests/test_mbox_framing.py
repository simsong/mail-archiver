# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Requirements: normalize only immediate double framing; retain source evidence and MIME/body bytes."""

import hashlib
from email import policy
from email.parser import BytesParser
from pathlib import Path

import pytest
from pydantic import ValidationError

from mailarchiver.bagit import _mailbag_metadata
from mailarchiver.message import parse_message, raw_header_values
from mailarchiver.mbox_framing import normalize_mbox_framing
from mailarchiver.plugin_api import MailObject, SourceReference
from mailarchiver.search import prepare_search_message
from mailarchiver.sources import _unwrap_xxx_record

OUTER = b"From XXX Thu Apr 15 04:21:10 2004\n"
INNER = b">From sender@example.test Thu Apr 15 00:20:49 2004\n"


@pytest.mark.parametrize("outer", [
    OUTER.replace(b"\n", b"\rjunk\n"), OUTER.replace(b"From ", b"From\t"),
    OUTER + b"Injected: field\n", OUTER.rstrip(b"\n"), OUTER.replace(b"\n", b"\r\r\n"),
])
def test_invalid_outer_envelope_cannot_be_promoted_into_a_header(outer: bytes) -> None:
    """Requirement: inner promotion cannot bypass framing validation or inject an RFC header."""
    raw = INNER + b"Subject: intact\n\nbody\n"
    record = normalize_mbox_framing(raw, outer)
    assert record.raw == raw
    assert record.envelope == outer
    assert record.normalization is None
    legacy_raw = b"Status: O\n\n" + raw
    assert _unwrap_xxx_record(legacy_raw, outer) == (legacy_raw, outer)
    source = SourceReference(plugin_kind="fixture", source_id="test", native_id="mailbox", display_name="mailbox")
    with pytest.raises(ValidationError, match="one complete From line"):
        MailObject(work_id="test", raw=record.raw, source=source, cursor="0", mbox_envelope=record.envelope)


@pytest.mark.parametrize("prefix", [b"\t", b" ", b">", b"\n", b"Status: O\n", b"Subject: example\n\n"])
def test_quoted_delimiter_elsewhere_is_not_normalized(prefix: bytes) -> None:
    """Only the first payload line qualifies, regardless of dates or following headers."""
    raw = prefix + INNER + b"From: quoted@example.test\n\nbody\n"
    normalized = normalize_mbox_framing(raw, OUTER)
    assert normalized.raw == raw
    assert normalized.envelope == OUTER
    assert normalized.normalization is None


@pytest.mark.parametrize("line", [b">From prose about email\n", b"> From sender Thu Apr 15 00:20:49 2004\n",
                                  b">From sender Thu Apr 15 00:20:49 2004", b">From sender Thu Apr 15 00:20:49 2004\rbroken\n"])
def test_invalid_quoted_delimiter_is_not_normalized(line: bytes) -> None:
    """From-like prose, spaced quoting, incomplete lines and embedded CR are not delimiters."""
    assert normalize_mbox_framing(line, OUTER).raw == line


def test_two_bogus_senders_do_not_replace_one_another() -> None:
    """Promotion requires a meaningful inner sender, not another placeholder."""
    raw = INNER.replace(b"sender@example.test", b"???@???") + b"Subject: example\n\nbody\n"
    normalized = normalize_mbox_framing(raw, OUTER)
    assert normalized.envelope == OUTER
    assert normalized.raw.startswith(b"X-From: ???@??? ")


def test_double_framing_preserves_mime_and_duplicate_x_from_fields() -> None:
    """Literal normalized headers work with unmodified RFC/MIME consumers and preserve field order."""
    raw = (INNER + b"X-From: prior metadata\nFrom: author@example.test\nTo: reader@example.test\n"
           b"Message-ID: <mime@example.test>\nDate: Thu, 15 Apr 2004 00:20:45 +0000\n"
           b"Subject: Multipart\nMIME-Version: 1.0\nContent-Type: multipart/mixed; boundary=parts\n\n"
           b"--parts\nContent-Type: text/plain\n\n>From literal body\n>>From deeper quote\n"
           b"--parts\nContent-Type: text/plain\nContent-Disposition: attachment; filename=note.txt\n"
           b"Content-Transfer-Encoding: base64\n\nSGVsbG8=\n--parts--\n")
    normalized = normalize_mbox_framing(raw, OUTER)
    parsed = BytesParser(policy=policy.compat32).parsebytes(normalized.raw)
    assert parsed.get_all("X-From") == ["XXX Thu Apr 15 04:21:10 2004", "prior metadata"]
    assert raw_header_values(normalized.raw, "X-From") == [b"XXX Thu Apr 15 04:21:10 2004", b"prior metadata"]
    assert parsed.is_multipart()
    parts = list(parsed.walk())
    assert parts[1].get_payload(decode=True) == b">From literal body\n>>From deeper quote"
    assert parts[2].get_payload(decode=True) == b"Hello"
    metadata = parse_message(normalized.raw, Path("source.mbox"), None)
    assert metadata.message_id == "mime@example.test"
    assert metadata.sender == "author@example.test"
    assert metadata.sha256 == hashlib.sha256(normalized.raw).hexdigest()
    assert normalized.normalization is not None
    assert normalized.normalization.source_raw_sha256 == hashlib.sha256(raw).hexdigest()
    search = prepare_search_message(normalized.raw, True)
    assert search.attachments[0].filename == "note.txt"
    assert search.attachment_content == "Hello"
    assert ">From literal body" in search.content
    assert "MIME-Version" not in search.content
    bag = _mailbag_metadata(normalized.raw, "fallback")
    assert bag.message_id == "<mime@example.test>"
    assert bag.attachments == 1
    assert bag.error == ""
