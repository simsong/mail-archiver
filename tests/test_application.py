# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Exercise document identity, window routing, startup, and shared ingest state."""

from __future__ import annotations

import sqlite3
import hashlib
from pathlib import Path
from threading import Event, Thread
from time import monotonic, sleep

import pytest

from mailarchiver.application import (
    ApplicationController,
    ApplicationPreferences,
    ApplicationPreferencesStore,
    ArchiveBusyError,
    IngestJob,
    InvalidArchiveError,
    WriterLease,
    create_empty_archive,
)
from mailarchiver.bagit import initialize_bag
from mailarchiver.catalog import create_catalog, create_search
from mailarchiver.__main__ import IngestInterrupted, IngestRequest, run_ingest
from mailarchiver.ingest_status import read_ingest_history
from mailarchiver.standalone_verify import verify_archive


def make_archive(path: Path) -> Path:
    initialize_bag(path)
    create_catalog(path / "archive.sqlite3").close()
    create_search(path / "search.sqlite3").close()
    return path


def controller(tmp_path: Path) -> ApplicationController:
    return ApplicationController(ApplicationPreferencesStore(tmp_path / "preferences.json"))


def test_gui_stop_checkpoints_partial_import_and_reimport_has_no_duplicates(tmp_path: Path) -> None:
    """Quit stops the shared ingest service, preserves committed bytes, and permits manual restart."""
    source = tmp_path / "source.mbox"
    digests = set()
    with source.open("wb") as output:
        for number in range(600):
            raw = (f"Message-ID: <stop-{number}@example.test>\nFrom: sender@example.test\n"
                   f"Date: Mon, 07 Sep 2026 12:00:00 +0000\nSubject: Stop fixture {number}\n\nBody\n").encode()
            digests.add(hashlib.sha256(raw).hexdigest())
            output.write(b"From sender@example.test Mon Sep  7 12:00:00 2026\n" + raw + b"\n")
    with source.open("rb") as stream:
        source_digest = hashlib.file_digest(stream, "sha256").hexdigest()
    owners = tmp_path / "owners.txt"
    owners.write_text("sender@example.test\n", encoding="utf-8")
    archive = tmp_path / "archive"
    request = IngestRequest(archive=archive, roots=[str(source)], owner_names_file=owners, scan_policy="not-scanned")
    stop = Event()
    errors: list[BaseException] = []

    def import_mail() -> None:
        try:
            run_ingest(request, stop_event=stop)
        except BaseException as error:
            errors.append(error)

    worker = Thread(target=import_mail, daemon=True)
    worker.start()
    count = 0
    deadline = monotonic() + 20
    try:
        while worker.is_alive() and monotonic() < deadline:
            try:
                with sqlite3.connect(f"{archive.as_uri()}/archive.sqlite3?mode=ro", uri=True) as catalog:
                    count = catalog.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            except sqlite3.OperationalError:
                pass  # The worker may not have created the catalog yet.
            if count:
                break
            sleep(0.005)
    finally:
        stop.set()
        worker.join(timeout=30)
    assert not worker.is_alive() and count > 0
    assert len(errors) == 1 and isinstance(errors[0], IngestInterrupted)
    assert read_ingest_history(archive).statuses[0].state == "interrupted"
    assert not verify_archive(archive)
    with sqlite3.connect(archive / "archive.sqlite3") as catalog:
        partial = catalog.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    assert 0 < partial < len(digests)
    assert (archive / "owner-names-detected.txt").read_text(encoding="utf-8") == "sender@example.test\n"
    run_ingest(request)
    with sqlite3.connect(archive / "archive.sqlite3") as catalog:
        rows = catalog.execute("SELECT sha256 FROM messages").fetchall()
    assert len(rows) == len(digests) and {row[0] for row in rows} == digests
    assert not verify_archive(archive)
    with source.open("rb") as stream:
        assert hashlib.file_digest(stream, "sha256").hexdigest() == source_digest


def test_confirmed_quit_stops_every_document_without_releasing_active_leases(tmp_path: Path) -> None:
    """All imports stop together; leases and owner-close guards remain until checkpoint completion."""
    from mailarchiver.gui_app import PyWebViewApplication

    state = controller(tmp_path)
    application = PyWebViewApplication(state)
    jobs = []
    try:
        for number in range(2):
            document = state.open_document(make_archive(tmp_path / f"archive-{number}"))
            assert document.path is not None
            window = state.new_search_window(document)
            job = IngestJob(operation_id=f"import-{number}", owner_window_id=window.window_id)
            lease = WriterLease.acquire(document.path, document.descriptor.identity, "test", job.operation_id, "test")
            state.begin_ingest(document.descriptor.document_id, job, lease)
            jobs.append(job)
        assert all(not job.stop.is_set() for job in jobs)
        assert application.stop_imports_for_quit() == tuple(jobs)
        assert all(job.stop.is_set() and not job.finished.is_set() for job in jobs)
        assert all(not state.can_close_window(job.owner_window_id) for job in jobs)
    finally:
        for document in state.documents():
            if document.ingest_job is not None:
                state.finish_ingest(document.descriptor.document_id, document.ingest_job.operation_id, published=False)


def test_same_archive_reuses_document_while_search_windows_remain_independent(tmp_path: Path) -> None:
    """Requirement: path aliases share one document while each search window owns its state."""
    archive = make_archive(tmp_path / "archive")
    application = controller(tmp_path)

    first_document = application.open_document(archive)
    assert first_document.path is not None
    second_document = application.open_document(archive / ".." / archive.name)
    assert second_document.path is not None
    first = application.new_search_window(first_document)
    second = application.new_search_window(second_document)
    first.query = "alpha"
    second.query = "beta"
    first.selected_message = 1
    second.selected_message = 2

    assert first_document is second_document
    assert set(first_document.window_ids) == {first.window_id, second.window_id}
    assert (first.query, first.selected_message) == ("alpha", 1)
    assert (second.query, second.selected_message) == ("beta", 2)
    assert application.active_window == second
    application.activate_window(first.window_id)
    assert application.active_document is first_document

    application.close_window(first.window_id)
    assert first_document.window_ids == (second.window_id,)
    application.close_window(second.window_id)
    with pytest.raises(ValueError, match="unknown archive document"):
        application.document(first_document.descriptor.document_id)


def test_startup_prefers_explicit_documents_then_last_archive_then_untitled(tmp_path: Path) -> None:
    """Requirement: startup applies explicit, last-valid, then Untitled precedence."""
    first_archive = make_archive(tmp_path / "first.mailarchive")
    second_archive = make_archive(tmp_path / "second.mailarchive")
    preferences = ApplicationPreferencesStore(tmp_path / "settings" / "preferences.json")
    preferences.write(
        ApplicationPreferences(last_archive=first_archive, recent_archives=[first_archive])
    )

    explicit = ApplicationController(preferences).startup((second_archive,))
    assert explicit.errors == []
    assert len(explicit.windows) == 1
    explicit_document = ApplicationController(preferences)
    reopened = explicit_document.startup()
    assert explicit_document.document(reopened.windows[0].document_id).display_path == second_archive

    application = ApplicationController(ApplicationPreferencesStore(tmp_path / "other-empty.json"))
    untitled = application.startup()
    assert application.document(untitled.windows[0].document_id).descriptor.untitled


def test_preference_write_failure_does_not_block_opening_an_archive(tmp_path: Path) -> None:
    """Requirement: discardable application preferences cannot prevent document access."""
    archive = make_archive(tmp_path / "archive")
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("block preference directory creation", encoding="utf-8")
    application = ApplicationController(
        ApplicationPreferencesStore(blocked_parent / "preferences.json")
    )

    document = application.open_document(archive)
    assert document.path is not None

    assert document.display_path == archive
    assert application.preferences.last_archive == archive
    assert application.preference_error is not None
    assert "Could not write application preferences" in application.preference_error


def test_preferences_round_trip_unicode_archive_paths_as_utf8(tmp_path: Path) -> None:
    """Requirement: atomic preferences preserve user-selected Unicode path text as UTF-8."""
    store = ApplicationPreferencesStore(tmp_path / "preferences.json")
    archive = tmp_path / "Courrier Été 日本語.mailarchive"
    preferences = ApplicationPreferences(last_archive=archive, recent_archives=[archive])

    store.write(preferences)

    assert store.read() == preferences
    assert "Courrier Été 日本語" in store.path.read_text(encoding="utf-8")


def test_missing_last_archive_is_removed_without_recreation(tmp_path: Path) -> None:
    """Requirement: a missing recent archive is reported and never recreated or mutated."""
    missing = tmp_path / "missing.mailarchive"
    store = ApplicationPreferencesStore(tmp_path / "preferences.json")
    store.write(ApplicationPreferences(last_archive=missing, recent_archives=[missing]))
    application = ApplicationController(store)

    result = application.startup()

    assert result.errors == [f"archive does not exist or is not a directory: {missing}"]
    assert not missing.exists()
    assert store.read() == ApplicationPreferences()
    assert application.document(result.windows[0].document_id).descriptor.untitled


def test_invalid_saved_database_is_ignored_and_removed_from_recents(tmp_path: Path) -> None:
    """Requirement: startup never opens or repairs an invalid saved SQLite database."""
    archive = make_archive(tmp_path / "invalid.mailarchive")
    (archive / "archive.sqlite3").write_bytes(b"not a SQLite database")
    store = ApplicationPreferencesStore(tmp_path / "preferences.json")
    store.write(ApplicationPreferences(last_archive=archive, recent_archives=[archive]))

    application = ApplicationController(store)
    result = application.startup()

    assert len(result.errors) == 1
    assert "archive databases are invalid" in result.errors[0]
    assert (archive / "archive.sqlite3").read_bytes() == b"not a SQLite database"
    assert store.read() == ApplicationPreferences()
    assert application.document(result.windows[0].document_id).descriptor.untitled


def test_open_rejects_wrong_database_schema_without_mutation(tmp_path: Path) -> None:
    """Requirement: Open checks both schemas read-only before creating a document."""
    archive = make_archive(tmp_path / "wrong-schema.mailarchive")
    search = archive / "search.sqlite3"
    search.unlink()
    database = sqlite3.connect(search)
    database.execute("CREATE TABLE unrelated(value TEXT)")
    database.commit()
    database.close()
    before = search.read_bytes()

    with pytest.raises(InvalidArchiveError, match="search database"):
        controller(tmp_path).open_document(archive)

    assert search.read_bytes() == before


def test_create_empty_archive_requires_a_new_or_empty_destination(tmp_path: Path) -> None:
    """Requirement: New initializes the selected destination and never overwrites content."""
    destination = tmp_path / "New.mailarchive"

    document = create_empty_archive(destination)

    assert document.display_path == destination
    assert (destination / "bagit.txt").is_file()
    assert (destination / "archive.sqlite3").is_file()
    assert (destination / "search.sqlite3").is_file()
    assert (destination / "status" / "archive-write.lock").is_file()

    occupied = tmp_path / "occupied.mailarchive"
    occupied.mkdir()
    sentinel = occupied / "keep.txt"
    sentinel.write_text("preserve me", encoding="utf-8")
    with pytest.raises(InvalidArchiveError, match="not empty"):
        create_empty_archive(occupied)
    assert sentinel.read_text(encoding="utf-8") == "preserve me"


def test_invalid_open_does_not_modify_the_candidate_directory(tmp_path: Path) -> None:
    """Requirement: Open validates non-mutatingly and never initializes an invalid directory."""
    invalid = tmp_path / "not-an-archive"
    invalid.mkdir()
    sentinel = invalid / "keep.txt"
    sentinel.write_text("unchanged", encoding="utf-8")
    application = controller(tmp_path)

    with pytest.raises(InvalidArchiveError, match="archive.sqlite3, search.sqlite3"):
        application.open_document(invalid)

    assert list(invalid.iterdir()) == [sentinel]
    assert sentinel.read_text(encoding="utf-8") == "unchanged"


def test_one_ingest_is_shared_per_document_and_requires_a_writer_lease(tmp_path: Path) -> None:
    """Requirement: one shared ingest job requires issue 70's archive-specific OS lease."""
    first = make_archive(tmp_path / "first")
    second = make_archive(tmp_path / "second")
    application = controller(tmp_path)
    first_document = application.open_document(first)
    assert first_document.path is not None
    second_document = application.open_document(second)
    assert second_document.path is not None
    first_window = application.new_search_window(first_document)
    second_window = application.new_search_window(first_document)
    other_window = application.new_search_window(second_document)
    first_job = IngestJob(operation_id="first-ingest", owner_window_id=first_window.window_id)
    first_lease = WriterLease.acquire(
        first, first_document.descriptor.identity, "test ingest", "first-ingest", "test"
    )

    application.begin_ingest(first_document.descriptor.document_id, first_job, first_lease)
    assert not application.can_close_window(first_window.window_id)
    assert application.can_close_window(second_window.window_id)
    assert application.activate_window(second_window.window_id).document_id == first_document.descriptor.document_id
    with pytest.raises(ArchiveBusyError, match="first-ingest"):
        application.import_document()
    with pytest.raises(ValueError, match="writer lease"):
        application.begin_ingest(
            second_document.descriptor.document_id,
            IngestJob(operation_id="wrong-lease", owner_window_id=other_window.window_id),
            first_lease,
        )

    second_job = IngestJob(operation_id="second-ingest", owner_window_id=other_window.window_id)
    second_lease = WriterLease.acquire(
        second, second_document.descriptor.identity, "test ingest", "second-ingest", "test"
    )
    application.begin_ingest(second_document.descriptor.document_id, second_job, second_lease)
    refreshed = application.finish_ingest(
        first_document.descriptor.document_id, "first-ingest", published=True
    )

    assert set(refreshed) == {first_window.window_id, second_window.window_id}
    assert first_document.generation == 1
    assert second_document.ingest_job == second_job
    assert application.finish_ingest(second_document.descriptor.document_id, "second-ingest", published=False) == ()
    assert second_document.generation == 0


def test_child_windows_and_ingest_keep_a_document_alive(tmp_path: Path) -> None:
    """Requirement: closing a search window does not release shared child or ingest state."""
    archive = make_archive(tmp_path / "archive")
    application = controller(tmp_path)
    document = application.open_document(archive)
    assert document.path is not None
    window = application.new_search_window(document)
    application.attach_child_window(document.descriptor.document_id, "ingest-history")

    application.close_window(window.window_id)
    assert application.document(document.descriptor.document_id) is document
    application.close_child_window(document.descriptor.document_id, "ingest-history")
    with pytest.raises(ValueError, match="unknown archive document"):
        application.document(document.descriptor.document_id)

    document = application.open_document(archive)
    assert document.path is not None
    window = application.new_search_window(document)
    job = IngestJob(operation_id="active-ingest", owner_window_id=window.window_id)
    lease = WriterLease.acquire(
        archive, document.descriptor.identity, "test ingest", "active-ingest", "test"
    )
    application.begin_ingest(document.descriptor.document_id, job, lease)
    with pytest.raises(ArchiveBusyError, match="cannot close"):
        application.close_window(window.window_id)
    assert application.document(document.descriptor.document_id).ingest_job == job
    application.finish_ingest(document.descriptor.document_id, job.operation_id, published=False)
    application.close_window(window.window_id)
    with pytest.raises(ValueError, match="unknown archive document"):
        application.document(document.descriptor.document_id)


def test_recent_archives_are_bounded_and_preserve_display_paths(tmp_path: Path) -> None:
    """Requirement: typed recent documents are ordered, deduplicated, and bounded."""
    application = controller(tmp_path)
    archives = [make_archive(tmp_path / f"archive-{index}") for index in range(12)]

    for archive in archives:
        application.open_document(archive)
    application.open_document(archives[-3])

    assert application.preferences.last_archive == archives[-3]
    assert application.preferences.recent_archives == [
        archives[-3], archives[-1], archives[-2], *reversed(archives[2:-3])
    ]
    assert len(application.preferences.recent_archives) == 10


@pytest.mark.parametrize("name", ["archive.sqlite3", "search.sqlite3"])
@pytest.mark.parametrize("alias", ["symlink", "hardlink"])
def test_database_aliases_are_rejected_without_mutation(tmp_path: Path, name: str, alias: str) -> None:
    """Requirement: aliased databases cannot bypass another archive's writer lease."""
    archive = make_archive(tmp_path / "archive")
    outside = make_archive(tmp_path / "outside") / name
    before = outside.read_bytes()
    target = archive / name
    target.unlink()
    if alias == "symlink":
        target.symlink_to(outside)
    else:
        target.hardlink_to(outside)
    with pytest.raises(InvalidArchiveError, match="regular file with one link"):
        controller(tmp_path).open_document(archive)
    assert outside.read_bytes() == before


def test_case_alias_shares_document_on_case_insensitive_filesystem(tmp_path: Path) -> None:
    """Requirement: directory identity survives alternate case on macOS volumes."""
    archive = make_archive(tmp_path / "CaseArchive")
    alias = tmp_path / "casearchive"
    if not alias.exists():
        pytest.skip("requires a case-insensitive filesystem")
    application = controller(tmp_path)
    assert application.open_document(archive) is application.open_document(alias)


def test_failed_discovery_does_not_claim_publication(tmp_path: Path) -> None:
    """Requirement: pre-publication failure must not trigger a document refresh."""
    from mailarchiver.__main__ import IngestOutcome, IngestRequest, run_ingest

    archive = make_archive(tmp_path / "archive")
    outcome = IngestOutcome()
    (tmp_path / "owners.txt").write_text("owner\n", encoding="utf-8")
    request = IngestRequest(archive=archive, owner_names_file=tmp_path / "owners.txt", roots=["unsupported://fixture"])
    with pytest.raises(ValueError, match="no source plug-in recognized"):
        run_ingest(request, outcome=outcome, terminal=False)
    assert not outcome.published


def test_quit_keeps_active_import_document_alive(tmp_path: Path) -> None:
    """Requirement: native Quit must retain the UI while an import owns its lease."""
    from mailarchiver.gui_app import PyWebViewApplication

    application = controller(tmp_path)
    document = application.open_document(make_archive(tmp_path / "archive"))
    assert document.path is not None
    window = application.new_search_window(document)
    host = PyWebViewApplication(application)
    assert document.path is not None
    lease = WriterLease.acquire(document.path, document.descriptor.identity, "test", "quit-test", "test")
    application.begin_ingest(document.descriptor.document_id, IngestJob(operation_id="quit-test", owner_window_id=window.window_id), lease)
    try:
        assert not host.prepare_quit()
        assert lease.acquired
        assert application.active_document is document
    finally:
        application.finish_ingest(document.descriptor.document_id, "quit-test", published=False)
    assert host.prepare_quit()


def test_child_window_keeps_document_routing_without_search_windows(tmp_path: Path) -> None:
    """Requirement: an Ingests window continues routing actions after searches close."""
    from mailarchiver.gui_app import PyWebViewApplication

    application = controller(tmp_path)
    document = application.open_document(make_archive(tmp_path / "archive"))
    assert document.path is not None
    session = application.new_search_window(document)
    application.attach_child_window(document.descriptor.document_id, "ingests-fixture")
    host = PyWebViewApplication(application)
    host._native_child_ids["ingests-fixture"] = document.descriptor.document_id
    application.close_window(session.window_id)
    assert host.document_for_native_window("ingests-fixture") is document
    assert host.document_for_native_window("unknown") is None


def test_renamed_application_preserves_existing_settings(tmp_path: Path) -> None:
    """Requirement: the product rename must not strand preferences or OAuth files."""
    from mailarchiver.identity import APPLICATION_NAME, LEGACY_DIRECTORY_NAME, application_data_directory

    current = tmp_path / APPLICATION_NAME
    legacy = tmp_path / LEGACY_DIRECTORY_NAME
    assert application_data_directory(tmp_path) == current
    assert not current.exists()
    legacy.mkdir()
    preferences = legacy / "preferences.json"
    preferences.write_bytes(b'{"recent_archives": []}')
    assert application_data_directory(tmp_path) == legacy
    assert preferences.read_bytes() == b'{"recent_archives": []}'
    current.write_text("unrelated file", encoding="utf-8")
    assert application_data_directory(tmp_path) == legacy
    assert current.read_text(encoding="utf-8") == "unrelated file"
    current.unlink()
    current.mkdir()
    assert application_data_directory(tmp_path) == legacy
    (current / ".DS_Store").write_bytes(b"incidental metadata")
    assert application_data_directory(tmp_path) == legacy
    (current / "auth").mkdir()
    assert application_data_directory(tmp_path) == current
    (current / "auth").rmdir()
    (current / "preferences.json").write_text("{}", encoding="utf-8")
    assert application_data_directory(tmp_path) == current
    assert preferences.exists()


def test_forced_setup_bypasses_archives_without_touching_preferences(tmp_path: Path) -> None:
    """Startup setup requirement: --new preserves saved paths and never opens them."""
    archive = make_archive(tmp_path / "saved")
    preferences = ApplicationPreferencesStore(tmp_path / "preferences.json")
    preferences.write(ApplicationPreferences(last_archive=archive, recent_archives=[archive]))
    before = preferences.path.read_bytes()
    application = ApplicationController(preferences)
    result = application.startup((tmp_path / "missing-explicit-archive",), new=True)
    assert not result.errors
    assert all(application.document(window.document_id).descriptor.untitled for window in result.windows)
    assert preferences.path.read_bytes() == before
    application.close_window(result.windows[0].window_id)
    normal = application.startup()
    assert application.document(normal.windows[0].document_id).path == archive


def test_setup_rejects_overlapping_source_and_destination_without_writes(tmp_path: Path) -> None:
    """Setup requirement: prevent recursive self-import, including directory aliases."""
    from mailarchiver.application import SetupSelection

    source = tmp_path / "source"
    source.mkdir()
    message = source / "message.eml"
    message.write_bytes(b"Subject: Original\n\nUntouched\n")
    alias = tmp_path / "alias"
    alias.symlink_to(source, target_is_directory=True)
    for destination in (source, tmp_path, source / "new-archive", alias / "new-archive"):
        with pytest.raises(ValueError, match="separate"):
            SetupSelection(source=source, destination=destination).validate_paths()
    with pytest.raises(ValueError, match="root folder"):
        SetupSelection(source=message, destination=tmp_path / "archive").validate_paths()
    SetupSelection(source=source, destination=tmp_path / "archive").validate_paths()
    assert sorted(path.name for path in source.iterdir()) == ["message.eml"]
    assert message.read_bytes() == b"Subject: Original\n\nUntouched\n"
    assert not (tmp_path / "archive").exists()


@pytest.mark.parametrize("alias_name", ["Mail source u\u0308", "MAIL SOURCE Ü"])
def test_setup_rejects_native_unicode_and_case_aliases(tmp_path: Path, alias_name: str) -> None:
    """Setup requirement: Cocoa Unicode/case path spellings cannot bypass source isolation."""
    from mailarchiver.application import SetupSelection

    source = tmp_path / "Mail source ü"
    source.mkdir()
    alias = tmp_path / alias_name
    if not alias.exists():
        pytest.skip("filesystem treats this spelling as a different directory")
    assert source.samefile(alias)
    for destination in (alias, alias / "new-archive"):
        with pytest.raises(ValueError, match="separate"):
            SetupSelection(source=source, destination=destination).validate_paths()
        with pytest.raises(ValueError, match="separate"):
            SetupSelection(source=alias, destination=source).validate_paths()
    assert not list(source.iterdir())
