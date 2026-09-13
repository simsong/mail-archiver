# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Requirement: import ALL tests/data, retain exact mail, terminate, and remain idempotent.

The public golden file covers tracked inputs. An ignored overlay covers private
local additions without publishing their subjects. Neither is updated implicitly.
Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field

from mailarchiver.ingest_status import read_ingest_history
from mailarchiver.layout import mbox_path
from mailarchiver.mbox import MboxLocation, read_verified_location
from mailarchiver.source_volume import METADATA_CURRENT_MOUNT_PATH

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests/data"
EXPECTED = ROOT / "tests/expected-corpus.json"
PRIVATE_EXPECTED = ROOT / ".tmp/expected-corpus-private.json"
TIMEOUT = 600


class MessageExpectation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    subject: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    def __hash__(self) -> int:
        return hash((self.subject, self.sha256))


class FileExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    sha256: str
    observations: int = 0
    excluded: int = 0
    messages: list[MessageExpectation] = Field(default_factory=list)


class CorpusExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    files: list[FileExpectation]


def fingerprint() -> list[FileExpectation]:
    result = []
    for path in sorted(SOURCE.rglob("*")):
        if path.is_file() and path.name != ".DS_Store":
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            result.append(FileExpectation(path=path.relative_to(SOURCE).as_posix(), sha256=digest))
    return result


def command(arguments: list[str], log: Path, *, timeout: float = TIMEOUT) -> None:
    """Bound the real subprocess and retain diagnostics rather than buffering progress."""
    with log.open("w", encoding="utf-8") as output:
        with subprocess.Popen(
            [sys.executable, *arguments], cwd=ROOT, stdout=output,
            stderr=subprocess.STDOUT, start_new_session=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        ) as process:
            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                # Only this test's process group; never a pre-existing external clamd.
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait()
                pytest.fail(f"Import/verification did not terminate within {timeout}s. Log: {log}")
    assert returncode == 0, f"Command failed; log: {log}\n{log.read_text(encoding='utf-8')}"


def snapshot(archive: Path, files: list[FileExpectation]) -> CorpusExpectation:
    """Read actual observations and verify every canonical location independently of SQL hashes."""
    result = CorpusExpectation(files=[item.model_copy(deep=True) for item in files])
    with sqlite3.connect(f"{archive.as_uri()}/archive.sqlite3?mode=ro", uri=True) as catalog:
        verified_count = 0
        for subject, digest, filename, offset, length in catalog.execute(
            "SELECT subject, messages.sha256, filename, byte_offset, byte_length "
            "FROM messages JOIN locations USING(message_pk) JOIN mbox_generations USING(generation_pk)"
        ):
            raw = read_verified_location(
                mbox_path(archive, filename), MboxLocation(byte_offset=offset, byte_length=length), digest,
            )
            assert hashlib.sha256(raw).hexdigest() == digest, subject
            verified_count += 1
        for key, path, metadata in catalog.execute(
            "SELECT source_file_pk, source_path, source_volumes.metadata_json FROM source_files "
            "JOIN source_volumes USING(source_volume_pk)"
        ):
            mount = Path(json.loads(metadata)[METADATA_CURRENT_MOUNT_PATH])
            relative = (mount / path).resolve().relative_to(SOURCE.resolve()).as_posix()
            entry = next(item for item in result.files if item.path == relative)
            entry.observations, entry.excluded = catalog.execute(
                "SELECT COUNT(*), COALESCE(SUM(disposition IN ('autosave-excluded', 'source-metadata-excluded')), 0) "
                "FROM observations WHERE source_file_pk = ?", (key,),
            ).fetchone()
            entry.messages = [MessageExpectation(subject=subject, sha256=digest) for subject, digest in catalog.execute(
                "SELECT DISTINCT subject, messages.sha256 FROM observations JOIN messages USING(message_pk) "
                "WHERE source_file_pk = ? ORDER BY subject, messages.sha256", (key,),
            )]
        canonical_count = catalog.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        assert verified_count == canonical_count, "Canonical messages lack verified locations"
        assert canonical_count == len({message for item in result.files for message in item.messages})
        assert catalog.execute("SELECT COUNT(*) FROM observations WHERE disposition = 'error'").fetchone() == (0,)
    return result


def difference(expected: CorpusExpectation, actual: CorpusExpectation) -> str:
    def identities(corpus: CorpusExpectation) -> set[MessageExpectation]:
        return {message for item in corpus.files for message in item.messages}

    wanted, found = identities(expected), identities(actual)
    lines = []
    for label, messages in (("Found but not expected", found - wanted), ("Expected but not found", wanted - found)):
        lines.append(f"{label} ({len(messages)}):")
        lines.extend(f"  {item.sha256}  {item.subject!r}" for item in sorted(messages, key=lambda item: (item.subject, item.sha256)))
    for path in sorted({item.path for item in expected.files + actual.files}):
        old = next((item for item in expected.files if item.path == path), None)
        new = next((item for item in actual.files if item.path == path), None)
        if old != new:
            lines.append(f"Source inventory, message membership, or accounting changed: {path}")
    return "\n".join(lines)


def test_complete_data_directory_import(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    """Full-directory import must match reviewed subjects/hashes and preserve source bytes.

    Use the GUI's real ClamAV path. Expectations cover preserved messages, not
    signature-dependent quarantine destinations; infected mail is retained too.
    """
    original = fingerprint()
    archive = tmp_path / "corpus.mailarchive"
    owners = tmp_path / "owners.txt"
    owners.write_text("alice@example.org\n", encoding="utf-8")
    args = ["-m", "mailarchiver", "--archive", str(archive), "ingest", "--clamav",
            "--owner-names-file", str(owners), str(SOURCE)]
    command(args, tmp_path / "import.log")
    actual = snapshot(archive, original)
    history = read_ingest_history(archive)
    assert not history.errors and len(history.statuses) == 1
    status = history.statuses[0]
    assert status.state == "completed" and status.percent == 100
    assert status.processed_messages == sum(item.observations for item in actual.files)
    command(["-I", str(archive / "verify_mail_archive.py"), str(archive)], tmp_path / "verify.log")
    command(args, tmp_path / "reimport.log")
    assert snapshot(archive, original) == actual, "Reimport changed message inventory or source observations"
    repeated = read_ingest_history(archive).statuses[0]
    assert repeated.state == "completed" and repeated.processed_messages == 0
    assert repeated.counts.unchanged_sources == status.files_total
    assert fingerprint() == original, "Import changed source files"

    if request.config.getoption("--update-corpus-expectations"):
        tracked = subprocess.run(
            ["git", "ls-files", "-z", "--", "tests/data"], cwd=ROOT, check=True, capture_output=True,
        ).stdout.decode().split("\0")
        for target, private in ((EXPECTED, False), (PRIVATE_EXPECTED, True)):
            subset = CorpusExpectation(files=[item for item in actual.files if
                (f"tests/data/{item.path}" not in tracked) == private])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(subset.model_dump_json(indent=2) + "\n", encoding="utf-8")
            print(f"Updated {target}: {len(subset.files)} files; review before accepting.")
    else:
        assert EXPECTED.exists(), "Missing corpus expectations; run make update-corpus-expectations and review."
        expected = CorpusExpectation.model_validate_json(EXPECTED.read_text(encoding="utf-8"))
        if PRIVATE_EXPECTED.exists():
            expected.files.extend(CorpusExpectation.model_validate_json(PRIVATE_EXPECTED.read_text(encoding="utf-8")).files)
        expected.files.sort(key=lambda item: item.path)
        if expected != actual:
            pytest.fail(difference(expected, actual), pytrace=False)


def test_corpus_difference_names_both_missing_and_unexpected_mail() -> None:
    """A changed subject/hash must identify both sides, not just unequal counts."""
    missing = MessageExpectation(subject="Expected subject", sha256="a" * 64)
    extra = MessageExpectation(subject="Unexpected subject", sha256="b" * 64)
    expected = CorpusExpectation(files=[FileExpectation(path="mail.eml", sha256="c" * 64, messages=[missing])])
    actual = CorpusExpectation(files=[FileExpectation(path="mail.eml", sha256="d" * 64, messages=[extra])])
    report = difference(expected, actual)
    assert f"Found but not expected (1):\n  {extra.sha256}  'Unexpected subject'" in report
    assert f"Expected but not found (1):\n  {missing.sha256}  'Expected subject'" in report


def test_corpus_deadline_terminates_a_stuck_subprocess(tmp_path: Path) -> None:
    """A genuine endless loop must fail the regression instead of hanging pytest."""
    with pytest.raises(pytest.fail.Exception, match="did not terminate"):
        command(["-c", "while True: pass"], tmp_path / "stuck.log", timeout=0.2)
