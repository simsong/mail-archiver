"""Fresh-process ingest, verification, and native-search acceptance test."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel

from mailarchiver.gui_app import GuiE2EReport
from e2e_tests.eicar_fixture import write_eicar_emlx


DATA = Path(__file__).parent / "data"
NORMAL_MESSAGE_COUNT = 107
PROCESSED_MESSAGE_COUNT = 110


class BuiltArchive(BaseModel):
    """Artifacts and captured output from one real ingest."""

    archive: Path
    source: Path
    infected_raw: bytes
    ingest_stdout: str
    ingest_stderr: str


@pytest.fixture(scope="module")
def built_archive(tmp_path_factory: pytest.TempPathFactory) -> BuiltArchive:
    """Ingest the committed corpus once, creating the antivirus sample only temporarily."""
    temporary = tmp_path_factory.mktemp("mailarchiver-e2e")
    source = temporary / "source"
    shutil.copytree(DATA / "source", source)
    owner_names = temporary / "owner-names.txt"
    owner_names.write_text("archive-owner@example.org\n", encoding="utf-8")
    infected_path = source / "Professional/Quarantine/runtime-infected.emlx"
    infected_raw = write_eicar_emlx(DATA / "infected.emlx.template", infected_path)
    archive = temporary / "archive"

    try:
        ingested = subprocess.run(
            [
                sys.executable,
                "-m",
                "mailarchiver",
                "--archive",
                str(archive),
                "ingest",
                "--owner-names-file",
                str(owner_names),
                "--clamav",
                "--index-attachments",
                "--workers",
                "2",
                str(source),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        infected_path.unlink(missing_ok=True)

    assert not infected_path.exists()
    assert ingested.returncode == 0, ingested.stdout + ingested.stderr
    return BuiltArchive(
        archive=archive,
        source=source,
        infected_raw=infected_raw,
        ingest_stdout=ingested.stdout,
        ingest_stderr=ingested.stderr,
    )


def test_fresh_ingest_builds_an_independently_verifiable_archive(built_archive: BuiltArchive) -> None:
    """Prove discovery, dedupe, quarantine, indexing, fixity, and standalone verification."""
    archive = built_archive.archive
    assert "completed:" in built_archive.ingest_stderr
    assert f"processed={PROCESSED_MESSAGE_COUNT}" in built_archive.ingest_stderr
    assert "infected=1" in built_archive.ingest_stderr
    assert "autosaved=1" in built_archive.ingest_stderr
    assert "seen_skipped=1" in built_archive.ingest_stderr
    assert "waiting for ClamAV startup:" in built_archive.ingest_stderr
    assert not any(path.name.endswith("runtime-infected.emlx") for path in built_archive.source.rglob("*"))
    assert (archive / "manifest-sha256.txt").is_file()
    assert (archive / "tagmanifest-sha256.txt").is_file()
    assert (archive / "mailbag.csv").is_file()
    assert "Mailarchiver-Message-Newline-Policy: preserve-source; add-final-LF-for-MBOX-framing\n" in (
        archive / "bag-info.txt"
    ).read_text(encoding="utf-8")

    catalog = sqlite3.connect(f"file:{archive / 'archive.sqlite3'}?mode=ro", uri=True)
    try:
        assert catalog.execute("SELECT count(*) FROM messages").fetchone() == (NORMAL_MESSAGE_COUNT + 1,)
        assert catalog.execute("SELECT count(*) FROM observations").fetchone() == (PROCESSED_MESSAGE_COUNT,)
        assert catalog.execute("SELECT count(*) FROM source_files").fetchone() == (7,)
        assert catalog.execute("SELECT count(*) FROM messages WHERE category = 'INFECTED'").fetchone() == (1,)
        assert catalog.execute(
            "SELECT count(*) FROM observations WHERE disposition = 'duplicate'"
        ).fetchone() == (1,)
        assert catalog.execute(
            "SELECT count(*) FROM observations WHERE disposition = 'autosave-excluded'"
        ).fetchone() == (1,)
        infected_hash = catalog.execute(
            "SELECT sha256 FROM messages WHERE message_id_normalized = 'infected-e2e@example'"
        ).fetchone()
        assert infected_hash == (hashlib.sha256(built_archive.infected_raw).hexdigest(),)
        no_newline = (DATA / "source/Professional/Projects/no-final-newline.eml").read_bytes()
        assert not no_newline.endswith(b"\n")
        assert catalog.execute(
            "SELECT sha256 FROM messages WHERE message_id_normalized = 'no-final-newline-e2e@example'"
        ).fetchone() == (hashlib.sha256(no_newline).hexdigest(),)
    finally:
        catalog.close()

    search = sqlite3.connect(f"file:{archive / 'search.sqlite3'}?mode=ro", uri=True)
    try:
        assert search.execute("SELECT count(*) FROM message_fts").fetchone() == (NORMAL_MESSAGE_COUNT,)
        assert search.execute("SELECT count(*) FROM attachment_fts").fetchone() == (1,)
        assert search.execute("SELECT count(*) FROM attachment_fts WHERE attachment_fts MATCH 'Appendixquartz'").fetchone() == (1,)
    finally:
        search.close()

    verified = subprocess.run(
        [sys.executable, "-I", str(archive / "verify_mail_archive.py"), str(archive)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert f"OK 2024-Archive1.mbox: {NORMAL_MESSAGE_COUNT} messages\n" in verified.stdout
    assert "OK INFECTED1.mbox: 1 messages\n" in verified.stdout
    assert verified.stdout.endswith("Archive integrity verified.\n")


@pytest.mark.skipif(sys.platform != "darwin", reason="native UI E2E requires macOS WKWebView")
def test_native_search_ui_end_to_end(built_archive: BuiltArchive, tmp_path: Path) -> None:
    """Drive the shipped HTML/JavaScript through the real pywebview Python bridge."""
    report = tmp_path / "gui-e2e.json"
    tested = subprocess.run(
        [
            sys.executable,
            "-m",
            "mailarchiver.gui_app",
            "--archive",
            str(built_archive.archive),
            "--e2e-test",
            str(report),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert tested.returncode == 0, tested.stdout + tested.stderr
    assert tested.stdout.endswith("GUI end-to-end test passed\n")
    result = GuiE2EReport.model_validate_json(report.read_text(encoding="utf-8"))
    assert result.passed
    assert len(result.checks) >= 30
    assert "saved-tiny.png" in result.exports
    assert "saved-review.command" in result.exports
    assert any(name.startswith("saved-Rich UI message-") and name.endswith(".eml") for name in result.exports)
    assert any(name.startswith("Rich UI message-") and name.endswith(".eml") for name in result.exports)
