# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Compare complete Apple Mail EMLX records with an Email Collection Toolkit archive read-only."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import sqlite3
import sys
from collections import Counter
from collections.abc import Iterator
from contextlib import closing, contextmanager
from itertools import groupby
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from .layout import mbox_path
from .mbox import MboxLocation, read_verified_location
from .sources import emlx_bytes
from .standalone_verify import FIELD_NAME_PATTERN, semantic_bytes


SEMANTIC_STANDARD = "h3 semantic-message v1"
DEDUPLICATION_STANDARD = "normalized Message-ID + h2 raw-message SHA-256"
HEADER_SEPARATOR = b"\r\n\r\n"
LINE_ENDINGS = re.compile(rb"\r\n|\r|\n")
SERVICE_GMAIL = "Gmail"
SERVICE_EXCHANGE = "Microsoft Exchange (EWS)"
SERVICE_IMAP = "Other IMAP"
SERVICE_POP = "POP"
SERVICE_LOCAL = "Local"
SERVICE_UNKNOWN = "Unknown"


class HeaderChange(BaseModel):
    """Aggregate one header-name difference without disclosing header values."""

    name: str
    messages: int = Field(ge=0)
    occurrences: int = Field(ge=0)


class ServiceComparison(BaseModel):
    """One privacy-preserving provider/protocol aggregate."""

    service: str
    complete_emlx: int = 0
    partial_emlx: int = 0
    unreadable_emlx: int = 0
    compared_emlx: int = 0
    exact_raw_matches: int = 0
    semantic_only_matches: int = 0
    cache_only_messages: int = 0
    archive_semantic_matches: int = 0
    ambiguous_semantic_matches: int = 0
    header_pairs_analyzed: int = 0
    formatting_only_pairs: int = 0


class ComparisonReport(BaseModel):
    """Read-only Apple Mail/cache reconciliation results."""

    apple_mail_root: str
    archive_root: str
    comparison_identity: str = SEMANTIC_STANDARD
    deduplication_identity: str = DEDUPLICATION_STANDARD
    complete_emlx: int = 0
    partial_emlx: int = 0
    unreadable_emlx: int = 0
    compared_emlx: int = 0
    archive_messages: int = 0
    exact_raw_matches: int = 0
    semantic_only_matches: int = 0
    cache_only_messages: int = 0
    archive_semantic_matches: int = 0
    archive_only_messages: int = 0
    ambiguous_semantic_matches: int = 0
    header_pairs_analyzed: int = 0
    formatting_only_pairs: int = 0
    source_changed_during_scan: bool = False
    apple_added_headers: list[HeaderChange] = Field(default_factory=list)
    apple_missing_headers: list[HeaderChange] = Field(default_factory=list)
    changed_headers: list[HeaderChange] = Field(default_factory=list)
    services: list[ServiceComparison] = Field(default_factory=list)


class ArchiveCandidate(BaseModel):
    """One canonical archive location selected by a semantic digest."""

    model_config = ConfigDict(frozen=True)

    message_pk: int
    raw_sha256: str
    filename: str
    byte_offset: int
    byte_length: int


class CacheInventory(BaseModel):
    """Counts produced while populating the temporary comparison index."""

    complete: int = 0
    partial: int = 0
    unreadable: int = 0
    services: list[ServiceComparison] = Field(default_factory=list)


def _service_inventory(inventory: CacheInventory, service: str) -> ServiceComparison:
    for item in inventory.services:
        if item.service == service:
            return item
    item = ServiceComparison(service=service)
    inventory.services.append(item)
    return item


def _index_file_state(path: Path) -> tuple[int, int, int, int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


@contextmanager
def mail_index_snapshot(index_path: Path) -> Iterator[sqlite3.Connection]:
    """Read database/WAL bytes without asking SQLite to open the source store."""
    sources = tuple(Path(str(index_path) + suffix) for suffix in ("", "-wal", "-journal"))
    before = tuple(_index_file_state(path) for path in sources)
    with TemporaryDirectory(prefix="mailarchiver-mail-index-") as temporary:
        directory = Path(temporary)
        for source, state in zip(sources, before, strict=True):
            if state is None:
                continue
            destination = directory / source.name
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_file:
                shutil.copyfileobj(input_file, output)
        if before[0] is None or before != tuple(_index_file_state(path) for path in sources):
            raise RuntimeError("Apple Mail index changed during snapshot; quit Mail and retry")
        # Any WAL recovery or shared-memory writes happen only in this private copy.
        with closing(sqlite3.connect(directory / index_path.name)) as database:
            yield database


def _account_services(root: Path) -> dict[str, str]:
    schemes: dict[str, set[str]] = {}
    gmail_accounts: set[str] = set()
    for version in sorted(root.glob("V*")):
        if not version.is_dir():
            continue
        for account in version.iterdir():
            if not account.is_dir() or account.name == "MailData":
                continue
            markers = {child.name.casefold() for child in account.iterdir() if child.is_dir()}
            if {"[gmail].mbox", "[google mail].mbox"} & markers:
                gmail_accounts.add(account.name.casefold())
        index_path = version / "MailData" / "Envelope Index"
        if not index_path.is_file():
            continue
        with mail_index_snapshot(index_path) as index:
            for (url,) in index.execute("SELECT url FROM mailboxes"):
                parsed = urlsplit(str(url))
                account = (parsed.hostname or parsed.netloc).casefold()
                if account:
                    schemes.setdefault(account, set()).add(parsed.scheme.casefold())
    services: dict[str, str] = {}
    for account in schemes.keys() | gmail_accounts:
        account_schemes = schemes.get(account, set())
        if account in gmail_accounts:
            service = SERVICE_GMAIL
        elif "ews" in account_schemes:
            service = SERVICE_EXCHANGE
        elif "imap" in account_schemes:
            service = SERVICE_IMAP
        elif "pop" in account_schemes:
            service = SERVICE_POP
        elif "local" in account_schemes:
            service = SERVICE_LOCAL
        else:
            service = SERVICE_UNKNOWN
        services[account] = service
    return services


def _service_for_path(relative_path: Path, account_services: dict[str, str]) -> str:
    parts = relative_path.parts
    if len(parts) >= 2 and parts[0].startswith("V"):
        return account_services.get(parts[1].casefold(), SERVICE_UNKNOWN)
    return SERVICE_UNKNOWN


def _mail_state(root: Path) -> tuple[tuple[str, int, int], ...]:
    markers: list[tuple[str, int, int]] = []
    for path in sorted(root.glob("V*/MailData/Envelope Index-wal")):
        stat = path.stat()
        markers.append((str(path), stat.st_size, stat.st_mtime_ns))
    return tuple(markers)


def _walk_error(error: OSError) -> None:
    raise error


def create_cache_index(database: sqlite3.Connection) -> None:
    """Create the disposable Apple Mail comparison tables."""
    database.executescript(
        """
        CREATE TABLE cache_messages (
            cache_pk INTEGER PRIMARY KEY,
            relative_path TEXT NOT NULL UNIQUE,
            raw_sha256 TEXT NOT NULL,
            semantic_sha256 TEXT NOT NULL,
            service TEXT NOT NULL
        );
        CREATE INDEX cache_raw_sha256 ON cache_messages(raw_sha256);
        CREATE INDEX cache_semantic_sha256 ON cache_messages(semantic_sha256);
        """
    )


def index_apple_mail_cache(
    database: sqlite3.Connection,
    root: Path,
    progress_every: int,
) -> CacheInventory:
    inventory = CacheInventory()
    account_services = _account_services(root)
    inserts: list[tuple[str, str, str, str]] = []
    for directory, directories, filenames in os.walk(root, onerror=_walk_error, followlinks=False):
        directories.sort()
        for filename in sorted(filenames):
            lowered = filename.lower()
            if not lowered.endswith(".emlx"):
                continue
            path = Path(directory) / filename
            if path.is_symlink():
                continue
            relative_path = path.relative_to(root)
            service_inventory = _service_inventory(
                inventory, _service_for_path(relative_path, account_services)
            )
            if lowered.endswith(".partial.emlx"):
                inventory.partial += 1
                service_inventory.partial_emlx += 1
                continue
            inventory.complete += 1
            service_inventory.complete_emlx += 1
            try:
                raw = emlx_bytes(path)
            except (OSError, ValueError):
                inventory.unreadable += 1
                service_inventory.unreadable_emlx += 1
                continue
            inserts.append(
                (
                    relative_path.as_posix(),
                    hashlib.sha256(raw).hexdigest(),
                    hashlib.sha256(semantic_bytes(raw)).hexdigest(),
                    service_inventory.service,
                )
            )
            if len(inserts) == 1_000:
                database.executemany(
                    "INSERT INTO cache_messages(relative_path, raw_sha256, semantic_sha256, service) "
                    "VALUES (?, ?, ?, ?)",
                    inserts,
                )
                database.commit()
                inserts.clear()
            if progress_every and inventory.complete % progress_every == 0:
                print(f"indexed {inventory.complete:,} complete EMLX records", file=sys.stderr)
    if inserts:
        database.executemany(
            "INSERT INTO cache_messages(relative_path, raw_sha256, semantic_sha256, service) "
            "VALUES (?, ?, ?, ?)",
            inserts,
        )
        database.commit()
    return inventory


def _relaxed_headers(raw: bytes) -> Counter[tuple[str, bytes]]:
    normalized = LINE_ENDINGS.sub(b"\r\n", raw)
    header_block = normalized.partition(HEADER_SEPARATOR)[0]
    fields: list[tuple[str, bytes]] = []
    current_name: str | None = None
    current_value = b""
    for line in header_block.split(b"\r\n"):
        if line.startswith((b" ", b"\t")) and current_name is not None:
            current_value += b"\r\n" + line
            continue
        if current_name is not None:
            fields.append((current_name, current_value))
            current_name = None
        name, marker, value = line.partition(b":")
        if marker and FIELD_NAME_PATTERN.fullmatch(name):
            current_name = name.decode("ascii").lower()
            current_value = value
    if current_name is not None:
        fields.append((current_name, current_value))
    return Counter(
        (name, re.sub(rb"[ \t]+", b" ", re.sub(rb"\r\n[ \t]+", b" ", value)).strip(b" \t"))
        for name, value in fields
    )


def _header_delta(
    apple: Counter[tuple[str, bytes]],
    archived: Counter[tuple[str, bytes]],
) -> tuple[Counter[str], Counter[str], Counter[str]]:
    added_pairs, missing_pairs = apple - archived, archived - apple
    added = Counter[str]()
    missing = Counter[str]()
    for (name, _value), count in added_pairs.items():
        added[name] += count
    for (name, _value), count in missing_pairs.items():
        missing[name] += count
    changed = Counter[str]()
    for name in added.keys() & missing.keys():
        changed[name] = min(added[name], missing[name])
        added[name] -= changed[name]
        missing[name] -= changed[name]
    return +added, +missing, +changed


def _changes(occurrences: Counter[str], messages: Counter[str]) -> list[HeaderChange]:
    return [
        HeaderChange(name=name, occurrences=count, messages=messages[name])
        for name, count in sorted(occurrences.items(), key=lambda item: (-item[1], item[0]))
    ]


def _scalar(
    database: sqlite3.Connection,
    statement: str,
    parameters: tuple[object, ...] = (),
) -> int:
    row = database.execute(statement, parameters).fetchone()
    if row is None:
        raise RuntimeError("aggregate query returned no row")
    return int(row[0])


def _service_comparisons(
    database: sqlite3.Connection,
    inventory: CacheInventory,
    analyzed: Counter[str],
    formatting: Counter[str],
) -> list[ServiceComparison]:
    results: list[ServiceComparison] = []
    for item in sorted(inventory.services, key=lambda value: value.service):
        service = item.service
        compared = _scalar(
            database, "SELECT count(*) FROM cache_messages WHERE service = ?", (service,)
        )
        exact = _scalar(
            database,
            "SELECT count(*) FROM cache_messages AS c WHERE c.service = ? AND EXISTS "
            "(SELECT 1 FROM archive.messages AS m WHERE m.sha256 = c.raw_sha256)",
            (service,),
        )
        semantic_only = _scalar(
            database,
            "SELECT count(*) FROM cache_messages AS c WHERE c.service = ? AND NOT EXISTS "
            "(SELECT 1 FROM archive.messages AS m WHERE m.sha256 = c.raw_sha256) AND EXISTS "
            "(SELECT 1 FROM archive.observations AS o WHERE o.message_pk IS NOT NULL "
            "AND o.semantic_sha256 = c.semantic_sha256)",
            (service,),
        )
        archive_matches = _scalar(
            database,
            "SELECT count(DISTINCT o.message_pk) FROM archive.observations AS o "
            "JOIN cache_messages AS c ON c.semantic_sha256 = o.semantic_sha256 "
            "WHERE o.message_pk IS NOT NULL AND c.service = ?",
            (service,),
        )
        ambiguous = _scalar(
            database,
            "SELECT count(*) FROM (SELECT c.cache_pk FROM cache_messages AS c "
            "JOIN archive.observations AS o ON o.semantic_sha256 = c.semantic_sha256 "
            "WHERE o.message_pk IS NOT NULL AND c.service = ? GROUP BY c.cache_pk "
            "HAVING count(DISTINCT o.message_pk) > 1)",
            (service,),
        )
        results.append(
            ServiceComparison(
                service=service,
                complete_emlx=item.complete_emlx,
                partial_emlx=item.partial_emlx,
                unreadable_emlx=item.unreadable_emlx,
                compared_emlx=compared,
                exact_raw_matches=exact,
                semantic_only_matches=semantic_only,
                cache_only_messages=compared - exact - semantic_only,
                archive_semantic_matches=archive_matches,
                ambiguous_semantic_matches=ambiguous,
                header_pairs_analyzed=analyzed[service],
                formatting_only_pairs=formatting[service],
            )
        )
    return results


def _candidate(row: tuple[int, str, str, str, int, str, str, int, int, str]) -> ArchiveCandidate:
    return ArchiveCandidate(
        message_pk=int(row[4]),
        raw_sha256=str(row[5]),
        filename=str(row[6]),
        byte_offset=int(row[7]),
        byte_length=int(row[8]),
    )


def _analyze_headers(
    database: sqlite3.Connection,
    apple_root: Path,
    archive_root: Path,
) -> tuple[
    int,
    int,
    list[HeaderChange],
    list[HeaderChange],
    list[HeaderChange],
    Counter[str],
    Counter[str],
]:
    statement = """
        SELECT c.cache_pk, c.relative_path, c.raw_sha256, c.semantic_sha256,
               m.message_pk, m.sha256, g.filename, l.byte_offset, l.byte_length,
               c.service
        FROM cache_messages AS c
        JOIN archive.observations AS o ON o.semantic_sha256 = c.semantic_sha256
        JOIN archive.messages AS m ON m.message_pk = o.message_pk
        JOIN archive.locations AS l ON l.message_pk = m.message_pk
        JOIN archive.mbox_generations AS g ON g.generation_pk = l.generation_pk
        WHERE NOT EXISTS (
            SELECT 1 FROM archive.messages AS exact WHERE exact.sha256 = c.raw_sha256
        )
        GROUP BY c.cache_pk, m.message_pk
        ORDER BY c.cache_pk, m.message_pk
    """
    added_occurrences = Counter[str]()
    missing_occurrences = Counter[str]()
    changed_occurrences = Counter[str]()
    added_messages = Counter[str]()
    missing_messages = Counter[str]()
    changed_messages = Counter[str]()
    analyzed_services = Counter[str]()
    formatting_services = Counter[str]()
    analyzed = formatting_only = 0
    for _cache_pk, rows_iter in groupby(database.execute(statement), key=lambda row: int(row[0])):
        rows = list(rows_iter)
        relative_path = str(rows[0][1])
        apple_raw = emlx_bytes(apple_root / relative_path)
        apple_headers = _relaxed_headers(apple_raw)
        best: tuple[int, int, Counter[str], Counter[str], Counter[str]] | None = None
        best_key: tuple[int, int] | None = None
        for row in rows:
            candidate = _candidate(row)
            archived_raw = read_verified_location(
                mbox_path(archive_root, candidate.filename),
                MboxLocation(byte_offset=candidate.byte_offset, byte_length=candidate.byte_length),
                candidate.raw_sha256,
            )
            if hashlib.sha256(semantic_bytes(archived_raw)).hexdigest() != str(row[3]):
                raise RuntimeError(f"stale semantic digest for archive message {candidate.message_pk}")
            added, missing, changed = _header_delta(apple_headers, _relaxed_headers(archived_raw))
            score = sum(added.values()) + sum(missing.values()) + sum(changed.values())
            choice = (score, candidate.message_pk, added, missing, changed)
            if best_key is None or choice[:2] < best_key:
                best = choice
                best_key = choice[:2]
        if best is None:
            continue
        analyzed += 1
        service = str(rows[0][9])
        analyzed_services[service] += 1
        _score, _message_pk, added, missing, changed = best
        if not added and not missing and not changed:
            formatting_only += 1
            formatting_services[service] += 1
        for source, occurrence_target, message_target in (
            (added, added_occurrences, added_messages),
            (missing, missing_occurrences, missing_messages),
            (changed, changed_occurrences, changed_messages),
        ):
            occurrence_target.update(source)
            message_target.update(source.keys())
    return (
        analyzed,
        formatting_only,
        _changes(added_occurrences, added_messages),
        _changes(missing_occurrences, missing_messages),
        _changes(changed_occurrences, changed_messages),
        analyzed_services,
        formatting_services,
    )


def compare_apple_mail(
    apple_mail_root: Path,
    archive_root: Path,
    *,
    progress_every: int = 10_000,
) -> ComparisonReport:
    """Return a content-private, read-only comparison of two mail stores."""
    apple_mail_root = apple_mail_root.expanduser().resolve()
    archive_root = archive_root.expanduser().resolve()
    catalog_path = archive_root / "archive.sqlite3"
    if not apple_mail_root.is_dir():
        raise FileNotFoundError(f"Apple Mail directory not found: {apple_mail_root}")
    if not catalog_path.is_file():
        raise FileNotFoundError(f"archive catalog not found: {catalog_path}")
    before = _mail_state(apple_mail_root)
    with TemporaryDirectory(prefix="mailarchiver-apple-compare-") as temporary:
        database = sqlite3.connect(Path(temporary) / "comparison.sqlite3", uri=True)
        try:
            create_cache_index(database)
            inventory = index_apple_mail_cache(database, apple_mail_root, progress_every)
            database.execute(
                "ATTACH DATABASE ? AS archive",
                (catalog_path.as_uri() + "?mode=ro",),
            )
            if database.execute("SELECT version FROM archive.schema_info").fetchall() != [(1,)]:
                raise RuntimeError("unsupported archive catalog schema")
            archive_messages = _scalar(database, "SELECT count(*) FROM archive.messages")
            exact = _scalar(
                database,
                "SELECT count(*) FROM cache_messages AS c WHERE EXISTS "
                "(SELECT 1 FROM archive.messages AS m WHERE m.sha256 = c.raw_sha256)",
            )
            semantic_only = _scalar(
                database,
                "SELECT count(*) FROM cache_messages AS c WHERE NOT EXISTS "
                "(SELECT 1 FROM archive.messages AS m WHERE m.sha256 = c.raw_sha256) AND EXISTS "
                "(SELECT 1 FROM archive.observations AS o WHERE o.message_pk IS NOT NULL "
                "AND o.semantic_sha256 = c.semantic_sha256)",
            )
            compared = _scalar(database, "SELECT count(*) FROM cache_messages")
            archive_matches = _scalar(
                database,
                "SELECT count(DISTINCT o.message_pk) FROM archive.observations AS o "
                "JOIN cache_messages AS c ON c.semantic_sha256 = o.semantic_sha256 "
                "WHERE o.message_pk IS NOT NULL",
            )
            ambiguous = _scalar(
                database,
                "SELECT count(*) FROM (SELECT c.cache_pk FROM cache_messages AS c "
                "JOIN archive.observations AS o ON o.semantic_sha256 = c.semantic_sha256 "
                "WHERE o.message_pk IS NOT NULL GROUP BY c.cache_pk "
                "HAVING count(DISTINCT o.message_pk) > 1)",
            )
            (
                analyzed,
                formatting,
                added,
                missing,
                changed,
                analyzed_services,
                formatting_services,
            ) = _analyze_headers(database, apple_mail_root, archive_root)
            services = _service_comparisons(
                database, inventory, analyzed_services, formatting_services
            )
        finally:
            database.close()
    after = _mail_state(apple_mail_root)
    return ComparisonReport(
        apple_mail_root=str(apple_mail_root),
        archive_root=str(archive_root),
        complete_emlx=inventory.complete,
        partial_emlx=inventory.partial,
        unreadable_emlx=inventory.unreadable,
        compared_emlx=compared,
        archive_messages=archive_messages,
        exact_raw_matches=exact,
        semantic_only_matches=semantic_only,
        cache_only_messages=compared - exact - semantic_only,
        archive_semantic_matches=archive_matches,
        archive_only_messages=archive_messages - archive_matches,
        ambiguous_semantic_matches=ambiguous,
        header_pairs_analyzed=analyzed,
        formatting_only_pairs=formatting,
        source_changed_during_scan=before != after,
        apple_added_headers=added,
        apple_missing_headers=missing,
        changed_headers=changed,
        services=services,
    )


def _print_changes(label: str, changes: list[HeaderChange]) -> None:
    print(f"{label}:")
    if not changes:
        print("  (none)")
        return
    for change in changes:
        print(f"  {change.name}: {change.occurrences:,} occurrences in {change.messages:,} messages")


def print_report(report: ComparisonReport) -> None:
    """Print a content-private human-readable report."""
    print(f"Comparison identity: {report.comparison_identity}")
    print(f"Archive deduplication identity: {report.deduplication_identity}")
    print(f"Complete EMLX: {report.complete_emlx:,}")
    print(f"Partial EMLX excluded: {report.partial_emlx:,}")
    print(f"Unreadable complete EMLX: {report.unreadable_emlx:,}")
    print(f"Archive messages: {report.archive_messages:,}")
    print(f"Exact raw matches: {report.exact_raw_matches:,}")
    print(f"Semantic-only matches: {report.semantic_only_matches:,}")
    print(f"Cache-only complete messages: {report.cache_only_messages:,}")
    print(f"Archive messages represented in cache: {report.archive_semantic_matches:,}")
    print(f"Archive-only messages: {report.archive_only_messages:,}")
    print(f"Ambiguous semantic matches: {report.ambiguous_semantic_matches:,}")
    print(f"Header pairs analyzed: {report.header_pairs_analyzed:,}")
    print(f"Formatting-only raw differences: {report.formatting_only_pairs:,}")
    print(f"Apple Mail changed during scan: {'yes' if report.source_changed_during_scan else 'no'}")
    _print_changes("Headers present only in Apple Mail", report.apple_added_headers)
    _print_changes("Headers absent from Apple Mail", report.apple_missing_headers)
    _print_changes("Headers with changed normalized values", report.changed_headers)
    print("By mail service:")
    print(
        "  Service | Complete | Partial | Exact | Semantic-only | Cache-only | "
        "Archive represented | Ambiguous | Formatting-only"
    )
    for service in report.services:
        print(
            f"  {service.service} | {service.complete_emlx:,} | {service.partial_emlx:,} | "
            f"{service.exact_raw_matches:,} | {service.semantic_only_matches:,} | "
            f"{service.cache_only_messages:,} | {service.archive_semantic_matches:,} | "
            f"{service.ambiguous_semantic_matches:,} | {service.formatting_only_pairs:,}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apple-mail", type=Path, default=Path.home() / "Library/Mail")
    parser.add_argument("--archive", type=Path, default=Path.home() / "mail-archive")
    parser.add_argument("--progress-every", type=int, default=10_000)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.progress_every < 0:
        parser.error("--progress-every must be nonnegative")
    try:
        report = compare_apple_mail(args.apple_mail, args.archive, progress_every=args.progress_every)
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
        parser.exit(1, f"comparison failed: {error}\n")
    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
