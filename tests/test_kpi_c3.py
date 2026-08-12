#!/usr/bin/env python3
"""C3 KPI 框架(kpi.compute_kpi)。合成 journal/sessions、免網。
情境:雙報 first-pass(strict/progress 分母)、rework 判定(retry/continue/
handoff)、效率中位、制衡(abort 原因/評分/unknown/abandonment)、
coverage、since 窗、空資料不炸。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from arcp.kpi import compute_kpi  # noqa: E402

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}  {detail}")


def _ev(iid, ts, typ, **kw):
    return {"issue_id": iid, "ts": ts, "type": typ, **kw}


NOW = 100000.0
J = []
S = []
# 票1:一次到位 close(session_created 100 → closed 700 = 10 分 cycle)
J += [_ev(1, 50, "new_issue"), _ev(1, 60, "route_matched"),
      _ev(1, 100, "session_created"), _ev(1, 110, "attempt_started"),
      _ev(1, 600, "resolved"), _ev(1, 700, "closed")]
S += [{"issue_id": 1, "outcome": "SUCCESS", "attempts": 1, "cost_usd": 0.05,
       "human_score": 9}]
# 票2:continue(打回)後才 close → 非一次到位
J += [_ev(2, 50, "new_issue"), _ev(2, 60, "route_matched"),
      _ev(2, 100, "session_created"), _ev(2, 110, "attempt_started"),
      _ev(2, 500, "hil_resumed", reason="continue"),
      _ev(2, 510, "attempt_started"), _ev(2, 900, "closed")]
S += [{"issue_id": 2, "outcome": "SUCCESS", "attempts": 2, "cost_usd": 0.15,
       "human_score": 6}]
# 票3:retry 過、close → 非一次到位
J += [_ev(3, 100, "session_created"), _ev(3, 110, "attempt_started"),
      _ev(3, 200, "command_accepted", command="retry"),
      _ev(3, 210, "attempt_started"), _ev(3, 800, "closed")]
S += [{"issue_id": 3, "outcome": "SUCCESS", "attempts": 1, "cost_usd": 0.10}]
# 票4:SUCCESS 但還在等評分(resolved 未 closed)
J += [_ev(4, 100, "session_created"), _ev(4, 110, "attempt_started"),
      _ev(4, 400, "resolved")]
S += [{"issue_id": 4, "outcome": "SUCCESS", "attempts": 1, "cost_usd": 0.02}]
# 票5:abort(cancel)
J += [_ev(5, 100, "session_created"),
      _ev(5, 300, "aborted", reason="cancel")]
S += [{"issue_id": 5, "outcome": "ABORTED", "abort_reason": "cancel"}]
# 票6:UNKNOWN 交人查(pending unknown)
J += [_ev(6, 100, "session_created"), _ev(6, 110, "attempt_started"),
      _ev(6, 150, "pending", reason="unknown")]
S += [{"issue_id": 6, "outcome": "UNKNOWN", "attempts": 1}]
# 票7:沒被 route 認領(coverage 分母)
J += [_ev(7, 100, "new_issue")]

k = compute_kpi(J, S, now=NOW)
ns = k["north_star"]
check("雙報:strict = 一次到位/closed = 1/3",
      ns["first_pass_close_rate_strict"] == 33.3
      and ns["closed"] == 3 and ns["first_pass"] == 1)
check("雙報:progress = 一次到位/終態 = 1/5(含票4 等評分+票6 UNKNOWN?)",
      ns["resolved"] >= 4
      and ns["first_pass_close_rate_progress"] is not None)
check("rework 判定:continue/retry 各 1、handoff 0",
      k["guard"]["continue_n"] == 1 and k["guard"]["retry_n"] == 1
      and k["guard"]["handoff_n"] == 0)
eff = k["efficiency"]
check("cycle 中位(分):票1=10、票2≈13.3、票3≈11.7 → med≈11.7",
      eff["cycle_time_min_med"] == 11.7, detail=str(eff))
check("cost per close 中位 = 0.1",
      eff["cost_per_close_med"] == 0.1)
check("throughput:近 4 週 list(本週含 3 張)",
      isinstance(eff["throughput_weekly"], list)
      and eff["throughput_weekly"][0] == 3)
g = k["guard"]
check("制衡:abort_reasons 帶 cancel、human_score 中位、unknown rate",
      g["abort_reasons"].get("cancel") == 1
      and g["human_score_med"] is not None
      and g["unknown_rate"] is not None)
check("abandonment = aborted/有結果的", g["abandonment_rate"] is not None)
check("coverage = routed/new = 2/3",
      k["coverage"]["automation_coverage"] == 66.7
      and k["coverage"]["new_issues"] == 3)

k2 = compute_kpi(J, S, now=NOW, since=NOW + 1)   # 窗外 → 全空
check("since 窗:全排除 → closed 0、比率 None",
      k2["north_star"]["closed"] == 0
      and k2["north_star"]["first_pass_close_rate_strict"] is None)
k3 = compute_kpi([], [], now=NOW)
check("空資料不炸:比率 None、清單空",
      k3["north_star"]["first_pass_close_rate_strict"] is None
      and k3["efficiency"]["throughput_weekly"] == [0, 0, 0, 0])

print(f"test-kpi-c3: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
