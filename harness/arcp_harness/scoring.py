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


class ScoreGate:
    """每輪對終態未評分的票:抓分數(→journal)或週期催評。"""

    def __init__(self, source, store, interval_sec: float = REMIND_INTERVAL_SEC):
        self.source = source
        self.store = store
        self.interval = interval_sec

    def on_poll(self, ticket, session, now: float | None = None) -> list[dict]:
        if session is None or session.outcome not in ("SUCCESS", "FAILURE"):
            return []
        if session.human_score is not None:
            return []                     # 已評分,不重抓/不催
        now = time.time() if now is None else now
        score = collect_score(ticket.description or "")
        if score is not None:
            session.human_score = score
            self.store.upsert_session(session)
            self.source.add_comment(
                ticket.id,
                f"[agent] 已收到完成度評分:{score}/{SCORE_MAX}"
                f"({score * 10}%),謝謝!")
            return [self.store.journal(
                "human_score", ticket.id, ticket.key,
                score=score, pct=score * 10, outcome=session.outcome)]
        if now - (session.score_reminded_at or 0.0) >= self.interval:
            session.score_reminded_at = now
            self.store.upsert_session(session)
            self.source.add_comment(ticket.id, (
                f"[agent] 這張票已 {session.outcome},尚未評分。請在 description 的 "
                f"[ARCP owner=human] 段填 `score: <{SCORE_MIN}–{SCORE_MAX}>`"
                f"(對照 agent:{session.profile} 段的目標的完成度)。"))
            return [self.store.journal("score_reminded", ticket.id, ticket.key)]
        return []
