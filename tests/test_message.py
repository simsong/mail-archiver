"""Verify malformed metadata is recorded while message identity and dates stay stable."""

import hashlib
from datetime import datetime, timedelta, timezone
from email import policy
from email.header import Header
from email.parser import BytesParser
from pathlib import Path

from mailarchiver.message import parse_message


def test_received_fallback_uses_trimmed_utc_median() -> None:
    raw = b"\n".join(
        [
            b"Message-ID: <received@example>",
            b"From: sender@example.net",
            b"Received: by outlier.example; Fri, 2 Feb 2024 12:00:00 +0000",
            b"Received: by second.example; Thu, 1 Feb 2024 08:00:00 -0400",
            b"Received: by first.example; Thu, 1 Feb 2024 11:00:00 +0000",
            b"",
            b"body",
        ]
    )
    parsed = parse_message(raw, Path("/input/2020/message.eml"), None)
    assert parsed.date_source == "received"
    assert parsed.date_utc == "2024-02-01T12:00:00+00:00"


def test_date_more_than_two_days_from_received_median_is_replaced() -> None:
    """Requirement: a Date outlier is tagged and replaced by the trimmed Received median."""
    raw = b"\n".join(
        [
            b"Message-ID: <date-outlier@example>",
            b"From: sender@example.net",
            b"Date: Sat, 1 Jan 2000 12:00:00 +0000",
            b"Received: by old-outlier.example; Wed, 31 Jan 2024 12:00:00 +0000",
            b"Received: by first.example; Thu, 1 Feb 2024 12:00:00 +0000",
            b"Received: by second.example; Fri, 2 Feb 2024 08:00:00 -0400",
            b"Received: by new-outlier.example; Sun, 4 Feb 2024 12:00:00 +0000",
            b"",
            b"body",
        ]
    )

    parsed = parse_message(raw, Path("/input/2024/message.eml"), None)

    assert parsed.date_source == "received-median"
    assert parsed.date_utc == "2024-02-02T00:00:00+00:00"
    assert any(defect.field == "Date" and "used Received median" in defect.detail for defect in parsed.defects)


def test_date_within_two_days_of_received_median_is_retained() -> None:
    """Requirement: ordinary Date and Received clock differences do not replace Date."""
    raw = b"\n".join(
        [
            b"From: sender@example.net",
            b"Date: Sat, 3 Feb 2024 12:00:00 +0000",
            b"Received: by example.net; Thu, 1 Feb 2024 12:00:00 +0000",
            b"",
            b"body",
        ]
    )

    parsed = parse_message(raw, Path("/input/2024/message.eml"), None)

    assert parsed.date_source == "date"
    assert parsed.date_utc == "2024-02-03T12:00:00+00:00"


def test_source_date_resolves_undated_virtual_message() -> None:
    """Requirement: source metadata preserves undated provider mail without inventing path state."""
    raw = b"Message-ID: <source-date@example>\nFrom: sender@example.net\n\nbody\n"
    source_date = datetime(2024, 2, 1, 7, tzinfo=timezone(timedelta(hours=-5)))
    prior_date = datetime(2001, 1, 1, tzinfo=timezone.utc)

    parsed = parse_message(raw, None, prior_date, source_date)

    assert parsed.date_source == "source-fallback"
    assert parsed.date_utc == "2024-02-01T12:00:00+00:00"


def test_subject_is_decoded_and_unfolded() -> None:
    """Requirement: catalog subjects decode RFC 2047 words without modifying source bytes."""
    raw = b"\n".join(
        [
            b"Message-ID: <subject@example>",
            b"From: sender@example.net",
            b"Subject: =?utf-8?B?UmU6IE5ZVGltZXM6IFlvdSBXb27igJl0IFdhbnQgdG8gU2hhcmUgVGhpcyBS?=",
            b" =?utf-8?B?b2FzdGVkIENhdWxpZmxvd2Vy?=",
            b"Date: Thu, 1 Feb 2024 12:00:00 +0000",
            b"",
            b"body",
        ]
    )
    assert parse_message(raw, Path("/input/2024/message.eml"), None).subject == "Re: NYTimes: You Won’t Want to Share This Roasted Cauliflower"


def test_unencoded_korean_headers_use_the_declared_body_charset() -> None:
    """Requirement: legacy 8-bit Korean headers stay searchable without rewriting mail."""
    subject = "이렇게 하면 부자가 됩니다"
    raw = b"\n".join(
        [
            b"Message-ID: <legacy-korean@example>",
            b"From: " + "장나나".encode("euc-kr") + b" <jjanana@hanmail.net>",
            b"Subject: " + subject.encode("euc-kr"),
            b"Date: Sat, 17 May 2003 15:23:19 +0900",
            b"Content-Type: text/html; charset=ks_c_5601-1987",
            b"",
            b"<p>" + "최고의 기회".encode("euc-kr") + b"</p>",
        ]
    )

    parsed = parse_message(raw, Path("/input/2003/message.eml"), None)

    assert parsed.subject == subject
    assert parsed.sha256 == hashlib.sha256(raw).hexdigest()


def test_raw_8bit_received_header_resolves_date_without_altering_identity() -> None:
    """Requirement: malformed header encoding falls back without affecting preservation."""
    raw = b"\n".join(
        [
            b"Message-ID: <raw-received@example>",
            b"From: sender@example.net",
            b"Received: from caf\xe9.example; Thu, 1 Feb 2024 12:00:00 +0000",
            b"",
            b"body",
        ]
    )
    assert isinstance(BytesParser(policy=policy.compat32).parsebytes(raw)["Received"], Header)

    parsed = parse_message(raw, Path("/input/2020/message.eml"), None)

    assert parsed.date_source == "received"
    assert parsed.date_utc.startswith("2024-02-01")
    assert parsed.sha256 == hashlib.sha256(raw).hexdigest()


def test_raw_8bit_recipient_header_preserves_address() -> None:
    """Requirement: recipient metadata survives malformed display-name encoding."""
    raw = b"\n".join(
        [
            b"Message-ID: <raw-recipient@example>",
            b"From: sender@example.net",
            b"To: Jos\xe9 <recipient@example.net>",
            b"Cc: Copy <copy@example.net>",
            b"Bcc: Blind <blind@example.net>",
            b"Date: Thu, 1 Feb 2024 12:00:00 +0000",
            b"",
            b"body",
        ]
    )
    assert isinstance(BytesParser(policy=policy.compat32).parsebytes(raw)["To"], Header)

    parsed = parse_message(raw, Path("/input/2020/message.eml"), None)

    assert [(recipient.address, recipient.role.value) for recipient in parsed.recipients] == [
        ("blind@example.net", "bcc"),
        ("copy@example.net", "cc"),
        ("recipient@example.net", "to"),
    ]
    assert parsed.sha256 == hashlib.sha256(raw).hexdigest()


def test_malformed_base64_subject_falls_back_and_records_defect() -> None:
    """Requirement: broken RFC 2047 metadata never prevents raw-message preservation."""
    subject = "=?EUC-KR?B? KLGks00pvY?= trailing text"
    raw = b"\n".join(
        [
            b"Message-ID: <spam@example>",
            b"From: sender@example.net",
            f"Subject: {subject}".encode(),
            b"Date: Mon, 30 Jun 2003 19:50:44 -0400",
            b"",
            b"body",
        ]
    )

    parsed = parse_message(raw, Path("/input/2003/message.eml"), None)

    assert parsed.subject == subject
    assert [(defect.field, defect.detail) for defect in parsed.defects] == [
        ("Subject", "HeaderParseError: Base64 decoding error")
    ]
    assert parsed.sha256 == hashlib.sha256(raw).hexdigest()


def test_implausible_date_uses_received_fallback() -> None:
    """Requirement: an implausible year is not accepted for archive routing."""
    raw = b"\n".join(
        [
            b"Message-ID: <bad-year@example>",
            b"From: sender@example.net",
            b"Date: Tue, 14 Sep 0104 12:00:00 +0000",
            b"Received: by example.net; Mon, 30 Jun 2003 19:50:44 -0400",
            b"",
            b"body",
        ]
    )

    parsed = parse_message(raw, Path("/input/2003/message.eml"), None)

    assert parsed.date_source == "received"
    assert parsed.date_utc.startswith("2003-06-30")
    assert [(defect.field, defect.detail) for defect in parsed.defects] == [
        ("Date", "invalid or implausible date")
    ]


def test_configured_earliest_year_rejects_epoch_date_and_uses_stream_context() -> None:
    """Requirement: archive-specific chronology rejects a pre-email epoch-like Date."""
    raw = b"From: sender@example.net\nDate: Thu, 1 Jan 1970 00:00:00 +0000\n\nbody\n"
    prior = datetime(2002, 4, 5, 12, tzinfo=timezone.utc)

    parsed = parse_message(raw, Path("/input/2002/outbox.mbox"), prior, earliest_year=1983)

    assert parsed.date_source == "previous-message"
    assert parsed.date_utc == "2002-04-05T12:00:00+00:00"
    assert ("Date", "invalid or implausible date") in [
        (defect.field, defect.detail) for defect in parsed.defects
    ]


def test_configured_earliest_year_routes_resent_legacy_mail_by_received() -> None:
    """Requirement: a pre-bound Date can still route from a credible post-bound Received timestamp."""
    raw = (
        b"From: sender@example.net\n"
        b"Date: Sat, 1 Jan 1982 12:00:00 +0000\n"
        b"Received: by archive.example; Tue, 17 Sep 1985 12:00:00 +0000\n\nbody\n"
    )

    parsed = parse_message(raw, Path("/input/1985/rmail"), None, earliest_year=1983)

    assert parsed.date_source == "received"
    assert parsed.date_utc == "1985-09-17T12:00:00+00:00"


def test_configured_earliest_year_is_applied_after_utc_normalization() -> None:
    """Requirement: timezone offsets cannot move an accepted date outside the UTC year bound."""
    crosses_forward = (
        b"From: sender@example.net\n"
        b"Date: Fri, 31 Dec 1982 23:30:00 -0200\n\nbody\n"
    )
    crosses_backward = (
        b"From: sender@example.net\n"
        b"Date: Sat, 1 Jan 1983 01:00:00 +0200\n\nbody\n"
    )

    accepted = parse_message(crosses_forward, Path("/input/1983/mail.eml"), None, earliest_year=1983)
    rejected = parse_message(crosses_backward, Path("/input/1983/mail.eml"), None, earliest_year=1983)

    assert (accepted.date_source, accepted.date_utc) == ("date", "1983-01-01T01:30:00+00:00")
    assert (rejected.date_source, rejected.date_utc) == ("path-year", "1983-01-01T00:00:00+00:00")


def test_epoch_like_date_uses_latest_embedded_quoted_email_date() -> None:
    """Requirement: absent Received evidence permits a documented quoted-date fallback."""
    raw = (Path(__file__).parent / "data" / "quoted_date_message.eml").read_bytes()

    parsed = parse_message(raw, Path("/input/2003/message.eml"), None)

    assert parsed.date_source == "body-embedded"
    assert parsed.date_utc == "2003-01-01T12:21:00+00:00"
    assert any(defect.field == "Date" and "message body" in defect.detail for defect in parsed.defects)
    assert parsed.sha256 == hashlib.sha256(raw).hexdigest()


def test_embedded_body_date_does_not_override_credible_header_date() -> None:
    """Requirement: body dates are a fallback, never a replacement for a credible Date header."""
    raw = (Path(__file__).parent / "data" / "credible_date_with_quoted_body.eml").read_bytes()

    parsed = parse_message(raw, Path("/input/2024/message.eml"), None)

    assert (parsed.date_source, parsed.date_utc) == ("date", "2024-02-01T12:00:00+00:00")


def test_sender_header_falls_back_when_from_is_missing() -> None:
    """Requirement: a real Sender header supplies a missing From identity."""
    raw = b"Sender: fallback@example.net\nDate: Thu, 1 Feb 2024 12:00:00 +0000\n\nbody\n"

    parsed = parse_message(raw, Path("/input/2024/message.eml"), None)

    assert parsed.sender == "fallback@example.net"
    assert ("From", "missing or invalid; used Sender header") in [
        (defect.field, defect.detail) for defect in parsed.defects
    ]


def test_google_chat_event_uses_embedded_actor_name() -> None:
    """Requirement: recognized Google Chat events expose a named non-email actor."""
    raw = b"\n".join(
        [
            b"X-GM-THRID: 1234",
            b"",
            b"sender {",
            b'  full_name: "Simson Garfinkel"',
            b"}",
            b'conversation_id: "conversation"',
            b"timestamp: 1369948644835641",
            b'event_id: "event"',
        ]
    )

    parsed = parse_message(raw, Path("/input/2013/message.eml"), None)

    assert parsed.sender == "Simson Garfinkel (Google Chat)"
    assert ("From", "missing; used Google Chat body identity") in [
        (defect.field, defect.detail) for defect in parsed.defects
    ]


def test_arbitrary_body_sender_text_is_not_treated_as_identity() -> None:
    """Requirement: ordinary body text cannot masquerade as Google Chat metadata."""
    raw = b'Date: Thu, 1 Feb 2024 12:00:00 +0000\n\nsender { full_name: "Impostor" }\n'

    parsed = parse_message(raw, Path("/input/2024/message.eml"), None)

    assert parsed.sender == ""


def test_quoted_nested_mbox_from_header_is_recovered() -> None:
    """Requirement: quoted nested-MBOX status prefixes do not hide a valid From header."""
    raw = b"\n".join(
        [
            b"Status: R",
            b"X-Status:",
            b">From nested@example.net  Sat Nov 23 07:40:54 2002",
            b"Return-Path: <nested@example.net>",
            b"From: Nested Sender <nested@example.net>",
            b"Date: Sat, 23 Nov 2002 07:40:54 +0000",
            b"",
            b"body",
        ]
    )

    parsed = parse_message(raw, Path("/input/2002/message.eml"), None)

    assert parsed.sender == "nested@example.net"
    assert ("From", "used quoted embedded MBOX From header") in [
        (defect.field, defect.detail) for defect in parsed.defects
    ]


def test_crlf_body_mbox_envelope_cannot_supply_sender() -> None:
    """Requirement: envelope-looking body text cannot masquerade as an embedded sender."""
    raw = (
        b"Date: Thu, 1 Feb 2024 12:00:00 +0000\r\n"
        b"Subject: quoted example\r\n"
        b"\r\n"
        b">From nested@example.net Sat Nov 23 07:40:54 2002\r\n"
        b"From: Body Impostor <impostor@example.net>\r\n"
        b"\r\n"
        b"body\r\n"
    )

    parsed = parse_message(raw, Path("/input/2024/message.eml"), None)

    assert parsed.sender == ""
    assert ("From", "used quoted embedded MBOX From header") not in [
        (defect.field, defect.detail) for defect in parsed.defects
    ]
