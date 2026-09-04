"""Fresh-process ingest, verification, and native-search acceptance test."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from playwright.sync_api import Page
from pydantic import BaseModel

from mailarchiver.catalog import address_pk, create_catalog, create_search
from mailarchiver.gui_app import (
    E2E_DRIVER,
    GUI_DIRECTORY,
    GuiApi,
    GuiE2EClientResult,
    IngestWindowApi,
    NativeSmokeApi,
    NativeSmokeController,
    NativeSmokeReport,
)
from mailarchiver.ingest_status import read_ingest_history
from mailarchiver.search import index_message
from e2e_tests.eicar_fixture import write_eicar_emlx


DATA = Path(__file__).parent / "data"
NORMAL_MESSAGE_COUNT = 207
PROCESSED_MESSAGE_COUNT = 210
GUI_API_METHODS = (
    "attachment", "choose_archive", "delete_filter_set", "mailbox_tree", "message",
    "copy_source_path", "copy_visible_text", "ingest_overview", "open_attachment", "open_ingest_window", "open_message_window", "part", "prepare_drag", "rename_filter_set",
    "request_previews", "save_attachment", "save_filter_set", "save_message",
    "saved_filter_sets", "search", "search_count", "status", "suggestions", "take_previews",
)
NATIVE_SMOKE_MESSAGE = (
    b"Message-ID: <native-smoke@example>\nFrom: sender@example.net\n"
    b"Subject: Native smoke\nDate: Wed, 03 Jan 2024 10:00:00 +0000\n\n"
    b"The message viewer bridge is ready.\n"
)
NATIVE_SMOKE_API_METHODS = ("status", "search", "native_smoke_complete")


class BuiltArchive(BaseModel):
    """Artifacts and captured output from one real ingest."""

    archive: Path
    source: Path
    infected_raw: bytes
    ingest_stdout: str
    ingest_stderr: str


@pytest.fixture(scope="module")
def built_archive(tmp_path_factory: pytest.TempPathFactory) -> BuiltArchive:
    """Ingest the committed corpus once, creating the antivirus sample only temporarily."""
    temporary = tmp_path_factory.mktemp("mailarchiver-e2e")
    source = temporary / "source"
    shutil.copytree(DATA / "source", source)
    owner_names = temporary / "owner-names.txt"
    owner_names.write_text("archive-owner@example.org\n", encoding="utf-8")
    infected_path = source / "Professional/Quarantine/runtime-infected.emlx"
    infected_raw = write_eicar_emlx(DATA / "infected.emlx.template", infected_path)
    archive = temporary / "archive"

    try:
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
                "--index-attachments",
                "--workers",
                "2",
                str(source),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        infected_path.unlink(missing_ok=True)

    assert not infected_path.exists()
    assert ingested.returncode == 0, ingested.stdout + ingested.stderr
    return BuiltArchive(
        archive=archive,
        source=source,
        infected_raw=infected_raw,
        ingest_stdout=ingested.stdout,
        ingest_stderr=ingested.stderr,
    )


@pytest.fixture(scope="module")
def native_smoke_archive(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the smallest derived archive that proves a real bridge search."""
    archive = tmp_path_factory.mktemp("native-smoke-archive")
    catalog = create_catalog(archive / "archive.sqlite3")
    search = create_search(archive / "search.sqlite3")
    try:
        sender = address_pk(catalog, "sender@example.net")
        catalog.execute(
            "INSERT INTO messages(message_id_normalized, sha256, sender_address_pk, subject, date_utc, date_source, category) "
            "VALUES ('native-smoke@example', ?, ?, 'Native smoke', '2024-01-03T10:00:00+00:00', 'date', 'Archive')",
            (hashlib.sha256(NATIVE_SMOKE_MESSAGE).hexdigest(), sender),
        )
        index_message(search, NATIVE_SMOKE_MESSAGE, False)
        catalog.commit()
        search.commit()
    finally:
        catalog.close()
        search.close()
    return archive


def test_fresh_ingest_builds_an_independently_verifiable_archive(built_archive: BuiltArchive) -> None:
    """Prove discovery, dedupe, quarantine, indexing, fixity, and standalone verification."""
    archive = built_archive.archive
    assert "completed:" in built_archive.ingest_stderr
    assert f"processed={PROCESSED_MESSAGE_COUNT}" in built_archive.ingest_stderr
    assert "infected=1" in built_archive.ingest_stderr
    assert "autosaved=1" in built_archive.ingest_stderr
    assert "seen_skipped=1" in built_archive.ingest_stderr
    assert "waiting for ClamAV startup:" in built_archive.ingest_stderr
    assert not any(path.name.endswith("runtime-infected.emlx") for path in built_archive.source.rglob("*"))
    assert (archive / "manifest-sha256.txt").is_file()
    assert (archive / "tagmanifest-sha256.txt").is_file()
    assert (archive / "mailbag.csv").is_file()
    history = read_ingest_history(archive)
    assert history.errors == []
    assert len(history.statuses) == 1
    ingest_status = history.statuses[0]
    assert ingest_status.state == "completed"
    assert ingest_status.processed_messages == PROCESSED_MESSAGE_COUNT
    assert ingest_status.counts.infected == 1
    assert ingest_status.counts.autosaves == 1
    assert "status/" not in (archive / "tagmanifest-sha256.txt").read_text(encoding="utf-8")
    assert "Mailarchiver-Message-Newline-Policy: preserve-source; add-final-LF-for-MBOX-framing\n" in (
        archive / "bag-info.txt"
    ).read_text(encoding="utf-8")

    catalog = sqlite3.connect(f"file:{archive / 'archive.sqlite3'}?mode=ro", uri=True)
    try:
        assert catalog.execute("SELECT count(*) FROM messages").fetchone() == (NORMAL_MESSAGE_COUNT + 1,)
        assert catalog.execute("SELECT count(*) FROM observations").fetchone() == (PROCESSED_MESSAGE_COUNT,)
        assert catalog.execute("SELECT count(*) FROM source_files").fetchone() == (7,)
        assert catalog.execute("SELECT count(*) FROM messages WHERE category = 'INFECTED'").fetchone() == (1,)
        assert catalog.execute(
            "SELECT count(*) FROM observations WHERE disposition = 'duplicate'"
        ).fetchone() == (1,)
        assert catalog.execute(
            "SELECT count(*) FROM observations WHERE disposition = 'autosave-excluded'"
        ).fetchone() == (1,)
        assert catalog.execute(
            "SELECT date_utc, date_source FROM messages WHERE message_id_normalized = 'rich-e2e@example'"
        ).fetchone() == ("2024-02-02T00:00:00+00:00", "received-median")
        infected_hash = catalog.execute(
            "SELECT sha256 FROM messages WHERE message_id_normalized = 'infected-e2e@example'"
        ).fetchone()
        assert infected_hash == (hashlib.sha256(built_archive.infected_raw).hexdigest(),)
        no_newline = (DATA / "source/Professional/Projects/no-final-newline.eml").read_bytes()
        assert not no_newline.endswith(b"\n")
        assert catalog.execute(
            "SELECT sha256 FROM messages WHERE message_id_normalized = 'no-final-newline-e2e@example'"
        ).fetchone() == (hashlib.sha256(no_newline).hexdigest(),)
    finally:
        catalog.close()

    search = sqlite3.connect(f"file:{archive / 'search.sqlite3'}?mode=ro", uri=True)
    try:
        assert search.execute("SELECT count(*) FROM message_fts").fetchone() == (NORMAL_MESSAGE_COUNT,)
        assert search.execute("SELECT count(*) FROM attachment_fts").fetchone() == (1,)
        assert search.execute("SELECT count(*) FROM attachment_fts WHERE attachment_fts MATCH 'Appendixquartz'").fetchone() == (1,)
    finally:
        search.close()

    verified = subprocess.run(
        [sys.executable, "-I", str(archive / "verify_mail_archive.py"), str(archive)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert f"OK 2024-Archive1.mbox: {NORMAL_MESSAGE_COUNT} messages\n" in verified.stdout
    assert "OK INFECTED1.mbox: 1 messages\n" in verified.stdout
    assert verified.stdout.endswith("Archive integrity verified.\n")


def test_search_ui_end_to_end_without_a_window(
    built_archive: BuiltArchive, tmp_path: Path, page: Page
) -> None:
    """Drive the shipped UI headlessly while every bridge call reaches the real service."""
    export_directory = tmp_path / "gui-e2e-exports"
    export_directory.mkdir()
    api = GuiApi(
        built_archive.archive, export_directory, export_directory,
        export_directory / "filter-sets.json",
    )
    try:
        for name in GUI_API_METHODS:
            page.expose_function(f"mailarchive_{name}", getattr(api, name))
        names = json.dumps(GUI_API_METHODS)
        page.add_init_script(
            f"const names = {names}; window.pywebview = {{api: {{}}}}; "
            "for (const name of names) window.pywebview.api[name] = "
            "(...args) => window[`mailarchive_${name}`](...args); "
            "window.addEventListener('DOMContentLoaded', () => "
            "window.dispatchEvent(new Event('pywebviewready')));",
        )
        page.goto((GUI_DIRECTORY / "index.html").as_uri())
        page.evaluate(E2E_DRIVER.read_text(encoding="utf-8"))
        page.wait_for_function("window.__mailarchiveE2E", timeout=90_000)
        result = GuiE2EClientResult.model_validate(page.evaluate("window.__mailarchiveE2E"))
    finally:
        api.close()

    assert result.passed, result.error
    assert len(result.checks) >= 30
    exports = {path.name for path in export_directory.iterdir()}
    assert {"saved-tiny.png", "saved-review.command", "filter-sets.json"} <= exports
    assert any(name.startswith("saved-Rich UI message-") and name.endswith(".eml") for name in exports)
    assert any(name.startswith("Rich UI message-") and name.endswith(".eml") for name in exports)


def test_ingest_history_ui_end_to_end(built_archive: BuiltArchive, page: Page) -> None:
    """Display persisted run history and every configured worker through the real service."""
    api = IngestWindowApi(built_archive.archive)
    page.expose_function("mailarchive_ingest_history", api.history)
    page.add_init_script(
        "window.pywebview = {api: {history: (...args) => "
        "window.mailarchive_ingest_history(...args)}}; "
        "window.addEventListener('DOMContentLoaded', () => "
        "window.dispatchEvent(new Event('pywebviewready')));"
    )

    page.goto((GUI_DIRECTORY / "ingests.html").as_uri())

    page.wait_for_selector(".history-row")
    assert page.locator(".history-row").count() == 1
    assert "Completed" in page.locator(".history-row").inner_text()
    assert page.locator(".worker-table tbody tr").count() == 2
    assert str(PROCESSED_MESSAGE_COUNT) in page.locator(".statistics").inner_text()


def test_native_smoke_page_reports_one_real_search(
    native_smoke_archive: Path, tmp_path: Path, page: Page
) -> None:
    """Requirement: the one-shot native page reports its real bridge search to Python."""
    report_path = tmp_path / "native-smoke.json"
    controller = NativeSmokeController(report_path)
    api = GuiApi(native_smoke_archive)
    bridge = NativeSmokeApi(api, controller)
    methods = {
        name for name in dir(bridge)
        if not name.startswith("_") and callable(getattr(bridge, name))
    }
    assert methods == set(NATIVE_SMOKE_API_METHODS)
    javascript_errors: list[str] = []
    page.on("pageerror", lambda error: javascript_errors.append(str(error)))
    try:
        for name in NATIVE_SMOKE_API_METHODS:
            page.expose_function(f"mailarchive_{name}", getattr(bridge, name))
        names = json.dumps(NATIVE_SMOKE_API_METHODS)
        page.add_init_script(
            f"const names = {names}; window.pywebview = {{api: {{}}}}; "
            "for (const name of names) window.pywebview.api[name] = "
            "(...args) => window[`mailarchive_${name}`](...args); "
            "window.addEventListener('DOMContentLoaded', () => "
            "window.dispatchEvent(new Event('pywebviewready')));"
        )
        page.goto((GUI_DIRECTORY / "index.html").as_uri() + "?native-smoke=1")
        page.wait_for_function("window.__mailarchiveNativeSmoke", timeout=5_000)
        assert controller.wait_for_result(0), "\n".join(javascript_errors) or "native smoke callback did not run"
    finally:
        api.close()

    report = NativeSmokeReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    assert report.passed
    assert [phase.name for phase in report.phases][-1] == "bridge-passed"


def test_native_smoke_report_keeps_the_primary_failure(tmp_path: Path) -> None:
    """Requirement: shutdown diagnostics must not replace the bridge failure."""
    report_path = tmp_path / "native-smoke.json"
    controller = NativeSmokeController(report_path)
    controller.complete(False, "bridge timeout")
    controller._abort("Cocoa shutdown timeout")  # pylint: disable=protected-access

    report = NativeSmokeReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    assert report.error == "bridge timeout"
    assert [phase.name for phase in report.phases][-1] == "watchdog-abort"


def terminate_process_group(process: subprocess.Popen[str]) -> tuple[str, str]:
    """Stop a timed-out smoke subprocess and collect its buffered output."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        return process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return process.communicate()


def sample_process(process: subprocess.Popen[str], destination: Path) -> None:
    """Capture a macOS stack sample before terminating an unbounded native process."""
    sampler = Path("/usr/bin/sample")
    if not sampler.is_file():
        return
    try:
        sampled = subprocess.run(
            [str(sampler), str(process.pid), "5", "-file", str(destination)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired as error:
        destination.write_text(f"process sampler timed out: {error}\n", encoding="utf-8")
        return
    if sampled.returncode and not destination.exists():
        destination.write_text(sampled.stdout + sampled.stderr, encoding="utf-8")


def run_native_smoke(native_smoke_archive: Path, tmp_path: Path, *, html_find: bool = False) -> None:
    """Run a bounded Cocoa/WKWebView smoke subprocess and retain its diagnostics."""
    configured_artifacts = os.environ.get("MAILARCHIVER_NATIVE_GUI_ARTIFACT_DIR")
    artifacts = Path(configured_artifacts) if configured_artifacts else tmp_path
    artifacts.mkdir(parents=True, exist_ok=True)
    name = "native-html-find-smoke" if html_find else "native-smoke"
    report_path = artifacts / f"{name}-report.json"
    sample_path = artifacts / f"{name}-sample.txt"
    report_path.unlink(missing_ok=True)
    sample_path.unlink(missing_ok=True)
    command = [
        sys.executable,
        "-m",
        "mailarchiver.gui_app",
        "--archive",
        str(native_smoke_archive),
        "--smoke-test",
    ]
    if html_find:
        command.append("--smoke-html-find")
    command.extend(("--smoke-report", str(report_path)))
    tested = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    timed_out = False
    try:
        stdout, stderr = tested.communicate(timeout=40)
    except subprocess.TimeoutExpired:
        timed_out = True
        sample_process(tested, sample_path)
        stdout, stderr = terminate_process_group(tested)

    report_exists = report_path.is_file()
    report_text = report_path.read_text(encoding="utf-8") if report_exists else "missing smoke report"
    detail = stdout + stderr + "\n" + report_text
    assert not timed_out, detail
    assert report_exists, detail
    assert tested.returncode == 0, detail
    assert stdout.endswith("GUI bridge smoke test passed\n")
    report = NativeSmokeReport.model_validate_json(report_text)
    assert report.completed and report.passed and report.error is None, detail
    assert {"window-loaded", "bridge-passed", "destroy-requested", "event-loop-returned"} <= {
        phase.name for phase in report.phases
    }


@pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("MAILARCHIVER_NATIVE_GUI_E2E") != "1",
    reason="set MAILARCHIVER_NATIVE_GUI_E2E=1 to exercise the native macOS WKWebView",
)
def test_native_search_ui_smoke(native_smoke_archive: Path, tmp_path: Path) -> None:
    """Exercise one bounded real search through a hidden pywebview bridge process."""
    run_native_smoke(native_smoke_archive, tmp_path)


@pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("MAILARCHIVER_NATIVE_HTML_FIND_E2E") != "1",
    reason="set MAILARCHIVER_NATIVE_HTML_FIND_E2E=1 for the visible macOS HTML-find check",
)
def test_native_html_find_smoke(native_smoke_archive: Path, tmp_path: Path) -> None:
    """Requirement: WKWebView paints the active HTML finder match after a header transition."""
    run_native_smoke(native_smoke_archive, tmp_path, html_find=True)
