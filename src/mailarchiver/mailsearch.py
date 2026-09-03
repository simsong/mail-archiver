"""Search catalog and FTS data read-only, then retrieve hash-verified MBOX bytes."""

from __future__ import annotations

import argparse
import hashlib
import shlex
import shutil
import sqlite3
import sys
import textwrap
from datetime import date as CalendarDate, timedelta
from email import policy
from email.message import Message
from email.parser import BytesParser
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from .archive_path import add_archive_argument, require_archive
from .layout import mbox_path
from .mailbox_tree import MailboxSelection
from .message import decoded_header
from .mbox import MboxLocation, read_verified_location
from .search import SEARCH_CATEGORIES, decoded_part, html_text, is_attachment

DEFAULT_LIMIT = 10
RECENT_FTS_SCAN_LIMIT = 10_000
BOLD = "\033[1m"
RESET = "\033[0m"


class SearchTerms(BaseModel):
    """Structured selectors and free-text terms accepted by mailsearch."""

    any_address: list[str] = Field(default_factory=list)
    to: list[str] = Field(default_factory=list)
    from_: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    subject: list[str] = Field(default_factory=list)
    date: list[CalendarDate] = Field(default_factory=list)
    before: list[CalendarDate] = Field(default_factory=list)
    after: list[CalendarDate] = Field(default_factory=list)
    text: list[str] = Field(default_factory=list)


class MessageHeader(BaseModel):
    message_pk: int
    recipients: str
    sender: str
    subject: str
    date_utc: str
    attachment_count: int = 0


class SearchStatement(BaseModel):
    sql: str
    parameters: list[str | int]


class SearchHeaderPage(BaseModel):
    results: list[MessageHeader]
    older_results_unchecked: bool = False


class SortField(StrEnum):
    DATE = "date"
    SUBJECT = "subject"
    SENDER = "sender"


class SortDirection(StrEnum):
    ASCENDING = "ascending"
    DESCENDING = "descending"


def parse_terms(tokens: list[str]) -> SearchTerms:
    terms = SearchTerms()
    for token in tokens:
        key, separator, value = token.partition(":")
        key = key.lower()
        if separator and key in {"any", "to", "from", "cc", "bcc", "subject", "date", "before", "after"}:
            if not value:
                raise ValueError(f"{key}: requires a value")
            if key in {"date", "before", "after"}:
                try:
                    getattr(terms, key).append(CalendarDate.fromisoformat(value))
                except ValueError as error:
                    raise ValueError(f"{key}: requires a YYYY-MM-DD date") from error
            else:
                field = {"any": "any_address", "from": "from_"}.get(key, key)
                getattr(terms, field).append(value.casefold())
        else:
            terms.text.append(token)
    return terms


def fts_query(words: list[str]) -> str:
    return " AND ".join(f'"{word.replace(chr(34), "")}"' for word in words)


def contains(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def parse_query(query: str) -> SearchTerms:
    """Parse one GUI-style search field with shell-compatible quoting."""
    try:
        return parse_terms(shlex.split(query))
    except ValueError as error:
        if "No closing quotation" in str(error):
            raise ValueError("search has an unclosed quote") from error
        raise


def _search_predicate(
    terms: SearchTerms,
    search_attachments: bool = False,
    mailbox_selections: list[MailboxSelection] | None = None,
    include_text: bool = True,
) -> SearchStatement:
    clauses = ["m.category IN (?, ?)"]
    parameters: list[str | int] = list(SEARCH_CATEGORIES)
    for address in terms.any_address:
        pattern = contains(address)
        clauses.append(
            "(lower(sender.address) LIKE ? ESCAPE '\\' OR EXISTS (SELECT 1 FROM recipients r "
            "JOIN email_addresses a ON a.address_pk = r.address_pk WHERE r.message_pk = m.message_pk "
            "AND lower(a.address) LIKE ? ESCAPE '\\'))"
        )
        parameters.extend((pattern, pattern))
    for role, addresses in (("to", terms.to), ("cc", terms.cc), ("bcc", terms.bcc)):
        for address in addresses:
            clauses.append(
                "EXISTS (SELECT 1 FROM recipients r INDEXED BY recipients_message_role_address "
                "JOIN email_addresses a ON a.address_pk = r.address_pk "
                "WHERE r.message_pk = m.message_pk AND r.role = ? AND lower(a.address) LIKE ? ESCAPE '\\')"
            )
            parameters.extend((role, contains(address)))
    for address in terms.from_:
        clauses.append("lower(sender.address) LIKE ? ESCAPE '\\'")
        parameters.append(contains(address))
    for subject in terms.subject:
        clauses.append("lower(m.subject) LIKE ? ESCAPE '\\'")
        parameters.append(contains(subject))
    for selected_date in terms.date:
        clauses.append("m.date_utc >= ? AND m.date_utc < ?")
        parameters.extend((selected_date.isoformat() + "T00:00:00+00:00", (selected_date + timedelta(days=1)).isoformat() + "T00:00:00+00:00"))
    for selected_date in terms.before:
        clauses.append("m.date_utc < ?")
        parameters.append(selected_date.isoformat() + "T00:00:00+00:00")
    for selected_date in terms.after:
        clauses.append("m.date_utc >= ?")
        parameters.append((selected_date + timedelta(days=1)).isoformat() + "T00:00:00+00:00")
    if terms.text and include_text:
        if search_attachments:
            for term in terms.text:
                clauses.append(
                    "m.sha256 IN (SELECT sha256 FROM search.message_fts WHERE message_fts MATCH ? "
                    "UNION SELECT sha256 FROM search.attachment_fts WHERE attachment_fts MATCH ?)"
                )
                match = fts_query([term])
                parameters.extend((match, match))
        else:
            clauses.append("m.sha256 IN (SELECT sha256 FROM search.message_fts WHERE message_fts MATCH ?)")
            parameters.append(fts_query(terms.text))
    if mailbox_selections:
        alternatives = []
        for selection in mailbox_selections:
            selected = []
            if selection.volume_identity is not None:
                selected.append("source_volumes.identity_json = ?")
                parameters.append(selection.volume_identity)
            if selection.path:
                selected.append(
                    "(source_files.hierarchy_path = ? OR "
                    "(source_files.hierarchy_path >= ? AND source_files.hierarchy_path < ?))"
                )
                parameters.extend((selection.path, selection.path + "/", selection.path + "0"))
            alternatives.append("(" + (" AND ".join(selected) if selected else "1") + ")")
        clauses.append(
            "EXISTS (SELECT 1 FROM observations INDEXED BY observations_message_pk "
            "JOIN source_files USING (source_file_pk) JOIN source_volumes USING (source_volume_pk) "
            "WHERE observations.message_pk = m.message_pk AND (" + " OR ".join(alternatives) + "))"
        )
    return SearchStatement(sql=" AND ".join(clauses), parameters=parameters)


def _search_statement(
    terms: SearchTerms,
    limit: int,
    offset: int = 0,
    sort_by: SortField = SortField.DATE,
    direction: SortDirection = SortDirection.DESCENDING,
    search_attachments: bool = False,
    mailbox_selections: list[MailboxSelection] | None = None,
    newest_page: bool = False,
    ordered_text_prefix: bool = False,
) -> SearchStatement:
    direct_catalog_scan = bool(ordered_text_prefix and terms.text and not search_attachments)
    predicate = _search_predicate(
        terms, search_attachments, mailbox_selections, include_text=not direct_catalog_scan
    )
    if direct_catalog_scan:
        predicate.sql += (
            " AND EXISTS (SELECT 1 FROM search.message_metadata matching_metadata "
            "JOIN search.message_fts matching_fts "
            "ON matching_fts.rowid = matching_metadata.message_fts_rowid "
            "WHERE matching_metadata.sha256 = m.sha256 AND matching_fts.content MATCH ?)"
        )
        predicate.parameters.append(fts_query(terms.text))
    parameters = predicate.parameters.copy()
    candidate_sort = SortField.DATE if newest_page else sort_by
    candidate_direction = SortDirection.DESCENDING if newest_page else direction
    candidate_order = {
        SortField.DATE: "m.date_utc",
        SortField.SUBJECT: "lower(m.subject)",
        SortField.SENDER: "lower(sender.address)",
    }[candidate_sort]
    result_order = {
        SortField.DATE: "c.date_utc",
        SortField.SUBJECT: "lower(c.subject)",
        SortField.SENDER: "lower(c.sender)",
    }[sort_by]
    candidate_order_direction = "ASC" if candidate_direction == SortDirection.ASCENDING else "DESC"
    result_order_direction = "ASC" if direction == SortDirection.ASCENDING else "DESC"
    limit_clause = "LIMIT ? OFFSET ? " if limit else ("LIMIT -1 OFFSET ? " if offset else "")
    if limit:
        parameters.extend((limit, offset))
    elif offset:
        parameters.append(offset)
    candidate_source = (
        "messages m INDEXED BY messages_sha256 "
        "JOIN email_addresses sender ON sender.address_pk = m.sender_address_pk "
    )
    if not terms.text or direct_catalog_scan:
        if sort_by is SortField.DATE:
            candidate_source = (
                "messages m INDEXED BY messages_date_message "
                "JOIN email_addresses sender ON sender.address_pk = m.sender_address_pk "
            )
        elif sort_by is SortField.SUBJECT:
            candidate_source = (
                "messages m INDEXED BY messages_subject_message "
                "JOIN email_addresses sender ON sender.address_pk = m.sender_address_pk "
            )
        else:
            candidate_source = (
                "email_addresses sender INDEXED BY email_addresses_lower_address "
                "CROSS JOIN messages m INDEXED BY messages_sender_address_pk "
                "ON m.sender_address_pk = sender.address_pk "
            )
    sql = (
        "WITH candidates AS MATERIALIZED ("
        "SELECT m.message_pk, m.sha256, sender.address AS sender, m.subject, m.date_utc "
        f"FROM {candidate_source}"
        f"WHERE {predicate.sql} "
        f"ORDER BY {candidate_order} {candidate_order_direction}, "
        f"m.message_pk {candidate_order_direction} {limit_clause}"
        ") SELECT c.message_pk, COALESCE(group_concat(DISTINCT recipient.address), ''), c.sender, c.subject, c.date_utc, "
        "COALESCE(metadata.attachment_count, 0) FROM candidates c "
        "LEFT JOIN search.message_metadata metadata ON metadata.sha256 = c.sha256 "
        "LEFT JOIN recipients r ON r.message_pk = c.message_pk "
        "LEFT JOIN email_addresses recipient ON recipient.address_pk = r.address_pk "
        "GROUP BY c.message_pk "
        f"ORDER BY {result_order} {result_order_direction}, c.message_pk {result_order_direction}"
    )
    return SearchStatement(sql=sql, parameters=parameters)


def _count_statement(
    terms: SearchTerms,
    search_attachments: bool = False,
    mailbox_selections: list[MailboxSelection] | None = None,
    maximum: int | None = None,
) -> SearchStatement:
    """Count matches, optionally stopping after a caller-supplied threshold."""
    direct_fts = bool(terms.text and not search_attachments)
    predicate = _search_predicate(
        terms, search_attachments, mailbox_selections, include_text=not direct_fts
    )
    if direct_fts:
        source = (
            "search.message_fts matched "
            "JOIN search.message_metadata metadata ON metadata.message_fts_rowid = matched.rowid "
            "JOIN messages m INDEXED BY messages_sha256 ON m.sha256 = metadata.sha256"
        )
        where = f"matched.content MATCH ? AND {predicate.sql}"
        parameters = [fts_query(terms.text), *predicate.parameters]
    else:
        source = "messages m"
        where = predicate.sql
        parameters = predicate.parameters.copy()
    limit = " LIMIT ?" if maximum is not None else ""
    if maximum is not None:
        parameters.append(maximum)
    return SearchStatement(
        sql=(
            "SELECT count(*) FROM (SELECT 1 "
            f"FROM {source} "
            "JOIN email_addresses sender ON sender.address_pk = m.sender_address_pk "
            f"WHERE {where}{limit})"
        ),
        parameters=parameters,
    )


def _recent_text_statement(
    terms: SearchTerms,
    limit: int,
    offset: int,
    sort_by: SortField = SortField.DATE,
    direction: SortDirection = SortDirection.DESCENDING,
) -> SearchStatement:
    """Search a bounded newest-first catalog window by indexed FTS row ID."""
    result_order = {
        SortField.DATE: "c.date_utc",
        SortField.SUBJECT: "lower(c.subject)",
        SortField.SENDER: "lower(c.sender)",
    }[sort_by]
    result_direction = "ASC" if direction is SortDirection.ASCENDING else "DESC"
    sql = (
        "WITH recent AS NOT MATERIALIZED ("
        "SELECT m.message_pk, m.sha256, sender.address AS sender, m.subject, m.date_utc "
        "FROM messages m INDEXED BY messages_date_message "
        "JOIN email_addresses sender ON sender.address_pk = m.sender_address_pk "
        "WHERE m.category IN (?, ?) "
        "ORDER BY m.date_utc DESC, m.message_pk DESC LIMIT ?), "
        "candidates AS MATERIALIZED ("
        "SELECT m.message_pk, m.sha256, m.sender, m.subject, m.date_utc FROM recent m "
        "WHERE EXISTS (SELECT 1 FROM search.message_metadata metadata "
        "JOIN search.message_fts ON message_fts.rowid = metadata.message_fts_rowid "
        "WHERE metadata.sha256 = m.sha256 AND message_fts MATCH ?) "
        "ORDER BY m.date_utc DESC, m.message_pk DESC LIMIT ? OFFSET ?) "
        "SELECT c.message_pk, COALESCE(group_concat(DISTINCT recipient.address), ''), "
        "c.sender, c.subject, c.date_utc, COALESCE(metadata.attachment_count, 0) "
        "FROM candidates c "
        "LEFT JOIN search.message_metadata metadata ON metadata.sha256 = c.sha256 "
        "LEFT JOIN recipients r ON r.message_pk = c.message_pk "
        "LEFT JOIN email_addresses recipient ON recipient.address_pk = r.address_pk "
        "GROUP BY c.message_pk "
        f"ORDER BY {result_order} {result_direction}, c.message_pk {result_direction}"
    )
    return SearchStatement(
        sql=sql,
        parameters=[*SEARCH_CATEGORIES, RECENT_FTS_SCAN_LIMIT, fts_query(terms.text), limit, offset],
    )


def _is_plain_text_search(
    terms: SearchTerms,
    sort_by: SortField,
    direction: SortDirection,
    search_attachments: bool,
    mailbox_selections: list[MailboxSelection] | None,
) -> bool:
    structured = (
        terms.any_address,
        terms.to,
        terms.from_,
        terms.cc,
        terms.bcc,
        terms.subject,
        terms.date,
        terms.before,
        terms.after,
    )
    return bool(
        terms.text
        and (sort_by is not SortField.DATE or direction is SortDirection.DESCENDING)
        and not search_attachments
        and not mailbox_selections
        and not any(structured)
    )


def _is_plain_recent_text_search(
    terms: SearchTerms,
    limit: int,
    sort_by: SortField,
    direction: SortDirection,
    search_attachments: bool,
    mailbox_selections: list[MailboxSelection] | None,
) -> bool:
    return bool(limit and _is_plain_text_search(
        terms, sort_by, direction, search_attachments, mailbox_selections
    ))


def search_header_page(
    archive: Path,
    terms: SearchTerms,
    limit: int,
    offset: int = 0,
    sort_by: SortField = SortField.DATE,
    direction: SortDirection = SortDirection.DESCENDING,
    search_attachments: bool = False,
    mailbox_selections: list[MailboxSelection] | None = None,
    find_older: bool = False,
    complete_sort: bool = False,
    ordered_text_prefix: bool = False,
) -> SearchHeaderPage:
    catalog_path, search_path = archive / "archive.sqlite3", archive / "search.sqlite3"
    if not catalog_path.is_file() or not search_path.is_file():
        raise ValueError(f"{archive} must contain archive.sqlite3 and search.sqlite3")
    database = sqlite3.connect(f"file:{catalog_path}?mode=ro", uri=True)
    try:
        database.execute("ATTACH DATABASE ? AS search", (f"file:{search_path}?mode=ro",))
        fields = ("message_pk", "recipients", "sender", "subject", "date_utc", "attachment_count")
        recent_eligible = _is_plain_recent_text_search(
            terms, limit, sort_by, direction, search_attachments, mailbox_selections
        )
        if recent_eligible and not find_older:
            recent = _recent_text_statement(terms, limit, offset, sort_by, direction)
            rows = database.execute(recent.sql, recent.parameters).fetchall()
            return SearchHeaderPage(
                results=[MessageHeader.model_validate(dict(zip(fields, row))) for row in rows],
                older_results_unchecked=True,
            )
        statement = _search_statement(
            terms, limit, offset, sort_by, direction, search_attachments, mailbox_selections,
            newest_page=find_older and not complete_sort and _is_plain_text_search(
                terms, sort_by, direction, search_attachments, mailbox_selections
            ),
            ordered_text_prefix=ordered_text_prefix,
        )
        return SearchHeaderPage(
            results=[
                MessageHeader.model_validate(dict(zip(fields, row)))
                for row in database.execute(statement.sql, statement.parameters)
            ]
        )
    finally:
        database.close()


def search_result_count(
    archive: Path,
    terms: SearchTerms,
    search_attachments: bool = False,
    mailbox_selections: list[MailboxSelection] | None = None,
    maximum: int | None = None,
) -> int:
    """Count searchable matches, stopping at ``maximum`` when supplied."""
    catalog_path, search_path = archive / "archive.sqlite3", archive / "search.sqlite3"
    if not catalog_path.is_file() or not search_path.is_file():
        raise ValueError(f"{archive} must contain archive.sqlite3 and search.sqlite3")
    database = sqlite3.connect(f"file:{catalog_path}?mode=ro", uri=True)
    try:
        database.execute("ATTACH DATABASE ? AS search", (f"file:{search_path}?mode=ro",))
        statement = _count_statement(terms, search_attachments, mailbox_selections, maximum)
        return int(database.execute(statement.sql, statement.parameters).fetchone()[0])
    finally:
        database.close()


def search_headers(
    archive: Path,
    terms: SearchTerms,
    limit: int,
    offset: int = 0,
    sort_by: SortField = SortField.DATE,
    direction: SortDirection = SortDirection.DESCENDING,
    search_attachments: bool = False,
    mailbox_selections: list[MailboxSelection] | None = None,
) -> list[MessageHeader]:
    """Return an exact CLI page, using recent results only when they fill it."""
    page = search_header_page(
        archive, terms, limit, offset, sort_by, direction, search_attachments, mailbox_selections
    )
    if not page.older_results_unchecked or len(page.results) == limit:
        return page.results
    return search_header_page(
        archive, terms, limit, offset, sort_by, direction, search_attachments, mailbox_selections,
        find_older=True,
    ).results


def rendered_headers(message: Message, full: bool) -> str:
    headers = message.items() if full else [(name, message.get(name)) for name in ("To", "From", "Cc", "Subject", "Date")]
    return "\n".join(f"{name}: {decoded_header(str(value))}" for name, value in headers if value is not None)


def text_parts(message: Message, content_type: str) -> list[str]:
    parts = message.walk() if message.is_multipart() else [message]
    return [decoded_part(part) for part in parts if part.get_content_type() == content_type and not is_attachment(part)]


def render_message(raw: bytes, full_headers: bool, html: bool) -> str:
    """Render selected headers and a user-readable preferred message part."""
    message = BytesParser(policy=policy.compat32).parsebytes(raw)
    headers = rendered_headers(message, full_headers)
    plain = text_parts(message, "text/plain")
    html_parts = text_parts(message, "text/html")
    if html:
        body = "\n\n".join(html_parts)
    elif plain:
        body = "\n\n".join(plain)
    else:
        body = "\n\n".join(html_text(part) for part in html_parts)
    return headers + ("\n\n" + body.rstrip("\n") if body else "") + "\n"


def read_message_bytes(archive: Path, message_pk: int) -> bytes:
    """Return one canonical message after direct-location SHA-256 validation."""
    database = sqlite3.connect(f"file:{archive / 'archive.sqlite3'}?mode=ro", uri=True)
    try:
        row = database.execute(
            "SELECT messages.sha256, mbox_generations.filename, locations.byte_offset, locations.byte_length "
            "FROM messages LEFT JOIN locations USING (message_pk) "
            "LEFT JOIN mbox_generations USING (generation_pk) WHERE message_pk = ?",
            (message_pk,),
        ).fetchone()
    finally:
        database.close()
    if row is None:
        raise ValueError(f"no message numbered {message_pk}")
    target, filename, offset, length = row
    if filename is None:
        raise ValueError(f"archive invariant failed: message {message_pk} has no MBOX location")
    try:
        raw = read_verified_location(
            mbox_path(archive, filename),
            MboxLocation(byte_offset=offset, byte_length=length),
            target,
        )
    except ValueError as error:
        raise ValueError(f"MBOX location hash mismatch for message {message_pk}") from error
    return raw


def print_message(archive: Path, message_pk: int, full_headers: bool, html: bool, mime: bool) -> None:
    raw = read_message_bytes(archive, message_pk)
    if mime:
        sys.stdout.buffer.write(raw)
    else:
        sys.stdout.write(render_message(raw, full_headers, html))


def format_header(result: MessageHeader, number_width: int, styled: bool) -> str:
    """Format a one-line result, emphasizing the subject only for terminals."""
    subject = result.subject
    if styled:
        subject = f"{BOLD}{subject}{RESET}"
    return f"{result.message_pk:>{number_width}} to:{result.recipients} from:{result.sender} subject:{subject} date:{result.date_utc}"


def terminal_width() -> int:
    return max(40, shutil.get_terminal_size(fallback=(80, 24)).columns - 2)


def search_epilog() -> str:
    width = terminal_width()
    selectors = (
        ("any:ADDRESS", "sender or To, Cc, or Bcc recipient address"),
        ("from:ADDRESS", "sender address"),
        ("to:ADDRESS", "To recipient address"),
        ("cc:ADDRESS", "Cc recipient address"),
        ("bcc:ADDRESS", "Bcc recipient address"),
        ("subject:TEXT", "subject text"),
        ("date:YYYY-MM-DD", "messages on that UTC calendar day"),
        ("before:YYYY-MM-DD", "messages before that UTC calendar day"),
        ("after:YYYY-MM-DD", "messages after that UTC calendar day"),
    )
    selector_lines = [textwrap.fill(f"  {key:<21} {description}", width=width, subsequent_indent=" " * 23) for key, description in selectors]
    examples = (
        "  mailsearch to:alice@example.com budget",
        "  mailsearch --limit 0 after:2024-01-01",
        "  mailsearch --archive OTHER_ARCHIVE 42",
    )
    return "\n\n".join(
        (
            textwrap.fill("Ordinary words search indexed headers and message text. Every term is ANDed.", width=width),
            "Search selectors:\n" + "\n".join(selector_lines),
            textwrap.fill("A sole message number prints selected headers and the preferred text part. Use --headers, --html, or --mime for alternate views.", width=width),
            "Examples:\n" + "\n".join(examples),
        )
    )


class TerminalHelpFormatter(argparse.HelpFormatter):
    """Wrap help to the current terminal width, including the search syntax."""

    def __init__(self, prog: str) -> None:
        super().__init__(prog, width=terminal_width())

    def _fill_text(self, text: str, width: int, indent: str) -> str:
        return "\n".join(indent + line for line in text.splitlines())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mailsearch",
        description="Read-only search of a mailarchiver archive.",
        epilog=search_epilog(),
        formatter_class=TerminalHelpFormatter,
    )
    add_archive_argument(parser, "directory containing archive.sqlite3 and search.sqlite3")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="maximum matches to print; 0 prints all (default: 10)")
    parser.add_argument("--headers", action="store_true", help="with a message number, show all headers")
    view = parser.add_mutually_exclusive_group()
    view.add_argument("--html", action="store_true", help="with a message number, print decoded text/html parts")
    view.add_argument("--mime", action="store_true", help="with a message number, print the full RFC 5322/MIME source")
    parser.add_argument("terms", nargs="*", metavar="SEARCH")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.archive = require_archive(parser, args.archive)
    if args.limit < 0:
        raise SystemExit("mailsearch: --limit must be zero or positive")
    try:
        if len(args.terms) == 1 and args.terms[0].isdigit():
            print_message(args.archive, int(args.terms[0]), args.headers, args.html, args.mime)
            return 0
        terms = parse_terms(args.terms)
        results = search_headers(args.archive, terms, args.limit)
        number_width = max((len(str(result.message_pk)) for result in results), default=1)
        for result in results:
            print(format_header(result, number_width, sys.stdout.isatty()))
    except ValueError as error:
        raise SystemExit(f"mailsearch: {error}") from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
