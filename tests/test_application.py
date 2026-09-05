"""Exercise document identity, window routing, startup, and shared ingest state."""

from __future__ import annotations

import sqlite3
from pathlib import Path

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


def make_archive(path: Path) -> Path:
    initialize_bag(path)
    create_catalog(path / "archive.sqlite3").close()
    create_search(path / "search.sqlite3").close()
    return path


def controller(tmp_path: Path) -> ApplicationController:
    return ApplicationController(ApplicationPreferencesStore(tmp_path / "preferences.json"))


def test_same_archive_reuses_document_while_search_windows_remain_independent(tmp_path: Path) -> None:
    """Requirement: path aliases share one document while each search window owns its state."""
    archive = make_archive(tmp_path / "archive")
    application = controller(tmp_path)

    first_document = application.open_document(archive)
    second_document = application.open_document(archive / ".." / archive.name)
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
    second_document = application.open_document(second)
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
    application.finish_ingest(second_document.descriptor.document_id, "second-ingest", published=False)


def test_child_windows_and_ingest_keep_a_document_alive(tmp_path: Path) -> None:
    """Requirement: closing a search window does not release shared child or ingest state."""
    archive = make_archive(tmp_path / "archive")
    application = controller(tmp_path)
    document = application.open_document(archive)
    window = application.new_search_window(document)
    application.attach_child_window(document.descriptor.document_id, "ingest-history")

    application.close_window(window.window_id)
    assert application.document(document.descriptor.document_id) is document
    application.close_child_window(document.descriptor.document_id, "ingest-history")
    with pytest.raises(ValueError, match="unknown archive document"):
        application.document(document.descriptor.document_id)

    document = application.open_document(archive)
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
