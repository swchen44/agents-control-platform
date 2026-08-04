"""F1 — 分層資源閘門(DESIGN §6)。純函數,與 poller 解耦、好測。

每輪 poll 對候選票按 FIFO 分配「同時能跑」的額度:全局 max_running、per-engine
(claude/codex 各自進程上限,機器 CPU/memory)、per-profile。超額的標 QUEUED(下輪重評)。

W8:in_flight_* 只含 active session(store.active_sessions 已排除 pending/inactive/
queued/終態)——不在機器人手上的不占額度。這是「怕系統不夠用」的直接解。
"""

from __future__ import annotations

from collections import Counter


def engine_of(profile) -> str:
    """該 profile 最終 spawn 的引擎(claude|codex)——per-engine 閘門的分類鍵。
    rawcli 由 agent.engine 決定;其餘 backend 預設 claude(acp/server 跑 claude-code)。"""
    return str((profile.agent.get("engine") or "claude")).lower()


def select_dispatchable(candidates, limits, in_flight_engine=None,
                        in_flight_profile=None, in_flight_total=0):
    """FIFO-select within layered quota.

    candidates: list of (engine, profile_name) in FIFO order (created ASC).
    limits: {max_running, per_engine:{claude:N,...}, per_profile:{name:K}}.
            缺的層 = 不限(None)。
    in_flight_*: already-running counts (W8:active only).
    Returns (run_idx, queued_idx) — indices into candidates.
    """
    max_running = int(limits.get("max_running", 1))
    per_engine = limits.get("per_engine") or {}
    per_profile = limits.get("per_profile") or {}
    eng = Counter(in_flight_engine or {})
    prof = Counter(in_flight_profile or {})
    running = int(in_flight_total)
    run, queued = [], []
    for i, (e, p) in enumerate(candidates):
        e_lim = per_engine.get(e)
        p_lim = per_profile.get(p)
        if (running < max_running
                and (e_lim is None or eng[e] < e_lim)
                and (p_lim is None or prof[p] < p_lim)):
            run.append(i)
            eng[e] += 1
            prof[p] += 1
            running += 1
        else:
            queued.append(i)
    return run, queued
