#!/usr/bin/env python3
# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Check the source-controlled Zola site and its application icon assets."""

from __future__ import annotations

import argparse
import struct
import tomllib
from pathlib import Path

import yaml

SIZES = (48, 64, 128, 192)
GMAIL_AUTH_IMAGES = tuple(f"{number:02d}-{name}.png" for number, name in enumerate((
    "get-started", "app-information", "external-audience", "contact-information",
    "user-data-policy", "create-configuration", "configuration-created", "add-test-user",
    "branding-requirements",
), start=1))
REQUIRED_TEXT = (
    "doc/RELEASE_NOTES.md",
    "README.md",
    "/releases",
    "/discussions/55",
    "/discussions/56",
    "gmail-authorization/",
    "importing/",
    "searching/",
    "https://www.epaddproject.org/",
    "https://www.rfc-editor.org/rfc/rfc8493",
    "https://creativecommons.org/licenses/by-sa/4.0/",
    "advanced/",
    "h3 semantic-message v1",
    "Apple Mail as a temporary provider adapter",
    "Import/Refresh",
    "Import/Rebuild",
    "credential_ref",
    "your.name@gmail.com",
    "For Individuals",
    "For Archivists",
    "BagIt 1.0",
    "Mailbag 1.0",
    "ePADD",
    "Privacy policy",
    "Copyright Simson Garfinkel",
    "GNU General Public License",
    "The non-GPL versions may be available",
    "Identify email files",
    "Search and report",
    "Verify and share",
    "Website changelog",
    "@/changelog.md",
    "https://github.com/simsong",
    "(C) 2026 Simson L. Garfinkel, authored with Codex.",
)

FORBIDDEN_TEXT = (
    "Digital email curation begins before the first message is copied",
    "Current boundary",
    "Built for personal memory and archival stewardship",
    "Search the decades you already saved",
    "Preserve the messages. Understand the collection",
    "Email Collection Toolkit exports a BagIt",
    "Export a BagIt",
    "The export uses MBOX, BagIt",
)


def png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as stream:
        header = stream.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ValueError(f"{path} is not a PNG")
    return struct.unpack(">II", header[16:24])


def validate_png(path: Path, expected_size: int) -> None:
    """Report missing or malformed icon assets without a traceback."""
    if not path.is_file():
        raise SystemExit(f"missing PNG icon: {path}")
    try:
        dimensions = png_size(path)
    except (OSError, ValueError, struct.error) as error:
        raise SystemExit(f"invalid PNG icon {path}: {error}") from None
    if dimensions != (expected_size, expected_size):
        raise SystemExit(f"{path} is not {expected_size}x{expected_size}")


def validate_config(path: Path) -> None:
    """Reject malformed Zola configuration before the site build."""
    try:
        tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise SystemExit(f"invalid Zola configuration {path}: {error}") from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).parents[1])
    root = parser.parse_args().root
    validate_config(root / "website/config.toml")
    required = [
        root / "website/content/_index.md",
        root / "website/content/use-cases.md",
        root / "website/content/about.md", root / "website/content/changelog.md",
        root / "website/content/privacy.md", root / "website/content/rights.md",
        root / "website/themes/envelope-rainbow/theme.toml",
        root / "website/themes/envelope-rainbow/templates/base.html",
        root / "website/themes/envelope-rainbow/templates/index.html",
        root / "website/themes/envelope-rainbow/templates/page.html",
        root / "website/themes/envelope-rainbow/templates/section.html",
        root / "website/content/gmail-authorization.md",
        root / "website/content/importing.md",
        root / "website/content/searching.md",
        root / "website/static/images/search-interface.png",
        root / "website/static/images/importing-interface.png",
        root / "website/static/images/hands-typing.svg",
        root / "website/static/images/ATTRIBUTION.md",
        root / "website/content/advanced.md",
        root / "website/content/oauth-client-registration.md",
        root / "website/static/icons/rainbow-post.svg", root / "gui/icons/rainbow-post.svg",
    ]
    auth_image_directory = root / "website/static/images/gmail-authorization"
    required.extend(auth_image_directory / name for name in GMAIL_AUTH_IMAGES)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit("missing website files: " + ", ".join(missing))
    for path in (
        root / ".github/workflows/continuous-integration.yml",
        root / ".github/workflows/pages.yml",
        root / ".github/workflows/release.yml",
    ):
        try:
            yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as error:
            raise SystemExit(f"invalid workflow YAML in {path}: {error}") from error
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "website").rglob("*")
        if path.is_file() and path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif"}
    )
    for required_text in REQUIRED_TEXT:
        if required_text not in text:
            raise SystemExit(f"website is missing required link text: {required_text}")
    for path in (root / "doc/USER_MANUAL.md", root / "website/content/gmail-authorization.md"):
        if "simsong@gmail.com" in path.read_text(encoding="utf-8"):
            raise SystemExit(f"end-user help contains the maintainer example address: {path}")
    for forbidden_text in FORBIDDEN_TEXT:
        if forbidden_text in text:
            raise SystemExit(f"website contains retired promotional text: {forbidden_text}")
    app_svg = (root / "gui/icons/rainbow-post.svg").read_bytes()
    site_svg = (root / "website/static/icons/rainbow-post.svg").read_bytes()
    if app_svg != site_svg:
        raise SystemExit("application and website SVG icons differ")
    for size in SIZES:
        for directory in (root / "gui/icons", root / "website/static/icons"):
            path = directory / f"rainbow-post-{size}.png"
            validate_png(path, size)
    for name in GMAIL_AUTH_IMAGES:
        path = auth_image_directory / name
        width, height = png_size(path)
        if width != 1800 or height < 1200:
            raise SystemExit(f"unexpected Gmail authorization illustration size: {path}")
    print("website assets and required links are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
