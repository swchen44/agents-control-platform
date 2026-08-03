"""Comment command channel + external-change policy (v5 §4.1, §6-10~14).

人 → agent 的唯一指令通道是 Jira comment。冪等由 watermark 保證(每則
comment 只以 comment_added 事件出現一次);自家 [agent] 前綴留言不解析
(防迴圈);不在白名單的指令會收到明確拒絕(§6-13);不認得的 @agent
指令也要回覆(§6-14——否則人以為下了指令其實沒生效)。

指令語意(對 ticket_session 的狀態操作;dispatch 由同一輪 poll 稍後執行):
  run    解除 pending、續跑(pending 的人工解除機制——含 pending:unknown)
  retry  歸零 attempts + 解除 pending,從頭再試
  stop   交還人工:pending:human-decision
  cancel 撤銷:outcome=ABORTED,此後不再派工

External-change policy(v5 §6-10/11):
  status → 終止類狀態(人在看板上直接關票)= out-of-band 撤銷 → ABORTED
  assignee 改走 = 隱含撤銷授權 → pending:external
"""

from __future__ import annotations

import re

from .jira_source import JiraCloudSource
from .store import Store
from .ticket import Comment, Ticket

_COMMANDS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)^@agent\s+run\b"), "run"),
    (re.compile(r"(?i)^@agent\s+(stop|hold|pause)\b"), "stop"),
    (re.compile(r"(?i)^@agent\s+retry\b"), "retry"),
    (re.compile(r"(?i)^@agent\s+cancel\b"), "cancel"),
]
_GENERIC = re.compile(r"(?i)^@agent\b")

HELP = ("[agent] 不認得這個指令。可用:@agent run|retry|stop|cancel")
DENIED = "[agent] 未授權:你的帳號不在指令白名單(commands.allowed_commenters)"


def parse(body: str) -> str | None:
    """None = 不是指令;'unknown' = @agent 開頭但不認得。"""
    body = body.strip()
    if body.startswith("[agent]"):
        return None               # 自家留言,防迴圈
    for pattern, name in _COMMANDS:
        if pattern.match(body):
            return name
    return "unknown" if _GENERIC.match(body) else None


class CommandHandler:
    def __init__(self, source: JiraCloudSource, store: Store,
                 allowed_commenters: list[str]):
        self.source = source
        self.store = store
        self.allowed = allowed_commenters

    def _authorized(self, c: Comment) -> bool:
        return (c.author in self.allowed) or (c.author_id in self.allowed)

    def handle(self, t: Ticket, c: Comment) -> list[dict]:
        cmd = parse(c.body)
        if cmd is None:
            return []
        if not self._authorized(c):
            self.source.add_comment(t.id, DENIED)
            return [self.store.journal("command_denied", t.id, t.key,
                                       command=cmd, author=c.author)]
        if cmd == "unknown":
            self.source.add_comment(t.id, HELP)
            return [self.store.journal("command_unknown", t.id, t.key,
                                       body=c.body[:80])]

        sess = self.store.get_session(t.id)
        if sess is None:
            self.source.add_comment(
                t.id, f"[agent] ack: {cmd}(此票尚無 session;"
                      f"run/retry 會在路由命中時生效)")
            return [self.store.journal("command_accepted", t.id, t.key,
                                       command=cmd, note="no-session")]
        if cmd in ("run", "retry"):
            if cmd == "retry":
                sess.attempts = 0
            sess.outcome, sess.pending_reason = None, None
        elif cmd == "stop":
            sess.pending_reason = "human-decision"
        elif cmd == "cancel":
            sess.outcome, sess.pending_reason = "ABORTED", None
        self.store.upsert_session(sess)
        self.source.add_comment(t.id, f"[agent] ack: {cmd}")
        return [self.store.journal("command_accepted", t.id, t.key,
                                   command=cmd, author=c.author)]


class ExternalChangePolicy:
    def __init__(self, source: JiraCloudSource, store: Store,
                 cancel_states: list[str]):
        self.source = source
        self.store = store
        self.cancel_states = cancel_states

    def on_status_changed(self, t: Ticket, new_state: str) -> list[dict]:
        sess = self.store.get_session(t.id)
        if (new_state in self.cancel_states and sess
                and sess.outcome not in ("SUCCESS", "ABORTED")):
            sess.outcome, sess.pending_reason = "ABORTED", None
            self.store.upsert_session(sess)
            return [self.store.journal("external_abort", t.id, t.key,
                                       state=new_state)]
        return []

    def on_assignee_changed(self, t: Ticket) -> list[dict]:
        sess = self.store.get_session(t.id)
        if sess and sess.outcome is None:  # 進行中被改走 = 撤銷授權
            sess.pending_reason = "external"
            self.store.upsert_session(sess)
            self.source.add_comment(
                t.id, "[agent] assignee 變更 → 視為撤銷授權,暫停。"
                      "要繼續請留言 @agent run")
            return [self.store.journal("external_pending", t.id, t.key,
                                       reason="assignee-changed")]
        return []
