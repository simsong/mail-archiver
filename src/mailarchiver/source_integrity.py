"""Source-specific integrity planning independent of ingest orchestration."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Generator, Iterator

from pydantic import BaseModel, ConfigDict, Field

from .plugin_api import (
    IntegrityDecision,
    IntegrityEvidence,
    MailContainer,
    ProgressEvent,
    SourceIntegrityControls as PluginSourceIntegrityControls,
)
from .sources import SourceFile, SourcePlan


LOCAL_FILE_CONTROL_ID = "local-file-sha256-v1"
LOCAL_FILE_PREFIX_CONTROL_ID = "local-file-prefix-sha256-v1"
SOURCE_INTEGRITY_PHASE = "checking source integrity"

IntegrityEvent = IntegrityDecision | IntegrityEvidence | ProgressEvent
ProgressCallback = Callable[[int, int], None]


class SourceIntegrityCheckpoint(BaseModel):
    """The last successfully completed local-source integrity checkpoint."""

    model_config = ConfigDict(frozen=True)

    byte_length: int | None = Field(default=None, ge=0)
    sha256: str | None = None
    modified_at_ns: int | None = None


class SourceIntegrityResult(BaseModel):
    """One source-control plan plus the evidence used to make it."""

    model_config = ConfigDict(frozen=True)

    plan: SourcePlan
    decision: IntegrityDecision
    evidence: tuple[IntegrityEvidence, ...]


class _FileHashes(BaseModel):
    model_config = ConfigDict(frozen=True)

    prefix_sha256: str
    sha256: str


class LocalFileIntegrityControls:
    """SHA-256 and append-boundary controls for a local source file."""

    control_id = LOCAL_FILE_CONTROL_ID

    def plan(
        self,
        source: SourceFile,
        prior: SourceIntegrityCheckpoint | None,
    ) -> Iterator[IntegrityEvent]:
        if prior is None:
            yield IntegrityDecision(action="read", reason="source has no prior checkpoint")
            return
        if prior.byte_length is None or prior.sha256 is None:
            digest = yield from _sha256_file(source)
            yield _full_file_evidence(source, digest)
            yield IntegrityDecision(action="read", reason="prior checkpoint is incomplete")
            return
        if source.byte_length == prior.byte_length:
            digest = yield from _sha256_file(source)
            yield _full_file_evidence(source, digest)
            if digest == prior.sha256:
                yield IntegrityDecision(action="skip", reason="complete SHA-256 matches prior checkpoint")
            else:
                yield IntegrityDecision(action="read", reason="complete SHA-256 differs from prior checkpoint")
            return
        if source.byte_length > prior.byte_length:
            hashes = yield from _sha256_file_with_prefix(source, prior.byte_length)
            yield _prefix_evidence(source, prior.byte_length, hashes.prefix_sha256)
            yield _full_file_evidence(source, hashes.sha256)
            if (
                hashes.prefix_sha256 == prior.sha256
                and source.kind == "mbox"
                and _has_mbox_append_boundary(source, prior.byte_length)
            ):
                yield IntegrityDecision(
                    action="resume",
                    resume_cursor=str(prior.byte_length),
                    reason="verified MBOX prefix ends at an appended message boundary",
                )
            else:
                yield IntegrityDecision(
                    action="read",
                    reason="grown source does not satisfy safe append-resume controls",
                )
            return
        digest = yield from _sha256_file(source)
        yield _full_file_evidence(source, digest)
        yield IntegrityDecision(action="read", reason="source is shorter than its prior checkpoint")

    def complete(
        self,
        source: SourceFile,
        sha256: str | None = None,
    ) -> Iterator[IntegrityEvidence | ProgressEvent]:
        digest = sha256
        if digest is None:
            digest = yield from _sha256_file(source)
        current = source.path.stat()
        if current.st_size != source.byte_length or current.st_mtime_ns != source.modified_at_ns:
            raise RuntimeError(f"source changed during ingest: {source.path}")
        yield _full_file_evidence(source, digest)

    def source_plan(
        self,
        source: SourceFile,
        prior: SourceIntegrityCheckpoint | None,
        progress: ProgressCallback | None = None,
    ) -> SourcePlan:
        """Adapt integrity events to the current local-file coordinator model."""
        return self.evaluate(source, prior, progress).plan

    def evaluate(
        self,
        source: SourceFile,
        prior: SourceIntegrityCheckpoint | None,
        progress: ProgressCallback | None = None,
    ) -> SourceIntegrityResult:
        """Collect one planning generator run for framework scheduling and persistence."""
        decision: IntegrityDecision | None = None
        sha256: str | None = None
        evidence: list[IntegrityEvidence] = []
        subject_id = _subject_id(source)
        for event in self.plan(source, prior):
            if isinstance(event, ProgressEvent):
                _report_progress(event, progress)
            elif isinstance(event, IntegrityEvidence):
                evidence.append(event)
                if event.control_id == self.control_id and event.subject_id == subject_id:
                    sha256 = event.value
            elif decision is None:
                decision = event
            else:
                raise RuntimeError(f"source integrity control emitted multiple decisions for {source.path}")
        if decision is None:
            raise RuntimeError(f"source integrity control emitted no decision for {source.path}")
        start_offset = 0
        if decision.action == "resume":
            if decision.resume_cursor is None:
                raise RuntimeError(f"resume decision omitted its cursor for {source.path}")
            try:
                start_offset = int(decision.resume_cursor)
            except ValueError as error:
                raise RuntimeError(f"invalid local-file resume cursor for {source.path}") from error
        return SourceIntegrityResult(
            plan=SourcePlan(
                source=source,
                sha256=sha256,
                start_offset=start_offset,
                skip=decision.action == "skip",
            ),
            decision=decision,
            evidence=tuple(evidence),
        )

    def complete_checkpoint(
        self,
        source: SourceFile,
        sha256: str | None = None,
        progress: ProgressCallback | None = None,
    ) -> SourceIntegrityCheckpoint:
        """Adapt final evidence to the current local-file catalog fields."""
        digest: str | None = None
        subject_id = _subject_id(source)
        for event in self.complete(source, sha256):
            if isinstance(event, ProgressEvent):
                _report_progress(event, progress)
            elif event.control_id == self.control_id and event.subject_id == subject_id:
                digest = event.value
        if digest is None:
            raise RuntimeError(f"source integrity control emitted no complete digest for {source.path}")
        return SourceIntegrityCheckpoint(
            byte_length=source.byte_length,
            sha256=digest,
            modified_at_ns=source.modified_at_ns,
        )


class LocalContainerIntegrityControls(PluginSourceIntegrityControls):
    """Bind local-file controls to the source-neutral plug-in contract."""

    control_id = LOCAL_FILE_CONTROL_ID

    def __init__(self, source_file: Callable[[MailContainer], SourceFile]) -> None:
        self.source_file = source_file
        self.local = LocalFileIntegrityControls()

    def plan(
        self,
        container: MailContainer,
        prior: tuple[IntegrityEvidence, ...],
    ) -> Iterator[IntegrityEvent]:
        checkpoint = None
        full = next(
            (
                item
                for item in reversed(prior)
                if item.control_id == self.control_id
                and item.evidence_kind == "cryptographic-digest"
                and item.algorithm == "sha256"
            ),
            None,
        )
        if full is not None:
            checkpoint = SourceIntegrityCheckpoint(byte_length=full.byte_length, sha256=full.value)
        yield from self.local.plan(self.source_file(container), checkpoint)

    def complete(
        self,
        container: MailContainer,
        planned: tuple[IntegrityEvidence, ...],
    ) -> Iterator[IntegrityEvidence | ProgressEvent]:
        full = next(
            (item for item in reversed(planned) if item.control_id == self.control_id),
            None,
        )
        yield from self.local.complete(
            self.source_file(container),
            None if full is None else full.value,
        )


def _report_progress(event: ProgressEvent, progress: ProgressCallback | None) -> None:
    if progress is not None and event.completed is not None and event.total is not None:
        progress(event.completed, event.total)


def _subject_id(source: SourceFile) -> str:
    digest = hashlib.sha256()
    digest.update(source.volume.identity_json.encode("utf-8"))
    digest.update(b"\0")
    digest.update(source.source_path.encode("utf-8"))
    return f"local-file:{digest.hexdigest()}"


def _full_file_evidence(source: SourceFile, digest: str) -> IntegrityEvidence:
    return IntegrityEvidence(
        control_id=LOCAL_FILE_CONTROL_ID,
        subject_id=_subject_id(source),
        evidence_kind="cryptographic-digest",
        algorithm="sha256",
        value=digest,
        byte_length=source.byte_length,
    )


def _prefix_evidence(source: SourceFile, prefix_length: int, digest: str) -> IntegrityEvidence:
    return IntegrityEvidence(
        control_id=LOCAL_FILE_PREFIX_CONTROL_ID,
        subject_id=f"{_subject_id(source)}#prefix:{prefix_length}",
        evidence_kind="cryptographic-digest",
        algorithm="sha256",
        value=digest,
        byte_length=prefix_length,
    )


def _progress(source: SourceFile, completed: int, total: int) -> ProgressEvent:
    return ProgressEvent(
        work_id=_subject_id(source),
        phase=SOURCE_INTEGRITY_PHASE,
        completed=completed,
        total=total,
        unit="bytes",
    )


def _sha256_file(source: SourceFile) -> Generator[ProgressEvent, None, str]:
    total = source.path.stat().st_size
    remaining = total
    digest = hashlib.sha256()
    with source.path.open("rb") as input_file:
        while remaining:
            block = input_file.read(min(1024 * 1024, remaining))
            if not block:
                raise ValueError(f"source shortened while hashing: {source.path}")
            digest.update(block)
            remaining -= len(block)
            yield _progress(source, total - remaining, total)
    return digest.hexdigest()


def _sha256_file_with_prefix(
    source: SourceFile,
    prefix_length: int,
) -> Generator[ProgressEvent, None, _FileHashes]:
    total = source.path.stat().st_size
    if not 0 <= prefix_length <= total:
        raise ValueError(f"invalid prefix length for {source.path}: {prefix_length}")
    digest = hashlib.sha256()
    prefix_sha256 = digest.hexdigest() if prefix_length == 0 else ""
    done = 0
    with source.path.open("rb") as input_file:
        while done < total:
            boundary = prefix_length if done < prefix_length else total
            block = input_file.read(min(1024 * 1024, boundary - done))
            if not block:
                raise ValueError(f"source shortened while hashing: {source.path}")
            digest.update(block)
            done += len(block)
            if done == prefix_length:
                prefix_sha256 = digest.copy().hexdigest()
            yield _progress(source, done, total)
    return _FileHashes(prefix_sha256=prefix_sha256, sha256=digest.hexdigest())


def _has_mbox_append_boundary(source: SourceFile, offset: int) -> bool:
    with source.path.open("rb") as input_file:
        if offset:
            input_file.seek(offset - 1)
            if input_file.read(1) != b"\n":
                return False
        input_file.seek(offset)
        return input_file.read(5) == b"From "
