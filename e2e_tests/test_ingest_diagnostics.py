# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Requirement: local failure evidence is readable in the real Ingests interface."""

from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from mailarchiver.__main__ import IngestRequest, run_ingest
from mailarchiver.gui_app import GUI_DIRECTORY, IngestWindowApi
from scripts.website_screenshots import bridge


def test_long_failure_detail_wraps_without_hiding_source_or_traceback(tmp_path: Path, page: Page) -> None:
    """A real failed import displays escaped mail as text and keeps diagnostics inside the pane."""
    source = tmp_path / "undated.eml"
    source.write_bytes(b"Message-ID: <undated@example.test>\n\n<script>alert(1)</script>" + b"x" * 5000)
    owners = tmp_path / "owners.txt"
    owners.write_text("owner@example.test\n", encoding="utf-8")
    archive = tmp_path / "archive"
    with pytest.raises(RuntimeError, match="failed to parse"):
        run_ingest(IngestRequest(
            archive=archive, roots=[str(source)], owner_names_file=owners, scan_policy="not-scanned",
        ), terminal=False)
    page.set_viewport_size({"width": 1000, "height": 900})
    bridge(page, IngestWindowApi(archive), ("history", "antivirus", "can_import_directory"))
    page.goto((GUI_DIRECTORY / "ingests.html").as_uri())
    failure = page.locator("div.failure")
    expect(failure).to_contain_text("Traceback (most recent call last)")
    expect(failure).to_contain_text("Message SHA-256:")
    expect(failure).to_contain_text(str(source))
    expect(failure).to_contain_text("<script>alert(1)</script>")
    assert failure.locator("script").count() == 0
    assert page.locator(".ingest-detail").evaluate("el => el.scrollWidth <= el.clientWidth + 1")
    failure.scroll_into_view_if_needed()
    page.screenshot(path=str(tmp_path / "import-failure.png"))
