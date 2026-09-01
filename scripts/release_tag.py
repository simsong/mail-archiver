#!/usr/bin/env python3
"""Validate a Mail Archiver release tag against pyproject metadata."""

from __future__ import annotations

import argparse
import re
import subprocess
import tomllib
from pathlib import Path


def expected_tag(version: str) -> str:
    beta = re.fullmatch(r"(\d+\.\d+\.\d+)b(\d+)", version)
    return f"v{beta.group(1)}-beta{beta.group(2)}" if beta else f"v{version}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--require-annotated", action="store_true")
    args = parser.parse_args()
    metadata = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    version = metadata["project"]["version"]
    expected = expected_tag(version)
    if args.tag != expected:
        parser.error(f"{args.tag} does not match pyproject version {version}; expected {expected}")
    if args.require_annotated:
        tag_type = subprocess.run(
            ["git", "cat-file", "-t", f"refs/tags/{args.tag}"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        if tag_type != "tag":
            parser.error(f"{args.tag} must be an annotated tag")
    print(f"version={version} tag={args.tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
