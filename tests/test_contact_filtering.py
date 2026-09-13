# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Requirements: human-contact output suppresses explainable non-human identities."""

import pytest

from mailarchiver.contact_filtering import ContactKind, classify_address, contact_filters


@pytest.mark.parametrize(
    ("address", "kind"),
    (
        ("private-board@googlegroups.com", ContactKind.MAILING_LIST),
        ("belmont-itac@yahoogroups.com", ContactKind.MAILING_LIST),
        ("customerresourcecenter@alerts.cambridgetrust.com", ContactKind.SERVICE),
        ("enews@issa.org", ContactKind.SERVICE),
        ("zdnetuk@newsletters.zdnetuk.cneteu.net", ContactKind.SERVICE),
        ("wlcrewboosters+noreply@googlegroups.com", ContactKind.MAILING_LIST),
        ("zones_5120+1007507.896179177.2@zones.reply.tm0.com", ContactKind.SERVICE),
        ("yourlist-160-2703@reply.info.drugstore.com", ContactKind.SERVICE),
        ("xmlrpc-dev-sc.1195693110.dickbhkloabcemflhjbn-simsong=acm.org@ws.apache.org", ContactKind.SERVICE),
        ("wt37notes1/sydney/au/westcongrp@westcon.com", ContactKind.BOGUS),
        ("root@.", ContactKind.BOGUS),
        ('"vshprod "@tera.umi.com', ContactKind.BOGUS),
        ("*@amexmail.com", ContactKind.BOGUS),
        ("=@ex.com", ContactKind.HUMAN),
        ("voice-message-123@vm.vonage.com", ContactKind.HUMAN),
        ("root@apache.vineyard.net", ContactKind.SERVICE),
        ("support@dreamhost.com", ContactKind.SERVICE),
        ("daily-html@chronicle.com", ContactKind.SERVICE),
        ("\ufffdprz@acm.org", ContactKind.BOGUS),
        ("a" * 49 + "@example.org", ContactKind.BOGUS),
        ("k+aorgpre78k_2hh_dspe86in2rcdyeiivipsfke0clbtwxhecpjz21wkvpgq1gi8156yds2jhbqxl@docs.google.com", ContactKind.BOGUS),
        ("person@example.org", ContactKind.HUMAN),
    ),
)
def test_classify_contact_addresses(address: str, kind: ContactKind) -> None:
    """Requirement: groups, service identities, and malformed values stay out of human contacts."""
    assert classify_address(address).kind is kind


@pytest.mark.parametrize(
    ("address", "reason"),
    (
        ("person@.", "invalid-domain"),
        ("person@", "invalid-domain"),
        ("*@", "invalid-local-part"),
        ("@", "invalid-local-part"),
        ("person@example..org", "invalid-domain"),
        ("*@example.org", "invalid-local-part"),
        ("*@.", "invalid-local-part"),
    ),
)
def test_bogus_reason_identifies_the_matching_address_component(address: str, reason: str) -> None:
    """Requirement: diagnostics distinguish domain rules from local-part rules."""
    result = classify_address(address)
    assert result.kind is ContactKind.BOGUS
    assert result.reason == reason


def test_archive_contact_filter_policy_overrides_the_packaged_default(tmp_path) -> None:
    """Requirement: an archive-local policy takes precedence over the packaged policy."""
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "contact_filters.yaml").write_text(
        """
version: 1
mode: replace
max_human_local_part_length: 48
allow_address_patterns: ['^support@example\\.org$']
mailing_list: {domain_patterns: [], local_part_patterns: []}
service: {domain_patterns: [], local_part_patterns: ['^support$']}
bogus_local_part_patterns: []
bogus_domain_patterns: []
""".lstrip(),
        encoding="utf-8",
    )

    assert classify_address("support@example.org", contact_filters(archive)).kind is ContactKind.HUMAN


def test_archive_contact_filter_policy_can_extend_the_packaged_default(tmp_path) -> None:
    """Requirement: extend unions configured lists with the packaged policy without duplicates."""
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "contact_filters.yaml").write_text(
        """
version: 1
mode: extend
allow_address_patterns: ['^support@example\\.org$']
mailing_list:
  domain_patterns: ['(^|\\.)googlegroups\\.com$']
service:
  local_part_patterns: ['^support$']
""".lstrip(),
        encoding="utf-8",
    )

    filters = contact_filters(archive)

    assert filters.mailing_list.domain_patterns.count("(^|\\.)googlegroups\\.com$") == 1
    assert classify_address("support@example.org", filters).kind is ContactKind.HUMAN
    assert classify_address("private-board@googlegroups.com", filters).kind is ContactKind.MAILING_LIST
