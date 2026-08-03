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
import threading
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


@dataclass
class TicketSession:
    issue_id: int
    key: str
    profile: str
    workspace: str
    session_id: str | None
    attempts: int
    outcome: str | None            # SUCCESS | FAILURE | UNKNOWN | None
    pending_reason: str | None     # human-decision | external | unknown | max-attempts
    cost_usd: float


class Store:
    def __init__(self, root: str):
        os.makedirs(root, exist_ok=True)
        self.db_path = os.path.join(root, "harness.db")
        self.journal_path = os.path.join(root, "events.jsonl")
        # parallel dispatch (conc.1): SQLite connections are NOT thread-safe.
        # dispatch is slow (tens of s); store ops are ms — a single lock
        # serializing all DB writes + journal appends costs nothing and keeps
        # the connection usable across dispatch threads.
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.db_path, check_same_thread=False)
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
        # v5 §4.4 TicketSession(子集):issue_id → workspace/session 對映
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS ticket_session (
                issue_id       INTEGER PRIMARY KEY,
                key            TEXT NOT NULL,
                profile        TEXT NOT NULL,
                workspace      TEXT NOT NULL,
                session_id     TEXT,
                attempts       INTEGER NOT NULL DEFAULT 0,
                outcome        TEXT,
                pending_reason TEXT,
                cost_usd       REAL NOT NULL DEFAULT 0
            )""")
        self._db.commit()

    def get(self, issue_id: int) -> TicketWatch | None:
        with self._lock:
            row = self._db.execute(
                "SELECT issue_id, key, last_comment_id, last_state,"
                " last_assignee_id, route_name FROM ticket_watch"
                " WHERE issue_id=?", (issue_id,)).fetchone()
        return TicketWatch(*row) if row else None

    def upsert(self, w: TicketWatch) -> None:
        # BEGIN IMMEDIATE: "查不到就建立" must be atomic (v5 D9)
        with self._lock, self._db:
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
        with self._lock:
            with open(self.journal_path, "a") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def get_session(self, issue_id: int) -> TicketSession | None:
        with self._lock:
            row = self._db.execute(
                "SELECT issue_id, key, profile, workspace, session_id,"
                " attempts, outcome, pending_reason, cost_usd FROM"
                " ticket_session WHERE issue_id=?", (issue_id,)).fetchone()
        return TicketSession(*row) if row else None

    def upsert_session(self, s: TicketSession) -> None:
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            self._db.execute("""
                INSERT INTO ticket_session
                    (issue_id, key, profile, workspace, session_id,
                     attempts, outcome, pending_reason, cost_usd)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(issue_id) DO UPDATE SET
                    key=excluded.key, profile=excluded.profile,
                    workspace=excluded.workspace,
                    session_id=excluded.session_id,
                    attempts=excluded.attempts, outcome=excluded.outcome,
                    pending_reason=excluded.pending_reason,
                    cost_usd=excluded.cost_usd
            """, (s.issue_id, s.key, s.profile, s.workspace, s.session_id,
                  s.attempts, s.outcome, s.pending_reason, s.cost_usd))

    def close(self) -> None:
        self._db.close()
