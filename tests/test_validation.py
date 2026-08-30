"""Requirements: public validation acquisition is bounded, safe, and source-preserving."""

from __future__ import annotations

import mailbox
import gzip
import stat
import subprocess
import tarfile
import zipfile
from email.message import EmailMessage
from pathlib import Path

import py7zr
import pytest

from mailarchiver.validation import (
    AcquiredArtifact,
    DataLayout,
    DatasetConfig,
    PreprocessConfig,
    SourceConfig,
    SourceManifest,
    extract,
    extract_7z,
    extract_gzip,
    extract_tar,
    extract_zip,
    load_datasets,
    prepare,
)


CONFIG_DIR = Path(__file__).parents[1] / "validation" / "datasets"


def dataset(dataset_id: str, kind: str) -> DatasetConfig:
    return DatasetConfig(
        schema_version=1,
        id=dataset_id,
        title=dataset_id,
        homepage="https://example.test/",
        max_unpacked_bytes=1024 * 1024,
        sources=[SourceConfig(kind="http", url="https://example.test/source", filename="source")],
        preprocess=PreprocessConfig(kind=kind, include=["*", "**/*"]),
    )


def test_public_dataset_configs_are_strict_and_exclude_avocado() -> None:
    datasets = load_datasets(CONFIG_DIR)

    assert set(datasets) == {
        "apache-httpd-dev",
        "enron",
        "gcc",
        "gnu-emacs-devel",
        "ietf-822",
        "lore-linux-doc",
        "sf-lovers",
        "spamassassin",
        "usenet-comp-mail-mime",
    }
    assert "avocado" not in datasets
    assert all(item.enabled for item in datasets.values())
    assert all(source.url.startswith("https://") for item in datasets.values() for source in item.sources)


def test_prepare_preserves_eml_and_mbox_bytes(tmp_path: Path) -> None:
    eml = b"From: sender@example.test\r\nTo: recipient@example.test\r\nSubject: invalid " + bytes([0xFF]) + b"\r\n\r\nbody\r\n"
    mbox = b"From sender@example.test Sat Jan  1 00:00:00 2000\nSubject: mboxrd\n\n>From body\n\n"
    source = tmp_path / "source"
    source.mkdir()
    (source / "message").write_bytes(eml)
    (source / "mailbox").write_bytes(mbox)
    layout = DataLayout(root=tmp_path / "data")

    count = prepare(dataset("mixed-bytes", "mixed"), [source], layout)
    prepared = sorted(layout.prepared("mixed-bytes").iterdir())

    assert count == 2
    assert {path.suffix for path in prepared} == {".eml", ".mbox"}
    assert {path.read_bytes() for path in prepared} == {eml, mbox}


def test_message_preparation_removes_only_the_mbox_envelope(tmp_path: Path) -> None:
    raw = b"From: sender@example.test\nSubject: wrapped\n\n>From quoted body\n"
    wrapped = b"From sender@example.test Sat Jan  1 00:00:00 2000\n" + raw
    source = tmp_path / "message"
    source.write_bytes(wrapped)
    layout = DataLayout(root=tmp_path / "data")

    assert prepare(dataset("wrapped-message", "messages"), [source], layout) == 1
    assert next(layout.prepared("wrapped-message").iterdir()).read_bytes() == raw


def test_prepare_converts_babyl_to_individual_messages(tmp_path: Path) -> None:
    source = tmp_path / "sf-lovers.babyl"
    message = EmailMessage()
    message["From"] = "fan@example.test"
    message["To"] = "sf-lovers@example.test"
    message["Subject"] = "The Left Hand of Darkness"
    message.set_content("A classic.\n")
    box = mailbox.Babyl(source, create=True)
    box.add(message)
    box.close()
    layout = DataLayout(root=tmp_path / "data")

    assert prepare(dataset("sf-test", "sf-lovers"), [source], layout) == 1
    prepared = next(layout.prepared("sf-test").iterdir())
    assert b"Subject: The Left Hand of Darkness" in prepared.read_bytes()


def test_archive_extractors_reject_escape_links_and_bombs(tmp_path: Path) -> None:
    destination = tmp_path / "out"
    bad_zip = tmp_path / "escape.zip"
    with zipfile.ZipFile(bad_zip, "w") as archive:
        archive.writestr("../escape", b"bad")
    with pytest.raises(ValueError, match="unsafe archive member"):
        extract_zip(bad_zip, destination, 100)

    link_zip = tmp_path / "link.zip"
    with zipfile.ZipFile(link_zip, "w") as archive:
        link = zipfile.ZipInfo("link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "outside")
    with pytest.raises(ValueError, match="unsupported ZIP member"):
        extract_zip(link_zip, destination, 100)

    bad_tar = tmp_path / "link.tar"
    with tarfile.open(bad_tar, "w") as archive:
        link = tarfile.TarInfo("link")
        link.type = tarfile.SYMTYPE
        link.linkname = "outside"
        archive.addfile(link)
    with pytest.raises(ValueError, match="unsupported tar member"):
        extract_tar(bad_tar, destination, 100)

    bomb_zip = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb_zip, "w") as archive:
        archive.writestr("large", b"x" * 101)
    with pytest.raises(ValueError, match="configured limit"):
        extract_zip(bomb_zip, destination, 100)


def test_dataset_expansion_limit_is_cumulative_across_artifacts(tmp_path: Path) -> None:
    archives = []
    for number in range(2):
        path = tmp_path / f"{number}.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(f"{number}.eml", b"x" * 60)
        archives.append(
            AcquiredArtifact(source_kind="http", url=f"https://example.test/{number}", path=str(path), archive="zip")
        )
    configured = dataset("bounded", "messages").model_copy(update={"max_unpacked_bytes": 100})
    manifest = SourceManifest(dataset_id="bounded", acquired_at="2026-08-29T00:00:00Z", artifacts=archives)

    with pytest.raises(ValueError, match="configured limit"):
        extract(configured, manifest, DataLayout(root=tmp_path / "data"))


def test_7z_extraction_preserves_content_and_enforces_limit(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    raw = b"From: sf-lover@example.test\nSubject: digest\n\ncontent\n"
    (source_dir / "digest").write_bytes(raw)
    archive_path = tmp_path / "digest.7z"
    with py7zr.SevenZipFile(archive_path, "w") as archive:
        archive.writeall(source_dir, "archive")

    extracted = tmp_path / "extracted"
    extract_7z(archive_path, extracted, 1024)
    assert (extracted / "archive" / "digest").read_bytes() == raw
    with pytest.raises(ValueError, match="configured limit"):
        extract_7z(archive_path, tmp_path / "too-small", len(raw) - 1)


def test_gzip_extraction_streams_and_enforces_limit(tmp_path: Path) -> None:
    raw = b"From sender@example.test Sat Jan  1 00:00:00 2000\nSubject: gzip\n\nbody\n"
    archive_path = tmp_path / "month.mbox.gz"
    with gzip.open(archive_path, "wb") as archive:
        archive.write(raw)

    extracted = tmp_path / "extracted"
    extracted.mkdir()
    extract_gzip(archive_path, extracted, 1024)
    assert (extracted / "month.mbox").read_bytes() == raw
    with pytest.raises(ValueError, match="configured limit"):
        extract_gzip(archive_path, tmp_path, len(raw) - 1)


def test_public_inbox_exports_message_blobs_without_git_metadata(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.email", "test@example.test"], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test"], check=True)
    raw = b"From: author@example.test\nTo: list@example.test\nSubject: docs\n\npatch\n"
    (repository / "message").write_bytes(raw)
    (repository / "not-a-message").write_text("ordinary git blob\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "fixture"], check=True)
    mirror = tmp_path / "archive.git"
    subprocess.run(["git", "clone", "-q", "--mirror", str(repository), str(mirror)], check=True)
    layout = DataLayout(root=tmp_path / "data")

    assert prepare(dataset("public-inbox-test", "public-inbox"), [mirror], layout) == 1
    assert next(layout.prepared("public-inbox-test").iterdir()).read_bytes() == raw
