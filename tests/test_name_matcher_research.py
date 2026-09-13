# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Substantive tests for the private authoritative-name-index prototype."""

from __future__ import annotations

import hashlib
import mailbox
import sqlite3
from pathlib import Path

import pytest
import yaml

from mailarchiver.mbox import add_message
from scripts.name_matcher.ai import AIProvider, AIResult, correlate_results, make_request
from scripts.name_matcher.build_observations import build_database, publish_evidence
from scripts.name_matcher.models import SignatureFactKind, SignatureMethod
from scripts.name_matcher.observations import extract_message_evidence
from scripts.name_matcher.signatures import extract_signature


ARCHIVE_SCHEMA = Path(__file__).parents[1] / "src" / "mailarchiver" / "sql" / "V1__archive.sql"


def test_signature_delimiter_extracts_contact_evidence_but_not_quoted_reply() -> None:
    """Requirement: signature evidence is local, bounded, typed, and excludes quoted replies."""
    body = """Please send the revised draft.

--\u0020
Simson L. Garfinkel
Research Director
simson@example.test | +1 (617) 555-0102
https://example.test/people/simson

On Tuesday, Someone wrote:
> --
> Wrong Person
> wrong@example.test
"""

    signature = extract_signature(body)

    assert signature is not None
    assert signature.method is SignatureMethod.DELIMITER
    assert "wrong@example.test" not in signature.text
    facts = {(fact.kind, fact.normalized_value) for fact in signature.facts}
    assert (SignatureFactKind.NAME_CANDIDATE, "simson l. garfinkel") in facts
    assert (SignatureFactKind.EMAIL, "simson@example.test") in facts
    assert (SignatureFactKind.PHONE, "+16175550102") in facts
    assert (SignatureFactKind.URL, "https://example.test/people/simson") in facts


def test_header_name_scope_excludes_subject_and_ordinary_body() -> None:
    """Requirement: searchable name observations come only from address headers; signatures are separate evidence."""
    raw = (
        b"From: Header Name <sender@example.test>\r\n"
        b"To: Recipient Name <recipient@example.test>\r\n"
        b"List-Id: Research List <research.example.test>\r\n"
        b"Subject: Body Only Name\r\n\r\n"
        b"Body Only Name appears here, but this is not a signature.\r\n"
    )

    evidence = extract_message_evidence(
        raw,
        catalog_message_pk=7,
        observed_at="2024-01-01T00:00:00+00:00",
        mbox_filename="2024-Archive1.mbox",
    )

    assert {(item.address, item.display_name) for item in evidence.header_observations} == {
        ("sender@example.test", "Header Name"),
        ("recipient@example.test", "Recipient Name"),
    }
    assert all(item.display_name != "Body Only Name" for item in evidence.header_observations)
    assert [(item.header_name, item.value) for item in evidence.markers] == [
        ("list-id", "Research List <research.example.test>")
    ]
    assert evidence.signature is None


def _one_message_archive(path: Path, raw: bytes) -> None:
    (path / "data" / "mbox").mkdir(parents=True)
    catalog = sqlite3.connect(path / "archive.sqlite3")
    catalog.executescript(ARCHIVE_SCHEMA.read_text(encoding="utf-8"))
    sender_pk = catalog.execute("INSERT INTO email_addresses(address) VALUES ('sender@example.test')").lastrowid
    digest = hashlib.sha256(raw).hexdigest()
    message_pk = catalog.execute(
        "INSERT INTO messages(message_id_normalized, sha256, sender_address_pk, subject, date_utc, date_source, "
        "category) VALUES ('one@example.test', ?, ?, 'hello', '2024-01-01T00:00:00+00:00', 'date', 'Archive')",
        (digest, sender_pk),
    ).lastrowid
    mbox_file = path / "data" / "mbox" / "2024-Archive1.mbox"
    box = mailbox.mbox(mbox_file, create=True)
    location = add_message(box, mbox_file, raw)
    box.close()
    generation_pk = catalog.execute(
        "INSERT INTO mbox_generations(filename, sha256, message_count, byte_count) VALUES (?, '', 1, ?)",
        (mbox_file.name, mbox_file.stat().st_size),
    ).lastrowid
    catalog.execute(
        "INSERT INTO locations(message_pk, generation_pk, byte_offset, byte_length) VALUES (?, ?, ?, ?)",
        (message_pk, generation_pk, location.byte_offset, location.byte_length),
    )
    catalog.commit()
    catalog.close()


def test_builder_reads_verified_mbox_and_creates_private_reproducible_derivative(tmp_path: Path) -> None:
    """Requirement: the prototype reads verified canonical bytes and writes only a new private derivative."""
    archive = tmp_path / "archive #100% café"
    raw = (
        b"Message-ID: <one@example.test>\r\n"
        b"From: Sim Example <sender@example.test>\r\n"
        b"To: Reader <reader@example.test>\r\n\r\n"
        b"Thanks.\r\n\r\n-- \r\nSim Example\r\nsim.alt@example.test\r\n"
    )
    _one_message_archive(archive, raw)
    archive_digest = hashlib.sha256((archive / "data" / "mbox" / "2024-Archive1.mbox").read_bytes()).hexdigest()
    output = tmp_path / "evidence.sqlite3"

    summary = build_database(archive, output, workers=1, limit=None)

    assert summary.messages == 1
    assert summary.signature_blocks == 1
    assert output.stat().st_mode & 0o777 == 0o600
    summary_path = output.with_suffix(".sqlite3.summary.yaml")
    assert summary_path.stat().st_mode & 0o777 == 0o600
    recorded_summary = yaml.safe_load(summary_path.read_text(encoding="utf-8"))
    run_id = recorded_summary.pop("run_id")
    assert recorded_summary == summary.model_dump()
    assert hashlib.sha256((archive / "data" / "mbox" / "2024-Archive1.mbox").read_bytes()).hexdigest() == archive_digest
    with sqlite3.connect(output) as database:
        assert database.execute("SELECT run_id FROM extraction_runs").fetchone() == (run_id,)
        assert database.execute(
            "SELECT role, address, display_name FROM header_observations ORDER BY role"
        ).fetchall() == [
            ("from", "sender@example.test", "Sim Example"),
            ("to", "reader@example.test", "Reader"),
        ]
        assert database.execute(
            "SELECT normalized_value FROM signature_facts WHERE kind = 'email'"
        ).fetchall() == [("sim.alt@example.test",)]
        assert database.execute("PRAGMA foreign_key_check").fetchall() == []
    with pytest.raises(FileExistsError):
        build_database(archive, output, workers=1, limit=None)
    with pytest.raises(ValueError, match="outside the source"):
        build_database(archive, archive / "evidence.sqlite3", workers=1, limit=None)
    assert not (archive / "evidence.sqlite3").exists()


@pytest.mark.parametrize("failure", ["database-exists", "summary-exists", "summary-missing"])
def test_evidence_publication_failure_preserves_competitors_and_allows_retry(tmp_path: Path, failure: str) -> None:
    """Requirement: a failed pair publication leaves no new output or overwritten competitor."""
    staged = tmp_path / "staged.sqlite3"
    with sqlite3.connect(staged) as database:
        database.execute("CREATE TABLE evidence (value TEXT)")
        database.execute("INSERT INTO evidence VALUES ('retained')")
    staged_summary = tmp_path / "staged.yaml"
    staged_summary.write_text("messages: 1\n", encoding="utf-8")
    output = tmp_path / "evidence.sqlite3"
    summary = tmp_path / "evidence.sqlite3.summary.yaml"
    competitor = output if failure == "database-exists" else summary
    if failure == "summary-missing":
        staged_summary.unlink()
    else:
        competitor.write_bytes(b"another publisher's output")

    with pytest.raises(OSError):
        publish_evidence(staged, staged_summary, output, summary)

    if failure == "summary-missing":
        assert not output.exists()
        assert not summary.exists()
        staged_summary.write_text("messages: 1\n", encoding="utf-8")
    else:
        assert competitor.read_bytes() == b"another publisher's output"
        assert not (summary if competitor == output else output).exists()
        competitor.unlink()
    publish_evidence(staged, staged_summary, output, summary)
    assert output.read_bytes() == staged.read_bytes()
    assert summary.read_bytes() == staged_summary.read_bytes()


def test_deferred_ai_results_are_correlated_independently_of_return_order() -> None:
    """Requirement: asynchronous provider results map back to the exact candidate pair."""
    first = make_request(
        provider=AIProvider.GEMINI,
        model="example-model",
        prompt_version="v1",
        identity_1="a@example.test",
        identity_2="b@example.test",
        prompt="Are these the same person?",
    )
    second = make_request(
        provider=AIProvider.GEMINI,
        model="example-model",
        prompt_version="v1",
        identity_1="c@example.test",
        identity_2="d@example.test",
        prompt="Are these the same person?",
    )
    returned = [
        AIResult(request_id=second.request_id, provider=AIProvider.GEMINI, status="succeeded", response="no"),
        AIResult(request_id=first.request_id, provider=AIProvider.GEMINI, status="succeeded", response="yes"),
    ]

    correlated = correlate_results([first, second], returned)

    assert [(request.identity_1, result.response) for request, result in correlated] == [
        ("a@example.test", "yes"),
        ("c@example.test", "no"),
    ]
    duplicate = make_request(
        provider=AIProvider.GEMINI,
        model="example-model",
        prompt_version="v1",
        identity_1="a@example.test",
        identity_2="b@example.test",
        prompt="Are these the same person?",
    )
    assert duplicate.request_id == first.request_id
    with pytest.raises(ValueError, match="duplicate request IDs"):
        correlate_results([first, duplicate], [returned[1]])
