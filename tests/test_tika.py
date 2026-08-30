"""Requirement: Tika 4 installs a verified CLI JAR with its lib directory."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from mailarchiver.tika import install


def write_tika_bundle(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("tika-app-4.0.0.jar", b"test jar")
        bundle.writestr("lib/tika-core.jar", b"test library")


def test_install_verifies_and_preserves_tika_4_layout(tmp_path: Path) -> None:
    archive = tmp_path / "tika-app-4.0.0.zip"
    write_tika_bundle(archive)
    checksum = tmp_path / "tika-app-4.0.0.zip.sha512"
    checksum.write_text(f"{hashlib.sha512(archive.read_bytes()).hexdigest()}  {archive.name}\n")

    jar = install(archive, checksum, tmp_path / "tika", "4.0.0")

    assert jar.read_bytes() == b"test jar"
    assert (jar.parent / "lib/tika-core.jar").read_bytes() == b"test library"


def test_install_rejects_bad_checksum(tmp_path: Path) -> None:
    archive = tmp_path / "tika-app-4.0.0.zip"
    write_tika_bundle(archive)
    checksum = tmp_path / "tika-app-4.0.0.zip.sha512"
    checksum.write_text("0" * 128 + "\n")

    with pytest.raises(ValueError, match="SHA-512"):
        install(archive, checksum, tmp_path / "tika", "4.0.0")

    assert not (tmp_path / "tika").exists()
