"""W3.4 — scheduled/oneshot 內部觸發源(DESIGN §5 / W20)。

profile 不只被 Jira 票驅動,也能被內部觸發器啟動,走同一條
provision→fork→grade 管線,差別只在:
- **無票面**:pseudo-Ticket(id=timestamp、key=run_name)→ 命名自然成為
  DESIGN §2 的無票格式 `{agent}__{run_name}__{timestamp}`;prompt 來自
  trigger config,渲染進 TICKET.md(agent 讀法不變)。
- **不經審批門**(W20):config 寫了 trigger 即視為授權。
- **結果進 journal/dashboard**,不寫 Jira comment。

觸發方式:
- scheduled:`every: 24h`(級距 Nm/Nh/Nd;poller 每輪檢 due,last_run 存
  store trigger_state)。冪等取向:**先記 last_run 再跑**(at-most-once——
  crash 寧可少跑一輪,下次 due 再補,不重複跑)。
- oneshot:`python3 run_trigger.py <名>`(CLI,忽略 every/last_run)。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

import yaml

from .dispatcher import BASE_PROMPT, _grader
from .inner_runner import run_attempt
from .logutil import get_logger
from .profiles import Profile
from .routing import ConfigError
from .store import TicketSession
from .ticket import Ticket
from .workspace import provision

log = get_logger("triggers")

_RUN_NAME_RE = re.compile(r"^[a-z0-9-]+$")     # 防 path 注入(DESIGN §10)
_EVERY_RE = re.compile(r"^(\d+)([mhd])$")
_UNIT_SEC = {"m": 60, "h": 3600, "d": 86400}


@dataclass
class Trigger:
    name: str
    profile: str
    run_name: str
    prompt: str
    every_sec: float | None      # None = 只能 oneshot(CLI)


def load_triggers(path: str, profiles: dict[str, Profile]) -> list[Trigger]:
    """fail-fast 載入(壞 config 死在 load,不是觸發時)。"""
    with open(path) as f:
        doc = yaml.safe_load(f) or {}
    out: list[Trigger] = []
    for i, t in enumerate(((doc.get("outer_loop") or {}).get("triggers")
                           or [])):
        name = t.get("name") or f"trigger[{i}]"
        run_name = str(t.get("run_name") or "")
        if not _RUN_NAME_RE.match(run_name):
            raise ConfigError(f"trigger {name}: run_name 必填且限 [a-z0-9-]"
                              f"(拿到 {run_name!r})")
        prof = t.get("profile")
        if prof not in profiles:
            raise ConfigError(f"trigger {name}: profile 不存在: {prof!r}")
        if not str(t.get("prompt") or "").strip():
            raise ConfigError(f"trigger {name}: prompt 必填")
        every = t.get("every")
        every_sec = None
        if every is not None:
            m = _EVERY_RE.match(str(every))
            if not m:
                raise ConfigError(f"trigger {name}: every 格式 N[mhd]"
                                  f"(拿到 {every!r})")
            every_sec = int(m.group(1)) * _UNIT_SEC[m.group(2)]
        out.append(Trigger(name=name, profile=prof, run_name=run_name,
                           prompt=str(t["prompt"]), every_sec=every_sec))
    return out


def due(trigger: Trigger, store, now: float | None = None) -> bool:
    """scheduled 判定;oneshot(every 缺)永不 due(只走 CLI)。"""
    if trigger.every_sec is None:
        return False
    now = time.time() if now is None else now
    return now - store.trigger_last_run(trigger.name) >= trigger.every_sec


def run_trigger(trigger: Trigger, profiles: dict[str, Profile], store,
                root: str, now: float | None = None) -> list[dict]:
    """跑一輪 trigger:pseudo-ticket → provision → 證據迴圈 → journal。

    迷你派工(dispatcher 減去 Jira 面):同 grader/三態語意;session 存
    TicketSession(issue_id=timestamp,不與 Jira id 衝突)→ dashboard 可見、
    retention 照收。
    """
    now = time.time() if now is None else now
    ts = int(now)
    profile = profiles[trigger.profile]
    store.set_trigger_last_run(trigger.name, now)   # 先記水位(at-most-once)
    ticket = Ticket(id=ts, key=trigger.run_name, summary=f"trigger:{trigger.name}",
                    state="internal", assignee=None, assignee_id=None,
                    description=trigger.prompt)
    ws = provision(root, ticket, profile)
    sess = TicketSession(issue_id=ts, key=trigger.run_name,
                         profile=profile.name, workspace=ws, session_id=None,
                         attempts=0, outcome=None, pending_reason=None,
                         cost_usd=0.0)
    store.upsert_session(sess)
    events = [store.journal("trigger_started", ts, trigger.run_name,
                            trigger=trigger.name, profile=profile.name,
                            workspace=ws)]
    log.info("trigger %s 啟動(run=%s ws=%s)", trigger.name, trigger.run_name, ws)

    grader = _grader(profile)
    artifacts = f"{ws.rstrip('/').rsplit('/ws', 1)[0]}/attempts" \
        if ws.endswith("/ws") else ws + ".attempts"
    feedback: str | None = None
    while sess.attempts < profile.max_attempts:
        sess.attempts += 1
        prompt = BASE_PROMPT if not feedback else (
            f"{BASE_PROMPT}\n\n上次嘗試未過驗證,失敗證據:\n{feedback}\n"
            f"請只修正缺失的部分。")
        res = run_attempt(dict(profile.agent), ws, prompt, artifacts,
                          sess.attempts, resume_session_id=sess.session_id)
        sess.session_id = res.session_id or sess.session_id
        sess.cost_usd += res.cost_usd or 0.0
        events.append(store.journal(
            "attempt_finished", ts, trigger.run_name, attempt=sess.attempts,
            raw=res.raw_outcome, error_kind=res.error_kind))
        if res.raw_outcome == "unknown":            # 同 v5 D3:不自動重試
            sess.outcome, sess.pending_reason = "UNKNOWN", "unknown"
            store.upsert_session(sess)
            events.append(store.journal("trigger_finished", ts,
                                        trigger.run_name, outcome="UNKNOWN"))
            return events
        verdict = grader.grade(ws)
        if verdict.passed and res.raw_outcome == "completed":
            sess.outcome = "SUCCESS"
            store.upsert_session(sess)
            kpi = ({"human_minutes_saved": profile.human_minutes_est}
                   if profile.human_minutes_est else {})    # W3.5 C3
            events.append(store.journal(
                "trigger_finished", ts, trigger.run_name, outcome="SUCCESS",
                attempts=sess.attempts, cost_usd=sess.cost_usd, **kpi))
            log.info("trigger %s SUCCESS(%d attempt, $%.4f)",
                     trigger.name, sess.attempts, sess.cost_usd)
            return events
        feedback = verdict.summary()
        store.upsert_session(sess)

    sess.outcome, sess.pending_reason = "FAILURE", "max-attempts"
    store.upsert_session(sess)
    events.append(store.journal("trigger_finished", ts, trigger.run_name,
                                outcome="FAILURE", attempts=sess.attempts))
    log.info("trigger %s FAILURE(max-attempts)", trigger.name)
    return events
