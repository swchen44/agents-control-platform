-- AI-generated session title
-- Migration: 006
-- Description: Add an `ai_title` column to `sessions` so the project
-- index, TUI, and session headers can surface Claude Code's curated
-- short title (emitted as `{"type":"ai-title", "aiTitle": <str>,
-- "sessionId": <id>}` JSONL entries). Multiple ai-title entries may
-- appear per session as the title is refined; the last one wins.
--
-- Backward-compatible: existing rows get NULL via SQLite's column-add
-- default (and `SessionCacheData.ai_title: Optional[str] = None`).
-- Old caches will simply not have ai-title populated until the next
-- cache rewrite for the affected project.

ALTER TABLE sessions ADD COLUMN ai_title TEXT;
