"""Verify GUI services search, sanitize, preview, and export without archive writes."""

from __future__ import annotations

import base64
import hashlib
import json
import mailbox
import sqlite3
import sys
import time
import zipfile
from pathlib import Path

import pytest

from mailarchiver.bagit import initialize_bag
from mailarchiver.catalog import address_pk, create_catalog, create_search
from mailarchiver.gui_service import (
    RAW_PART_ID,
    attachment_content,
    attachment_descriptor,
    describe_message,
    is_risky,
    message_previews,
    render_part,
    searchable_message_count,
    search_page,
    search_suggestions,
    write_attachment,
    write_message,
)
from mailarchiver.gui_app import (
    GUI_DIRECTORY,
    GuiApi,
    application_menu,
    application_metadata,
    configure_macos_application,
)
from mailarchiver.mailsearch import _search_statement, parse_query
from mailarchiver.layout import mbox_directory
from mailarchiver.mailbox_tree import FilterSet, FilterSetStore, MailboxSelection, MailboxTreeNode, mailbox_tree
from mailarchiver.plugin_api import SourceContainerMetadata, SourceRelationship
from mailarchiver.mbox import add_message
from mailarchiver.search import index_message
from mailarchiver.standalone_verify import semantic_bytes


SIMPLE_MESSAGE = (
    b"Message-ID: <simple@example>\nFrom: sender@example.net\nTo: recipient@example.net\n"
    b"Subject: annual plan\nDate: Wed, 03 Jan 2024 10:00:00 +0000\n\nThe report is ready.\n"
)
MULTIPART_MESSAGE = (
    b"Message-ID: <multipart@example>\nFrom: sender@example.net\nTo: recipient@example.net\n"
    b"Subject: multipart message\nDate: Thu, 04 Jan 2024 10:00:00 +0000\n"
    b"Content-Type: multipart/mixed; boundary=outer\n\n"
    b"--outer\nContent-Type: multipart/alternative; boundary=alternative\n\n"
    b"--alternative\nContent-Type: text/plain; charset=utf-8\n\nPlain version.\n"
    b"--alternative\nContent-Type: text/html; charset=utf-8\n\n"
    b'<html><body onload="bad()"><script>bad()</script><p>HTML version.</p>'
    b'<img src="cid:logo"><img src="https://tracker.example/pixel"></body></html>\n'
    b"--alternative--\n"
    b"--outer\nContent-Type: image/png\nContent-ID: <logo>\nContent-Disposition: inline; filename=logo.png\n"
    b"Content-Transfer-Encoding: base64\n\naW1hZ2U=\n"
    b"--outer\nContent-Type: application/pdf\nContent-Disposition: attachment; filename=../report.pdf\n"
    b"Content-Transfer-Encoding: base64\n\nJVBERi0xLjQK\n"
    b"--outer\nContent-Type: text/plain; charset=utf-8\nContent-Disposition: attachment; filename=notes.txt\n\n"
    b"Appendixquartz appears only in this attachment.\n"
    b"--outer--\n"
)


def make_gui_archive(tmp_path: Path) -> Path:
    archive = tmp_path / "archive"
    initialize_bag(archive)
    catalog = create_catalog(archive / "archive.sqlite3")
    search = create_search(archive / "search.sqlite3")
    message_pks: list[int] = []
    try:
        sender = address_pk(catalog, "sender@example.net")
        recipient = address_pk(catalog, "recipient@example.net")
        for raw, message_id, subject, timestamp in (
            (SIMPLE_MESSAGE, "simple@example", "annual plan", "2024-01-03T10:00:00+00:00"),
            (MULTIPART_MESSAGE, "multipart@example", "multipart message", "2024-01-04T10:00:00+00:00"),
        ):
            cursor = catalog.execute(
                "INSERT INTO messages(message_id_normalized, sha256, sender_address_pk, subject, date_utc, date_source, category) "
                "VALUES (?, ?, ?, ?, ?, 'date', 'Archive')",
                (message_id, hashlib.sha256(raw).hexdigest(), sender, subject, timestamp),
            )
            message_pks.append(int(cursor.lastrowid))
            catalog.execute(
                "INSERT INTO recipients(message_pk, address_pk, role) VALUES (?, ?, 'to')",
                (cursor.lastrowid, recipient),
            )
            index_message(search, raw, True)
        catalog.commit()
        search.commit()
    finally:
        catalog.close()
        search.close()
    path = mbox_directory(archive) / "2024-Archive1.mbox"
    box = mailbox.mbox(path)
    try:
        locations = [add_message(box, path, raw) for raw in (SIMPLE_MESSAGE, MULTIPART_MESSAGE)]
    finally:
        box.close()
    catalog = create_catalog(archive / "archive.sqlite3")
    try:
        generation = catalog.execute(
            "INSERT INTO mbox_generations(filename, sha256, message_count, byte_count) VALUES (?, '', 0, 0) "
            "RETURNING generation_pk",
            (path.name,),
        ).fetchone()
        assert generation is not None
        catalog.executemany(
            "INSERT INTO locations(message_pk, generation_pk, byte_offset, byte_length) VALUES (?, ?, ?, ?)",
            ((message_pk, generation[0], location.byte_offset, location.byte_length)
             for message_pk, location in zip(message_pks, locations)),
        )
        run = catalog.execute("INSERT INTO ingest_runs(started_at) VALUES (?)", ("2026-08-23T00:00:00+00:00",)).lastrowid
        volume = catalog.execute(
            "INSERT INTO source_volumes(identity_json, metadata_json, first_observed_at, last_observed_at) VALUES (?, ?, ?, ?)",
            (
                json.dumps({"kind": "local-volume", "stable_id": "fixture"}),
                json.dumps({"volume_label": "Fixture Backup", "current_mount_path": "/Volumes/Fixture"}),
                "2026-08-23T00:00:00+00:00", "2026-08-23T00:00:00+00:00",
            ),
        ).lastrowid
        source_file = catalog.execute(
            "INSERT INTO source_files(source_volume_pk, source_path, hierarchy_path, path_kind, source_kind) "
            "VALUES (?, 'mail/simple.eml', 'mail/simple.eml', 'file', 'message')",
            (volume,),
        ).lastrowid
        catalog.execute(
            "INSERT INTO observations(run_pk, message_pk, source_file_pk, source_offset, raw_sha256, semantic_sha256, disposition, detail) "
            "VALUES (?, ?, ?, 0, ?, ?, 'archived', 'Archive')",
            (run, message_pks[0], source_file, hashlib.sha256(SIMPLE_MESSAGE).hexdigest(),
             hashlib.sha256(semantic_bytes(SIMPLE_MESSAGE)).hexdigest()),
        )
        catalog.commit()
    finally:
        catalog.close()
    return archive


def test_gui_search_field_preserves_selector_and_quote_semantics(tmp_path: Path) -> None:
    """Requirement: selectors and quoted phrases can coexist in one GUI search field."""
    archive = make_gui_archive(tmp_path)

    assert [result.subject for result in search_page(archive, "subject:annual report").results] == ["annual plan"]
    assert search_page(archive, 'subject:"annual report"').results == []
    assert [result.subject for result in search_page(archive, 'subject:"annual plan" report').results] == ["annual plan"]


def test_gui_results_and_message_views_expose_stable_mail_ids(tmp_path: Path) -> None:
    """Requirement: users can copy a stable mid identifier from both panes."""
    archive = make_gui_archive(tmp_path)

    assert search_page(archive, "mid-1").results[0].mail_id == "mid-1"
    assert describe_message(archive, 1).mail_id == "mid-1"


def test_gui_application_metadata_names_the_product() -> None:
    """Requirement: native menus and the About panel identify Mail Archiver, not Python."""
    metadata = application_metadata()

    assert metadata.name == "Mail Archiver"
    assert metadata.version == "0.1.0"
    assert "Mail Archiver" in metadata.copyright


def test_gui_windows_menu_opens_the_ingest_browser(tmp_path: Path) -> None:
    """Requirement: the native Windows menu exposes the independent ingest browser."""
    api = GuiApi(None, e2e_directory=tmp_path)
    try:
        menus = application_menu(api)
        assert [menu.title for menu in menus] == ["Windows"]
        assert [item.title for item in menus[0].items] == ["Ingest"]
        assert menus[0].items[0].function()
    finally:
        api.close()


def test_gui_uses_the_canonical_rainbow_icon() -> None:
    """Requirement: the native application has one canonical high-resolution icon."""
    assert sorted(path.name for path in (GUI_DIRECTORY / "icons").glob("*.svg")) == ["rainbow-post.svg"]


@pytest.mark.skipif(sys.platform != "darwin", reason="Cocoa metadata is macOS-specific")
def test_gui_applies_application_metadata_to_cocoa() -> None:
    """Requirement: the live Cocoa process and bundle receive the product metadata."""
    from AppKit import NSApplication  # pylint: disable=import-outside-toplevel,no-name-in-module,import-error
    from Foundation import NSBundle, NSProcessInfo  # pylint: disable=import-outside-toplevel,no-name-in-module,import-error

    configure_macos_application()
    info = NSBundle.mainBundle().localizedInfoDictionary() or NSBundle.mainBundle().infoDictionary()

    assert NSProcessInfo.processInfo().processName() == "Mail Archiver"
    assert info["CFBundleName"] == "Mail Archiver"
    assert info["CFBundleShortVersionString"] == "0.1.0"
    assert NSApplication.sharedApplication().applicationIconImage() is not None


def test_gui_search_sorting_is_whitelisted_and_stable(tmp_path: Path) -> None:
    """Requirement: GUI results sort by date, subject, or sender in either direction."""
    archive = make_gui_archive(tmp_path)

    ascending = search_page(archive, "", sort_by="subject", direction="ascending")
    descending = search_page(archive, "", sort_by="subject", direction="descending")

    assert [result.subject for result in ascending.results] == ["annual plan", "multipart message"]
    assert [result.subject for result in descending.results] == ["multipart message", "annual plan"]
    with pytest.raises(ValueError):
        search_page(archive, "", sort_by="subject; DROP TABLE messages")


def test_gui_search_results_include_indexed_attachment_counts(tmp_path: Path) -> None:
    """Requirement: result rows receive attachment counts without rereading canonical MBOX data."""
    archive = make_gui_archive(tmp_path)

    results = search_page(archive, "", sort_by="date", direction="ascending").results

    assert [(result.message_pk, result.attachment_count) for result in results] == [(1, 0), (2, 3)]


def test_gui_attachment_checkbox_expands_full_text_search(tmp_path: Path) -> None:
    """Requirement: attachment terms affect GUI results only when attachment search is selected."""
    archive = make_gui_archive(tmp_path)

    assert search_page(archive, "Appendixquartz").results == []
    assert [result.message_pk for result in search_page(archive, "Appendixquartz", search_attachments=True).results] == [2]
    assert [result.message_pk for result in search_page(archive, "Plain Appendixquartz", search_attachments=True).results] == [2]


def test_gui_suggestions_use_trigram_substrings_and_deduplicated_message_counts(tmp_path: Path) -> None:
    """Requirement: partial names, addresses, and subject substrings produce ranked completions."""
    archive = make_gui_archive(tmp_path)
    search = create_search(archive / "search.sqlite3")
    catalog = create_catalog(archive / "archive.sqlite3")
    raw_messages: list[bytes] = []
    try:
        sender = address_pk(catalog, "beth@example.org")
        for number, subject in enumerate(("Flight for ELISABETH", "Ordinary subject"), 1):
            raw = "".join((
                f"Message-ID: <suggestion-{number}@example>\n"
                "From: Beth Rosenberg <beth@example.org>\n",
                "Cc: Beth Rosenberg <beth@example.org>\n" if number == 1 else "",
                f"Subject: {subject}\n\nbody\n",
            )).encode()
            raw_messages.append(raw)
            index_message(search, raw, False, date_utc=f"2024-01-0{number}T00:00:00+00:00")
            catalog.execute(
                "INSERT INTO messages(message_id_normalized, sha256, sender_address_pk, subject, date_utc, "
                "date_source, category) VALUES (?, ?, ?, ?, '2024-01-01T00:00:00+00:00', 'date', 'Archive')",
                (f"suggestion-{number}@example", hashlib.sha256(raw).hexdigest(), sender, subject),
            )
        index_message(search, raw_messages[0], False, date_utc="2024-01-01T00:00:00+00:00")
        search.commit()
        catalog.commit()
    finally:
        search.close()
        catalog.close()

    suggestions = search_suggestions(archive, "beth")

    assert [(item.address, item.display_name, item.message_count, item.last_seen) for item in suggestions.addresses] == [
        ("beth@example.org", "Beth Rosenberg", 2, "2024-01-02T00:00:00+00:00")
    ]
    assert [(item.subject, item.message_count) for item in suggestions.subjects] == [
        ("Flight for ELISABETH", 1)
    ]
    assert search_suggestions(archive, "be").addresses == []
    assert searchable_message_count(archive) == 4


def test_gui_address_suggestions_break_frequency_ties_by_recency(tmp_path: Path) -> None:
    """Requirement: equally frequent address completions prefer the most recently seen address."""
    archive = make_gui_archive(tmp_path)
    search = create_search(archive / "search.sqlite3")
    try:
        index_message(
            search, b"From: Older Match <older-match@example.org>\n\nbody", False,
            date_utc="2024-01-01T00:00:00+00:00",
        )
        index_message(
            search, b"From: Newer Match <newer-match@example.org>\n\nbody", False,
            date_utc="2025-01-01T00:00:00+00:00",
        )
        search.commit()
    finally:
        search.close()

    assert [item.address for item in search_suggestions(archive, "match").addresses] == [
        "newer-match@example.org",
        "older-match@example.org",
    ]


def test_gui_loads_indexed_previews_on_its_background_worker(tmp_path: Path) -> None:
    """Requirement: result metadata is independent from asynchronously loaded body previews."""
    archive = make_gui_archive(tmp_path)
    assert [(item.message_pk, item.preview) for item in message_previews(archive, [1, 2])] == [
        (1, "The report is ready."),
        (2, "Plain version."),
    ]
    api = GuiApi(archive)
    try:
        assert api.request_previews([1, 2])
        deadline = time.monotonic() + 2
        while (batch := api.take_previews([1, 2]))["pending"] and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        api.close()

    assert not batch["pending"]
    assert batch["error"] is None
    assert [(item["message_pk"], item["preview"]) for item in batch["previews"]] == [
        (1, "The report is ready."),
        (2, "Plain version."),
    ]


def test_gui_selects_multipart_views_and_sanitizes_html(tmp_path: Path) -> None:
    """Requirement: body alternatives are selectable and HTML is inert with remote content blocked."""
    archive = make_gui_archive(tmp_path)
    view = describe_message(archive, 2)
    plain_id = next(part.part_id for part in view.body_parts if part.content_type == "text/plain")
    html_id = next(part.part_id for part in view.body_parts if part.content_type == "text/html")

    assert {part.content_type for part in view.body_parts} == {"text/plain", "text/html", "message/rfc822"}
    assert view.preferred_part_id == html_id
    assert [(item.filename, item.preview) for item in view.attachments] == [
        ("logo.png", "image"),
        ("report.pdf", "pdf"),
        ("notes.txt", None),
    ]
    assert render_part(archive, 2, plain_id).content == "Plain version."
    blocked = render_part(archive, 2, html_id)
    assert "<script" not in blocked.content
    assert "onload" not in blocked.content
    assert "data:image/png;base64,aW1hZ2U=" in blocked.content
    assert "tracker.example" not in blocked.content
    assert blocked.remote_content_blocked
    allowed = render_part(archive, 2, html_id, allow_remote=True)
    assert "https://tracker.example/pixel" in allowed.content
    assert not allowed.remote_content_blocked
    assert "Subject: multipart message" in render_part(archive, 2, RAW_PART_ID).content


def test_gui_displays_archive_and_source_locations(tmp_path: Path) -> None:
    """Requirement: the message viewer distinguishes archive mailboxes from source discoveries."""
    archive = make_gui_archive(tmp_path)

    view = describe_message(archive, 1)

    assert view.archive_path == "data/mbox/2024-Archive1.mbox:0"
    assert [(item.volume, item.path, item.offset) for item in view.source_locations] == [
        ("Fixture Backup", "mail/simple.eml", 0)
    ]
    assert view.source_locations[0].origin == "Local source"
    assert not view.source_locations[0].preferred


def test_gui_prefers_direct_cloud_observation_over_local_cache(tmp_path: Path) -> None:
    """Requirement: direct provider provenance precedes a retained local-cache observation."""
    archive = make_gui_archive(tmp_path)
    database = sqlite3.connect(archive / "archive.sqlite3")
    try:
        cache_metadata = SourceContainerMetadata(
            display_name="Apple Mail All Mail cache",
            hierarchy=("[Gmail]", "All Mail"),
            provenance_json="{}",
            relationship=SourceRelationship(
                role="cache", upstream_plugin_kind="gmail", account_hint="APPLE-ACCOUNT-UUID"
            ),
        ).model_dump_json()
        database.execute("UPDATE source_files SET metadata_json = ?", (cache_metadata,))
        provider_volume = database.execute(
            "INSERT INTO source_volumes(identity_json, metadata_json, first_observed_at, last_observed_at) "
            "VALUES (?, ?, '2026-08-28', '2026-08-28') RETURNING source_volume_pk",
            (
                json.dumps({"plugin_kind": "gmail", "source_id": "simsong@gmail.com"}),
                json.dumps({"plugin_kind": "gmail", "volume_label": "simsong@gmail.com"}),
            ),
        ).fetchone()
        assert provider_volume is not None
        provider_metadata = SourceContainerMetadata(
            display_name="All Mail",
            hierarchy=("All Mail",),
            provenance_json="{}",
        ).model_dump_json()
        provider_file = database.execute(
            "INSERT INTO source_files(source_volume_pk, source_plugin, source_path, hierarchy_path, "
            "metadata_json, path_kind, source_kind) VALUES (?, 'gmail', 'messages/1', 'All Mail', ?, "
            "'provider', 'gmail') RETURNING source_file_pk",
            (provider_volume[0], provider_metadata),
        ).fetchone()
        assert provider_file is not None
        run_pk = database.execute("SELECT min(run_pk) FROM ingest_runs").fetchone()[0]
        database.execute(
            "INSERT INTO observations(run_pk, message_pk, source_file_pk, source_cursor, raw_sha256, "
            "disposition, detail) VALUES (?, 1, ?, 'gmail-message-1', '', 'duplicate', "
            "'same Message-ID and SHA-256')",
            (run_pk, provider_file[0]),
        )
        database.commit()
    finally:
        database.close()

    view = describe_message(archive, 1)

    assert [item.origin for item in view.source_locations] == [
        "Direct Gmail source",
        "Local cache of Gmail",
    ]
    assert [item.preferred for item in view.source_locations] == [True, False]


def test_gui_exposes_computed_date_tag_for_warning_banner(tmp_path: Path) -> None:
    """Requirement: the GUI identifies messages whose catalog date replaced the Date header."""
    archive = make_gui_archive(tmp_path)
    database = sqlite3.connect(archive / "archive.sqlite3")
    try:
        database.execute("UPDATE messages SET date_source = 'received-median' WHERE message_pk = 1")
        database.commit()
    finally:
        database.close()

    assert describe_message(archive, 1).date_source == "received-median"


def test_gui_exports_exact_eml_and_decoded_attachment(tmp_path: Path) -> None:
    """Requirement: .eml export preserves RFC 5322 bytes and attachment export decodes the selected part."""
    archive = make_gui_archive(tmp_path)
    view = describe_message(archive, 2)
    pdf = next(item for item in view.attachments if item.content_type == "application/pdf")
    eml_path, pdf_path = tmp_path / "message.eml", tmp_path / "report.pdf"

    write_message(archive, 2, eml_path)
    write_attachment(archive, 2, pdf.part_id, pdf_path)

    assert eml_path.read_bytes() == MULTIPART_MESSAGE
    assert hashlib.sha256(eml_path.read_bytes()).hexdigest() == hashlib.sha256(MULTIPART_MESSAGE).hexdigest()
    assert pdf_path.read_bytes() == b"%PDF-1.4\n"
    descriptor = attachment_descriptor(archive, 2, pdf.part_id)
    assert (descriptor.filename, descriptor.content_type) == ("report.pdf", "application/pdf")
    content = attachment_content(archive, 2, pdf.part_id)
    assert base64.b64decode(content.content_base64) == pdf_path.read_bytes()
    assert content.filename == "report.pdf"


def test_gui_prepares_named_single_and_multi_message_exports(tmp_path: Path) -> None:
    """Requirement: drag-out exports use stable IDs and preserve exact message bytes."""
    archive = make_gui_archive(tmp_path)
    exports = tmp_path / "exports"
    api = GuiApi(archive, temporary_directory=exports)
    try:
        single = api.prepare_drag(1)
        bundle = api.prepare_drag_zip([1, 2])
    finally:
        api.close()

    assert single["filename"] == "mid-1.eml"
    assert (exports / "mid-1.eml").read_bytes() == SIMPLE_MESSAGE
    with zipfile.ZipFile(exports / "selected-messages.zip") as zipped:
        assert zipped.namelist() == ["mid-1.eml", "mid-2.eml"]
        assert zipped.read("mid-1.eml") == SIMPLE_MESSAGE
    assert bundle["filename"] == "selected-messages.zip"


def test_gui_flags_executable_attachment_types() -> None:
    """Requirement: opening executable-looking attachments requires explicit confirmation."""
    assert is_risky("installer.dmg", "application/octet-stream")
    assert is_risky("script", "application/x-sh")
    assert not is_risky("report.pdf", "application/pdf")


def find_tree_node(nodes: list[MailboxTreeNode], label: str) -> MailboxTreeNode:
    for node in nodes:
        if node.label == label:
            return node
        try:
            return find_tree_node(node.children, label)
        except AssertionError:
            continue
    raise AssertionError(f"tree has no node named {label}")


def add_tree_source(
    archive: Path, identity: str, label: str, path: str, source_kind: str, message_pks: list[int]
) -> None:
    database = sqlite3.connect(archive / "archive.sqlite3")
    try:
        volume = database.execute(
            "INSERT INTO source_volumes(identity_json, metadata_json, first_observed_at, last_observed_at) "
            "VALUES (?, ?, '2026-08-26', '2026-08-26') ON CONFLICT(identity_json) DO UPDATE SET "
            "metadata_json = excluded.metadata_json RETURNING source_volume_pk",
            (identity, json.dumps({"volume_label": label, "current_mount_path": f"/Volumes/{label}"})),
        ).fetchone()
        assert volume is not None
        source_file = database.execute(
            "INSERT INTO source_files(source_volume_pk, source_path, hierarchy_path, path_kind, source_kind) "
            "VALUES (?, ?, ?, 'file', ?) RETURNING source_file_pk",
            (volume[0], path, path, source_kind),
        ).fetchone()
        assert source_file is not None
        run_pk = database.execute("SELECT min(run_pk) FROM ingest_runs").fetchone()[0]
        database.executemany(
            "INSERT INTO observations(run_pk, message_pk, source_file_pk, raw_sha256, disposition, detail) "
            "VALUES (?, ?, ?, '', 'archived', 'Archive')",
            ((run_pk, message_pk, source_file[0]) for message_pk in message_pks),
        )
        database.commit()
    finally:
        database.close()


def test_original_mailbox_tree_deduplicates_counts_merges_volumes_and_filters(tmp_path: Path) -> None:
    """Requirement: tree counts and selected-path unions use canonical message identities."""
    archive = make_gui_archive(tmp_path)
    add_tree_source(archive, '{"stable_id":"backup-1"}', "Backup 1", "Professional/Inbox/work.mbox", "mbox", [1, 2])
    add_tree_source(archive, '{"stable_id":"backup-2"}', "Backup 2", "Professional/Inbox/work.mbox", "mbox", [1])
    add_tree_source(archive, '{"stable_id":"backup-1b"}', "Backup 1", "Personal/Loose/001.eml", "message", [1])

    merged = mailbox_tree(archive)
    professional = find_tree_node(merged, "Professional")
    loose = find_tree_node(merged, "Loose")

    assert professional.count == 2
    assert loose.count == 1
    assert not any(node.label == "001.eml" for node in loose.children)
    assert [item.message_pk for item in search_page(archive, "", mailbox_selections=[professional.selection]).results] == [2, 1]
    assert [item.message_pk for item in search_page(archive, "", mailbox_selections=[loose.selection]).results] == [1]

    by_volume = mailbox_tree(archive, show_volumes=True)
    assert {node.label for node in by_volume} >= {"Backup 1", "Backup 2"}
    backup_two = next(node for node in by_volume if node.label == "Backup 2")
    scoped = find_tree_node(backup_two.children, "Professional")
    assert scoped.count == 1
    assert [item.message_pk for item in search_page(archive, "", mailbox_selections=[scoped.selection]).results] == [1]


def test_filter_sets_are_versioned_and_atomically_managed(tmp_path: Path) -> None:
    """Requirement: named selections persist outside an archive and support rename/delete."""
    path = tmp_path / "preferences" / "filter-sets.json"
    store = FilterSetStore(path)

    saved = store.save(FilterSet(name="Work", selections=["selection-a"]))
    assert [(item.name, item.selections) for item in saved.filter_sets] == [("Work", ["selection-a"])]
    assert path.read_text(encoding="utf-8").endswith("\n")
    renamed = store.rename("Work", "Professional")
    assert [item.name for item in renamed.filter_sets] == ["Professional"]
    assert store.delete("Professional").filter_sets == []
    with pytest.raises(ValueError, match="reserved"):
        store.save(FilterSet(name="None"))


def test_original_mailbox_count_and_search_queries_use_provenance_indexes(tmp_path: Path) -> None:
    """Requirement: source-tree counts and pre-page filtering use the intended indexes."""
    archive = make_gui_archive(tmp_path)
    selection = MailboxSelection(path="mail")
    database = sqlite3.connect(archive / "archive.sqlite3")
    try:
        count_plan = database.execute(
            "EXPLAIN QUERY PLAN SELECT count(DISTINCT observations.message_pk) "
            "FROM source_files INDEXED BY source_files_hierarchy_volume "
            "JOIN observations INDEXED BY observations_source_file_offset USING (source_file_pk) "
            "WHERE observations.message_pk IS NOT NULL AND (source_files.hierarchy_path = ? OR "
            "(source_files.hierarchy_path >= ? AND source_files.hierarchy_path < ?))",
            ("mail", "mail/", "mail0"),
        ).fetchall()
        database.execute("ATTACH DATABASE ? AS search", (str(archive / "search.sqlite3"),))
        statement = _search_statement(parse_query(""), 10, mailbox_selections=[selection])
        search_plan = database.execute("EXPLAIN QUERY PLAN " + statement.sql, statement.parameters).fetchall()
    finally:
        database.close()

    assert any("source_files_hierarchy_volume" in detail for *_prefix, detail in count_plan)
    assert any("observations_source_file_offset" in detail for *_prefix, detail in count_plan)
    assert any("observations_message_pk" in detail for *_prefix, detail in search_plan)
