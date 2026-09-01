#!/usr/bin/env python3
"""Check the source-controlled Zola site and its application icon assets."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

import yaml


SIZES = (48, 64, 128, 192)
REQUIRED_TEXT = ("doc/RELEASE_NOTES.md", "README.md", "/releases", "/discussions/55", "/discussions/56")


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).parents[1])
    root = parser.parse_args().root
    required = [
        root / "website/config.toml", root / "website/content/_index.md",
        root / "website/themes/envelope-rainbow/theme.toml",
        root / "website/themes/envelope-rainbow/templates/base.html",
        root / "website/themes/envelope-rainbow/templates/index.html",
        root / "website/themes/envelope-rainbow/templates/page.html",
        root / "website/static/icons/rainbow-post.svg", root / "gui/icons/rainbow-post.svg",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit("missing website files: " + ", ".join(missing))
    for path in (root / ".github/workflows/pages.yml", root / ".github/workflows/release.yml"):
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
    app_svg = (root / "gui/icons/rainbow-post.svg").read_bytes()
    site_svg = (root / "website/static/icons/rainbow-post.svg").read_bytes()
    if app_svg != site_svg:
        raise SystemExit("application and website SVG icons differ")
    for size in SIZES:
        for directory in (root / "gui/icons", root / "website/static/icons"):
            path = directory / f"rainbow-post-{size}.png"
            validate_png(path, size)
    print("website assets and required links are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
