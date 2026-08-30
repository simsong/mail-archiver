"""Verify disposable FTS extraction, attachment metadata, updates, and failure isolation."""

import hashlib
import sqlite3
import warnings
from pathlib import Path

from bs4 import XMLParsedAsHTMLWarning

from mailarchiver.catalog import create_search
from mailarchiver.search import body_preview, index_message, index_message_safely, message_text


def test_plain_body_wins_and_attachment_requires_opt_in() -> None:
    raw = b"\n".join(
        [
            b"From: sender@example.net",
            b"Subject: preferred body",
            b"Content-Type: multipart/mixed; boundary=x",
            b"",
            b"--x",
            b"Content-Type: text/plain; charset=utf-8",
            b"",
            b"plain body",
            b"--x",
            b"Content-Type: text/html; charset=utf-8",
            b"",
            b"<p>html body</p>",
            b"--x",
            b"Content-Type: text/plain; name=attached.txt",
            b"Content-Disposition: attachment; filename=attached.txt",
            b"",
            b"attachment words",
            b"--x--",
        ]
    )
    default = message_text(raw, index_attachments=False)
    assert "plain body" in default
    assert "html body" not in default
    assert "attachment words" not in default
    assert "attachment words" in message_text(raw, index_attachments=True)


def test_xml_looking_html_is_rendered_without_parser_warning() -> None:
    """Requirement: text/html remains best-effort derived content even when it resembles XML."""
    raw = b"\n".join(
        [
            b"Content-Type: text/html; charset=utf-8",
            b"",
            b'<?xml version="1.0"?><html><body><p>message body</p></body></html>',
        ]
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rendered = message_text(raw, index_attachments=False)

    assert "message body" in rendered
    assert not any(item.category is XMLParsedAsHTMLWarning for item in caught)


def test_index_records_attachment_count_names_and_mime_types(tmp_path: Path) -> None:
    """Requirement: the disposable index stores ordered attachment metadata."""
    raw = b"\n".join(
        [
            b"Content-Type: multipart/mixed; boundary=x",
            b"",
            b"--x",
            b"Content-Type: text/plain",
            b"",
            b"body",
            b"--x",
            b"Content-Type: application/pdf",
            b'Content-Disposition: attachment; filename="annual report.pdf"',
            b"",
            b"pdf",
            b"--x",
            b"Content-Type: text/calendar; name=invite.ics",
            b"Content-Disposition: attachment",
            b"",
            b"calendar",
            b"--x--",
        ]
    )
    search = create_search(tmp_path / "search.sqlite3")
    try:
        index_message(search, raw, False)
        metadata = search.execute("SELECT attachment_count, preview FROM message_metadata").fetchone()
        attachments = search.execute(
            "SELECT attachment_ordinal, part_id, filename, mime_type FROM message_attachments ORDER BY attachment_ordinal"
        ).fetchall()
    finally:
        search.close()

    assert metadata == (2, "body")
    assert attachments == [
        (1, 2, "annual report.pdf", "application/pdf"),
        (2, 3, "invite.ics", "text/calendar"),
    ]


def test_reindex_uses_indexed_sha_mapping_and_fts_rowids(tmp_path: Path) -> None:
    """Requirement: updating disposable FTS rows never scans FTS by its unindexed SHA-256 column."""
    first = b"\n".join(
        (
            b"Content-Type: multipart/mixed; boundary=x",
            b"",
            b"--x",
            b"Content-Type: text/plain",
            b"",
            b"first body",
            b"--x",
            b"Content-Type: text/plain; name=attachment.txt",
            b"Content-Disposition: attachment",
            b"",
            b"attachment body",
            b"--x--",
        )
    )
    second = b"Content-Type: text/plain\n\nsecond body"
    search = create_search(tmp_path / "search.sqlite3")
    try:
        index_message(search, first, True)
        index_message(search, second, False)
        digest = hashlib.sha256(first).hexdigest()
        old_rowids = search.execute(
            "SELECT message_fts_rowid, attachment_fts_rowid FROM message_metadata WHERE sha256 = ?", (digest,)
        ).fetchone()
        assert old_rowids is not None and old_rowids[1] is not None

        plan = search.execute(
            "EXPLAIN QUERY PLAN SELECT message_fts_rowid, attachment_fts_rowid "
            "FROM message_metadata WHERE sha256 = ?",
            (digest,),
        ).fetchone()[3]
        index_message(search, first, False)
        new_rowids = search.execute(
            "SELECT message_fts_rowid, attachment_fts_rowid FROM message_metadata WHERE sha256 = ?", (digest,)
        ).fetchone()
        old_message_count = search.execute("SELECT count(*) FROM message_fts WHERE rowid = ?", (old_rowids[0],)).fetchone()
        old_attachment_count = search.execute(
            "SELECT count(*) FROM attachment_fts WHERE rowid = ?", (old_rowids[1],)
        ).fetchone()
    finally:
        search.close()

    assert "USING INDEX" in plan and "sha256=?" in plan
    assert new_rowids is not None and new_rowids[0] != old_rowids[0] and new_rowids[1] is None
    assert old_message_count == old_attachment_count == (0,)


def test_address_suggestion_reindex_recalculates_last_seen(tmp_path: Path) -> None:
    """Requirement: address ranking retains deduplicated counts and the newest indexed message date."""
    search = create_search(tmp_path / "search.sqlite3")
    first = b"Message-ID: <first@example>\nFrom: Person <person@example.org>\n\nfirst"
    second = b"Message-ID: <second@example>\nFrom: Person <person@example.org>\n\nsecond"
    try:
        index_message(search, first, False, date_utc="2024-01-01T00:00:00+00:00")
        index_message(search, second, False, date_utc="2025-01-01T00:00:00+00:00")
        index_message(search, second, False, date_utc="2023-01-01T00:00:00+00:00")
        row = search.execute(
            "SELECT message_count, last_seen FROM address_suggestions WHERE address = 'person@example.org'"
        ).fetchone()
    finally:
        search.close()

    assert row == (2, "2024-01-01T00:00:00+00:00")


def test_body_preview_collapses_and_bounds_words() -> None:
    """Requirement: indexed result previews contain the first 18 body words on one line."""
    body = "one two\nthree four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen"

    assert body_preview(body) == (
        "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen…"
    )


def test_index_failure_is_recorded_without_raising() -> None:
    """Requirement: disposable search extraction cannot veto canonical publication."""
    catalog = sqlite3.connect(":memory:")
    search = sqlite3.connect(":memory:")
    catalog.execute(
        "CREATE TABLE metadata_defects (message_pk INTEGER, field TEXT, detail TEXT, UNIQUE(message_pk, field, detail))"
    )

    index_message_safely(catalog, search, 7, b"Subject: retained\n\nbody", False)

    field, detail = catalog.execute("SELECT field, detail FROM metadata_defects").fetchone()
    assert field == "search-index"
    assert "no such table:" in detail
