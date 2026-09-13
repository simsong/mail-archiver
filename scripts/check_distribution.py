#!/usr/bin/env python3
# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Build and install both distribution formats, then smoke their entry points."""

from __future__ import annotations

import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path


REQUIRED_PACKAGE_MEMBERS = (
    "mailarchiver/apple_summary.swift",
    "mailarchiver/configuration.yaml",
    "mailarchiver/contact_filters.yaml",
    "mailarchiver/local_source_rules.yaml",
    "mailarchiver/message_patterns.yaml",
    "mailarchiver/plugins/files/mbox/plugin.toml",
    "mailarchiver/plugins/sources/file-folder/plugin.toml",
    "mailarchiver/sql/V1__archive.sql",
    "mailarchiver/sql/V1__search.sql",
)
SMOKE_COMMANDS = (
    ("mailarchiver-auth", ("--help",), 0),
    ("mailarchiver-compare-apple-mail", ("--help",), 0),
    ("mailarchiver-h3-review", ("--help",), 0),
    ("mailarchiver", ("--help",), 0),
    ("mailsearch", ("--help",), 0),
    ("mailsearch-gui", ("--help",), 0),
    ("summarize", (), 2),
    ("verify-mail-archive", ("--help",), 0),
    ("mailarchiver-validation", ("--help",), 0),
    ("extract-pdf-mail", ("--help",), 0),
)


def archive_members(path: Path) -> set[str]:
    """Return normalized names from a wheel or gzipped source distribution."""
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return set(archive.namelist())
    with tarfile.open(path, "r:gz") as archive:
        return set(archive.getnames())


def require_package_members(path: Path) -> None:
    """Require the runtime resources already declared as package data."""
    members = archive_members(path)
    missing = [
        member
        for member in REQUIRED_PACKAGE_MEMBERS
        if not any(name.endswith(member) for name in members)
    ]
    if missing:
        raise RuntimeError(f"{path.name} is missing package data: {', '.join(missing)}")


def install_and_smoke(uv: str, artifact: Path, root: Path) -> None:
    """Install one artifact in isolation and invoke every console entry point."""
    environment = root / artifact.name.replace(".", "-")
    subprocess.run([uv, "venv", "--python", sys.executable, str(environment)], check=True)
    python = environment / "bin" / "python"
    subprocess.run([uv, "pip", "install", "--python", str(python), str(artifact)], check=True)
    for command, arguments, expected in SMOKE_COMMANDS:
        result = subprocess.run(
            [str(environment / "bin" / command), *arguments],
            check=False,
            capture_output=True,
            input="",
            text=True,
            cwd=root,
            timeout=30,
        )
        if result.returncode != expected:
            raise RuntimeError(
                f"{artifact.name}: {command} returned {result.returncode}, expected {expected}: "
                f"{result.stdout}{result.stderr}"
            )


def main() -> int:
    project = Path(__file__).parents[1]
    with tempfile.TemporaryDirectory(prefix="mailarchiver-distribution-") as temporary:
        root = Path(temporary)
        distribution = root / "dist"
        subprocess.run(
            ["uv", "build", "--sdist", "--wheel", "--out-dir", str(distribution)],
            cwd=project,
            check=True,
        )
        artifacts = sorted((*distribution.glob("*.tar.gz"), *distribution.glob("*.whl")))
        if len(artifacts) != 2:
            raise RuntimeError(f"expected one sdist and one wheel, found {len(artifacts)} artifacts")
        for artifact in artifacts:
            require_package_members(artifact)
            install_and_smoke("uv", artifact, root)
    print("sdist and wheel install with packaged resources and working entry points")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
