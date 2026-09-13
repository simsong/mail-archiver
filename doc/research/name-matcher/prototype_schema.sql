-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

PRAGMA foreign_keys = ON;

CREATE TABLE extraction_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    archive_catalog TEXT NOT NULL,
    extractor_name TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    configuration_yaml TEXT NOT NULL
);

CREATE TABLE messages (
    catalog_message_pk INTEGER PRIMARY KEY,
    message_sha256 TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    mbox_filename TEXT NOT NULL
);

CREATE TABLE header_observations (
    catalog_message_pk INTEGER NOT NULL REFERENCES messages(catalog_message_pk),
    role TEXT NOT NULL CHECK (role IN ('from', 'to', 'cc', 'bcc')),
    ordinal INTEGER NOT NULL,
    address TEXT NOT NULL,
    display_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    PRIMARY KEY (catalog_message_pk, role, ordinal, address)
);

CREATE INDEX messages_sha256 ON messages(message_sha256);
CREATE INDEX header_observations_address_name ON header_observations(address, normalized_name);

CREATE TABLE message_markers (
    catalog_message_pk INTEGER NOT NULL REFERENCES messages(catalog_message_pk),
    header_name TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (catalog_message_pk, header_name, ordinal)
);

CREATE INDEX message_markers_header_value ON message_markers(header_name, value);

CREATE TABLE signature_blocks (
    catalog_message_pk INTEGER NOT NULL REFERENCES messages(catalog_message_pk),
    block_ordinal INTEGER NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    method TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    text_sha256 TEXT NOT NULL,
    block_text TEXT NOT NULL,
    PRIMARY KEY (catalog_message_pk, block_ordinal)
);

CREATE TABLE signature_facts (
    catalog_message_pk INTEGER NOT NULL,
    block_ordinal INTEGER NOT NULL,
    fact_ordinal INTEGER NOT NULL,
    line_ordinal INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('email', 'name-candidate', 'phone', 'text', 'url')),
    value TEXT NOT NULL,
    normalized_value TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    PRIMARY KEY (catalog_message_pk, block_ordinal, fact_ordinal),
    FOREIGN KEY (catalog_message_pk, block_ordinal) REFERENCES signature_blocks(catalog_message_pk, block_ordinal)
);

CREATE INDEX signature_facts_kind_value ON signature_facts(kind, normalized_value);

CREATE TABLE extraction_errors (
    catalog_message_pk INTEGER PRIMARY KEY,
    message_sha256 TEXT NOT NULL,
    stage TEXT NOT NULL,
    error_type TEXT NOT NULL,
    detail TEXT NOT NULL
);

-- The tables below separate matcher output and review decisions from evidence.
CREATE TABLE person_clusters (
    person_guid TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    superseded_by TEXT REFERENCES person_clusters(person_guid)
);

CREATE TABLE identity_memberships (
    person_guid TEXT NOT NULL REFERENCES person_clusters(person_guid),
    identifier_kind TEXT NOT NULL CHECK (identifier_kind IN ('email', 'name', 'phone', 'url')),
    identifier_value TEXT NOT NULL,
    valid_from TEXT NOT NULL DEFAULT '',
    valid_through TEXT NOT NULL DEFAULT '',
    probability REAL NOT NULL CHECK (probability >= 0.0 AND probability <= 1.0),
    source TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('automatic', 'reviewed', 'rejected', 'superseded')),
    PRIMARY KEY (person_guid, identifier_kind, identifier_value, valid_from, source)
);

CREATE TABLE candidate_pairs (
    candidate_guid TEXT PRIMARY KEY,
    identity_1 TEXT NOT NULL,
    identity_2 TEXT NOT NULL,
    matcher TEXT NOT NULL,
    matcher_version TEXT NOT NULL,
    probability REAL NOT NULL CHECK (probability >= 0.0 AND probability <= 1.0),
    evidence_yaml TEXT NOT NULL,
    proposed_at TEXT NOT NULL,
    UNIQUE (identity_1, identity_2, matcher, matcher_version)
);

CREATE TABLE review_decisions (
    decision_pk INTEGER PRIMARY KEY,
    candidate_guid TEXT NOT NULL REFERENCES candidate_pairs(candidate_guid),
    disposition TEXT NOT NULL CHECK (disposition IN ('match', 'non-match', 'defer', 'undo')),
    reviewer TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    note TEXT NOT NULL
);

CREATE TABLE account_classifications (
    address TEXT NOT NULL,
    valid_from TEXT NOT NULL DEFAULT '',
    valid_through TEXT NOT NULL DEFAULT '',
    account_kind TEXT NOT NULL CHECK (account_kind IN ('person', 'mailing-list', 'role', 'shared', 'automated', 'unknown')),
    probability REAL NOT NULL CHECK (probability >= 0.0 AND probability <= 1.0),
    source TEXT NOT NULL,
    PRIMARY KEY (address, valid_from, source)
);
