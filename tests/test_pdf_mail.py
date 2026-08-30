"""Verify standalone printed-email PDF extraction against human-reviewed ground truth."""

import hashlib
import mailbox
import shutil
from difflib import SequenceMatcher
from pathlib import Path

import pytest

from mailarchiver.pdf_mail import extract_pdf_mail, write_pdf_mbox


DATA = Path(__file__).parent / "data"
PDF = DATA / "sipbadmin.pdf"
GROUND_TRUTH = DATA / "sipbadmin.mbox"
PDF_SHA256 = "724b9090dd531356c9a565b4579f148f454a0ece1b40075eeb5fcd065ef7e04a"

pytestmark = pytest.mark.skipif(shutil.which("pdftotext") is None, reason="Poppler pdftotext is unavailable")


def _ground_truth() -> list[mailbox.mboxMessage]:
    box = mailbox.mbox(GROUND_TRUTH, factory=None, create=False)
    try:
        return [box[key] for key in box.iterkeys()]
    finally:
        box.close()


def _reviewed_body(message: mailbox.mboxMessage) -> str:
    payload = message.get_payload(decode=True)
    return payload.decode("utf-8") if isinstance(payload, bytes) else str(message.get_payload())


def _comparable_text(value: str) -> str:
    return " ".join(value.casefold().split())


def test_sipbadmin_pdf_extracts_reviewed_message_structure(tmp_path: Path) -> None:
    """Requirement: derived messages retain exact PDF pages and reviewed structural ground truth."""
    before = hashlib.sha256(PDF.read_bytes()).hexdigest()
    expected = _ground_truth()

    result = extract_pdf_mail(PDF, handwritten_pages=frozenset({2}))

    assert result.pdf_sha256 == PDF_SHA256 == before
    assert result.page_count == 6
    assert result.non_message_pages == (1, 6)
    assert [message.page_start for message in result.messages] == [
        int(message["X-Mailarchiver-Source-Page-Start"]) for message in expected
    ]
    assert [message.subject for message in result.messages] == [message["Subject"] for message in expected]
    assert [message.has_handwritten_annotations for message in result.messages] == [True, False, False, False]
    similarities = [
        SequenceMatcher(
            None,
            _comparable_text(actual.body),
            _comparable_text(_reviewed_body(reviewed)),
            autojunk=False,
        ).ratio()
        for actual, reviewed in zip(result.messages, expected, strict=True)
    ]
    assert similarities[0] > 0.55  # The PDF's page-2 text layer contains a second, interleaved message.
    assert min(similarities[1:]) > 0.90

    output = tmp_path / "sipbadmin.mbox"
    write_pdf_mbox(result, output)
    second_output = tmp_path / "sipbadmin-again.mbox"
    write_pdf_mbox(result, second_output)
    assert output.read_bytes() == second_output.read_bytes()
    with pytest.raises(FileExistsError):
        write_pdf_mbox(result, output)

    generated = mailbox.mbox(output, factory=None, create=False)
    try:
        assert len(generated) == 4
        assert [generated[index]["X-Mailarchiver-Source-Page-Start"] for index in range(4)] == ["2", "3", "4", "5"]
        assert [generated[index]["X-Mailarchiver-Handwritten-Annotations"] for index in range(4)] == [
            "yes",
            "no",
            "no",
            "no",
        ]
        assert all(generated[index]["X-Mailarchiver-Transcription-Status"] == "machine-unreviewed" for index in range(4))
    finally:
        generated.close()
    assert hashlib.sha256(PDF.read_bytes()).hexdigest() == before
