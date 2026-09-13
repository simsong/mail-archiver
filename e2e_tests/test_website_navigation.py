# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Exercise the actual website header and stylesheet at narrow viewport widths."""

from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from playwright.sync_api import Page, expect

ROOT = Path(__file__).parents[1]
WIDTH = "width"
HEIGHT = "height"
X = "x"


@pytest.mark.parametrize("width", [390, 600, 800, 1280])
def test_navigation_remains_visible_and_within_viewport(page: Page, width: int) -> None:
    """Requirement: adding or reordering navigation cannot hide mobile access to setup."""
    template = ROOT / "website/themes/envelope-rainbow/templates/base.html"
    header = BeautifulSoup(template.read_text(encoding="utf-8"), "html.parser").find("header")
    assert header is not None
    page.set_viewport_size({WIDTH: width, HEIGHT: 900})
    page.set_content(str(header))
    page.add_style_tag(content=(ROOT / "website/static/styles.css").read_text(encoding="utf-8"))
    if width <= 650:
        bounds = page.locator("header").bounding_box()
        assert bounds is not None
        assert bounds[X] == pytest.approx(15, abs=0.5)
        assert bounds[WIDTH] == pytest.approx(width - 30, abs=0.5)
    for _ in range(2):
        links = page.locator("nav a")
        expect(links).to_have_count(9)
        for link in links.all():
            expect(link).to_be_visible()
            bounds = link.bounding_box()
            assert bounds is not None
            assert 0 <= bounds[X] and bounds[X] + bounds[WIDTH] <= width
        expect(page.get_by_role("link", name="Gmail", exact=True)).to_be_visible()
        # Exercise the regression after a real DOM reordering, not an index-string check.
        page.locator("nav").evaluate("(nav) => nav.prepend(nav.lastElementChild)")


def test_documentation_code_scrolls_within_mobile_page(page: Page) -> None:
    """Requirement: long configuration examples must not widen the mobile page."""
    page.set_viewport_size({WIDTH: 390, HEIGHT: 900})
    page.set_content(
        '<main class="page shell"><div class="prose"><pre><code>'
        'credential_ref: keyring://mail-archiver/personal-imap-account'
        '</code></pre></div></main>'
    )
    page.add_style_tag(content=(ROOT / "website/static/styles.css").read_text(encoding="utf-8"))
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    assert page.locator("pre").evaluate("(block) => block.scrollWidth > block.clientWidth")


@pytest.mark.parametrize("width", [390, 655, 1001, 1312, 1920])
def test_cover_text_remains_accessible_without_artwork(page: Page, width: int) -> None:
    """Requirement: banner copy is real text and reflows even when artwork cannot load.

    655px represents the effective viewport of a 1310px window at 200% zoom.
    """
    template = ROOT / "website/themes/envelope-rainbow/templates/index.html"
    cover = BeautifulSoup(template.read_text(encoding="utf-8"), "html.parser").find(
        "section", class_="hero-wrap"
    )
    assert cover is not None
    artwork = cover.find("img")
    assert artwork is not None
    artwork["src"] = "data:,"
    page.set_viewport_size({WIDTH: width, HEIGHT: 900})
    page.set_content(str(cover))
    page.add_style_tag(content=(ROOT / "website/static/styles.css").read_text(encoding="utf-8"))
    expect(page.get_by_role("heading", level=1, name="Mail Collection Toolkit")).to_be_visible()
    expect(page.get_by_text("Collect. Preserve. Search. Understand.", exact=True)).to_be_visible()
    expect(page.get_by_text(
        "A modern, open source toolkit for making email a lasting part of the historical record.",
        exact=True,
    )).to_be_visible()
    expect(page.locator(".cover-tagline")).to_have_text("Email has a history. Keep it")
    tagline = page.locator(".cover-tagline")
    if width > 1000:
        # Whitespace between block spans must not introduce anonymous blank lines.
        height = tagline.evaluate("element => element.getBoundingClientRect().height")
        line_height = tagline.evaluate("element => parseFloat(getComputedStyle(element).lineHeight)")
        assert height == pytest.approx(3 * line_height, abs=1)
    else:
        # Literal spaces must remain copyable text when the spans flow inline.
        assert tagline.inner_text() == "Email has a history. Keep it"
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    assert page.locator(".cover-description").evaluate(
        "element => parseFloat(getComputedStyle(element).fontSize) >= 16"
    )
    selected_text = page.locator(".cover-banner").evaluate("""element => {
        const range = document.createRange();
        range.selectNodeContents(element);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        return selection.toString();
    }""")
    assert "Mail Collection Toolkit" in selected_text
    assert "has a history." in selected_text
    assert "Keep it" in selected_text
