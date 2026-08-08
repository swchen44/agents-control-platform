-- HTML cache for incremental regeneration
-- Migration: 002
-- Description: Tracks when HTML files were generated to enable incremental regeneration

CREATE TABLE IF NOT EXISTS html_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    html_path TEXT NOT NULL,          -- e.g., "session-abc123.html" or "combined_transcripts.html"
    generated_at TEXT NOT NULL,       -- ISO timestamp when HTML was generated
    source_session_id TEXT,           -- session_id for individual files, NULL for combined
    message_count INTEGER,            -- for sanity checking
    library_version TEXT NOT NULL,    -- which version generated it
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(project_id, html_path)
);

CREATE INDEX IF NOT EXISTS idx_html_cache_project ON html_cache(project_id);
CREATE INDEX IF NOT EXISTS idx_html_cache_session ON html_cache(source_session_id);
