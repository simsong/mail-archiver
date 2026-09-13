# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Render clearly labeled setup diagrams without third-party account screenshots."""

from html import escape
from pathlib import Path

from playwright.sync_api import sync_playwright
from pydantic import BaseModel

from scripts.check_website import GMAIL_AUTH_IMAGES

WIDTH = "width"
HEIGHT = "height"
OUTPUT = Path(__file__).parents[1] / "website/static/images/gmail-authorization"


class SetupStep(BaseModel):
    title: str
    instruction: str
    fields: list[str]


STEPS = (
    SetupStep(title="Start branding", instruction="Open Google Auth Platform → Branding and choose Get started.", fields=["Project: Email Collection Toolkit personal Gmail", "Use the Google account that owns the project."]),
    SetupStep(title="App information", instruction="Identify the application and its support contact, then select Next.", fields=["App name: Email Collection Toolkit personal", "User support email: your.name@gmail.com"]),
    SetupStep(title="Audience", instruction="For a personal Gmail account, select External, then Next.", fields=["Audience: External", "Internal is for eligible organization-controlled Workspace projects."]),
    SetupStep(title="Contact information", instruction="Enter the maintainer's own Google address, then select Next.", fields=["Developer contact: your.name@gmail.com", "Use an address you control."]),
    SetupStep(title="User-data policy", instruction="Read Google's user-data policy. Agree only after reviewing it.", fields=["Review Google API Services User Data Policy", "If you agree: select the checkbox, then Continue."]),
    SetupStep(title="Create configuration", instruction="Review all four completed sections, then choose Create.", fields=["App information · Audience · Contact information · Finish", "App: Email Collection Toolkit personal"]),
    SetupStep(title="Configuration created", instruction="Return to OAuth Overview after the configuration is created.", fields=["Next: Audience → Test users", "Then configure Data Access and a Desktop app client."]),
    SetupStep(title="Add test users", instruction="Open Audience → Test users → Add users.", fields=["Add the actual Google addresses of your testers.", "Example: your.name@gmail.com"]),
    SetupStep(title="Production branding", instruction="Review Branding before requesting production publication.", fields=["Application home page and privacy-policy link", "Authorized domains you control", "Developer contact address and any requested verification"]),
)


def main() -> None:
    """Render authored reference diagrams, explicitly distinguished from captures."""
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={WIDTH: 1800, HEIGHT: 1200}, device_scale_factor=1)
            for number, (filename, step) in enumerate(zip(GMAIL_AUTH_IMAGES, STEPS, strict=True), start=1):
                fields = "".join(f"<li>{escape(field)}</li>" for field in step.fields)
                page.set_content(f'''<!doctype html><html lang="en"><meta charset="utf-8">
<style>body {{margin:0;background:#edf4fc;color:#153663;font:32px/1.5 system-ui,sans-serif}} main {{margin:90px;padding:72px;background:white;border-radius:36px}} header {{font-size:24px;color:#526d8e;letter-spacing:.08em}} h1 {{font-size:62px;line-height:1.1;margin:36px 0}} p {{max-width:1350px}} li {{margin:24px 0;padding:24px;background:#f0f6fc;border-radius:14px}} ul {{list-style:none;padding:0}} footer {{margin-top:60px;color:#526d8e;font-size:24px}}</style>
<main><header>EMAIL COLLECTION TOOLKIT · GMAIL SETUP</header><h1>{number:02d} / {escape(step.title)}</h1><p>{escape(step.instruction)}</p><ul>{fields}</ul><footer>Schematic setup illustration — not a Google Console screenshot.<br>Labels and requirements may change. Use your own account details.</footer></main></html>''')
                page.screenshot(path=str(OUTPUT / filename))
        finally:
            browser.close()


if __name__ == "__main__":
    main()
