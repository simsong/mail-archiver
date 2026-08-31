# Name Resolution Record Linkage

## Operating decision for librarians

The name-resolution system will use the open-source Python [`dedupe`](https://github.com/dedupeio/dedupe)
library as its matching engine. Dedupe compares structured records, learns
weights and blocking rules from a small set of human-labeled matches and
non-matches, and produces likely entity clusters. Its documentation describes
this workflow as machine-learning-assisted deduplication and entity resolution
for structured data; the project is MIT-licensed.

The application will also provide conservative default rules before any local
training occurs. Those rules will normalize addresses, preserve explicit RFC
display names, recognize common local-part forms, and assign evidence and
confidence rather than silently deciding that two people are the same. A
shared domain, a shared surname, or similar initials is candidate evidence, not
proof.

The resolver will produce a reviewable match list. A librarian will be able to:

* accept or reject a proposed match;
* merge records into a person cluster;
* split a cluster when an automatic match is wrong;
* edit the canonical display name and alias notes; and
* record a short reason or source for the decision.

The original email address, original display-name observations, source
provenance, and raw messages remain unchanged. Match decisions and canonical
names are rebuildable analysis metadata. An accepted match is therefore a
reviewed assertion about identity, not a rewrite of the preserved email.

As of 2026-08-30, this is the selected design, not an implemented feature in
the repository. The repository currently extracts explicit display names and
maintains address suggestions, and it contains a synthetic benchmark, but it
does not yet integrate Dedupe or provide the match-review editor.

## Do librarians need to edit configuration files?

Short answer: no. Some librarians are comfortable editing text files, and
plain YAML is useful for transparent, versionable defaults. We should not
assume that every librarian wants to edit YAML, understand schemas, or repair a
syntax error. More importantly, asking a user to edit every configuration file
would expose implementation details that are not part of the archival task.

We should not build a user interface for every configuration file. Instead,
use three levels of configuration:

1. **Librarian-facing review.** Provide a UI for match decisions, canonical
   names, aliases, merge/split operations, notes, and undoable review history.
2. **Librarian-facing policy.** Expose a small set of safe controls such as
   whether automatic matches may be accepted above a threshold, which fields
   are trusted, and whether a domain is organizational or public mail. The UI
   should validate these controls and show a before/after preview.
3. **Administrator/developer configuration.** Keep source plug-ins, archive
   paths, scanner settings, schema versions, and advanced resolver features in
   documented YAML or TOML files. Validate them before use and report errors in
   plain language.

The match list is the important user interface. It is a finite, explainable
work queue, whereas the full configuration surface is an implementation
boundary. The application may offer an optional “open configuration folder” or
“edit advanced settings” action for expert users, but ordinary review should
not depend on a text editor.

## Background

The motivating example is a person represented by several email addresses
across different parts of an academic and cultural-work history. The observed
pattern includes:

* a short organization-style local part;
* a public-mail address containing a concatenated name;
* an academic address with initials and a numeric suffix;
* a university address with a concatenated name; and
* a museum-style address with a dotted name.

Some messages supply a full display name, some supply an abbreviated or
reordered display name, and some supply only the address. The hard problem is
not parsing one RFC 5322 address. It is deciding which address records likely
refer to one person, choosing a useful canonical name, and preserving the
uncertainty when the evidence is weak.

The repository contains a privacy-safe synthetic benchmark at
[`benchmarks/name_resolution/organization_aliases_synthetic.yaml`](../benchmarks/name_resolution/organization_aliases_synthetic.yaml).
It represents five aliases for one synthetic person using reserved `.test`
domains. The benchmark covers short initials, concatenated names, initials
with numeric suffixes, and dotted names. Its current header-only baseline
finds explicit evidence in three cases and exactly reproduces the expected
canonical name in one case; it intentionally demonstrates why inference and
linkage are still needed.

The real source example is not reproduced in this document or in the synthetic
corpus. Source mail and source address data remain subject to the project's
read-only preservation rules.

## Problem definition

Input records may contain:

```text
record_id | observed_display_name | email_address | message/source evidence
```

The output is not merely a prettier address list. It is a set of candidate
links and reviewed person groups:

```text
person_group | canonical_name | alias_address | confidence | evidence | status
```

The system should distinguish these states:

* **same address:** the address is identical after the project's address
  normalization;
* **candidate match:** evidence suggests two records may describe one person;
* **reviewed match:** a librarian accepted the link or a configured rule made a
  sufficiently strong, auditable decision;
* **reviewed non-match:** a librarian rejected the link; and
* **unknown:** available evidence is insufficient.

An address can have several observed names over time. A person group can have
several addresses. Neither relation should replace the original observations.

## Proposed processing workflow

```text
RFC headers and catalog metadata
              |
              v
    normalize address/name observations
              |
              v
    generate candidates using default rules
              |
              v
       score pairs with Dedupe
              |
              v
       cluster likely matching records
              |
              v
    librarian reviews match list and edits groups
              |
              v
       versioned alias/person derivative
```

### 1. Preserve and normalize observations

The ingest layer already retains the raw message and normalizes email address
text into the archive catalog. The resolver should consume a derivative table
containing one row for each address observation, including:

* normalized address and its original spelling;
* every observed RFC display name, including an empty display name;
* message count and first/last dates;
* sender, To, Cc, and Bcc roles;
* source and mailbox context when available; and
* resolver-policy and extraction versions.

Name normalization must be reversible or retain the original value. It may
remove surrounding whitespace, decode RFC 2047 words, normalize punctuation,
and parse `Last, First` forms, but it must not discard the original header
value.

### 2. Generate candidate links

Candidate generation keeps the comparison set manageable and prevents weak
signals from becoming automatic merges. Candidate features may include:

* exact normalized address, which links repeated observations of one address
  but does not by itself link different addresses;
* normalized full-name equality;
* parsed given/family-name components and initials;
* local-part tokens after removing separators and known numeric suffixes;
* compatible local-part forms such as `first.last`, `firstlast`, and initials;
* domain category and organization relationship; and
* shared signature, reply context, or other message evidence when available.

The resolver must not treat a shared domain, a shared short local part, or a
common surname as sufficient by itself. Generic addresses such as `info`,
`admin`, mailing lists, aliases, role accounts, and automated senders should be
blocked or down-weighted.

### 3. Score and cluster

Dedupe should receive structured fields and the default feature decisions. A
small reviewed training set can teach the match weights for this archive. The
result must retain pair scores, the fields that contributed to the score, the
blocking rule, and the resolver version. Clustering should be deterministic
for a fixed input, configuration, training set, and software version.

Automatic acceptance should be limited to high-confidence, explainable cases.
Intermediate scores should enter the librarian's review queue. Low-confidence
records should remain separate rather than being forced into a person group.

### 4. Review and edit

The review UI should show both records side by side, the proposed group, the
score, the evidence fields, and the consequences of accepting or rejecting the
match. It should support keyboard-friendly decisions and bounded pages so a
librarian can work through a large archive incrementally.

Edits should be stored as explicit assertions with reviewer, timestamp,
decision, reason, and resolver version. A later rerun may produce a new
candidate score, but it must not erase a reviewed decision without an explicit
user action.

## Default rules

The initial defaults should be conservative and visible in the review output:

1. Normalize address comparison according to the archive's versioned address
   policy; do not apply provider-specific alias rules unless configured.
2. Retain every non-empty explicit display name observed for an address.
3. Prefer a complete, repeated explicit name over an inferred name.
4. Parse comma-reordered names and common initials, but label the transformation
   as derived evidence.
5. Generate local-part candidates from separators, concatenated alphabetic
   runs, and trailing numeric suffixes.
6. Use organization/domain continuity as supporting evidence only.
7. Down-weight role accounts, list addresses, automated senders, and addresses
   whose local parts contain no plausible name signal.
8. Require review for a merge supported only by an inferred name or a single
   weak observation.
9. Never delete an address, observation, source path, or message because a
   match is rejected.
10. Keep canonical names and alias groups in rebuildable derivative data, not
    in the canonical MBOX or source mailbox.

These are defaults, not universal truths. A librarian must be able to override
an incorrect automatic decision, and the override must remain traceable.

## Match-list design

The first reviewable export should contain at least:

| Field | Purpose |
| --- | --- |
| `match_id` | Stable identifier for the candidate assertion |
| `left_record` / `right_record` | Records being compared |
| `left_address` / `right_address` | Addresses shown to the reviewer |
| `left_name` / `right_name` | Best observed names, with access to all observations |
| `score` | Dedupe or deterministic score |
| `evidence` | Human-readable contributing signals |
| `suggested_group` | Proposed person-group identifier |
| `status` | `pending`, `accepted`, `rejected`, or `deferred` |
| `canonical_name` | Editable reviewed name for the group |
| `reviewer` / `reviewed_at` | Audit trail |
| `note` | Optional reason or source citation |
| `policy_version` / `model_version` | Reproducibility |

The UI should edit these records through typed application services. A YAML or
CSV export may be provided for bulk review or archival reporting, but a text
file should not be the only editing path.

## Open-source landscape

### Dedupe

[`dedupe`](https://github.com/dedupeio/dedupe) is the selected first engine. It
is a Python library for fuzzy matching, record deduplication, and entity
resolution. Its workflow uses human-labeled examples to learn useful weights
and blocking rules and is designed to cluster structured records. It is the
closest fit for a local bag of name/email records.

### Splink

[`Splink`](https://moj-analytical-services.github.io/splink/) is a strong
alternative for larger-scale probabilistic linkage. It predicts pairwise links
and clusters them into estimated entity IDs, with local DuckDB support and
larger SQL/Spark backends. Its documentation cautions that it works best with
multiple standardized fields rather than a single bag-of-words field. It may
be appropriate if the archive later needs very large-scale linkage or more
advanced model diagnostics.

### Python Record Linkage Toolkit

The [`Python Record Linkage Toolkit`](https://recordlinkage.readthedocs.io/en/latest/)
provides modular indexing, field comparison, classification, clustering, and
evaluation. It is useful if we want to assemble the entire policy ourselves,
but it supplies more of a toolkit than a librarian-ready review workflow.

### Zingg

[`Zingg`](https://github.com/zinggAI/zingg) is an ML-based entity-resolution and
master-data platform aimed at larger data-platform and Spark deployments. It
is likely excessive for the first local archive implementation, and its AGPL
license and platform-oriented operating model require a separate deployment
decision.

### Supporting parsers

[`nameparser`](https://github.com/derek73/python-nameparser) can parse an
already-observed name into given, family, title, suffix, and related fields. It
is useful for feature preparation and display-name normalization, but it does
not decide whether two email addresses belong to the same person. Email syntax
validators and RFC address parsers solve address validity and extraction, not
identity resolution.

## Implementation status and next steps

Current repository support:

* explicit display-name extraction from `From`, `To`, `Cc`, and `Bcc`;
* per-address search suggestions with deduplicated message counts;
* a YAML synthetic benchmark with expected person groups; and
* a Makefile benchmark command.

Not yet implemented:

* Dedupe dependency and adapter;
* a versioned resolver-input derivative;
* labeled match/non-match training data beyond the synthetic benchmark;
* candidate scoring and persistent person groups;
* match-list export and review persistence; and
* a librarian-facing review UI.

The next useful implementation slice is an offline adapter that runs Dedupe on
the synthetic benchmark and emits a typed, explainable match list. It should
be evaluated against the YAML benchmark before it is connected to a real
archive. No external profile lookup, web scraping, SMTP probing, or canonical
archive mutation is required for this design.
