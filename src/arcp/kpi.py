"""C3 — KPI 框架(v5 §10;2026-08-13)。設計正本:docs/design/kpi.md。

從 journal + sessions 算北極星(First-pass Close rate,雙報)+ 效率 + 制衡
指標。**兩個原則(v5 §10.0)**:P1 只建基線不設目標值;每個效率指標配制衡
指標(效率全部可以靠「調鬆 verify」作弊)。一律中位數/p90,不用平均
(長尾分布,平均被卡死的 attempt 帶偏)。

「一次到位」= 該票**無人為返工**:沒有 retry 指令、沒有評分表單 continue
(打回續作)、沒有換手(next/base)。系統內部多 attempt 不算(那是
Attempts per close 在盯)——first-pass 語意是「人第一次看就願意關」。
"""

from __future__ import annotations

import time
from collections import Counter

_REWORK_EVENTS = ("hil_resumed", "handoff")   # + command_accepted(retry)
_WEEK = 7 * 86400


def _pct(part: int, whole: int) -> float | None:
    return round(part * 100.0 / whole, 1) if whole else None


def _median_p90(vals: list[float]) -> tuple[float | None, float | None]:
    if not vals:
        return None, None
    s = sorted(vals)
    n = len(s)
    med = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    p90 = s[min(n - 1, int(n * 0.9))]
    return round(med, 1), round(p90, 1)


def compute_kpi(journal: list[dict], sessions: list, now: float | None = None,
                since: float = 0.0, profile: str | None = None) -> dict:
    """→ 結構化 KPI dict(dashboard /data 與 /api/v1/kpi 共用)。

    journal=事件 list(dict);sessions=session dict 的 list;since>0 只計
    「該票最後活動 >= since」的票(時間窗);profile=只計 session.profile
    等於該名的票(C6 A/B 手選對照——注意:非隨機分流的 profile 對照,
    差異可能來自任務不同質,僅供參考)。"""
    now = time.time() if now is None else now
    sess_by = {s.get("issue_id"): s for s in sessions}
    by_iid: dict[int, list] = {}
    for e in journal:
        iid = e.get("issue_id")
        if isinstance(iid, int) and iid > 0:
            if profile and (sess_by.get(iid) or {}).get("profile") != profile:
                continue
            by_iid.setdefault(iid, []).append(e)

    n_new = n_routed = 0          # Automation coverage(全事件粒度,不分票齡)
    closed_ids, resolved_ids, aborted, first_pass = [], [], [], []
    cycle, attempts_l, cost_l, scores = [], [], [], []
    continue_n = retry_n = handoff_n = unknown_n = attempt_n = 0
    abort_reasons: Counter = Counter()
    closed_ts: list[float] = []

    for iid, evs in by_iid.items():
        last = max(float(e.get("ts") or 0) for e in evs)
        types = [e.get("type") for e in evs]
        if "new_issue" in types:
            n_new += 1
        if any(e.get("type") == "route_matched" for e in evs):
            n_routed += 1
        if since and last < since:
            continue
        s = sess_by.get(iid) or {}
        attempt_n += sum(1 for t in types if t == "attempt_started")
        unknown_n += sum(1 for e in evs if e.get("type") == "pending"
                         and e.get("reason") == "unknown")
        rework = False
        for e in evs:
            t = e.get("type")
            if t == "command_accepted" and e.get("command") == "retry":
                retry_n += 1
                rework = True
            elif t == "hil_resumed" and e.get("reason") == "continue":
                continue_n += 1
                rework = True
            elif t == "handoff":
                handoff_n += 1
                rework = True
        oc = s.get("outcome")
        was_terminal = oc in ("SUCCESS", "FAILURE") or "closed" in types \
            or any(e.get("type") == "resolved" for e in evs)
        if oc == "ABORTED":
            aborted.append(iid)
            abort_reasons[s.get("abort_reason") or "?"] += 1
        if was_terminal:
            resolved_ids.append(iid)
        if "closed" in types:
            closed_ids.append(iid)
            closed_ts.append(next(float(e.get("ts") or 0) for e in evs
                                  if e.get("type") == "closed"))
            if not rework:
                first_pass.append(iid)
            created = min((float(e.get("ts") or 0) for e in evs
                           if e.get("type") in ("session_created",
                                                "new_issue")), default=None)
            done = max(float(e.get("ts") or 0) for e in evs
                       if e.get("type") in ("closed", "resolved"))
            if created:
                cycle.append((done - created) / 60.0)      # 分鐘
            attempts_l.append(int(s.get("attempts") or 0))
            cost_l.append(float(s.get("cost_usd") or 0))
        if s.get("human_score") is not None:
            scores.append(int(s["human_score"]))

    engaged = len(resolved_ids) + len(aborted)             # 接管且有結果的
    cyc_med, cyc_p90 = _median_p90(cycle)
    att_med, _ = _median_p90([float(a) for a in attempts_l])
    cost_med, cost_p90 = _median_p90(cost_l)
    sc_med, _ = _median_p90([float(x) for x in scores])
    weekly = Counter(int((now - ts) // _WEEK) for ts in closed_ts
                     if now - ts < 4 * _WEEK)              # 0=本週
    return {
        "north_star": {
            # 雙報(2026-08-13 定案):嚴格=決策用(調併發依據);進行=趨勢
            "first_pass_close_rate_strict": _pct(len(first_pass),
                                                 len(closed_ids)),
            "first_pass_close_rate_progress": _pct(len(first_pass),
                                                   len(resolved_ids)),
            "closed": len(closed_ids), "resolved": len(resolved_ids),
            "first_pass": len(first_pass),
        },
        "efficiency": {
            "cycle_time_min_med": cyc_med, "cycle_time_min_p90": cyc_p90,
            "attempts_per_close_med": att_med,
            "cost_per_close_med": cost_med, "cost_per_close_p90": cost_p90,
            "throughput_weekly": [weekly.get(i, 0) for i in range(4)],
        },
        "guard": {                     # 制衡(v5 原則二:防調鬆 verify 作弊)
            "continue_rate": _pct(continue_n, len(resolved_ids)),
            "retry_n": retry_n, "continue_n": continue_n,
            "handoff_n": handoff_n,
            "human_score_med": sc_med, "human_score_n": len(scores),
            "unknown_rate": _pct(unknown_n, attempt_n),
            "abort_reasons": dict(abort_reasons),
            "abandonment_rate": _pct(len(aborted), engaged),
        },
        "coverage": {
            "automation_coverage": _pct(n_routed, n_new),
            "new_issues": n_new, "routed": n_routed,
        },
    }
