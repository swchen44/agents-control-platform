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

import os
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
    (re.compile(r"(?i)^@agent\s+(hold|interrupt)\b"), "hold"),   # Q11:強制中斷→HIL
    (re.compile(r"(?i)^@agent\s+(stop|pause)\b"), "stop"),       # 交還人工(pending)
    (re.compile(r"(?i)^@agent\s+retry\b"), "retry"),
    (re.compile(r"(?i)^@agent\s+cancel\b"), "cancel"),
    (re.compile(r"(?i)^@agent\s+next\b"), "next"),
]
_NEXT_RE = re.compile(r"(?i)^@agent\s+next\s+([A-Za-z0-9_-]+)")
_GENERIC = re.compile(r"(?i)^@agent\b")

HELP = ("[agent] 不認得這個指令。可用:@agent run|retry|hold|stop|cancel"
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
                 profiles: dict | None = None,
                 base_url: str = "", mention: str = ""):
        self.source = source
        self.store = store
        self.allowed = allowed_commenters
        self.profiles = profiles       # W2.5:next 目標校驗(None=不校驗)
        self.base_url = base_url        # Q11:hold 開一次性表單用(同 ScoreGate)
        self.mention = mention

    def _evict_running(self, sess) -> None:
        """Q11:寫 EVICT 檔 → agent 看門狗 killpg(同 control /evict);無 workspace 則略。"""  # noqa: E501
        ws = getattr(sess, "workspace", "") or ""
        if ws in ("", "(adopted)", "(handoff)"):
            return
        artifacts = os.path.join(os.path.dirname(ws), "attempts")
        try:
            os.makedirs(artifacts, exist_ok=True)
            with open(os.path.join(artifacts, "EVICT"), "w") as f:
                f.write("evict")
        except OSError as e:           # 寫不了不擋指令(可能還沒 spawn)
            log.warning("hold evict 寫檔失敗 %s: %s", getattr(sess, "key", "?"), e)

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
        if cmd == "hold":                       # Q11:人類強制中斷 → HIL(Middle)
            self._evict_running(sess)           # 立即 evict(killpg,不耗 attempt)
            sess.pending_reason = "hold"        # 進 HIL(Middle),下輪不派工
            self.store.upsert_session(sess)
            from .hil import request_human  # lazy(避免 import 期耦合)
            request_human(
                self.source, self.store, t.id, t.key, "hold",
                question="人類強制中斷,請給 agent 新指示(填完 agent 會帶著它 resume)",
                base_url=self.base_url, mention=self.mention)
            log.info("%s hold:evict + 開 hold 表單", t.key)
            return [self.store.journal("command_accepted", t.id, t.key,
                                       command="hold", author=c.author)]
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
        """W11:assignee **恆定=Agent**,不再當資源開關/觸發(人機互動改走一次性
        表單)。被改離 agent → 記告警 + 貼一次提醒(**不強制改回**,避免搶 assignee +
        revert→通知噪音);改回 agent → 靜默記錄。poller 只在 assignee 實際變動時呼此,
        故一次變動只提醒一次(冪等)。"""
        sess = self.store.get_session(t.id)
        if sess is None or sess.outcome is not None:
            return []                       # 無 session / 已終態:不管
        if self.bot_account_id and (t.assignee_id or "") == self.bot_account_id:
            log.info("%s assignee 改回 agent(靜默)", t.key)
            return [self.store.journal("assignee_restored", t.id, t.key)]
        self.source.add_comment(
            t.id, "[agent] 提醒:本票由 agent 處理,assignee 請保持為 agent。"
                  "人類要介入,請用 agent 貼出的一次性表單連結(請勿改 assignee)。")
        log.info("%s assignee 被改離 agent → 告警(不改回)", t.key)
        return [self.store.journal("assignee_alert", t.id, t.key,
                                   assignee=t.assignee or "")]
