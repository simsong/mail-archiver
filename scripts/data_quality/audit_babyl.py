"""Exercise the proposed Babyl adapter against every real source message."""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from mailarchiver.message import parse_message
from mailarchiver.sources import source_files, source_messages


class BabylMessageEvidence(BaseModel):
    source_path: str
    source_offset: int
    byte_length: int
    sha256: str = ""
    date_utc: str = ""
    date_source: str = ""
    sender: str = ""
    subject: str = ""
    defects: str = ""
    error: str = ""


class BabylAuditSummary(BaseModel):
    files: int
    messages: int
    parsed: int
    errors: int
    missing_senders: int
    years: dict[str, int]
    date_sources: dict[str, int]


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = arguments()
    records: list[BabylMessageEvidence] = []
    files = 0
    for source in source_files(args.source):
        if source.kind != "babyl":
            continue
        files += 1
        prior_date: datetime | None = None
        for item in source_messages(source):
            evidence = BabylMessageEvidence(
                source_path=str(item.path), source_offset=item.source_offset, byte_length=len(item.raw)
            )
            try:
                parsed = parse_message(item.raw, item.path, prior_date)
                prior_date = datetime.fromisoformat(parsed.date_utc)
                evidence.sha256 = parsed.sha256
                evidence.date_utc = parsed.date_utc
                evidence.date_source = parsed.date_source
                evidence.sender = parsed.sender
                evidence.subject = parsed.subject
                evidence.defects = " || ".join(f"{defect.field}: {defect.detail}" for defect in parsed.defects)
            except Exception as error:  # Record every unreadable message without dropping later evidence.
                evidence.error = f"{type(error).__name__}: {error}"
            records.append(evidence)
    destination = args.output / "BABYL_MESSAGES.csv"
    if destination.exists():
        raise FileExistsError(f"refusing to replace {destination}")
    with destination.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(BabylMessageEvidence.model_fields))
        writer.writeheader()
        writer.writerows(record.model_dump() for record in records)
    parsed_records = [record for record in records if not record.error]
    summary = BabylAuditSummary(
        files=files,
        messages=len(records),
        parsed=len(parsed_records),
        errors=len(records) - len(parsed_records),
        missing_senders=sum(not record.sender for record in parsed_records),
        years=dict(sorted(Counter(record.date_utc[:4] for record in parsed_records).items())),
        date_sources=dict(sorted(Counter(record.date_source for record in parsed_records).items())),
    )
    summary_path = args.output / "BABYL_SUMMARY.json"
    if summary_path.exists():
        raise FileExistsError(f"refusing to replace {summary_path}")
    summary_path.write_text(summary.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(summary.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
