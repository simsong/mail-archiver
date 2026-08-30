#!/usr/bin/env python3
"""Inventory PDF MIME attachments and run resumable, source-preserving OCR trials."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from email import policy
from email.parser import BytesParser
from pathlib import Path
from statistics import median

from pydantic import BaseModel

from mailarchiver.mbox import MboxLocation, read_verified_location


PDFINFO_ENCRYPTED = "Encrypted"
PDFINFO_PAGES = "Pages"


class PdfCandidate(BaseModel):
    message_pk: int
    message_sha256: str
    attachment_ordinal: int
    part_id: int
    filename: str
    declared_mime_type: str
    mbox_filename: str
    byte_offset: int
    byte_length: int


class PdfInstance(BaseModel):
    message_pk: int
    message_sha256: str
    attachment_ordinal: int
    part_id: int
    filename: str
    declared_mime_type: str = "application/pdf"
    pdf_sha256: str
    byte_length: int
    input_file: str


class PdfDocument(BaseModel):
    pdf_sha256: str
    byte_length: int
    input_file: str
    instance_count: int


class InventoryBoundary(BaseModel):
    max_message_pk: int
    candidate_count: int
    selection: str = "application/pdf MIME type or .pdf filename; filename-only candidates require PDF magic"


class PdfProfile(BaseModel):
    pdf_sha256: str
    valid: bool
    pages: int | None = None
    encrypted: bool | None = None
    error: str = ""


class TextMetrics(BaseModel):
    pdf_sha256: str
    engine: str
    status: str
    byte_count: int = 0
    character_count: int = 0
    line_count: int = 0
    word_count: int = 0
    page_break_count: int = 0
    email_address_count: int = 0
    mail_header_count: int = 0
    replacement_count: int = 0
    mojibake_count: int = 0
    control_count: int = 0
    repeated_glyph_runs: int = 0
    very_long_tokens: int = 0
    symbol_heavy_lines: int = 0
    artifact_rate_per_million: float = 0.0


class EngineComparison(BaseModel):
    pdf_sha256: str
    first_engine: str
    second_engine: str
    token_count_first: int
    token_count_second: int
    weighted_token_jaccard: float
    length_ratio: float


class EngineRollup(BaseModel):
    engine: str
    ok: int
    error: int
    missing: int
    total_words: int
    total_page_breaks: int
    total_mail_headers: int
    empty: int
    under_100_characters: int
    total_replacements: int
    total_mojibake: int
    total_controls: int
    total_repeated_glyph_runs: int
    total_very_long_tokens: int
    total_symbol_heavy_lines: int
    median_artifact_rate_per_million: float
    p95_artifact_rate_per_million: float


class ComparisonRollup(BaseModel):
    first_engine: str
    second_engine: str
    count: int
    median_weighted_token_jaccard: float
    p10_weighted_token_jaccard: float
    median_length_ratio: float
    p10_length_ratio: float


def connection(archive: Path) -> sqlite3.Connection:
    database = sqlite3.connect(f"file:{archive / 'archive.sqlite3'}?mode=ro", uri=True, timeout=30)
    database.execute("PRAGMA query_only=ON")
    database.execute("PRAGMA busy_timeout=30000")
    database.execute("ATTACH DATABASE ? AS search", (f"file:{archive / 'search.sqlite3'}?mode=ro",))
    return database


def candidates(archive: Path, max_message_pk: int | None) -> tuple[int, list[PdfCandidate]]:
    database = connection(archive)
    try:
        database.execute("BEGIN")
        if max_message_pk is None:
            max_message_pk = database.execute("SELECT max(message_pk) FROM messages").fetchone()[0]
        if max_message_pk is None:
            return 0, []
        rows = database.execute(
            "SELECT messages.message_pk, messages.sha256, attachments.attachment_ordinal, "
            "attachments.part_id, attachments.filename, attachments.mime_type, generations.filename, "
            "locations.byte_offset, locations.byte_length FROM search.message_attachments attachments "
            "JOIN messages USING (sha256) JOIN locations USING (message_pk) "
            "JOIN mbox_generations generations USING (generation_pk) "
            "WHERE messages.message_pk <= ? AND (lower(attachments.mime_type) = 'application/pdf' "
            "OR lower(attachments.filename) LIKE '%.pdf') "
            "ORDER BY messages.message_pk, attachments.attachment_ordinal",
            (max_message_pk,),
        ).fetchall()
        return max_message_pk, [
            PdfCandidate(
                message_pk=row[0],
                message_sha256=row[1],
                attachment_ordinal=row[2],
                part_id=row[3],
                filename=row[4],
                declared_mime_type=row[5],
                mbox_filename=row[6],
                byte_offset=row[7],
                byte_length=row[8],
            )
            for row in rows
        ]
    finally:
        database.close()


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def payload_for(message, part_id: int) -> bytes:
    parts = list(message.walk()) if message.is_multipart() else [message]
    if part_id < 0 or part_id >= len(parts):
        raise ValueError(f"missing MIME part {part_id}")
    payload = parts[part_id].get_payload(decode=True)
    if isinstance(payload, bytes):
        return payload
    value = parts[part_id].get_payload()
    if isinstance(value, str):
        return value.encode(parts[part_id].get_content_charset() or "utf-8", "replace")
    return b""


def write_jsonl(path: Path, models: list[BaseModel]) -> None:
    text = "".join(model.model_dump_json() + "\n" for model in models)
    atomic_write(path, text.encode("utf-8"))


def has_pdf_magic(payload: bytes) -> bool:
    return b"%PDF-" in payload[:1024]


def inventory(archive: Path, output: Path, max_message_pk: int | None) -> None:
    output.mkdir(parents=True, exist_ok=True)
    inputs = output / "input-pdfs"
    instances: list[PdfInstance] = []
    errors: list[str] = []
    rejected: list[str] = []
    boundary, rows = candidates(archive, max_message_pk)
    grouped: dict[int, list[PdfCandidate]] = {}
    for item in rows:
        grouped.setdefault(item.message_pk, []).append(item)
    for position, (message_pk, items) in enumerate(grouped.items(), 1):
        item = items[0]
        try:
            raw = read_verified_location(
                archive / "data" / "mbox" / item.mbox_filename,
                MboxLocation(byte_offset=item.byte_offset, byte_length=item.byte_length),
                item.message_sha256,
            )
            message = BytesParser(policy=policy.compat32).parsebytes(raw)
            for candidate in items:
                payload = payload_for(message, candidate.part_id)
                if candidate.declared_mime_type.casefold() != "application/pdf" and not has_pdf_magic(payload):
                    rejected.append(
                        f"message {message_pk} attachment {candidate.attachment_ordinal}: "
                        f"{candidate.declared_mime_type} {candidate.filename!r} lacks PDF magic"
                    )
                    continue
                digest = hashlib.sha256(payload).hexdigest()
                relative = Path("input-pdfs") / f"{digest}.pdf"
                destination = output / relative
                if destination.exists():
                    if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                        raise ValueError(f"existing input has wrong SHA-256: {destination}")
                else:
                    atomic_write(destination, payload)
                instances.append(
                    PdfInstance(
                        message_pk=candidate.message_pk,
                        message_sha256=candidate.message_sha256,
                        attachment_ordinal=candidate.attachment_ordinal,
                        part_id=candidate.part_id,
                        filename=candidate.filename,
                        declared_mime_type=candidate.declared_mime_type,
                        pdf_sha256=digest,
                        byte_length=len(payload),
                        input_file=str(relative),
                    )
                )
        except (OSError, ValueError) as error:
            errors.append(f"message {message_pk}: {error}")
        if position % 100 == 0 or position == len(grouped):
            print(f"inventoried {position}/{len(grouped)} messages; {len(instances)} PDF instances", flush=True)
    counts: dict[str, int] = {}
    documents: dict[str, PdfDocument] = {}
    for item in instances:
        counts[item.pdf_sha256] = counts.get(item.pdf_sha256, 0) + 1
        documents[item.pdf_sha256] = PdfDocument(
            pdf_sha256=item.pdf_sha256,
            byte_length=item.byte_length,
            input_file=item.input_file,
            instance_count=counts[item.pdf_sha256],
        )
    write_jsonl(output / "instances.jsonl", instances)
    write_jsonl(output / "documents.jsonl", sorted(documents.values(), key=lambda item: item.pdf_sha256))
    atomic_write(
        output / "inventory-boundary.json",
        (InventoryBoundary(max_message_pk=boundary, candidate_count=len(rows)).model_dump_json(indent=2) + "\n").encode(
            "utf-8"
        ),
    )
    atomic_write(output / "inventory-errors.txt", ("\n".join(errors) + ("\n" if errors else "")).encode("utf-8"))
    atomic_write(
        output / "non-pdf-candidates.txt",
        ("\n".join(rejected) + ("\n" if rejected else "")).encode("utf-8"),
    )
    with (output / "instances.csv.tmp").open("w", encoding="utf-8", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=list(PdfInstance.model_fields))
        writer.writeheader()
        writer.writerows(item.model_dump() for item in instances)
    (output / "instances.csv.tmp").replace(output / "instances.csv")
    print(f"PDF attachment instances: {len(instances)}")
    print(f"Unique PDF payloads: {len(documents)}")
    print(f"Inventory errors: {len(errors)}")
    print(f"Filename-only candidates rejected by PDF magic: {len(rejected)}")
    print(f"Message PK boundary: {boundary}")


def command_output(command: list[str], timeout: int = 1800) -> bytes:
    result = subprocess.run(command, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"exit {result.returncode}: {detail[-4000:]}")
    return result.stdout


def native_text(source: Path, destination: Path) -> None:
    command_output(["pdftotext", "-layout", str(source), str(destination)])


def ocrmypdf_text(source: Path, destination: Path) -> None:
    command_output(
        [
            "ocrmypdf", "--output-type", "none", "--sidecar", str(destination),
            "--force-ocr", "--rotate-pages", "--deskew", "--continue-on-soft-render-error",
            "--invalidate-digital-signatures",
            "--tesseract-timeout", "300", "--jobs", "1", str(source), "-",
        ]
    )


def page_number(path: Path) -> int:
    match = re.search(r"-(\d+)\.png$", path.name)
    return int(match.group(1)) if match else 0


def tesseract_text(source: Path, destination: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="mailarchiver-ocr-") as directory:
        prefix = Path(directory) / "page"
        command_output(["pdftoppm", "-r", "300", "-png", str(source), str(prefix)])
        pages = sorted(Path(directory).glob("page-*.png"), key=page_number)
        if not pages:
            raise RuntimeError("PDF rendered no pages")
        text = b"\n\f\n".join(command_output(["tesseract", str(page), "stdout", "-l", "eng"]) for page in pages)
        atomic_write(destination, text)


def abbyy_text(source: Path, destination: Path) -> None:
    script = Path(__file__).with_name("ocr_abbyy.applescript")
    with tempfile.TemporaryDirectory(prefix="mailarchiver-abbyy-") as directory:
        recognized = Path(directory) / "recognized.pdf"
        command_output(["osascript", str(script), str(source), str(recognized)], timeout=3600)
        native_text(recognized, destination)


def word_text(source: Path, destination: Path) -> None:
    script = Path(__file__).with_name("ocr_word.applescript")
    with tempfile.TemporaryDirectory(prefix="mailarchiver-word-") as directory:
        exported = Path(directory) / "recognized.txt"
        command_output(["osascript", str(script), str(source), str(exported)], timeout=3600)
        payload = exported.read_bytes()
        encoding = "utf-16" if payload.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8"
        atomic_write(destination, payload.decode(encoding, "replace").encode("utf-8"))


ENGINES = {
    "native": native_text,
    "ocrmypdf": ocrmypdf_text,
    "tesseract": tesseract_text,
    "abbyy": abbyy_text,
    "word": word_text,
}


def load_documents(output: Path) -> list[PdfDocument]:
    path = output / "documents.jsonl"
    if not path.exists():
        raise SystemExit(f"missing inventory: {path}")
    return [PdfDocument.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines()]


def profile_one(output: Path, document: PdfDocument) -> PdfProfile:
    try:
        metadata = command_output(["pdfinfo", str(output / document.input_file)]).decode("utf-8", "replace")
        values = {line.partition(":")[0]: line.partition(":")[2].strip() for line in metadata.splitlines() if ":" in line}
        return PdfProfile(
            pdf_sha256=document.pdf_sha256,
            valid=True,
            pages=int(values[PDFINFO_PAGES]),
            encrypted=values.get(PDFINFO_ENCRYPTED, "no").split()[0].casefold() == "yes",
        )
    except (KeyError, OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        return PdfProfile(pdf_sha256=document.pdf_sha256, valid=False, error=str(error))


def profile(output: Path, workers: int) -> None:
    documents = load_documents(output)
    profiles: list[PdfProfile] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(profile_one, output, document) for document in documents]
        for position, future in enumerate(as_completed(futures), 1):
            profiles.append(future.result())
            if position % 100 == 0 or position == len(futures):
                print(f"profiled {position}/{len(futures)} PDFs", flush=True)
    profiles.sort(key=lambda item: item.pdf_sha256)
    write_jsonl(output / "profiles.jsonl", profiles)
    print(f"valid={sum(item.valid for item in profiles)} invalid={sum(not item.valid for item in profiles)}")
    print(f"pages={sum(item.pages or 0 for item in profiles)} encrypted={sum(bool(item.encrypted) for item in profiles)}")


def text_metrics(output: Path, document: PdfDocument, engine: str) -> TextMetrics:
    path = output / engine / f"{document.pdf_sha256}.txt"
    error_path = output / engine / f"{document.pdf_sha256}.error.txt"
    if error_path.exists():
        return TextMetrics(pdf_sha256=document.pdf_sha256, engine=engine, status="error")
    if not path.exists():
        return TextMetrics(pdf_sha256=document.pdf_sha256, engine=engine, status="missing")
    payload = path.read_bytes()
    text = payload.decode("utf-8", "replace")
    words = re.findall(r"\S+", text)
    headers = re.findall(r"(?im)^\s*(?:from|to|cc|bcc|subject|date|sent):\s*\S", text)
    replacement_count = text.count("\ufffd")
    mojibake_count = sum(text.count(value) for value in ("Ã", "Â", "â€", "ðŸ"))
    control_count = sum(ord(character) < 32 and character not in "\n\r\t\f" for character in text)
    repeated_glyph_runs = len(re.findall(r"([^\s])\1{5,}", text))
    very_long_tokens = sum(len(word) > 80 for word in words)
    symbol_heavy_lines = sum(
        bool(line.strip())
        and sum(character.isalnum() for character in line) / len(line.strip()) < 0.2
        and len(line.strip()) >= 8
        for line in text.splitlines()
    )
    artifact_count = (
        replacement_count * 10
        + mojibake_count * 5
        + control_count * 20
        + repeated_glyph_runs * 5
        + very_long_tokens * 2
        + symbol_heavy_lines
    )
    return TextMetrics(
        pdf_sha256=document.pdf_sha256,
        engine=engine,
        status="ok",
        byte_count=len(payload),
        character_count=len(text),
        line_count=len(text.splitlines()),
        word_count=len(words),
        page_break_count=text.count("\f"),
        email_address_count=len(re.findall(r"(?i)\b[\w.!#$%&'*+/=?^`{|}~-]+@[\w.-]+\.[a-z]{2,}\b", text)),
        mail_header_count=len(headers),
        replacement_count=replacement_count,
        mojibake_count=mojibake_count,
        control_count=control_count,
        repeated_glyph_runs=repeated_glyph_runs,
        very_long_tokens=very_long_tokens,
        symbol_heavy_lines=symbol_heavy_lines,
        artifact_rate_per_million=artifact_count * 1_000_000 / max(len(text), 1),
    )


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * fraction)]


def write_analysis_rollups(output: Path, metrics: list[TextMetrics], comparisons: list[EngineComparison]) -> None:
    engines = sorted({item.engine for item in metrics})
    engine_rollups: list[EngineRollup] = []
    for engine in engines:
        rows = [item for item in metrics if item.engine == engine]
        successful = [item for item in rows if item.status == "ok"]
        rates = [item.artifact_rate_per_million for item in successful]
        engine_rollups.append(
            EngineRollup(
                engine=engine,
                ok=sum(item.status == "ok" for item in rows),
                error=sum(item.status == "error" for item in rows),
                missing=sum(item.status == "missing" for item in rows),
                total_words=sum(item.word_count for item in rows),
                total_page_breaks=sum(item.page_break_count for item in rows),
                total_mail_headers=sum(item.mail_header_count for item in rows),
                empty=sum(item.character_count == 0 for item in successful),
                under_100_characters=sum(item.character_count < 100 for item in successful),
                total_replacements=sum(item.replacement_count for item in rows),
                total_mojibake=sum(item.mojibake_count for item in rows),
                total_controls=sum(item.control_count for item in rows),
                total_repeated_glyph_runs=sum(item.repeated_glyph_runs for item in rows),
                total_very_long_tokens=sum(item.very_long_tokens for item in rows),
                total_symbol_heavy_lines=sum(item.symbol_heavy_lines for item in rows),
                median_artifact_rate_per_million=median(rates) if rates else 0.0,
                p95_artifact_rate_per_million=percentile(rates, 0.95),
            )
        )
    pairs = sorted({(item.first_engine, item.second_engine) for item in comparisons})
    comparison_rollups: list[ComparisonRollup] = []
    for first_engine, second_engine in pairs:
        rows = [
            item
            for item in comparisons
            if item.first_engine == first_engine and item.second_engine == second_engine
        ]
        agreements = [item.weighted_token_jaccard for item in rows]
        length_ratios = [item.length_ratio for item in rows]
        comparison_rollups.append(
            ComparisonRollup(
                first_engine=first_engine,
                second_engine=second_engine,
                count=len(rows),
                median_weighted_token_jaccard=median(agreements),
                p10_weighted_token_jaccard=percentile(agreements, 0.10),
                median_length_ratio=median(length_ratios),
                p10_length_ratio=percentile(length_ratios, 0.10),
            )
        )
    summary = {
        "engines": [item.model_dump() for item in engine_rollups],
        "comparisons": [item.model_dump() for item in comparison_rollups],
    }
    atomic_write(output / "analysis-summary.json", (json.dumps(summary, indent=2) + "\n").encode("utf-8"))
    outliers = sorted(
        (item for item in metrics if item.status == "ok"),
        key=lambda item: (item.engine, -item.artifact_rate_per_million, -item.character_count),
    )
    outlier_path = output / "artifact-outliers.csv.tmp"
    with outlier_path.open("w", encoding="utf-8", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=list(TextMetrics.model_fields))
        writer.writeheader()
        for engine in engines:
            engine_outliers = [item for item in outliers if item.engine == engine][:100]
            writer.writerows(item.model_dump() for item in engine_outliers)
    outlier_path.replace(output / "artifact-outliers.csv")


def analyze(output: Path, engines: list[str]) -> None:
    documents = load_documents(output)
    metrics = [text_metrics(output, document, engine) for document in documents for engine in engines]
    write_jsonl(output / "text-metrics.jsonl", metrics)
    temporary = output / "text-metrics.csv.tmp"
    with temporary.open("w", encoding="utf-8", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=list(TextMetrics.model_fields))
        writer.writeheader()
        writer.writerows(item.model_dump() for item in metrics)
    temporary.replace(output / "text-metrics.csv")
    comparisons: list[EngineComparison] = []
    for document in documents:
        for first_index, first_engine in enumerate(engines):
            first_path = output / first_engine / f"{document.pdf_sha256}.txt"
            if not first_path.exists():
                continue
            first_text = first_path.read_text(encoding="utf-8", errors="replace")
            first_tokens = Counter(re.findall(r"(?u)[\w@.+-]+", first_text.casefold()))
            for second_engine in engines[first_index + 1 :]:
                second_path = output / second_engine / f"{document.pdf_sha256}.txt"
                if not second_path.exists():
                    continue
                second_text = second_path.read_text(encoding="utf-8", errors="replace")
                second_tokens = Counter(re.findall(r"(?u)[\w@.+-]+", second_text.casefold()))
                union = sum((first_tokens | second_tokens).values())
                larger_length = max(len(first_text), len(second_text))
                comparisons.append(
                    EngineComparison(
                        pdf_sha256=document.pdf_sha256,
                        first_engine=first_engine,
                        second_engine=second_engine,
                        token_count_first=sum(first_tokens.values()),
                        token_count_second=sum(second_tokens.values()),
                        weighted_token_jaccard=(sum((first_tokens & second_tokens).values()) / union if union else 1.0),
                        length_ratio=(min(len(first_text), len(second_text)) / larger_length if larger_length else 1.0),
                    )
                )
    write_jsonl(output / "engine-comparisons.jsonl", comparisons)
    comparison_csv = output / "engine-comparisons.csv.tmp"
    with comparison_csv.open("w", encoding="utf-8", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=list(EngineComparison.model_fields))
        writer.writeheader()
        writer.writerows(item.model_dump() for item in comparisons)
    comparison_csv.replace(output / "engine-comparisons.csv")
    write_analysis_rollups(output, metrics, comparisons)
    for engine in engines:
        rows = [item for item in metrics if item.engine == engine]
        print(
            f"{engine}: ok={sum(item.status == 'ok' for item in rows)} "
            f"error={sum(item.status == 'error' for item in rows)} "
            f"missing={sum(item.status == 'missing' for item in rows)} "
            f"words={sum(item.word_count for item in rows)} "
            f"mail_headers={sum(item.mail_header_count for item in rows)}"
        )
    instances = [
        PdfInstance.model_validate_json(line)
        for line in (output / "instances.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    linked = 0
    for instance in instances:
        directory = output / "by-instance" / (
            f"message-{instance.message_pk}-attachment-{instance.attachment_ordinal}-part-{instance.part_id}"
        )
        directory.mkdir(parents=True, exist_ok=True)
        for engine in engines:
            source = output / engine / f"{instance.pdf_sha256}.txt"
            name = f"{engine}.txt"
            if not source.exists():
                source = output / engine / f"{instance.pdf_sha256}.error.txt"
                name = f"{engine}.error.txt"
            if not source.exists():
                continue
            destination = directory / name
            relative = Path(os.path.relpath(source, directory))
            if destination.is_symlink() and Path(os.readlink(destination)) == relative:
                linked += 1
                continue
            if destination.exists() or destination.is_symlink():
                if not destination.is_symlink():
                    raise RuntimeError(f"refusing to replace non-symlink result: {destination}")
                destination.unlink()
            destination.symlink_to(relative)
            linked += 1
    print(f"instance result links={linked}")
    print(f"engine comparisons={len(comparisons)}")


def run_one(output: Path, document: PdfDocument, engine: str) -> tuple[str, str, str]:
    source = output / document.input_file
    destination = output / engine / f"{document.pdf_sha256}.txt"
    error_path = output / engine / f"{document.pdf_sha256}.error.txt"
    if destination.exists() and not error_path.exists():
        return engine, document.pdf_sha256, "skipped"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".txt.tmp")
    temporary.unlink(missing_ok=True)
    try:
        ENGINES[engine](source, temporary)
        temporary.replace(destination)
        error_path.unlink(missing_ok=True)
        return engine, document.pdf_sha256, "ok"
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        temporary.unlink(missing_ok=True)
        atomic_write(error_path, (str(error) + "\n").encode("utf-8", "replace"))
        return engine, document.pdf_sha256, "error"


def run(
    output: Path,
    engines: list[str],
    workers: int,
    limit: int | None,
    digest: str | None,
    selection: str,
) -> None:
    unknown = set(engines) - set(ENGINES)
    if unknown:
        raise SystemExit(f"unknown engines: {', '.join(sorted(unknown))}")
    if {"abbyy", "word"} & set(engines) and workers != 1:
        raise SystemExit("GUI application automation requires --workers 1")
    documents = load_documents(output)
    if selection == "filename-only":
        instances = [
            PdfInstance.model_validate_json(line)
            for line in (output / "instances.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        standard_mime_digests = {
            item.pdf_sha256 for item in instances if item.declared_mime_type.casefold() == "application/pdf"
        }
        documents = [document for document in documents if document.pdf_sha256 not in standard_mime_digests]
    if digest is not None:
        documents = [document for document in documents if document.pdf_sha256.startswith(digest)]
        if len(documents) != 1:
            raise SystemExit(f"--digest matched {len(documents)} PDFs; require exactly one")
    if limit is not None:
        documents = documents[:limit]
    jobs = [(document, engine) for document in documents for engine in engines]
    counts = {"ok": 0, "skipped": 0, "error": 0}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(run_one, output, document, engine) for document, engine in jobs]
        for position, future in enumerate(as_completed(futures), 1):
            engine, digest, status = future.result()
            counts[status] += 1
            if status == "error" or position % 25 == 0 or position == len(jobs):
                print(f"{position}/{len(jobs)} {engine} {digest[:12]} {status}", flush=True)
    print(json.dumps(counts, sort_keys=True))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    inventory_parser = commands.add_parser("inventory")
    inventory_parser.add_argument("--archive", type=Path, required=True)
    inventory_parser.add_argument("--output", type=Path, required=True)
    inventory_parser.add_argument("--max-message-pk", type=int)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--output", type=Path, required=True)
    run_parser.add_argument("--engines", default=",".join(ENGINES))
    run_parser.add_argument("--workers", type=int, default=max(1, min(os.cpu_count() or 1, 4)))
    run_parser.add_argument("--limit", type=int)
    run_parser.add_argument("--digest")
    run_parser.add_argument("--selection", choices=("all", "filename-only"), default="all")
    profile_parser = commands.add_parser("profile")
    profile_parser.add_argument("--output", type=Path, required=True)
    profile_parser.add_argument("--workers", type=int, default=max(1, min(os.cpu_count() or 1, 8)))
    analyze_parser = commands.add_parser("analyze")
    analyze_parser.add_argument("--output", type=Path, required=True)
    analyze_parser.add_argument("--engines", default=",".join(ENGINES))
    return root


def main() -> None:
    args = parser().parse_args()
    if args.command == "inventory":
        inventory(args.archive.resolve(), args.output.resolve(), args.max_message_pk)
    elif args.command == "run":
        if args.workers < 1:
            raise SystemExit("--workers must be positive")
        if args.limit is not None and args.limit < 1:
            raise SystemExit("--limit must be positive")
        run(
            args.output.resolve(),
            [item.strip() for item in args.engines.split(",") if item.strip()],
            args.workers,
            args.limit,
            args.digest,
            args.selection,
        )
    elif args.command == "profile":
        if args.workers < 1:
            raise SystemExit("--workers must be positive")
        profile(args.output.resolve(), args.workers)
    elif args.command == "analyze":
        analyze(args.output.resolve(), [item.strip() for item in args.engines.split(",") if item.strip()])


if __name__ == "__main__":
    main()
