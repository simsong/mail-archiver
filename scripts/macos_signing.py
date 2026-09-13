# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Optional Developer ID signing with a short-lived imported identity."""

from __future__ import annotations

import base64
import binascii
import os
import re
import secrets
import shlex
import subprocess
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel, SecretStr

CERTIFICATE_SECRET = "APPLE_CERTIFICATE_P12_BASE64"
PASSWORD_SECRET = "APPLE_CERTIFICATE_PASSWORD"
GITHUB_ACTIONS = "GITHUB_ACTIONS"
RUNNER_ENVIRONMENT = "RUNNER_ENVIRONMENT"
EXPLICIT_UNSIGNED_WARNING = (
    "::warning::Developer ID signing disabled by --signing-identity -. "
    "Producing _UNSIGNED.dmg with an ad-hoc-signed app; this build is not notarized."
)
UNSIGNED_WARNING = (
    "::warning::Developer ID signing skipped: both APPLE_CERTIFICATE_P12_BASE64 "
    "and APPLE_CERTIFICATE_PASSWORD are required. Producing _UNSIGNED.dmg "
    "with an ad-hoc-signed app; this build is not notarized."
)


class SigningSecrets(BaseModel):
    """GitHub environment boundary; secret values must never appear in reprs."""

    certificate: SecretStr = SecretStr("")
    password: SecretStr = SecretStr("")
    hosted_runner: bool = False

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> SigningSecrets:
        return cls(certificate=SecretStr(environment.get(CERTIFICATE_SECRET, "")),
                   password=SecretStr(environment.get(PASSWORD_SECRET, "")),
                   hosted_runner=(environment.get(GITHUB_ACTIONS) == "true"
                                  and environment.get(RUNNER_ENVIRONMENT) == "github-hosted"))

    @property
    def available(self) -> bool:
        return bool(self.certificate.get_secret_value().strip() and self.password.get_secret_value())

    def decode(self) -> bytes:
        """Accept wrapped Base64 while rejecting malformed configured credentials."""
        try:
            encoded = "".join(self.certificate.get_secret_value().split())
            decoded = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("Signing certificate secret is not valid Base64") from None
        if not decoded:
            raise ValueError("Signing certificate secret is empty")
        return decoded


def developer_identity(output: str) -> str:
    """Select exactly one valid Developer ID Application key from the imported file."""
    identities = re.findall(r'^\s*\d+\) ([0-9A-Fa-f]{40}) "Developer ID Application: [^"\n]+"\s*$',
                            output, re.MULTILINE)
    if len(identities) != 1:
        raise ValueError("PKCS#12 must contain exactly one valid Developer ID Application signing identity")
    return identities[0]


def security_command(*arguments: str) -> str:
    """Omit argv/output from exceptions; argv remains visible to local processes."""
    try:
        result = subprocess.run(["/usr/bin/security", *arguments], check=False,
                                capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("Apple signing keychain operation could not complete") from None
    if result.returncode:
        raise RuntimeError(f"Apple signing keychain operation failed: {arguments[0]}")
    return result.stdout


@contextmanager
def signing_identity(credentials: SigningSecrets, work_root: Path,
                     explicit: str | None = None) -> Iterator[str]:
    """Restore the user's keychain search list and remove the imported key on exit."""
    if explicit is not None:
        if explicit == "-":
            print(EXPLICIT_UNSIGNED_WARNING, flush=True)
        yield explicit
        return
    if not credentials.available:
        print(UNSIGNED_WARNING, flush=True)
        yield "-"
        return
    if not credentials.hosted_runner:
        raise RuntimeError("Automatic PKCS#12 import requires an isolated GitHub-hosted runner; "
                           "use --signing-identity with an existing local keychain identity")
    certificate_bytes = credentials.decode()
    work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="signing-", dir=work_root) as temporary:
        certificate = Path(temporary) / "identity.p12"
        keychain = str(Path(temporary) / "signing.keychain-db")
        password = secrets.token_urlsafe(32)
        original = shlex.split(security_command("list-keychains", "-d", "user"))
        try:
            with certificate.open("xb") as handle:
                os.chmod(certificate, 0o600)
                handle.write(certificate_bytes)
            security_command("create-keychain", "-p", password, keychain)
            security_command("set-keychain-settings", "-lut", "21600", keychain)
            security_command("unlock-keychain", "-p", password, keychain)
            security_command("import", str(certificate), "-P", credentials.password.get_secret_value(),
                             "-k", keychain, "-T", "/usr/bin/codesign")
            certificate.unlink()
            security_command("set-key-partition-list", "-S", "apple-tool:,apple:,codesign:",
                             "-s", "-k", password, keychain)
            identity = developer_identity(security_command("find-identity", "-v", "-p", "codesigning", keychain))
            security_command("list-keychains", "-d", "user", "-s", keychain, *original)
            yield identity
        finally:
            try:
                security_command("list-keychains", "-d", "user", "-s", *original)
            finally:
                if Path(keychain).exists():
                    security_command("delete-keychain", keychain)


def dmg_filename(version: str, architecture: str, identity: str) -> str:
    """Unsigned artifacts must be identifiable independently of Actions logs."""
    suffix = "_UNSIGNED" if identity == "-" else ""
    return f"Email-Collection-Toolkit-{version}-{architecture}{suffix}.dmg"


def sign_image(image: Path, identity: str) -> None:
    """Sign and verify the finished container before publishing the candidate."""
    if identity != "-":
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", identity, "--timestamp", str(image)], check=True)
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(image)], check=True)
