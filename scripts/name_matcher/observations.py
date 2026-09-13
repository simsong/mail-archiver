# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Extract and store identity evidence without modifying the source archive."""

from __future__ import annotations

import hashlib
import sqlite3
import unicodedata
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
from pathlib import Path

from mailarchiver.message import decoded_header
from mailarchiver.search import preferred_body_text

from .models import HeaderObservation, HeaderRole, MessageEvidence, MessageMarker
from .signatures import extract_signature


EXTRACTOR_NAME = "mailarchiver-name-observations"
EXTRACTOR_VERSION = "0.1.0"
MARKER_HEADERS = (
    "List-Id",
    "List-Post",
    "List-Unsubscribe",
    "Mailing-List",
    "Precedence",
    "Auto-Submitted",
    "X-BeenThere",
    "Sender",
    "Reply-To",
)


def normalize_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def extract_message_evidence(
    raw: bytes,
    *,
    catalog_message_pk: int,
    observed_at: str,
    mbox_filename: str,
    expected_sha256: str | None = None,
) -> MessageEvidence:
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError("raw message SHA-256 does not match catalog")
    message = BytesParser(policy=policy.compat32).parsebytes(raw)
    observations: list[HeaderObservation] = []
    for role in HeaderRole:
        values = [str(value) for value in message.get_all(role.value, [])]
        for ordinal, (supplied_name, supplied_address) in enumerate(getaddresses(values)):
            address = supplied_address.strip().casefold()
            if not address:
                continue
            display_name = decoded_header(supplied_name).strip()
            observations.append(
                HeaderObservation(
                    role=role,
                    ordinal=ordinal,
                    address=address,
                    display_name=display_name,
                    normalized_name=normalize_name(display_name),
                )
            )
    markers = [
        MessageMarker(header_name=name.casefold(), ordinal=ordinal, value=decoded_header(str(value)).strip())
        for name in MARKER_HEADERS
        for ordinal, value in enumerate(message.get_all(name, []))
    ]
    return MessageEvidence(
        message_sha256=digest,
        catalog_message_pk=catalog_message_pk,
        observed_at=observed_at,
        mbox_filename=mbox_filename,
        header_observations=observations,
        markers=markers,
        signature=extract_signature(preferred_body_text(message)),
    )


def create_database(path: Path, schema_path: Path) -> sqlite3.Connection:
    path.touch(mode=0o600, exist_ok=False)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(schema_path.read_text(encoding="utf-8"))
    return connection


def store_message(connection: sqlite3.Connection, evidence: MessageEvidence) -> None:
    connection.execute(
        "INSERT INTO messages(catalog_message_pk, message_sha256, observed_at, mbox_filename) VALUES (?, ?, ?, ?)",
        (evidence.catalog_message_pk, evidence.message_sha256, evidence.observed_at, evidence.mbox_filename),
    )
    connection.executemany(
        "INSERT INTO header_observations(catalog_message_pk, role, ordinal, address, display_name, normalized_name) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            (
                evidence.catalog_message_pk,
                observation.role.value,
                observation.ordinal,
                observation.address,
                observation.display_name,
                observation.normalized_name,
            )
            for observation in evidence.header_observations
        ),
    )
    connection.executemany(
        "INSERT INTO message_markers(catalog_message_pk, header_name, ordinal, value) VALUES (?, ?, ?, ?)",
        (
            (evidence.catalog_message_pk, marker.header_name, marker.ordinal, marker.value)
            for marker in evidence.markers
        ),
    )
    if evidence.signature is None:
        return
    block = evidence.signature
    connection.execute(
        "INSERT INTO signature_blocks(catalog_message_pk, block_ordinal, start_line, end_line, method, confidence, "
        "text_sha256, block_text) VALUES (?, 0, ?, ?, ?, ?, ?, ?)",
        (
            evidence.catalog_message_pk,
            block.start_line,
            block.end_line,
            block.method.value,
            block.confidence,
            hashlib.sha256(block.text.encode("utf-8")).hexdigest(),
            block.text,
        ),
    )
    connection.executemany(
        "INSERT INTO signature_facts(catalog_message_pk, block_ordinal, fact_ordinal, line_ordinal, kind, value, "
        "normalized_value, confidence) VALUES (?, 0, ?, ?, ?, ?, ?, ?)",
        (
            (
                evidence.catalog_message_pk,
                fact.ordinal,
                fact.line_ordinal,
                fact.kind.value,
                fact.value,
                fact.normalized_value,
                fact.confidence,
            )
            for fact in block.facts
        ),
    )
