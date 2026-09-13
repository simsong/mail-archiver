# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Export private, hash-verified examples of ambiguous h3 equivalence classes."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .apple_mail_compare import create_cache_index, index_apple_mail_cache
from .layout import mbox_path
from .mbox import MboxLocation, read_verified_location
from .sources import emlx_bytes
from .standalone_verify import SEMANTIC_DOMAIN, semantic_bytes


class ReviewVariant(BaseModel):
    """One exported byte-preserving message representation."""

    model_config = ConfigDict(frozen=True)

    role: Literal["apple-cache", "canonical-archive"]
    filename: str
    source_reference: str
    raw_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_length: int = Field(ge=0)
    h2_group: str = ""
    h2_shared_across_stores: bool = False
    apple_autosave: bool = False


class ReviewCase(BaseModel):
    """One h3 equivalence class and all selected-store variants."""

    model_config = ConfigDict(frozen=True)

    case_number: int = Field(ge=1)
    directory: str
    semantic_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    services: list[str]
    cache_records: int = Field(ge=1)
    archive_records: int = Field(ge=2)
    exported_bytes: int = Field(ge=0)
    distinct_h2: int = Field(default=0, ge=0)
    h2_shared_across_stores: bool = False
    normalized_date_agrees: bool = False
    normalized_subject_agrees: bool = False
    date_present_on_all: bool = False
    subject_present_on_all: bool = False
    apple_autosave_files: list[str] = Field(default_factory=list)
    variants: list[ReviewVariant]


class ReviewSet(BaseModel):
    """Manifest for a private ambiguous-h3 review corpus."""

    model_config = ConfigDict(frozen=True)

    created_at: datetime
    selection_method: str
    requested_cases: int = Field(ge=1)
    exported_cases: int = Field(ge=0)
    exported_files: int = Field(ge=0)
    exported_bytes: int = Field(ge=0)
    cases: list[ReviewCase]


class ArchiveVariant(BaseModel):
    """Verified catalog location used for one canonical export."""

    model_config = ConfigDict(frozen=True)

    message_pk: int
    raw_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    filename: str
    byte_offset: int
    byte_length: int


def _semantic_digest(raw: bytes) -> str:
    return hashlib.sha256(semantic_bytes(raw)).hexdigest()


def _semantic_header_occurrences(raw: bytes, name: bytes) -> tuple[bytes, ...]:
    canonical = semantic_bytes(raw)
    header_block = canonical[len(SEMANTIC_DOMAIN) :].partition(b"\r\n\r\n")[0]
    prefix = name.lower() + b":"
    return tuple(
        line[len(prefix) :] for line in header_block.split(b"\r\n") if line.startswith(prefix)
    )


def _has_header(raw: bytes, name: bytes) -> bool:
    normalized = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    header_block = normalized.partition(b"\n\n")[0]
    return any(
        not line.startswith((b" ", b"\t")) and line.partition(b":")[0].lower() == name.lower()
        for line in header_block.split(b"\n")
    )


def _review_path(root: Path, relative: str) -> Path:
    """Reject edited manifests that redirect reads or writes outside the review set."""
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("review paths must be relative and contained")
    target = root
    for part in path.parts:
        target = target / part
        if target.is_symlink():
            raise ValueError("review paths must not contain symlinks")
    return target


def _write_private_bytes(path: Path, value: bytes) -> None:
    # Atomic replacement does not follow a preexisting symlink or hard link.
    with NamedTemporaryFile(dir=path.parent, prefix=".review-", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def _write_private_text(path: Path, value: str) -> None:
    _write_private_bytes(path, value.encode("utf-8"))


def _archive_variants(database: sqlite3.Connection, digest: str) -> list[ArchiveVariant]:
    rows = database.execute(
        """
        SELECT m.message_pk, m.sha256, g.filename, l.byte_offset, l.byte_length
        FROM archive.observations AS o
        JOIN archive.messages AS m ON m.message_pk = o.message_pk
        JOIN archive.locations AS l ON l.message_pk = m.message_pk
        JOIN archive.mbox_generations AS g ON g.generation_pk = l.generation_pk
        WHERE o.semantic_sha256 = ? AND o.message_pk IS NOT NULL
        GROUP BY m.message_pk
        ORDER BY m.message_pk
        """,
        (digest,),
    )
    return [
        ArchiveVariant(
            message_pk=int(row[0]),
            raw_sha256=str(row[1]),
            filename=str(row[2]),
            byte_offset=int(row[3]),
            byte_length=int(row[4]),
        )
        for row in rows
    ]


def _annotate_case(case_root: Path, case: ReviewCase) -> ReviewCase:
    autosaves: set[str] = set()
    dates: list[tuple[bytes, ...]] = []
    subjects: list[tuple[bytes, ...]] = []
    roles_by_h2: dict[str, set[str]] = {}
    for variant in case.variants:
        raw = _review_path(case_root, variant.filename).read_bytes()
        if hashlib.sha256(raw).hexdigest() != variant.raw_sha256:
            raise RuntimeError(f"review file h2 changed: {variant.filename}")
        if _semantic_digest(raw) != case.semantic_sha256:
            raise RuntimeError(f"review file h3 changed: {variant.filename}")
        dates.append(_semantic_header_occurrences(raw, b"date"))
        subjects.append(_semantic_header_occurrences(raw, b"subject"))
        if _has_header(raw, b"x-apple-auto-saved"):
            autosaves.add(variant.filename)
        roles_by_h2.setdefault(variant.raw_sha256, set()).add(variant.role)
    h2_groups = {
        digest: f"h2-{number:02d}"
        for number, digest in enumerate(sorted(roles_by_h2), 1)
    }
    shared = {
        digest for digest, roles in roles_by_h2.items() if roles == {"apple-cache", "canonical-archive"}
    }
    variants = [
        variant.model_copy(
            update={
                "h2_group": h2_groups[variant.raw_sha256],
                "h2_shared_across_stores": variant.raw_sha256 in shared,
                "apple_autosave": variant.filename in autosaves,
            }
        )
        for variant in case.variants
    ]
    return case.model_copy(
        update={
            "distinct_h2": len(roles_by_h2),
            "h2_shared_across_stores": bool(shared),
            "normalized_date_agrees": len(set(dates)) == 1,
            "normalized_subject_agrees": len(set(subjects)) == 1,
            "date_present_on_all": all(dates),
            "subject_present_on_all": all(subjects),
            "apple_autosave_files": [variant.filename for variant in variants if variant.apple_autosave],
            "variants": variants,
        }
    )


def _write_case_manifest(case_root: Path, case: ReviewCase) -> None:
    _write_private_text(case_root / "manifest.json", case.model_dump_json(indent=2) + "\n")


def _write_case(
    database: sqlite3.Connection,
    apple_root: Path,
    archive_root: Path,
    output: Path,
    case_number: int,
    digest: str,
) -> ReviewCase:
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("invalid h3 digest in archive catalog")
    directory_name = f"case-{case_number:02d}-h3-{digest[:16]}"
    case_root = output / directory_name
    cache_root = case_root / "apple-cache"
    archive_output = case_root / "canonical-archive"
    cache_root.mkdir(parents=True, mode=0o700)
    case_root.chmod(0o700)
    archive_output.mkdir(mode=0o700)
    variants: list[ReviewVariant] = []
    services: set[str] = set()
    cache_rows = database.execute(
        "SELECT relative_path, raw_sha256, service FROM cache_messages "
        "WHERE semantic_sha256 = ? ORDER BY relative_path",
        (digest,),
    ).fetchall()
    for number, row in enumerate(cache_rows, 1):
        relative_path, expected_raw, service = str(row[0]), str(row[1]), str(row[2])
        raw = emlx_bytes(apple_root / relative_path)
        actual_raw = hashlib.sha256(raw).hexdigest()
        if actual_raw != expected_raw or _semantic_digest(raw) != digest:
            raise RuntimeError(f"Apple Mail record changed during export: {relative_path}")
        filename = f"cache-{number:03d}-h2-{actual_raw[:16]}.eml"
        _write_private_bytes(cache_root / filename, raw)
        services.add(service)
        variants.append(
            ReviewVariant(
                role="apple-cache",
                filename=f"apple-cache/{filename}",
                source_reference=relative_path,
                raw_sha256=actual_raw,
                byte_length=len(raw),
            )
        )
    archive_variants = _archive_variants(database, digest)
    for number, candidate in enumerate(archive_variants, 1):
        raw = read_verified_location(
            mbox_path(archive_root, candidate.filename),
            MboxLocation(
                byte_offset=candidate.byte_offset,
                byte_length=candidate.byte_length,
            ),
            candidate.raw_sha256,
        )
        if _semantic_digest(raw) != digest:
            raise RuntimeError(f"stale h3 for archive message {candidate.message_pk}")
        filename = (
            f"archive-{number:03d}-message-{candidate.message_pk}"
            f"-h2-{candidate.raw_sha256[:16]}.eml"
        )
        _write_private_bytes(archive_output / filename, raw)
        variants.append(
            ReviewVariant(
                role="canonical-archive",
                filename=f"canonical-archive/{filename}",
                source_reference=(
                    f"data/mbox/{candidate.filename}@{candidate.byte_offset}"
                    f"+{candidate.byte_length}"
                ),
                raw_sha256=candidate.raw_sha256,
                byte_length=len(raw),
            )
        )
    case = ReviewCase(
        case_number=case_number,
        directory=directory_name,
        semantic_sha256=digest,
        services=sorted(services),
        cache_records=len(cache_rows),
        archive_records=len(archive_variants),
        exported_bytes=sum(variant.byte_length for variant in variants),
        variants=variants,
    )
    case = _annotate_case(case_root, case)
    _write_case_manifest(case_root, case)
    return case


def _write_index(output: Path, review: ReviewSet) -> None:
    with io.StringIO(newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            (
                "case", "h3", "services", "cache_records", "archive_records", "distinct_h2",
                "h2_shared_across_stores", "normalized_date_agrees",
                "normalized_subject_agrees", "apple_autosave_files", "exported_bytes",
            )
        )
        for case in review.cases:
            writer.writerow(
                (
                    case.directory,
                    case.semantic_sha256,
                    "; ".join(case.services),
                    case.cache_records,
                    case.archive_records,
                    case.distinct_h2,
                    case.h2_shared_across_stores,
                    case.normalized_date_agrees,
                    case.normalized_subject_agrees,
                    len(case.apple_autosave_files),
                    case.exported_bytes,
                )
            )
        _write_private_text(output / "summary.csv", stream.getvalue())
    exact_cross_store_cases = sum(case.h2_shared_across_stores for case in review.cases)
    autosave_files = sum(len(case.apple_autosave_files) for case in review.cases)
    lines = [
        "# Private ambiguous-h3 review set",
        "",
        "Do not commit or distribute this directory. It contains private RFC 5322 messages.",
        "",
        review.selection_method,
        "",
        f"The set contains {review.exported_files:,} files ({review.exported_bytes:,} bytes). "
        f"{exact_cross_store_cases} of {review.exported_cases} cases have an h2 representation "
        "shared by both stores; the remaining cases overlap only through h3. "
        f"{autosave_files} exported files contain `X-Apple-Auto-Saved`.",
        "",
        "Each case is one h3 equivalence class. `apple-cache/` and",
        "`canonical-archive/` contain every matching record from the two stores at export time.",
        "Each `manifest.json` records complete h2 and h3 identifiers, h2 equivalence groups,",
        "source references, and the autosave result for every file.",
        "",
        "`Date` and `Subject` below compare the exact components used by h3: all occurrences",
        "after DKIM-relaxed canonicalization. `same (present)` does not claim that the raw",
        "header lines are byte-identical; folding and whitespace can differ.",
        "",
        "| Case | H2 relationship | Distinct H2 | Date | Subject | Apple autosave | Bytes |",
        "| --- | --- | ---: | --- | --- | ---: | ---: |",
    ]
    lines.extend(
        f"| {case.directory} | "
        f"{'shared across stores' if case.h2_shared_across_stores else 'h3 only'} | "
        f"{case.distinct_h2:,} | "
        f"{'same (present)' if case.normalized_date_agrees and case.date_present_on_all else 'same (absent)' if case.normalized_date_agrees else 'different'} | "
        f"{'same (present)' if case.normalized_subject_agrees and case.subject_present_on_all else 'same (absent)' if case.normalized_subject_agrees else 'different'} | "
        f"{len(case.apple_autosave_files):,} | {case.exported_bytes:,} |"
        for case in review.cases
    )
    _write_private_text(output / "README.md", "\n".join(lines) + "\n")
    _write_private_text(output / "manifest.json", review.model_dump_json(indent=2) + "\n")


def refresh_review_report(output: Path) -> ReviewSet:
    """Recompute derived h2/header/autosave annotations for an existing private export."""
    output = output.expanduser().resolve()
    manifest_path = _review_path(output, "manifest.json")
    review = ReviewSet.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    cases = [_annotate_case(_review_path(output, case.directory), case) for case in review.cases]
    updated = review.model_copy(
        update={
            "exported_files": sum(len(case.variants) for case in cases),
            "exported_bytes": sum(case.exported_bytes for case in cases),
            "cases": cases,
        }
    )
    for case in cases:
        _write_case_manifest(_review_path(output, case.directory), case)
    _write_index(output, updated)
    return updated


def publish_review(staging: Path, output: Path) -> None:
    """Reserve a new destination and publish its completion manifest last."""
    children = sorted(staging.iterdir(), key=lambda path: (path.name == "manifest.json", path.name))
    output.mkdir(mode=0o700)
    published: list[Path] = []
    try:
        for child in children:
            child.rename(output / child.name)
            published.append(child)
    except BaseException:
        for child in reversed(published):
            (output / child.name).rename(child)
        output.rmdir()
        raise


def open_review_database(path: Path, catalog_path: Path) -> sqlite3.Connection:
    """Create a writable comparison index with an explicitly read-only archive."""
    database = sqlite3.connect(path, uri=True)
    try:
        create_cache_index(database)
        database.execute("ATTACH DATABASE ? AS archive", (catalog_path.resolve().as_uri() + "?mode=ro",))
    except BaseException:
        database.close()
        raise
    return database


def export_ambiguous_h3_examples(
    apple_root: Path,
    archive_root: Path,
    output: Path,
    *,
    limit: int = 20,
    progress_every: int = 10_000,
) -> ReviewSet:
    """Export all variants for deterministically selected ambiguous h3 classes."""
    apple_root = apple_root.expanduser().resolve()
    archive_root = archive_root.expanduser().resolve()
    output = output.expanduser().resolve()
    if any(output.is_relative_to(source) for source in (apple_root, archive_root)):
        raise ValueError("review output must be outside both source stores")
    if output.exists():
        raise FileExistsError(f"review output already exists: {output}")
    if limit < 1:
        raise ValueError("limit must be positive")
    catalog_path = archive_root / "archive.sqlite3"
    if not apple_root.is_dir() or not catalog_path.is_file():
        raise FileNotFoundError("Apple Mail root or archive catalog is missing")
    output.parent.mkdir(parents=True, exist_ok=True)
    selection = (
        "Selection is diagnostic rather than random: distinct h3 classes are ranked by "
        "descending canonical-archive variant count, then descending Apple-cache record "
        "count, then hexadecimal h3."
    )
    with TemporaryDirectory(prefix="mailarchiver-h3-review-", dir=output.parent) as temporary:
        temporary_root = Path(temporary)
        staging = temporary_root / "review"
        staging.mkdir(mode=0o700)
        database = open_review_database(temporary_root / "comparison.sqlite3", catalog_path)
        try:
            index_apple_mail_cache(database, apple_root, progress_every)
            rows = database.execute(
                """
                WITH archive_groups AS (
                    SELECT semantic_sha256, count(DISTINCT message_pk) AS archive_records
                    FROM archive.observations
                    WHERE message_pk IS NOT NULL AND semantic_sha256 IS NOT NULL
                    GROUP BY semantic_sha256
                    HAVING count(DISTINCT message_pk) > 1
                )
                SELECT c.semantic_sha256, count(*) AS cache_records,
                       a.archive_records
                FROM cache_messages AS c
                JOIN archive_groups AS a ON a.semantic_sha256 = c.semantic_sha256
                GROUP BY c.semantic_sha256, a.archive_records
                ORDER BY a.archive_records DESC, cache_records DESC,
                         c.semantic_sha256
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            cases = [
                _write_case(
                    database,
                    apple_root,
                    archive_root,
                    staging,
                    number,
                    str(row[0]),
                )
                for number, row in enumerate(rows, 1)
            ]
        finally:
            database.close()
        review = ReviewSet(
            created_at=datetime.now(timezone.utc),
            selection_method=selection,
            requested_cases=limit,
            exported_cases=len(cases),
            exported_files=sum(len(case.variants) for case in cases),
            exported_bytes=sum(case.exported_bytes for case in cases),
            cases=cases,
        )
        _write_index(staging, review)
        publish_review(staging, output)
    return review


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apple-mail", type=Path, default=Path.home() / "Library/Mail")
    parser.add_argument("--archive", type=Path, default=Path.home() / "mail-archive")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--progress-every", type=int, default=10_000)
    parser.add_argument(
        "--refresh-existing",
        action="store_true",
        help="recompute reports from an existing output without reading either source store",
    )
    args = parser.parse_args(argv)
    try:
        if args.refresh_existing:
            review = refresh_review_report(args.output)
        else:
            review = export_ambiguous_h3_examples(
                args.apple_mail,
                args.archive,
                args.output,
                limit=args.limit,
                progress_every=args.progress_every,
            )
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
        parser.exit(1, f"export failed: {error}\n")
    print(review.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
