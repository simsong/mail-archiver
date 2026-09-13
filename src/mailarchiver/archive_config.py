# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Human-editable, per-archive operational configuration.

Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
from typing import Literal

from pydantic import BaseModel, ConfigDict
from yaml import YAMLError, safe_dump, safe_load

from .owner_rules import OwnerRules

ARCHIVE_CONFIG_FILENAME = "config.yaml"


class ArchiveConfig(BaseModel):
    """Settings that describe how the application should open an archive."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)
    version: Literal[1, 2] = 2
    last_import_directory: Path | None = None
    owner: OwnerRules | None = None


def config_path(archive: Path) -> Path:
    return archive / ARCHIVE_CONFIG_FILENAME


def load_archive_config(archive: Path) -> ArchiveConfig:
    path = config_path(archive)
    try:
        with path.open(encoding="utf-8") as source:
            value = safe_load(source)
        return ArchiveConfig.model_validate(value or {})
    except FileNotFoundError:
        return ArchiveConfig()
    except (OSError, ValueError, TypeError, YAMLError) as error:
        raise ValueError(f"invalid archive configuration {path}: {error}") from error


def save_archive_config(archive: Path, config: ArchiveConfig) -> None:
    """Atomically write readable YAML beside the archive's operational state."""
    archive.mkdir(parents=True, exist_ok=True)
    config.version = 2
    path = config_path(archive)
    descriptor, temporary = tempfile.mkstemp(prefix=".config-", dir=archive)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            safe_dump(config.model_dump(mode="json"), output, sort_keys=False, allow_unicode=True)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def remember_import_directory(archive: Path, selected: list[Path]) -> Path | None:
    """Persist the directory represented by the last source selection."""
    if not selected:
        return None
    first = selected[0]
    candidate = first if first.is_dir() else first.parent
    config = load_archive_config(archive)
    if config.last_import_directory != candidate.absolute():
        config.last_import_directory = candidate.absolute()
        save_archive_config(archive, config)
    return config.last_import_directory


def import_directory(archive: Path) -> Path:
    """Return a usable picker start directory, falling back beside the archive."""
    config = load_archive_config(archive)
    candidate = config.last_import_directory
    if candidate is not None and candidate.is_dir():
        return candidate
    return archive.parent
