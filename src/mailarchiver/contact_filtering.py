# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Classify address identities conservatively for the human-contacts view."""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, TypeAdapter
from yaml import YAMLError, safe_load


def _validate_regex(pattern: str) -> str:
    """Reject policy typos before matching, even for empty catalogs or unused rules."""
    try:
        re.compile(pattern, re.IGNORECASE)
    except re.error as error:
        raise ValueError(f"invalid regular expression {pattern!r}: {error}") from error
    return pattern


RegexPattern = Annotated[str, AfterValidator(_validate_regex)]


class ContactKind(StrEnum):
    HUMAN = "human"
    MAILING_LIST = "mailing-list"
    SERVICE = "service"
    BOGUS = "bogus"


class PatternSet(BaseModel):
    """Regexes applied to a normalized address component."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    domain_patterns: tuple[RegexPattern, ...] = ()
    local_part_patterns: tuple[RegexPattern, ...] = ()


class ContactFilterConfiguration(BaseModel):
    """Versioned packaged policy for excluding non-human address identities."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    mode: Literal["replace"]
    max_human_local_part_length: int = Field(ge=1, le=64)
    allow_address_patterns: tuple[RegexPattern, ...]
    mailing_list: PatternSet
    service: PatternSet
    bogus_local_part_patterns: tuple[RegexPattern, ...]
    bogus_domain_patterns: tuple[RegexPattern, ...]


class ContactFilterExtension(BaseModel):
    """Additional exclusion patterns layered onto the packaged policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    mode: Literal["extend"]
    allow_address_patterns: tuple[RegexPattern, ...] = ()
    mailing_list: PatternSet = PatternSet()
    service: PatternSet = PatternSet()
    bogus_local_part_patterns: tuple[RegexPattern, ...] = ()
    bogus_domain_patterns: tuple[RegexPattern, ...] = ()


class AddressClassification(BaseModel):
    """A stable, explainable classification result."""

    kind: ContactKind
    reason: str


def load_contact_filters(path: Path) -> ContactFilterConfiguration | ContactFilterExtension:
    """Load one strict contact-filter policy."""
    try:
        with path.open(encoding="utf-8") as source:
            contents = safe_load(source)
        return TypeAdapter(
            Annotated[ContactFilterConfiguration | ContactFilterExtension, Field(discriminator="mode")]
        ).validate_python(contents)
    except (ValueError, YAMLError) as error:
        raise ValueError(f"invalid contact-filter policy {path}: {error}") from error


def _extend(base: tuple[str, ...], additions: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*base, *additions)))


def extend_contact_filters(
    base: ContactFilterConfiguration, additions: ContactFilterExtension
) -> ContactFilterConfiguration:
    """Return the effective policy with each configured regex list unioned in order."""
    return ContactFilterConfiguration(
        version=base.version,
        mode="replace",
        max_human_local_part_length=base.max_human_local_part_length,
        allow_address_patterns=_extend(base.allow_address_patterns, additions.allow_address_patterns),
        mailing_list=PatternSet(
            domain_patterns=_extend(base.mailing_list.domain_patterns, additions.mailing_list.domain_patterns),
            local_part_patterns=_extend(base.mailing_list.local_part_patterns, additions.mailing_list.local_part_patterns),
        ),
        service=PatternSet(
            domain_patterns=_extend(base.service.domain_patterns, additions.service.domain_patterns),
            local_part_patterns=_extend(base.service.local_part_patterns, additions.service.local_part_patterns),
        ),
        bogus_local_part_patterns=_extend(base.bogus_local_part_patterns, additions.bogus_local_part_patterns),
        bogus_domain_patterns=_extend(base.bogus_domain_patterns, additions.bogus_domain_patterns),
    )


@lru_cache(maxsize=None)
def contact_filters(archive: Path | None = None) -> ContactFilterConfiguration:
    """Return an archive-local policy when present, otherwise the packaged default."""
    packaged = load_contact_filters(Path(__file__).with_name("contact_filters.yaml"))
    assert isinstance(packaged, ContactFilterConfiguration)
    override = None if archive is None else archive / "contact_filters.yaml"
    if override is None or not override.is_file():
        return packaged
    configured = load_contact_filters(override)
    return configured if isinstance(configured, ContactFilterConfiguration) else extend_contact_filters(packaged, configured)


def _matches(value: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, value, re.IGNORECASE) is not None for pattern in patterns)


def classify_address(
    address: str, configuration: ContactFilterConfiguration | None = None
) -> AddressClassification:
    """Classify one catalogued address without treating ordinary Unicode as invalid."""
    configuration = contact_filters() if configuration is None else configuration
    if _matches(address, configuration.allow_address_patterns):
        return AddressClassification(kind=ContactKind.HUMAN, reason="allow-pattern")
    local, separator, domain = address.partition("@")
    if not separator or "@" in domain:
        return AddressClassification(kind=ContactKind.BOGUS, reason="malformed-at-sign")
    if any(character == "\ufffd" or unicodedata.category(character) in {"Cc", "Cs"} for character in address):
        return AddressClassification(kind=ContactKind.BOGUS, reason="invalid-unicode")
    if _matches(local, configuration.bogus_local_part_patterns):
        return AddressClassification(kind=ContactKind.BOGUS, reason="invalid-local-part")
    if not domain or _matches(domain, configuration.bogus_domain_patterns):
        return AddressClassification(kind=ContactKind.BOGUS, reason="invalid-domain")
    if _matches(domain, configuration.mailing_list.domain_patterns) or _matches(
        local, configuration.mailing_list.local_part_patterns
    ):
        return AddressClassification(kind=ContactKind.MAILING_LIST, reason="mailing-list-pattern")
    if _matches(domain, configuration.service.domain_patterns) or _matches(
        local, configuration.service.local_part_patterns
    ):
        return AddressClassification(kind=ContactKind.SERVICE, reason="service-pattern")
    if len(local) > configuration.max_human_local_part_length:
        return AddressClassification(kind=ContactKind.BOGUS, reason="long-local-part")
    return AddressClassification(kind=ContactKind.HUMAN, reason="no-exclusion-rule")
