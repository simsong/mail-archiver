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

CREATE INDEX IF NOT EXISTS message_attachments_mime_type ON message_attachments(mime_type);
