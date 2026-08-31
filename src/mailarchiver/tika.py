"""Verified installation of the optional Apache Tika command-line layout."""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import zipfile
from pathlib import Path, PurePosixPath

from pydantic import BaseModel


SHA512_PATTERN = re.compile(r"[0-9a-fA-F]{128}\Z")


class TikaDistribution(BaseModel):
    version: str

    @property
    def jar_name(self) -> str:
        return f"tika-app-{self.version}.jar"


def checksum_value(path: Path) -> str:
    values = path.read_text(encoding="ascii").split(maxsplit=1)
    if not values:
        raise ValueError(f"empty checksum file: {path}")
    value = values[0]
    if SHA512_PATTERN.fullmatch(value) is None:
        raise ValueError(f"invalid SHA-512 checksum in {path}")
    return value.lower()


def validate_members(names: list[str], distribution: TikaDistribution) -> None:
    for name in names:
        member = PurePosixPath(name)
        if member.is_absolute() or ".." in member.parts:
            raise ValueError(f"unsafe archive member: {name}")
    if distribution.jar_name not in names:
        raise ValueError(f"missing {distribution.jar_name}")
    if not any(name.startswith("lib/") and not name.endswith("/") for name in names):
        raise ValueError("missing Tika lib directory")


def install(archive: Path, checksum: Path, destination: Path, version: str) -> Path:
    expected = checksum_value(checksum)
    with archive.open("rb") as source:
        actual = hashlib.file_digest(source, "sha512").hexdigest()
    if actual != expected:
        raise ValueError("Tika SHA-512 verification failed")
    if destination.exists():
        raise FileExistsError(f"Tika is already installed at {destination}")
    distribution = TikaDistribution(version=version)
    temporary = destination.with_name(f".{destination.name}.tmp")
    if temporary.exists():
        raise FileExistsError(f"temporary Tika directory already exists: {temporary}")
    try:
        with zipfile.ZipFile(archive) as bundle:
            names = bundle.namelist()
            validate_members(names, distribution)
            bundle.extractall(temporary)
        if not (temporary / distribution.jar_name).is_file() or not (temporary / "lib").is_dir():
            raise ValueError("incomplete Tika application layout")
        temporary.rename(destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination / distribution.jar_name


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--checksum", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    print(install(args.archive, args.checksum, args.destination, args.version))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
