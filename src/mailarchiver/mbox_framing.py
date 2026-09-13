# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Normalize explicitly double-processed MBOX framing, retaining source evidence."""

import hashlib
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MBOX_ENVELOPE = re.compile(
    br"From \S+ (?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) "
    br"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
    br"[ 0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9] (?:19|20)[0-9][0-9]"
)
QUOTED_ENVELOPE = re.compile(br">" + MBOX_ENVELOPE.pattern + br"(?: [^\r\n]+)?\r?\n")
BOGUS_SENDERS = {b"XXX", b"???@???"}


def is_complete_envelope(value: bytes) -> bool:
    """Accept one literal From line ending in LF or CRLF, without embedded line breaks."""
    content = value.removesuffix(b"\n").removesuffix(b"\r")
    return value.startswith(b"From ") and value.endswith(b"\n") and b"\n" not in content and b"\r" not in content


class MboxNormalization(BaseModel):
    """Original framing and source payload hash, separate from the normalized archive hash."""

    model_config = ConfigDict(frozen=True, extra="forbid", ser_json_bytes="base64", val_json_bytes="base64")

    format_version: Literal[1] = 1
    rule: Literal["inner-envelope-promoted", "quoted-envelope-to-x-from"]
    source_raw_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    original_envelope: bytes
    quoted_envelope: bytes


class NormalizedMboxRecord(BaseModel):
    raw: bytes
    envelope: bytes
    normalization: MboxNormalization | None = None


def normalize_mbox_framing(raw: bytes, envelope: bytes) -> NormalizedMboxRecord:
    """Convert only an immediate quoted delimiter into a literal X-From field."""
    quoted = QUOTED_ENVELOPE.match(raw)
    outer_fields = envelope.split(maxsplit=2)
    if quoted is None or not is_complete_envelope(envelope) or len(outer_fields) < 2:
        return NormalizedMboxRecord(raw=raw, envelope=envelope)
    inner = raw[:quoted.end()]
    outer_sender = outer_fields[1]
    inner_sender = inner.split(maxsplit=2)[1]
    promote = outer_sender in BOGUS_SENDERS and inner_sender not in BOGUS_SENDERS
    field = envelope[len(b"From "):] if promote else inner[len(b">From "):]
    return NormalizedMboxRecord(
        raw=b"X-From: " + field + raw[quoted.end():],
        envelope=inner[1:] if promote else envelope,
        normalization=MboxNormalization(
            rule="inner-envelope-promoted" if promote else "quoted-envelope-to-x-from",
            source_raw_sha256=hashlib.sha256(raw).hexdigest(),
            original_envelope=envelope,
            quoted_envelope=inner,
        ),
    )
