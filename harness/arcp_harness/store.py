"""Harness persistence (v5 D9): SQLite, WAL, IMMEDIATE transactions.

Keys are NUMERIC issue ids (v5 C3). Two responsibilities today:
  - per-ticket watch state (comment watermark, last seen state/assignee)
    so polling is idempotent — old comments are never replayed (v5 §6-8/9)
  - append-only journal (events.jsonl) for route_matched / change events;
    same audit-trail idea as the A-route PoC's journal

Phase 2 adds workspace/session mapping columns (create_or_resume).
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass


@dataclass
class TicketWatch:
    issue_id: int
    key: str
    last_comment_id: int
    last_state: str
    last_assignee_id: str
    route_name: str | None


class Store:
    def __init__(self, root: str):
        os.makedirs(root, exist_ok=True)
        self.db_path = os.path.join(root, "harness.db")
        self.journal_path = os.path.join(root, "events.jsonl")
        self._db = sqlite3.connect(self.db_path)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS ticket_watch (
                issue_id         INTEGER PRIMARY KEY,
                key              TEXT NOT NULL,
                last_comment_id  INTEGER NOT NULL DEFAULT 0,
                last_state       TEXT NOT NULL DEFAULT '',
                last_assignee_id TEXT NOT NULL DEFAULT '',
                route_name       TEXT,
                first_seen_ts    REAL NOT NULL
            )""")
        self._db.commit()

    def get(self, issue_id: int) -> TicketWatch | None:
        row = self._db.execute(
            "SELECT issue_id, key, last_comment_id, last_state,"
            " last_assignee_id, route_name FROM ticket_watch WHERE issue_id=?",
            (issue_id,)).fetchone()
        return TicketWatch(*row) if row else None

    def upsert(self, w: TicketWatch) -> None:
        # BEGIN IMMEDIATE: "查不到就建立" must be atomic (v5 D9)
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            self._db.execute("""
                INSERT INTO ticket_watch
                    (issue_id, key, last_comment_id, last_state,
                     last_assignee_id, route_name, first_seen_ts)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(issue_id) DO UPDATE SET
                    key=excluded.key,
                    last_comment_id=excluded.last_comment_id,
                    last_state=excluded.last_state,
                    last_assignee_id=excluded.last_assignee_id,
                    route_name=excluded.route_name
            """, (w.issue_id, w.key, w.last_comment_id, w.last_state,
                  w.last_assignee_id, w.route_name, time.time()))

    def journal(self, event_type: str, issue_id: int, key: str,
                **fields) -> dict:
        event = {"ts": time.time(), "type": event_type,
                 "issue_id": issue_id, "key": key, **fields}
        with open(self.journal_path, "a") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def close(self) -> None:
        self._db.close()
