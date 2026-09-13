# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Desktop delivery requirements: actual no-scanner ingest and headless diagnostics.

Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""

import os
import sqlite3
import subprocess
import sys
from importlib import import_module
from pathlib import Path

import pytest

from mailarchiver.ingest_status import read_ingest_history
from mailarchiver.self_test import SelfTestReport
from mailarchiver.standalone_verify import verify_archive
from mailarchiver.scanner import clamav_prefix

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(sys.platform != "darwin", reason="AppKit renders the macOS installer background")
def test_dmg_retina_background_keeps_text_and_arrow_in_bounds(tmp_path: Path) -> None:
    """Desktop delivery: Retina background ink must align with the real Finder icons."""
    from scripts.dmg_layout import background_image, verify_background

    background = tmp_path / "background.tiff"
    background_image(background)
    verify_background(background)


@pytest.mark.skipif(sys.platform != "darwin", reason="Native import confirmation uses AppKit")
@pytest.mark.parametrize("buttons", [("Import", "Cancel"), ("Cancel", "Import Without Scanning", "Install ClamAV…")])
def test_import_confirmation_has_wide_selectable_body(buttons: tuple[str, ...]) -> None:
    """Import confirmation must retain complete paths in a wider native dialog."""
    AppKit = import_module("AppKit")
    from mailarchiver.gui_app import IMPORT_CONFIRMATION_WIDTH, create_macos_alert

    AppKit.NSApplication.sharedApplication()
    message = ("Destination archive: /Users/example/tiny.mailarchive\n\n"
               "Read-only sources:\n/Users/example/gits/mail-archiver/tests/data\n\n"
               "Sent-mail owner names: /Users/example/tiny.mailarchive/owner-names.txt")
    alert = create_macos_alert("Import into tiny.mailarchive", message, buttons,
                               body_width=IMPORT_CONFIRMATION_WIDTH)
    alert.layout()
    body = alert.accessoryView()
    assert body.stringValue() == message
    assert body.isSelectable()
    assert body.frame().size.width >= IMPORT_CONFIRMATION_WIDTH
    assert alert.window().frame().size.width >= IMPORT_CONFIRMATION_WIDTH
    assert [button.title() for button in alert.buttons()] == list(buttons)
    assert alert.buttons()[0].keyEquivalent() == ("\x1b" if buttons[0] == "Cancel" else "\r")


def test_scanner_discovery_requires_both_programs(tmp_path: Path) -> None:
    """A partially installed prefix must not hide a complete separate installation."""
    incomplete, complete = tmp_path / "incomplete", tmp_path / "complete"
    for prefix in (incomplete, complete):
        (prefix / "sbin").mkdir(parents=True)
        (prefix / "sbin/clamd").touch()
    (complete / "bin").mkdir()
    (complete / "bin/clamdscan").touch()
    assert clamav_prefix((incomplete, complete)) == complete


def test_headless_self_test_without_scanner_or_display(tmp_path: Path) -> None:
    """No user archive, saved preference, native window, or installed antivirus is required."""
    report_path = tmp_path / "report.json"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/desktop_entry.py"), "--self-test", "--report", str(report_path)],
        cwd=tmp_path, capture_output=True, text=True, check=False, timeout=30,
        env={**os.environ, "DISPLAY": "", "MAIL_ARCHIVE_DIR": str(tmp_path / "must-not-exist")},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = SelfTestReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    assert report.passed and report.mode == "headless"
    assert not (tmp_path / "must-not-exist").exists()


def test_scanner_failure_never_silently_imports_unscanned(tmp_path: Path) -> None:
    """Mandatory scanning fails closed; explicit opt-out retains evidence and bytes."""
    source = tmp_path / "message.eml"
    raw = b"From: sender@example.net\nDate: Mon, 07 Sep 2026 12:00:00 +0000\nSubject: Scanner regression\n\nOriginal bytes\n"
    source.write_bytes(raw)
    owners = tmp_path / "owners.txt"
    owners.write_text("sender@example.net\n", encoding="utf-8")
    environment = {**os.environ, "MAILARCHIVER_CLAMD_CONFIG": str(tmp_path / "absent.conf"),
                   "MAILARCHIVER_CLAMD": str(tmp_path / "missing"),
                   "MAILARCHIVER_CLAMDSCAN": str(tmp_path / "missing")}
    archive = tmp_path / "test.mailarchive"
    command = [sys.executable, str(ROOT / "scripts/desktop_entry.py"), "--cli", "--archive", str(archive),
               "ingest", str(source), "--owner-names-file", str(owners)]
    refused = subprocess.run(command + ["--clamav"], env=environment, capture_output=True, check=False, timeout=30)
    assert refused.returncode != 0
    with sqlite3.connect(archive / "archive.sqlite3") as catalog:
        assert catalog.execute("SELECT count(*) FROM messages").fetchone() == (0,)
    accepted = subprocess.run(command + ["--no-scan"], env=environment, capture_output=True, check=False, timeout=30)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert b"NOT scanned" in accepted.stderr
    assert read_ingest_history(archive).statuses[0].scan_policy == "not-scanned"
    with sqlite3.connect(archive / "archive.sqlite3") as catalog:
        assert catalog.execute("SELECT count(*) FROM metadata_defects WHERE field='antivirus' AND detail LIKE 'not-scanned:%'").fetchone() == (1,)
    assert source.read_bytes() == raw
    assert not verify_archive(archive)


@pytest.mark.skipif(sys.platform != "darwin", reason="Mach-O linkage requires Apple's toolchain")
def test_dependency_audit_rejects_external_rpath(tmp_path: Path) -> None:
    """Requirement: an @rpath dependency cannot hide a build-machine LC_RPATH."""
    import shutil

    if shutil.which("cc") is None:
        pytest.skip("C compiler is unavailable")
    # build_macos is also an executable script importing its adjacent module.
    scripts = str(ROOT / "scripts")
    sys.path.insert(0, scripts)
    try:
        from build_macos import verify_dependencies
    finally:
        sys.path.remove(scripts)
    app = tmp_path / "Fixture.app"
    binary = app / "Contents/MacOS/fixture"
    binary.parent.mkdir(parents=True)
    source = tmp_path / "fixture.c"
    source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    subprocess.run(["cc", str(source), "-Wl,-rpath,/opt/homebrew/lib", "-o", str(binary)], check=True)
    with pytest.raises(RuntimeError, match="nonportable binary search path"):
        verify_dependencies(app)
    subprocess.run(["cc", str(source), "-Wl,-rpath,@executable_path", "-o", str(binary)], check=True)
    verify_dependencies(app)
    # A dylib's install name is its own identity, not a dependency on another file.
    library_source = tmp_path / "library.c"
    library_source.write_text("int fixture_value(void) { return 0; }\n", encoding="utf-8")
    library = binary.parent / "library.dylib"
    subprocess.run(["cc", "-dynamiclib", str(library_source), "-Wl,-install_name,@rpath/identity-only.dylib", "-o", str(library)], check=True)
    verify_dependencies(app)
    # Once an executable actually loads that name, it must resolve within the app.
    source.write_text("extern int fixture_value(void); int main(void) { return fixture_value(); }\n", encoding="utf-8")
    subprocess.run(["cc", str(source), str(library), "-Wl,-rpath,@executable_path", "-o", str(binary)], check=True)
    with pytest.raises(RuntimeError, match="unresolved bundled dependency"):
        verify_dependencies(app)


@pytest.mark.parametrize("succeeds", [False, True])
def test_gui_remembers_source_only_after_success(tmp_path: Path, succeeds: bool) -> None:
    """Requirement: failed GUI imports retain picker state and do not publish a generation."""
    from threading import Event

    from mailarchiver.__main__ import IngestRequest
    from mailarchiver.application import ApplicationController, ApplicationPreferencesStore, IngestJob
    from mailarchiver.archive_config import import_directory, remember_import_directory
    from mailarchiver.gui_app import PyWebViewApplication
    from mailarchiver.writer_lock import WriterLease

    controller = ApplicationController(ApplicationPreferencesStore(tmp_path / "preferences.json"))
    document = controller.create_document(tmp_path / "archive.mailarchive")
    assert document.path is not None
    session = controller.new_search_window(document)
    previous = tmp_path / "previous"
    previous.mkdir()
    remember_import_directory(document.path, [previous])
    source = tmp_path / "new-source" / "message.eml"
    source.parent.mkdir()
    if succeeds:
        source.write_bytes(b"From: sender@example.net\nDate: Mon, 07 Sep 2026 12:00:00 +0000\nSubject: fixture\n\nbody\n")
    owners = tmp_path / "owners.txt"
    owners.write_text("fixture-owner\n", encoding="utf-8")
    lease = WriterLease.acquire(document.path, document.descriptor.identity, "fixture", "fixture", "test")
    controller.begin_ingest(document.descriptor.document_id, IngestJob(operation_id="fixture", owner_window_id=session.window_id), lease)
    host = PyWebViewApplication(controller)
    host._run_import(document, "fixture", lease, IngestRequest(archive=document.path, owner_names_file=owners, roots=[str(source)], scan_policy="not-scanned"), Event())
    assert import_directory(document.path) == (source.parent if succeeds else previous)
    assert document.generation == int(succeeds)
    assert not lease.acquired
