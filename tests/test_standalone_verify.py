"""Verify the installed stdlib checker enforces BagIt, Mailbag, and message fixity."""

from __future__ import annotations

import hashlib
import json
import mailbox
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from mailarchiver.bagit import initialize_bag, refresh_tag_manifest, write_bag_checkpoint
from mailarchiver.catalog import address_pk, create_catalog
from mailarchiver.layout import integrity_path, mbox_directory
from mailarchiver.mbox import add_message
from mailarchiver.standalone_verify import (
    INSTALLED_NAME,
    IntegrityMessage,
    install_archive_verifier,
    semantic_bytes,
    verify_mbox,
    write_integrity_file,
)


def run_verifier(script: Path, archive: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(script), str(archive)],
        capture_output=True,
        text=True,
        check=False,
    )


def make_integrity_archive(tmp_path: Path, raw: bytes | None = None) -> tuple[Path, Path, bytes]:
    raw = raw if raw is not None else (
        b"Message-ID: <verify@example>\nFrom: sender@example\nTo: recipient@example\n"
        b"Delivered-To: mailbox@example\nSubject: integrity\nDate: Thu, 1 Feb 2024 12:00:00 +0000\n"
        b"Status: RO\n\nPreserve these bytes.\n"
    )
    initialize_bag(tmp_path)
    path = mbox_directory(tmp_path) / "2024-Archive1.mbox"
    box = mailbox.mbox(path)
    try:
        location = add_message(box, path, raw)
    finally:
        box.close()
    catalog = create_catalog(tmp_path / "archive.sqlite3")
    sender_pk = address_pk(catalog, "sender@example")
    message_pk = catalog.execute(
        "INSERT INTO messages(message_id_normalized, sha256, sender_address_pk, subject, date_utc, date_source, category) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("verify@example", hashlib.sha256(raw).hexdigest(), sender_pk, "integrity",
         "2024-02-01T12:00:00+00:00", "date", "Archive"),
    ).lastrowid
    generation_pk = catalog.execute(
        "INSERT INTO mbox_generations(filename, sha256, message_count, byte_count) VALUES (?, '', 0, 0)",
        (path.name,),
    ).lastrowid
    catalog.execute(
        "INSERT INTO locations(message_pk, generation_pk, byte_offset, byte_length) VALUES (?, ?, ?, ?)",
        (message_pk, generation_pk, location.byte_offset, location.byte_length),
    )
    catalog.commit()
    install_archive_verifier(tmp_path)
    write_bag_checkpoint(tmp_path, catalog, datetime(2026, 8, 22, tzinfo=timezone.utc))
    catalog.commit()
    catalog.close()
    return path, integrity_path(tmp_path, path.name), raw


def test_message_without_terminal_newline_retains_original_identity(tmp_path: Path) -> None:
    """Requirement: an MBOX separator newline is not part of the original message."""
    raw = b"Message-ID: <no-newline@example>\nFrom: sender@example\nSubject: exact\n\nbody"
    _path, _integrity, preserved = make_integrity_archive(tmp_path, raw)
    script = install_archive_verifier(tmp_path)

    verified = run_verifier(script, tmp_path)

    assert preserved == raw
    assert verified.returncode == 0, verified.stderr


def test_folded_message_id_does_not_put_bare_lf_in_mailbag_csv(tmp_path: Path) -> None:
    """Requirement: Mailbag CSV uses CRLF records and single-line metadata fields."""
    raw = (
        b"Message-ID: <folded@example>\n"
        b" (added by relay.example)\nFrom: sender@example\nSubject: folded id\n\nbody\n"
    )
    make_integrity_archive(tmp_path, raw)
    csv_bytes = (tmp_path / "mailbag.csv").read_bytes()
    script = install_archive_verifier(tmp_path)

    assert b"\n (added by" not in csv_bytes
    assert b"<folded@example> (added by relay.example)" in csv_bytes
    assert b"\n" not in csv_bytes.replace(b"\r\n", b"")
    assert run_verifier(script, tmp_path).returncode == 0


def test_standalone_verifier_checks_current_file_and_message_hashes(tmp_path: Path) -> None:
    """Requirement: the stdlib tool checks h1 MBOX, h2 raw, and h3 semantic hashes."""
    path, integrity, raw = make_integrity_archive(tmp_path)
    script = install_archive_verifier(tmp_path)

    valid = run_verifier(script, tmp_path)
    assert valid.returncode == 0, valid.stderr
    assert "Archive integrity verified." in valid.stdout
    lines = integrity.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["code"] for line in lines[1:4]] == ["h1", "h2", "h3"]
    fields = lines[-1].split("\t")
    assert [field[:3] for field in fields[2:]] == ["h2:", "h3:"]
    fields[-1] = "h3:" + "0" * 64
    lines[-1] = "\t".join(fields)
    integrity.write_text("\n".join(lines) + "\n", encoding="utf-8")
    refresh_tag_manifest(tmp_path)
    bad_message = run_verifier(script, tmp_path)
    assert bad_message.returncode == 1
    assert "message 1 h3 mismatch" in bad_message.stderr

    write_integrity_file(
        path,
        integrity,
        (IntegrityMessage("verify@example", hashlib.sha256(raw).hexdigest(), raw),),
        1,
    )
    with path.open("ab") as output:
        output.write(b"damage")
    bad_file = run_verifier(script, tmp_path)
    assert bad_file.returncode == 1
    assert "h1 mismatch" in bad_file.stderr
    assert script.name == INSTALLED_NAME


def test_semantic_v1_selects_stable_delivery_headers_and_complete_body() -> None:
    """Requirement: h3 ignores mutable status but includes delivery identity and body bytes."""
    base = (
        b"From: sender@example\r\nTo: recipient@example\r\nDelivered-To: first@example\r\n"
        b"Subject: folded\r\n\tvalue\r\nDate: Thu, 1 Feb 2024 12:00:00 +0000\r\n"
        b"Message-ID: <same@example>\r\nStatus: RO\r\nReceived: trace one\r\n\r\nbody\r\n\r\n"
    )
    refolded = base.replace(b"Subject: folded\r\n\tvalue", b"Subject:  folded   value")
    mutable = refolded.replace(b"Status: RO", b"Status: O").replace(b"Received: trace one", b"Received: trace two")
    delivered = mutable.replace(b"Delivered-To: first@example", b"Delivered-To: second@example")
    changed_body = mutable.replace(b"body", b"changed body")

    assert semantic_bytes(base) == semantic_bytes(refolded) == semantic_bytes(mutable)
    assert semantic_bytes(mutable) != semantic_bytes(delivered)
    assert semantic_bytes(mutable) != semantic_bytes(changed_body)


def test_integrity_serialization_is_deterministic(tmp_path: Path) -> None:
    """Requirement: regenerating unchanged declarations and inputs reproduces exact bytes."""
    path, integrity, raw = make_integrity_archive(tmp_path)
    first = integrity.read_bytes()
    write_integrity_file(
        path,
        integrity,
        (IntegrityMessage("verify@example", hashlib.sha256(raw).hexdigest(), raw),),
        1,
    )
    assert integrity.read_bytes() == first


def test_verifier_recovers_multiple_ambiguous_from_lines(tmp_path: Path) -> None:
    """Requirement: independent verification tests every bounded MBOX quote interpretation."""
    raw = b"Message-ID: <quoted@example>\n\nFrom one\n>From two\nFrom three\n"
    path = tmp_path / "quoted.mbox"
    box = mailbox.mbox(path)
    try:
        box.add(raw)
        box.flush()
    finally:
        box.close()
    sidecar = tmp_path / "quoted.mbox.integrity"
    digest = hashlib.sha256(raw).hexdigest()
    write_integrity_file(path, sidecar, (IntegrityMessage("quoted@example", digest, raw),), 1)

    assert verify_mbox(path, sidecar) == []


def test_verifier_recovers_message_without_source_final_newline(tmp_path: Path) -> None:
    """Regression: independent verification recognizes one writer-added final LF."""
    raw = b"Message-ID: <no-final-newline@example>\n\nbody"
    path = tmp_path / "no-final-newline.mbox"
    box = mailbox.mbox(path)
    try:
        box.add(raw)
        box.flush()
    finally:
        box.close()
    sidecar = tmp_path / "no-final-newline.mbox.integrity"
    digest = hashlib.sha256(raw).hexdigest()
    write_integrity_file(
        path,
        sidecar,
        (IntegrityMessage("no-final-newline@example", digest, raw),),
        1,
    )

    assert verify_mbox(path, sidecar) == []


def test_verifier_recovers_original_leading_from_envelope(tmp_path: Path) -> None:
    """Requirement: independent h2 verification considers a source-supplied envelope line."""
    raw = (
        b"From legacy.example Sat Jan 01 00:00:00 2000\n"
        b"Message-ID: <source-envelope@example>\n\nFrom body\n>From literal\n"
    )
    path = tmp_path / "source-envelope.mbox"
    box = mailbox.mbox(path)
    try:
        box.add(raw)
        box.flush()
    finally:
        box.close()
    sidecar = tmp_path / "source-envelope.mbox.integrity"
    digest = hashlib.sha256(raw).hexdigest()
    write_integrity_file(
        path,
        sidecar,
        (IntegrityMessage("source-envelope@example", digest, raw),),
        1,
    )

    assert verify_mbox(path, sidecar) == []


def test_verifier_accepts_multiple_digest_algorithms_for_one_standard(tmp_path: Path) -> None:
    """Requirement: one file may tag SHA-256 and SHA-512 digests of the same semantic input."""
    _, integrity, raw = make_integrity_archive(tmp_path)
    lines = integrity.read_text(encoding="utf-8").splitlines()
    h4 = {
        "code": "h4",
        "digest_algorithm": "sha512",
        "hash_standard": "semantic",
        "hash_version": 1,
        "id": "tag:simson.net,2026:mailarchiver/hash/semantic/v1/sha512",
        "same_input_as": "h3",
        "scope": "message",
        "type": "hash-standard",
    }
    lines.insert(4, json.dumps(h4, separators=(",", ":"), sort_keys=True))
    lines[-1] += f"\th4:{hashlib.sha512(semantic_bytes(raw)).hexdigest()}"
    integrity.write_text("\n".join(lines) + "\n", encoding="utf-8")
    refresh_tag_manifest(tmp_path)
    script = install_archive_verifier(tmp_path)

    verified = run_verifier(script, tmp_path)

    assert verified.returncode == 0, verified.stderr


def test_message_id_field_is_json_null_when_header_is_absent(tmp_path: Path) -> None:
    """Requirement: the TSV diagnostic field does not invent a Message-ID."""
    raw = b"From: sender@example\nSubject: no identifier\n\nbody\n"
    initialize_bag(tmp_path)
    path = mbox_directory(tmp_path) / "2024-Archive1.mbox"
    box = mailbox.mbox(path)
    try:
        box.add(raw)
        box.flush()
    finally:
        box.close()
    sidecar = integrity_path(tmp_path, path.name)
    write_integrity_file(path, sidecar, (IntegrityMessage(None, hashlib.sha256(raw).hexdigest(), raw),), 1)

    fields = sidecar.read_text(encoding="utf-8").splitlines()[-1].split("\t")

    assert fields[1] == "null"
