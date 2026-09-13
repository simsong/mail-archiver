#!/usr/bin/env python3
# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Audit and optionally collect licenses for the installed runtime closure."""

from __future__ import annotations

import argparse
import json
import platform
import re
import shutil
from importlib.metadata import Distribution, PackageNotFoundError, distribution
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from pydantic import BaseModel, ConfigDict


APPLICATION_DISTRIBUTION = "mailarchiver"
METADATA_CLASSIFIER = "Classifier"
METADATA_LICENSE = "License"
METADATA_LICENSE_EXPRESSION = "License-Expression"
METADATA_NAME = "Name"
METADATA_VERSION = "Version"
DEV_ONLY_DISTRIBUTIONS = frozenset({"astroid", "playwright", "pylint", "pytest", "pytest-playwright"})
LICENSE_BASENAMES = ("copying", "copyright", "license", "notice")
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
NOTICE_FILES = ("COPYRIGHT", "THIRD_PARTY_NOTICES.md")
PROXY_TOOLS_LICENSE = REPOSITORY_ROOT / "licenses/proxy_tools-BSD.txt"


class LicenseRecord(BaseModel):
    """One installed distribution in the application's runtime closure."""

    model_config = ConfigDict(frozen=True)

    name: str
    version: str
    license: str
    license_files: tuple[str, ...]
    license_fallback: str | None = None


class LicenseInventory(BaseModel):
    """Serializable audit result for one platform-specific build environment."""

    model_config = ConfigDict(frozen=True)

    application_version: str
    platform: str
    python_version: str
    distributions: tuple[LicenseRecord, ...]


def license_label(item: Distribution) -> str:
    """Return the strongest machine-readable license label in package metadata."""
    if canonicalize_name(item.metadata[METADATA_NAME]) == "proxy-tools":
        return "BSD (reviewed upstream; wheel metadata incorrectly says MIT)"
    expression = item.metadata.get(METADATA_LICENSE_EXPRESSION, "").strip()
    if expression:
        return expression
    classifiers = tuple(
        value.removeprefix("License :: ")
        for value in item.metadata.get_all(METADATA_CLASSIFIER, ())
        if value.startswith("License :: ")
    )
    if classifiers:
        return "; ".join(classifiers)
    value = item.metadata.get(METADATA_LICENSE, "").strip()
    return value.splitlines()[0].strip() if value else "UNKNOWN"


def license_files(item: Distribution) -> tuple[str, ...]:
    """Find complete license and notice files shipped in an installed wheel."""
    files: list[str] = []
    for path in item.files or ():
        value = str(path)
        parts = Path(value).parts
        if not Path(value).name.lower().startswith(LICENSE_BASENAMES):
            continue
        if any(part.endswith(".dist-info") for part in parts[:-1]) or "licenses" in parts:
            files.append(value)
    return tuple(sorted(files))


def runtime_distributions() -> tuple[Distribution, ...]:
    """Resolve only dependencies reachable from the installed application."""
    root = distribution(APPLICATION_DISTRIBUTION)
    pending = [Requirement(value) for value in root.requires or ()]
    found: dict[str, Distribution] = {}
    while pending:
        requirement = pending.pop()
        if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
            continue
        name = canonicalize_name(requirement.name)
        if name in found:
            continue
        try:
            item = distribution(name)
        except PackageNotFoundError as error:
            raise RuntimeError(f"required runtime distribution is not installed: {name}") from error
        found[name] = item
        pending.extend(Requirement(value) for value in item.requires or ())
    return tuple(found[name] for name in sorted(found))


def inventory() -> LicenseInventory:
    """Build the typed inventory for the current platform and environment."""
    installed = runtime_distributions()
    pyobjc_provider = next(
        (
            canonicalize_name(item.metadata[METADATA_NAME])
            for item in installed
            if canonicalize_name(item.metadata[METADATA_NAME]).startswith("pyobjc-")
            and license_files(item)
        ),
        None,
    )
    records = tuple(
        LicenseRecord(
            name=item.metadata[METADATA_NAME],
            version=item.metadata[METADATA_VERSION],
            license=license_label(item),
            license_files=license_files(item),
            license_fallback=(
                "repository:licenses/proxy_tools-BSD.txt"
                if canonicalize_name(item.metadata[METADATA_NAME]) == "proxy-tools"
                else f"shared:{pyobjc_provider}"
                if canonicalize_name(item.metadata[METADATA_NAME]).startswith("pyobjc-")
                and not license_files(item)
                and pyobjc_provider
                else None
            ),
        )
        for item in installed
    )
    return LicenseInventory(
        application_version=distribution(APPLICATION_DISTRIBUTION).version,
        platform=platform.platform(),
        python_version=platform.python_version(),
        distributions=records,
    )


def audit_errors(result: LicenseInventory) -> tuple[str, ...]:
    """Reject missing license evidence, strong copyleft, and dev-only leakage."""
    errors: list[str] = []
    for record in result.distributions:
        normalized_name = canonicalize_name(record.name)
        if normalized_name in DEV_ONLY_DISTRIBUTIONS:
            errors.append(f"development dependency is in the runtime closure: {record.name}")
        if record.license == "UNKNOWN":
            errors.append(f"runtime license is unknown: {record.name}")
        if re.search(r"(?<!L)GPL", record.license.upper()):
            errors.append(f"GPL/AGPL runtime license is not approved: {record.name} ({record.license})")
        if not record.license_files and not record.license_fallback:
            errors.append(f"runtime distribution has no complete license file: {record.name}")
    return tuple(errors)


def write_bundle(result: LicenseInventory, output: Path) -> None:
    """Copy complete runtime license texts and write their exact inventory."""
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"license output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    for filename in NOTICE_FILES:
        shutil.copyfile(REPOSITORY_ROOT / filename, output / filename)
    (output / "runtime-license-inventory.json").write_text(
        json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for record in result.distributions:
        item = distribution(record.name)
        package_dir = output / canonicalize_name(record.name)
        package_dir.mkdir(exist_ok=True)
        for index, filename in enumerate(record.license_files, start=1):
            source = Path(str(item.locate_file(filename)))
            target = package_dir / f"{index:02d}-{source.name}"
            shutil.copyfile(source, target)
    proxy_dir = output / "proxy-tools"
    proxy_dir.mkdir(exist_ok=True)
    shutil.copyfile(PROXY_TOOLS_LICENSE, proxy_dir / PROXY_TOOLS_LICENSE.name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="directory for inventory and complete license texts")
    args = parser.parse_args()
    result = inventory()
    for record in result.distributions:
        print(f"{record.name} {record.version}: {record.license}")
    errors = audit_errors(result)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    if args.output:
        write_bundle(result, args.output)
        print(f"Wrote runtime license bundle to {args.output}")
    print(f"Audited {len(result.distributions)} runtime distributions; no GPL/AGPL dependency found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
