-- Nested sub-agent discovery: sidecar inputs join cache invalidation
-- Migration: 007
-- Description: Add a `subagents_fingerprint` column to `cached_files`.
-- Since #213, parsing a transcript also reads the sibling
-- `subagents/agent-*.meta.json` sidecars (spawn discovery); a sidecar
-- appearing AFTER the transcript was cached would otherwise go
-- unnoticed, because invalidation compared the source jsonl's mtime
-- only. The fingerprint (sidecar count + newest sidecar mtime) is
-- stored at save time and compared on every cache read.
--
-- Backward-compatible: existing rows get NULL via SQLite's column-add
-- default. A NULL fingerprint counts as valid only when the file has
-- no sidecars today — cached files WITH sidecars reparse once to pick
-- up the spawn links, everything else stays cached.

ALTER TABLE cached_files ADD COLUMN subagents_fingerprint TEXT;
