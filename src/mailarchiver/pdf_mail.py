"""Extract printed email records from standalone PDFs without modifying them."""

from __future__ import annotations

import argparse
import hashlib
import mailbox
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterable, Iterator
from email.message import EmailMessage
from email.policy import default
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .plugin_api import FrozenModel


PDF_MAGIC = b"%PDF-"
PDF_TEXT_POLICY = "native-pdf-text-v1"
SEGMENTATION_POLICY = "printed-email-page-v1"
DERIVED_KIND = "printed-email-pdf-v1"
HEADER_LINE = re.compile(r"^(?P<name>[A-Za-z][A-Za-z0-9-]{0,39}):[ \t]*(?P<value>.*)$")
COPIED_HEADER_NAMES = frozenset(
    {"bcc", "cc", "date", "from", "received", "reply-to", "return-path", "sender", "subject", "to"}
)


class PdfMailExtractionError(RuntimeError):
    """A standalone PDF could not be converted to page-addressable text."""


class ObservedHeader(FrozenModel):
    """One unfolded header observation from extracted page text."""

    name: str = Field(min_length=1)
    value: str


class PdfTextPage(FrozenModel):
    """Page-addressable text supplied by any extraction policy."""

    page_number: int = Field(ge=1)
    text: str


class ClassifiedPdfPage(PdfTextPage):
    """Page text plus its conservative printed-message classification."""

    classification: Literal["printed-email", "non-message"]


class PrintedEmailRecord(FrozenModel):
    """A provisional message interpretation anchored to exact PDF pages."""

    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    headers: tuple[ObservedHeader, ...]
    body: str
    extracted_text: str
    subject: str
    has_handwritten_annotations: bool = False

    @model_validator(mode="after")
    def validate_page_range(self) -> PrintedEmailRecord:
        if self.page_end < self.page_start:
            raise ValueError("page_end cannot precede page_start")
        return self


class PdfMailExtraction(FrozenModel):
    """Complete, reproducible interpretation of one preserved source PDF."""

    source_pdf: Path
    pdf_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_length: int = Field(ge=0)
    extraction_policy: str = PDF_TEXT_POLICY
    segmentation_policy: str = SEGMENTATION_POLICY
    pages: tuple[ClassifiedPdfPage, ...]
    messages: tuple[PrintedEmailRecord, ...]

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def non_message_pages(self) -> tuple[int, ...]:
        return tuple(page.page_number for page in self.pages if page.classification == "non-message")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pdftotext(executable: str | Path | None) -> str:
    if executable is not None:
        return str(executable)
    resolved = shutil.which("pdftotext")
    if resolved is None:
        raise PdfMailExtractionError("Poppler pdftotext is required for native PDF text extraction")
    return resolved


def _page_texts(path: Path, executable: str | Path | None) -> Iterator[tuple[int, str]]:
    """Stream Poppler's form-feed-delimited page text without rewriting the PDF."""
    with tempfile.TemporaryFile() as diagnostics:
        process = subprocess.Popen(
            [_pdftotext(executable), "-layout", str(path.resolve()), "-"],
            stdout=subprocess.PIPE,
            stderr=diagnostics,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        page_number = 1
        buffered = ""
        try:
            while chunk := process.stdout.read(64 * 1024):
                buffered += chunk
                while "\f" in buffered:
                    page, buffered = buffered.split("\f", 1)
                    yield page_number, page.rstrip()
                    page_number += 1
            if buffered:
                yield page_number, buffered.rstrip()
            return_code = process.wait()
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.terminate()
                process.wait()
        if return_code:
            diagnostics.seek(0)
            detail = diagnostics.read().decode("utf-8", "replace").strip()
            raise PdfMailExtractionError(f"pdftotext failed for {path}: {detail or f'exit {return_code}'}")


def _header_block(text: str) -> tuple[tuple[ObservedHeader, ...], str]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    start = next((index for index, line in enumerate(lines) if line.strip()), len(lines))
    headers: list[ObservedHeader] = []
    current_name: str | None = None
    current_value: list[str] = []
    body_start = start

    def finish_header() -> None:
        nonlocal current_name, current_value
        if current_name is not None:
            headers.append(ObservedHeader(name=current_name, value=" ".join(current_value).strip()))
        current_name = None
        current_value = []

    for index in range(start, len(lines)):
        line = lines[index]
        if not line.strip():
            finish_header()
            body_start = index + 1
            break
        match = HEADER_LINE.fullmatch(line)
        if match is not None:
            finish_header()
            current_name = match.group("name")
            current_value = [match.group("value")]
            continue
        if current_name is not None and line[:1].isspace():
            current_value.append(line.strip())
            continue
        return (), text
    else:
        finish_header()
        body_start = len(lines)
    return tuple(headers), "\n".join(lines[body_start:]).rstrip()


def _is_printed_email(headers: tuple[ObservedHeader, ...]) -> bool:
    names = {header.name.casefold() for header in headers}
    return {"date", "subject"} <= names and bool(names & {"from", "to"})


def extract_pdf_mail(
    path: Path,
    *,
    handwritten_pages: frozenset[int] = frozenset(),
    pdftotext: str | Path | None = None,
) -> PdfMailExtraction:
    """Extract one provisional printed-email record from each qualifying page."""
    source = path.resolve()
    before = source.stat()
    with source.open("rb") as stream:
        if stream.read(len(PDF_MAGIC)) != PDF_MAGIC:
            raise PdfMailExtractionError(f"not a PDF file: {path}")
    source_sha256 = _sha256(source)
    page_text = tuple(PdfTextPage(page_number=number, text=text) for number, text in _page_texts(source, pdftotext))
    result = segment_pdf_mail(
        source,
        source_sha256,
        before.st_size,
        page_text,
        extraction_policy=PDF_TEXT_POLICY,
        handwritten_pages=handwritten_pages,
    )
    after = source.stat()
    metadata_changed = (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns)
    if metadata_changed or _sha256(source) != source_sha256:
        raise PdfMailExtractionError(f"source PDF changed during extraction: {path}")
    return result


def segment_pdf_mail(
    source_pdf: Path,
    pdf_sha256: str,
    byte_length: int,
    page_text: Iterable[PdfTextPage],
    *,
    extraction_policy: str,
    handwritten_pages: frozenset[int] = frozenset(),
) -> PdfMailExtraction:
    """Segment typed page text independently of the engine that produced it."""
    pages: list[ClassifiedPdfPage] = []
    messages: list[PrintedEmailRecord] = []
    expected_page = 1
    for page in page_text:
        if page.page_number != expected_page:
            raise ValueError(f"page text must be consecutive from 1; expected {expected_page}, got {page.page_number}")
        expected_page += 1
        headers, body = _header_block(page.text)
        printed_email = _is_printed_email(headers)
        pages.append(
            ClassifiedPdfPage(
                page_number=page.page_number,
                text=page.text,
                classification="printed-email" if printed_email else "non-message",
            )
        )
        if printed_email:
            subject = next(header.value for header in headers if header.name.casefold() == "subject")
            messages.append(
                PrintedEmailRecord(
                    page_start=page.page_number,
                    page_end=page.page_number,
                    headers=headers,
                    body=body,
                    extracted_text=page.text,
                    subject=subject,
                    has_handwritten_annotations=page.page_number in handwritten_pages,
                )
            )
    unknown_handwriting = handwritten_pages - {page.page_number for page in pages}
    if unknown_handwriting:
        raise ValueError(f"handwritten page does not exist: {min(unknown_handwriting)}")
    return PdfMailExtraction(
        source_pdf=source_pdf,
        pdf_sha256=pdf_sha256,
        byte_length=byte_length,
        extraction_policy=extraction_policy,
        pages=tuple(pages),
        messages=tuple(messages),
    )


def _message_bytes(extraction: PdfMailExtraction, record: PrintedEmailRecord) -> bytes:
    message = EmailMessage(policy=default)
    message["Message-ID"] = f"<pdf-{extraction.pdf_sha256}-p{record.page_start}@mailarchiver.invalid>"
    message["X-Mailarchiver-Derived"] = DERIVED_KIND
    message["X-Mailarchiver-Transcription-Status"] = "machine-unreviewed"
    message["X-Mailarchiver-Source-PDF"] = extraction.source_pdf.name
    message["X-Mailarchiver-Source-PDF-SHA256"] = extraction.pdf_sha256
    message["X-Mailarchiver-Source-Page-Start"] = str(record.page_start)
    message["X-Mailarchiver-Source-Page-End"] = str(record.page_end)
    message["X-Mailarchiver-Extraction-Policy"] = extraction.extraction_policy
    message["X-Mailarchiver-Segmentation-Policy"] = extraction.segmentation_policy
    message["X-Mailarchiver-Handwritten-Annotations"] = "yes" if record.has_handwritten_annotations else "no"
    for header in record.headers:
        name = header.name.casefold()
        if name == "message-id":
            message["X-Mailarchiver-Observed-Message-ID"] = header.value
        elif name in COPIED_HEADER_NAMES:
            message[header.name] = header.value
    message.set_content(record.body, subtype="plain", charset="utf-8", cte="8bit")
    return message.as_bytes(policy=default.clone(linesep="\n", utf8=True))


def write_pdf_mbox(extraction: PdfMailExtraction, output: Path) -> None:
    """Atomically write standard MBOX records for one PDF interpretation."""
    destination = output.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    box = mailbox.mbox(temporary, factory=None, create=True)
    try:
        box.lock()
        for record in extraction.messages:
            raw = b"From pdf-scan@localhost Thu Jan  1 00:00:00 1970\n" + _message_bytes(extraction, record)
            box.add(raw)
        box.flush()
        box.unlock()
        box.close()
        os.replace(temporary, destination)
    except BaseException:
        try:
            box.close()
        finally:
            temporary.unlink(missing_ok=True)
        raise


def positive_page(value: str) -> int:
    page = int(value)
    if page < 1:
        raise argparse.ArgumentTypeError("page must be greater than zero")
    return page


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="extract printed email from a standalone PDF into derived MBOX")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--handwritten-page",
        action="append",
        default=[],
        type=positive_page,
        help="source page containing useful handwritten annotations; repeatable",
    )
    args = parser.parse_args(argv)
    extraction = extract_pdf_mail(args.pdf, handwritten_pages=frozenset(args.handwritten_page))
    write_pdf_mbox(extraction, args.output)
    print(
        f"Extracted {len(extraction.messages)} messages from {extraction.page_count} pages; "
        f"non-message pages: {','.join(map(str, extraction.non_message_pages)) or 'none'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
