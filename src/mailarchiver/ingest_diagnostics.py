# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Local import failure evidence without copying whole messages or traceback locals."""

import hashlib
import traceback

from pydantic import BaseModel, ValidationError

from .plugin_api import SourceReference
from .mbox_framing import MboxNormalization

MESSAGE_PREVIEW_BYTES = 4096
ENVELOPE_PREVIEW_BYTES = 512
IDENTITY_PREVIEW_CHARACTERS = 1024
EXCEPTION_PREVIEW_CHARACTERS = 2048
NOTE_PREVIEW_CHARACTERS = 32768
FAILURE_PREVIEW_CHARACTERS = 65536
VALIDATION_CONTEXT = "ctx"
VALIDATION_ERROR = "error"
VALIDATION_LOCATION = "loc"
VALIDATION_MESSAGE = "msg"
VALIDATION_TYPE = "type"


class SourceFailureIdentity(BaseModel):
    """Bounded lookup fields; arbitrary source provenance is not diagnostic input."""

    plugin_kind: str
    source_id: str
    native_id: str
    display_name: str


def _text_preview(value: str, limit: int = IDENTITY_PREVIEW_CHARACTERS) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"... ({len(value)} characters)"


def format_source_identity(source: SourceReference) -> str:
    """Avoid serializing potentially unbounded plugin provenance or hierarchy."""
    return SourceFailureIdentity(
        plugin_kind=_text_preview(source.plugin_kind), source_id=_text_preview(source.source_id),
        native_id=_text_preview(source.native_id), display_name=_text_preview(source.display_name),
    ).model_dump_json()


def add_message_context(
    error: BaseException, source: SourceReference, cursor: str, raw: bytes, envelope: bytes | None,
    normalization: MboxNormalization | None = None,
) -> None:
    """Attach exact provenance/hash and an escaped, bounded prefix of the failing input."""
    preview = raw[:MESSAGE_PREVIEW_BYTES]
    error.add_note(
        f"Source: {format_source_identity(source)}\n"
        f"Source cursor: {_text_preview(cursor)!r}\n"
        f"Message SHA-256: {hashlib.sha256(raw).hexdigest()}; bytes={len(raw)}\n"
        f"Message prefix ({len(preview)}/{len(raw)} bytes): {preview!r}\n"
        f"MBOX envelope prefix (up to {ENVELOPE_PREVIEW_BYTES} bytes): "
        f"{None if envelope is None else envelope[:ENVELOPE_PREVIEW_BYTES]!r}"
    )
    if normalization is not None:
        error.add_note(
            f"MBOX normalization: {normalization.rule}; source payload SHA-256: {normalization.source_raw_sha256}\n"
            f"Original envelope prefix: {normalization.original_envelope[:ENVELOPE_PREVIEW_BYTES]!r}\n"
            f"Quoted envelope prefix: {normalization.quoted_envelope[:ENVELOPE_PREVIEW_BYTES]!r}"
        )


def _exception_summary(error: BaseException) -> str:
    if isinstance(error, ValidationError):
        issues = error.errors(include_input=False, include_context=False, include_url=False)
        summary = f"ValidationError: {error.title}\n" + "\n".join(
            f"{issue[VALIDATION_LOCATION]!r}: {issue[VALIDATION_MESSAGE]} [{issue[VALIDATION_TYPE]}]"
            for issue in issues
        )
    else:
        summary = f"{type(error).__name__}: {error}"
    return _text_preview(summary, EXCEPTION_PREVIEW_CHARACTERS)


def format_failure(error: BaseException) -> str:
    """Retain exception chains and validator frames without Pydantic's input_value dump."""
    detail = _exception_summary(error) + "\n\n"
    pending = [("", error)]
    seen: set[int] = set()
    while pending and len(detail) < FAILURE_PREVIEW_CHARACTERS:
        label, current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        detail += label + "Traceback (most recent call last):\n"
        detail += "".join(traceback.format_tb(current.__traceback__))
        detail += _exception_summary(current) + "\n"
        for note in getattr(current, "__notes__", ()):
            detail += _text_preview(note, NOTE_PREVIEW_CHARACTERS) + "\n"
            if len(detail) >= FAILURE_PREVIEW_CHARACTERS:
                break
        if isinstance(current, ValidationError):
            for issue in current.errors(include_input=False, include_url=False):
                cause = issue.get(VALIDATION_CONTEXT, {}).get(VALIDATION_ERROR)
                if isinstance(cause, BaseException):
                    pending.append((f"\nValidator origin for {issue[VALIDATION_LOCATION]!r}:\n", cause))
        cause = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
        if cause is not None:
            label = "Caused by" if current.__cause__ is not None else "During handling of"
            pending.append((f"\n{label}:\n", cause))
    return _text_preview(detail, FAILURE_PREVIEW_CHARACTERS)
