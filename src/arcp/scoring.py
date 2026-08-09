"""W7.2(R1/R2)— 人類完成度評分。

流程(見 REQUIREMENTS §12.1):
- **交人時**(SUCCESS/FAILURE 終態,dispatcher 呼 `write_handoff_sections`):把
  `Profile.goal` 寫進 description 的 `agent:<profile>` 段;若**尚無 human 段**才 seed
  一個含 `score:` placeholder + 註解(**尊重既有 human 段、不覆蓋**)。
- **每輪**(poller 呼 `ScoreGate.on_poll`):對「終態 + 未評分 + 仍開著」的票讀 human 段
  `score`(0–10);填了 → 存 session + journal `human_score`;沒填 → 每票每 ~1h 催一次。

註:render 順序是 human→control→agent,故 goal(agent 段)在 score(human 段)**下方**;
註解以段名指涉「對照 agent:<profile> 段的目標」,不用上/下方,避免錯。
"""

from __future__ import annotations

import time

import yaml

from .logutil import get_logger
from .sections import Section, parse, render

log = get_logger("scoring")

REMIND_INTERVAL_SEC = 3600.0     # 每票最多每小時催一次評分
SCORE_MIN, SCORE_MAX = 0, 10


def profile_goal(profile) -> str:
    """人可讀 agent 目標;未設 goal → fallback(不留空,∵ 人要對照它評分)。"""
    return profile.goal or (f"(profile «{profile.name}» 未設 goal,"
                            f"請參考票的 summary/描述)")


def _score_caption(agent_owner: str) -> str:
    return (f"# 完成度評分(請填 {SCORE_MIN}–{SCORE_MAX} 整數):對照 {agent_owner} "
            f"段的目標,agent 幫了多少({SCORE_MIN}=沒幫上,{SCORE_MAX}=完全達成)\n"
            f"score:")


def write_handoff_sections(source, ticket, profile) -> bool:
    """交人評分:agent:<profile> 段寫 goal;無 human 段才 seed score placeholder。
    只在描述有變時 set_description(冪等)。回傳是否有寫。"""
    desc = ticket.description or ""
    before, secs, after = parse(desc)
    agent_owner = f"agent:{profile.name}"
    body = yaml.safe_dump({"goal": profile_goal(profile)},
                          allow_unicode=True, default_flow_style=False).strip()
    for s in secs:                       # upsert agent 段
        if s.owner == agent_owner:
            s.body = body
            break
    else:
        secs.append(Section(owner=agent_owner, body=body))
    if not any(s.owner == "human" for s in secs):   # 尊重既有 human 段
        secs.append(Section(owner="human", body=_score_caption(agent_owner)))
    new_desc = render(before, secs, after)
    if new_desc.strip() != desc.strip():
        source.set_description(ticket.id, new_desc)
        return True
    return False


def _human_value(description: str | None, key: str):
    """讀 human 段某 key 的原始值(無 human 段/無 key → None)。"""
    _b, secs, _a = parse(description or "")
    for s in secs:
        if s.owner == "human":
            return s.data().get(key)
    return None


def collect_score(description: str | None) -> int | None:
    """讀 human 段 `score`,容錯轉整數並驗 0–10;無/非法 → None。"""
    v = _human_value(description, "score")
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        n = int(str(v).strip())
    except (ValueError, TypeError):
        return None
    return n if SCORE_MIN <= n <= SCORE_MAX else None


def collect_budget_override(description: str | None) -> float | None:
    """W7.3:讀 human 段 `budget_override`(USD,此票單次上限放寬);非法/<=0 → None。"""
    v = _human_value(description, "budget_override")
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        f = float(str(v).strip())
    except (ValueError, TypeError):
        return None
    return f if f > 0 else None


STALL_REMINDERS = 10             # W11:N 次無回應 → 記異常(可設)


class ScoreGate:
    """W11:終態(SUCCESS/FAILURE/UNKNOWN)未評分的票 → 確保有一張 score_and_close
    一次性表單(@mention+連結);逾期未回週期催辦,多次無回應記異常。評分/關單/續跑
    由表單提交(hil.apply_submission)完成——**不再讀描述 score**(全面替換)。"""

    def __init__(self, source, store, base_url: str = "", mention: str = "",
                 interval_sec: float = REMIND_INTERVAL_SEC, ttl_sec: float = 0.0,
                 stall_after: int = STALL_REMINDERS, self_score_fn=None,
                 profiles_fn=None, jira_base_url: str = ""):
        self.source = source
        self.store = store
        self.base_url = base_url                 # 表單服務 base(人開連結用)
        self.base_url_jira = jira_base_url       # Jira base(組 /browse/<key> 連結)
        self.mention = mention
        self.interval = interval_sec
        self.ttl = ttl_sec
        self.stall_after = stall_after
        # Q13:關單時取 agent 數字自評(0–10)。fn(session)->int|None;None=不取。
        # 只在首次發 score_and_close 表單時呼叫一次(非每 attempt)。含一次真 agent 呼叫。
        self.self_score_fn = self_score_fn
        # W10.3:handoff 下拉候選 = 目前載入的全部 profile 名(注入表單 payload)。
        self.profiles_fn = profiles_fn

    def on_poll(self, ticket, session, now: float | None = None) -> list[dict]:
        if session is None or session.outcome not in (
                "SUCCESS", "FAILURE", "UNKNOWN"):       # W10:UNKNOWN 也進 HIL(End)
            return []
        if session.human_score is not None:
            return []                     # 已評分(表單提交時記入),不重發/不催
        now = time.time() if now is None else now
        from .hil import form_link, request_human
        reqs = [r for r in self.store.interactions_for_ticket(ticket.id)
                if r.schema_id == "score_and_close"]
        if not reqs:                       # 首次:發 score_and_close 表單
            agent_score = getattr(session, "agent_score", None)
            if agent_score is None and self.self_score_fn is not None:
                try:                       # Q13:關單這刻 resume+prompt 問 agent 一次
                    agent_score = self.self_score_fn(session)
                except Exception as e:     # noqa: BLE001 — 取不到自評不擋關單流程
                    log.warning("agent 自評取得失敗 ticket=%s: %s", ticket.key, e)
                    agent_score = None
            # W(agent-output):交付物快照進 payload,讓評分表單頁自足呈現
            # (summary_md/code/references/附件 meta;bytes 由 /files/<token> 服務)。
            from .deliverables import snapshot_for_form
            deliv = None
            try:
                deliv = snapshot_for_form(session.workspace)
            except Exception as e:  # noqa: BLE001 — 交付物是加值,取不到不擋評分
                log.warning("交付物快照失敗 ticket=%s: %s", ticket.key, e)
            jira_url = (f"{self.base_url_jira.rstrip('/')}/browse/{ticket.key}"
                        if getattr(self, "base_url_jira", "") else "")
            req = request_human(
                self.source, self.store, ticket.id, ticket.key,
                "score_and_close", question="請評分並裁決:關單 / 續跑 / 換手",
                payload_extra={"title": (ticket.summary or "")[:120],
                               "agent_state": "HIL(End)",
                               "grader": session.outcome,
                               "agent_score": agent_score,
                               "cost_usd": round(session.cost_usd or 0, 4),
                               "attempts": session.attempts,
                               "jira_url": jira_url,
                               "clearquest_id": getattr(
                                   session, "clearquest_id", None),
                               "deliverables": deliv,
                               "profiles": (list(self.profiles_fn())
                                            if self.profiles_fn else [])},
                base_url=self.base_url, mention=self.mention,
                ttl_sec=self.ttl, now=now)
            return [self.store.journal("score_requested", ticket.id,
                                       ticket.key, request_id=req.request_id)]
        pend = [r for r in reqs
                if r.status == "pending" and not r.is_expired(now)]
        if not pend:                       # 已提交 / 全逾期 → 不催
            return []
        r = pend[0]
        # 首次催辦以「建立時間」起算(剛發完表單不該立刻催)
        if now - (r.reminded_at or r.created_at) < self.interval:
            return []
        r.reminders += 1
        r.reminded_at = now
        self.store.upsert_interaction(r)
        at = f"[~accountid:{self.mention}] " if self.mention else ""
        self.source.add_comment(
            ticket.id,
            f"[agent] {at}此票已 {session.outcome},尚待評分/裁決(第 "
            f"{r.reminders} 次提醒)。請填:{form_link(self.base_url, r.token)}")
        evs = [self.store.journal("score_reminded", ticket.id, ticket.key,
                                  reminders=r.reminders)]
        if r.reminders >= self.stall_after:      # W11:多次無回應 → 異常記號
            evs.append(self.store.journal(
                "hil_stalled", ticket.id, ticket.key, reminders=r.reminders,
                request_id=r.request_id))
        return evs
