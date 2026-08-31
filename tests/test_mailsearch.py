"""Verify CLI search syntax, indexed plans, rendering, and exact numbered retrieval."""

from __future__ import annotations

import hashlib
import mailbox
import sqlite3
import subprocess
import sys
from os import environ
from pathlib import Path

from mailarchiver.bagit import initialize_bag
from mailarchiver.catalog import address_pk, create_catalog, create_search
from mailarchiver.layout import mbox_directory
from mailarchiver.mailsearch import (
    MessageHeader,
    RECENT_FTS_SCAN_LIMIT,
    SearchTerms,
    SortDirection,
    SortField,
    _search_statement,
    _recent_text_statement,
    format_header,
    render_message,
    search_header_page,
    search_headers,
)
from mailarchiver.mbox import add_message
from mailarchiver.search import index_message


def add_catalogued_message(archive: Path, message_pk: int, raw: bytes) -> None:
    path = mbox_directory(archive) / "2024-Archive1.mbox"
    box = mailbox.mbox(path, create=True)
    try:
        location = add_message(box, path, raw)
    finally:
        box.close()
    catalog = create_catalog(archive / "archive.sqlite3")
    try:
        generation = catalog.execute(
            "INSERT INTO mbox_generations(filename, sha256, message_count, byte_count) "
            "VALUES (?, '', 0, 0) ON CONFLICT(filename) DO UPDATE SET filename = excluded.filename "
            "RETURNING generation_pk",
            (path.name,),
        ).fetchone()
        assert generation is not None
        catalog.execute(
            "INSERT INTO locations(message_pk, generation_pk, byte_offset, byte_length) VALUES (?, ?, ?, ?)",
            (message_pk, generation[0], location.byte_offset, location.byte_length),
        )
        catalog.commit()
    finally:
        catalog.close()


def make_archive(tmp_path: Path) -> tuple[Path, bytes]:
    archive = tmp_path / "archive"
    initialize_bag(archive)
    raw = (
        b"Message-ID: <one@example>\nFrom: sender@example.net\nTo: recipient@example.net\nCc: copy@example.net\nX-Trace: one\n"
        b"Subject: planning meeting\nDate: Wed, 03 Jan 2024 10:00:00 +0000\n\nMeeting agenda.\n"
    )
    catalog = create_catalog(archive / "archive.sqlite3")
    search = create_search(archive / "search.sqlite3")
    try:
        sender, recipient = address_pk(catalog, "sender@example.net"), address_pk(catalog, "recipient@example.net")
        copy, blind = address_pk(catalog, "copy@example.net"), address_pk(catalog, "blind@example.net")
        cursor = catalog.execute(
            "INSERT INTO messages(message_id_normalized, sha256, sender_address_pk, subject, date_utc, date_source, category) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("one@example", hashlib.sha256(raw).hexdigest(), sender, "planning meeting", "2024-01-03T10:00:00+00:00", "date", "Archive"),
        )
        message_pk = int(cursor.lastrowid)
        catalog.execute(
            "INSERT INTO recipients(message_pk, address_pk, role) VALUES (?, ?, 'to')",
            (message_pk, recipient),
        )
        catalog.execute(
            "INSERT INTO recipients(message_pk, address_pk, role) VALUES (?, ?, 'cc')",
            (message_pk, copy),
        )
        catalog.execute(
            "INSERT INTO recipients(message_pk, address_pk, role) VALUES (?, ?, 'bcc')",
            (message_pk, blind),
        )
        catalog.commit()
        index_message(search, raw, False)
        search.commit()
    finally:
        catalog.close()
        search.close()
    add_catalogued_message(archive, message_pk, raw)
    return archive, raw


def run_search(*arguments: str, environment: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "mailarchiver.mailsearch", *arguments], capture_output=True, text=True, check=False, env=environment)


def test_mailsearch_finds_structured_and_full_text_matches(tmp_path: Path) -> None:
    """Requirement: selectors and ordinary terms intersect through the two databases."""
    archive, _ = make_archive(tmp_path)
    result = run_search(
        "--archive", str(archive), "to:recipient@example.net", "from:sender@example.net", "subject:planning", "date:2024-01-03", "agenda"
    )
    assert result.returncode == 0, result.stderr
    assert "from:sender@example.net subject:planning meeting" in result.stdout


def test_address_selectors_preserve_from_to_cc_bcc_and_any_roles(tmp_path: Path) -> None:
    """Requirement: address chips can search any address or one exact RFC recipient role."""
    archive, _ = make_archive(tmp_path)

    for selector in (
        "any:sender@example.net",
        "any:copy@example.net",
        "from:sender@example.net",
        "to:recipient@example.net",
        "cc:copy@example.net",
        "bcc:blind@example.net",
    ):
        assert run_search("--archive", str(archive), selector).returncode == 0
        assert run_search("--archive", str(archive), selector).stdout.startswith("1 to:")
    assert run_search("--archive", str(archive), "to:copy@example.net").stdout == ""


def test_bounded_listing_uses_date_index_before_recipient_aggregation(tmp_path: Path) -> None:
    """Requirement: a bounded date listing selects indexed candidates before recipient aggregation."""
    archive, _ = make_archive(tmp_path)
    catalog = sqlite3.connect(archive / "archive.sqlite3")
    try:
        catalog.execute("ATTACH DATABASE ? AS search", (str(archive / "search.sqlite3"),))
        statement = _search_statement(SearchTerms(), limit=10)
        plan = [row[3] for row in catalog.execute("EXPLAIN QUERY PLAN " + statement.sql, statement.parameters)]
    finally:
        catalog.close()

    assert "MATERIALIZE candidates" in plan
    assert any("messages_date_message" in step for step in plan)


def test_bounded_alphabetical_sorts_use_v1_expression_indexes(tmp_path: Path) -> None:
    """Requirement: subject and sender pages traverse their requested sort indexes."""
    archive, _ = make_archive(tmp_path)
    catalog = sqlite3.connect(archive / "archive.sqlite3")
    try:
        catalog.execute("ATTACH DATABASE ? AS search", (str(archive / "search.sqlite3"),))
        subject = _search_statement(
            SearchTerms(), 10, sort_by=SortField.SUBJECT, direction=SortDirection.ASCENDING
        )
        sender = _search_statement(
            SearchTerms(), 10, sort_by=SortField.SENDER, direction=SortDirection.ASCENDING
        )
        subject_plan = [
            row[3] for row in catalog.execute("EXPLAIN QUERY PLAN " + subject.sql, subject.parameters)
        ]
        sender_plan = [
            row[3] for row in catalog.execute("EXPLAIN QUERY PLAN " + sender.sql, sender.parameters)
        ]
    finally:
        catalog.close()

    assert any("messages_subject_message" in step for step in subject_plan)
    assert any("email_addresses_lower_address" in step for step in sender_plan)
    assert any("messages_sender_address_pk" in step for step in sender_plan)


def test_full_text_candidates_use_fts_and_catalog_sha_indexes(tmp_path: Path) -> None:
    """Requirement: FTS hits enter the catalog through indexed SHA-256 lookups."""
    archive, _ = make_archive(tmp_path)
    catalog = sqlite3.connect(archive / "archive.sqlite3")
    try:
        catalog.execute("ATTACH DATABASE ? AS search", (str(archive / "search.sqlite3"),))
        statement = _search_statement(SearchTerms(text=["agenda"]), 10)
        plan = [
            row[3] for row in catalog.execute("EXPLAIN QUERY PLAN " + statement.sql, statement.parameters)
        ]
    finally:
        catalog.close()

    assert any("messages_sha256" in step for step in plan)
    assert any("message_fts VIRTUAL TABLE INDEX" in step for step in plan)


def test_recent_full_text_page_uses_date_and_fts_rowid_indexes(tmp_path: Path) -> None:
    """Requirement: common newest-first text searches bound catalog work before aggregation."""
    archive, _ = make_archive(tmp_path)
    catalog = sqlite3.connect(archive / "archive.sqlite3")
    try:
        catalog.execute("ATTACH DATABASE ? AS search", (str(archive / "search.sqlite3"),))
        statement = _recent_text_statement(SearchTerms(text=["agenda"]), 10, 0)
        plan = [row[3] for row in catalog.execute("EXPLAIN QUERY PLAN " + statement.sql, statement.parameters)]
    finally:
        catalog.close()

    assert any("messages_date_message" in step for step in plan)
    assert any("message_metadata" in step and "sha256" in step for step in plan)
    assert any("message_fts VIRTUAL TABLE INDEX" in step for step in plan)


def test_common_text_fast_path_returns_newest_requested_page(tmp_path: Path) -> None:
    """Requirement: a full bounded FTS page preserves newest-first result semantics."""
    archive = tmp_path / "archive"
    initialize_bag(archive)
    catalog = create_catalog(archive / "archive.sqlite3")
    search = create_search(archive / "search.sqlite3")
    try:
        sender = address_pk(catalog, "sender@example.net")
        for number in range(12):
            subject = f"subject-{number * 7 % 12:02d}"
            raw = (
                f"Message-ID: <{number}@example>\nFrom: sender@example.net\n"
                f"Subject: {subject}\n\ncommon-search-needle\n"
            ).encode()
            catalog.execute(
                "INSERT INTO messages(message_id_normalized, sha256, sender_address_pk, subject, date_utc, date_source, category) "
                "VALUES (?, ?, ?, ?, '2024-01-01T00:00:00+00:00', 'date', 'Archive')",
                (f"{number}@example", hashlib.sha256(raw).hexdigest(), sender, subject),
            )
            index_message(search, raw, False)
        catalog.commit()
        search.commit()
    finally:
        catalog.close()
        search.close()

    found = search_headers(archive, SearchTerms(text=["common-search-needle"]), 10)

    assert [header.message_pk for header in found] == list(range(12, 2, -1))

    subject_page = search_header_page(
        archive,
        SearchTerms(text=["common-search-needle"]),
        10,
        sort_by=SortField.SUBJECT,
        direction=SortDirection.ASCENDING,
    )
    expected = sorted(range(3, 13), key=lambda message_pk: ((message_pk - 1) * 7 % 12, message_pk))
    assert subject_page.older_results_unchecked
    assert [header.message_pk for header in subject_page.results] == expected

    older_page = search_header_page(
        archive,
        SearchTerms(text=["common-search-needle"]),
        10,
        offset=10,
        sort_by=SortField.SUBJECT,
        direction=SortDirection.ASCENDING,
        find_older=True,
    )
    assert not older_page.older_results_unchecked
    assert [header.message_pk for header in older_page.results] == [1, 2]


def test_sparse_text_match_beyond_recent_window_uses_exact_fallback(tmp_path: Path) -> None:
    """Requirement: a bounded fast-path miss must not hide an older FTS result."""
    archive = tmp_path / "archive"
    initialize_bag(archive)
    catalog = create_catalog(archive / "archive.sqlite3")
    search = create_search(archive / "search.sqlite3")
    raw = b"Message-ID: <old-match@example>\nFrom: sender@example.net\nSubject: old match\n\nunique-search-needle\n"
    try:
        sender = address_pk(catalog, "sender@example.net")
        catalog.execute(
            "INSERT INTO messages(message_id_normalized, sha256, sender_address_pk, subject, date_utc, date_source, category) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "old-match@example",
                hashlib.sha256(raw).hexdigest(),
                sender,
                "old match",
                "2024-01-01T00:00:00+00:00",
                "date",
                "Archive",
            ),
        )
        catalog.executemany(
            "INSERT INTO messages(message_id_normalized, sha256, sender_address_pk, subject, date_utc, date_source, category) "
            "VALUES (?, ?, ?, '', '2024-01-01T00:00:00+00:00', 'date', 'Archive')",
            (
                (f"recent-{number}@example", hashlib.sha256(f"recent-{number}".encode()).hexdigest(), sender)
                for number in range(RECENT_FTS_SCAN_LIMIT)
            ),
        )
        catalog.commit()
        index_message(search, raw, False)
        search.commit()
    finally:
        catalog.close()
        search.close()

    found = search_headers(archive, SearchTerms(text=["unique-search-needle"]), 10)

    assert [header.message_pk for header in found] == [1]


def test_mailsearch_limit_zero_and_number_print_original_message(tmp_path: Path) -> None:
    """Requirement: zero removes the result limit and a message number emits preserved RFC 5322 bytes."""
    archive, raw = make_archive(tmp_path)
    listed = run_search("--archive", str(archive), "--limit", "0")
    assert listed.returncode == 0, listed.stderr
    assert listed.stdout.startswith("1 to:")
    assert "recipient@example.net" in listed.stdout
    displayed = subprocess.run(
        [sys.executable, "-m", "mailarchiver.mailsearch", "--archive", str(archive), "1"], capture_output=True, check=False
    )
    assert displayed.returncode == 0, displayed.stderr
    assert displayed.stdout == render_message(raw, False, False).encode()


def test_mailsearch_listing_excludes_quarantine_categories(tmp_path: Path) -> None:
    """Requirement: ordinary search results exclude catalogued quarantine mail."""
    archive, _ = make_archive(tmp_path)
    catalog = create_catalog(archive / "archive.sqlite3")
    try:
        sender = address_pk(catalog, "quarantine@example.net")
        for category in ("INFECTED", "MALFORMED"):
            catalog.execute(
                "INSERT INTO messages(message_id_normalized, sha256, sender_address_pk, subject, date_utc, date_source, category) "
                "VALUES (?, ?, ?, ?, '2024-01-04T10:00:00+00:00', 'date', ?)",
                (category.lower(), hashlib.sha256(category.encode()).hexdigest(), sender, category.lower(), category),
            )
        catalog.commit()
    finally:
        catalog.close()

    listed = run_search("--archive", str(archive), "--limit", "0")

    assert listed.returncode == 0, listed.stderr
    assert listed.stdout.count("\n") == 1
    assert "planning meeting" in listed.stdout
    assert "infected" not in listed.stdout
    assert "malformed" not in listed.stdout


def test_numbered_empty_message_restores_zero_source_bytes(tmp_path: Path) -> None:
    """Requirement: MBOX's separator newline does not change an empty message's identity."""
    archive = tmp_path / "archive"
    initialize_bag(archive)
    catalog = create_catalog(archive / "archive.sqlite3")
    search = create_search(archive / "search.sqlite3")
    try:
        blank = address_pk(catalog, "")
        cursor = catalog.execute(
            "INSERT INTO messages(message_id_normalized, sha256, sender_address_pk, subject, date_utc, date_source, category) "
            "VALUES (?, ?, ?, '', '2024-01-01T00:00:00+00:00', 'path-year', 'Archive')",
            (hashlib.sha256(b"").hexdigest(), hashlib.sha256(b"").hexdigest(), blank),
        )
        message_pk = int(cursor.lastrowid)
        catalog.commit()
    finally:
        catalog.close()
        search.close()
    add_catalogued_message(archive, message_pk, b"")

    displayed = subprocess.run(
        [sys.executable, "-m", "mailarchiver.mailsearch", "--archive", str(archive), "--mime", "1"],
        capture_output=True,
        check=False,
    )

    assert displayed.returncode == 0, displayed.stderr
    assert displayed.stdout == b""


def test_numbered_message_preserves_literal_quoted_from_line(tmp_path: Path) -> None:
    """Requirement: identity disambiguates an original >From line from MBOX quoting."""
    archive, _ = make_archive(tmp_path)
    raw = b"Message-ID: <quoted@example>\nDate: Thu, 1 Feb 2024 12:00:00 +0000\n\n>From original body\n"
    catalog = create_catalog(archive / "archive.sqlite3")
    try:
        blank = address_pk(catalog, "")
        cursor = catalog.execute(
            "INSERT INTO messages(message_id_normalized, sha256, sender_address_pk, subject, date_utc, date_source, category) "
            "VALUES ('quoted@example', ?, ?, '', '2024-01-01T00:00:00+00:00', 'date', 'Archive')",
            (hashlib.sha256(raw).hexdigest(), blank),
        )
        message_pk = int(cursor.lastrowid)
        catalog.commit()
    finally:
        catalog.close()
    add_catalogued_message(archive, message_pk, raw)

    displayed = subprocess.run(
        [sys.executable, "-m", "mailarchiver.mailsearch", "--archive", str(archive), "--mime", "2"],
        capture_output=True,
        check=False,
    )

    assert displayed.returncode == 0, displayed.stderr
    assert displayed.stdout == raw


def test_message_views_select_headers_and_preferred_mime_parts() -> None:
    """Requirement: display defaults to text/plain, supports HTML, and exposes full headers on request."""
    raw = (
        b"From: sender@example.net\nTo: recipient@example.net\nSubject: test\nX-Trace: retained\n"
        b"Content-Type: multipart/alternative; boundary=part\n\n--part\nContent-Type: text/plain; charset=utf-8\n\nPlain text\n"
        b"--part\nContent-Type: text/html; charset=utf-8\n\n<p>HTML text</p>\n--part--\n"
    )
    assert "Plain text" in render_message(raw, False, False)
    assert "HTML text" not in render_message(raw, False, False)
    assert "<p>HTML text</p>" in render_message(raw, False, True)
    assert "X-Trace: retained" in render_message(raw, True, False)
    html_only = raw.replace(b"Content-Type: text/plain; charset=utf-8\n\nPlain text\n", b"")
    assert "HTML text" in render_message(html_only, False, False)


def test_mailsearch_mime_outputs_original_source(tmp_path: Path) -> None:
    """Requirement: --mime returns all original MIME parts and headers unchanged."""
    archive, raw = make_archive(tmp_path)
    displayed = subprocess.run(
        [sys.executable, "-m", "mailarchiver.mailsearch", "--archive", str(archive), "--mime", "1"], capture_output=True, check=False
    )
    assert displayed.returncode == 0, displayed.stderr
    assert displayed.stdout == raw


def test_mailsearch_help_describes_syntax() -> None:
    """Requirement: help is sufficient to discover the search language and default limit."""
    result = run_search("--help")
    assert result.returncode == 0
    for selector in (
        "any:ADDRESS",
        "from:ADDRESS",
        "to:ADDRESS",
        "cc:ADDRESS",
        "bcc:ADDRESS",
        "subject:TEXT",
        "date:YYYY-MM-DD",
        "before:YYYY-MM-DD",
        "after:YYYY-MM-DD",
    ):
        assert selector in result.stdout
    assert "default: 10" in result.stdout
    assert max(map(len, result.stdout.splitlines())) <= 78


def test_mailsearch_date_bounds_are_strict_calendar_days(tmp_path: Path) -> None:
    """Requirement: date, before, and after use documented UTC calendar-day boundaries."""
    archive, _ = make_archive(tmp_path)
    assert run_search("--archive", str(archive), "before:2024-01-03").stdout == ""
    assert run_search("--archive", str(archive), "after:2024-01-03").stdout == ""
    assert run_search("--archive", str(archive), "after:2024-01-02").stdout.startswith("1 to:")


def test_mailsearch_formats_dynamic_numbers_and_terminal_subjects() -> None:
    """Requirement: numbers align to the result-set width and terminal subjects are bold."""
    header = MessageHeader(message_pk=42, recipients="to@example.net", sender="from@example.net", subject="subject", date_utc="2024-01-03T10:00:00+00:00")
    assert format_header(header, 4, False).startswith("  42 to:")
    assert "subject:\033[1msubject\033[0m" in format_header(header, 4, True)


def test_mail_archive_dir_defaults_and_archive_option_overrides(tmp_path: Path) -> None:
    """Requirement: both CLIs honor MAIL_ARCHIVE_DIR unless --archive supplies a different archive."""
    archive, _ = make_archive(tmp_path)
    environment = {**environ, "MAIL_ARCHIVE_DIR": str(archive)}
    assert run_search("subject:planning", environment=environment).stdout.startswith("1 to:")
    assert run_search("--archive", str(archive), "subject:planning", environment={**environ, "MAIL_ARCHIVE_DIR": str(tmp_path / "missing")}).stdout.startswith("1 to:")
    result = subprocess.run(
        [sys.executable, "-m", "mailarchiver", "report", "--top", "0"], capture_output=True, text=True, check=False, env=environment
    )
    assert result.returncode == 0, result.stderr
    override = subprocess.run(
        [sys.executable, "-m", "mailarchiver", "--archive", str(archive), "report", "--top", "0"],
        capture_output=True,
        text=True,
        check=False,
        env={**environ, "MAIL_ARCHIVE_DIR": str(tmp_path / "missing")},
    )
    assert override.returncode == 0, override.stderr
