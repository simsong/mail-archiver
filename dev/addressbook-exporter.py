#!/usr/bin/env python3
# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Export referenced catalog addresses as conservative ePADD 11.1.3 contacts.

Run through ``make addressbook-export ARGS='--help'``. No display-name or
person-alias inference is performed. Output replaces an entire ePADD address
book; import into a copy first. Source archives are always opened read-only.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import os
import sqlite3
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field


class ExportOptions(BaseModel):
    archive: Path
    output: Path
    owner: list[str] = Field(default_factory=list)
    owners_from_sent: bool = False
    owner_name: str = "Archive owner"


class Rejection(BaseModel):
    address: str
    reason: str


class ExportReport(BaseModel):
    archive: str
    catalog_sha256: str
    message_count: int = 0
    referenced_catalog_addresses: int = 0
    exported_addresses: int = 0
    contacts: int = 0
    addresses_without_at: int = 0
    normalized_duplicates: int = 0
    owner_addresses: list[str] = Field(default_factory=list)
    owner_selection: str = "Explicit --owner addresses"
    ungrouped_non_at_owner_addresses: list[str] = Field(default_factory=list)
    rejected: list[Rejection] = Field(default_factory=list)
    output_sha256: str = ""
    policy: str = "One contact per lowercase address, except the selected owner addresses."
    limitations: str = (
        "Catalog-derived, not a fresh MIME audit. No display names, reviewed person aliases, "
        "or mailing-list classifications are exported. No ePADD import has been performed. "
        "UTF-8 requires an ePADD JVM configured to read UTF-8."
    )


def sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def line_error(value: str) -> str:
    """Reject values that ePADD cannot safely preserve as one contact line."""
    if not value:
        return "empty address"
    if value.startswith("--"):
        return "contact delimiter prefix"
    if not value.isprintable():
        return "control or non-printing character"
    if html.unescape(value) != value:
        return "HTML entity would change during ePADD save/reload"
    if "@" not in value and html.escape(value) != value:
        return "Non-@ identifier is double-escaped by ePADD's address fallback"
    if len(value.encode("utf-16-le")) // 2 > 900:
        return "exceeds conservative ePADD mark/reset line limit"
    return ""


def require_stable_catalog(catalog: Path) -> None:
    if not catalog.is_file():
        raise ValueError(f"Missing catalog: {catalog}")
    for suffix in ("-wal", "-journal"):
        if Path(str(catalog) + suffix).exists():
            raise ValueError("Catalog has a journal/WAL; finish archive writes before exporting")


def populate(source: sqlite3.Connection, staging: sqlite3.Connection, report: ExportReport) -> None:
    staging.execute("CREATE TABLE addresses(address TEXT PRIMARY KEY)")
    report.message_count = source.execute("SELECT count(*) FROM messages").fetchone()[0]
    rows = source.execute(
        "SELECT address FROM email_addresses e WHERE "
        "EXISTS (SELECT 1 FROM messages m WHERE m.sender_address_pk=e.address_pk) OR "
        "EXISTS (SELECT 1 FROM recipients r JOIN messages m USING(message_pk) "
        "WHERE r.address_pk=e.address_pk) ORDER BY e.address_pk"
    )
    for (original,) in rows:
        report.referenced_catalog_addresses += 1
        address = original.strip().lower()
        reason = line_error(address)
        if reason:
            report.rejected.append(Rejection(address=original, reason=reason))
            continue
        cursor = staging.execute("INSERT OR IGNORE INTO addresses VALUES (?)", (address,))
        report.normalized_duplicates += int(cursor.rowcount == 0)
    staging.commit()


def select_owners(options: ExportOptions, source: sqlite3.Connection, report: ExportReport) -> list[str]:
    owners = set(address.strip().lower() for address in options.owner)
    if any(line_error(address) or "@" not in address for address in owners):
        raise ValueError("Explicit owners must be exact email addresses, not name fragments")
    if options.owners_from_sent:
        report.owner_selection = "Distinct catalog sender addresses for category=Sent, plus explicit --owner values"
        rows = source.execute(
            "SELECT DISTINCT e.address FROM messages m JOIN email_addresses e "
            "ON e.address_pk=m.sender_address_pk WHERE m.category='Sent' ORDER BY e.address"
        )
        for (original,) in rows:
            address = original.strip().lower()
            if line_error(address):
                continue  # Already recorded as a rejection while populating the address set.
            if "@" in address:
                owners.add(address)
            else:
                report.ungrouped_non_at_owner_addresses.append(address)
    if not owners:
        raise ValueError("No owner email addresses: supply --owners-from-sent or --owner")
    report.owner_addresses = sorted(owners)
    return report.owner_addresses


def export_addressbook(options: ExportOptions) -> ExportReport:
    archive = options.archive.expanduser().resolve(strict=True)
    output = options.output.expanduser().resolve()
    report_path = output.with_suffix(output.suffix + ".report.json")
    if output.is_relative_to(archive) or report_path.is_relative_to(archive):
        raise ValueError("Export and report must be outside the source archive")
    if output.exists() or report_path.exists():
        raise ValueError("Refusing to replace an existing export or report")
    if line_error(options.owner_name) or "@" in options.owner_name:
        raise ValueError("Owner name must be a safe non-address display line")
    catalog = archive / "archive.sqlite3"
    require_stable_catalog(catalog)
    digest = sha256_file(catalog)
    report = ExportReport(archive=str(archive), catalog_sha256=digest)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="addressbook-", dir=output.parent) as temporary:
        work = Path(temporary)
        # Immutable mode cannot create SQLite sidecars in the source archive.
        # Journals are refused and the catalog hash is rechecked before publishing.
        source = sqlite3.connect(catalog.as_uri() + "?mode=ro&immutable=1", uri=True)
        staging = sqlite3.connect(work / "addresses.sqlite3")
        try:
            source.execute("PRAGMA query_only=ON")
            populate(source, staging, report)
            owners = select_owners(options, source, report)
            for address in owners:
                if not staging.execute("SELECT 1 FROM addresses WHERE address=?", (address,)).fetchone():
                    raise ValueError(f"Owner address is not referenced in this archive: {address}")
            candidate = work / "addressbook.txt"
            with candidate.open("x", encoding="utf-8", newline="\n") as handle:
                os.chmod(candidate, 0o600)
                handle.write("-- Archive owner\n" + options.owner_name + "\n")
                for address in owners:
                    handle.write(address + "\n")
                report.contacts = 1
                for (address,) in staging.execute("SELECT address FROM addresses ORDER BY address"):
                    report.exported_addresses += 1
                    report.addresses_without_at += int("@" not in address)
                    if address not in owners:
                        handle.write("--\n" + address + "\n")
                        report.contacts += 1
                handle.flush()
                os.fsync(handle.fileno())
            require_stable_catalog(catalog)
            if sha256_file(catalog) != digest:
                raise ValueError("Source catalog changed during export; output not published")
            report.output_sha256 = sha256_file(candidate)
            report_candidate = work / "report.json"
            report_candidate.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
            os.chmod(report_candidate, 0o600)
            # Hard-link publication refuses replacement, including a concurrent export.
            os.link(report_candidate, report_path)
            try:
                os.link(candidate, output)
            except OSError:
                report_path.unlink()
                raise
        finally:
            staging.close()
            source.close()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--owner", action="append", default=[], help="Exact owner address; repeat for aliases")
    parser.add_argument("--owners-from-sent", action="store_true", help="Group distinct Sent senders as the archive owner")
    parser.add_argument("--owner-name", default="Archive owner")
    args = parser.parse_args()
    options = ExportOptions(
        archive=args.archive, output=args.output, owner=args.owner,
        owners_from_sent=args.owners_from_sent, owner_name=args.owner_name,
    )
    report = export_addressbook(options)
    print(f"Exported {report.exported_addresses:,} addresses in {report.contacts:,} contacts.")
    print(f"Addresses without @: {report.addresses_without_at:,}; rejected: {len(report.rejected):,}.")
    print(f"Output: {options.output.expanduser().resolve()}")
    print(f"SHA-256: {report.output_sha256}")


if __name__ == "__main__":
    main()
