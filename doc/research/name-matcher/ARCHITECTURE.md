<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Architecture

## Identity model

The central distinction is between evidence and conclusions:

```text
verified message bytes
   |-- header observation: address --uses-name--> name
   |-- signature block: sender --asserts-contact--> email/phone/URL/name/text
   |-- transport/list evidence: address --has-account-kind--> list/role/shared/person
   v
candidate pair --scored-by--> matcher/version/features
   v
review decision or conservative automatic decision
   v
person GUID <--temporal membership-- email/name/phone/URL
```

An observation has a message SHA-256, time, field or signature method, extractor
version, and confidence. A candidate score has a matcher and version. A review
decision has a reviewer, time, disposition, and optional note. These must never
be collapsed into one probability field whose meaning changes by stage.

The user's proposed tuple—email address, human name, person GUID, observation
time, source, and probability—is retained conceptually, but normalized. A person
GUID is nullable until resolution; source observations remain immutable; and
membership assertions have validity intervals and decision status. This supports
accounts shared concurrently or handed from one person to another.

Email memberships should usually be unique at a given time, subject to an
explicit `shared` account classification. Names are intentionally many-to-many:
the alias `Sim` can belong to several person GUIDs without merging those people.

## Why SQLite first

The data is logically a knowledge graph, but a graph server is not required for
the prototype. SQLite provides reproducible single-file experiments, mature
indexes, easy export, and no workstation service. The normalized schema retains
the graph semantics and can later export nodes and edges.

Graph backends remain experiments, not prerequisites. Neo4j Community Edition
is GPLv3 and runs as a standalone server; Apache AGE adds an Apache-licensed
property graph to PostgreSQL. DuckDB's current graph-query documentation calls
the DuckPGQ community extension experimental and notes version constraints, so
it is not the default persistence layer. A graph backend should win only if
collective/multi-hop inference produces a measurable quality or runtime benefit
that justifies its operational cost.

## Pipeline

### 1. Extract observations once

For each catalogued message, read its exact MBOX location with
`read_verified_location` and require its original SHA-256. Parse only a derived
copy. Record:

- every From/To/Cc/Bcc display-name and address occurrence with date and role;
- list and automation headers needed to classify receiving endpoints;
- a bounded candidate signature plus every retained line and typed contact fact;
- message/thread and address co-occurrence edges needed by collective matchers;
- extraction defects rather than silently dropping a message; and
- extractor name, version, and configuration.

Headers and signatures are different evidence channels. Ordinary body and
Subject names never become name-search terms. Signature facts may affect identity
resolution, but should not become accepted aliases merely because a detector
found them.

### 2. Learn repeated signatures without an LLM

The single-message heuristic is only a seed. Normalize tail lines conservatively,
remove quoted replies, and aggregate bottom-aligned n-grams per sending address.
Lines or blocks recurring across many messages from the same sender become
signature candidates. Down-weight corpus-wide legal disclaimers, mailing-list
footers, quotations, and blocks shared across many unrelated senders. Compare
the line heuristic, repeated-tail model, and a supervised line/zone classifier.

This stage is CPU- and I/O-oriented. It does not require a GPU or a generative
model.

### 3. Classify accounts before linking people

An address is not necessarily a person. Account kinds are temporal and include:

- `person`: predominantly one natural person;
- `mailing-list`: a receiving/distribution endpoint, supported by `List-*`,
  bulk precedence, repeated fan-out, and reply behavior;
- `role`: an organizational function such as `info@` or `president@`;
- `shared`: multiple simultaneous human senders;
- `automated`: software-generated traffic; and
- `unknown`.

A sending address with different stable signature/name regimes in disjoint time
windows is a handoff candidate. Overlapping regimes suggest simultaneous sharing.
Change-point detection should operate on dated name, signature, phone, and style
features. Stylometry is supporting evidence only; quotations, templates, and
short messages make it unsafe as sole evidence.

### 4. Generate candidates with blocking

All-pairs comparison of 219,074 addresses would require about 24 billion pairs.
Blocking must propose plausible pairs without turning weak similarities into
merges. Candidate blocks include:

- exact or compatible normalized multi-token names;
- name/local-part compatibility (`first.last`, concatenated names, initials);
- one sender's signature explicitly naming another observed sender address;
- a stable, discriminative phone or URL shared across signature regimes;
- reply/thread continuity and stable correspondent neighborhoods; and
- temporal succession for a shared or role address.

Common short names, shared surnames, domains, organizations, and co-recipiency
are blocking hints, never sufficient merge evidence.

### 5. Score with interchangeable engines

Every engine consumes the same frozen observation/candidate tables and emits the
same candidate-pair contract. Planned engines are:

1. deterministic precision-first rules;
2. Fellegi-Sunter/logistic scoring with calibrated probabilities;
3. Dedupe;
4. Splink on DuckDB;
5. a collective graph model using correspondent/thread context; and
6. optional LLM adjudication only for a bounded uncertainty band.

The LLM boundary is `scripts/name_matcher/ai.py`. Requests have stable IDs,
provider/model/prompt versions, pair IDs, and payload hashes. Results are joined
by request ID, not return order. Local models are the development default.
OpenAI and Gemini JSONL batch formats are represented, but this prototype does
not submit requests or send archive data over a network.

### 6. Review and cluster

Candidate pairs are sorted by score around two configurable thresholds:

- `score >= automatic_threshold`: merge, but show and permit undo;
- `review_threshold <= score < automatic_threshold`: human review; and
- below `review_threshold`: retain as an unmerged candidate when requested.

The per-session review budget defaults to 150 and is configurable. The review
matrix columns are identity 1, identity 2, calibrated match probability,
evidence explanation, time compatibility, account classification, and a
disposition control (`match`, `non-match`, `defer`, `undo`). Sampling should
include some above- and below-threshold pairs to measure silent errors, not just
the easiest uncertain cases.

Clusters are connected components of accepted equivalence edges only after
constraint checks. A new edge that would create incompatible simultaneous email
ownership or a rejected pair inside a cluster must be blocked for review. Every
merge and split is an append-only decision event; a materialized cluster view is
rebuildable.

## Search semantics

An email-address query expands through accepted temporal person memberships to
the other addresses of the same person. A name query finds all accepted person
clusters having that canonical name or alias, then expands to their addresses.
If `Sim` belongs to two people, both clusters are searched; they are not merged.

Name discovery is confined to From/To/Cc/Bcc identity data plus explicitly
reviewed signature/local-part inferences. Subject and ordinary body text are not
sources for name expansion. Search results still use their normal full-text
index; this restriction concerns identity expansion, not general body search.

## Parallel execution

MBOX generation is the natural extraction partition. Each worker:

1. opens the archive catalog read-only;
2. reads one generation sequentially by byte offset;
3. verifies every message hash;
4. writes an independent SQLite shard; and
5. reports defects without touching canonical data.

The coordinator merges completed shards deterministically, avoiding a global
SQLite write lock. Candidate feature computation partitions by blocking key,
pair scoring partitions by candidate ID, and signature-repetition aggregation
partitions by sender address. Cluster construction and constraint validation are
smaller deterministic reduce steps. No planned stage requires GPU acceleration.
