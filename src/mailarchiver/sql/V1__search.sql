PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_info (version INTEGER NOT NULL);
INSERT INTO schema_info(version) SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_info);

CREATE VIRTUAL TABLE IF NOT EXISTS message_fts USING fts5(sha256 UNINDEXED, content);
CREATE VIRTUAL TABLE IF NOT EXISTS attachment_fts USING fts5(sha256 UNINDEXED, content);

CREATE TABLE IF NOT EXISTS message_metadata (
    sha256 TEXT PRIMARY KEY,
    message_fts_rowid INTEGER NOT NULL UNIQUE,
    attachment_fts_rowid INTEGER UNIQUE,
    attachment_count INTEGER NOT NULL CHECK (attachment_count >= 0),
    preview TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS message_attachments (
    sha256 TEXT NOT NULL REFERENCES message_metadata(sha256) ON DELETE CASCADE,
    attachment_ordinal INTEGER NOT NULL CHECK (attachment_ordinal > 0),
    part_id INTEGER NOT NULL CHECK (part_id >= 0),
    filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    PRIMARY KEY (sha256, attachment_ordinal)
);

CREATE TABLE IF NOT EXISTS address_suggestions (
    suggestion_pk INTEGER PRIMARY KEY,
    address TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    message_count INTEGER NOT NULL CHECK (message_count >= 0)
);

CREATE TABLE IF NOT EXISTS message_address_suggestions (
    sha256 TEXT NOT NULL REFERENCES message_metadata(sha256) ON DELETE CASCADE,
    suggestion_pk INTEGER NOT NULL REFERENCES address_suggestions(suggestion_pk),
    PRIMARY KEY(sha256, suggestion_pk)
);

CREATE VIRTUAL TABLE IF NOT EXISTS address_suggestion_fts USING fts5(
    address,
    content='address_suggestions',
    content_rowid='suggestion_pk',
    tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS address_suggestions_insert AFTER INSERT ON address_suggestions BEGIN
    INSERT INTO address_suggestion_fts(rowid, address) VALUES (new.suggestion_pk, new.address);
END;

CREATE TRIGGER IF NOT EXISTS address_suggestions_delete AFTER DELETE ON address_suggestions BEGIN
    INSERT INTO address_suggestion_fts(address_suggestion_fts, rowid, address)
    VALUES ('delete', old.suggestion_pk, old.address);
END;

CREATE INDEX IF NOT EXISTS message_attachments_mime_type ON message_attachments(mime_type);
