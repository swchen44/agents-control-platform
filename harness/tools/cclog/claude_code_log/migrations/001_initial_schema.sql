-- Initial schema for SQLite cache
-- Migration: 001
-- Description: Creates all tables and indexes for the cache system

-- Project metadata
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_path TEXT UNIQUE NOT NULL,
    version TEXT NOT NULL,
    cache_created TEXT NOT NULL,
    last_updated TEXT NOT NULL,
    total_message_count INTEGER DEFAULT 0,
    total_input_tokens INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    total_cache_creation_tokens INTEGER DEFAULT 0,
    total_cache_read_tokens INTEGER DEFAULT 0,
    earliest_timestamp TEXT DEFAULT '',
    latest_timestamp TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_projects_path ON projects(project_path);

-- File tracking for invalidation
CREATE TABLE IF NOT EXISTS cached_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    source_mtime REAL NOT NULL,
    cached_mtime REAL NOT NULL,
    message_count INTEGER DEFAULT 0,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(project_id, file_name)
);

CREATE INDEX IF NOT EXISTS idx_cached_files_project ON cached_files(project_id);
CREATE INDEX IF NOT EXISTS idx_cached_files_name ON cached_files(file_name);

-- Session aggregates
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    summary TEXT,
    first_timestamp TEXT NOT NULL DEFAULT '',
    last_timestamp TEXT NOT NULL DEFAULT '',
    message_count INTEGER DEFAULT 0,
    first_user_message TEXT DEFAULT '',
    cwd TEXT,
    total_input_tokens INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    total_cache_creation_tokens INTEGER DEFAULT 0,
    total_cache_read_tokens INTEGER DEFAULT 0,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(project_id, session_id)
);

CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id);
CREATE INDEX IF NOT EXISTS idx_sessions_session_id ON sessions(session_id);
CREATE INDEX IF NOT EXISTS idx_sessions_first_timestamp ON sessions(first_timestamp);
CREATE INDEX IF NOT EXISTS idx_sessions_cwd ON sessions(cwd);

-- Fully normalised messages
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    file_id INTEGER NOT NULL,

    -- Core fields
    type TEXT NOT NULL,
    timestamp TEXT,
    session_id TEXT,

    -- BaseTranscriptEntry fields (prefixed)
    _uuid TEXT,
    _parent_uuid TEXT,
    _is_sidechain INTEGER DEFAULT 0,
    _user_type TEXT,
    _cwd TEXT,
    _version TEXT,
    _is_meta INTEGER,
    _agent_id TEXT,

    -- AssistantTranscriptEntry
    _request_id TEXT,

    -- Flattened usage tokens
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_creation_tokens INTEGER,
    cache_read_tokens INTEGER,

    -- SummaryTranscriptEntry
    _leaf_uuid TEXT,

    -- SystemTranscriptEntry
    _level TEXT,

    -- QueueOperationTranscriptEntry
    _operation TEXT,

    -- Message content as compressed JSON (zlib)
    content BLOB NOT NULL,

    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (file_id) REFERENCES cached_files(id) ON DELETE CASCADE
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_project_timestamp ON messages(project_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_file ON messages(file_id);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_uuid ON messages(_uuid);
