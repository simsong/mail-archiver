"""Centralize canonical Mailbag payload and integrity-tag path construction."""

from __future__ import annotations

from pathlib import Path


DATA_DIRECTORY = "data"
MBOX_DIRECTORY = "mbox"
INTEGRITY_DIRECTORY = "integrity"


def mbox_directory(archive: Path) -> Path:
    """Return the canonical Mailbag MBOX payload directory."""
    return archive / DATA_DIRECTORY / MBOX_DIRECTORY


def integrity_directory(archive: Path) -> Path:
    """Return the BagIt tag directory holding message-level integrity files."""
    return archive / INTEGRITY_DIRECTORY


def mbox_path(archive: Path, filename: str) -> Path:
    """Return one canonical MBOX path from its catalogued basename."""
    return mbox_directory(archive) / filename


def integrity_path(archive: Path, filename: str) -> Path:
    """Return the integrity tag path for one catalogued MBOX basename."""
    return integrity_directory(archive) / f"{filename}.integrity"
