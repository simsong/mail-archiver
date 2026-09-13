# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Public application identity and read-only discovery of existing settings."""

from pathlib import Path

APPLICATION_NAME = "Email Collection Toolkit"
# Preserve access to settings created under the former two-word product name.
LEGACY_DIRECTORY_NAME = " ".join(("Mail", "Archiver"))


def application_data_directory(root: Path) -> Path:
    """Use the current directory, falling back to existing pre-rename settings."""
    current = root / APPLICATION_NAME
    legacy = root / LEGACY_DIRECTORY_NAME
    has_settings = (current / "preferences.json").is_file() or (current / "auth").is_dir()
    if legacy.is_dir() and not has_settings:
        return legacy
    return current
