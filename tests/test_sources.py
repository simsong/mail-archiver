"""Verify read-only source discovery, EMLX/MBOX streaming, and append fingerprints."""

import json
import mailbox
from pathlib import Path

import pytest

from mailarchiver.plugin_api import MailContainer, MailObject, SkippedInput, SourceSpec
from mailarchiver.plugin_loader import load_plugins
from mailarchiver.sources import (
    FileParser,
    IncompleteAppleMailMessageError,
    MailboxHierarchyParser,
    SourceFile,
    SourceInventory,
    SourceMessage,
    emlx_bytes,
    local_hierarchy_path,
    mailbox_hierarchy_parsers,
    register_mailbox_hierarchy_parser,
    register_file_parser,
    source_files,
    source_inventory,
    source_messages,
    unregister_mailbox_hierarchy_parser,
    unregister_file_parser,
)


def test_local_source_plugin_generates_containers_and_delegates_mail_objects(tmp_path: Path) -> None:
    """Requirement: source and file generators compose without owning worker or status machinery."""
    eml = tmp_path / "mail.eml"
    raw = b"From: plugin@example.net\nDate: Thu, 1 Feb 2024 12:00:00 +0000\n\nbody\n"
    eml.write_bytes(raw)
    ignored = tmp_path / "metadata.plist"
    ignored.write_bytes(b"not mail")
    plugin = load_plugins().source("file-folder").implementation

    discovered = list(plugin.discover(SourceSpec(locator=str(tmp_path))))

    containers = [item for item in discovered if isinstance(item, MailContainer)]
    skipped = [item for item in discovered if isinstance(item, SkippedInput)]
    assert len(containers) == 1
    assert containers[0].parser_kind == "message"
    source = plugin.source_file(containers[0])
    assert containers[0].source.hierarchy == tuple(Path(source.source_path).parts[:-1])
    assert local_hierarchy_path(source).endswith("/mail.eml")
    assert skipped[0].source.display_name == str(ignored.resolve())
    messages = list(plugin.messages(containers[0], None))
    assert len(messages) == 1 and isinstance(messages[0], MailObject)
    assert messages[0].raw == raw
    assert messages[0].source == containers[0].source


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
    skipped: list[tuple[Path, str]] = []

    inventory = source_inventory(
        [source],
        progress=lambda files, size: updates.append((files, size)),
        skipped=lambda path, reason: skipped.append((path, reason)),
    )

    assert inventory.file_count == 2
    assert inventory.byte_count == eml.stat().st_size + mbox.stat().st_size
    assert inventory.skipped_file_count == 1
    assert skipped == [(source / "ignored.plist", "no file parser recognized it")]
    assert updates[-1] == (inventory.file_count, inventory.byte_count)


def test_local_discovery_reports_skipped_files_in_stable_order(tmp_path: Path) -> None:
    """Requirement: every unrecognized regular file is named once in deterministic inventory order."""
    root = tmp_path / "source"
    (root / "z").mkdir(parents=True)
    (root / "a").mkdir()
    (root / "z" / "second.dat").write_bytes(b"not mail")
    (root / "a" / "first.dat").write_bytes(b"not mail")
    (root / "message.eml").write_bytes(b"From: sender@example.net\n\nbody\n")
    skipped: list[Path] = []

    inventory = source_inventory([root], skipped=lambda path, _reason: skipped.append(path))

    assert inventory == SourceInventory(file_count=1, byte_count=(root / "message.eml").stat().st_size, skipped_file_count=2)
    assert skipped == [(root / "a" / "first.dat").resolve(), (root / "z" / "second.dat").resolve()]


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


def test_apple_mail_cache_uses_mbox_packages_as_logical_hierarchy(tmp_path: Path) -> None:
    """Requirement: Apple cache files retain physical provenance but group by `.mbox` packages."""
    account = "B705E5AE-3E33-4277-8BE6-5AE1F2B0046A"
    messages = (
        tmp_path / "Library" / "Mail" / "V10" / account / "[Gmail].mbox" / "All Mail.mbox"
        / "D32894D5-2B73-483D-85A7-C61A5316DD04" / "Data" / "7" / "2" / "Messages"
    )
    messages.mkdir(parents=True)
    raw = b"Message-ID: <apple-cache@example>\nFrom: sender@example.net\n\nbody\n"
    emlx = messages / "42.emlx"
    emlx.write_bytes(str(len(raw)).encode() + b"\n" + raw + b"<?xml version='1.0'?><plist/>")
    plugin = load_plugins().source("file-folder").implementation

    containers = [
        item for item in plugin.discover(SourceSpec(locator=str(tmp_path / "Library" / "Mail")))
        if isinstance(item, MailContainer)
    ]

    assert len(containers) == 1
    container = containers[0]
    source = plugin.source_file(container)
    assert container.parser_kind == "emlx"
    assert container.source.hierarchy[-4:] == ("V10", account, "[Gmail]", "All Mail")
    assert container.source.relationship.role == "cache"
    assert container.source.relationship.upstream_plugin_kind == "gmail"
    assert container.source.relationship.account_hint == account
    assert "Data" not in container.source.hierarchy
    assert container.source.native_id.endswith("/Data/7/2/Messages/42.emlx")
    assert local_hierarchy_path(source).endswith(f"/V10/{account}/[Gmail]/All Mail")
    assert [message.raw for message in plugin.messages(container, None)] == [raw]


def test_maildir_content_parser_wins_without_changing_logical_folder(tmp_path: Path) -> None:
    """Requirement: a valid Maildir is one mailbox even when a message has an MBOX envelope."""
    root = tmp_path / "2003" / "mbox.2003.ID-Policy"
    for name in ("cur", "new", "tmp"):
        (root / name).mkdir(parents=True, exist_ok=True)
    path = root / "cur" / "1071235664.M505205P94798:2,S.txt"
    raw = b"From: sender@example.net\nDate: Thu, 1 Feb 2024 12:00:00 +0000\n\nbody\n"
    box = mailbox.mbox(path)
    try:
        box.add(raw)
        box.flush()
    finally:
        box.close()
    plugin = load_plugins().source("file-folder").implementation

    containers = [
        item for item in plugin.discover(SourceSpec(locator=str(root)))
        if isinstance(item, MailContainer)
    ]

    assert len(containers) == 1
    container = containers[0]
    source = plugin.source_file(container)
    assert container.parser_kind == "mbox"
    assert container.source.hierarchy == tuple(Path(source.source_path).parts[:-2])
    assert local_hierarchy_path(source).endswith("/2003/mbox.2003.ID-Policy")
    assert container.source.native_id.endswith("/mbox.2003.ID-Policy/cur/1071235664.M505205P94798:2,S.txt")
    assert [message.raw for message in plugin.messages(container, None)] == [raw]


def test_cur_directory_without_maildir_structure_is_not_a_maildir(tmp_path: Path) -> None:
    """Requirement: a directory named `cur` alone must not classify arbitrary files as mail."""
    path = tmp_path / "cache" / "cur" / "message"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"From: sender@example.net\n\nbody\n")
    plugin = load_plugins().source("file-folder").implementation

    discovered = list(plugin.discover(SourceSpec(locator=str(tmp_path / "cache"))))

    assert len(discovered) == 1
    assert isinstance(discovered[0], SkippedInput)
    assert discovered[0].source.display_name == str(path.resolve())


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


def test_mbox_parser_excludes_mbcp_metadata_and_unwraps_xxx_records(tmp_path: Path) -> None:
    """Requirement: exact MBCP stubs are observed as metadata and From XXX exposes its nested email."""
    path = tmp_path / "eudora.mbx"
    nested = (
        b"From: actual@example.net\nDate: Thu, 1 Feb 2024 12:00:00 +0000\n\n"
        b"body\nFrom quoted body\n>From original literal body\n"
    )
    path.write_bytes(
        b"From mbcp@s.eecs.harvard.edu Thu Feb  1 11:59:00 2024\n"
        b"X-UID: 123\nStatus: O\nX-MBCP-Flags: $NotJunk\n"
        b"From XXX Thu Feb  1 12:00:00 2024\nStatus: O\n\n"
        b">From actual@example.net Thu Feb  1 12:00:00 2024\n"
        + nested.replace(b"\nFrom ", b"\n>From ").replace(b"\n>From original", b"\n>>From original")
    )

    messages = list(source_messages(next(source_files(path))))

    assert len(messages) == 2
    assert messages[0].exclusion_reason == "Eudora MBCP metadata stub"
    assert messages[1].exclusion_reason is None
    assert messages[1].raw == nested


def test_file_parser_registry_accepts_a_real_extension(tmp_path: Path) -> None:
    """Requirement: file formats are independently registerable without changing discovery orchestration."""

    class FixtureFileParser(FileParser):
        kind = "fixture"

        def recognizes(self, path: Path) -> bool:
            return path.suffix == ".archive-test"

        def messages(self, source: SourceFile, start_offset: int = 0):
            raw = source.path.read_bytes()
            yield SourceMessage(
                path=source.path,
                raw=raw,
                source_offset=start_offset,
                bytes_done=len(raw),
                bytes_total=len(raw),
            )

    path = tmp_path / "mail.archive-test"
    raw = b"From: plugin@example.net\n\nregistered\n"
    path.write_bytes(raw)
    register_file_parser(FixtureFileParser())
    try:
        source = next(source_files(path))
        assert source.kind == "fixture"
        assert [message.raw for message in source_messages(source)] == [raw]
    finally:
        unregister_file_parser("fixture")


def test_legacy_mailbox_hierarchy_registry_accepts_local_compatibility_plugins(tmp_path: Path) -> None:
    """Requirement: the legacy local facade remains extensible without impersonating remote sources."""
    parsers = {parser.kind: parser for parser in mailbox_hierarchy_parsers()}
    assert set(parsers) == {"file-folder"}
    assert parsers["file-folder"].available

    class FixtureHierarchyParser(MailboxHierarchyParser):
        kind = "fixture-hierarchy"

        def paths(self, source: Path):
            yield source / "selected.eml"

    selected = tmp_path / "selected.eml"
    selected.write_bytes(b"From: selected@example.net\n\nbody\n")
    (tmp_path / "ignored.eml").write_bytes(b"From: ignored@example.net\n\nbody\n")
    register_mailbox_hierarchy_parser(FixtureHierarchyParser())
    try:
        assert [item.path for item in source_files(tmp_path, hierarchy="fixture-hierarchy")] == [selected.resolve()]
    finally:
        unregister_mailbox_hierarchy_parser("fixture-hierarchy")


@pytest.mark.parametrize("newline", (b"\n", b"\r\n"))
def test_rmail_babyl_stream_preserves_messages(tmp_path: Path, newline: bytes) -> None:
    """Requirement: Emacs RMAIL Babyl files yield every reconstructed RFC 5322 message read-only."""
    path = tmp_path / "aliza"
    first = newline.join((b"From: aliza@example.org", b"Date: Mon, 16 Sep 85 21:53:28 EDT", b"Subject: one", b"", b"first", b"body"))
    second = newline.join((b"From: simsong@example.org", b"Date: Tue, 17 Sep 85 09:01:00 EDT", b"Subject: two", b"", b"second"))
    first_headers, _, first_body = first.partition(newline * 2)
    second_headers, _, second_body = second.partition(newline * 2)
    path.write_bytes(
        newline.join((b"Babyl Options:", b"Version: 5", b"\x1f\x0c", b"1,,")) + newline
        + first_headers + newline + b"*** EOOH ***" + newline + first_headers + newline * 2 + first_body
        + newline + b"\x1f\x0c" + newline + b"1,answered,," + newline
        + b"*** EOOH ***" + newline + second_headers + newline * 2 + second_body
        + newline + b"\x1f"
    )

    plugin = load_plugins().source("file-folder").implementation
    discovered = list(plugin.discover(SourceSpec(locator=str(path))))
    containers = [item for item in discovered if isinstance(item, MailContainer)]
    assert len(containers) == 1
    messages = list(plugin.messages(containers[0], None))
    first_offset = path.read_bytes().index(b"\x1f\x0c")
    second_offset = path.read_bytes().index(b"\x1f\x0c", first_offset + 2)

    assert containers[0].parser_kind == "babyl"
    assert all(isinstance(message, MailObject) for message in messages)
    assert [message.raw for message in messages] == [first, second]
    assert [message.cursor for message in messages] == [str(first_offset), str(second_offset)]
    assert messages[-1].completed_bytes == path.stat().st_size
    assert path.read_bytes().startswith(b"Babyl Options:" + newline)


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
