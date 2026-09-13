# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Build a self-contained app and DMG, optionally Developer ID signed, and test it mounted."""

from __future__ import annotations

import argparse
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from importlib.metadata import distribution, version
from pathlib import Path

from mailarchiver.self_test import SelfTestReport
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from dmg_layout import create_image, verify_layout
from macos_signing import CERTIFICATE_SECRET, PASSWORD_SECRET, SigningSecrets, dmg_filename, sign_image, signing_identity

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "Email Collection Toolkit"
IDENTIFIER = "net.simson.mailarchiver"
PLIST_DOCUMENT_TYPES = "CFBundleDocumentTypes"
PLIST_EXPORTED_TYPES = "UTExportedTypeDeclarations"
PLIST_SHORT_VERSION = "CFBundleShortVersionString"
PLIST_COPYRIGHT = "NSHumanReadableCopyright"
PLIST_TYPE_NAME = "CFBundleTypeName"
PLIST_TYPE_ROLE = "CFBundleTypeRole"
PLIST_HANDLER_RANK = "LSHandlerRank"
PLIST_CONTENT_TYPES = "LSItemContentTypes"
PLIST_IS_PACKAGE = "LSTypeIsPackage"
PLIST_UTI = "UTTypeIdentifier"
PLIST_DESCRIPTION = "UTTypeDescription"
PLIST_CONFORMS = "UTTypeConformsTo"
PLIST_TAGS = "UTTypeTagSpecification"
PLIST_EXTENSION = "public.filename-extension"
COPYRIGHT = "Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved."
MACHO_MAGIC = {bytes.fromhex(value) for value in ("feedface", "cefaedfe", "feedfacf", "cffaedfe", "cafebabe", "bebafeca", "cafebabf", "bfbafeca")}


def run(*arguments: str | Path, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run([str(argument) for argument in arguments], check=True, **kwargs)


def icon(work: Path) -> Path:
    iconset = work / "MailArchiver.iconset"
    iconset.mkdir()
    source = ROOT / "gui/icons/rainbow-post-192.png"
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            pixels = str(size * scale)
            suffix = "@2x" if scale == 2 else ""
            run("/usr/bin/sips", "-z", pixels, pixels, source, "--out",
                iconset / f"icon_{size}x{size}{suffix}.png", stdout=subprocess.DEVNULL)
    result = work / "MailArchiver.icns"
    run("/usr/bin/iconutil", "-c", "icns", "-o", result, iconset)
    return result


def configure_bundle(app: Path, signing_identity: str) -> None:
    plist = app / "Contents/Info.plist"
    with plist.open("rb") as handle:
        info = plistlib.load(handle)
    info[PLIST_SHORT_VERSION] = version("mailarchiver")
    info[PLIST_COPYRIGHT] = COPYRIGHT
    info[PLIST_DOCUMENT_TYPES] = [{
        PLIST_TYPE_NAME: "Mail Archive", PLIST_TYPE_ROLE: "Editor",
        PLIST_HANDLER_RANK: "Owner", PLIST_CONTENT_TYPES: [IDENTIFIER + ".archive"],
        PLIST_IS_PACKAGE: True,
    }]
    info[PLIST_EXPORTED_TYPES] = [{
        PLIST_UTI: IDENTIFIER + ".archive", PLIST_DESCRIPTION: "Mail Archive",
        PLIST_CONFORMS: ["com.apple.package"],
        PLIST_TAGS: {PLIST_EXTENSION: ["mailarchive"]},
    }]
    with plist.open("wb") as handle:
        plistlib.dump(info, handle)
    # PyInstaller signs every nested binary; refresh the outer seal after metadata changes.
    options = [] if signing_identity == "-" else ["--options", "runtime", "--timestamp"]
    run("/usr/bin/codesign", "--force", "--sign", signing_identity, *options, app)
    run("/usr/bin/codesign", "--verify", "--deep", "--strict", app)


@contextmanager
def mounted_image(dmg: Path):
    """Never recursively clean a directory that might still contain a mounted volume."""
    temporary = Path(tempfile.mkdtemp(prefix="mailarchiver-dmg-test-"))
    mount = temporary / "mounted"
    mount.mkdir()
    try:
        run("/usr/bin/hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", mount, dmg)
        try:
            yield mount
        finally:
            # WebKit helpers can retain a framework briefly after the GUI process exits.
            result: subprocess.CompletedProcess[str] | None = None
            for _ in range(10):
                result = subprocess.run(["/usr/bin/hdiutil", "detach", str(mount)],
                                        capture_output=True, text=True, check=False)
                if result.returncode == 0:
                    break
                time.sleep(1)
            else:
                assert result is not None
                raise RuntimeError(f"Could not eject test volume {mount}: {result.stderr}")
    finally:
        # rmdir is nonrecursive and fails safely on an occupied mount or unexpected file.
        if not os.path.ismount(mount):
            try:
                mount.rmdir()
                temporary.rmdir()
            except OSError:
                pass


def test_image(dmg: Path) -> None:
    """Mount read-only and test the actual bundle outside the source checkout."""
    with mounted_image(dmg) as mount:
        app = mount / f"{APP_NAME}.app"
        run("/usr/bin/codesign", "--verify", "--deep", "--strict", app)
        if not (mount / "Applications").is_symlink() or os.readlink(mount / "Applications") != "/Applications":
            raise RuntimeError("DMG is missing its Applications shortcut")
        verify_dependencies(app)
        verify_layout(mount, app.name)
        executable = app / "Contents/MacOS" / APP_NAME
        environment = {key: value for key, value in os.environ.items()
                       if key not in (CERTIFICATE_SECRET, PASSWORD_SECRET)
                       and not key.startswith(("PYTHON", "DYLD_", "MAILARCHIVER", "MAIL_ARCHIVE"))}
        environment["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        for mode in ("self-test", "self-test-gui"):
            report_path = dmg.with_suffix(f".{mode}.json")
            run(executable, f"--{mode}", "--report", report_path,
                cwd=mount.parent, env=environment, timeout=150)
            report = SelfTestReport.model_validate_json(report_path.read_text(encoding="utf-8"))
            if not report.passed or not report.frozen:
                raise RuntimeError(f"mounted {mode} failed: {report}")


def load_rpaths(commands: str) -> tuple[str, ...]:
    """Read LC_RPATH values from otool load-command output, preserving spaces."""
    paths = []
    in_rpath = False
    for line in commands.splitlines():
        value = line.strip()
        if value.startswith("cmd "):
            in_rpath = value == "cmd LC_RPATH"
        elif in_rpath and value.startswith("path "):
            paths.append(value[5:].rsplit(" (offset ", 1)[0])
            in_rpath = False
    return tuple(paths)


def load_dependencies(commands: str) -> tuple[str, ...]:
    """Read actual dylib load commands; LC_ID_DYLIB describes the binary itself."""
    paths = []
    dependency = False
    for line in commands.splitlines():
        value = line.strip()
        if value.startswith("cmd "):
            dependency = value in {
                "cmd LC_LOAD_DYLIB", "cmd LC_LOAD_WEAK_DYLIB", "cmd LC_REEXPORT_DYLIB",
                "cmd LC_LAZY_LOAD_DYLIB", "cmd LC_LOAD_UPWARD_DYLIB",
            }
        elif dependency and value.startswith("name "):
            paths.append(value[5:].rsplit(" (offset ", 1)[0])
            dependency = False
    return tuple(paths)


def bundle_loader_path(app: Path, binary: Path, value: str) -> Path:
    """Expand loader paths and reject build-machine or escaping search paths."""
    if value == "@loader_path" or value.startswith("@loader_path/"):
        target = binary.parent / value.removeprefix("@loader_path").lstrip("/")
    elif value == "@executable_path" or value.startswith("@executable_path/"):
        target = app / "Contents/MacOS" / value.removeprefix("@executable_path").lstrip("/")
    else:
        target = Path(value)
        if not target.is_absolute():
            raise RuntimeError(f"unsupported binary search path: {binary}: {value}")
    resolved = target.resolve()
    if not resolved.is_relative_to(app.resolve()):
        raise RuntimeError(f"nonportable binary search path: {binary}: {value}")
    return resolved


def verify_dependencies(app: Path) -> None:
    """Reject accidental Homebrew/build-machine linkage in every bundled Mach-O file."""
    checked: set[Path] = set()
    rpaths: set[Path] = set()
    for candidate in app.rglob("*"):
        if not candidate.is_file():
            continue
        path = candidate.resolve()
        if not path.is_relative_to(app.resolve()):
            raise RuntimeError(f"bundle file links outside app: {candidate}")
        if path in checked:
            continue
        with path.open("rb") as handle:
            if handle.read(4) not in MACHO_MAGIC:
                continue
        checked.add(path)
        commands = run("/usr/bin/otool", "-l", path, capture_output=True, text=True).stdout
        rpaths.update(bundle_loader_path(app, path, value) for value in load_rpaths(commands))
    for path in checked:
        commands = run("/usr/bin/otool", "-l", path, capture_output=True, text=True).stdout
        for dependency in load_dependencies(commands):
            if dependency.startswith(("/usr/lib/", "/System/Library/")):
                continue
            if dependency.startswith("@rpath/"):
                candidates = [(root / dependency.removeprefix("@rpath/")).resolve() for root in rpaths]
                if not any(item.is_relative_to(app.resolve()) and item.is_file() for item in candidates):
                    raise RuntimeError(f"unresolved bundled dependency: {path}: {dependency}")
            else:
                target = bundle_loader_path(app, path, dependency)
                if not target.is_file():
                    raise RuntimeError(f"missing bundled dependency: {path}: {dependency}")
    if not checked:
        raise RuntimeError("no native executable found inside app")
    print(f"Verified {len(checked)} bundled Mach-O files: no external non-system library paths")


def build(signing_identity: str) -> Path:
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    work_root = ROOT / ".tmp"
    work_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dmg-build-", dir=work_root) as temporary:
        work = Path(temporary)
        bundle_output = work / "bundle"
        # Collect runtime notices, not development-only tools such as Pylint.
        notices = work / "Third Party Notices"
        notices.mkdir()
        pending = ["mailarchiver", "pyobjc-framework-Cocoa", "pyobjc-framework-WebKit"]
        visited = set()
        while pending:
            name = canonicalize_name(pending.pop())
            if name in visited:
                continue
            visited.add(name)
            package = distribution(name)
            for requirement_text in package.requires or ():
                requirement = Requirement(requirement_text)
                if requirement.marker is None or requirement.marker.evaluate():
                    pending.append(requirement.name)
            for entry in package.files or ():
                if ".dist-info/" in str(entry) and any(word in str(entry).lower() for word in ("license", "copying", "notice")):
                    target = notices / str(entry)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(str(package.locate_file(entry)), target)
        app_icon = icon(work)
        command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--windowed", "--onedir",
                   "--name", APP_NAME, "--osx-bundle-identifier", IDENTIFIER,
                   "--target-arch", platform.machine(), "--codesign-identity", signing_identity,
                   "--icon", str(app_icon), "--distpath", str(bundle_output),
                   "--workpath", str(work / "work"), "--specpath", str(work),
                   "--copy-metadata", "mailarchiver", "--collect-data", "mailarchiver",
                   "--collect-data", "webview", "--hidden-import", "webview.platforms.cocoa",
                   "--hidden-import", "mailarchiver.sources", "--hidden-import", "mailarchiver.source_stubs",
                   "--add-data", f"{ROOT / 'gui'}:gui",
                   "--add-data", f"{ROOT / 'src/mailarchiver/standalone_verify.py'}:mailarchiver",
                   "--add-data", f"{notices}:Third Party Notices",
                   str(ROOT / "scripts/desktop_entry.py")]
        environment = {key: value for key, value in os.environ.items()
                       if key not in (CERTIFICATE_SECRET, PASSWORD_SECRET) and not key.startswith("PYTHON")}
        run(*command, cwd=ROOT, env=environment)
        app = bundle_output / f"{APP_NAME}.app"
        configure_bundle(app, signing_identity)
        dmg = output / dmg_filename(version("mailarchiver"), platform.machine(), signing_identity)
        candidate = work / "candidate.dmg"
        create_image(app, app_icon, candidate, work)
        # Keep a previous artifact until both mounted tests have passed.
        test_image(candidate)
        sign_image(candidate, signing_identity)
        candidate.replace(dmg)
        for mode in ("self-test", "self-test-gui"):
            candidate.with_suffix(f".{mode}.json").replace(dmg.with_suffix(f".{mode}.json"))
        return dmg


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-dmg", type=Path, help="mount and retest an existing DMG")
    parser.add_argument("--preview-dmg", type=Path, help="open the mounted installer in Finder until Return is pressed")
    parser.add_argument("--signing-identity", help="existing Keychain identity; otherwise import optional signing secrets; '-' forces unsigned")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("DMG builds and native tests require macOS")
    if args.preview_dmg:
        with mounted_image(args.preview_dmg.resolve(strict=True)) as mount:
            run("/usr/bin/open", mount)
            input("Inspect the installer in Finder; press Return to eject: ")
    elif args.test_dmg:
        test_image(args.test_dmg.resolve(strict=True))
    else:
        credentials = SigningSecrets.from_environment(os.environ)
        with signing_identity(credentials, ROOT / ".tmp", args.signing_identity) as identity:
            print(f"Built and tested: {build(identity)}")


if __name__ == "__main__":
    main()
