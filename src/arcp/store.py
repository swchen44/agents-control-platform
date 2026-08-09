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
    last_assignee: str = ""        # W4.1:displayName(dashboard 顯示用)
    summary: str = ""              # W4.7:dashboard 過濾/顯示用
    description: str = ""          # W4.7:過濾用(截 2000 字)


@dataclass
class TicketSession:
    issue_id: int
    key: str
    profile: str
    workspace: str
    session_id: str | None
    attempts: int
    outcome: str | None            # SUCCESS | FAILURE | UNKNOWN | None
    pending_reason: str | None     # human-decision|external|unknown|max-attempts|budget
    cost_usd: float
    queued: bool = False           # F1:本輪額滿排隊(下輪重評)
    queued_at: float = 0.0         # FIFO 排序時間
    inactive: bool = False         # DESIGN §6:assignee 不在機器人手上→不占額度(W2 置位)
    approval_revisions: int = 0    # W2.3 審批退回重填次數
    finished_at: float = 0.0       # W3.3:進終態時間戳(retention 回收判定基準)
    evict_count: int = 0           # W6.3:強制驅逐次數(異常處理健康指標)
    clearquest_id: str | None = None  # W7(R8/R9):ClearQuest CR id(現預留,CQ 監控 To-Do)
    human_score: int | None = None    # W7(R1):人類完成度評分 0-10(None=未評分)
    score_reminded_at: float = 0.0    # W7(R1):上次催評分時間(每票每 ~1h 一次)
    agent_score: int | None = None    # agent 自評 0-10(contract.score;auto_close 複製)
    base_ref: str | None = None       # W10.3:跨票 base 子票的來源票 issue_id(字串);
    #                                    dispatcher 首次佈建注入 base 脈絡後清為 None


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
                cost_usd       REAL NOT NULL DEFAULT 0,
                queued         INTEGER NOT NULL DEFAULT 0,
                queued_at      REAL NOT NULL DEFAULT 0,
                inactive       INTEGER NOT NULL DEFAULT 0,
                approval_revisions INTEGER NOT NULL DEFAULT 0,
                finished_at    REAL NOT NULL DEFAULT 0,
                clearquest_id  TEXT,
                human_score    INTEGER,
                score_reminded_at REAL NOT NULL DEFAULT 0,
                base_ref       TEXT,
                agent_score    INTEGER
            )""")
        # W3.4:內部觸發源(scheduled)的 last_run 水位
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS trigger_state (
                name      TEXT PRIMARY KEY,
                last_run  REAL NOT NULL DEFAULT 0,
                run_count INTEGER NOT NULL DEFAULT 0
            )""")
        # W11.2:互動請求(一次性 token 表單)。新表,不動既有 table。
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS interactions (
                request_id     TEXT PRIMARY KEY,
                token          TEXT NOT NULL UNIQUE,
                issue_id       INTEGER NOT NULL,
                key            TEXT NOT NULL,
                schema_id      TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                created_at     REAL NOT NULL,
                expires_at     REAL NOT NULL DEFAULT 0,
                status         TEXT NOT NULL DEFAULT 'pending',
                payload        TEXT,
                submission     TEXT,
                submitted_at   REAL NOT NULL DEFAULT 0,
                submitted_by   TEXT NOT NULL DEFAULT '',
                reminders      INTEGER NOT NULL DEFAULT 0,
                reminded_at    REAL NOT NULL DEFAULT 0
            )""")
        self._db.execute("CREATE INDEX IF NOT EXISTS ix_interactions_issue "
                         "ON interactions(issue_id)")
        self._migrate()
        self._db.commit()

    def _migrate(self) -> None:
        """Add F1 columns to a pre-existing ticket_session (SQLite has no
        ADD COLUMN IF NOT EXISTS)."""
        cols = {r[1] for r in self._db.execute(
            "PRAGMA table_info(ticket_session)")}
        for name, ddl in (("queued", "INTEGER NOT NULL DEFAULT 0"),
                          ("queued_at", "REAL NOT NULL DEFAULT 0"),
                          ("inactive", "INTEGER NOT NULL DEFAULT 0"),
                          ("approval_revisions",
                           "INTEGER NOT NULL DEFAULT 0"),
                          ("finished_at", "REAL NOT NULL DEFAULT 0"),
                          ("evict_count", "INTEGER NOT NULL DEFAULT 0"),
                          ("clearquest_id", "TEXT"),      # W7(R8/R9)
                          ("human_score", "INTEGER"),     # W7(R1)
                          ("score_reminded_at",
                           "REAL NOT NULL DEFAULT 0"),    # W7(R1)
                          ("base_ref", "TEXT"),           # W10.3 跨票 base
                          ("agent_score", "INTEGER")):    # contract.score
            if name not in cols:
                self._db.execute(
                    f"ALTER TABLE ticket_session ADD COLUMN {name} {ddl}")
        wcols = {r[1] for r in self._db.execute(
            "PRAGMA table_info(ticket_watch)")}
        for name in ("last_assignee",                  # W4.1 dashboard 顯示
                     "summary", "description"):        # W4.7 過濾用
            if name not in wcols:
                self._db.execute(f"ALTER TABLE ticket_watch ADD COLUMN"
                                 f" {name} TEXT NOT NULL DEFAULT ''")
        tcols = {r[1] for r in self._db.execute(
            "PRAGMA table_info(trigger_state)")}
        if "run_count" not in tcols:               # jobs P2:次數上限記數
            self._db.execute("ALTER TABLE trigger_state ADD COLUMN"
                             " run_count INTEGER NOT NULL DEFAULT 0")

    def get(self, issue_id: int) -> TicketWatch | None:
        with self._lock:
            row = self._db.execute(
                "SELECT issue_id, key, last_comment_id, last_state,"
                " last_assignee_id, route_name, last_assignee, summary,"
                " description"
                " FROM ticket_watch WHERE issue_id=?", (issue_id,)).fetchone()
        return TicketWatch(*row) if row else None

    def upsert(self, w: TicketWatch) -> None:
        # BEGIN IMMEDIATE: "查不到就建立" must be atomic (v5 D9)
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            self._db.execute("""
                INSERT INTO ticket_watch
                    (issue_id, key, last_comment_id, last_state,
                     last_assignee_id, route_name, first_seen_ts,
                     last_assignee, summary, description)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(issue_id) DO UPDATE SET
                    key=excluded.key,
                    last_comment_id=excluded.last_comment_id,
                    last_state=excluded.last_state,
                    last_assignee_id=excluded.last_assignee_id,
                    route_name=excluded.route_name,
                    last_assignee=excluded.last_assignee,
                    summary=excluded.summary,
                    description=excluded.description
            """, (w.issue_id, w.key, w.last_comment_id, w.last_state,
                  w.last_assignee_id, w.route_name, time.time(),
                  w.last_assignee, w.summary, w.description[:2000]))

    def journal(self, event_type: str, issue_id: int, key: str,
                **fields) -> dict:
        event = {"ts": time.time(), "type": event_type,
                 "issue_id": issue_id, "key": key, **fields}
        with self._lock:
            with open(self.journal_path, "a") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def monthly_cost(self, profile: str, now: float | None = None) -> float:
        """W7.3:profile 當日曆月(跨所有票)累計花費 = sum(attempt_finished.cost)。
        資料源是 journal(帶 ts+cost+profile);簡單掃檔,量大再改月帳表。"""
        import datetime
        now = time.time() if now is None else now
        ref = datetime.datetime.fromtimestamp(now)
        total = 0.0
        try:
            with open(self.journal_path) as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if (e.get("type") != "attempt_finished"
                            or e.get("profile") != profile or not e.get("cost")):
                        continue
                    edt = datetime.datetime.fromtimestamp(e.get("ts") or 0)
                    if edt.year == ref.year and edt.month == ref.month:
                        total += float(e["cost"])
        except OSError:
            pass
        return total

    _SESSION_COLS = ("issue_id, key, profile, workspace, session_id, attempts,"
                     " outcome, pending_reason, cost_usd, queued, queued_at,"
                     " inactive, approval_revisions, finished_at, evict_count,"
                     " clearquest_id, human_score, score_reminded_at, base_ref,"
                     " agent_score")

    @staticmethod
    def _row_to_session(row) -> TicketSession:
        return TicketSession(
            issue_id=row[0], key=row[1], profile=row[2], workspace=row[3],
            session_id=row[4], attempts=row[5], outcome=row[6],
            pending_reason=row[7], cost_usd=row[8], queued=bool(row[9]),
            queued_at=row[10], inactive=bool(row[11]),
            approval_revisions=row[12], finished_at=row[13],
            evict_count=row[14], clearquest_id=row[15],
            human_score=row[16], score_reminded_at=row[17] or 0.0,
            base_ref=row[18], agent_score=row[19])

    def get_session(self, issue_id: int) -> TicketSession | None:
        with self._lock:
            row = self._db.execute(
                f"SELECT {self._SESSION_COLS} FROM ticket_session"
                " WHERE issue_id=?", (issue_id,)).fetchone()
        return self._row_to_session(row) if row else None

    def active_sessions(self) -> list[TicketSession]:
        """In-flight = 占機器額度的 session:非終態(outcome IS NULL)且不在等待
        (pending_reason IS NULL)且 active(inactive=0)且未排隊(queued=0)。
        W8:pending/inactive/queued/終態都不占額度。"""
        with self._lock:
            rows = self._db.execute(
                f"SELECT {self._SESSION_COLS} FROM ticket_session"
                " WHERE outcome IS NULL AND pending_reason IS NULL"
                " AND inactive=0 AND queued=0").fetchall()
        return [self._row_to_session(r) for r in rows]

    def all_sessions(self) -> list[TicketSession]:
        """全部 session(控制面/dashboard 彙總用),issue_id 排序。"""
        with self._lock:
            rows = self._db.execute(
                f"SELECT {self._SESSION_COLS} FROM ticket_session"
                " ORDER BY issue_id").fetchall()
        return [self._row_to_session(r) for r in rows]

    def upsert_session(self, s: TicketSession) -> None:
        # W3.3:終態時間戳由 store 統一蓋章(所有寫入路徑都經這裡——dispatcher/
        # commands/external policy 不必各自記);outcome 清空(retry)→ 歸零
        if s.outcome in ("SUCCESS", "ABORTED", "FAILURE", "UNKNOWN"):
            if not s.finished_at:
                s.finished_at = time.time()
        else:
            s.finished_at = 0.0
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            self._db.execute("""
                INSERT INTO ticket_session
                    (issue_id, key, profile, workspace, session_id,
                     attempts, outcome, pending_reason, cost_usd,
                     queued, queued_at, inactive, approval_revisions,
                     finished_at, evict_count, clearquest_id,
                     human_score, score_reminded_at, base_ref, agent_score)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(issue_id) DO UPDATE SET
                    key=excluded.key, profile=excluded.profile,
                    workspace=excluded.workspace,
                    session_id=excluded.session_id,
                    attempts=excluded.attempts, outcome=excluded.outcome,
                    pending_reason=excluded.pending_reason,
                    cost_usd=excluded.cost_usd, queued=excluded.queued,
                    queued_at=excluded.queued_at, inactive=excluded.inactive,
                    approval_revisions=excluded.approval_revisions,
                    finished_at=excluded.finished_at,
                    evict_count=excluded.evict_count,
                    clearquest_id=excluded.clearquest_id,
                    human_score=excluded.human_score,
                    score_reminded_at=excluded.score_reminded_at,
                    base_ref=excluded.base_ref,
                    agent_score=excluded.agent_score
            """, (s.issue_id, s.key, s.profile, s.workspace, s.session_id,
                  s.attempts, s.outcome, s.pending_reason, s.cost_usd,
                  int(s.queued), s.queued_at, int(s.inactive),
                  s.approval_revisions, s.finished_at, s.evict_count,
                  s.clearquest_id, s.human_score, s.score_reminded_at,
                  s.base_ref, s.agent_score))

    # -- W3.4 trigger last_run 水位 ---------------------------------------- #
    def trigger_last_run(self, name: str) -> float:
        with self._lock:
            row = self._db.execute(
                "SELECT last_run FROM trigger_state WHERE name=?",
                (name,)).fetchone()
        return row[0] if row else 0.0

    def set_trigger_last_run(self, name: str, ts: float) -> None:
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            self._db.execute("""
                INSERT INTO trigger_state (name, last_run) VALUES (?,?)
                ON CONFLICT(name) DO UPDATE SET last_run=excluded.last_run
            """, (name, ts))

    def trigger_run_count(self, name: str) -> int:
        """job 已觸發幾次(count 上限用)。"""
        with self._lock:
            row = self._db.execute(
                "SELECT run_count FROM trigger_state WHERE name=?",
                (name,)).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def bump_trigger_run(self, name: str, ts: float) -> None:
        """記水位 + run_count+1(at-most-once:先記再跑)。"""
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            self._db.execute("""
                INSERT INTO trigger_state (name, last_run, run_count)
                VALUES (?,?,1)
                ON CONFLICT(name) DO UPDATE SET last_run=excluded.last_run,
                    run_count=trigger_state.run_count+1
            """, (name, ts))

    # -- W11.2 互動請求(一次性 token 表單)------------------------------------ #
    _IX_COLS = ("request_id, token, issue_id, key, schema_id, schema_version,"
                " created_at, expires_at, status, payload, submission,"
                " submitted_at, submitted_by, reminders, reminded_at")

    @staticmethod
    def _row_to_interaction(row):
        from .interaction import InteractionRequest
        return InteractionRequest(
            request_id=row[0], token=row[1], issue_id=row[2], key=row[3],
            schema_id=row[4], schema_version=row[5], created_at=row[6],
            expires_at=row[7], status=row[8],
            payload=json.loads(row[9]) if row[9] else {},
            submission=json.loads(row[10]) if row[10] else None,
            submitted_at=row[11], submitted_by=row[12],
            reminders=row[13], reminded_at=row[14])

    def upsert_interaction(self, r) -> None:
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            self._db.execute("""
                INSERT INTO interactions
                    (request_id, token, issue_id, key, schema_id,
                     schema_version, created_at, expires_at, status, payload,
                     submission, submitted_at, submitted_by, reminders,
                     reminded_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(request_id) DO UPDATE SET
                    status=excluded.status, payload=excluded.payload,
                    submission=excluded.submission,
                    submitted_at=excluded.submitted_at,
                    submitted_by=excluded.submitted_by,
                    reminders=excluded.reminders,
                    reminded_at=excluded.reminded_at
            """, (r.request_id, r.token, r.issue_id, r.key, r.schema_id,
                  r.schema_version, r.created_at, r.expires_at, r.status,
                  json.dumps(r.payload, ensure_ascii=False),
                  json.dumps(r.submission, ensure_ascii=False)
                  if r.submission is not None else None,
                  r.submitted_at, r.submitted_by, r.reminders, r.reminded_at))

    def get_interaction(self, token: str):
        """依 token 取請求(表單服務入口用);查無回 None。"""
        with self._lock:
            row = self._db.execute(
                f"SELECT {self._IX_COLS} FROM interactions WHERE token=?",
                (token,)).fetchone()
        return self._row_to_interaction(row) if row else None

    def interactions_for_ticket(self, issue_id: int) -> list:
        with self._lock:
            rows = self._db.execute(
                f"SELECT {self._IX_COLS} FROM interactions WHERE issue_id=?"
                " ORDER BY created_at", (int(issue_id),)).fetchall()
        return [self._row_to_interaction(r) for r in rows]

    def open_interactions_for_ticket(self, issue_id: int, now=None) -> list:
        """該票仍 pending 且未逾期的請求(催辦 / 觸發偵測用)。"""
        return [r for r in self.interactions_for_ticket(issue_id)
                if r.is_open(now)]

    def close(self) -> None:
        self._db.close()
