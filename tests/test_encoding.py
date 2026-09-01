"""Verify conservative recovery of legacy MIME text and Unicode mojibake."""

import mailbox
from email import policy
from email.parser import BytesParser
from pathlib import Path

import pytest

from mailarchiver.encoding import (
    DETECTION_SAMPLE_BYTES,
    _candidate_encodings,
    _detected_encodings,
    _encoding_name,
    decode_text,
)
from mailarchiver.search import decoded_part, message_text


SOURCE_FIXTURE = Path(__file__).parent / "data" / "email-korean-bad-encoding.eml"
KOREAN_MESSAGE_ID = "<E17Vbwd-0006HW-00@sandbox.sandstorm.net>"


def test_declared_ks_c_5601_is_decoded_as_euc_kr() -> None:
    """Requirement: a valid legacy MIME charset produces readable derived text."""
    body = "부자가 되세요".encode("euc-kr")

    result = decode_text(body, "ks_c_5601-1987")

    assert result.value == "부자가 되세요"
    assert result.encoding == "euc_kr"
    assert "�" not in result.value


def test_undeclared_euc_kr_is_recovered_before_replacement() -> None:
    """Requirement: missing charset metadata must not make a recoverable body unreadable."""
    body = "한국어 메시지".encode("euc-kr")

    result = decode_text(body)

    assert result.value == "한국어 메시지"
    assert "�" not in result.value


def test_misdeclared_utf8_uses_readable_western_encoding() -> None:
    """Requirement: invalid declared UTF-8 falls back to a readable Western encoding."""
    result = decode_text(b"The world\x92s largest", "utf-8")

    assert result.value == "The world’s largest"
    assert result.encoding in {"cp1250", "cp1252"}
    assert result.defect is not None


def test_detector_candidates_precede_universal_single_byte_fallbacks() -> None:
    """Requirement: detector evidence precedes codecs that accept nearly every byte."""
    body = b"The world\x92s largest"
    detected = [
        canonical
        for name in _detected_encodings(body)
        if (canonical := _encoding_name(name)) is not None
    ]
    expected_prefix = list(dict.fromkeys(detected))

    assert expected_prefix
    assert _candidate_encodings(body)[: len(expected_prefix)] == expected_prefix


def test_large_payload_is_ranked_on_a_sample_before_one_full_fallback_decode() -> None:
    """Requirement: candidate ranking must not repeatedly decode a complete large MIME part."""

    class DecodeCountingBytes(bytes):
        calls: list[str]
        slices: list[slice]

        def __new__(cls, value: bytes) -> "DecodeCountingBytes":
            instance = super().__new__(cls, value)
            instance.calls = []
            instance.slices = []
            return instance

        def __getitem__(self, key: int | slice) -> int | bytes:
            if isinstance(key, slice):
                self.slices.append(key)
            return super().__getitem__(key)

        def decode(self, encoding: str = "utf-8", errors: str = "strict") -> str:
            self.calls.append(encoding)
            return super().decode(encoding, errors)

    body = DecodeCountingBytes("한국어메시지".encode("euc-kr") * 50_000)

    result = decode_text(body, "utf-8")

    assert result.value.startswith("한국어메시지")
    assert body.calls == ["utf-8", result.encoding]
    half = DETECTION_SAMPLE_BYTES // 2
    assert body.slices == [slice(None, half), slice(-half, None)]


def test_ftfy_repairs_mojibake_after_valid_utf8_decode() -> None:
    """Requirement: clear UTF-8 mojibake is repaired in derived text only."""
    result = decode_text("cafÃ©".encode("utf-8"), "utf-8")

    assert result.value == "café"
    assert result.encoding == "utf-8"
    assert result.repaired


def test_message_text_uses_declared_korean_charset() -> None:
    """Requirement: search and previews use the same source-preserving decoder."""
    raw = b"Content-Type: text/plain; charset=ks_c_5601-1987\n\n" + "안녕하세요".encode("euc-kr")

    assert "안녕하세요" in message_text(raw, index_attachments=False)


def test_supplied_korean_source_message_is_readable() -> None:
    """Requirement: the supplied historical message renders without replacement characters."""
    if not SOURCE_FIXTURE.is_file():
        pytest.skip("local 86 MB source fixture is not present")

    box = mailbox.mbox(SOURCE_FIXTURE, factory=None, create=False)
    try:
        raw = next(
            bytes(item)
            for item in box.itervalues()
            if KOREAN_MESSAGE_ID.encode() in bytes(item)
        )
    finally:
        box.close()

    message = BytesParser(policy=policy.compat32).parsebytes(raw)
    parts = [part for part in (message.walk() if message.is_multipart() else [message]) if part.get_content_maintype() == "text"]
    assert message.get_content_charset() == "ks_c_5601-1987"
    assert parts

    rendered = "\n".join(decoded_part(part) for part in parts)
    assert "최고의" in rendered
    assert sum("\uac00" <= char <= "\ud7a3" for char in rendered) > 100
    assert "�" not in rendered
