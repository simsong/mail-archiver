# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Capture the shipped search and import interfaces with synthetic email only."""

from __future__ import annotations

import json
from email.message import EmailMessage
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import Page, expect, sync_playwright

from mailarchiver.__main__ import IngestRequest, run_ingest
from mailarchiver.gui_app import GuiApi, IngestWindowApi, OwnerRulesPrompt
from mailarchiver.owner_rules import OwnerRules

ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "website/static/images"
WIDTH = "width"
HEIGHT = "height"
SEARCH_METHODS = (
    "activate", "status", "search", "search_count", "suggestions", "mailbox_tree", "message", "part",
    "request_previews", "take_previews", "ingest_overview", "saved_filter_sets",
)


def bridge(page: Page, api: object, methods: tuple[str, ...]) -> None:
    """Connect the real Python service to the shipped HTML, as in Chromium E2E."""
    for name in methods:
        page.expose_function(f"toolkit_{name}", getattr(api, name))
    page.add_init_script(
        f"const names = {json.dumps(methods)}; window.pywebview = {{api: {{}}}}; "
        "for (const name of names) window.pywebview.api[name] = "
        "(...args) => window[`toolkit_${name}`](...args); "
        "window.addEventListener('DOMContentLoaded', () => "
        "window.dispatchEvent(new Event('pywebviewready')));"
    )


def build_collection(work: Path) -> Path:
    """Ingest authored fixture messages through the actual archive service."""
    source = work / "Riverbank Collection"
    source.mkdir()
    subjects = (
        "Riverbank exhibition: opening weekend", "Photographs for the exhibition",
        "Oral history interviews", "Exhibition catalogue draft", "Volunteer afternoon",
        "Riverbank walking tour", "Exhibition loans confirmed", "Community archive notes",
    )
    for number, subject in enumerate(subjects, start=1):
        message = EmailMessage()
        message["From"] = "Alex Morgan <alex@example.org>"
        message["To"] = "Sam Rivera <sam@example.org>"
        message["Subject"] = subject
        message["Date"] = f"{number:02d} May 2024 10:00:00 +0000"
        message["Message-ID"] = f"<riverbank-{number}@example.org>"
        message.set_content(
            f"Hello Sam,\n\n{subject}\n\n"
            "The Riverbank exhibition brings together photographs, letters, and memories "
            "from our community archive. The collection tells the story of the old "
            "riverside library and the people who cared for it.\n\n"
            "Please bring your notes to our planning meeting on Thursday. We will review "
            "the exhibition labels and select photographs for the catalogue.\n\n"
            "Thank you,\nAlex\n\nSynthetic demonstration message; no private email.\n"
        )
        (source / f"message-{number:02d}.eml").write_bytes(message.as_bytes())
    owner = work / "owner-names.txt"
    owner.write_text("sam@example.org\n", encoding="utf-8")
    archive = work / "Riverbank.mailarchive"
    run_ingest(IngestRequest(archive=archive, owner_names_file=owner, roots=[str(source)], workers=1))
    return archive


def main() -> None:
    """Write screenshots only after the real interfaces have rendered their data."""
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="toolkit-website-") as temporary:
        work = Path(temporary)
        archive = build_collection(work)
        api = GuiApi(archive, work, preferences_file=work / "filter-sets.json")
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    page = browser.new_page(viewport={WIDTH: 1440, HEIGHT: 960}, device_scale_factor=1)
                    bridge(page, api, SEARCH_METHODS)
                    page.goto((ROOT / "gui/index.html").as_uri())
                    page.locator("#search").fill("exhibition")
                    page.locator("#search-form").evaluate("form => form.requestSubmit()")
                    expect(page.locator("#result-list .result")).to_have_count(8)
                    page.locator("#result-list .result").first.click()
                    page.locator("#message-content").wait_for(state="visible")
                    for preview in page.get_by_label("Message preview", exact=True).all():
                        expect(preview).to_contain_text("Hello Sam")
                    assert page.locator("#error").is_hidden(), page.locator("#error").inner_text()
                    page.screenshot(path=str(OUTPUT / "search-interface.png"))
                    page.close()
                    page = browser.new_page(viewport={WIDTH: 1440, HEIGHT: 960}, device_scale_factor=1)
                    bridge(page, IngestWindowApi(archive), ("history", "antivirus", "can_import_directory"))
                    page.goto((ROOT / "gui/ingests.html").as_uri())
                    page.locator(".state-badge").filter(has_text="completed").wait_for()
                    page.screenshot(path=str(OUTPUT / "importing-interface.png"))
                    page.close()
                    page = browser.new_page(viewport={WIDTH: 760, HEIGHT: 740}, device_scale_factor=1)
                    bridge(page, OwnerRulesPrompt(OwnerRules(include=["sam", "sam.rivera@example.org"], exclude=["sam@shared.example.org"])), ("status", "update", "cancel"))
                    page.goto((ROOT / "gui/options.html").as_uri() + "?import=1")
                    expect(page.locator("#owner-include")).to_have_value("sam\nsam.rivera@example.org")
                    expect(page.locator("#owner-exclude")).to_have_value("sam@shared.example.org")
                    expect(page.locator("#save")).to_be_enabled()
                    page.screenshot(path=str(OUTPUT / "owner-rules-interface.png"))
                finally:
                    browser.close()
        finally:
            api.close()


if __name__ == "__main__":
    main()
