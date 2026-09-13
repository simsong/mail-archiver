# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""macOS delivery: optional credentials, unambiguous artifact names, and secret safety."""

import base64
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from pydantic import SecretStr
from yaml import safe_load

from scripts.macos_signing import (
    CERTIFICATE_SECRET, GITHUB_ACTIONS, PASSWORD_SECRET, RUNNER_ENVIRONMENT, SigningSecrets, developer_identity,
    dmg_filename, security_command, sign_image, signing_identity,
)

FINGERPRINT = "0123456789ABCDEF0123456789ABCDEF01234567"
IDENTITY_LINE = f'  1) {FINGERPRINT} "Developer ID Application: Fixture (ABCDEFGHIJ)"'
JOBS, STEPS, NEEDS, WITH, REF, ENV, USES, RUN = "jobs", "steps", "needs", "with", "ref", "env", "uses", "run"
ASSEMBLE, MACOS, PATH, NAME = "assemble", "macos", "path", "name"
RUNS_ON, CONDITION = "runs-on", "if"
RELEASE_TAG, RELEASE_KEYS = "RELEASE_TAG", "RELEASE_SIGNING_PUBLIC_KEYS"
GNUPGHOME = "GNUPGHOME"
TRUST_STEP = "Verify trusted tag signer"


@pytest.mark.parametrize("certificate,password", [("", ""), ("encoded", ""), ("", "password"), (" \n", "password")])
def test_incomplete_secrets_warn_and_build_unsigned(tmp_path: Path, capsys, certificate: str, password: str) -> None:
    """Missing either secret must not decode/import anything or prevent unsigned output."""
    credentials = SigningSecrets.from_environment({CERTIFICATE_SECRET: certificate, PASSWORD_SECRET: password})
    work = tmp_path / "must-not-be-created"
    with signing_identity(credentials, work) as identity:
        assert identity == "-"
        assert dmg_filename("1.2.3", "arm64", identity) == "Email-Collection-Toolkit-1.2.3-arm64_UNSIGNED.dmg"
        # Unsigned container signing must not even try to access this absent file.
        sign_image(tmp_path / "absent.dmg", identity)
    assert not work.exists()
    warning = capsys.readouterr().out
    assert "::warning::" in warning and "_UNSIGNED.dmg" in warning
    assert "password" not in warning


def test_supplied_corrupt_certificate_fails_without_exposing_secrets(tmp_path: Path) -> None:
    """Configured invalid signing data must fail, not silently become an unsigned release."""
    credentials = SigningSecrets.from_environment({
        CERTIFICATE_SECRET: "PRIVATE-INVALID-CERT!", PASSWORD_SECRET: "private-password",
        GITHUB_ACTIONS: "true", RUNNER_ENVIRONMENT: "github-hosted"})
    assert credentials.available
    with pytest.raises(ValueError, match="not valid Base64") as caught:
        with signing_identity(credentials, tmp_path / "unused"):
            pytest.fail("invalid credentials yielded an identity")
    assert "PRIVATE-INVALID-CERT" not in str(caught.value)
    assert "private-password" not in repr(credentials)
    assert not (tmp_path / "unused").exists()


def test_wrapped_base64_preserves_exported_bytes() -> None:
    """Clipboard wrapping must not corrupt the binary PKCS#12 export."""
    original = bytes(range(256))
    encoded = base64.encodebytes(original).decode("ascii")
    credentials = SigningSecrets(certificate=SecretStr(encoded), password=SecretStr("p12-password"))
    assert credentials.available and credentials.decode() == original


@pytest.mark.parametrize("output", ["0 valid identities found", IDENTITY_LINE + " (CSSMERR_TP_CERT_EXPIRED)",
                                   IDENTITY_LINE + "\n" + IDENTITY_LINE,
                                   IDENTITY_LINE.replace("Developer ID Application", "Apple Distribution")])
def test_reject_unusable_wrong_or_ambiguous_identity(output: str) -> None:
    """Release signing must use one usable Developer ID Application private key."""
    with pytest.raises(ValueError, match="exactly one valid Developer ID Application"):
        developer_identity(output)


def test_valid_identity_and_existing_keychain_override(tmp_path: Path, capsys) -> None:
    """A valid imported fingerprint and explicit local identity retain signed naming."""
    fingerprint = developer_identity(IDENTITY_LINE + "\n     1 valid identities found")
    assert fingerprint == FINGERPRINT
    with signing_identity(SigningSecrets(), tmp_path / "unused", fingerprint) as identity:
        assert dmg_filename("1.2.3", "x86_64", identity) == "Email-Collection-Toolkit-1.2.3-x86_64.dmg"
    assert not (tmp_path / "unused").exists()
    assert not capsys.readouterr().out


def test_explicit_unsigned_overrides_complete_secrets(tmp_path: Path, capsys) -> None:
    """A deliberate local unsigned build must not import even fully configured secrets."""
    credentials = SigningSecrets(certificate=SecretStr("invalid"), password=SecretStr("secret"))
    with signing_identity(credentials, tmp_path / "unused", "-") as identity:
        assert identity == "-"
    assert not (tmp_path / "unused").exists()
    warning = capsys.readouterr().out
    assert "::warning::" in warning and "--signing-identity -" in warning
    assert "required" not in warning and "secret" not in warning


@pytest.mark.parametrize("actions,runner", [("", ""), ("true", "self-hosted"), ("", "github-hosted")])
def test_automatic_import_rejects_nonisolated_runners(tmp_path: Path, actions: str, runner: str) -> None:
    """macOS delivery: reject local/shared imports before decoding or creating private files."""
    credentials = SigningSecrets.from_environment({CERTIFICATE_SECRET: "invalid", PASSWORD_SECRET: "secret",
                                                   GITHUB_ACTIONS: actions, RUNNER_ENVIRONMENT: runner})
    work = tmp_path / "unused"
    with pytest.raises(RuntimeError, match="isolated GitHub-hosted runner"):
        with signing_identity(credentials, work):
            pytest.fail("nonisolated import accepted")
    assert not work.exists()


@pytest.mark.skipif(sys.platform != "darwin" or os.environ.get(RUNNER_ENVIRONMENT) != "github-hosted",
                    reason="Keychain lifecycle integration runs only on an isolated macOS Actions runner")
def test_failed_import_restores_keychains_and_removes_temporary_files(tmp_path: Path) -> None:
    """A real PKCS#12 import failure must roll back keychain changes without any mocks."""
    before = security_command("list-keychains", "-d", "user")
    credentials = SigningSecrets.from_environment({**os.environ,
        CERTIFICATE_SECRET: base64.b64encode(b"not a PKCS12 file").decode(),
        PASSWORD_SECRET: "fixture-only-password"})
    with pytest.raises(RuntimeError, match="operation failed: import"):
        with signing_identity(credentials, tmp_path):
            pytest.fail("invalid PKCS12 file was accepted")
    assert security_command("list-keychains", "-d", "user") == before
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(sys.platform != "darwin", reason="Apple security tool is macOS-only")
def test_keychain_errors_do_not_include_password_arguments() -> None:
    """Even real tool failures must not put secret-bearing command lines in tracebacks."""
    with pytest.raises(RuntimeError) as caught:
        security_command("invalid-fixture-command", "private-fixture-password")
    assert "private-fixture-password" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_release_waits_for_exact_dmg_before_checksumming() -> None:
    """Release delivery must include the tested artifact and keep signing secrets scoped."""
    workflow = safe_load((Path(__file__).parents[1] / ".github/workflows/release.yml").read_text())
    jobs = workflow[JOBS]
    assembly, macos = jobs[ASSEMBLE], jobs[MACOS]
    assert assembly[NEEDS] == MACOS
    assert assembly[STEPS][0][WITH][REF] == "${{ needs.macos.outputs.commit }}"
    steps = assembly[STEPS]
    download = next(i for i, step in enumerate(steps) if "actions/download-artifact@" in step.get(USES, ""))
    checksum = next(i for i, step in enumerate(steps) if "sha256sum" in step.get(RUN, ""))
    assert download < checksum
    assert steps[download][WITH][NAME] == "macos-dmg"
    assert "*.dmg" in steps[checksum][RUN]
    secret_steps = [step for step in macos[STEPS] if CERTIFICATE_SECRET in step.get(ENV, {})]
    assert len(secret_steps) == 1 and secret_steps[0][RUN] == "make dmg"
    assert PASSWORD_SECRET in secret_steps[0][ENV]
    assert macos[RUNS_ON] == "macos-15"
    assert secret_steps[0][CONDITION] == "${{ runner.environment == 'github-hosted' }}"
    trust = next(step for step in macos[STEPS] if step[NAME] == TRUST_STEP)
    assert trust[ENV][RELEASE_KEYS] == "${{ vars.RELEASE_SIGNING_PUBLIC_KEYS }}"
    assert macos[STEPS].index(trust) < next(i for i, step in enumerate(macos[STEPS])
                                          if step.get(RUN, "").startswith("make "))
    uploads = [step[WITH][PATH] for step in macos[STEPS] if "actions/upload-artifact@" in step.get(USES, "")]
    assert uploads == ["dist/*.dmg", "dist/*.json"]


def test_release_signer_allowlist_checks_real_signatures(tmp_path: Path) -> None:
    """Release trust: accept an allowed signer, reject another valid signer and missing keys."""
    # Keep gpg-agent's Unix socket path below macOS's limit (pytest paths are long).
    keyring = tempfile.TemporaryDirectory(prefix="release-keys-")
    environment = {**os.environ, GNUPGHOME: keyring.name}

    def run(*args: str) -> str:
        result = subprocess.run(args, cwd=tmp_path, env=environment, check=False,
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return result.stdout

    try:
        for signer in ("trusted@example.invalid", "untrusted@example.invalid"):
            run("gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase", "",
                "--quick-generate-key", signer, "ed25519", "sign", "0")
        public_key = run("gpg", "--armor", "--export", "<trusted@example.invalid>")
        run("git", "init")
        run("git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "fixture")
        for name, signer in (("trusted", "trusted@example.invalid"), ("untrusted", "untrusted@example.invalid")):
            run("git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                "-c", "gpg.format=openpgp", "-c", "gpg.program=gpg", "tag", "-s", "-u", f"<{signer}>",
                name, "-m", "fixture")
        run("git", "-c", "tag.gpgsign=false", "tag", "lightweight")
        workflow = safe_load((Path(__file__).parents[1] / ".github/workflows/release.yml").read_text())
        command = next(step[RUN] for step in workflow[JOBS][MACOS][STEPS] if step[NAME] == TRUST_STEP)
        for tag, keys, accepted in (("trusted", public_key, True), ("untrusted", public_key, False),
                                    ("trusted", "", False), ("trusted", "invalid key", False),
                                    ("lightweight", public_key, False)):
            result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", command], cwd=tmp_path,
                                    env={**environment, RELEASE_TAG: tag, RELEASE_KEYS: keys},
                                    capture_output=True, text=True, check=False)
            assert (result.returncode == 0) == accepted, result.stderr
    finally:
        try:
            run("gpgconf", "--kill", "gpg-agent")
        finally:
            keyring.cleanup()
