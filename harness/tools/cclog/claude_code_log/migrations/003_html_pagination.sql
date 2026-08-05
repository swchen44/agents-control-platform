-- HTML pagination for combined transcripts
-- Migration: 003
-- Description: Tracks page assignments for paginated combined transcript HTML files

-- Pages table: tracks each generated page file
CREATE TABLE IF NOT EXISTS html_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    page_number INTEGER NOT NULL,
    html_path TEXT NOT NULL,              -- e.g., "combined_transcripts.html" or "combined_transcripts_2.html"
    page_size_config INTEGER NOT NULL,    -- the --page-size value used
    message_count INTEGER NOT NULL,       -- total messages on this page
    first_session_id TEXT NOT NULL,
    last_session_id TEXT NOT NULL,
    first_timestamp TEXT,
    last_timestamp TEXT,
    total_input_tokens INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    total_cache_creation_tokens INTEGER DEFAULT 0,
    total_cache_read_tokens INTEGER DEFAULT 0,
    generated_at TEXT NOT NULL,           -- ISO timestamp when page was generated
    library_version TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(project_id, page_number)
);

-- Page-session mapping: tracks which sessions are on which page
CREATE TABLE IF NOT EXISTS page_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    session_order INTEGER NOT NULL,       -- order of session within the page
    FOREIGN KEY (page_id) REFERENCES html_pages(id) ON DELETE CASCADE,
    UNIQUE(page_id, session_id)
);

CREATE INDEX IF NOT EXISTS idx_html_pages_project ON html_pages(project_id);
CREATE INDEX IF NOT EXISTS idx_page_sessions_page ON page_sessions(page_id);
CREATE INDEX IF NOT EXISTS idx_page_sessions_session ON page_sessions(session_id);
