"""Build original-mailbox trees and persist per-user filter sets."""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import sys
import tempfile
from itertools import groupby
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field, field_validator


FILTER_SET_VERSION = 1
RESERVED_FILTER_NAMES = {"none", "save..."}


class MailboxSelection(BaseModel):
    """Stable path selection, optionally scoped to one source volume identity."""

    version: int = 1
    path: str = ""
    volume_identity: str | None = None

    @field_validator("path")
    @classmethod
    def valid_path(cls, value: str) -> str:
        normalized = PurePosixPath(value).as_posix().strip("/") if value else ""
        if normalized == "." or ".." in PurePosixPath(normalized).parts:
            raise ValueError("mailbox paths must be normalized volume-relative paths")
        return normalized

    def token(self) -> str:
        encoded = base64.urlsafe_b64encode(self.model_dump_json().encode("utf-8")).decode("ascii")
        return encoded.rstrip("=")

    @classmethod
    def from_token(cls, token: str) -> "MailboxSelection":
        padding = "=" * (-len(token) % 4)
        try:
            return cls.model_validate_json(base64.urlsafe_b64decode(token + padding))
        except (ValueError, TypeError) as error:
            raise ValueError("invalid original-mailbox selection") from error


class MailboxTreeNode(BaseModel):
    selection: str
    logical_selection: str
    label: str
    count: int
    kind: Literal["volume", "folder", "mailbox"]
    children: list["MailboxTreeNode"] = Field(default_factory=list)


class SourceTreeFile(BaseModel):
    volume_identity: str
    volume_label: str
    path: str
    source_kind: str

    @property
    def parts(self) -> tuple[str, ...]:
        return PurePosixPath(self.path).parts


class FilterSet(BaseModel):
    name: str
    show_volumes: bool = False
    selections: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        name = value.strip()
        if not name or name.casefold() in RESERVED_FILTER_NAMES:
            raise ValueError("filter-set name must not be empty or reserved")
        return name


class FilterSetPreferences(BaseModel):
    version: int = FILTER_SET_VERSION
    filter_sets: list[FilterSet] = Field(default_factory=list)


def preferences_path() -> Path:
    """Return the operating system's per-user preferences location."""
    if sys.platform == "darwin":
        root = Path.home() / "Library" / "Preferences"
    elif os.name == "nt":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "mailarchiver" / "filter-sets.json"


class FilterSetStore:
    """Atomically maintain versioned search preferences outside the archive."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or preferences_path()

    def read(self) -> FilterSetPreferences:
        if not self.path.exists():
            return FilterSetPreferences()
        preferences = FilterSetPreferences.model_validate_json(self.path.read_text(encoding="utf-8"))
        if preferences.version != FILTER_SET_VERSION:
            raise ValueError(f"unsupported filter-set preferences version {preferences.version}")
        return preferences

    def save(self, filter_set: FilterSet) -> FilterSetPreferences:
        preferences = self.read()
        preferences.filter_sets = [item for item in preferences.filter_sets if item.name != filter_set.name]
        preferences.filter_sets.append(filter_set)
        preferences.filter_sets.sort(key=lambda item: item.name.casefold())
        self._write(preferences)
        return preferences

    def rename(self, old_name: str, new_name: str) -> FilterSetPreferences:
        preferences = self.read()
        replacement = FilterSet(name=new_name)
        if any(item.name == replacement.name and item.name != old_name for item in preferences.filter_sets):
            raise ValueError(f"filter set already exists: {replacement.name}")
        target = next((item for item in preferences.filter_sets if item.name == old_name), None)
        if target is None:
            raise ValueError(f"no filter set named {old_name}")
        target.name = replacement.name
        preferences.filter_sets.sort(key=lambda item: item.name.casefold())
        self._write(preferences)
        return preferences

    def delete(self, name: str) -> FilterSetPreferences:
        preferences = self.read()
        remaining = [item for item in preferences.filter_sets if item.name != name]
        if len(remaining) == len(preferences.filter_sets):
            raise ValueError(f"no filter set named {name}")
        preferences.filter_sets = remaining
        self._write(preferences)
        return preferences

    def _write(self, preferences: FilterSetPreferences) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile("w", dir=self.path.parent, prefix=".filter-sets-", delete=False) as output:
                temporary = output.name
                output.write(preferences.model_dump_json(indent=2) + "\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)


def mailbox_tree(archive: Path, show_volumes: bool = False) -> list[MailboxTreeNode]:
    """Return an eagerly counted tree derived from source observations."""
    database = sqlite3.connect(f"file:{archive / 'archive.sqlite3'}?mode=ro", uri=True)
    try:
        rows = [
            SourceTreeFile(
                volume_identity=identity,
                volume_label=_volume_label(metadata),
                path=path,
                source_kind=source_kind,
            )
            for identity, metadata, path, source_kind in database.execute(
                "SELECT source_volumes.identity_json, source_volumes.metadata_json, source_files.source_path, "
                "source_files.source_kind FROM source_files JOIN source_volumes USING (source_volume_pk) "
                "WHERE EXISTS (SELECT 1 FROM observations INDEXED BY observations_source_file_offset "
                "WHERE observations.source_file_pk = source_files.source_file_pk AND observations.message_pk IS NOT NULL) "
                "ORDER BY source_volumes.identity_json, source_files.source_path"
            )
        ]
        if not show_volumes:
            return _nodes(database, rows, (), None)
        roots = []
        for identity, grouped in groupby(rows, key=lambda item: item.volume_identity):
            volume_rows = list(grouped)
            selection = MailboxSelection(volume_identity=identity)
            roots.append(
                MailboxTreeNode(
                    selection=selection.token(),
                    logical_selection=MailboxSelection().token(),
                    label=volume_rows[0].volume_label,
                    count=_count(database, selection),
                    kind="volume",
                    children=_nodes(database, volume_rows, (), identity),
                )
            )
        return roots
    finally:
        database.close()


def _nodes(
    database: sqlite3.Connection,
    rows: list[SourceTreeFile],
    prefix: tuple[str, ...],
    volume_identity: str | None,
) -> list[MailboxTreeNode]:
    depth = len(prefix)
    nodes = []
    eligible = (row for row in rows if len(row.parts) > depth and row.parts[:depth] == prefix)
    for label, grouped in groupby(sorted(eligible, key=lambda row: row.parts[depth]), key=lambda row: row.parts[depth]):
        branch_rows = list(grouped)
        path_parts = (*prefix, label)
        path = PurePosixPath(*path_parts).as_posix()
        direct_file = next((row for row in branch_rows if len(row.parts) == len(path_parts)), None)
        if direct_file is not None:
            kind, children = "mailbox", []
        elif _collapsible_single_message_folder(branch_rows, path_parts):
            kind, children = "mailbox", []
        else:
            kind = "folder"
            children = _nodes(database, branch_rows, path_parts, volume_identity)
        selection = MailboxSelection(path=path, volume_identity=volume_identity)
        nodes.append(
            MailboxTreeNode(
                selection=selection.token(),
                logical_selection=MailboxSelection(path=path).token(),
                label=label,
                count=_count(database, selection),
                kind=kind,
                children=children,
            )
        )
    return nodes


def _collapsible_single_message_folder(rows: list[SourceTreeFile], prefix: tuple[str, ...]) -> bool:
    remainders = [row.parts[len(prefix):] for row in rows]
    single_kinds = all(row.source_kind in {"message", "emlx"} for row in rows)
    direct = all(len(parts) == 1 for parts in remainders)
    maildir = all(len(parts) == 2 and parts[0] in {"cur", "new"} for parts in remainders)
    return single_kinds and (direct or maildir)


def _count(database: sqlite3.Connection, selection: MailboxSelection) -> int:
    clauses = ["observations.message_pk IS NOT NULL"]
    parameters: list[str] = []
    index = "source_files_path_volume"
    if selection.volume_identity is not None:
        clauses.append("source_volumes.identity_json = ?")
        parameters.append(selection.volume_identity)
        index = "source_files_volume_path"
    if selection.path:
        clauses.append("(source_files.source_path = ? OR (source_files.source_path >= ? AND source_files.source_path < ?))")
        parameters.extend((selection.path, selection.path + "/", selection.path + "0"))
    row = database.execute(
        "SELECT count(DISTINCT observations.message_pk) FROM source_files INDEXED BY " + index + " "
        "JOIN source_volumes USING (source_volume_pk) "
        "JOIN observations INDEXED BY observations_source_file_offset USING (source_file_pk) WHERE "
        + " AND ".join(clauses),
        parameters,
    ).fetchone()
    return 0 if row is None else int(row[0])


def _volume_label(metadata_json: str) -> str:
    metadata = json.loads(metadata_json)
    return str(metadata.get("volume_label") or metadata.get("current_mount_path") or "Unknown source volume")
