"""Recover readable derived text without changing source message bytes."""

from __future__ import annotations

import codecs
from collections.abc import Iterable
from typing import Final

from charset_normalizer import from_bytes
from ftfy import fix_encoding
from pydantic import BaseModel


DETECTION_SAMPLE_BYTES: Final = 512 * 1024
DETECTION_RESULTS: Final = 8
FALLBACK_ENCODINGS: Final = (
    "utf-8",
    "cp1252",
    "cp949",
    "euc-kr",
    "big5",
    "gb18030",
    "shift_jis",
    "iso-2022-jp",
    "iso-2022-kr",
    "iso-8859-1",
)


class DecodedText(BaseModel):
    """Text derived from a MIME payload, with recovery provenance."""

    value: str
    encoding: str
    repaired: bool = False
    defect: str | None = None


class _EncodingCandidate(BaseModel):
    encoding: str
    quality: float
    detector_rank: int


def _encoding_name(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return codecs.lookup(value.strip().strip('"')).name
    except (LookupError, TypeError):
        return None


def _sample(payload: bytes) -> bytes:
    if len(payload) <= DETECTION_SAMPLE_BYTES:
        return payload
    half = DETECTION_SAMPLE_BYTES // 2
    return payload[:half] + payload[-half:]


def _detected_encodings(payload: bytes) -> Iterable[str]:
    try:
        matches = list(from_bytes(_sample(payload)))
    except (LookupError, UnicodeError, ValueError):
        return ()
    return (match.encoding for match in matches[:DETECTION_RESULTS] if match.encoding)


def _quality(text: str) -> float:
    """Prefer printable text and a modest signal for CJK multibyte decoding."""
    if not text:
        return 0.0
    controls = sum(ord(char) < 32 and char not in "\t\r\n" for char in text)
    printable = sum(char.isprintable() or char.isspace() for char in text)
    cjk = sum(
        "\u3040" <= char <= "\u30ff"
        or "\u3400" <= char <= "\u9fff"
        or "\uac00" <= char <= "\ud7a3"
        for char in text
    )
    length = len(text)
    script_signal = 0.25 * cjk / length if cjk >= 4 and cjk / length >= 0.02 else 0.0
    return (printable - 4 * controls) / length + script_signal


def _candidate_encodings(payload: bytes) -> list[str]:
    names: list[str] = []
    for name in (*_detected_encodings(payload), *FALLBACK_ENCODINGS):
        canonical = _encoding_name(name)
        if canonical is not None and canonical not in names:
            names.append(canonical)
    return names


def _repair(text: str) -> tuple[str, bool]:
    try:
        repaired = fix_encoding(text)
    except (UnicodeError, ValueError):
        return text, False
    return repaired, repaired != text


def decode_text(payload: bytes, declared_encoding: str | None = None) -> DecodedText:
    """Decode a MIME payload strictly, recovering legacy encodings when needed.

    A declared charset wins when it can decode the bytes. Otherwise the
    candidates are scored on a bounded sample and the full payload is decoded
    with the best strict candidate. The final UTF-8 replacement fallback is
    retained only for genuinely undecodable data.
    """
    declared = _encoding_name(declared_encoding)
    declared_error: str | None = None
    if declared is not None:
        try:
            text = payload.decode(declared)
        except UnicodeError as error:
            declared_error = f"{declared_encoding}: {type(error).__name__}: {error}"
        else:
            repaired, changed = _repair(text)
            return DecodedText(value=repaired, encoding=declared, repaired=changed)
    elif declared_encoding:
        declared_error = f"{declared_encoding}: unknown charset"
    else:
        try:
            text = payload.decode("utf-8")
        except UnicodeError as error:
            declared_error = f"utf-8: {type(error).__name__}: {error}"
        else:
            repaired, changed = _repair(text)
            return DecodedText(value=repaired, encoding="utf-8", repaired=changed)

    sample = _sample(payload)
    candidates: list[_EncodingCandidate] = []
    for detector_rank, encoding in enumerate(_candidate_encodings(payload)):
        try:
            text = sample.decode(encoding)
        except UnicodeError:
            continue
        candidates.append(
            _EncodingCandidate(encoding=encoding, quality=_quality(text), detector_rank=detector_rank)
        )
    candidates.sort(key=lambda candidate: (candidate.quality, -candidate.detector_rank), reverse=True)
    for candidate in candidates:
        if candidate.quality <= 0.5:
            break
        try:
            text = payload.decode(candidate.encoding)
        except UnicodeError:
            continue
        repaired, changed = _repair(text)
        return DecodedText(value=repaired, encoding=candidate.encoding, repaired=changed, defect=declared_error)

    text = payload.decode("utf-8", "replace")
    repaired, changed = _repair(text)
    return DecodedText(
        value=repaired,
        encoding="utf-8",
        repaired=changed,
        defect=declared_error or "no usable character encoding",
    )
