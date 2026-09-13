# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Regenerate the application and website PNG icons from their shared SVG."""

from pathlib import Path

from playwright.sync_api import sync_playwright

from scripts.check_website import SIZES

ROOT = Path(__file__).parents[1]


def main() -> None:
    """Render the same SVG at each required native pixel size."""
    source = (ROOT / "gui/icons/rainbow-post.svg").read_text(encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            for size in SIZES:
                page.set_content(
                    f'<style>body {{ margin: 0; }} svg {{ width: {size}px; '
                    f'height: {size}px; display: block; }}</style>{source}'
                )
                png = page.locator("svg").screenshot(omit_background=True)
                for directory in ("gui/icons", "website/static/icons"):
                    (ROOT / directory / f"rainbow-post-{size}.png").write_bytes(png)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
