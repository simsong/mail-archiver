"""Fresh-process ingest and independent archive-verification acceptance test."""

from __future__ import annotations

import hashlib
import sqlite3
import subprocess
import sys
from pathlib import Path


SAMPLE_MESSAGES = (
    (
        "normal.eml",
        b"Message-ID: <normal-e2e@example>\n"
        b"Date: Thu, 1 Feb 2024 12:00:00 +0000\n"
        b"From: sender@example.net\nSubject: normal\n\nnormal body\n",
    ),
    (
        "no-final-newline.eml",
        b"Message-ID: <no-final-newline-e2e@example>\n"
        b"Date: Fri, 2 Feb 2024 12:00:00 +0000\n"
        b"From: sender@example.net\nSubject: no final newline\n\nbody without final newline",
    ),
    (
        "mboxrd.eml",
        b"Message-ID: <mboxrd-e2e@example>\n"
        b"Date: Sat, 3 Feb 2024 12:00:00 +0000\n"
        b"From: sender@example.net\nSubject: mboxrd quoting\n\n"
        b"From unquoted body line\n>From literal quoted body line\n",
    ),
)


def test_fresh_ingest_builds_an_independently_verifiable_archive(tmp_path: Path) -> None:
    """Ingest representative source bytes, publish all fixity, then verify in isolation."""
    source = tmp_path / "source"
    source.mkdir()
    for filename, raw in SAMPLE_MESSAGES:
        (source / filename).write_bytes(raw)
    owner_names = tmp_path / "owner-names.txt"
    owner_names.write_text("archive-owner@example.org\n", encoding="utf-8")
    archive = tmp_path / "archive"

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
            "--workers",
            "2",
            str(source),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert ingested.returncode == 0, ingested.stdout + ingested.stderr
    assert "completed:" in ingested.stderr
    assert "processed=3" in ingested.stderr
    assert (archive / "manifest-sha256.txt").is_file()
    assert (archive / "tagmanifest-sha256.txt").is_file()
    assert (archive / "mailbag.csv").is_file()
    assert "Mailarchiver-Message-Newline-Policy: preserve-source; add-final-LF-for-MBOX-framing\n" in (
        archive / "bag-info.txt"
    ).read_text(encoding="utf-8")

    catalog = sqlite3.connect(f"file:{archive / 'archive.sqlite3'}?mode=ro", uri=True)
    try:
        assert catalog.execute("SELECT count(*) FROM messages").fetchone() == (3,)
        assert {row[0] for row in catalog.execute("SELECT sha256 FROM messages")} == {
            hashlib.sha256(raw).hexdigest() for _filename, raw in SAMPLE_MESSAGES
        }
    finally:
        catalog.close()

    verified = subprocess.run(
        [sys.executable, "-I", str(archive / "verify_mail_archive.py"), str(archive)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert "OK 2024-Archive1.mbox: 3 messages\n" in verified.stdout
    assert verified.stdout.endswith("Archive integrity verified.\n")
