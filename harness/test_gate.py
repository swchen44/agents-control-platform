#!/usr/bin/env python3
"""W1.5 — F1 分層資源閘門 單元測(免 token,純函數 + store)。

  gate.select_dispatchable:
    G1 per_engine:claude=1 → 第二張 claude 排隊、codex 不受影響
    G3 FIFO + max_running:額度不足時保留前面的(created ASC)
    per_profile / in_flight(W8)/ max_running 各層
  gate.engine_of:rawcli agent.engine;缺省 claude
  store.active_sessions:W8 排除 pending/inactive/queued/終態

真並行閘門的端到端見 e2e_gate.py。Usage: <venv>/python test_gate.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp.gate import engine_of, select_dispatchable  # noqa: E402
from arcp.store import Store, TicketSession  # noqa: E402

ok = True


def check(name, cond):
    global ok
    ok = ok and cond
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


class P:  # profile stub — engine_of 只看 .agent
    def __init__(self, engine=None):
        self.agent = {"engine": engine} if engine else {}


# -- engine_of ------------------------------------------------------------- #
check("engine_of rawcli codex", engine_of(P("codex")) == "codex")
check("engine_of 缺省 claude", engine_of(P()) == "claude")

# -- G1:per_engine claude=1 ------------------------------------------------ #
run, q = select_dispatchable(
    [("claude", "pa"), ("claude", "pb"), ("codex", "pc")],
    {"max_running": 10, "per_engine": {"claude": 1, "codex": 2},
     "per_profile": {}})
check("G1 claude 只跑第一張(codex 不受影響)", run == [0, 2])
check("G1 第二張 claude 排隊", q == [1])

# -- G3:FIFO + max_running ------------------------------------------------- #
run, q = select_dispatchable(
    [("claude", "a"), ("claude", "b"), ("claude", "c")],
    {"max_running": 2, "per_engine": {}, "per_profile": {}})
check("G3 FIFO 保留前兩張", run == [0, 1] and q == [2])

# -- per_profile ----------------------------------------------------------- #
run, q = select_dispatchable(
    [("claude", "pa"), ("claude", "pa"), ("claude", "pb")],
    {"max_running": 10, "per_engine": {}, "per_profile": {"pa": 1}})
check("per_profile pa=1 擋第二張 pa", run == [0, 2] and q == [1])

# -- in_flight(W8):已在跑的占額度 ---------------------------------------- #
run, q = select_dispatchable(
    [("claude", "pa")],
    {"max_running": 10, "per_engine": {"claude": 1}, "per_profile": {}},
    in_flight_engine={"claude": 1})
check("in_flight 占滿 per_engine → 排隊", run == [] and q == [0])

run, q = select_dispatchable(
    [("claude", "pa")], {"max_running": 1, "per_engine": {}},
    in_flight_total=1)
check("in_flight 占滿 max_running → 排隊", run == [] and q == [0])

# -- 不設任何層 = 只受 max_running 限 -------------------------------------- #
run, q = select_dispatchable(
    [("claude", "a"), ("codex", "b")],
    {"max_running": 10, "per_engine": {}, "per_profile": {}})
check("無 per_* 限 → 都跑", run == [0, 1] and q == [])

# -- store.active_sessions:W8 排除 --------------------------------------- #
root = tempfile.mkdtemp()
st = Store(os.path.join(root, "s"))


def _sess(iid, **kw):
    base = dict(issue_id=iid, key=f"K-{iid}", profile="p",
                workspace="ws", session_id=None, attempts=0, outcome=None,
                pending_reason=None, cost_usd=0.0)
    base.update(kw)
    return TicketSession(**base)


st.upsert_session(_sess(1))                              # active
st.upsert_session(_sess(2, pending_reason="external"))   # 等外部
st.upsert_session(_sess(3, inactive=True))               # 交人類 inactive
st.upsert_session(_sess(4, queued=True))                 # 本輪排隊
st.upsert_session(_sess(5, outcome="SUCCESS"))           # 終態
active = st.active_sessions()
ids = sorted(s.issue_id for s in active)
check("active_sessions 只含 active(排除 pending/inactive/queued/終態)",
      ids == [1])
check("active_sessions 欄位往返正確(queued/inactive bool)",
      active[0].queued is False and active[0].inactive is False)

print("test-gate:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
