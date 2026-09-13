# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Authorize one remote mailbox without placing credentials in an archive."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time
import webbrowser
from enum import StrEnum
from pathlib import Path
from urllib.parse import quote

import dns.exception
import dns.resolver
import keyring
import requests
from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow, WSGITimeoutError
from keyring.errors import KeyringError
from oauthlib.oauth2 import OAuth2Error
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .identity import application_data_directory


GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
KEYRING_SERVICE = "mailarchiver.gmail.oauth"
DISTRIBUTED_CLIENT_FILENAME = "gmail_client.json"
DISTRIBUTED_CLIENT_ENV = "MAILARCHIVER_GMAIL_CLIENT_JSON"
GOOGLE_MX_SUFFIXES = (".google.com", ".googlemail.com", ".l.google.com")
MICROSOFT_MX_SUFFIXES = (".mail.protection.outlook.com",)
MICROSOFT_AUTODISCOVER_SUFFIXES = (".outlook.com", ".office365.com")
GMAIL_DOMAINS = frozenset({"gmail.com", "googlemail.com"})
M365_DOMAINS = frozenset({"hotmail.com", "live.com", "outlook.com"})
PROJECT_ID_PATTERN = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]")
DOMAIN_PATTERN = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
)
DOWNLOAD_PATTERNS = ("client_secret*.json", "client_*.json", "credentials*.json")


class StrictModel(BaseModel):
    """Pydantic base for internal structures."""

    model_config = ConfigDict(extra="forbid")


class MailProvider(StrEnum):
    GMAIL = "gmail"
    M365 = "m365"
    UNKNOWN = "unknown"


class MailboxAddress(StrictModel):
    """Normalized mailbox identity supplied on the command line."""

    address: str
    local_part: str
    domain: str

    @classmethod
    def parse(cls, value: str) -> "MailboxAddress":
        address = value.strip()
        if address.count("@") != 1 or any(character.isspace() for character in address):
            raise ValueError("account must be one email address")
        local_part, raw_domain = address.rsplit("@", 1)
        if not local_part or not raw_domain:
            raise ValueError("account must be one email address")
        try:
            domain = raw_domain.rstrip(".").encode("idna").decode("ascii").lower()
        except UnicodeError as error:
            raise ValueError("account has an invalid domain") from error
        if not DOMAIN_PATTERN.fullmatch(domain):
            raise ValueError("account has an invalid domain")
        return cls(address=f"{local_part}@{domain}", local_part=local_part, domain=domain)


class DnsEvidence(StrictModel):
    """Provider-discovery DNS answers for one mail domain."""

    domain: str
    mx_hosts: tuple[str, ...] = ()
    autodiscover_targets: tuple[str, ...] = ()


class ProviderDetection(StrictModel):
    """Explainable provider classification."""

    provider: MailProvider
    evidence: tuple[str, ...]


class InstalledClient(StrictModel):
    """Google's installed-application client configuration."""

    client_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    auth_uri: str = Field(min_length=1)
    token_uri: str = Field(min_length=1)
    auth_provider_x509_cert_url: str | None = None
    client_secret: str = ""
    redirect_uris: tuple[str, ...] = Field(min_length=1)

    @field_validator("client_id")
    @classmethod
    def require_google_client_id(cls, value: str) -> str:
        if not value.endswith(".apps.googleusercontent.com"):
            raise ValueError("expected a Google OAuth client ID")
        return value

    @field_validator("auth_uri")
    @classmethod
    def require_google_auth_uri(cls, value: str) -> str:
        if value != GOOGLE_AUTH_URI:
            raise ValueError("unexpected Google authorization endpoint")
        return value

    @field_validator("token_uri")
    @classmethod
    def require_google_token_uri(cls, value: str) -> str:
        if value != GOOGLE_TOKEN_URI:
            raise ValueError("unexpected Google token endpoint")
        return value

    @field_validator("redirect_uris")
    @classmethod
    def require_loopback_redirect(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != ("http://localhost",):
            raise ValueError("expected the Google Desktop-client loopback redirect")
        return value


class ClientSecrets(StrictModel):
    """Top-level Google Desktop-client download."""

    installed: InstalledClient


class GmailProfile(StrictModel):
    """Typed response from Gmail users.getProfile."""

    email_address: str = Field(alias="emailAddress")
    messages_total: int = Field(alias="messagesTotal")
    threads_total: int = Field(alias="threadsTotal")
    history_id: str = Field(alias="historyId")


class StoredCredentials(StrictModel):
    """Authorized-user JSON retained only inside the operating-system keyring."""

    token: str | None = None
    refresh_token: str
    token_uri: str
    client_id: str
    client_secret: str
    scopes: tuple[str, ...]
    rapt_token: str | None = None
    universe_domain: str | None = None
    account: str | None = None
    expiry: str | None = None


class GcloudPlan(StrictModel):
    """Exact bounded Google Cloud CLI operations for personal setup."""

    login: tuple[str, ...]
    create_project: tuple[str, ...]
    enable_gmail: tuple[str, ...]


class ConsoleStep(StrictModel):
    """One user-confirmed Google Auth Platform page."""

    url: str
    instruction: str


class AuthorizerError(RuntimeError):
    """A disclosed setup, detection, or authorization failure."""


def _dns_name(value: object) -> str:
    return str(value).rstrip(".").lower()


def resolve_dns_evidence(domain: str) -> DnsEvidence:
    """Resolve bounded MX and Autodiscover evidence without modifying the provider."""
    resolver = dns.resolver.Resolver()
    resolver.lifetime = 5.0
    try:
        mx_hosts = tuple(_dns_name(answer.exchange) for answer in resolver.resolve(domain, "MX"))
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
        mx_hosts = ()
    except dns.exception.DNSException as error:
        raise AuthorizerError(f"DNS MX lookup failed for {domain}: {error}") from error
    try:
        targets = tuple(
            _dns_name(answer.target)
            for answer in resolver.resolve(f"autodiscover.{domain}", "CNAME")
        )
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
        targets = ()
    except dns.exception.DNSException as error:
        raise AuthorizerError(f"DNS Autodiscover lookup failed for {domain}: {error}") from error
    return DnsEvidence(domain=domain, mx_hosts=mx_hosts, autodiscover_targets=targets)


def classify_provider(evidence: DnsEvidence) -> ProviderDetection:
    """Classify only strong provider-specific DNS evidence."""
    if evidence.domain in GMAIL_DOMAINS:
        return ProviderDetection(provider=MailProvider.GMAIL, evidence=("well-known Gmail domain",))
    if evidence.domain in M365_DOMAINS:
        return ProviderDetection(provider=MailProvider.M365, evidence=("well-known Microsoft domain",))

    google_hosts = tuple(
        host for host in evidence.mx_hosts if host.endswith(GOOGLE_MX_SUFFIXES)
    )
    if google_hosts:
        return ProviderDetection(
            provider=MailProvider.GMAIL,
            evidence=tuple(f"MX {host}" for host in google_hosts),
        )

    microsoft_hosts = tuple(
        host for host in evidence.mx_hosts if host.endswith(MICROSOFT_MX_SUFFIXES)
    )
    microsoft_autodiscover = tuple(
        target
        for target in evidence.autodiscover_targets
        if target.endswith(MICROSOFT_AUTODISCOVER_SUFFIXES)
    )
    if microsoft_hosts or microsoft_autodiscover:
        return ProviderDetection(
            provider=MailProvider.M365,
            evidence=tuple(f"MX {host}" for host in microsoft_hosts)
            + tuple(f"Autodiscover {target}" for target in microsoft_autodiscover),
        )
    return ProviderDetection(provider=MailProvider.UNKNOWN, evidence=("no provider-specific DNS record",))


def detect_provider(account: MailboxAddress, force_gmail: bool = False) -> ProviderDetection:
    if force_gmail:
        return ProviderDetection(provider=MailProvider.GMAIL, evidence=("--gmail override",))
    if account.domain in GMAIL_DOMAINS | M365_DOMAINS:
        return classify_provider(DnsEvidence(domain=account.domain))
    return classify_provider(resolve_dns_evidence(account.domain))


def unavailable_provider_message(provider: MailProvider) -> str | None:
    """Return the stable user-facing boundary for recognized unavailable providers."""
    if provider is MailProvider.M365:
        return "Microsoft Office not yet implemented."
    return None


def user_config_directory() -> Path:
    """Return a platform-local configuration directory without touching an archive."""
    if sys.platform == "darwin":
        return application_data_directory(Path.home() / "Library" / "Application Support")
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return application_data_directory(base)
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "mailarchiver"


def account_directory(account: MailboxAddress, config_root: Path | None = None) -> Path:
    digest = hashlib.sha256(account.address.casefold().encode()).hexdigest()[:16]
    return (config_root or user_config_directory()) / "auth" / "gmail" / digest


def client_path(account: MailboxAddress, config_root: Path | None = None) -> Path:
    return account_directory(account, config_root) / "client.json"


def distributed_client_path() -> Path:
    """Return the release-provided public Desktop-client configuration path."""
    override = os.environ.get(DISTRIBUTED_CLIENT_ENV)
    return Path(override).expanduser() if override else Path(__file__).with_name(
        DISTRIBUTED_CLIENT_FILENAME
    )


def parse_client_secrets(path: Path) -> ClientSecrets:
    try:
        return ClientSecrets.model_validate_json(path.read_bytes())
    except (OSError, ValueError) as error:
        raise AuthorizerError(f"invalid Google Desktop-client JSON: {path}: {error}") from error


def install_client_secrets(
    source: Path,
    account: MailboxAddress,
    project_id: str | None = None,
    config_root: Path | None = None,
) -> Path:
    try:
        raw = source.read_bytes()
        configuration = ClientSecrets.model_validate_json(raw)
    except (OSError, ValueError) as error:
        raise AuthorizerError(f"invalid Google Desktop-client JSON: {source}: {error}") from error
    if project_id is not None and configuration.installed.project_id != project_id:
        raise AuthorizerError(
            f"download belongs to project {configuration.installed.project_id}, expected {project_id}"
        )
    destination = client_path(account, config_root)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(destination.parent, stat.S_IRWXU)
    temporary = destination.with_suffix(".tmp")
    temporary.write_bytes(raw)
    os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
    temporary.replace(destination)
    return destination


def existing_client_secrets(
    account: MailboxAddress,
    config_root: Path | None = None,
    distributed_path: Path | None = None,
) -> Path | None:
    """Prefer an account override, then the client registered once for the release."""
    account_path = client_path(account, config_root)
    if account_path.is_file():
        parse_client_secrets(account_path)
        return account_path
    shared_path = distributed_path or distributed_client_path()
    if shared_path.is_file():
        parse_client_secrets(shared_path)
        return shared_path
    return None


def _open_console(url: str) -> None:
    print(f"Opening {url}")
    if not webbrowser.open(url, new=2):
        print("The browser did not open automatically; open the URL above.")


def _wait_for_enter(instruction: str) -> None:
    if not sys.stdin.isatty():
        raise AuthorizerError("interactive Google Cloud setup requires a terminal")
    input(f"\n{instruction}\nPress Enter when complete: ")


def _project_id(value: str) -> str:
    project_id = value.strip().lower()
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise AuthorizerError("project ID must be 6-30 lowercase letters, digits, or hyphens")
    return project_id


def _new_project_id() -> str:
    return f"mailarchiver-personal-{secrets.token_hex(4)}"


def gcloud_plan(account: MailboxAddress, project_id: str) -> GcloudPlan:
    """Build auditable commands without changing gcloud's default project."""
    return GcloudPlan(
        login=("auth", "login", account.address, "--brief", "--no-activate"),
        create_project=(
            "projects",
            "create",
            project_id,
            "--name",
            "Email Collection Toolkit personal Gmail",
            "--account",
            account.address,
        ),
        enable_gmail=(
            "services",
            "enable",
            "gmail.googleapis.com",
            "--project",
            project_id,
            "--account",
            account.address,
        ),
    )


def console_steps(account: MailboxAddress, project_id: str) -> tuple[ConsoleStep, ...]:
    """Return stable project-scoped handoff pages for unsupported Console operations."""
    project = quote(project_id, safe="")
    return (
        ConsoleStep(
            url=f"https://console.cloud.google.com/auth/branding?project={project}",
            instruction=(
                "Click Get started if necessary. Use app name 'Email Collection Toolkit personal', "
                "select your own address for support and contact email, choose External for "
                "personal Gmail, accept Google's user-data policy, and create the configuration."
            ),
        ),
        ConsoleStep(
            url=f"https://console.cloud.google.com/auth/audience?project={project}",
            instruction=(
                f"Under Test users, add {account.address}. Leave the app in Testing for this "
                "trial; Google expires Testing authorizations after seven days. Publishing "
                "requires additional branding, policy, and possibly domain-verification work."
            ),
        ),
        ConsoleStep(
            url=f"https://console.cloud.google.com/auth/scopes?project={project}",
            instruction=f"Add exactly this Gmail scope: {GMAIL_READONLY_SCOPE}",
        ),
        ConsoleStep(
            url=f"https://console.cloud.google.com/auth/clients?project={project}",
            instruction=(
                "Create a client named 'Email Collection Toolkit' with application type Desktop app, "
                "then download its JSON file."
            ),
        ),
    )


def _run_gcloud(gcloud: str, *arguments: str) -> None:
    try:
        subprocess.run([gcloud, *arguments], check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise AuthorizerError(f"Google Cloud CLI command failed: gcloud {' '.join(arguments)}") from error


def _create_project_with_gcloud(gcloud: str, account: MailboxAddress) -> str:
    print(f"Google Cloud will authenticate {account.address} in the system browser.")
    answer = input("Create a personal Google Cloud project for this account? [y/N] ").strip().casefold()
    if answer not in {"y", "yes"}:
        raise AuthorizerError("Google Cloud project creation cancelled")
    project_id = _new_project_id()
    plan = gcloud_plan(account, project_id)
    _run_gcloud(gcloud, *plan.login)
    _run_gcloud(gcloud, *plan.create_project)
    _run_gcloud(gcloud, *plan.enable_gmail)
    return project_id


def _create_project_in_browser() -> str:
    _open_console("https://console.cloud.google.com/projectcreate")
    _wait_for_enter("Create a project named 'Email Collection Toolkit personal Gmail'.")
    return _project_id(input("Paste the new project ID: "))


def _download_candidates(since: float) -> tuple[Path, ...]:
    downloads = Path.home() / "Downloads"
    if not downloads.is_dir():
        return ()
    candidates: set[Path] = set()
    for pattern in DOWNLOAD_PATTERNS:
        candidates.update(
            path
            for path in downloads.glob(pattern)
            if path.is_file() and path.stat().st_mtime >= since
        )
    return tuple(sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True))


def _select_download(project_id: str, since: float) -> Path:
    for candidate in _download_candidates(since):
        try:
            if parse_client_secrets(candidate).installed.project_id == project_id:
                print(f"Found {candidate}")
                return candidate
        except AuthorizerError:
            continue
    raw_path = input("Path to the downloaded Desktop-client JSON: ").strip().strip("'\"")
    if not raw_path:
        raise AuthorizerError("no Desktop-client JSON was selected")
    return Path(raw_path).expanduser()


def provision_google_client(account: MailboxAddress) -> Path:
    """Register the distributable client once; this is a maintainer operation."""
    if not sys.stdin.isatty():
        raise AuthorizerError("Google client setup requires an interactive terminal")
    gcloud = shutil.which("gcloud")
    project_id = (
        _create_project_with_gcloud(gcloud, account) if gcloud else _create_project_in_browser()
    )
    if not gcloud:
        project = quote(project_id, safe="")
        _open_console(
            f"https://console.cloud.google.com/apis/library/gmail.googleapis.com?project={project}"
        )
        _wait_for_enter("Click Enable for the Gmail API.")

    steps = console_steps(account, project_id)
    for step in steps[:-1]:
        _open_console(step.url)
        _wait_for_enter(step.instruction)
    download_started = time.time()
    _open_console(steps[-1].url)
    _wait_for_enter(steps[-1].instruction)
    return install_client_secrets(_select_download(project_id, download_started), account, project_id)


def _load_credentials(account: MailboxAddress) -> Credentials | None:
    try:
        serialized = keyring.get_password(KEYRING_SERVICE, account.address.casefold())
    except KeyringError as error:
        raise AuthorizerError(f"OS credential store is unavailable: {error}") from error
    if serialized is None:
        return None
    try:
        payload = StoredCredentials.model_validate_json(serialized)
        return Credentials.from_authorized_user_info(
            payload.model_dump(mode="json", exclude_none=True), [GMAIL_READONLY_SCOPE]
        )
    except (TypeError, ValueError) as error:
        raise AuthorizerError("stored Gmail authorization is invalid") from error


def _store_credentials(account: MailboxAddress, credentials: Credentials) -> None:
    try:
        serialized = StoredCredentials.model_validate_json(credentials.to_json()).model_dump_json(
            exclude_none=True
        )
        keyring.set_password(KEYRING_SERVICE, account.address.casefold(), serialized)
    except (KeyringError, ValueError) as error:
        raise AuthorizerError(f"could not store authorization in the OS credential store: {error}") from error


def _profile(credentials: Credentials) -> GmailProfile:
    try:
        response = AuthorizedSession(credentials).get(GMAIL_PROFILE_URL, timeout=30)
        response.raise_for_status()
        return GmailProfile.model_validate(response.json())
    except (requests.RequestException, ValueError) as error:
        raise AuthorizerError(f"Gmail profile verification failed: {error}") from error


def authorize_gmail(account: MailboxAddress, secrets_path: Path) -> GmailProfile:
    """Authorize only gmail.readonly and verify the chosen account before storage."""
    credentials = _load_credentials(account)
    if credentials is not None:
        try:
            if not credentials.valid and credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
            if credentials.valid:
                profile = _profile(credentials)
                if profile.email_address.casefold() == account.address.casefold():
                    _store_credentials(account, credentials)
                    return profile
        except TransportError as error:
            raise AuthorizerError(f"Gmail token refresh transport failed: {error}") from error
        except RefreshError:
            credentials = None

    flow = InstalledAppFlow.from_client_secrets_file(
        str(secrets_path), scopes=[GMAIL_READONLY_SCOPE]
    )
    try:
        credentials = flow.run_local_server(
            host="127.0.0.1",
            port=0,
            authorization_prompt_message="Authorize Gmail in your browser: {url}",
            success_message="Email Collection Toolkit authorization completed. You may close this window.",
            timeout_seconds=300,
            login_hint=account.address,
            prompt="consent",
        )
    except (OSError, OAuth2Error, requests.RequestException, WSGITimeoutError) as error:
        raise AuthorizerError(f"Gmail authorization failed: {error}") from error
    if not isinstance(credentials, Credentials):
        raise AuthorizerError("Gmail authorization returned unsupported credentials; no token was stored")
    profile = _profile(credentials)
    if profile.email_address.casefold() != account.address.casefold():
        raise AuthorizerError(
            f"Google authorized {profile.email_address}, not requested account {account.address}; "
            "no token was stored"
        )
    _store_credentials(account, credentials)
    return profile


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mailarchiver-auth",
        description="Authorize a remote mailbox without storing credentials in an archive.",
    )
    parser.add_argument("account", help="mailbox address to authorize")
    parser.add_argument(
        "--gmail",
        action="store_true",
        help="treat the account as Gmail when automatic detection is inconclusive",
    )
    client_options = parser.add_mutually_exclusive_group()
    client_options.add_argument(
        "--client-secrets",
        type=Path,
        help="developer override: import an existing Google Desktop-client JSON",
    )
    client_options.add_argument(
        "--register-client",
        action="store_true",
        help="maintainer only: register the shared Desktop client used by a release",
    )
    parser.add_argument(
        "--detect-only",
        action="store_true",
        help="report the detected provider without authorizing or changing external state",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        account = MailboxAddress.parse(arguments.account)
        detection = detect_provider(account, arguments.gmail)
        print(
            f"{account.address}: {detection.provider.value} "
            f"({'; '.join(detection.evidence)})"
        )
        if arguments.detect_only:
            return 0 if detection.provider is not MailProvider.UNKNOWN else 1
        unavailable = unavailable_provider_message(detection.provider)
        if unavailable is not None:
            raise AuthorizerError(unavailable)
        if detection.provider is MailProvider.UNKNOWN:
            raise AuthorizerError(
                f"could not determine the provider for {account.domain}; rerun with --gmail if it is Gmail"
            )

        if arguments.register_client:
            destination = provision_google_client(account)
            print(f"Maintainer client configuration saved at {destination}.")
        elif arguments.client_secrets is not None:
            destination = install_client_secrets(arguments.client_secrets, account)
        else:
            destination = existing_client_secrets(account)
            if destination is None:
                raise AuthorizerError(
                    "this development build has no distributed Gmail client; the release "
                    "maintainer must register it once with --register-client"
                )
        profile = authorize_gmail(account, destination)
        print(f"Authorized read-only Gmail access for {profile.email_address}.")
        print("The refresh token is stored in the operating-system credential store.")
        return 0
    except (AuthorizerError, ValueError) as error:
        parser.exit(1, f"mailarchiver-auth: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
