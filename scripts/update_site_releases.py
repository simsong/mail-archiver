#!/usr/bin/env python3
"""Write Zola release data from newline-delimited Git tag names."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


STABLE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
BETA = re.compile(r"^v(\d+)\.(\d+)\.(\d+)-beta(\d+)$")
REPOSITORY = "https://github.com/simsong/mail-archiver"


def choose(tags: list[str], pattern: re.Pattern[str]) -> str | None:
    candidates = [(tuple(int(part) for part in match.groups()), tag)
                  for tag in tags if (match := pattern.fullmatch(tag))]
    return max(candidates)[1] if candidates else None


def release_block(tag: str | None, label: str) -> str:
    if tag is None:
        return f'version = "No {label} release yet"\ntag = ""\nurl = "{REPOSITORY}/releases"'
    return f'version = "{tag}"\ntag = "{tag}"\nurl = "{REPOSITORY}/releases/tag/{tag}"'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    tags = [line.strip() for line in sys.stdin if line.strip()]
    stable = choose(tags, STABLE)
    beta = choose(tags, BETA)
    current = stable or beta
    current_version = current or "Unreleased"
    current_url = f"{REPOSITORY}/releases/tag/{current}" if current else f"{REPOSITORY}/releases"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        f'current_version = "{current_version}"\ncurrent_url = "{current_url}"\n\n'
        f"[stable]\n{release_block(stable, 'stable')}\n\n[beta]\n{release_block(beta, 'beta')}\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
