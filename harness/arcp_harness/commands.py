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
  next <profile>  F3 換手(W2.5):重置 session、pin 新 profile(dispatcher 以
         session.profile 優先於 route)→ 下輪重新排隊;目標 require_approval
         則重走審批門;workspace 置哨值 → 下輪重 provision(新 instance)

External-change policy(v5 §6-10/11 + W12 假設更新):
  status → 終止類狀態(人在看板上直接關票)= out-of-band 撤銷 → ABORTED
  assignee = 資源開關(DESIGN §6):交人類 → inactive(不再派工、讓出 F1 額度);
  回機器人 → 清 inactive(下輪 resume)。未配置 bot_account_id 時退回舊語義
  (任何 assignee 變更 = 撤銷授權 → pending:external)。
  註:同步架構下 inactive=「不再拉起」(agent 每 attempt 跑完自然釋放進程);
  實時 killpg 長駐 agent 留未來異步架構(§6 完整版)。
"""

from __future__ import annotations

import re

from .jira_source import JiraCloudSource
from .logutil import get_logger
from .store import Store
from .ticket import Comment, Ticket
from .transcript import engine_of_agent
from .transcript import finalize as finalize_transcript

log = get_logger("commands")


def _finalize_leaving(sess, profiles: dict | None, reason: str) -> None:
    """W4.3 離手定格:session 交出去(換手/交人)前產 final HTML(不打包)。"""
    prof = (profiles or {}).get(sess.profile)
    engine = engine_of_agent(prof.agent) if prof is not None else "claude"
    finalize_transcript(sess.session_id, engine, sess.workspace,
                        pack=False, reason=reason)


_COMMANDS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)^@agent\s+run\b"), "run"),
    (re.compile(r"(?i)^@agent\s+(stop|hold|pause)\b"), "stop"),
    (re.compile(r"(?i)^@agent\s+retry\b"), "retry"),
    (re.compile(r"(?i)^@agent\s+cancel\b"), "cancel"),
    (re.compile(r"(?i)^@agent\s+next\b"), "next"),
]
_NEXT_RE = re.compile(r"(?i)^@agent\s+next\s+([A-Za-z0-9_-]+)")
_GENERIC = re.compile(r"(?i)^@agent\b")

HELP = ("[agent] 不認得這個指令。可用:@agent run|retry|stop|cancel"
        "|next <profile>")
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
                 allowed_commenters: list[str],
                 profiles: dict | None = None):
        self.source = source
        self.store = store
        self.allowed = allowed_commenters
        self.profiles = profiles       # W2.5:next 目標校驗(None=不校驗)

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
        if cmd == "next":                       # F3 換手(W2.5)
            m = _NEXT_RE.match(c.body.strip())
            target = m.group(1) if m else ""
            if not target or (self.profiles is not None
                              and target not in self.profiles):
                avail = (f"可用:{', '.join(sorted(self.profiles))}"
                         if self.profiles else "用法:@agent next <profile>")
                self.source.add_comment(
                    t.id, f"[agent] next 目標 profile 無效:'{target}'。{avail}")
                return [self.store.journal("command_rejected", t.id, t.key,
                                           command="next", target=target)]
            _finalize_leaving(sess, self.profiles, "handoff-cmd")  # W4.3/W6.4
            # 重置 session、pin 新 profile;下輪 poll 經 gate 重新排隊,目標
            # require_approval 則重走審批門;workspace 哨值→下輪重 provision
            sess.profile = target
            sess.session_id = None
            sess.attempts = 0
            sess.outcome, sess.pending_reason = None, None
            sess.inactive, sess.queued, sess.queued_at = False, False, 0.0
            sess.approval_revisions = 0
            sess.workspace = "(handoff)"
            self.store.upsert_session(sess)
            self.source.add_comment(
                t.id, f"[agent] ack: next → {target}(已重置 session,"
                      f"下輪重新排隊接手)")
            log.info("%s 換手指令 → %s", t.key, target)
            return [self.store.journal("handoff", t.id, t.key, kind="command",
                                       to=target, author=c.author)]
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
                 cancel_states: list[str],
                 bot_account_id: str | None = None,
                 profiles: dict | None = None):
        self.source = source
        self.store = store
        self.cancel_states = cancel_states
        # W12:知道機器人 accountId 才能判 assignee 方向;None = 舊語義
        self.bot_account_id = bot_account_id
        self.profiles = profiles       # W4.3:離手定格的 engine 查表

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
        if sess is None or sess.outcome is not None:
            return []                       # 無 session / 已終態:不管
        if self.bot_account_id is None:     # 舊語義:變更 = 撤銷授權
            sess.pending_reason = "external"
            self.store.upsert_session(sess)
            self.source.add_comment(
                t.id, "[agent] assignee 變更 → 視為撤銷授權,暫停。"
                      "要繼續請留言 @agent run")
            return [self.store.journal("external_pending", t.id, t.key,
                                       reason="assignee-changed")]

        # W12 assignee=資源開關(DESIGN §6)。審批中的票除外:審批流自己用
        # assignee 當放行信號(W2.3 指派審批者/交回機器人),不可誤標 inactive。
        if sess.pending_reason == "approval":
            return []
        # 已 pending 的票(human-decision/budget/…):inactive 只記 journal 不留言
        # ——pending comment 已說明怎麼繼續;再留言會重複甚至矛盾(SCRUM-22 實測:
        # G1 handoff 交人後,資源開關把 harness 自己改的 assignee 當外部變更補留言)。
        quiet = sess.pending_reason is not None
        if (t.assignee_id or "") == self.bot_account_id:
            if not sess.inactive:
                return []                   # 本來就 active,無事
            sess.inactive = False
            self.store.upsert_session(sess)
            if not quiet:
                self.source.add_comment(
                    t.id, "[agent] assignee 回到機器人 → 恢復 active,"
                          "下輪 resume 續跑。")
            log.info("%s assignee 回機器人 → active(resume)", t.key)
            return [self.store.journal("inactive_cleared", t.id, t.key)]
        if sess.inactive:
            return []                       # 已 inactive(人→人)不重複
        sess.inactive = True
        self.store.upsert_session(sess)
        _finalize_leaving(sess, self.profiles, "assignee-inactive")  # W6.4
        if not quiet:
            self.source.add_comment(
                t.id, "[agent] assignee 交給人類 → inactive:不再派工、讓出並發"
                      "額度(不占 CPU/memory)。把 assignee 改回機器人即恢復續跑。")
        log.info("%s assignee 交人類 → inactive(讓出額度)", t.key)
        return [self.store.journal("inactive_set", t.id, t.key,
                                   assignee=t.assignee or "")]
