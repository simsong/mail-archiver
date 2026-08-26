"""Verify read-only source discovery, EMLX/MBOX streaming, and append fingerprints."""

import hashlib
import json
import mailbox
from pathlib import Path

import pytest

from mailarchiver.sources import (
    IncompleteAppleMailMessageError,
    emlx_bytes,
    sha256_file_with_prefix,
    source_files,
    source_inventory,
    source_messages,
)


def test_one_pass_hashing_returns_prefix_and_complete_sha256(tmp_path: Path) -> None:
    path = tmp_path / "source.mbox"
    prior = b"complete old source bytes\n"
    appended = b"From sender@example Fri Feb  2 00:00:00 2024\nmessage\n"
    path.write_bytes(prior + appended)

    hashes = sha256_file_with_prefix(path, len(prior))

    assert hashes.prefix_sha256 == hashlib.sha256(prior).hexdigest()
    assert hashes.sha256 == hashlib.sha256(prior + appended).hexdigest()


def test_local_source_file_has_a_stable_volume_identity_and_relative_path(tmp_path: Path) -> None:
    """Requirement: every local source file records its source volume and path within that volume."""
    path = tmp_path / "message.eml"
    path.write_bytes(b"Message-ID: <source@example>\n\nbody\n")

    source = next(source_files(path))

    metadata = json.loads(source.volume.metadata_json)
    identity = json.loads(source.volume.identity_json)
    assert identity["kind"] == "local-volume"
    assert metadata["current_mount_path"] == str(source.volume.mount_path)
    assert source.path == path.resolve()
    assert source.source_path == path.resolve().relative_to(source.volume.mount_path).as_posix()


def test_source_inventory_totals_only_recognized_message_files(tmp_path: Path) -> None:
    """Requirement: metadata discovery totals eligible files without hashing the source tree."""
    source = tmp_path / "source"
    source.mkdir()
    eml = source / "message.eml"
    eml.write_bytes(b"Message-ID: <source@example>\n\nbody\n")
    mbox = source / "mailbox"
    mbox.write_bytes(b"From sender@example Fri Feb  2 00:00:00 2024\nmessage\n")
    (source / "ignored.plist").write_bytes(b"not mail" * 100)
    updates: list[tuple[int, int]] = []

    inventory = source_inventory([source], progress=lambda files, size: updates.append((files, size)))

    assert inventory.file_count == 2
    assert inventory.byte_count == eml.stat().st_size + mbox.stat().st_size
    assert updates[-1] == (inventory.file_count, inventory.byte_count)


def test_modern_apple_mail_package_reads_complete_emlx_only(tmp_path: Path) -> None:
    """Requirement: modern Apple Mail packages preserve complete EMLX payload bytes."""
    root = tmp_path / "V10"
    messages = root / "account" / "Archive.mbox" / "Data" / "4" / "2" / "Messages"
    messages.mkdir(parents=True)
    raw = b"Message-ID: <apple@example>\nFrom: sender@example.net\nDate: Thu, 1 Feb 2024 12:00:00 +0000\n\nbody\n"
    emlx = messages / "42.emlx"
    emlx.write_bytes(str(len(raw)).encode() + b"\n" + raw + b"<?xml version='1.0'?><plist/>")
    (messages.parent / "Attachments" / "42").mkdir(parents=True)
    (messages.parent / "Attachments" / "42" / "1.emlxpart").write_bytes(b"detached attachment")
    mail_data = root / "MailData"
    mail_data.mkdir()
    (mail_data / "Envelope Index").write_bytes(b"not a message")

    discovered = list(source_files(root))

    assert [source.path for source in discovered] == [emlx.resolve()]
    assert [message.raw for message in source_messages(discovered[0])] == [raw]
    assert emlx_bytes(emlx) == raw


def test_classic_apple_mail_package_reads_mbox_stream(tmp_path: Path) -> None:
    """Requirement: Apple Mail package MBOX streams are valid source mail."""
    path = tmp_path / "On My Mac.mbox" / "mbox"
    path.parent.mkdir()
    raw = b"Message-ID: <classic@example>\nDate: Thu, 1 Feb 2024 12:00:00 +0000\n\nbody\n"
    box = mailbox.mbox(path)
    try:
        box.add(raw)
        box.flush()
    finally:
        box.close()

    discovered = list(source_files(tmp_path))

    assert len(discovered) == 1
    assert discovered[0].kind == "mbox"
    assert [message.raw for message in source_messages(discovered[0])] == [raw]


def test_partial_apple_mail_message_is_rejected(tmp_path: Path) -> None:
    """Requirement: detached Apple Mail attachment bytes must not be silently omitted."""
    path = tmp_path / "V10" / "account" / "Inbox.mbox" / "Data" / "Messages" / "7.partial.emlx"
    path.parent.mkdir(parents=True)
    raw = b"Message-ID: <partial@example>\nDate: Thu, 1 Feb 2024 12:00:00 +0000\n\nbody without attachment\n"
    path.write_bytes(str(len(raw)).encode() + b"\n" + raw)

    with pytest.raises(IncompleteAppleMailMessageError, match="omits detached attachment bytes"):
        list(source_files(tmp_path))


def test_missing_source_is_not_silently_empty(tmp_path: Path) -> None:
    """Requirement: an unreadable or missing source must not look like an empty mailbox."""
    with pytest.raises(FileNotFoundError):
        list(source_files(tmp_path / "missing"))
