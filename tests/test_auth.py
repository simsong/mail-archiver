# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Requirements: remote authorization is provider-aware, bounded, and credential-safe."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from mailarchiver.auth import (
    GMAIL_READONLY_SCOPE,
    AuthorizerError,
    ClientSecrets,
    DnsEvidence,
    MailProvider,
    MailboxAddress,
    build_parser,
    classify_provider,
    console_steps,
    detect_provider,
    existing_client_secrets,
    gcloud_plan,
    install_client_secrets,
    unavailable_provider_message,
)


def client_document(project_id: str = "mailarchiver-personal-1234abcd") -> str:
    return json.dumps(
        {
            "installed": {
                "client_id": "client.apps.googleusercontent.com",
                "project_id": project_id,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "client_secret": "public-desktop-value",
                "redirect_uris": ["http://localhost"],
            }
        }
    )


@pytest.mark.parametrize(
    ("value", "normalized"),
    [
        ("simsong@gmail.com", "simsong@gmail.com"),
        (" Simsong@BasisTech.COM ", "Simsong@basistech.com"),
    ],
)
def test_mailbox_address_is_normalized(value: str, normalized: str) -> None:
    assert MailboxAddress.parse(value).address == normalized


@pytest.mark.parametrize("value", ["gmail.com", "a@@gmail.com", "a@localhost", "a@bad_domain.test"])
def test_invalid_mailbox_address_is_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        MailboxAddress.parse(value)


def test_google_workspace_mx_is_gmail() -> None:
    detection = classify_provider(
        DnsEvidence(domain="basistech.com", mx_hosts=("smtp.google.com",))
    )

    assert detection.provider is MailProvider.GMAIL
    assert detection.evidence == ("MX smtp.google.com",)


def test_outlook_autodiscover_identifies_m365_behind_mail_gateway() -> None:
    detection = classify_provider(
        DnsEvidence(
            domain="fas.harvard.edu",
            mx_hosts=("mx0a-00171101.pphosted.com",),
            autodiscover_targets=("autodiscover.outlook.com",),
        )
    )

    assert detection.provider is MailProvider.M365
    assert detection.evidence == ("Autodiscover autodiscover.outlook.com",)
    assert unavailable_provider_message(detection.provider) == "Microsoft Office not yet implemented."


def test_gateway_alone_does_not_guess_provider() -> None:
    detection = classify_provider(
        DnsEvidence(domain="example.edu", mx_hosts=("example.mx.proofpoint.com",))
    )

    assert detection.provider is MailProvider.UNKNOWN


def test_gmail_override_does_not_require_dns() -> None:
    detection = detect_provider(MailboxAddress.parse("person@example.test"), force_gmail=True)

    assert detection.provider is MailProvider.GMAIL
    assert detection.evidence == ("--gmail override",)


def test_requested_cli_forms_have_one_account_positional() -> None:
    parser = build_parser()

    automatic = parser.parse_args(["simsong@basistech.com"])
    overridden = parser.parse_args(["--gmail", "simsong@basistech.com"])

    assert automatic.account == "simsong@basistech.com"
    assert automatic.gmail is False
    assert overridden.account == "simsong@basistech.com"
    assert overridden.gmail is True


def test_release_client_is_shared_without_per_user_registration(tmp_path: Path) -> None:
    shared = tmp_path / "installed" / "gmail_client.json"
    shared.parent.mkdir()
    shared.write_text(client_document(), encoding="utf-8")

    selected = existing_client_secrets(
        MailboxAddress.parse("new.user@gmail.com"),
        config_root=tmp_path / "user-config",
        distributed_path=shared,
    )

    assert selected == shared


def test_missing_release_client_does_not_start_registration(tmp_path: Path) -> None:
    selected = existing_client_secrets(
        MailboxAddress.parse("new.user@gmail.com"),
        config_root=tmp_path / "user-config",
        distributed_path=tmp_path / "missing.json",
    )

    assert selected is None


def test_gcloud_plan_is_project_scoped_and_least_privilege() -> None:
    account = MailboxAddress.parse("simsong@gmail.com")
    plan = gcloud_plan(account, "mailarchiver-personal-1234abcd")

    assert plan.login == (
        "auth",
        "login",
        "simsong@gmail.com",
        "--brief",
        "--no-activate",
    )
    assert plan.create_project[:3] == (
        "projects",
        "create",
        "mailarchiver-personal-1234abcd",
    )
    assert plan.enable_gmail == (
        "services",
        "enable",
        "gmail.googleapis.com",
        "--project",
        "mailarchiver-personal-1234abcd",
        "--account",
        "simsong@gmail.com",
    )
    assert "config" not in plan.model_dump_json()
    assert plan.create_project[-2:] == ("--account", "simsong@gmail.com")


def test_console_plan_requests_only_gmail_readonly_and_desktop_client() -> None:
    steps = console_steps(
        MailboxAddress.parse("simsong@gmail.com"), "mailarchiver-personal-1234abcd"
    )

    assert all("project=mailarchiver-personal-1234abcd" in step.url for step in steps)
    assert "your own address" in steps[0].instruction
    assert "seven days" in steps[1].instruction
    assert GMAIL_READONLY_SCOPE in steps[2].instruction
    assert "Desktop app" in steps[3].instruction


def test_client_download_is_validated_and_installed_privately(tmp_path: Path) -> None:
    source = tmp_path / "client_secret.json"
    source.write_text(client_document(), encoding="utf-8")
    account = MailboxAddress.parse("simsong@gmail.com")

    destination = install_client_secrets(
        source,
        account,
        project_id="mailarchiver-personal-1234abcd",
        config_root=tmp_path / "config",
    )

    parsed = ClientSecrets.model_validate_json(destination.read_bytes())
    assert parsed.installed.project_id == "mailarchiver-personal-1234abcd"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert stat.S_IMODE(destination.parent.stat().st_mode) == 0o700


def test_wrong_project_download_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "client_secret.json"
    source.write_text(client_document("other-project"), encoding="utf-8")

    with pytest.raises(AuthorizerError, match="download belongs to project other-project"):
        install_client_secrets(
            source,
            MailboxAddress.parse("simsong@gmail.com"),
            project_id="mailarchiver-personal-1234abcd",
            config_root=tmp_path / "config",
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("client_id", "client.evil.example", "expected a Google OAuth client ID"),
        ("auth_uri", "https://accounts.google.evil.example/oauth", "authorization endpoint"),
        ("token_uri", "https://oauth2.googleapis.evil.example/token", "token endpoint"),
        ("redirect_uris", ["https://evil.example/callback"], "loopback redirect"),
    ],
)
def test_google_client_download_rejects_lookalike_endpoints(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    document = json.loads(client_document())
    document["installed"][field] = value
    source = tmp_path / "client_secret.json"
    source.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(AuthorizerError, match=message):
        install_client_secrets(
            source,
            MailboxAddress.parse("simsong@gmail.com"),
            config_root=tmp_path / "config",
        )


@pytest.mark.parametrize("domain", ["gmail.com", "outlook.com"])
def test_consumer_detection_never_queries_dns(domain: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement: authoritative consumer domains work while DNS is unavailable."""
    from mailarchiver import auth

    def unavailable(_domain: str) -> DnsEvidence:
        raise AssertionError("consumer-domain detection must not query DNS")

    monkeypatch.setattr(auth, "resolve_dns_evidence", unavailable)
    assert detect_provider(MailboxAddress.parse(f"user@{domain}")).provider != MailProvider.UNKNOWN


def test_client_install_reads_selected_bytes_once(tmp_path: Path) -> None:
    """Requirement: install exactly the validated buffer even if the source changes."""
    class ChangingPath(Path):
        def read_bytes(self) -> bytes:
            raw = super().read_bytes()
            self.write_text("invalid replacement", encoding="utf-8")
            return raw

    source = ChangingPath(tmp_path / "client.json")
    expected = client_document().encode()
    source.write_bytes(expected)
    target = install_client_secrets(source, MailboxAddress.parse("user@gmail.com"), config_root=tmp_path / "config")
    assert target.read_bytes() == expected


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("profile_address", ["user@gmail.com", "wrong@gmail.com"])
def test_authorization_stores_only_verified_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cached: bool, profile_address: str,
) -> None:
    """Requirement: fresh and cached tokens cannot be stored for a mismatched profile.

    External browser/profile/keyring boundaries are substituted to avoid real consent
    or changing the user's credential store; credentials and serialization are real.
    """
    from mailarchiver import auth

    credentials = auth.Credentials("fixture-token", refresh_token="fixture-refresh", token_uri="https://oauth2.googleapis.com/token", client_id="fixture-client", client_secret="fixture-secret", scopes=[GMAIL_READONLY_SCOPE])
    stored: list[str] = []
    monkeypatch.setattr(auth, "_load_credentials", lambda _account: credentials if cached else None)
    monkeypatch.setattr(auth, "_profile", lambda _credentials: auth.GmailProfile(emailAddress=profile_address, messagesTotal=0, threadsTotal=0, historyId="1"))
    monkeypatch.setattr(auth.keyring, "set_password", lambda _service, _account, value: stored.append(value))

    class Flow:
        @classmethod
        def from_client_secrets_file(cls, *_args: object, **_kwargs: object) -> "Flow":
            return cls()

        def run_local_server(self, **_kwargs: object) -> auth.Credentials:
            return credentials

    monkeypatch.setattr(auth, "InstalledAppFlow", Flow)
    account = MailboxAddress.parse("user@gmail.com")
    if profile_address == account.address:
        assert auth.authorize_gmail(account, tmp_path / "client.json").email_address == account.address
        assert len(stored) == 1
    else:
        with pytest.raises(AuthorizerError, match="no token was stored"):
            auth.authorize_gmail(account, tmp_path / "client.json")
        assert stored == []


def test_refresh_transport_failure_does_not_start_consent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Requirement: a network outage discloses refresh failure without opening consent."""
    from datetime import UTC, datetime, timedelta
    from mailarchiver import auth

    class OfflineCredentials(auth.Credentials):
        def refresh(self, request: object) -> None:
            raise auth.TransportError("fixture network unavailable")

    credentials = OfflineCredentials("expired", refresh_token="refresh", expiry=(datetime.now(UTC) - timedelta(days=1)).replace(tzinfo=None))
    monkeypatch.setattr(auth, "_load_credentials", lambda _account: credentials)
    with pytest.raises(AuthorizerError, match="refresh transport failed"):
        auth.authorize_gmail(MailboxAddress.parse("user@gmail.com"), tmp_path / "missing-client.json")


def test_keyring_write_failure_is_disclosed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement: credential storage failures have no plaintext fallback."""
    from mailarchiver import auth

    def unavailable(*_args: object) -> None:
        raise auth.KeyringError("fixture store locked")

    monkeypatch.setattr(auth.keyring, "set_password", unavailable)
    credentials = auth.Credentials("token", refresh_token="refresh", token_uri="https://oauth2.googleapis.com/token", client_id="client", client_secret="secret", scopes=[GMAIL_READONLY_SCOPE])
    with pytest.raises(AuthorizerError, match="could not store authorization"):
        auth._store_credentials(MailboxAddress.parse("user@gmail.com"), credentials)


def test_cached_refresh_is_verified_before_storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Requirement: refreshed credentials are profile-checked before keyring storage."""
    from datetime import UTC, datetime, timedelta
    from mailarchiver import auth

    events: list[str] = []

    class RefreshingCredentials(auth.Credentials):
        def refresh(self, request: object) -> None:
            events.append("refresh")
            self.token = "refreshed"
            self.expiry = (datetime.now(UTC) + timedelta(hours=1)).replace(tzinfo=None)

    credentials = RefreshingCredentials("expired", refresh_token="refresh", token_uri="https://oauth2.googleapis.com/token", client_id="client", client_secret="secret", scopes=[GMAIL_READONLY_SCOPE], expiry=(datetime.now(UTC) - timedelta(days=1)).replace(tzinfo=None))
    monkeypatch.setattr(auth, "_load_credentials", lambda _account: credentials)

    def profile(current: auth.Credentials) -> auth.GmailProfile:
        assert current.token == "refreshed"
        events.append("profile")
        return auth.GmailProfile(emailAddress="user@gmail.com", messagesTotal=0, threadsTotal=0, historyId="1")

    monkeypatch.setattr(auth, "_profile", profile)
    monkeypatch.setattr(auth.keyring, "set_password", lambda *_args: events.append("store"))
    auth.authorize_gmail(MailboxAddress.parse("user@gmail.com"), tmp_path / "missing-client.json")
    assert events == ["refresh", "profile", "store"]


def test_dns_outage_is_not_negative_provider_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement: DNS timeouts remain errors instead of authoritative negatives."""
    from mailarchiver import auth

    def unavailable(_resolver: object, *_args: object) -> None:
        raise auth.dns.exception.Timeout()

    monkeypatch.setattr(auth.dns.resolver.Resolver, "resolve", unavailable)
    with pytest.raises(AuthorizerError, match="DNS MX lookup failed"):
        detect_provider(MailboxAddress.parse("user@example.test"))
