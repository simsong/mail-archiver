#!/usr/bin/env python3
# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Check project-owned text files for the repository copyright notice."""

from __future__ import annotations

import subprocess
from pathlib import Path


NOTICE = "Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved."
EXCLUDED_FILES = frozenset({
    "owner-names.txt", "uv.lock", "website/data/releases.toml",
    "website/static/images/hands-typing.svg",  # Upstream CC-BY-SA artwork; retain original bytes.
    "CLAUDE.md", ".github/copilot-instructions.md",  # Generated shared workflow wrappers.
})
EXCLUDED_PREFIXES = (
    ".agents/skills/",
    ".claude/skills/",
    "doc/images/",
    "e2e_tests/data/",
    "gui/vendor/",
    "tests/data/",
    "website/themes/envelope-rainbow/",
)
ELIGIBLE_SUFFIXES = frozenset(
    {".applescript", ".css", ".html", ".js", ".md", ".mjs", ".py", ".pyi", ".sql", ".svg", ".swift", ".toml", ".yaml", ".yml"}
)
ELIGIBLE_NAMES = frozenset({".gitignore", "Makefile", "COPYRIGHT"})


def repository_files(root: Path) -> tuple[Path, ...]:
    """Return tracked and unignored untracked repository files."""
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return tuple(Path(item.decode()) for item in result.stdout.split(b"\0") if item)


def is_eligible(path: Path) -> bool:
    """Return whether a tracked path is project-owned, comment-safe text."""
    name = path.as_posix()
    return (
        name not in EXCLUDED_FILES
        and not name.startswith(EXCLUDED_PREFIXES)
        and (path.name in ELIGIBLE_NAMES or path.suffix.lower() in ELIGIBLE_SUFFIXES)
    )


def expected_header(path: Path) -> str:
    """Return the required native text form for an eligible path."""
    if path.name == "COPYRIGHT":
        return NOTICE
    if path.name in {".gitignore", "Makefile"} or path.suffix.lower() in {".py", ".pyi", ".toml", ".yaml", ".yml"}:
        return f"# {NOTICE}"
    if path.suffix.lower() == ".swift":
        return f"// {NOTICE}"
    if path.suffix.lower() in {".css", ".js", ".mjs"}:
        return f"/* {NOTICE} */"
    if path.suffix.lower() in {".html", ".md", ".svg"}:
        return f"<!-- {NOTICE} -->"
    return f"-- {NOTICE}"


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    files = repository_files(root)
    missing = [
        path
        for path in files
        if is_eligible(path)
        and expected_header(path) not in (root / path).read_text(encoding="utf-8")[:4096]
    ]
    if missing:
        print("WARNING: Missing the project copyright notice:")
        for path in missing:
            print(path)
        return 0
    print(f"Copyright notice present in {sum(is_eligible(path) for path in files)} eligible files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
