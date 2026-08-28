"""Verify trusted manifest discovery before ingest workers start."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from mailarchiver.plugin_api import FileProbe, MailContainer, MailObject, SourceReference, SourceSpec
from mailarchiver.plugin_loader import PluginDiscoveryError, load_plugins


def write_plugin(
    root: Path,
    plugin_type: str,
    kind: str,
    code: str,
    *,
    priority: int = 100,
    api_version: int = 1,
    entrypoint: str = "plugin:create_plugin",
) -> Path:
    category = "sources" if plugin_type == "source" else "files"
    directory = root / category / kind
    directory.mkdir(parents=True)
    (directory / "plugin.toml").write_text(
        "\n".join(
            (
                f"api_version = {api_version}",
                f'plugin_type = "{plugin_type}"',
                f'kind = "{kind}"',
                f'name = "{kind} fixture"',
                'implementation_version = "1"',
                f"priority = {priority}",
                f'entrypoint = "{entrypoint}"',
                "",
            )
        )
    )
    (directory / "plugin.py").write_text(code)
    return directory


FILE_PLUGIN = """
class FixtureParser:
    kind = "fixture"

    def recognizes(self, path):
        return str(path).endswith(".fixture")

    def messages(self, source, start_offset=0):
        yield source, start_offset


def create_plugin():
    return FixtureParser()
"""


def test_packaged_manifests_load_existing_builtin_parsers_in_priority_order() -> None:
    """Requirement: packaged parsers are manifest-loaded before worker startup."""
    registry = load_plugins()

    assert [plugin.manifest.kind for plugin in registry.sources] == [
        "file-folder",
        "gmail",
        "imap",
        "o365",
        "microsoft-exchange",
        "stdin",
    ]
    assert [plugin.manifest.kind for plugin in registry.files] == ["emlx", "babyl", "mbox", "message"]
    assert registry.source("file-folder").implementation.__class__.__name__ == "LocalSourcePlugin"
    assert registry.file("babyl").implementation.__class__.__name__ == "BabylFileParser"
    assert all(plugin.builtin for plugin in (*registry.sources, *registry.files))
    assert registry.source("o365").implementation.recognizes(SourceSpec(locator="o365://account"))
    assert registry.source("stdin").implementation.capabilities.max_concurrency == 1
    with pytest.raises(NotImplementedError, match="gmail source plug-in is reserved"):
        list(registry.source("gmail").implementation.discover(SourceSpec(locator="gmail://account")))


def test_explicit_external_directory_loads_a_generator_parser(tmp_path: Path) -> None:
    """Requirement: an explicitly trusted directory can add a physical parser without editing core code."""
    root = tmp_path / "trusted-plugins"
    write_plugin(root, "file", "fixture", FILE_PLUGIN, priority=250)

    registry = load_plugins([root])
    parser = registry.file("fixture").implementation

    assert parser.recognizes(Path("mail.fixture"))
    assert list(parser.messages("container", 7)) == [("container", 7)]
    assert not registry.file("fixture").builtin


def test_local_source_delegates_to_a_directory_discovered_file_generator(tmp_path: Path) -> None:
    """Requirement: local source and external file generators compose through the frozen registry."""
    root = tmp_path / "trusted-plugins"
    code = """
from pathlib import Path
from mailarchiver.plugin_api import MailObject

class FixtureParser:
    kind = "fixture-mail"

    def recognizes(self, probe):
        return probe.path.suffix == ".fixture-mail"

    def messages(self, container, checkpoint):
        del checkpoint
        raw = Path(container.source.display_name).read_bytes()
        yield MailObject(
            work_id=container.work_id,
            raw=raw,
            source=container.source,
            cursor="fixture:0",
            completed_bytes=len(raw),
            total_bytes=len(raw),
        )

def create_plugin():
    return FixtureParser()
"""
    write_plugin(root, "file", "fixture-mail", code, priority=250)
    raw = b"From: external@example.net\nDate: Thu, 1 Feb 2024 12:00:00 +0000\n\nbody\n"
    source = tmp_path / "mail.fixture-mail"
    source.write_bytes(raw)
    registry = load_plugins([root])
    local = registry.source("file-folder").implementation

    discovered = list(local.discover(SourceSpec(locator=str(source))))

    assert len(discovered) == 1 and isinstance(discovered[0], MailContainer)
    messages = list(local.messages(discovered[0], None))
    assert len(messages) == 1 and isinstance(messages[0], MailObject)
    assert messages[0].raw == raw
    assert messages[0].cursor == "fixture:0"


def test_local_source_rejects_ambiguous_file_recognizers(tmp_path: Path) -> None:
    """Requirement: priority orders diagnostics but never silently resolves two matching parsers."""
    root = tmp_path / "trusted-plugins"
    template = """
class Parser:
    kind = {kind!r}
    def recognizes(self, probe):
        return probe.path.suffix == ".ambiguous"
    def messages(self, container, checkpoint):
        del container, checkpoint
        yield from ()
def create_plugin():
    return Parser()
"""
    write_plugin(root, "file", "alpha-parser", template.format(kind="alpha-parser"), priority=10)
    write_plugin(root, "file", "zulu-parser", template.format(kind="zulu-parser"), priority=20)
    source = tmp_path / "mail.ambiguous"
    source.write_bytes(b"ambiguous")
    local = load_plugins([root]).source("file-folder").implementation

    with pytest.raises(ValueError, match=r"ambiguous file parser plug-ins.*alpha-parser, zulu-parser"):
        list(local.discover(SourceSpec(locator=str(source))))


def test_unconfigured_directory_is_never_searched_for_plugins(tmp_path: Path) -> None:
    """Requirement: source or working-directory membership alone must not execute Python."""
    marker = tmp_path / "executed"
    code = "from pathlib import Path\n" + FILE_PLUGIN + f'\nPath({str(marker)!r}).write_text("executed")\n'
    write_plugin(tmp_path / "untrusted", "file", "fixture", code)

    registry = load_plugins()

    assert all(plugin.manifest.kind != "fixture" for plugin in registry.files)
    assert not marker.exists()


def test_every_manifest_is_validated_before_any_external_code_is_imported(tmp_path: Path) -> None:
    """Requirement: one invalid manifest prevents all dynamic code execution for that startup."""
    root = tmp_path / "trusted"
    marker = tmp_path / "executed"
    code = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n" + FILE_PLUGIN
    write_plugin(root, "file", "fixture", code)
    write_plugin(root, "file", "invalid-api", FILE_PLUGIN.replace('"fixture"', '"invalid-api"'), api_version=2)

    with pytest.raises(PluginDiscoveryError, match="invalid plug-in manifests"):
        load_plugins([root])

    assert not marker.exists()


def test_duplicate_kind_is_rejected_before_import(tmp_path: Path) -> None:
    """Requirement: an external parser cannot ambiguously replace a packaged parser."""
    root = tmp_path / "trusted"
    marker = tmp_path / "executed"
    code = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n" + FILE_PLUGIN.replace(
        '"fixture"', '"mbox"'
    )
    write_plugin(root, "file", "mbox", code)

    with pytest.raises(PluginDiscoveryError, match="duplicate file plug-in kind mbox"):
        load_plugins([root])

    assert not marker.exists()


@pytest.mark.parametrize(
    ("kind", "plugin_type", "entrypoint", "match"),
    (
        ("wrong-place", "source", "plugin:create_plugin", "declares source, but is under files"),
        ("unsafe", "file", "../outside:create_plugin", "invalid entrypoint module"),
        ("missing", "file", "absent:create_plugin", "entrypoint module is not inside"),
    ),
)
def test_manifest_location_and_entrypoint_are_fail_closed(
    tmp_path: Path, kind: str, plugin_type: str, entrypoint: str, match: str
) -> None:
    """Requirement: type mismatches and nonlocal external entrypoints fail before import."""
    root = tmp_path / "trusted"
    directory = root / "files" / kind
    directory.mkdir(parents=True)
    (directory / "plugin.py").write_text(FILE_PLUGIN.replace('"fixture"', f'"{kind}"'))
    (directory / "plugin.toml").write_text(
        f"""api_version = 1
plugin_type = "{plugin_type}"
kind = "{kind}"
name = "invalid fixture"
implementation_version = "1"
priority = 10
entrypoint = "{entrypoint}"
"""
    )

    with pytest.raises(PluginDiscoveryError, match=match):
        load_plugins([root])


def test_registry_order_is_independent_of_directory_creation_order(tmp_path: Path) -> None:
    """Requirement: priority and kind completely determine parser registry order."""
    root = tmp_path / "trusted"
    write_plugin(root, "file", "zulu", FILE_PLUGIN.replace('"fixture"', '"zulu"'), priority=150)
    write_plugin(root, "file", "alpha", FILE_PLUGIN.replace('"fixture"', '"alpha"'), priority=150)
    write_plugin(root, "file", "first", FILE_PLUGIN.replace('"fixture"', '"first"'), priority=50)

    kinds = [plugin.manifest.kind for plugin in load_plugins([root]).files]

    assert kinds == ["first", "emlx", "alpha", "zulu", "babyl", "mbox", "message"]


def test_registry_is_frozen_after_loading() -> None:
    """Requirement: workers share a registry that cannot be mutated after discovery."""
    registry = load_plugins()

    with pytest.raises(ValidationError, match="frozen"):
        registry.files = ()  # type: ignore[misc]


def test_entrypoint_must_return_the_declared_parser_kind(tmp_path: Path) -> None:
    """Requirement: a manifest cannot silently bind a differently named implementation."""
    root = tmp_path / "trusted"
    write_plugin(root, "file", "claimed", FILE_PLUGIN)

    with pytest.raises(PluginDiscoveryError, match="returned kind 'fixture'; expected 'claimed'"):
        load_plugins([root])


def test_source_entrypoint_requires_capabilities_and_integrity_controls(tmp_path: Path) -> None:
    """Requirement: a source cannot enter workers without framework-enforceable controls."""
    root = tmp_path / "trusted"
    code = """
class IncompleteSource:
    kind = "incomplete-source"
    def recognizes(self, source):
        return False
    def discover(self, source):
        del source
        yield from ()
    def messages(self, container, checkpoint):
        del container, checkpoint
        yield from ()
def create_plugin():
    return IncompleteSource()
"""
    write_plugin(root, "source", "incomplete-source", code)

    with pytest.raises(
        PluginDiscoveryError,
        match=r"lacks or has invalid capabilities, integrity_controls\.plan/complete",
    ):
        load_plugins([root])


def test_source_integrity_controls_require_a_nonempty_control_id(tmp_path: Path) -> None:
    """Requirement: incomplete integrity controls fail before source or scanner work starts."""
    root = tmp_path / "trusted"
    code = """
from mailarchiver.plugin_api import PluginCapabilities

class Controls:
    def plan(self, container, prior):
        del container, prior
        yield from ()
    def complete(self, container, planned):
        del container, planned
        yield from ()

class IncompleteSource:
    kind = "incomplete-source"
    capabilities = PluginCapabilities()
    integrity_controls = Controls()
    def recognizes(self, source):
        return False
    def discover(self, source):
        del source
        yield from ()
    def messages(self, container, resume_cursor):
        del container, resume_cursor
        yield from ()

def create_plugin():
    return IncompleteSource()
"""
    write_plugin(root, "source", "incomplete-source", code)

    with pytest.raises(PluginDiscoveryError, match=r"invalid integrity_controls\.control_id"):
        load_plugins([root])


def test_mail_byte_boundaries_reject_text_coercion(tmp_path: Path) -> None:
    """Requirement: source plug-ins must supply original bytes rather than text for implicit encoding."""
    reference = SourceReference(
        plugin_kind="fixture-source",
        source_id="fixture-account",
        native_id="message-1",
        display_name="Fixture/message-1",
    )

    with pytest.raises(ValidationError, match="bytes_type"):
        MailObject(
            work_id="fixture:message-1",
            raw="silently encoded text",  # type: ignore[arg-type]
            source=reference,
            cursor="message-1",
        )
    with pytest.raises(ValidationError, match="bytes_type"):
        FileProbe(
            path=tmp_path / "message.eml",
            byte_length=21,
            prefix="silently encoded text",  # type: ignore[arg-type]
        )


def test_source_fallback_date_is_timezone_aware_and_normalized() -> None:
    """Requirement: provider fallback dates cross the plug-in boundary as UTC instants."""
    reference = SourceReference(
        plugin_kind="fixture-source",
        source_id="fixture-account",
        native_id="message-1",
        display_name="Fixture/message-1",
    )

    with pytest.raises(ValidationError, match="source_date_utc must be timezone-aware"):
        MailObject(
            work_id="fixture:message-1",
            raw=b"From: sender@example.net\n\nbody\n",
            source=reference,
            cursor="message-1",
            source_date_utc=datetime(2024, 2, 1, 7),
        )

    mail = MailObject(
        work_id="fixture:message-1",
        raw=b"From: sender@example.net\n\nbody\n",
        source=reference,
        cursor="message-1",
        source_date_utc=datetime(2024, 2, 1, 7, tzinfo=timezone(timedelta(hours=-5))),
    )
    assert mail.source_date_utc == datetime(2024, 2, 1, 12, tzinfo=timezone.utc)


def test_path_only_legacy_source_cannot_enter_the_production_registry(tmp_path: Path) -> None:
    """Requirement: every manifest source implements the full framework and integrity contract."""
    root = tmp_path / "trusted"
    code = """
class LegacySource:
    kind = "legacy-source"
    def paths(self, source):
        yield source
def create_plugin():
    return LegacySource()
"""
    write_plugin(root, "source", "legacy-source", code)

    with pytest.raises(
        PluginDiscoveryError,
        match=r"lacks or has invalid recognizes, discover, messages, capabilities, "
        r"integrity_controls\.plan/complete, integrity_controls\.control_id",
    ):
        load_plugins([root])


def test_symlinked_plugin_category_cannot_escape_configured_root(tmp_path: Path) -> None:
    """Requirement: trusting one plug-in root does not authorize code reached through an escaping category link."""
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    outside = tmp_path / "outside"
    marker = tmp_path / "executed"
    code = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n" + FILE_PLUGIN.replace(
        '"fixture"', '"escape"'
    )
    write_plugin(outside, "file", "escape", code)
    (trusted / "files").symlink_to(outside / "files", target_is_directory=True)

    with pytest.raises(PluginDiscoveryError, match="plug-in category escapes configured root"):
        load_plugins([trusted])

    assert not marker.exists()


def test_symlinked_plugin_directory_cannot_escape_configured_root(tmp_path: Path) -> None:
    """Requirement: each discovered plug-in package remains physically below its trusted root."""
    trusted = tmp_path / "trusted"
    (trusted / "files").mkdir(parents=True)
    outside = tmp_path / "outside"
    marker = tmp_path / "executed"
    code = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n" + FILE_PLUGIN.replace(
        '"fixture"', '"escape"'
    )
    outside_plugin = write_plugin(outside, "file", "escape", code)
    (trusted / "files" / "escape").symlink_to(outside_plugin, target_is_directory=True)

    with pytest.raises(PluginDiscoveryError, match="plug-in directory escapes configured root"):
        load_plugins([trusted])

    assert not marker.exists()
