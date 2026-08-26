"""Exercise complete ingest, recovery, reporting, and failure behavior as subprocesses."""

from __future__ import annotations

import argparse
import hashlib
import json
import mailbox
import os
import signal
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path
from shutil import copy, copytree

import pytest

from mailarchiver.__main__ import nonnegative_integer, positive_integer, report_years
from mailarchiver.catalog import address_pk, create_catalog, create_search
from mailarchiver.layout import mbox_directory
from mailarchiver.mbox import add_message
from mailarchiver.source_volume import METADATA_CURRENT_MOUNT_PATH
from mailarchiver.standalone_verify import semantic_bytes


TEST_DATA = Path(__file__).parent / "data"
CLAMD_ENV = "MAILARCHIVER_CLAMD"
CLAMD_SOCKET_ENV = "MAILARCHIVER_CLAMD_SOCKET"


def emlx_message_bytes(path: Path) -> bytes:
    with path.open("rb") as source:
        size = int(source.readline())
        return source.read(size)


@pytest.fixture
def source_mail(tmp_path: Path) -> tuple[Path, dict[str, bytes]]:
    source = tmp_path / "source"
    source.mkdir()
    mbox = source / "three_messages.mbox"
    copy(TEST_DATA / "three_messages.mbox", mbox)
    copytree(TEST_DATA / "emlx_maildir", source / "emlx_maildir")
    messages = mailbox_message_bytes(mbox)
    return source, {
        "sent": messages[0],
        "collision_one": messages[2],
        "collision_two": emlx_message_bytes(source / "emlx_maildir/2024/001-collision.emlx"),
        "infected": emlx_message_bytes(source / "emlx_maildir/2024/infected/003-infected.emlx"),
    }


def run_ingest(source: Path, archive: Path, owner_names: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
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
            str(source),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def mailbox_message_bytes(path: Path) -> list[bytes]:
    box = mailbox.mbox(path, factory=None, create=False)
    try:
        return [box.get_bytes(key, from_=False) for key in box.iterkeys()]
    finally:
        box.close()


def assert_success(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, result.stdout + result.stderr


def test_cli_numeric_ranges_fail_early() -> None:
    """Requirement: nonsensical concurrency, report counts, and year ranges fail at parsing."""
    assert positive_integer("1") == 1
    assert nonnegative_integer("0") == 0
    assert report_years("2020-2024") == (2020, 2024)
    with pytest.raises(argparse.ArgumentTypeError, match="greater than zero"):
        positive_integer("0")
    with pytest.raises(argparse.ArgumentTypeError, match="zero or positive"):
        nonnegative_integer("-1")
    with pytest.raises(ValueError, match="ascending"):
        report_years("2024-2020")


def catalog_source_path(catalog: sqlite3.Connection, path: Path) -> str:
    """Return the recorded volume-relative path that reconstructs *path*."""
    resolved = path.resolve()
    matches = []
    for source_path, metadata_json in catalog.execute(
        "SELECT source_files.source_path, source_volumes.metadata_json FROM source_files "
        "JOIN source_volumes USING (source_volume_pk)"
    ):
        metadata = json.loads(metadata_json)
        mount_path = Path(metadata[METADATA_CURRENT_MOUNT_PATH])
        if (mount_path / source_path).resolve() == resolved:
            matches.append(source_path)
    assert len(matches) == 1
    return str(matches[0])


def test_ingest_routes_preserves_and_indexes_messages(
    source_mail: tuple[Path, dict[str, bytes]], tmp_path: Path
) -> None:
    """Requirements: canonical preservation, dedupe, routing, FTS, and audit log."""
    source, raw = source_mail
    archive = tmp_path / "archive"
    owner_names = Path(__file__).parents[1] / "owner-names.txt"

    result = run_ingest(source, archive, owner_names)
    assert_success(result)
    assert "completed:" in result.stderr
    assert "processed=6" in result.stderr
    assert "files_processed=4" in result.stderr
    assert "started:" in result.stderr
    assert "waiting for ClamAV startup:" in result.stderr
    assert "discovering sources:" in result.stderr
    assert "ingesting:" in result.stderr
    startup = result.stderr.index("waiting for ClamAV startup:")
    ingesting = result.stderr.index("ingesting:")
    assert startup < ingesting
    assert "processed=0 active_workers=0" in result.stderr[startup:ingesting]
    assert "workers=" in result.stderr
    assert "peak_workers=4" in result.stderr
    assert "seen_skipped=" in result.stderr
    assert "overall_percent=100.0%" in result.stderr
    assert "files_total=4" in result.stderr
    assert "eta=0s" in result.stderr
    assert "  year    sent    received    people" in result.stdout
    assert "  2024       1           2" in result.stdout
    assert "top senders" in result.stdout

    assert mailbox_message_bytes(mbox_directory(archive) / "2024-Sent1.mbox") == [raw["sent"]]
    assert Counter(mailbox_message_bytes(mbox_directory(archive) / "2024-Archive1.mbox")) == Counter(
        (raw["collision_one"], raw["collision_two"])
    )
    assert len(mailbox_message_bytes(mbox_directory(archive) / "INFECTED1.mbox")) == 1
    assert all(
        b"autosave@example" not in item
        for item in mailbox_message_bytes(mbox_directory(archive) / "2024-Sent1.mbox")
    )

    catalog = sqlite3.connect(archive / "archive.sqlite3")
    try:
        assert catalog.execute("SELECT count(*) FROM messages").fetchone() == (4,)
        assert catalog.execute("SELECT count(*) FROM locations").fetchone() == (4,)
        assert catalog.execute("SELECT count(*) FROM observations").fetchone() == (6,)
        assert catalog.execute("SELECT count(*) FROM source_files").fetchone() == (4,)
        assert catalog.execute("SELECT count(*) FROM source_volumes").fetchone() == (1,)
        assert catalog.execute(
            "SELECT count(*) FROM observations WHERE disposition = 'duplicate' AND message_pk IS NULL"
        ).fetchone() == (0,)
        raw_hash, semantic_hash = catalog.execute(
            "SELECT raw_sha256, semantic_sha256 FROM observations "
            "JOIN messages USING (message_pk) WHERE messages.category = 'Sent'"
        ).fetchone()
        assert raw_hash == hashlib.sha256(raw["sent"]).hexdigest()
        assert semantic_hash == hashlib.sha256(semantic_bytes(raw["sent"])).hexdigest()
        fingerprints = {
            path: (modified_at_ns, byte_length, sha256)
            for path, modified_at_ns, byte_length, sha256 in catalog.execute(
                "SELECT source_files.source_path, modified_at_ns, byte_length, sha256 FROM source_files"
            )
        }
        for path in source.rglob("*"):
            if path.is_file():
                stat = path.stat()
                assert fingerprints[catalog_source_path(catalog, path)] == (
                    stat.st_mtime_ns,
                    stat.st_size,
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
        assert catalog.execute(
            "SELECT date_source FROM messages WHERE message_id_normalized = 'infected@example'"
        ).fetchone() == ("path-year",)
        assert catalog.execute(
            "SELECT disposition FROM observations WHERE detail = 'X-Apple-Auto-Saved'"
        ).fetchone() == ("autosave-excluded",)
        assert catalog.execute(
            "SELECT count(*) FROM messages WHERE message_id_normalized = 'collision@example'"
        ).fetchone() == (2,)
        assert catalog.execute(
            "SELECT sha256 FROM messages WHERE message_id_normalized = 'infected@example'"
        ).fetchone() == (hashlib.sha256(raw["infected"]).hexdigest(),)
        assert catalog.execute("SELECT count(*) FROM email_addresses").fetchone() == (3,)
        assert catalog.execute("SELECT count(*) FROM messages JOIN email_addresses ON email_addresses.address_pk = messages.sender_address_pk WHERE address = 'sender@example.net'").fetchone() == (3,)
    finally:
        catalog.close()

    search = sqlite3.connect(archive / "search.sqlite3")
    try:
        infected_sha256 = hashlib.sha256(raw["infected"]).hexdigest()
        assert search.execute("SELECT count(*) FROM message_fts").fetchone() == (3,)
        assert search.execute("SELECT count(*) FROM message_fts WHERE sha256 = ?", (infected_sha256,)).fetchone() == (0,)
    finally:
        search.close()

    refreshed = subprocess.run(
        [sys.executable, "-m", "mailarchiver", "--archive", str(archive), "refresh-index"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert_success(refreshed)
    search = sqlite3.connect(archive / "search.sqlite3")
    try:
        assert search.execute("SELECT count(*) FROM message_fts").fetchone() == (3,)
        assert search.execute("SELECT count(*) FROM message_fts WHERE sha256 = ?", (infected_sha256,)).fetchone() == (0,)
    finally:
        search.close()
    assert (archive / "integrity" / "2024-Archive1.mbox.integrity").is_file()
    assert (archive / "integrity" / "INFECTED1.mbox.integrity").is_file()
    assert (archive / "manifest-sha256.txt").is_file()
    assert (archive / "tagmanifest-sha256.txt").is_file()
    assert (archive / "mailbag.csv").is_file()
    assert (archive / "verify_mail_archive.py").is_file()
    assert not (archive / ".mailarchiver-pending.json").exists()
    verified = subprocess.run(
        [sys.executable, "-I", str(archive / "verify_mail_archive.py"), str(archive)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert_success(verified)
    assert "Archive integrity verified." in verified.stdout


def test_refresh_index_excludes_quarantine_mailboxes(tmp_path: Path) -> None:
    """Requirement: FTS rebuild excludes infected and malformed quarantine mail."""
    archive = tmp_path / "archive"
    archive.mkdir()
    mbox_directory(archive).mkdir(parents=True)
    messages = {
        "2024-Archive1.mbox": (
            b"Message-ID: <normal@example>\nSubject: normal\n\nnormal body\nFrom ordinary\n>From literal\n"
        ),
        "INFECTED1.mbox": b"Message-ID: <infected@example>\nSubject: infected\n\ninfected body\n",
        "MALFORMED1.mbox": b"Message-ID: <malformed@example>\nSubject: malformed\n\nmalformed body\n",
    }
    locations = {}
    for filename, raw in messages.items():
        path = mbox_directory(archive) / filename
        box = mailbox.mbox(path, create=True)
        try:
            locations[filename] = add_message(box, path, raw)
        finally:
            box.close()
    catalog = create_catalog(archive / "archive.sqlite3")
    try:
        sender_pk = address_pk(catalog, "sender@example.net")
        for filename, raw in messages.items():
            category = "Archive" if filename.startswith("2024-") else filename.split("1.", 1)[0]
            message_pk = catalog.execute(
                "INSERT INTO messages(message_id_normalized, sha256, sender_address_pk, subject, date_utc, date_source, category) "
                "VALUES (?, ?, ?, '', '2024-01-01T00:00:00+00:00', 'date', ?)",
                (filename, hashlib.sha256(raw).hexdigest(), sender_pk, category),
            ).lastrowid
            generation_pk = catalog.execute(
                "INSERT INTO mbox_generations(filename, sha256, message_count, byte_count) VALUES (?, '', 1, ?)",
                (filename, (mbox_directory(archive) / filename).stat().st_size),
            ).lastrowid
            location = locations[filename]
            catalog.execute(
                "INSERT INTO locations(message_pk, generation_pk, byte_offset, byte_length) VALUES (?, ?, ?, ?)",
                (message_pk, generation_pk, location.byte_offset, location.byte_length),
            )
        catalog.commit()
    finally:
        catalog.close()

    refreshed = subprocess.run(
        [sys.executable, "-m", "mailarchiver", "--archive", str(archive), "refresh-index"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert_success(refreshed)
    search = sqlite3.connect(archive / "search.sqlite3")
    try:
        assert search.execute("SELECT sha256 FROM message_fts").fetchall() == [
            (hashlib.sha256(messages["2024-Archive1.mbox"]).hexdigest(),)
        ]
    finally:
        search.close()


def test_refresh_index_preserves_prior_index_when_mbox_and_catalog_disagree(tmp_path: Path) -> None:
    """Requirement: FTS replacement fails closed unless every normal MBOX row is catalogued."""
    archive = tmp_path / "archive"
    mbox_directory(archive).mkdir(parents=True)
    path = mbox_directory(archive) / "2024-Archive1.mbox"
    box = mailbox.mbox(path, create=True)
    try:
        box.add(b"Message-ID: <uncatalogued@example>\n\nbody\n")
        box.flush()
    finally:
        box.close()
    create_catalog(archive / "archive.sqlite3").close()
    old_search = create_search(archive / "search.sqlite3")
    old_search.execute("CREATE TABLE preserved(value TEXT)")
    old_search.execute("INSERT INTO preserved VALUES ('old index')")
    old_search.commit()
    old_search.close()

    refreshed = subprocess.run(
        [sys.executable, "-m", "mailarchiver", "--archive", str(archive), "refresh-index"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert refreshed.returncode != 0
    search = sqlite3.connect(archive / "search.sqlite3")
    try:
        assert search.execute("SELECT value FROM preserved").fetchone() == ("old index",)
    finally:
        search.close()


def test_unchanged_source_files_are_skipped_wholesale(source_mail: tuple[Path, dict[str, bytes]], tmp_path: Path) -> None:
    """Requirements: matching source-file SHA-256 avoids per-message reingest."""
    source, _ = source_mail
    archive = tmp_path / "archive"
    owner_names = Path(__file__).parents[1] / "owner-names.txt"
    assert_success(run_ingest(source, archive, owner_names))
    before = {path.name: path.read_bytes() for path in mbox_directory(archive).glob("*.mbox")}
    touched = source / "three_messages.mbox"
    stat = touched.stat()
    os.utime(touched, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    rerun = run_ingest(source, archive, owner_names)
    assert_success(rerun)

    assert {path.name: path.read_bytes() for path in mbox_directory(archive).glob("*.mbox")} == before
    catalog = sqlite3.connect(archive / "archive.sqlite3")
    try:
        assert catalog.execute("SELECT count(*) FROM messages").fetchone() == (4,)
        assert catalog.execute("SELECT count(*) FROM ingest_runs").fetchone() == (2,)
        assert catalog.execute("SELECT count(*) FROM observations").fetchone() == (6,)
        assert catalog.execute("SELECT count(*) FROM source_files WHERE completed_run = 2").fetchone() == (4,)
        assert catalog.execute(
            "SELECT modified_at_ns FROM source_files WHERE source_path = ?",
            (catalog_source_path(catalog, touched),),
        ).fetchone() == (touched.stat().st_mtime_ns,)
    finally:
        catalog.close()
    assert "processed=0" in rerun.stderr
    assert "files_processed=4" in rerun.stderr


def test_discovery_failure_prevents_partial_ingest(tmp_path: Path) -> None:
    """Requirement: metadata discovery failures stop before MBOX publication."""
    source = tmp_path / "first.eml"
    source.write_bytes(
        b"Message-ID: <first-before-failure@example>\n"
        b"From: sender@example.net\n"
        b"Date: Thu, 1 Feb 2024 12:00:00 +0000\n\nfirst body\n"
    )
    archive = tmp_path / "archive"
    owner_names = Path(__file__).parents[1] / "owner-names.txt"

    result = subprocess.run(
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
            str(source),
            str(tmp_path / "missing-source"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "source not found" in result.stderr
    assert not any(mbox_directory(archive).glob("*.mbox"))
    catalog = sqlite3.connect(archive / "archive.sqlite3")
    try:
        assert catalog.execute("SELECT count(*) FROM messages").fetchone() == (0,)
        assert catalog.execute("SELECT count(*) FROM source_files").fetchone() == (0,)
    finally:
        catalog.close()


def test_mbox_append_resumes_after_verified_prefix(tmp_path: Path) -> None:
    """Requirement: appended MBOX input resumes at the old verified byte boundary."""
    source = tmp_path / "source.mbox"
    first = b"Message-ID: <first@example>\nFrom: sender@example.net\nDate: Thu, 1 Feb 2024 12:00:00 +0000\n\nfirst\n"
    second = b"Message-ID: <second@example>\nFrom: sender@example.net\nDate: Fri, 2 Feb 2024 12:00:00 +0000\n\nsecond\n"
    box = mailbox.mbox(source, create=True)
    try:
        box.add(first)
        box.flush()
    finally:
        box.close()
    archive = tmp_path / "archive"
    owner_names = Path(__file__).parents[1] / "owner-names.txt"
    assert_success(run_ingest(source, archive, owner_names))
    first_length = source.stat().st_size
    box = mailbox.mbox(source, create=False)
    try:
        box.add(second)
        box.flush()
    finally:
        box.close()

    result = run_ingest(source, archive, owner_names)

    assert_success(result)
    assert "processed=1" in result.stderr
    catalog = sqlite3.connect(archive / "archive.sqlite3")
    try:
        assert catalog.execute("SELECT count(*) FROM messages").fetchone() == (2,)
        assert catalog.execute("SELECT count(*) FROM observations").fetchone() == (2,)
        length, sha256 = catalog.execute("SELECT byte_length, sha256 FROM source_files").fetchone()
        assert length == source.stat().st_size > first_length
        assert sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    finally:
        catalog.close()


def test_malformed_subject_is_archived_with_metadata_defect(tmp_path: Path) -> None:
    """Requirement: malformed display metadata does not block canonical preservation."""
    source = tmp_path / "2003" / "spam.eml"
    source.parent.mkdir()
    subject = "=?EUC-KR?B? KLGks00pvY?= trailing text"
    raw = b"\n".join(
        [
            b"Message-ID: <spam@example>",
            b"From: sender@example.net",
            f"Subject: {subject}".encode(),
            b"Date: Mon, 30 Jun 2003 19:50:44 -0400",
            b"",
            b"body\n",
        ]
    )
    source.write_bytes(raw)
    archive = tmp_path / "archive"
    owner_names = Path(__file__).parents[1] / "owner-names.txt"

    result = run_ingest(source, archive, owner_names)

    assert_success(result)
    assert mailbox_message_bytes(mbox_directory(archive) / "2003-Archive1.mbox") == [raw]
    catalog = sqlite3.connect(archive / "archive.sqlite3")
    try:
        assert catalog.execute("SELECT subject FROM messages").fetchone() == (subject,)
        assert catalog.execute("SELECT field, detail FROM metadata_defects").fetchone() == (
            "Subject",
            "HeaderParseError: Base64 decoding error",
        )
    finally:
        catalog.close()


def test_report_counts_years_people_and_correspondents(source_mail: tuple[Path, dict[str, bytes]], tmp_path: Path) -> None:
    """Requirement: owner addresses are catalogued but omitted from top correspondents."""
    source, _ = source_mail
    archive = tmp_path / "archive"
    owner_names = Path(__file__).parents[1] / "owner-names.txt"
    assert_success(run_ingest(source, archive, owner_names))
    catalog = sqlite3.connect(archive / "archive.sqlite3")
    try:
        catalog.execute(
            "INSERT OR IGNORE INTO recipients(message_pk, address_pk) "
            "SELECT message_pk, sender_address_pk FROM messages WHERE category = 'Sent'"
        )
        catalog.commit()
    finally:
        catalog.close()

    result = subprocess.run(
        [sys.executable, "-m", "mailarchiver", "--archive", str(archive), "report", "--year", "2024", "--top", "2"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert "  2024       1           2         3" in lines
    assert "sender@example.net           2  2024-01-03  2024-01-04" in lines
    assert "recipient@example.net           3  2024-01-02  2024-01-04" in lines
    assert "simsong@example.com" not in result.stdout


def test_report_labels_missing_sender(tmp_path: Path) -> None:
    """Requirement: unresolved sender metadata is explicit rather than a blank table row."""
    source = tmp_path / "2024" / "message.eml"
    source.parent.mkdir()
    source.write_bytes(b"Message-ID: <missing@example>\nDate: Thu, 1 Feb 2024 12:00:00 +0000\n\nbody\n")
    archive = tmp_path / "archive"
    owner_names = Path(__file__).parents[1] / "owner-names.txt"
    assert_success(run_ingest(source, archive, owner_names))

    result = subprocess.run(
        [sys.executable, "-m", "mailarchiver", "--archive", str(archive), "report"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "(missing sender)" in result.stdout


def test_interrupt_stops_cleanly(source_mail: tuple[Path, dict[str, bytes]], tmp_path: Path) -> None:
    """Requirement: Ctrl-C exits cleanly without an exception traceback."""
    source, _ = source_mail
    archive = tmp_path / "archive"
    owner_names = Path(__file__).parents[1] / "owner-names.txt"
    process = subprocess.Popen(
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
            str(source),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stderr.readline().startswith("started:")
    process.send_signal(signal.SIGINT)
    stdout, stderr = process.communicate(timeout=20)
    assert process.returncode == 130, stdout + stderr
    assert "interrupted:" in stderr
    assert "Traceback" not in stderr
    assert stdout.startswith("year    sent    received    people\n")


def test_parser_failure_records_source_identity_and_failed_run(tmp_path: Path) -> None:
    """Requirement: an unexpected parser failure is identifiable and safely rerunnable."""
    source = tmp_path / "undated.eml"
    raw = b"Message-ID: <undated@example>\nFrom: sender@example.net\n\nbody\n"
    source.write_bytes(raw)
    archive = tmp_path / "archive"
    owner_names = Path(__file__).parents[1] / "owner-names.txt"

    result = run_ingest(source, archive, owner_names)

    assert result.returncode != 0
    digest = hashlib.sha256(raw).hexdigest()
    assert f"source offset 0; sha256={digest}" in result.stderr
    catalog = sqlite3.connect(archive / "archive.sqlite3")
    try:
        completed_at, run_result, detail = catalog.execute(
            "SELECT completed_at, result, detail FROM ingest_runs"
        ).fetchone()
        assert completed_at is not None
        assert run_result == "failed"
        assert detail.startswith("RuntimeError: failed to parse")
        assert catalog.execute(
            "SELECT source_files.source_path, source_offset, raw_sha256, disposition, detail FROM observations "
            "JOIN source_files USING (source_file_pk)"
        ).fetchone() == (
            catalog_source_path(catalog, source), 0, digest, "error",
            f"ValueError: no date or year path fallback for {source}",
        )
    finally:
        catalog.close()
    assert not list(mbox_directory(archive).glob("*.mbox"))


def test_fresh_catalog_is_refused_beside_existing_mbox(tmp_path: Path) -> None:
    """Requirement: deleting only the catalog cannot cause duplicate canonical output."""
    source = tmp_path / "source.eml"
    source.write_bytes(b"Message-ID: <one@example>\nDate: Thu, 1 Feb 2024 12:00:00 +0000\n\nbody\n")
    archive = tmp_path / "archive"
    archive.mkdir()
    mbox_directory(archive).mkdir(parents=True)
    existing = mbox_directory(archive) / "2024-Archive1.mbox"
    existing.write_bytes(b"existing canonical bytes\n")
    owner_names = Path(__file__).parents[1] / "owner-names.txt"

    result = run_ingest(source, archive, owner_names)

    assert result.returncode != 0
    assert "use a new empty archive directory" in result.stderr
    assert existing.read_bytes() == b"existing canonical bytes\n"
    assert not (archive / "archive.sqlite3").exists()


@pytest.mark.parametrize(
    ("source_name", "contents", "diagnostic"),
    [
        ("missing", None, "source not found:"),
        ("7.partial.emlx", b"5\nbody\n", "unsupported source: Apple Mail partial message"),
    ],
)
def test_unusable_source_fails_cleanly(
    tmp_path: Path, source_name: str, contents: bytes | None, diagnostic: str
) -> None:
    """Requirement: missing or incomplete Apple Mail input cannot look successful."""
    source = tmp_path / source_name
    if contents is not None:
        source.write_bytes(contents)
    archive = tmp_path / "archive"
    owner_names = Path(__file__).parents[1] / "owner-names.txt"

    result = run_ingest(source, archive, owner_names)

    assert result.returncode == 1
    assert diagnostic in result.stderr
    assert "Traceback" not in result.stderr
    assert not list(mbox_directory(archive).glob("*.mbox"))


def test_clamav_startup_failure_prevents_worker_activity(tmp_path: Path) -> None:
    """Regression: clamd must become ready before workers can parse or publish mail."""
    source = tmp_path / "source.eml"
    source.write_bytes(
        b"Message-ID: <one@example>\nDate: Thu, 1 Feb 2024 12:00:00 +0000\n\nbody\n"
    )
    archive = tmp_path / "archive"
    owner_names = Path(__file__).parents[1] / "owner-names.txt"
    missing_clamd = tmp_path / "missing-clamd"
    environment = os.environ.copy()
    environment[CLAMD_ENV] = str(missing_clamd)
    environment[CLAMD_SOCKET_ENV] = str(tmp_path / "clamd.sock")

    result = subprocess.run(
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
            str(source),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 1
    assert f"ClamAV startup failed: cannot start {missing_clamd}" in result.stderr
    assert "processed=0 active_workers=0 peak_workers=0" in result.stderr
    assert "Traceback" not in result.stderr
    assert not list(mbox_directory(archive).glob("*.mbox"))
    catalog = sqlite3.connect(archive / "archive.sqlite3")
    try:
        assert catalog.execute("SELECT count(*) FROM source_files").fetchone() == (0,)
        assert catalog.execute("SELECT count(*) FROM observations").fetchone() == (0,)
        assert catalog.execute("SELECT result FROM ingest_runs").fetchone() == ("failed",)
    finally:
        catalog.close()
