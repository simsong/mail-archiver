"""Build disposable FTS5 body, preview, and optional text-attachment indexes."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import warnings
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import getaddresses

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from pydantic import BaseModel

from .encoding import decode_text
from .message import decoded_header, decoded_message_header

SEARCH_CATEGORIES = ("Archive", "Sent")
QUARANTINE_MAILBOX = re.compile(r"^(?:INFECTED|MALFORMED)\d+\.mbox$")
PREVIEW_WORDS = 18


class IndexedAttachment(BaseModel):
    attachment_ordinal: int
    part_id: int
    filename: str
    mime_type: str


class SuggestedAddress(BaseModel):
    address: str
    display_name: str


def decoded_part(part: Message) -> str:
    payload = part.get_payload(decode=True) or b""
    return decode_text(payload, part.get_content_charset()).value


def is_attachment(part: Message) -> bool:
    return part.get_content_disposition() == "attachment" or part.get_filename() is not None


def html_text(value: str) -> str:
    """Render email HTML, including XML-looking XHTML declared as text/html."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        return BeautifulSoup(value, "html.parser").get_text(" ", strip=True)


def preferred_body_text(message: Message) -> str:
    """Extract the user-readable message body without attachment text."""
    parts = list(message.walk()) if message.is_multipart() else [message]
    plain = [decoded_part(part) for part in parts if part.get_content_type() == "text/plain" and not is_attachment(part)]
    html = [decoded_part(part) for part in parts if part.get_content_type() == "text/html" and not is_attachment(part)]
    if plain:
        body = "\n".join(plain)
    elif html:
        body = "\n".join(html_text(part) for part in html)
    elif not message.is_multipart() and not is_attachment(message):
        body = decoded_part(message)
    else:
        body = ""
    return body


def parsed_message_text(
    message: Message,
    index_attachments: bool,
    body: str | None = None,
    raw: bytes | None = None,
) -> str:
    names = ("From", "To", "Cc", "Subject", "Date")
    headers = "\n".join(
        decoded_message_header(raw, message, name) if raw is not None else decoded_header(str(message.get(name) or ""))
        for name in names
    )
    parts = list(message.walk()) if message.is_multipart() else [message]
    body = preferred_body_text(message) if body is None else body
    if index_attachments:
        attachments = [decoded_part(part) for part in parts if is_attachment(part) and part.get_content_maintype() == "text"]
        body = "\n".join([body, *attachments])
    return "\n".join((headers, body))


def attachment_text(message: Message) -> str:
    """Return decoded text from MIME attachments, excluding binary payloads."""
    parts = list(message.walk()) if message.is_multipart() else [message]
    return "\n".join(
        decoded_part(part) for part in parts if is_attachment(part) and part.get_content_maintype() == "text"
    )


def body_preview(body: str, word_limit: int = PREVIEW_WORDS) -> str:
    """Collapse the first bounded body words into a single display line."""
    words = re.findall(r"\S+", body)
    return " ".join(words[:word_limit]) + ("…" if len(words) > word_limit else "")


def message_text(raw: bytes, index_attachments: bool) -> str:
    """Return headers plus preferred body text; omit attachments unless requested."""
    message = BytesParser(policy=policy.compat32).parsebytes(raw)
    return parsed_message_text(message, index_attachments, raw=raw)


def indexed_attachments(message: Message) -> list[IndexedAttachment]:
    """Return stable metadata for MIME parts treated as attachments."""
    parts = list(message.walk()) if message.is_multipart() else [message]
    attachments: list[IndexedAttachment] = []
    for part_id, part in enumerate(parts):
        if not is_attachment(part):
            continue
        ordinal = len(attachments) + 1
        supplied_name = part.get_filename()
        filename = decoded_header(str(supplied_name)).strip() if supplied_name is not None else f"attachment-{ordinal}"
        attachments.append(
            IndexedAttachment(
                attachment_ordinal=ordinal,
                part_id=part_id,
                filename=filename or f"attachment-{ordinal}",
                mime_type=part.get_content_type(),
            )
        )
    return attachments


def suggested_addresses(message: Message) -> list[SuggestedAddress]:
    """Return one decoded display name for every address present in the message headers."""
    identities: dict[str, str] = {}
    values = [str(value) for name in ("From", "To", "Cc", "Bcc") for value in message.get_all(name, [])]
    for supplied_name, supplied_address in getaddresses(values):
        address = supplied_address.strip().lower()
        if not address:
            continue
        name = decoded_header(supplied_name).strip()
        if address not in identities or (name and not identities[address]):
            identities[address] = name
    return [SuggestedAddress(address=address, display_name=name) for address, name in sorted(identities.items())]


def delete_indexed_message(search: sqlite3.Connection, digest: str) -> None:
    """Delete disposable message content through the indexed SHA-256 mapping."""
    suggestion_pks = [
        int(row[0]) for row in search.execute(
            "SELECT suggestion_pk FROM message_address_suggestions WHERE sha256 = ?", (digest,)
        )
    ]
    search.execute("DELETE FROM message_address_suggestions WHERE sha256 = ?", (digest,))
    search.executemany(
        "UPDATE address_suggestions SET message_count = message_count - 1, "
        "last_seen = COALESCE((SELECT max(seen_at) FROM message_address_suggestions "
        "WHERE suggestion_pk = ?), '') WHERE suggestion_pk = ?",
        ((suggestion_pk, suggestion_pk) for suggestion_pk in suggestion_pks),
    )
    search.executemany(
        "DELETE FROM address_suggestions WHERE suggestion_pk = ? AND message_count = 0",
        ((suggestion_pk,) for suggestion_pk in suggestion_pks),
    )
    row = search.execute(
        "SELECT message_fts_rowid, attachment_fts_rowid FROM message_metadata WHERE sha256 = ?", (digest,)
    ).fetchone()
    if row is None:
        return
    message_fts_rowid, attachment_fts_rowid = row
    search.execute("DELETE FROM message_fts WHERE rowid = ?", (message_fts_rowid,))
    if attachment_fts_rowid is not None:
        search.execute("DELETE FROM attachment_fts WHERE rowid = ?", (attachment_fts_rowid,))
    search.execute("DELETE FROM message_metadata WHERE sha256 = ?", (digest,))


def index_message(
    search: sqlite3.Connection, raw: bytes, index_attachments: bool, *, date_utc: str = ""
) -> None:
    digest = hashlib.sha256(raw).hexdigest()
    message = BytesParser(policy=policy.compat32).parsebytes(raw)
    attachments = indexed_attachments(message)
    body = preferred_body_text(message)
    delete_indexed_message(search, digest)
    message_fts_rowid = search.execute(
        "INSERT INTO message_fts(sha256, content) VALUES (?, ?)",
        (digest, parsed_message_text(message, False, body)),
    ).lastrowid
    assert message_fts_rowid is not None
    attachment_fts_rowid: int | None = None
    if index_attachments and (attachments_text := attachment_text(message)):
        attachment_fts_rowid = search.execute(
            "INSERT INTO attachment_fts(sha256, content) VALUES (?, ?)", (digest, attachments_text)
        ).lastrowid
    search.execute(
        "INSERT INTO message_metadata(sha256, message_fts_rowid, attachment_fts_rowid, attachment_count, preview) "
        "VALUES (?, ?, ?, ?, ?)",
        (digest, message_fts_rowid, attachment_fts_rowid, len(attachments), body_preview(body)),
    )
    search.executemany(
        "INSERT INTO message_attachments(sha256, attachment_ordinal, part_id, filename, mime_type) VALUES (?, ?, ?, ?, ?)",
        ((digest, item.attachment_ordinal, item.part_id, item.filename, item.mime_type) for item in attachments),
    )
    for identity in suggested_addresses(message):
        suggestion = search.execute(
            "INSERT INTO address_suggestions(address, display_name, message_count, last_seen) VALUES (?, ?, 1, ?) "
            "ON CONFLICT(address) DO UPDATE SET message_count = message_count + 1, "
            "last_seen = max(address_suggestions.last_seen, excluded.last_seen), display_name = CASE "
            "WHEN address_suggestions.display_name = '' THEN excluded.display_name "
            "ELSE address_suggestions.display_name END RETURNING suggestion_pk",
            (identity.address, identity.display_name, date_utc),
        ).fetchone()
        assert suggestion is not None
        search.execute(
            "INSERT INTO message_address_suggestions(sha256, suggestion_pk, seen_at) VALUES (?, ?, ?)",
            (digest, suggestion[0], date_utc),
        )


def index_message_safely(
    catalog: sqlite3.Connection, search: sqlite3.Connection, message_pk: int, raw: bytes,
    index_attachments: bool, *, date_utc: str = ""
) -> None:
    """Index disposable content without allowing extraction failure to veto canonical mail."""
    try:
        index_message(search, raw, index_attachments, date_utc=date_utc)
        search.commit()
    except Exception as error:
        search.rollback()
        catalog.execute(
            "INSERT OR IGNORE INTO metadata_defects(message_pk, field, detail) VALUES (?, 'search-index', ?)",
            (message_pk, f"{type(error).__name__}: {error}"),
        )
        catalog.commit()
