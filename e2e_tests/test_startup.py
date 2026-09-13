# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Requirements: all setup steps fit together; real native selections start import."""

import os
from pathlib import Path
import subprocess
import sys

from playwright.sync_api import Page, expect
import pytest

from mailarchiver.gui_app import GUI_DIRECTORY
from mailarchiver.loopback import LoopbackAssetServer


def test_setup_steps_fit_in_window(page: Page) -> None:
    """All numbered boxes and the disabled initial action remain visible without scrolling."""
    server = LoopbackAssetServer(GUI_DIRECTORY)
    try:
        page.goto(server.url("setup.html"))
        for width, height in ((760, 650), (560, 580)):
            page.set_viewport_size({"width": width, "height": height})
            expect(page.locator("section")).to_have_count(3)
            for section in page.locator("section").all():
                expect(section).to_be_in_viewport(ratio=1)
            expect(page.get_by_role("button", name="Start import")).to_be_disabled()
            expect(page.get_by_role("button", name="Cancel", exact=True)).to_be_in_viewport(ratio=1)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.set_viewport_size({"width": 760, "height": 650})
        output = GUI_DIRECTORY.parent / ".tmp" / "startup-setup.png"
        output.parent.mkdir(exist_ok=True)
        page.screenshot(path=str(output))
    finally:
        server.close()


@pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("MAILARCHIVER_NATIVE_SETUP_E2E") != "1",
    reason="make test-native-setup requires a logged-in Mac",
)
@pytest.mark.parametrize("action", ["import", "cancel", "cancel-race"])
def test_native_setup_import(tmp_path: Path, action: str) -> None:
    """The native folder browsers retain/reselect paths and Start import opens real progress."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "e2e_tests.native_setup_probe"],
            env=os.environ | {"MAILARCHIVER_SETUP_FIXTURE": str(tmp_path), "MAILARCHIVER_SETUP_ACTION": action},
            capture_output=True, text=True, timeout=120, check=False,
        )
    except subprocess.TimeoutExpired as error:
        pytest.fail(f"Native setup timed out: {(error.stderr or b'').decode(errors='replace')}")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Native setup import passed" in result.stdout


@pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("MAILARCHIVER_NATIVE_SETUP_E2E") != "1",
    reason="make test-native-setup requires a logged-in Mac",
)
def test_native_option_startup_and_reopen(tmp_path: Path) -> None:
    """Native modifier flags force setup and Dock reopen reuses the existing window."""
    result = subprocess.run(
        [sys.executable, "-m", "e2e_tests.native_option_probe"],
        env=os.environ | {"MAILARCHIVER_SETUP_FIXTURE": str(tmp_path)},
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Native Option startup and reopen passed" in result.stdout
