"""Create reproducible, read-only mail-archive data-quality evidence."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import mailbox
import random
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from mailarchiver.layout import mbox_path
from mailarchiver.mbox import MboxLocation, read_verified_location
from mailarchiver.source_volume import METADATA_CURRENT_MOUNT_PATH
from mailarchiver.sources import source_files, source_messages


SAMPLE_SEED = 20260827
MIN_REAL_YEAR = 1983
NORMAL_CATEGORIES = ("Archive", "Sent")
SENDER_HEADERS = ("From", "Resent-From", "Reply-To", "Return-Path", "X-Sender", "X-Envelope-From", "Errors-To")


class CatalogMessage(BaseModel):
    message_pk: int
    sha256: str
    sender: str
    subject: str
    date_utc: str
    date_source: str
    category: str
    filename: str
    byte_offset: int
    byte_length: int


class SourceObservation(BaseModel):
    source_path: str
    source_kind: str
    source_offset: int
    disposition: str


class EarlyDateEvidence(BaseModel):
    message_pk: int
    sha256: str
    catalog_date: str
    catalog_date_source: str
    category: str
    sender: str
    subject: str
    raw_date: str
    parsed_date: str
    received_dates: str
    plausible_received_date: str
    previous_catalog_date: str
    next_catalog_date: str
    source_paths: str


class MissingSenderEvidence(BaseModel):
    sample_order: int
    message_pk: int
    sha256: str
    catalog_date: str
    date_source: str
    subject: str
    content_type: str
    from_values: str
    from_addresses: str
    candidate_header: str
    candidate_addresses: str
    source_boundary_sender: str
    source_paths: str
    gmail_thread: bool
    likely_kind: str


class EarlySourceFile(BaseModel):
    path: str
    size: int
    format: str
    message_count: int = 0
    first_date: str = ""
    last_date: str = ""
    error: str = ""


class AnalysisSummary(BaseModel):
    sample_seed: int = SAMPLE_SEED
    bad_date_count: int
    bad_date_years: dict[str, int]
    bad_date_plausible_received: int
    missing_sender_population: int
    missing_sender_sample_count: int
    missing_sender_sample_kinds: dict[str, int]
    missing_sender_candidate_headers: dict[str, int]
    early_source_files: int
    early_source_formats: dict[str, int]
    early_source_messages: int
    output_sha256: dict[str, str] = Field(default_factory=dict)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--early-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def open_catalog(archive: Path) -> sqlite3.Connection:
    database = (archive / "archive.sqlite3").resolve()
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def catalog_message(row: sqlite3.Row) -> CatalogMessage:
    return CatalogMessage(
        message_pk=row[0], sha256=row[1], sender=row[2], subject=row[3], date_utc=row[4],
        date_source=row[5], category=row[6], filename=row[7], byte_offset=row[8], byte_length=row[9],
    )


def selected_messages(connection: sqlite3.Connection, where: str, parameters: tuple[object, ...] = ()) -> list[CatalogMessage]:
    sql = (
        "SELECT m.message_pk,m.sha256,e.address,m.subject,m.date_utc,m.date_source,m.category,"
        "g.filename,l.byte_offset,l.byte_length FROM messages m JOIN email_addresses e ON "
        "e.address_pk=m.sender_address_pk JOIN locations l USING(message_pk) JOIN mbox_generations g "
        f"ON g.generation_pk=l.generation_pk WHERE {where} ORDER BY m.message_pk"
    )
    return [catalog_message(row) for row in connection.execute(sql, parameters)]


def raw_message(archive: Path, record: CatalogMessage) -> bytes:
    return read_verified_location(
        mbox_path(archive, record.filename),
        MboxLocation(byte_offset=record.byte_offset, byte_length=record.byte_length),
        record.sha256,
    )


def header_values(message: Message, name: str) -> list[str]:
    try:
        return [str(value) for value in message.get_all(name, [])]
    except Exception:
        return []


def normalized_date(value: str) -> datetime | None:
    try:
        parsed = parsedate_to_datetime(value)
    except Exception:
        return None
    if parsed is None:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def received_dates(message: Message) -> list[datetime]:
    dates: list[datetime] = []
    for value in header_values(message, "Received"):
        parsed = normalized_date(value.rsplit(";", 1)[-1].strip())
        if parsed is not None:
            dates.append(parsed)
    return sorted(dates)


def observations(connection: sqlite3.Connection, message_pk: int) -> list[SourceObservation]:
    rows = connection.execute(
        "SELECT sf.source_path,sf.source_kind,o.source_offset,o.disposition,sv.metadata_json "
        "FROM observations o JOIN source_files sf USING(source_file_pk) "
        "JOIN source_volumes sv USING(source_volume_pk) "
        "WHERE o.message_pk=? ORDER BY o.observation_pk",
        (message_pk,),
    )
    result: list[SourceObservation] = []
    for row in rows:
        metadata = json.loads(row[4])
        mount = metadata.get(METADATA_CURRENT_MOUNT_PATH)
        path = str(Path(mount) / row[0]) if mount else row[0]
        result.append(
            SourceObservation(source_path=path, source_kind=row[1], source_offset=row[2], disposition=row[3])
        )
    return result


def neighboring_dates(connection: sqlite3.Connection, message_pk: int) -> tuple[str, str]:
    row = connection.execute(
        "WITH target AS (SELECT source_file_pk,source_offset FROM observations WHERE message_pk=? "
        "ORDER BY observation_pk LIMIT 1) SELECT "
        "COALESCE((SELECT m.date_utc FROM observations o JOIN messages m USING(message_pk),target t "
        "WHERE o.source_file_pk=t.source_file_pk AND o.source_offset<t.source_offset "
        "ORDER BY o.source_offset DESC LIMIT 1),''),"
        "COALESCE((SELECT m.date_utc FROM observations o JOIN messages m USING(message_pk),target t "
        "WHERE o.source_file_pk=t.source_file_pk AND o.source_offset>t.source_offset "
        "ORDER BY o.source_offset LIMIT 1),'')",
        (message_pk,),
    ).fetchone()
    return row[0], row[1]


def source_boundary_sender(items: list[SourceObservation]) -> str:
    for item in items:
        if item.source_kind != "mbox":
            continue
        path = Path("/") / item.source_path
        if not path.is_file():
            continue
        try:
            with path.open("rb") as source:
                start = max(0, item.source_offset - 1024)
                source.seek(start)
                prefix = source.read(item.source_offset - start)
                current = source.readline()
            lines = prefix.splitlines()
            candidates = ([lines[-1]] if lines else []) + [current.rstrip(b"\r\n")]
            for line in candidates:
                if line.startswith(b"From "):
                    sender = line.split(maxsplit=2)[1].decode("ascii", "replace")
                    if sender.lower() not in {"mailer-daemon", "-"}:
                        return sender
        except OSError:
            continue
    return ""


def write_mbox(path: Path, messages: list[bytes]) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to replace {path}")
    box = mailbox.mbox(path, create=True)
    try:
        box.lock()
        for raw in messages:
            box.add(raw)
        box.flush()
    finally:
        try:
            box.unlock()
        finally:
            box.close()


def write_csv(path: Path, models: list[BaseModel]) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to replace {path}")
    if not models:
        raise ValueError(f"no records for {path}")
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=list(models[0].model_fields))
        writer.writeheader()
        for model in models:
            writer.writerow(model.model_dump())


def analyze_bad_dates(connection: sqlite3.Connection, archive: Path, output: Path) -> tuple[list[EarlyDateEvidence], Path]:
    records = selected_messages(
        connection,
        "m.category IN (?,?) AND m.date_utc<'1984-01-01T00:00:00+00:00'",
        NORMAL_CATEGORIES,
    )
    evidence: list[EarlyDateEvidence] = []
    raw_messages: list[bytes] = []
    for record in records:
        raw = raw_message(archive, record)
        raw_messages.append(raw)
        message = BytesParser(policy=policy.compat32).parsebytes(raw)
        raw_dates = header_values(message, "Date")
        parsed = normalized_date(raw_dates[0]) if raw_dates else None
        received = received_dates(message)
        plausible = [date for date in received if date.year >= MIN_REAL_YEAR]
        before, after = neighboring_dates(connection, record.message_pk)
        source_items = observations(connection, record.message_pk)
        evidence.append(EarlyDateEvidence(
            message_pk=record.message_pk, sha256=record.sha256, catalog_date=record.date_utc,
            catalog_date_source=record.date_source, category=record.category, sender=record.sender,
            subject=record.subject, raw_date=" || ".join(raw_dates),
            parsed_date=parsed.isoformat() if parsed else "",
            received_dates=" || ".join(date.isoformat() for date in received),
            plausible_received_date=plausible[0].isoformat() if plausible else "",
            previous_catalog_date=before, next_catalog_date=after,
            source_paths=" || ".join(item.source_path for item in source_items),
        ))
    path = output / "BAD_DATES.mbox"
    write_mbox(path, raw_messages)
    write_csv(output / "BAD_DATES.csv", evidence)
    return evidence, path


def candidate_sender(message: Message) -> tuple[str, list[str], list[str]]:
    from_values = header_values(message, "From")
    from_addresses = sorted({address.lower() for _, address in getaddresses(from_values) if address})
    for name in SENDER_HEADERS[1:]:
        addresses = sorted({address.lower() for _, address in getaddresses(header_values(message, name)) if address})
        if addresses:
            return name, addresses, from_addresses
    return "", [], from_addresses


def missing_kind(message: Message, from_values: list[str], from_addresses: list[str], candidate: str, boundary: str) -> str:
    if from_addresses:
        return "recoverable-from"
    if candidate:
        return "recoverable-fallback-header"
    if boundary:
        return "recoverable-source-envelope"
    if message.get("X-GM-THRID") is not None:
        return "gmail-chat-or-export"
    if from_values:
        return "invalid-from"
    return "no-from-or-fallback"


def analyze_missing_senders(connection: sqlite3.Connection, archive: Path, output: Path) -> tuple[list[MissingSenderEvidence], int, Path]:
    population = selected_messages(
        connection, "m.category IN (?,?) AND e.address=''", NORMAL_CATEGORIES,
    )
    selected = random.Random(SAMPLE_SEED).sample(population, 100)
    evidence: list[MissingSenderEvidence] = []
    raw_messages: list[bytes] = []
    for order, record in enumerate(selected, 1):
        raw = raw_message(archive, record)
        raw_messages.append(raw)
        message = BytesParser(policy=policy.compat32).parsebytes(raw)
        from_values = header_values(message, "From")
        candidate, addresses, from_addresses = candidate_sender(message)
        source_items = observations(connection, record.message_pk)
        boundary = source_boundary_sender(source_items)
        evidence.append(MissingSenderEvidence(
            sample_order=order, message_pk=record.message_pk, sha256=record.sha256,
            catalog_date=record.date_utc, date_source=record.date_source, subject=record.subject,
            content_type=message.get_content_type(), from_values=" || ".join(from_values),
            from_addresses=" || ".join(from_addresses), candidate_header=candidate,
            candidate_addresses=" || ".join(addresses), source_boundary_sender=boundary,
            source_paths=" || ".join(item.source_path for item in source_items),
            gmail_thread=message.get("X-GM-THRID") is not None,
            likely_kind=missing_kind(message, from_values, from_addresses, candidate, boundary),
        ))
    path = output / "MISSING_SENDER.mbox"
    write_mbox(path, raw_messages)
    write_csv(output / "MISSING_SENDER.csv", evidence)
    return evidence, len(population), path


def source_format(path: Path) -> Literal["babyl", "mbox", "pdf", "text", "empty", "other"]:
    with path.open("rb") as source:
        prefix = source.read(64)
    lowered = prefix.lower()
    if not prefix:
        return "empty"
    if lowered.startswith(b"babyl options:"):
        return "babyl"
    if prefix.startswith(b"From "):
        return "mbox"
    if prefix.startswith(b"%PDF-"):
        return "pdf"
    if path.suffix.lower() in {".txt", ".text"}:
        return "text"
    return "other"


def analyze_early_source(root: Path, output: Path) -> list[EarlySourceFile]:
    evidence: list[EarlySourceFile] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        kind = source_format(path)
        result = EarlySourceFile(path=str(path), size=path.stat().st_size, format=kind)
        if kind in {"babyl", "mbox"}:
            dates: list[datetime] = []
            try:
                discovered = list(source_files(path))
                if len(discovered) != 1:
                    raise ValueError(f"expected one source, found {len(discovered)}")
                for item in source_messages(discovered[0]):
                    message = BytesParser(policy=policy.compat32).parsebytes(item.raw)
                    values = header_values(message, "Date")
                    parsed = normalized_date(values[0]) if values else None
                    if parsed is not None:
                        dates.append(parsed)
                    result.message_count += 1
                if dates:
                    result.first_date = min(dates).isoformat()
                    result.last_date = max(dates).isoformat()
            except Exception as error:  # Preserve a per-file diagnosis rather than dropping it.
                result.error = f"{type(error).__name__}: {error}"
        evidence.append(result)
    write_csv(output / "EARLY_SOURCE_FILES.csv", evidence)
    return evidence


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    args = arguments()
    args.output.mkdir(parents=True, exist_ok=True)
    connection = open_catalog(args.archive)
    try:
        bad_dates, bad_path = analyze_bad_dates(connection, args.archive, args.output)
        missing, population, missing_path = analyze_missing_senders(connection, args.archive, args.output)
        early_source = analyze_early_source(args.early_source, args.output)
    finally:
        connection.close()
    years = Counter(item.catalog_date[:4] for item in bad_dates)
    kinds = Counter(item.likely_kind for item in missing)
    headers = Counter(item.candidate_header or "none" for item in missing)
    formats = Counter(item.format for item in early_source)
    summary = AnalysisSummary(
        bad_date_count=len(bad_dates), bad_date_years=dict(sorted(years.items())),
        bad_date_plausible_received=sum(bool(item.plausible_received_date) for item in bad_dates),
        missing_sender_population=population, missing_sender_sample_count=len(missing),
        missing_sender_sample_kinds=dict(sorted(kinds.items())),
        missing_sender_candidate_headers=dict(sorted(headers.items())),
        early_source_files=len(early_source), early_source_formats=dict(sorted(formats.items())),
        early_source_messages=sum(item.message_count for item in early_source),
        output_sha256={bad_path.name: file_sha256(bad_path), missing_path.name: file_sha256(missing_path)},
    )
    summary_path = args.output / "SUMMARY.json"
    if summary_path.exists():
        raise FileExistsError(f"refusing to replace {summary_path}")
    summary_path.write_text(summary.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(summary.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
