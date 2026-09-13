"""Requirements: reversible MBOXRD, legacy recovery, and exact h2 including provenance.

See doc/MBOX_READING.md and doc/INTEGRITY_CONTROLS.md.
"""

import hashlib
import mailbox
from contextlib import closing
from pathlib import Path

import pytest

from mailarchiver.mbox import add_message, read_verified_location
from mailarchiver.mboxrd import quote, unquote
from mailarchiver.pdf_mail import PdfMailExtraction, PrintedEmailRecord, write_pdf_mbox
from mailarchiver.sources import source_files, source_messages
from mailarchiver.standalone_verify import _stored_candidates, semantic_bytes
from mailarchiver.validation import write_mbox_messages
from scripts.data_quality.analyze_archive import write_mbox

ENVELOPE = b"From sender@example.test Mon Jan  1 00:00:00 2024\n"


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize("ending", [b"", b"\n", b"\r\n", b"\n\n"])
def test_publication_recovers_arbitrary_quote_depth(tmp_path: Path, newline: bytes, ending: bytes) -> None:
    """Header/body quotes and final bytes survive without bounded mboxo guessing."""
    raw = (b"Subject: exact\n>From malformed header\n\n" + b"".join(
        b">" * depth + b"From body\n" for depth in range(30)
    ) + b" From indented\n> From spaced\n\xff malformed MIME").replace(b"\n", newline) + ending
    path = tmp_path / "archive.mbox"
    with closing(mailbox.mbox(path)) as box:
        location = add_message(box, path, raw, envelope=ENVELOPE)
        expected = raw.replace(b">From malformed header", b">>From malformed header")
        for depth in range(29, -1, -1):
            expected = expected.replace(newline + b">" * depth + b"From body",
                                        newline + b">" * (depth + 1) + b"From body")
        stored = path.read_bytes()
        assert stored == ENVELOPE + expected + (b"" if raw.endswith(b"\n") else b"\n") + b"\n"
        assert read_verified_location(path, location, hashlib.sha256(raw).hexdigest()) == raw
        assert raw in _stored_candidates(box, next(box.iterkeys()))


def test_mixed_legacy_and_mboxrd_recovery(tmp_path: Path) -> None:
    """Appending mboxrd must not reinterpret old mboxo records or require rewriting them."""
    raw = b"Subject: mixed\n\nFrom body\n>From literal\n>>From twice\n"
    path = tmp_path / "mixed.mbox"
    with closing(mailbox.mbox(path)) as box:
        legacy_key = box.add(ENVELOPE + raw)
        box.flush()
        legacy_bytes = path.read_bytes()
        location = add_message(box, path, raw, envelope=ENVELOPE)
        assert path.read_bytes().startswith(legacy_bytes)
        assert raw in _stored_candidates(box, legacy_key)
        assert read_verified_location(path, location, hashlib.sha256(raw).hexdigest()) == raw


@pytest.mark.parametrize("suffix", [".mbox", ".mboxrd"])
def test_declared_source_dialect_and_validation_export(tmp_path: Path, suffix: str) -> None:
    """Decode declared mboxrd once; never guess the dialect of an unknown source."""
    payload = b">From malformed header\nSubject: source\n\nFrom bare\n>From literal\n>>From twice\n"
    source = tmp_path / ("source" + suffix)
    encoded = b">>From malformed header\nSubject: source\n\n>From bare\n>>From literal\n>>>From twice\n"
    original = ENVELOPE + encoded + b"\n"
    source.write_bytes(original)
    record, = source_messages(next(source_files(source)))
    expected = payload if suffix == ".mboxrd" else encoded
    assert record.raw == expected
    assert record.mbox_normalization is None
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    assert write_mbox_messages(source, prepared, 0) == 1
    assert next(prepared.iterdir()).read_bytes() == expected
    assert source.read_bytes() == original


def test_importer_headers_are_integrity_protected_but_not_h3_selected() -> None:
    """All provenance affects h2; h3 already covers selected headers AND the encoded body."""
    raw = b"Message-ID: <same@example>\nSubject: same\n\nattachment and body\n"
    annotated = (b"X-Imported-URI: file:///mail.pst#item=123\n"
                 b"X-Importer-Name: outlook-pst\nX-Importer-Version: 1\n" + raw)
    assert hashlib.sha256(annotated).digest() != hashlib.sha256(raw).digest()
    assert semantic_bytes(annotated) == semantic_bytes(raw)
    assert semantic_bytes(annotated.replace(b"body", b"lost")) != semantic_bytes(raw)
    assert semantic_bytes(annotated.replace(b"Subject: same", b"Subject: different")) != semantic_bytes(raw)


def test_quoting_is_not_idempotent_and_only_changes_from_lines() -> None:
    """The caller must encode/decode once, including the first payload line."""
    raw = b"From first\n>From second\n>>From third\n> From spaced\nfrom lowercase\rFrom after CR\n"
    stored = b">From first\n>>From second\n>>>From third\n> From spaced\nfrom lowercase\rFrom after CR\n"
    assert quote(raw) == stored
    assert unquote(stored) == raw
    assert quote(stored) != stored


def test_derived_mbox_writers_preserve_quote_depth(tmp_path: Path) -> None:
    """Requirement: PDF interpretations and audit exports must use reversible quoting too."""
    body = "From bare\n>From literal\n>>From twice\n"
    extraction = PdfMailExtraction(
        source_pdf=tmp_path / "synthetic.pdf", pdf_sha256="0" * 64, byte_length=0, pages=(),
        messages=(PrintedEmailRecord(page_start=1, page_end=1, headers=(), body=body,
                                     extracted_text=body, subject=""),),
    )
    pdf_output = tmp_path / "pdf.mboxrd"
    write_pdf_mbox(extraction, pdf_output)
    pdf_record, = source_messages(next(source_files(pdf_output)))
    assert pdf_record.raw.partition(b"\n\n")[2] == body.encode()
    raw = b"Subject: audit\n\n" + body.encode()
    audit_output = tmp_path / "audit.mboxrd"
    write_mbox(audit_output, [raw])
    audit_record, = source_messages(next(source_files(audit_output)))
    assert audit_record.raw == raw
