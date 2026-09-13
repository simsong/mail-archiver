# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Requirements: retain failure lines, exact message identity and read-only source provenance."""

import hashlib
import re
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from mailarchiver.__main__ import IngestRequest, run_ingest
from mailarchiver.ingest_diagnostics import FAILURE_PREVIEW_CHARACTERS, add_message_context, format_failure
from mailarchiver.mbox_framing import normalize_mbox_framing
from mailarchiver.plugin_api import SourceReference
from mailarchiver.ingest_status import read_ingest_history
from tests.test_plugin_loader import write_plugin


PARSER = '''
from mailarchiver.sources import FileParser, SourceMessage

class DiagnosticParser(FileParser):
    kind = "diagnostic"

    def recognizes(self, path):
        return path.suffix == ".diagnostic"

    def messages(self, source, start_offset=0):
        raw = source.path.read_bytes()
        yield SourceMessage(path=source.path, raw=raw, source_offset=17,
                            bytes_done=len(raw), bytes_total=len(raw),
                            mbox_envelope=b"From first Thu Apr 15 00:00:00 2004\\n")
        yield SourceMessage(path=source.path, raw=raw.replace(b"first", b"second"), source_offset=123,
                            bytes_done=len(raw), bytes_total=len(raw),
                            mbox_envelope=b"\\tFrom bad Thu Apr 15 00:00:00 2004\\n")

def create_plugin():
    return DiagnosticParser()
'''


@pytest.mark.parametrize("envelope_suffix", [b"", b"x" * 100_000 + b"ENVELOPE-PRIVATE-TAIL"], ids=["short", "large"])
def test_validation_failure_retains_validator_line_and_current_message(tmp_path: Path, envelope_suffix: bytes) -> None:
    """Real parser validation after successful publication persists the failing, not prior, message."""
    plugins = tmp_path / "plugins"
    invalid_envelope = b"\tFrom bad Thu Apr 15 00:00:00 2004\n"
    parser = PARSER.replace(repr(invalid_envelope).replace("'", '"'), repr(invalid_envelope + envelope_suffix))
    write_plugin(plugins, "file", "diagnostic", parser)
    source = tmp_path / "mail.diagnostic"
    raw = (b"Message-ID: <first@example.test>\nFrom: author@example.test\n"
           b"Date: Thu, 15 Apr 2004 00:00:00 +0000\n\nbody\n" + b"x" * 5000 + b"PRIVATE-TAIL")
    source.write_bytes(raw)
    owners = tmp_path / "owners.txt"
    owners.write_text("owner@example.test\n", encoding="utf-8")
    archive = tmp_path / "archive"
    with pytest.raises(ValidationError) as failure:
        run_ingest(IngestRequest(
            archive=archive, roots=[str(source)], owner_names_file=owners,
            scan_policy="not-scanned", plugin_dir=[plugins], workers=2,
        ), terminal=False)
    with sqlite3.connect(archive / "archive.sqlite3") as catalog:
        state, detail = catalog.execute("SELECT result, detail FROM ingest_runs").fetchone()
        assert state == "failed"
        assert catalog.execute("SELECT message_id_normalized FROM messages").fetchall() == [("first@example.test",)]
    assert "Source cursor: '123'" in detail
    assert str(source) in detail
    assert "<second@example.test>" in detail
    assert hashlib.sha256(raw.replace(b"first", b"second")).hexdigest() in detail
    assert "PRIVATE-TAIL" not in detail
    assert "input_value" not in detail
    assert len(detail) < 20_000
    assert "4096/" in detail
    assert "\\tFrom bad" in detail
    assert re.search(r'plugin_api.py", line \d+, in validate_mbox_envelope', detail)
    assert re.search(r'sources.py", line \d+, in messages', detail)
    assert "Validator origin for ('mbox_envelope',)" in detail
    history = read_ingest_history(archive)
    assert not history.errors
    assert history.statuses[0].failure_detail == detail
    assert source.read_bytes() == raw
    with pytest.raises(RuntimeError) as chained:
        raise RuntimeError("validation failed in worker") from failure.value
    rendered = format_failure(chained.value)
    assert "Caused by:" in rendered
    assert "Validator origin for ('mbox_envelope',)" in rendered
    assert "input_value" not in rendered
    assert "PRIVATE-TAIL" not in rendered


def test_iterator_failure_does_not_blame_previously_yielded_message(tmp_path: Path) -> None:
    """A generator failing between messages reports its source and actual raising frame."""
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "file", "diagnostic", PARSER[:PARSER.index('        yield SourceMessage(path=source.path, raw=raw.replace')]
                 + '        raise ValueError("iterator stopped")\n\ndef create_plugin():\n    return DiagnosticParser()\n')
    source = tmp_path / "mail.diagnostic"
    source.write_bytes(b"Message-ID: <first@example.test>\nDate: Thu, 15 Apr 2004 00:00:00 +0000\n\nbody\n")
    owners = tmp_path / "owners.txt"
    owners.write_text("owner@example.test\n", encoding="utf-8")
    archive = tmp_path / "archive"
    with pytest.raises(ValueError, match="iterator stopped"):
        run_ingest(IngestRequest(
            archive=archive, roots=[str(source)], owner_names_file=owners,
            scan_policy="not-scanned", plugin_dir=[plugins],
        ), terminal=False)
    detail = read_ingest_history(archive).statuses[0].failure_detail
    assert detail is not None
    assert str(source) in detail
    assert re.search(r'plugin.py", line \d+, in messages', detail)
    assert "Message SHA-256:" not in detail


def test_message_processing_failure_retains_raw_identity_and_traceback(tmp_path: Path) -> None:
    """A real date-resolution failure carries message evidence through a chained exception."""
    source = tmp_path / "undated.eml"
    raw = b"Message-ID: <undated@example.test>\nFrom: author@example.test\n\nbody\n"
    source.write_bytes(raw)
    owners = tmp_path / "owners.txt"
    owners.write_text("owner@example.test\n", encoding="utf-8")
    archive = tmp_path / "archive"
    with pytest.raises(RuntimeError, match="failed to parse"):
        run_ingest(IngestRequest(
            archive=archive, roots=[str(source)], owner_names_file=owners, scan_policy="not-scanned",
        ), terminal=False)
    detail = read_ingest_history(archive).statuses[0].failure_detail
    assert detail is not None
    assert hashlib.sha256(raw).hexdigest() in detail
    assert "<undated@example.test>" in detail
    assert re.search(r'message.py", line \d+, in ', detail)
    assert "no date or year path fallback" in detail
    assert source.read_bytes() == raw


def test_normalized_failure_retains_bounded_quoted_envelope() -> None:
    """Requirement: pre-observation failure context identifies both original framing lines."""
    outer = b"From real@example.test Thu Apr 15 00:20:49 2004\n"
    quoted = b">From quoted@example.test Thu Apr 15 00:20:49 2004 " + b"x" * 1000 + b"QUOTED-TAIL\n"
    record = normalize_mbox_framing(quoted + b"Subject: body\n\nbody\n", outer)
    source = SourceReference(plugin_kind="file-folder", source_id="fixture", native_id="source.mbox", display_name="source.mbox")
    error = ValueError("failure before observation")
    add_message_context(error, source, "fixture:0", b"invalid payload", record.envelope, record.normalization)
    detail = format_failure(error)
    assert "Source cursor: 'fixture:0'" in detail
    assert "byte offset" not in detail
    assert "Quoted envelope prefix: b'>From quoted@example.test" in detail
    assert "Original envelope prefix: b'From real@example.test" in detail
    assert "QUOTED-TAIL" not in detail
    assert record.normalization is not None
    assert record.normalization.source_raw_sha256 in detail


def test_source_metadata_and_cursor_cannot_bypass_preview_limits() -> None:
    """Requirement: plugin-provided identity is bounded and arbitrary provenance stays out of errors."""
    identity = "lookup-prefix-" + "x" * 100_000 + "IDENTITY-TAIL"
    source = SourceReference(
        plugin_kind=identity, source_id=identity, native_id=identity, display_name=identity,
        hierarchy=("HIERARCHY-SECRET",) * 1000, provenance_json="PROVENANCE-SECRET" + "x" * 100_000,
    )
    error = ValueError("plugin failed")
    add_message_context(error, source, "native:" + "x" * 100_000 + "CURSOR-TAIL", b"message", None)
    detail = format_failure(error)
    assert "lookup-prefix-" in detail
    assert "Source cursor: 'native:" in detail
    assert "characters)" in detail
    assert hashlib.sha256(b"message").hexdigest() in detail
    assert all(secret not in detail for secret in ("IDENTITY-TAIL", "CURSOR-TAIL", "PROVENANCE-SECRET", "HIERARCHY-SECRET"))
    assert len(detail) < 9000


@pytest.mark.parametrize("note_count", [1, 5])
def test_exception_messages_and_notes_cannot_bypass_report_limits(note_count: int) -> None:
    """Requirement: arbitrary summaries, chained errors, and repeated notes have bounded output."""
    error = RuntimeError("wrapper-prefix " + "x" * 100_000 + "SUMMARY-TAIL")
    error.__cause__ = ValueError("cause-prefix " + "x" * 100_000 + "CAUSE-TAIL")
    source = SourceReference(plugin_kind="fixture", source_id="lookup", display_name="source", native_id="message")
    add_message_context(error, source, "cursor", b"message", None)
    for _ in range(note_count):
        error.add_note("note-prefix " + "x" * 100_000 + "NOTE-TAIL")
    detail = format_failure(error)
    assert "wrapper-prefix" in detail and "note-prefix" in detail
    assert hashlib.sha256(b"message").hexdigest() in detail
    assert all(tail not in detail for tail in ("SUMMARY-TAIL", "CAUSE-TAIL", "NOTE-TAIL"))
    assert len(detail) < FAILURE_PREVIEW_CHARACTERS + 64
    assert "characters)" in detail
    if note_count == 1:
        assert "Caused by:" in detail and "cause-prefix" in detail


def test_parse_failure_persists_only_bounded_native_cursor(tmp_path: Path) -> None:
    """Requirement: real parse-error wrappers cannot duplicate an unbounded plugin cursor."""
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "file", "long-identity", '''
from mailarchiver.plugin_api import MailObject

class Parser:
    kind = "long-identity"
    def recognizes(self, probe):
        return probe.path.suffix == ".long-identity"
    def messages(self, container, checkpoint):
        yield MailObject(
            work_id=container.work_id,
            raw=b"Message-ID: <undated@example.test>\\nFrom: author@example.test\\n\\nbody\\n",
            source=container.source,
            cursor="native:" + "x" * 100_000 + "CURSOR-TAIL",
        )

def create_plugin():
    return Parser()
''')
    source = tmp_path / "undated.long-identity"
    source.write_bytes(b"fixture source")
    owners = tmp_path / "owners.txt"
    owners.write_text("owner@example.test\n", encoding="utf-8")
    archive = tmp_path / "archive"
    with pytest.raises(RuntimeError, match="failed to parse message"):
        run_ingest(IngestRequest(
            archive=archive, roots=[str(source)], owner_names_file=owners,
            scan_policy="not-scanned", plugin_dir=[plugins],
        ), terminal=False)
    detail = read_ingest_history(archive).statuses[0].failure_detail
    assert detail is not None
    with sqlite3.connect(archive / "archive.sqlite3") as catalog:
        assert catalog.execute("SELECT detail FROM ingest_runs").fetchone() == (detail,)
    assert str(source) in detail and "Source cursor: 'native:" in detail
    assert "CURSOR-TAIL" not in detail
    assert "no date or year path fallback" in detail
    assert re.search(r'message.py", line \d+, in ', detail)
    assert len(detail) < 10_000
    assert source.read_bytes() == b"fixture source"
