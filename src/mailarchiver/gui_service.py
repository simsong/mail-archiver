"""Provide typed, read-only search, MIME rendering, and safe export services."""

from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
import tempfile
import zipfile
from email import policy
from email.message import Message
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from .encoding import decode_text
from .mailsearch import (
    MessageHeader,
    SearchTerms,
    SortDirection,
    SortField,
    parse_query,
    read_message_bytes,
    search_header_page,
    search_result_count,
)
from .mailbox_tree import MailboxSelection
from .message import decoded_header
from .plugin_api import SourceContainerMetadata
from .search import SEARCH_CATEGORIES, decoded_part, is_attachment

PAGE_SIZE = 100
IMMEDIATE_SEARCH_LIMIT = 2_000
RAW_PART_ID = -1
LEGACY_X_HTML_PART_ID = -2
HEADER_BODY_SEPARATOR = re.compile(br"\r\n\r\n|\n\n|\r\r")
LEGACY_X_HTML = re.compile(
    br"\A[ \t\r\n]*<x-html>[ \t\r\n]*(?P<body>.*?)[ \t\r\n]*</x-html>[ \t\r\n]*\Z",
    re.IGNORECASE | re.DOTALL,
)
PROVIDER_LABELS = {
    "gmail": "Gmail",
    "imap": "IMAP",
    "microsoft-exchange": "Microsoft Exchange",
    "o365": "Microsoft 365",
}
BLOCKED_ELEMENTS = {"base", "button", "embed", "form", "frame", "frameset", "iframe", "input", "link", "meta", "object", "script"}
RISKY_SUFFIXES = {
    ".app",
    ".applescript",
    ".bat",
    ".bin",
    ".command",
    ".dmg",
    ".exe",
    ".iso",
    ".jar",
    ".js",
    ".pkg",
    ".ps1",
    ".scpt",
    ".sh",
    ".vbs",
    ".zip",
}


class SearchPage(BaseModel):
    results: list[MessageHeader]
    offset: int
    has_more: bool
    highlight_terms: list[str] = Field(default_factory=list)


class SearchCount(BaseModel):
    total: int | None
    immediate_limit: int = IMMEDIATE_SEARCH_LIMIT


class AddressSuggestion(BaseModel):
    address: str
    display_name: str
    message_count: int
    last_seen: str


class SubjectSuggestion(BaseModel):
    subject: str
    message_count: int


class SearchSuggestions(BaseModel):
    query: str
    addresses: list[AddressSuggestion]
    subjects: list[SubjectSuggestion]


class MessagePreview(BaseModel):
    message_pk: int
    preview: str


class PreviewBatch(BaseModel):
    previews: list[MessagePreview]
    pending: bool
    error: str | None = None


class HeaderField(BaseModel):
    name: str
    value: str


class BodyPart(BaseModel):
    part_id: int
    content_type: str
    label: str


class AttachmentInfo(BaseModel):
    part_id: int
    filename: str
    content_type: str
    byte_length: int
    inline: bool
    preview: str | None = None
    risky: bool = False


class SourceLocation(BaseModel):
    volume: str
    path: str
    offset: int | None
    copy_path: str | None = None
    raw_sha256: str
    semantic_sha256: str | None
    origin: str
    preferred: bool = False


class DateAdjustment(BaseModel):
    date_header: str
    received_median_utc: str
    archive_routing_utc: str


class MessageView(BaseModel):
    message_pk: int
    subject: str
    date_source: str
    date_adjustment: DateAdjustment | None = None
    headers: list[HeaderField]
    body_parts: list[BodyPart]
    preferred_part_id: int
    attachments: list[AttachmentInfo]
    archive_path: str | None
    source_locations: list[SourceLocation]


class PartContent(BaseModel):
    part_id: int
    kind: str
    content_type: str
    content: str
    remote_content_blocked: bool = False


class AttachmentContent(BaseModel):
    filename: str
    content_type: str
    content_base64: str


class AttachmentDescriptor(BaseModel):
    filename: str
    content_type: str


def search_page(
    archive: Path,
    query: str,
    offset: int = 0,
    limit: int = PAGE_SIZE,
    sort_by: SortField | str = SortField.DATE,
    direction: SortDirection | str = SortDirection.DESCENDING,
    search_attachments: bool = False,
    mailbox_selections: list[str] | None = None,
) -> SearchPage:
    """Return exact GUI results using the CLI query language."""
    if offset < 0 or limit < 0:
        raise ValueError("search offset and limit must be nonnegative")
    selections = [MailboxSelection.from_token(token) for token in mailbox_selections or []]
    terms = parse_query(query)
    fetch_limit = limit + 1 if limit else 0
    page = search_header_page(
        archive, terms, fetch_limit, offset, SortField(sort_by),
        SortDirection(direction), search_attachments, selections, find_older=True,
        complete_sort=True,
    )
    return SearchPage(
        results=page.results[:limit] if limit else page.results,
        offset=offset,
        highlight_terms=_highlight_terms(terms),
        has_more=bool(limit and len(page.results) > limit),
    )


def search_count(
    archive: Path,
    query: str,
    search_attachments: bool = False,
    mailbox_selections: list[str] | None = None,
    immediate_limit: int = IMMEDIATE_SEARCH_LIMIT,
) -> SearchCount:
    """Count a complete GUI search before materializing and sorting its headers."""
    if immediate_limit < 1:
        raise ValueError("immediate search limit must be positive")
    selections = [MailboxSelection.from_token(token) for token in mailbox_selections or []]
    observed = search_result_count(
        archive, parse_query(query), search_attachments, selections,
        maximum=immediate_limit + 1,
    )
    return SearchCount(
        total=observed if observed <= immediate_limit else None,
        immediate_limit=immediate_limit,
    )


def _highlight_terms(terms: SearchTerms) -> list[str]:
    """Return unique literal text and textual-selector values for viewer highlighting."""
    values: list[str] = []
    seen: set[str] = set()
    for value in (*terms.text, *terms.any_address, *terms.from_, *terms.to, *terms.cc, *terms.bcc, *terms.subject):
        folded = value.casefold()
        if folded and folded not in seen:
            seen.add(folded)
            values.append(value)
    return values


def search_suggestions(archive: Path, query: str, limit: int = 20) -> SearchSuggestions:
    """Return ranked completions; only email substrings use a derived accelerator."""
    value = " ".join(query.split()).strip()
    if not 1 <= limit <= 50:
        raise ValueError("suggestion limit must be between 1 and 50")
    if len(value) < 3:
        return SearchSuggestions(query=value, addresses=[], subjects=[])
    match = f'"{value.replace(chr(34), chr(34) * 2)}"'
    database = sqlite3.connect(f"file:{archive / 'archive.sqlite3'}?mode=ro", uri=True)
    try:
        database.execute("ATTACH DATABASE ? AS search", (f"file:{archive / 'search.sqlite3'}?mode=ro",))
        address_rows = database.execute(
            "WITH matching(suggestion_pk) AS ("
            "SELECT rowid FROM search.address_suggestion_fts WHERE address_suggestion_fts MATCH ? "
            "UNION SELECT suggestion_pk FROM search.address_suggestions "
            "WHERE instr(lower(display_name), lower(?)) > 0) "
            "SELECT suggestions.address, suggestions.display_name, suggestions.message_count, suggestions.last_seen "
            "FROM matching JOIN search.address_suggestions suggestions USING (suggestion_pk) "
            "ORDER BY suggestions.message_count DESC, suggestions.last_seen DESC, "
            "lower(suggestions.address) LIMIT ?",
            (match, value, limit),
        )
        subject_rows = database.execute(
            "SELECT subject, count(*) AS message_count FROM messages "
            "WHERE category IN (?, ?) AND subject <> '' AND instr(lower(subject), lower(?)) > 0 "
            "GROUP BY subject ORDER BY message_count DESC, lower(subject) LIMIT ?",
            (*SEARCH_CATEGORIES, value, limit),
        )
        return SearchSuggestions(
            query=value,
            addresses=[
                AddressSuggestion(address=address, display_name=name, message_count=count, last_seen=last_seen)
                for address, name, count, last_seen in address_rows
            ],
            subjects=[SubjectSuggestion(subject=subject, message_count=count) for subject, count in subject_rows],
        )
    finally:
        database.close()


def searchable_message_count(archive: Path) -> int:
    """Count deduplicated canonical messages visible to search."""
    database = sqlite3.connect(f"file:{archive / 'archive.sqlite3'}?mode=ro", uri=True)
    try:
        row = database.execute(
            "SELECT count(*) FROM messages WHERE category IN (?, ?)", SEARCH_CATEGORIES
        ).fetchone()
        return 0 if row is None else int(row[0])
    finally:
        database.close()


def message_previews(archive: Path, message_pks: list[int]) -> list[MessagePreview]:
    """Read indexed body previews without opening canonical MBOX content."""
    unique_pks: list[int] = []
    seen: set[int] = set()
    for message_pk in message_pks:
        if message_pk not in seen:
            unique_pks.append(message_pk)
            seen.add(message_pk)
    if not unique_pks or len(unique_pks) > PAGE_SIZE:
        raise ValueError(f"request between 1 and {PAGE_SIZE} message previews")
    database = sqlite3.connect(f"file:{archive / 'archive.sqlite3'}?mode=ro", uri=True)
    try:
        database.execute("ATTACH DATABASE ? AS search", (f"file:{archive / 'search.sqlite3'}?mode=ro",))
        placeholders = ", ".join("?" for _ in unique_pks)
        rows = database.execute(
            "SELECT messages.message_pk, COALESCE(metadata.preview, '') FROM messages "
            "LEFT JOIN search.message_metadata metadata USING (sha256) "
            f"WHERE messages.message_pk IN ({placeholders})",
            unique_pks,
        )
        previews = {message_pk: preview for message_pk, preview in rows}
        return [MessagePreview(message_pk=message_pk, preview=previews.get(message_pk, "")) for message_pk in unique_pks]
    finally:
        database.close()


def parsed_message(archive: Path, message_pk: int) -> tuple[bytes, Message]:
    raw = read_message_bytes(archive, message_pk)
    return raw, BytesParser(policy=policy.compat32).parsebytes(raw)


def describe_message(archive: Path, message_pk: int) -> MessageView:
    """Describe selectable body parts and attachments without returning payloads."""
    raw, message = parsed_message(archive, message_pk)
    headers = [HeaderField(name=name, value=decoded_header(str(value))) for name, value in message.items()]
    body_parts: list[BodyPart] = []
    attachments: list[AttachmentInfo] = []
    legacy_x_html = legacy_x_html_body(raw, message)
    for part_id, part in enumerate(_parts(message)):
        if legacy_x_html is not None and part is message:
            continue
        content_type = part.get_content_type()
        attachment = _is_gui_attachment(part)
        if attachment:
            payload = _payload_bytes(part)
            filename = safe_filename(part.get_filename(), part_id, content_type)
            preview = "image" if content_type.startswith("image/") else "pdf" if content_type == "application/pdf" else None
            attachments.append(
                AttachmentInfo(
                    part_id=part_id,
                    filename=filename,
                    content_type=content_type,
                    byte_length=len(payload),
                    inline=part.get_content_disposition() == "inline" or bool(part.get("Content-ID")),
                    preview=preview,
                    risky=is_risky(filename, content_type),
                )
            )
        elif content_type in {"text/plain", "text/html"}:
            label = "Plain Text" if content_type == "text/plain" else "HTML"
            body_parts.append(BodyPart(part_id=part_id, content_type=content_type, label=f"{label} — part {part_id}"))
    if legacy_x_html is not None:
        body_parts.append(BodyPart(part_id=LEGACY_X_HTML_PART_ID, content_type="text/html", label="HTML — legacy x-html"))
    body_parts.append(BodyPart(part_id=RAW_PART_ID, content_type="message/rfc822", label="Raw Source"))
    html_candidates = [
        (part.part_id, len(decoded_part(_part(message, part.part_id)).strip()))
        for part in body_parts
        if part.content_type == "text/html" and part.part_id >= 0
    ]
    html_part = max(html_candidates, key=lambda candidate: candidate[1])[0] if html_candidates else None
    if legacy_x_html is not None:
        html_part = LEGACY_X_HTML_PART_ID
    text_part = next((part.part_id for part in body_parts if part.content_type == "text/plain"), RAW_PART_ID)
    archive_path, source_locations = message_locations(archive, message_pk)
    date_utc, date_source = message_catalog_date(archive, message_pk)
    date_adjustment = None
    if date_source == "received-median":
        date_adjustment = DateAdjustment(
            date_header=decoded_header(str(message.get("Date", "(missing)"))),
            received_median_utc=date_utc,
            archive_routing_utc=date_utc,
        )
    return MessageView(
        message_pk=message_pk,
        subject=decoded_header(str(message.get("Subject", "(no subject)"))),
        date_source=date_source,
        date_adjustment=date_adjustment,
        headers=headers,
        body_parts=body_parts,
        preferred_part_id=html_part if html_part is not None else text_part,
        attachments=attachments,
        archive_path=archive_path,
        source_locations=source_locations,
    )


def message_catalog_date(archive: Path, message_pk: int) -> tuple[str, str]:
    database = sqlite3.connect(f"file:{archive / 'archive.sqlite3'}?mode=ro", uri=True)
    try:
        row = database.execute("SELECT date_utc, date_source FROM messages WHERE message_pk = ?", (message_pk,)).fetchone()
        if row is None:
            raise ValueError(f"unknown message {message_pk}")
        return str(row[0]), str(row[1])
    finally:
        database.close()


def message_locations(archive: Path, message_pk: int) -> tuple[str | None, list[SourceLocation]]:
    """Return source discoveries and the canonical archive mailbox location."""
    database = sqlite3.connect(f"file:{archive / 'archive.sqlite3'}?mode=ro", uri=True)
    try:
        archive_row = database.execute(
            "SELECT 'data/mbox/' || mbox_generations.filename, locations.byte_offset "
            "FROM locations JOIN mbox_generations USING (generation_pk) WHERE locations.message_pk = ?",
            (message_pk,),
        ).fetchone()
        rows = database.execute(
            "SELECT source_volumes.metadata_json, source_files.metadata_json, source_files.source_path, "
            "source_files.path_kind, source_files.source_plugin, observations.source_offset, "
            "observations.raw_sha256, observations.semantic_sha256 FROM observations "
            "JOIN source_files USING (source_file_pk) JOIN source_volumes USING (source_volume_pk) "
            "WHERE observations.message_pk = ? ORDER BY observations.observation_pk",
            (message_pk,),
        )
        locations = [
            _source_location(*row)
            for row in rows
        ]
        locations.sort(key=lambda item: (not item.preferred, item.origin, item.volume, item.path))
        archive_path = None if archive_row is None else location_display_path(str(archive_row[0]), archive_row[1])
        return archive_path, locations
    finally:
        database.close()


def _volume_label(metadata_json: str) -> str:
    metadata = json.loads(metadata_json)
    if not isinstance(metadata, dict):
        return "Unknown source volume"
    label = metadata.get("volume_label")
    mount_path = metadata.get("current_mount_path")
    return str(label or mount_path or "Unknown source volume")


def _source_display_path(metadata_json: str, source_path: str, path_kind: str) -> str:
    if path_kind != "provider":
        return source_path
    metadata = json.loads(metadata_json)
    if not isinstance(metadata, dict):
        return source_path
    return str(metadata.get("display_name") or source_path)


def location_display_path(path: str, offset: int | None) -> str:
    """Present a byte offset as metadata rather than as part of a pathname."""
    return path if not offset else f"{path}?offset={offset}"


def _copy_path(volume_metadata: str, source_path: str, path_kind: str, source_plugin: str) -> str | None:
    """Return a local source pathname suitable for the macOS pasteboard."""
    if source_plugin != "file-folder" or path_kind == "provider":
        return None
    try:
        metadata = json.loads(volume_metadata)
    except json.JSONDecodeError:
        return None
    if not isinstance(metadata, dict):
        return None
    mount_path = metadata.get("current_mount_path")
    if not isinstance(mount_path, str) or not Path(mount_path).is_absolute():
        return None
    source = Path(source_path)
    return str(source if source.is_absolute() else Path(mount_path) / source)


def _source_location(
    volume_metadata: str,
    container_metadata: str,
    source_path: str,
    path_kind: str,
    source_plugin: str,
    source_offset: int | None,
    raw_sha256: str,
    semantic_sha256: str | None,
) -> SourceLocation:
    try:
        relationship = SourceContainerMetadata.model_validate_json(container_metadata).relationship
    except ValueError:
        relationship = None
    cached_provider = None if relationship is None else relationship.upstream_plugin_kind
    cached = relationship is not None and relationship.role == "cache"
    preferred = source_plugin != "file-folder" and not cached
    provider = PROVIDER_LABELS.get(source_plugin, source_plugin)
    upstream = PROVIDER_LABELS.get(cached_provider, cached_provider) if cached_provider else None
    if preferred:
        origin = f"Direct {provider} source"
    elif cached:
        origin = f"Local cache of {upstream or 'upstream account'}"
    else:
        origin = "Local source" if source_plugin == "file-folder" else f"{provider} source"
    return SourceLocation(
        volume=_volume_label(volume_metadata),
        path=_source_display_path(container_metadata, source_path, path_kind),
        offset=source_offset,
        copy_path=_copy_path(volume_metadata, source_path, path_kind, source_plugin),
        raw_sha256=raw_sha256,
        semantic_sha256=semantic_sha256,
        origin=origin,
        preferred=preferred,
    )


def render_part(archive: Path, message_pk: int, part_id: int, allow_remote: bool = False) -> PartContent:
    raw, message = parsed_message(archive, message_pk)
    if part_id == RAW_PART_ID:
        return PartContent(part_id=part_id, kind="raw", content_type="message/rfc822", content=raw.decode("utf-8", "replace"))
    if part_id == LEGACY_X_HTML_PART_ID:
        content = legacy_x_html_body(raw, message)
        if content is None:
            raise ValueError("message has no legacy x-html body")
        content, blocked = safe_html(content, message, allow_remote)
        return PartContent(
            part_id=part_id,
            kind="html",
            content_type="text/html",
            content=content,
            remote_content_blocked=blocked,
        )
    part = _part(message, part_id)
    content_type = part.get_content_type()
    if content_type == "text/plain":
        return PartContent(part_id=part_id, kind="text", content_type=content_type, content=decoded_part(part))
    if content_type == "text/html":
        content, blocked = safe_html(decoded_part(part), message, allow_remote)
        return PartContent(
            part_id=part_id,
            kind="html",
            content_type=content_type,
            content=content,
            remote_content_blocked=blocked,
        )
    raise ValueError(f"MIME part {part_id} is not a displayable body part")


def attachment_content(archive: Path, message_pk: int, part_id: int) -> AttachmentContent:
    part, descriptor = _attachment(archive, message_pk, part_id)
    return AttachmentContent(
        filename=descriptor.filename,
        content_type=descriptor.content_type,
        content_base64=base64.b64encode(_payload_bytes(part)).decode("ascii"),
    )


def attachment_descriptor(archive: Path, message_pk: int, part_id: int) -> AttachmentDescriptor:
    """Return attachment naming and type metadata without base64-encoding its payload."""
    _, descriptor = _attachment(archive, message_pk, part_id)
    return descriptor


def write_message(archive: Path, message_pk: int, destination: Path) -> None:
    """Write an exact, verified RFC 5322 export outside the canonical archive."""
    _write_bytes(destination, read_message_bytes(archive, message_pk))


def write_messages_zip(archive: Path, message_pks: list[int], destination: Path) -> None:
    """Atomically write exact RFC 5322 exports as a ZIP created for an explicit drag."""
    unique = list(dict.fromkeys(message_pks))
    if len(unique) < 2:
        raise ValueError("a ZIP export requires at least two messages")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    temporary = Path(temporary_name)
    os.close(descriptor)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for message_pk in unique:
                output.writestr(export_filename(describe_message(archive, message_pk)), read_message_bytes(archive, message_pk))
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def write_attachment(archive: Path, message_pk: int, part_id: int, destination: Path) -> None:
    part, _ = _attachment(archive, message_pk, part_id)
    _write_bytes(destination, _payload_bytes(part))


def export_filename(view: MessageView) -> str:
    subject = re.sub(r"[^\w .()-]+", "_", view.subject, flags=re.UNICODE).strip(" .") or "message"
    return f"{subject[:80]}-{view.message_pk}.eml"


def safe_filename(filename: str | None, part_id: int, content_type: str) -> str:
    candidate = Path(str(filename or "")).name
    candidate = "".join(character for character in candidate if character >= " " and character not in "/\\:")
    if candidate:
        return candidate
    suffix = ".pdf" if content_type == "application/pdf" else ".bin"
    if content_type.startswith("image/"):
        suffix = "." + content_type.split("/", 1)[1].replace("jpeg", "jpg")
    return f"attachment-{part_id}{suffix}"


def is_risky(filename: str, content_type: str) -> bool:
    return Path(filename).suffix.casefold() in RISKY_SUFFIXES or content_type in {
        "application/java-archive",
        "application/vnd.apple.installer+xml",
        "application/x-executable",
        "application/x-mach-binary",
        "application/x-sh",
    }


def safe_html(value: str, message: Message, allow_remote: bool) -> tuple[str, bool]:
    """Return inert HTML with CID images resolved and remote loads policy-controlled."""
    soup = BeautifulSoup(value, "html.parser")
    for element in soup.find_all(tuple(BLOCKED_ELEMENTS)):
        element.decompose()
    cid_images = _cid_images(message)
    remote_blocked = False
    for element in soup.find_all(True):
        for attribute in list(element.attrs):
            if attribute.casefold().startswith("on") or attribute.casefold() in {"srcset", "formaction", "poster"}:
                del element.attrs[attribute]
        if element.name == "a" and element.get("href"):
            href = str(element["href"])
            if urlparse(href).scheme.casefold() not in {"http", "https", "mailto"}:
                del element.attrs["href"]
            else:
                element.attrs.pop("target", None)
                element["rel"] = "noopener noreferrer"
        if element.name == "img" and element.get("src"):
            source = str(element["src"])
            if source.casefold().startswith("cid:"):
                replacement = cid_images.get(source[4:].strip("<>").casefold())
                if replacement:
                    element["src"] = replacement
                else:
                    del element.attrs["src"]
            elif urlparse(source).scheme.casefold() in {"http", "https"}:
                if not allow_remote:
                    del element.attrs["src"]
                    remote_blocked = True
            elif not source.casefold().startswith("data:image/"):
                del element.attrs["src"]
    image_sources = "data: http: https:" if allow_remote else "data:"
    csp = soup.new_tag("meta")
    csp["http-equiv"] = "Content-Security-Policy"
    csp["content"] = f"default-src 'none'; img-src {image_sources}; style-src 'unsafe-inline'; font-src 'none'; media-src 'none'"
    if soup.head:
        soup.head.insert(0, csp)
    else:
        soup.insert(0, csp)
    return str(soup), remote_blocked


def legacy_x_html_body(raw: bytes, message: Message) -> str | None:
    """Decode a whole raw body wrapped by the pre-MIME Netscape x-html convention."""
    if message.is_multipart():
        return None
    separator = HEADER_BODY_SEPARATOR.search(raw)
    if separator is None:
        return None
    match = LEGACY_X_HTML.fullmatch(raw[separator.end():])
    if match is None:
        return None
    return decode_text(match.group("body"), message.get_content_charset()).value


def _parts(message: Message) -> list[Message]:
    return list(message.walk()) if message.is_multipart() else [message]


def _is_gui_attachment(part: Message) -> bool:
    content_type = part.get_content_type()
    return is_attachment(part) or content_type.startswith("image/") or content_type == "application/pdf"


def _part(message: Message, part_id: int) -> Message:
    parts = _parts(message)
    if part_id < 0 or part_id >= len(parts):
        raise ValueError(f"no MIME part {part_id}")
    return parts[part_id]


def _attachment(archive: Path, message_pk: int, part_id: int) -> tuple[Message, AttachmentDescriptor]:
    _, message = parsed_message(archive, message_pk)
    part = _part(message, part_id)
    content_type = part.get_content_type()
    if not _is_gui_attachment(part):
        raise ValueError(f"MIME part {part_id} is not an attachment")
    return part, AttachmentDescriptor(
        filename=safe_filename(part.get_filename(), part_id, content_type),
        content_type=content_type,
    )


def _payload_bytes(part: Message) -> bytes:
    payload = part.get_payload(decode=True)
    if isinstance(payload, bytes):
        return payload
    value = part.get_payload()
    if isinstance(value, str):
        return value.encode(part.get_content_charset() or "utf-8", "replace")
    return b""


def _cid_images(message: Message) -> dict[str, str]:
    images: dict[str, str] = {}
    for part in _parts(message):
        content_id = str(part.get("Content-ID", "")).strip("<>").casefold()
        if content_id and part.get_content_type().startswith("image/"):
            encoded = base64.b64encode(_payload_bytes(part)).decode("ascii")
            images[content_id] = f"data:{part.get_content_type()};base64,{encoded}"
    return images


def _write_bytes(destination: Path, value: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
