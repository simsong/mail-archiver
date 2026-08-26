PRAGMA foreign_keys = ON;

CREATE TABLE schema_info (version INTEGER NOT NULL);
INSERT INTO schema_info(version) VALUES (1);

CREATE TABLE ingest_runs (
    run_pk INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    result TEXT,
    detail TEXT
);

CREATE TABLE email_addresses (
    address_pk INTEGER PRIMARY KEY,
    address TEXT NOT NULL UNIQUE
);

CREATE INDEX email_addresses_lower_address ON email_addresses(lower(address), address_pk);

CREATE TABLE messages (
    message_pk INTEGER PRIMARY KEY,
    message_id_normalized TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    sender_address_pk INTEGER NOT NULL REFERENCES email_addresses(address_pk),
    subject TEXT NOT NULL,
    date_utc TEXT NOT NULL,
    date_source TEXT NOT NULL,
    category TEXT NOT NULL,
    UNIQUE(message_id_normalized, sha256)
);

CREATE TABLE source_volumes (
    source_volume_pk INTEGER PRIMARY KEY,
    identity_json TEXT NOT NULL UNIQUE,
    metadata_json TEXT NOT NULL,
    first_observed_at TEXT NOT NULL,
    last_observed_at TEXT NOT NULL
);

CREATE TABLE source_files (
    source_file_pk INTEGER PRIMARY KEY,
    source_volume_pk INTEGER NOT NULL REFERENCES source_volumes(source_volume_pk),
    source_path TEXT NOT NULL,
    path_kind TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    modified_at_ns INTEGER,
    byte_length INTEGER,
    sha256 TEXT,
    checked_at TEXT,
    completed_run INTEGER REFERENCES ingest_runs(run_pk),
    UNIQUE(source_volume_pk, source_path)
);

CREATE TABLE observations (
    observation_pk INTEGER PRIMARY KEY,
    run_pk INTEGER NOT NULL REFERENCES ingest_runs(run_pk),
    message_pk INTEGER REFERENCES messages(message_pk),
    source_file_pk INTEGER NOT NULL REFERENCES source_files(source_file_pk),
    source_offset INTEGER NOT NULL DEFAULT 0,
    raw_sha256 TEXT NOT NULL DEFAULT '',
    semantic_sha256 TEXT,
    disposition TEXT NOT NULL,
    detail TEXT NOT NULL
);

CREATE TABLE metadata_defects (
    message_pk INTEGER NOT NULL REFERENCES messages(message_pk),
    field TEXT NOT NULL,
    detail TEXT NOT NULL,
    PRIMARY KEY (message_pk, field, detail)
);

CREATE TABLE recipients (
    message_pk INTEGER NOT NULL REFERENCES messages(message_pk),
    address_pk INTEGER NOT NULL REFERENCES email_addresses(address_pk),
    PRIMARY KEY (message_pk, address_pk)
);

CREATE TABLE mbox_generations (
    generation_pk INTEGER PRIMARY KEY,
    filename TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL,
    message_count INTEGER NOT NULL,
    byte_count INTEGER NOT NULL
);

CREATE TABLE locations (
    message_pk INTEGER PRIMARY KEY REFERENCES messages(message_pk),
    generation_pk INTEGER NOT NULL REFERENCES mbox_generations(generation_pk),
    byte_offset INTEGER NOT NULL,
    byte_length INTEGER NOT NULL
);

CREATE INDEX messages_sender_address_pk ON messages(sender_address_pk);
CREATE INDEX messages_sha256 ON messages(sha256);
CREATE INDEX messages_date_message ON messages(date_utc DESC, message_pk DESC);
CREATE INDEX messages_subject_message ON messages(lower(subject), message_pk);
CREATE INDEX messages_category_date_message ON messages(category, date_utc DESC, message_pk DESC);
CREATE INDEX messages_category_sender_address ON messages(category, sender_address_pk);
CREATE INDEX recipients_address_pk ON recipients(address_pk);
CREATE INDEX locations_generation_pk ON locations(generation_pk);
CREATE INDEX locations_generation_offset ON locations(generation_pk, byte_offset, byte_length);
CREATE INDEX source_files_volume_path ON source_files(source_volume_pk, source_path);
CREATE INDEX source_files_path_volume ON source_files(source_path, source_volume_pk);
CREATE INDEX observations_message_pk ON observations(message_pk);
CREATE INDEX observations_raw_sha256 ON observations(raw_sha256);
CREATE INDEX observations_semantic_sha256 ON observations(semantic_sha256);
CREATE INDEX observations_source_file_offset ON observations(source_file_pk, source_offset DESC);
CREATE INDEX observations_run_observation ON observations(run_pk, observation_pk);
