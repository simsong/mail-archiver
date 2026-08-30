"""Reserved source plug-ins whose provider adapters are not implemented yet."""

from __future__ import annotations

from collections.abc import Iterator

from .plugin_api import (
    MailContainer,
    MailObject,
    IntegrityDecision,
    IntegrityEvidence,
    PluginCapabilities,
    ProgressEvent,
    SkippedInput,
    SourcePlugin,
    SourceIntegrityControls,
    SourceSpec,
)


class ReservedIntegrityControls(SourceIntegrityControls):
    control_id = "reserved-source-integrity-v1"

    def plan(
        self, container: MailContainer, prior: tuple[IntegrityEvidence, ...]
    ) -> Iterator[IntegrityDecision | IntegrityEvidence | ProgressEvent]:
        del container, prior
        raise NotImplementedError("reserved source integrity controls are not implemented")
        yield

    def complete(
        self, container: MailContainer, planned: tuple[IntegrityEvidence, ...]
    ) -> Iterator[IntegrityEvidence | ProgressEvent]:
        del container, planned
        raise NotImplementedError("reserved source integrity controls are not implemented")
        yield


class ReservedSourcePlugin(SourcePlugin):
    """Expose a stable source name without pretending provider ingest works."""

    kind = "reserved"
    scheme = "reserved://"
    capabilities = PluginCapabilities()
    integrity_controls = ReservedIntegrityControls()

    def recognizes(self, source: SourceSpec) -> bool:
        return source.kind == self.kind or source.locator.startswith(self.scheme)

    def discover(self, source: SourceSpec) -> Iterator[MailContainer | ProgressEvent | SkippedInput]:
        del source
        raise NotImplementedError(f"{self.kind} source plug-in is reserved but not implemented")
        yield

    def messages(
        self, container: MailContainer, checkpoint: str | None
    ) -> Iterator[MailObject | ProgressEvent]:
        del container, checkpoint
        raise NotImplementedError(f"{self.kind} source plug-in is reserved but not implemented")
        yield


class GmailSourcePlugin(ReservedSourcePlugin):
    kind = "gmail"
    scheme = "gmail://"
    capabilities = PluginCapabilities(resumable=True, stable_inventory=False, network_access=True, max_concurrency=4)


class ImapSourcePlugin(ReservedSourcePlugin):
    kind = "imap"
    scheme = "imap://"
    capabilities = PluginCapabilities(resumable=True, stable_inventory=False, network_access=True, max_concurrency=2)


class O365SourcePlugin(ReservedSourcePlugin):
    kind = "o365"
    scheme = "o365://"
    capabilities = PluginCapabilities(resumable=True, stable_inventory=False, network_access=True, max_concurrency=4)


class MicrosoftExchangeSourcePlugin(ReservedSourcePlugin):
    kind = "microsoft-exchange"
    scheme = "exchange://"
    capabilities = PluginCapabilities(resumable=True, stable_inventory=False, network_access=True, max_concurrency=2)


class StdinSourcePlugin(ReservedSourcePlugin):
    kind = "stdin"
    scheme = "stdin://"
    capabilities = PluginCapabilities(resumable=False, stable_inventory=False, max_concurrency=1)

    def recognizes(self, source: SourceSpec) -> bool:
        return source.locator == "-" or super().recognizes(source)
