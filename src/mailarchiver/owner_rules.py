# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Explicit, case-insensitive mailbox/address glob rules for Sent classification."""

from __future__ import annotations

from fnmatch import fnmatchcase
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


def normalize_rules(values: list[str]) -> list[str]:
    result: set[str] = set()
    for value in values:
        rule = value.strip().lower()
        if not rule or rule.startswith("#"):
            continue
        if any(character.isspace() or (ord(character) < 32 or ord(character) == 127) for character in rule):
            raise ValueError("Use mailbox names or email patterns, not display names or spaces.")
        if rule.count("@") > 1 or ("@" in rule and any(not part for part in rule.split("@"))):
            raise ValueError(f"Incomplete email pattern: {rule}. Use a mailbox name or mailbox@domain.")
        if any(character in rule for character in ",;<>"):
            raise ValueError("Enter one mailbox/email rule per line, or separate rules with commas.")
        result.add(rule)
    return sorted(result)


def split_rules(value: str) -> list[str]:
    return normalize_rules(re.split(r"[,\n\r]+", value))


def rule_matches(rule: str, sender: str) -> bool:
    if not sender.strip():
        return False
    local, separator, domain = sender.strip().lower().rpartition("@")
    if not separator:
        local, domain = sender.strip().lower(), ""
    mailbox_pattern, separator, domain_pattern = rule.partition("@")
    return fnmatchcase(local, mailbox_pattern) and fnmatchcase(domain, domain_pattern if separator else "*")


class OwnerRules(BaseModel):
    """Exclusions override includes; a bare rule matches the whole mailbox name."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)

    @field_validator("include", "exclude")
    @classmethod
    def validate_rules(cls, values: list[str]) -> list[str]:
        return normalize_rules(values)

    def matches(self, sender: str) -> bool:
        return any(rule_matches(rule, sender) for rule in self.include) and not any(
            rule_matches(rule, sender) for rule in self.exclude
        )

    @classmethod
    def from_text(cls, include: str, exclude: str) -> OwnerRules:
        return cls(include=split_rules(include), exclude=split_rules(exclude))
