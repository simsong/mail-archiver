"""Versioned contracts shared by ingest plug-ins and the framework."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


API_VERSION = 1
PluginType = Literal["source", "file"]
EvidenceKind = Literal[
    "cryptographic-digest",
    "cryptographic-fixity",
    "immutable-identifier",
    "version-token",
    "cursor",
    "metadata",
]


class FrozenModel(BaseModel):
    """Strict immutable base for values crossing the plug-in boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class PluginManifest(FrozenModel):
    api_version: Literal[1]
    plugin_type: PluginType
    kind: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    name: str = Field(min_length=1)
    implementation_version: str = Field(min_length=1)
    priority: int = 100
    entrypoint: str = Field(min_length=3)


class PluginCapabilities(FrozenModel):
    resumable: bool = False
    stable_inventory: bool = False
    network_access: bool = False
    max_concurrency: int | None = Field(default=None, ge=1)


class SourceSpec(FrozenModel):
    kind: str | None = None
    locator: str = Field(min_length=1)
    configuration_json: str | None = None


class FileProbe(FrozenModel):
    path: Path
    byte_length: int = Field(ge=0)
    prefix: bytes


class SourceReference(FrozenModel):
    plugin_kind: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    hierarchy: tuple[str, ...] = ()
    native_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    provenance_json: str = "{}"

    @field_validator("hierarchy")
    @classmethod
    def validate_hierarchy(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(
            not part or part in {".", ".."} or "/" in part or "\\" in part or not part.isprintable()
            for part in value
        ):
            raise ValueError("hierarchy entries must be printable normalized path components")
        return value


class ArchiveReference(FrozenModel):
    format_id: str
    archive_id: str
    root: Path


class MailContainer(FrozenModel):
    work_id: str = Field(min_length=1)
    source: SourceReference
    parser_kind: str | None = None
    estimated_messages: int | None = Field(default=None, ge=0)
    estimated_bytes: int | None = Field(default=None, ge=0)
    concurrency_key: str = Field(min_length=1)
    plugin_data_json: str = "{}"


class MailObject(FrozenModel):
    work_id: str = Field(min_length=1)
    raw: bytes
    source: SourceReference
    cursor: str = Field(min_length=1)
    source_date_utc: datetime | None = None
    completed_messages: int | None = Field(default=None, ge=0)
    total_messages: int | None = Field(default=None, ge=0)
    completed_bytes: int | None = Field(default=None, ge=0)
    total_bytes: int | None = Field(default=None, ge=0)
    exclusion_reason: str | None = None

    @field_validator("source_date_utc")
    @classmethod
    def normalize_source_date(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("source_date_utc must be timezone-aware")
        return value.astimezone(timezone.utc)


class ProgressEvent(FrozenModel):
    work_id: str
    phase: str
    completed: int | None = Field(default=None, ge=0)
    total: int | None = Field(default=None, ge=0)
    unit: str | None = None


class SkippedInput(FrozenModel):
    source: SourceReference
    reason_code: str
    detail: str


class IntegrityEvidence(FrozenModel):
    control_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    evidence_kind: EvidenceKind
    algorithm: str | None = None
    value: str = Field(min_length=1)
    byte_length: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_algorithm(self) -> IntegrityEvidence:
        cryptographic = self.evidence_kind in {"cryptographic-digest", "cryptographic-fixity"}
        if cryptographic and self.algorithm is None:
            raise ValueError("cryptographic evidence requires an algorithm")
        if not cryptographic and self.algorithm is not None:
            raise ValueError("non-cryptographic evidence cannot declare an algorithm")
        return self


class IntegrityDecision(FrozenModel):
    action: Literal["read", "skip", "resume"]
    resume_cursor: str | None = None
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_resume_cursor(self) -> IntegrityDecision:
        if self.action == "resume" and self.resume_cursor is None:
            raise ValueError("resume decisions require a cursor")
        if self.action != "resume" and self.resume_cursor is not None:
            raise ValueError("only resume decisions may declare a cursor")
        return self


class SourceIntegrityControls(ABC):
    """Source-selected controls executed and persisted by the framework."""

    control_id: str

    @abstractmethod
    def plan(
        self,
        container: MailContainer,
        prior: tuple[IntegrityEvidence, ...],
    ) -> Iterator[IntegrityDecision | IntegrityEvidence | ProgressEvent]:
        """Yield evidence and exactly one scheduling decision."""

    @abstractmethod
    def complete(
        self,
        container: MailContainer,
        planned: tuple[IntegrityEvidence, ...],
    ) -> Iterator[IntegrityEvidence | ProgressEvent]:
        """Validate stable completion and yield the final evidence."""


class SourcePlugin(ABC):
    """Discover schedulable containers and stream their messages."""

    capabilities: PluginCapabilities
    integrity_controls: SourceIntegrityControls

    @abstractmethod
    def recognizes(self, source: SourceSpec) -> bool:
        """Return whether this plug-in owns the source specification."""

    @abstractmethod
    def discover(self, source: SourceSpec) -> Iterator[MailContainer | ProgressEvent | SkippedInput]:
        """Yield bounded work items and discovery events."""

    @abstractmethod
    def messages(
        self, container: MailContainer, resume_cursor: str | None
    ) -> Iterator[MailObject | ProgressEvent]:
        """Yield messages from one container without creating threads."""


class FileParserPlugin(ABC):
    """Recognize and stream one physical mailbox encoding."""

    @abstractmethod
    def recognizes(self, probe: FileProbe) -> bool:
        """Return whether this parser owns the probed local file."""

    @abstractmethod
    def messages(
        self, container: MailContainer, resume_cursor: str | None
    ) -> Iterator[MailObject | ProgressEvent]:
        """Yield records from one physical mailbox container."""


class LoadedPlugin(FrozenModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid", frozen=True, strict=True)

    manifest: PluginManifest
    implementation: Any
    origin: Path
    builtin: bool


class PluginContext(FrozenModel):
    """Read-only framework services supplied while constructing a source plug-in."""

    files: tuple[LoadedPlugin, ...] = ()


class PluginRegistry(FrozenModel):
    """Immutable, deterministically ordered plug-ins safe to share with workers."""

    sources: tuple[LoadedPlugin, ...] = ()
    files: tuple[LoadedPlugin, ...] = ()

    def source(self, kind: str) -> LoadedPlugin:
        return self._get(self.sources, "source", kind)

    def file(self, kind: str) -> LoadedPlugin:
        return self._get(self.files, "file", kind)

    @staticmethod
    def _get(plugins: tuple[LoadedPlugin, ...], plugin_type: PluginType, kind: str) -> LoadedPlugin:
        match = next((plugin for plugin in plugins if plugin.manifest.kind == kind), None)
        if match is None:
            raise KeyError(f"{plugin_type} plug-in is not registered: {kind}")
        return match
