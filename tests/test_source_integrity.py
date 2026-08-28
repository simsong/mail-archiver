"""Exercise local source controls independently of workers and persistence."""

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from mailarchiver.plugin_api import IntegrityDecision, IntegrityEvidence, ProgressEvent
from mailarchiver.source_integrity import LocalFileIntegrityControls, SourceIntegrityCheckpoint
from mailarchiver.source_volume import SourceVolume
from mailarchiver.sources import SourceFile


def _source(path: Path, kind: str = "mbox") -> SourceFile:
    stat = path.stat()
    return SourceFile(
        path=path,
        volume=SourceVolume(identity_json='{"kind":"fixture"}', metadata_json="{}", mount_path=path.parent),
        source_path=path.name,
        kind=kind,
        modified_at_ns=stat.st_mtime_ns,
        byte_length=stat.st_size,
    )


def _decision(events: list[IntegrityDecision | IntegrityEvidence | ProgressEvent]) -> IntegrityDecision:
    decisions = [event for event in events if isinstance(event, IntegrityDecision)]
    assert len(decisions) == 1
    return decisions[0]


def test_local_control_hashes_and_skips_only_an_unchanged_source(tmp_path: Path) -> None:
    """Requirement: a full matching source digest permits a whole-container skip."""
    path = tmp_path / "mailbox.mbox"
    original = b"From sender@example Thu Feb  1 12:00:00 2024\nmessage\n"
    path.write_bytes(original)
    source = _source(path)
    prior = SourceIntegrityCheckpoint(byte_length=len(original), sha256=hashlib.sha256(original).hexdigest())
    updates: list[tuple[int, int]] = []

    plan = LocalFileIntegrityControls().source_plan(source, prior, lambda done, total: updates.append((done, total)))

    assert plan.skip
    assert plan.start_offset == 0
    assert plan.sha256 == prior.sha256
    assert updates[-1] == (len(original), len(original))

    replacement = original.replace(b"message", b"changed")
    assert len(replacement) == len(original)
    path.write_bytes(replacement)
    changed = _source(path)
    changed_plan = LocalFileIntegrityControls().source_plan(changed, prior)

    assert not changed_plan.skip
    assert changed_plan.start_offset == 0
    assert changed_plan.sha256 == hashlib.sha256(replacement).hexdigest()


def test_local_control_reprocesses_a_truncated_source(tmp_path: Path) -> None:
    """Requirement: a source shorter than its checkpoint never resumes or skips."""
    path = tmp_path / "mailbox.mbox"
    original = b"From sender@example Thu Feb  1 12:00:00 2024\nmessage body\n"
    path.write_bytes(original[:-5])
    source = _source(path)
    prior = SourceIntegrityCheckpoint(byte_length=len(original), sha256=hashlib.sha256(original).hexdigest())

    events = list(LocalFileIntegrityControls().plan(source, prior))
    plan = LocalFileIntegrityControls().source_plan(source, prior)

    assert _decision(events).action == "read"
    assert "shorter" in _decision(events).reason
    assert not plan.skip
    assert plan.start_offset == 0
    assert plan.sha256 == hashlib.sha256(original[:-5]).hexdigest()


@pytest.mark.parametrize(
    ("kind", "append", "prefix_matches", "expected_action"),
    (
        ("mbox", b"From next@example Fri Feb  2 12:00:00 2024\nnext\n", True, "resume"),
        ("mbox", b"From next@example Fri Feb  2 12:00:00 2024\nnext\n", False, "read"),
        ("mbox", b"not an MBOX boundary\n", True, "read"),
        ("babyl", b"From next@example Fri Feb  2 12:00:00 2024\nnext\n", True, "read"),
    ),
)
def test_local_control_resumes_only_a_verified_mbox_append(
    tmp_path: Path,
    kind: str,
    append: bytes,
    prefix_matches: bool,
    expected_action: str,
) -> None:
    """Requirement: append resume requires both prefix fixity and an MBOX boundary."""
    path = tmp_path / "mailbox"
    original = b"From sender@example Thu Feb  1 12:00:00 2024\nmessage\n"
    path.write_bytes(original + append)
    source = _source(path, kind)
    prior_bytes = original if prefix_matches else b"x" * len(original)
    prior = SourceIntegrityCheckpoint(byte_length=len(original), sha256=hashlib.sha256(prior_bytes).hexdigest())

    events = list(LocalFileIntegrityControls().plan(source, prior))
    decision = _decision(events)
    evidence = [event for event in events if isinstance(event, IntegrityEvidence)]

    assert decision.action == expected_action
    assert decision.resume_cursor == (str(len(original)) if expected_action == "resume" else None)
    assert {item.byte_length for item in evidence} == {len(original), len(original + append)}
    assert all(item.algorithm == "sha256" for item in evidence)


def test_local_control_rejects_an_append_after_an_unterminated_mbox_record(tmp_path: Path) -> None:
    """Requirement: append resume starts only at a line-delimited MBOX separator."""
    path = tmp_path / "mailbox.mbox"
    original = b"From sender@example Thu Feb  1 12:00:00 2024\nmessage without newline"
    path.write_bytes(original + b"From next@example Fri Feb  2 12:00:00 2024\nnext\n")
    prior = SourceIntegrityCheckpoint(
        byte_length=len(original),
        sha256=hashlib.sha256(original).hexdigest(),
    )

    decision = _decision(list(LocalFileIntegrityControls().plan(_source(path), prior)))

    assert decision.action == "read"
    assert decision.resume_cursor is None


def test_completion_rejects_a_source_that_changed_after_discovery(tmp_path: Path) -> None:
    """Requirement: a source checkpoint is not advanced when source metadata changes in flight."""
    path = tmp_path / "message.eml"
    path.write_bytes(b"From: sender@example\n\nbody\n")
    source = _source(path, "message")
    path.write_bytes(path.read_bytes() + b"changed\n")

    with pytest.raises(RuntimeError, match="source changed during ingest"):
        LocalFileIntegrityControls().complete_checkpoint(source)


def test_completion_returns_typed_final_evidence_and_checkpoint(tmp_path: Path) -> None:
    """Requirement: final source evidence binds SHA-256 to the stable completed byte length."""
    path = tmp_path / "message.eml"
    raw = b"From: sender@example\n\nbody\n"
    path.write_bytes(raw)
    source = _source(path, "message")
    control = LocalFileIntegrityControls()

    events = list(control.complete(source))
    evidence = [event for event in events if isinstance(event, IntegrityEvidence)]
    checkpoint = control.complete_checkpoint(source)

    assert any(isinstance(event, ProgressEvent) for event in events)
    assert len(evidence) == 1
    assert evidence[0].evidence_kind == "cryptographic-digest"
    assert evidence[0].algorithm == "sha256"
    assert evidence[0].value == hashlib.sha256(raw).hexdigest()
    assert checkpoint == SourceIntegrityCheckpoint(
        byte_length=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        modified_at_ns=source.modified_at_ns,
    )


def test_integrity_evidence_cannot_mislabel_provider_tokens_as_hashes() -> None:
    """Requirement: provider cursors/version tokens and cryptographic fixity remain distinct evidence."""
    with pytest.raises(ValidationError, match="non-cryptographic evidence cannot declare an algorithm"):
        IntegrityEvidence(
            control_id="provider-v1",
            subject_id="message-1",
            evidence_kind="version-token",
            algorithm="sha256",
            value="etag-value",
        )
    with pytest.raises(ValidationError, match="cryptographic evidence requires an algorithm"):
        IntegrityEvidence(
            control_id="file-v1",
            subject_id="file-1",
            evidence_kind="cryptographic-digest",
            value="not-adequately-described",
        )
    with pytest.raises(ValidationError, match="resume decisions require a cursor"):
        IntegrityDecision(action="resume", reason="cursor omitted")
    with pytest.raises(ValidationError, match="only resume decisions may declare a cursor"):
        IntegrityDecision(action="skip", resume_cursor="unexpected", reason="invalid cursor")
