# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Archive owner include/exclude settings and derived detected addresses."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile

from pydantic import BaseModel, Field
from yaml import safe_dump, safe_load

from .archive_config import load_archive_config, save_archive_config
from .owner_rules import OwnerRules, normalize_rules
from .writer_lock import WriterLease

OWNER_NAMES_FILENAME = "owner-names.txt"  # Legacy input, never rewritten.
OWNER_RULES_USED = "owner-rules-used.yaml"
OWNER_NAMES_DETECTED = "owner-names-detected.txt"


def read_owner_names(path: Path) -> list[str]:
    try:
        return normalize_rules(path.read_text(encoding="utf-8").splitlines())
    except FileNotFoundError:
        return []


def source_owner_names(roots: list[Path]) -> list[str]:
    return normalize_rules([
        name for root in roots if root.is_dir()
        for name in read_owner_names(root / OWNER_NAMES_FILENAME)
    ])


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".owner-rules-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(text)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class OwnerRulesState(BaseModel):
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    revision: str
    changed_since_import: bool
    import_rules_known: bool
    editable: bool = True


class DocumentOptions:
    """Authoritative owner policy in config.yaml, always protected by WriterLease."""

    def __init__(self, archive: Path) -> None:
        self.archive = archive
        self.used_path = archive / "status" / OWNER_RULES_USED

    def defaults(self, roots: list[Path] | None = None) -> OwnerRules:
        saved = load_archive_config(self.archive).owner
        if saved is not None:
            return saved
        # Legacy files seed the editor only until the archive has saved YAML rules.
        return OwnerRules(include=read_owner_names(self.archive / OWNER_NAMES_FILENAME) + source_owner_names(roots or []))

    def state(self) -> OwnerRulesState:
        rules = self.defaults()
        known = self.used_path.exists()
        previous = OwnerRules.model_validate(safe_load(self.used_path.read_text(encoding="utf-8"))) if known else None
        return OwnerRulesState(
            include=rules.include, exclude=rules.exclude,
            revision=hashlib.sha256(rules.model_dump_json().encode()).hexdigest(),
            changed_since_import=known and rules != previous, import_rules_known=known,
        )

    def _check_lease(self, lease: WriterLease) -> None:
        if not lease.acquired or lease.lock_path.parent.parent.resolve() != self.archive.resolve():
            raise ValueError("An active writer lease for this document is required.")

    def save(self, rules: OwnerRules, lease: WriterLease, revision: str | None = None) -> OwnerRulesState:
        self._check_lease(lease)
        if revision is not None and revision != self.state().revision:
            raise ValueError("Owner rules changed in another window. Reload and try again.")
        config = load_archive_config(self.archive)
        if config.owner != rules:
            config.owner = rules
            save_archive_config(self.archive, config)
        return self.state()

    def record_import(self, rules: OwnerRules, lease: WriterLease) -> None:
        self._check_lease(lease)
        _atomic_text(self.used_path, safe_dump(rules.model_dump(mode="json"), sort_keys=False))

    def write_detected(self, catalog: sqlite3.Connection, rules: OwnerRules, lease: WriterLease) -> None:
        """Stream all matching archived senders, including earlier imports, outside YAML."""
        self._check_lease(lease)
        destination = self.archive / OWNER_NAMES_DETECTED
        descriptor, temporary = tempfile.mkstemp(prefix=".owner-detected-", dir=self.archive)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                rows = catalog.execute(
                    "SELECT DISTINCT lower(e.address) FROM email_addresses e WHERE "
                    "EXISTS (SELECT 1 FROM messages m WHERE m.sender_address_pk=e.address_pk) ORDER BY 1"
                )
                for (address,) in rows:
                    if rules.matches(address) and not any(character in address for character in "\x00\r\n"):
                        output.write(address + "\n")
            os.replace(temporary, destination)
        finally:
            Path(temporary).unlink(missing_ok=True)
