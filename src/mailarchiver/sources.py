"""Discover and stream MBOX, Babyl, EMLX, EML, and Maildir messages read-only."""

from __future__ import annotations

import hashlib
import mailbox
import os
import re
import stat as stat_module
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Iterator
from email import policy
from email.parser import BytesParser
from pathlib import Path

from pydantic import BaseModel

from .plugin_api import (
    FileProbe,
    MailContainer,
    MailObject,
    PluginCapabilities,
    PluginContext,
    PluginManifest,
    ProgressEvent,
    SkippedInput,
    SourcePlugin,
    SourceReference,
    SourceRelationship,
    SourceSpec,
)
from .source_volume import SourceVolume, local_mount_path, local_source_volume


SourceKind = str
BABYL_OPTIONS = b"babyl options:"
BABYL_RECORD = b"\x1f\x0c"
BABYL_END = b"\x1f"
BABYL_EOOH = b"*** EOOH ***"
MBCP_ENVELOPE_SENDER = b"mbcp@s.eecs.harvard.edu"
MBCP_HEADERS = {"status", "x-mbcp-flags", "x-uid"}
XXX_ENVELOPE_SENDER = b"XXX"
XXX_WRAPPER_HEADERS = {"status", "x-keywords", "x-status"}
HEADER_SEPARATOR = re.compile(br"\r?\n\r?\n")
MBOXRD_QUOTED_FROM = re.compile(br"(?m)^>(?=>*From )")


class SourceMessage(BaseModel):
    path: Path
    raw: bytes
    source_offset: int
    bytes_done: int
    bytes_total: int
    exclusion_reason: str | None = None


class SourceFile(BaseModel):
    path: Path
    volume: SourceVolume
    source_path: str
    kind: SourceKind
    modified_at_ns: int
    byte_length: int


class SourcePlan(BaseModel):
    source: SourceFile
    sha256: str | None = None
    start_offset: int = 0
    skip: bool = False


class SourceInventory(BaseModel):
    file_count: int = 0
    byte_count: int = 0
    skipped_file_count: int = 0


class LocalContainerData(BaseModel):
    """Local-only state carried opaquely through the source-neutral framework."""

    source: SourceFile


class IncompleteAppleMailMessageError(ValueError):
    """An Apple Mail partial message cannot preserve detached attachment bytes."""


class FileParser(ABC):
    """Extension point for recognizing and streaming one source-file format."""

    kind: SourceKind

    @abstractmethod
    def recognizes(self, path: Path) -> bool:
        """Return whether this parser owns the source path."""

    @abstractmethod
    def messages(self, source: SourceFile, start_offset: int = 0) -> Iterator[SourceMessage]:
        """Stream source records in forensic order."""


class MailboxHierarchyParser(ABC):
    """Extension point for enumerating provider-specific mailbox containers."""

    kind: str
    available = True

    @abstractmethod
    def paths(self, source: Path) -> Iterator[Path]:
        """Yield candidate mail files or stream endpoints in hierarchy order."""


class FileFolderHierarchyParser(MailboxHierarchyParser):
    kind = "file-folder"

    def paths(self, source: Path) -> Iterator[Path]:
        if not source.exists():
            raise FileNotFoundError(source)
        if source.is_file():
            yield source
            return

        def raise_walk_error(error: OSError) -> None:
            raise error

        for directory, subdirectories, filenames in os.walk(source, onerror=raise_walk_error):
            subdirectories.sort()
            for filename in sorted(filenames):
                yield Path(directory) / filename


_HIERARCHY_PARSERS: list[MailboxHierarchyParser] = [FileFolderHierarchyParser()]


def mailbox_hierarchy_parsers() -> tuple[MailboxHierarchyParser, ...]:
    """Return the legacy local hierarchy compatibility registry."""
    return tuple(_HIERARCHY_PARSERS)


def register_mailbox_hierarchy_parser(parser: MailboxHierarchyParser) -> None:
    if any(candidate.kind == parser.kind for candidate in _HIERARCHY_PARSERS):
        raise ValueError(f"mailbox hierarchy parser already registered: {parser.kind}")
    _HIERARCHY_PARSERS.append(parser)


def unregister_mailbox_hierarchy_parser(kind: str) -> None:
    for index, parser in enumerate(_HIERARCHY_PARSERS):
        if parser.kind == kind:
            del _HIERARCHY_PARSERS[index]
            return
    raise ValueError(f"mailbox hierarchy parser is not registered: {kind}")


def emlx_bytes(path: Path) -> bytes:
    with path.open("rb") as source:
        length = int(source.readline().strip())
        raw = source.read(length)
    if len(raw) != length:
        raise ValueError(f"truncated emlx message: {path}")
    return raw


def is_mbox(path: Path) -> bool:
    with path.open("rb") as source:
        return source.read(5) == b"From "


def is_babyl(path: Path) -> bool:
    """Recognize an Emacs RMAIL Babyl file without relying on its suffix."""
    with path.open("rb") as source:
        return source.readline(256).rstrip(b"\r\n").lower() == BABYL_OPTIONS


def is_maildir_message(path: Path) -> bool:
    return maildir_root(path) is not None


def maildir_root(path: Path) -> Path | None:
    """Return the root for a direct message in a structurally valid Maildir."""
    if path.parent.name not in {"cur", "new"}:
        return None
    root = path.parent.parent
    return root if all((root / name).is_dir() for name in ("cur", "new", "tmp")) else None


class EmlxFileParser(FileParser):
    kind = "emlx"

    def recognizes(self, path: Path) -> bool:
        if path.name.lower().endswith(".partial.emlx"):
            raise IncompleteAppleMailMessageError(
                f"Apple Mail partial message omits detached attachment bytes: {path}; "
                "export the mailbox from Apple Mail before ingest"
            )
        return path.suffix.lower() == ".emlx"

    def messages(self, source: SourceFile, start_offset: int = 0) -> Iterator[SourceMessage]:
        if start_offset:
            raise ValueError(f"EMLX append resume is unsupported: {source.path}")
        yield SourceMessage(
            path=source.path,
            raw=emlx_bytes(source.path),
            source_offset=0,
            bytes_done=source.byte_length,
            bytes_total=source.byte_length,
        )


class BabylFileParser(FileParser):
    kind = "babyl"

    def recognizes(self, path: Path) -> bool:
        return is_babyl(path)

    def messages(self, source: SourceFile, start_offset: int = 0) -> Iterator[SourceMessage]:
        if start_offset:
            raise ValueError(f"Babyl append resume is unsupported: {source.path}")
        yield from babyl_messages(source)


class MboxFileParser(FileParser):
    kind = "mbox"

    def recognizes(self, path: Path) -> bool:
        return is_mbox(path)

    def messages(self, source: SourceFile, start_offset: int = 0) -> Iterator[SourceMessage]:
        box = mailbox.mbox(source.path, factory=None, create=False)
        try:
            for key in box.iterkeys():
                start, end = box._toc[key]
                if start < start_offset:
                    continue
                envelope_record = box.get_bytes(key, from_=True)
                envelope, _, _ = envelope_record.partition(b"\n")
                envelope_sender = _mbox_envelope_sender(envelope.rstrip(b"\r"))
                raw = box.get_bytes(key, from_=False)
                exclusion = _mbcp_exclusion(envelope_sender, raw)
                if envelope_sender == XXX_ENVELOPE_SENDER:
                    raw = _unwrap_xxx_record(raw)
                yield SourceMessage(
                    path=source.path,
                    raw=raw,
                    source_offset=start,
                    bytes_done=end,
                    bytes_total=source.byte_length,
                    exclusion_reason=exclusion,
                )
        finally:
            box.close()


class MessageFileParser(FileParser):
    kind = "message"

    def recognizes(self, path: Path) -> bool:
        return path.suffix.lower() == ".eml" or is_maildir_message(path)

    def messages(self, source: SourceFile, start_offset: int = 0) -> Iterator[SourceMessage]:
        if start_offset:
            raise ValueError(f"single-message append resume is unsupported: {source.path}")
        raw = source.path.read_bytes()
        yield SourceMessage(
            path=source.path,
            raw=raw,
            source_offset=0,
            bytes_done=len(raw),
            bytes_total=len(raw),
        )


_FILE_PARSERS: list[FileParser] = [EmlxFileParser(), BabylFileParser(), MboxFileParser(), MessageFileParser()]


class LocalSourcePlugin(SourcePlugin):
    """Discover local files and delegate each container to a file-parser plug-in."""

    kind = "file-folder"
    manifest = PluginManifest(
        api_version=1,
        plugin_type="source",
        kind=kind,
        name="Local file and folder source",
        implementation_version="1",
        priority=100,
        entrypoint="mailarchiver.sources:LocalSourcePlugin",
    )
    capabilities = PluginCapabilities(resumable=True, stable_inventory=True)

    def __init__(self, context: PluginContext) -> None:
        from .source_integrity import LocalContainerIntegrityControls

        self.file_plugins = context.files
        self.integrity_controls = LocalContainerIntegrityControls(self.source_file)
        self.volumes: dict[tuple[int, Path], SourceVolume] = {}
        self.mount_paths: dict[Path, Path] = {}

    def recognizes(self, source: SourceSpec) -> bool:
        return source.locator != "-" and source.kind in {None, self.kind} and "://" not in source.locator

    def discover(self, source: SourceSpec) -> Iterator[MailContainer | ProgressEvent | SkippedInput]:
        root = Path(source.locator)
        for candidate in FileFolderHierarchyParser().paths(root):
            path = candidate.resolve()
            stat = path.stat()
            if not stat_module.S_ISREG(stat.st_mode):
                yield SkippedInput(
                    source=SourceReference(
                        plugin_kind=self.kind,
                        source_id=str(root.resolve()),
                        hierarchy=(),
                        native_id=str(path),
                        display_name=str(path),
                    ),
                    reason_code="not-regular-file",
                    detail="not a regular file",
                )
                continue
            parser = self._recognize_file(path, stat.st_size)
            if parser is None:
                yield SkippedInput(
                    source=SourceReference(
                        plugin_kind=self.kind,
                        source_id=str(root.resolve()),
                        hierarchy=(),
                        native_id=str(path),
                        display_name=str(path),
                    ),
                    reason_code="unrecognized-file",
                    detail="no file parser recognized it",
                )
                continue
            mount_path = self.mount_paths.get(path.parent)
            if mount_path is None:
                mount_path = local_mount_path(path)
                self.mount_paths[path.parent] = mount_path
            volume = self.volumes.get((stat.st_dev, mount_path))
            if volume is None:
                volume = local_source_volume(path)
                self.volumes[(stat.st_dev, volume.mount_path)] = volume
            local = SourceFile(
                path=path,
                volume=volume,
                source_path=path.relative_to(volume.mount_path).as_posix(),
                kind=parser.manifest.kind,
                modified_at_ns=stat.st_mtime_ns,
                byte_length=stat.st_size,
            )
            reference = _local_reference(local)
            yield MailContainer(
                work_id=_local_work_id(local),
                source=reference,
                parser_kind=local.kind,
                estimated_bytes=local.byte_length,
                concurrency_key=reference.source_id,
                plugin_data_json=LocalContainerData(source=local).model_dump_json(),
            )

    def messages(
        self, container: MailContainer, checkpoint: str | None
    ) -> Iterator[MailObject | ProgressEvent]:
        source = self.source_file(container)
        parser = next(
            (item for item in self.file_plugins if item.manifest.kind == container.parser_kind),
            None,
        )
        if parser is None:
            raise ValueError(f"file parser plug-in is not registered: {container.parser_kind}")
        implementation = parser.implementation
        if isinstance(implementation, FileParser):
            start_offset = 0 if checkpoint is None else int(checkpoint)
            for message in implementation.messages(source, start_offset):
                yield MailObject(
                    work_id=container.work_id,
                    raw=message.raw,
                    source=container.source,
                    cursor=str(message.source_offset),
                    completed_bytes=message.bytes_done,
                    total_bytes=message.bytes_total,
                    exclusion_reason=message.exclusion_reason,
                )
            return
        yield from implementation.messages(container, checkpoint)

    @staticmethod
    def source_file(container: MailContainer) -> SourceFile:
        return LocalContainerData.model_validate_json(container.plugin_data_json).source

    def _recognize_file(self, path: Path, byte_length: int):
        with path.open("rb") as source:
            probe = FileProbe(path=path, byte_length=byte_length, prefix=source.read(4096))
        matches = []
        for plugin in self.file_plugins:
            implementation = plugin.implementation
            recognized = (
                implementation.recognizes(path)
                if isinstance(implementation, FileParser)
                else implementation.recognizes(probe)
            )
            if recognized:
                matches.append(plugin)
        if len(matches) > 1 and is_maildir_message(path):
            content_matches = [plugin for plugin in matches if plugin.manifest.kind != "message"]
            if len(content_matches) == 1:
                return content_matches[0]
        if len(matches) > 1 and not all(plugin.builtin for plugin in matches):
            kinds = ", ".join(plugin.manifest.kind for plugin in matches)
            raise ValueError(f"ambiguous file parser plug-ins for {path}: {kinds}")
        return None if not matches else matches[0]


def _local_reference(source: SourceFile) -> SourceReference:
    volume_id = hashlib.sha256(source.volume.identity_json.encode("utf-8")).hexdigest()
    return SourceReference(
        plugin_kind=LocalSourcePlugin.kind,
        source_id=f"local-volume:{volume_id}",
        hierarchy=_local_hierarchy(source),
        native_id=source.source_path,
        display_name=str(source.path),
        relationship=_local_relationship(source),
    )


def _local_hierarchy(source: SourceFile) -> tuple[str, ...]:
    """Separate a source file's physical identity from its logical mailbox."""
    logical = _logical_container_hierarchy(source)
    source_parts = tuple(Path(source.source_path).parts)
    return logical if logical is not None else (source_parts[:-1] or source_parts)


def _logical_container_hierarchy(source: SourceFile) -> tuple[str, ...] | None:
    """Return a structural Maildir or Apple package hierarchy when present."""
    source_parts = tuple(Path(source.source_path).parts)
    physical_parts = tuple(source.path.parts)
    root = maildir_root(source.path)
    if root is not None:
        hierarchy = source_parts[: -len(physical_parts) + len(root.parts)]
        return hierarchy or (root.name or "Maildir",)

    package_index = next(
        (
            index
            for index in range(len(physical_parts) - 1, -1, -1)
            if physical_parts[index].lower().endswith(".mbox")
        ),
        None,
    )
    inside_apple_package = package_index is not None and (
        source.kind == "emlx" or (source.kind == "mbox" and source.path.name == "mbox")
    )
    if inside_apple_package and package_index is not None:
        logical = list(source_parts[: -len(physical_parts) + package_index + 1])
        for index, part in enumerate(logical):
            if part.lower().endswith(".mbox"):
                logical[index] = part[:-5]
        return tuple(logical)
    return None


def _local_relationship(source: SourceFile) -> SourceRelationship:
    packages = [part for part in source.path.parts if part.lower().endswith(".mbox")]
    if source.kind != "emlx" or not any(part.casefold() == "[gmail].mbox" for part in packages):
        return SourceRelationship()
    gmail_index = next(
        index for index, part in enumerate(source.path.parts)
        if part.casefold() == "[gmail].mbox"
    )
    account_hint = source.path.parts[gmail_index - 1] if gmail_index else None
    return SourceRelationship(
        role="cache",
        upstream_plugin_kind="gmail",
        account_hint=account_hint,
    )


def local_hierarchy_path(source: SourceFile) -> str:
    """Return the catalog path for the physical source's logical mailbox."""
    logical = _logical_container_hierarchy(source)
    return source.source_path if logical is None else "/".join(logical)


def _local_work_id(source: SourceFile) -> str:
    digest = hashlib.sha256()
    digest.update(source.volume.identity_json.encode("utf-8"))
    digest.update(b"\0")
    digest.update(source.source_path.encode("utf-8"))
    return f"local-file:{digest.hexdigest()}"


def file_parsers() -> tuple[FileParser, ...]:
    """Return the ordered production parser registry."""
    return tuple(_FILE_PARSERS)


def register_file_parser(parser: FileParser, *, first: bool = False) -> None:
    """Register a file parser, rejecting ambiguous duplicate kinds."""
    if any(candidate.kind == parser.kind for candidate in _FILE_PARSERS):
        raise ValueError(f"file parser already registered: {parser.kind}")
    _FILE_PARSERS.insert(0 if first else len(_FILE_PARSERS), parser)


def unregister_file_parser(kind: SourceKind) -> None:
    """Remove a registered parser by kind; intended for plugin lifecycle management."""
    for index, parser in enumerate(_FILE_PARSERS):
        if parser.kind == kind:
            del _FILE_PARSERS[index]
            return
    raise ValueError(f"file parser is not registered: {kind}")


def _file_parser(path: Path) -> FileParser | None:
    matches = [parser for parser in _FILE_PARSERS if parser.recognizes(path)]
    if len(matches) > 1 and is_maildir_message(path):
        content_matches = [parser for parser in matches if parser.kind != "message"]
        if len(content_matches) == 1:
            return content_matches[0]
    if len(matches) > 1:
        kinds = ", ".join(parser.kind for parser in matches)
        raise ValueError(f"ambiguous file parsers for {path}: {kinds}")
    return None if not matches else matches[0]


def _mbox_envelope_sender(envelope: bytes) -> bytes:
    fields = envelope.split(maxsplit=2)
    return fields[1] if len(fields) >= 2 and fields[0] == b"From" else b""


def _mbcp_exclusion(envelope_sender: bytes, raw: bytes) -> str | None:
    if envelope_sender != MBCP_ENVELOPE_SENDER:
        return None
    message = BytesParser(policy=policy.compat32).parsebytes(raw)
    names = {name.casefold() for name in message.keys()}
    payload = message.get_payload()
    if {"x-uid", "x-mbcp-flags"} <= names <= MBCP_HEADERS and str(payload).strip() == "":
        return "Eudora MBCP metadata stub"
    return None


def _unwrap_xxx_record(raw: bytes) -> bytes:
    separator = HEADER_SEPARATOR.search(raw)
    if separator is None:
        return raw
    wrapper = BytesParser(policy=policy.compat32).parsebytes(raw[: separator.end()])
    if not set(name.casefold() for name in wrapper.keys()) <= XXX_WRAPPER_HEADERS:
        return raw
    nested = MBOXRD_QUOTED_FROM.sub(b"", raw[separator.end():])
    envelope, newline, message = nested.partition(b"\n")
    if not newline or _mbox_envelope_sender(envelope.rstrip(b"\r")) == b"":
        return raw
    return message


def _source_paths(source: Path, hierarchy: str = "file-folder") -> Iterator[Path]:
    parser = next((candidate for candidate in _HIERARCHY_PARSERS if candidate.kind == hierarchy), None)
    if parser is None:
        raise ValueError(f"mailbox hierarchy parser is not registered: {hierarchy}")
    yield from parser.paths(source)


def _source_kind(path: Path) -> SourceKind | None:
    parser = _file_parser(path)
    return None if parser is None else parser.kind


def source_inventory(
    roots: Iterable[Path],
    progress: Callable[[int, int], None] | None = None,
    skipped: Callable[[Path, str], None] | None = None,
    *,
    hierarchy: str = "file-folder",
) -> SourceInventory:
    """Count recognized source files and bytes without hashing or retaining them."""
    inventory = SourceInventory()
    for root in roots:
        for path in _source_paths(root, hierarchy):
            path = path.resolve()
            if _source_kind(path) is None:
                inventory.skipped_file_count += 1
                if skipped is not None:
                    skipped(path, "no file parser recognized it")
                continue
            inventory.file_count += 1
            inventory.byte_count += path.stat().st_size
            if progress is not None:
                progress(inventory.file_count, inventory.byte_count)
    return inventory


def source_files(source: Path, *, hierarchy: str = "file-folder") -> Iterator[SourceFile]:
    volumes: dict[tuple[int, Path], SourceVolume] = {}
    mount_paths: dict[Path, Path] = {}
    for path in _source_paths(source, hierarchy):
        path = path.resolve()
        kind = _source_kind(path)
        if kind is not None:
            stat = path.stat()
            mount_path = mount_paths.get(path.parent)
            if mount_path is None:
                mount_path = local_mount_path(path)
                mount_paths[path.parent] = mount_path
            volume = volumes.get((stat.st_dev, mount_path))
            if volume is None:
                volume = local_source_volume(path)
                volumes[(stat.st_dev, volume.mount_path)] = volume
            yield SourceFile(
                path=path,
                volume=volume,
                source_path=path.relative_to(volume.mount_path).as_posix(),
                kind=kind,
                modified_at_ns=stat.st_mtime_ns,
                byte_length=stat.st_size,
            )


def source_messages(source: SourceFile, start_offset: int = 0) -> Iterator[SourceMessage]:
    parser = next((candidate for candidate in _FILE_PARSERS if candidate.kind == source.kind), None)
    if parser is None:
        raise ValueError(f"no file parser registered for source kind {source.kind}")
    yield from parser.messages(source, start_offset)


def _without_container_newline(body: bytearray) -> bytes:
    """Remove the one line ending Babyl adds before its record separator."""
    if body.endswith(b"\r\n"):
        return bytes(body[:-2])
    if body.endswith(b"\n") or body.endswith(b"\r"):
        return bytes(body[:-1])
    return bytes(body)


def _header_separator(headers: bytearray) -> bytes:
    if headers.endswith(b"\r\n"):
        return b"\r\n"
    if headers.endswith(b"\n"):
        return b"\n"
    if headers.endswith(b"\r"):
        return b"\r"
    return b"\n"


def babyl_messages(source: SourceFile) -> Iterator[SourceMessage]:
    """Stream RFC 5322 messages from CRLF- or LF-delimited RMAIL Babyl files."""
    with source.path.open("rb") as mailbox_file:
        if mailbox_file.readline(256).rstrip(b"\r\n").lower() != BABYL_OPTIONS:
            raise ValueError(f"invalid Babyl header: {source.path}")
        delimiter_offset = 0
        for line in mailbox_file:
            delimiter_offset = mailbox_file.tell() - len(line)
            if line.rstrip(b"\r\n") == BABYL_RECORD:
                break
        else:
            raise ValueError(f"Babyl file has no message records: {source.path}")

        while True:
            labels = mailbox_file.readline()
            if not labels or labels[:1] not in {b"0", b"1"}:
                raise ValueError(f"invalid Babyl label record at offset {delimiter_offset}: {source.path}")
            headers = bytearray()
            for line in mailbox_file:
                if line.rstrip(b"\r\n") == BABYL_EOOH:
                    break
                headers.extend(line)
            else:
                raise ValueError(f"Babyl record has no EOOH marker at offset {delimiter_offset}: {source.path}")
            visible_headers = bytearray()
            for line in mailbox_file:
                if not line.rstrip(b"\r\n"):
                    break
                visible_headers.extend(line)
            else:
                raise ValueError(f"Babyl record has no visible-header terminator at offset {delimiter_offset}: {source.path}")
            selected_headers = headers if headers.rstrip(b"\r\n") else visible_headers

            body = bytearray()
            next_record = False
            for line in mailbox_file:
                marker = line.rstrip(b"\r\n")
                if marker in {BABYL_RECORD, BABYL_END}:
                    raw = bytes(selected_headers) + _header_separator(selected_headers) + _without_container_newline(body)
                    yield SourceMessage(
                        path=source.path,
                        raw=raw,
                        source_offset=delimiter_offset,
                        bytes_done=mailbox_file.tell(),
                        bytes_total=source.byte_length,
                    )
                    if marker == BABYL_END:
                        return
                    delimiter_offset = mailbox_file.tell() - len(line)
                    next_record = True
                    break
                body.extend(line)
            if not next_record:
                if body:
                    raw = bytes(selected_headers) + _header_separator(selected_headers) + bytes(body)
                    yield SourceMessage(
                        path=source.path,
                        raw=raw,
                        source_offset=delimiter_offset,
                        bytes_done=source.byte_length,
                        bytes_total=source.byte_length,
                    )
                return
