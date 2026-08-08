#!/usr/bin/env python3
"""W1.5 E2E — poller 分層閘門端到端(fake dispatcher,免 token)。

3 張同 engine(claude)票 + per_engine claude=2:
  第一輪 → 跑 2 張、1 張 QUEUED
  第二輪 → 前兩張已終態(passthrough,不重跑)、額度空出 → 第 3 張補跑

驗 poller._gate 的接線:active_sessions 計數、FIFO、QUEUED 標記、passthrough 不占額度。
Usage: <venv>/python e2e_gate.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp.poller import OuterLoop  # noqa: E402
from arcp.profiles import load_profiles  # noqa: E402
from arcp.routing import load_config  # noqa: E402
from arcp.store import Store, TicketSession  # noqa: E402
from arcp.ticket import Ticket  # noqa: E402

ok = True


def check(name, cond):
    global ok
    ok = ok and cond
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


class FakeSource:
    def __init__(self, tickets):
        self._t = tickets

    def search(self, jql, max_results=50):
        return self._t

    def get_comments(self, iid):
        return []


class FakeDispatcher:
    """模擬 dispatcher.handle:跑一次設 SUCCESS;終態的 skip(如真 dispatcher)。"""
    def __init__(self, store, profiles):
        self.store = store
        self.profiles = profiles
        self.handled = []

    def handle(self, t, prof):
        s = self.store.get_session(t.id)
        if s and s.outcome in ("SUCCESS", "ABORTED"):
            return []                                    # skip 終態(不重跑)
        self.handled.append(t.id)
        self.store.upsert_session(TicketSession(
            issue_id=t.id, key=t.key, profile=prof, workspace="ws",
            session_id="s", attempts=1, outcome="SUCCESS",
            pending_reason=None, cost_usd=0.0))
        return []


_, routes = load_config("routes.yaml")
profiles = load_profiles("routes.yaml")   # filechain-rawcli = engine claude
tickets = [Ticket(id=i, key=f"P-{i}", summary="s", state="To Do",
                  assignee=None, assignee_id=None,
                  labels=["filechain-rawcli"]) for i in (101, 102, 103)]
root = tempfile.mkdtemp()
store = Store(os.path.join(root, "s"))
disp = FakeDispatcher(store, profiles)
loop = OuterLoop(FakeSource(tickets), store, routes, "jql", dispatcher=disp,
                 concurrency={"max_running": 10, "per_engine": {"claude": 2},
                              "per_profile": {}})

ev1 = loop.poll_once()
q1 = [e for e in ev1 if e.get("type") == "queued"]
check("第一輪跑 2 張(per_engine claude=2)", len(disp.handled) == 2)
check("第一輪跑的是 FIFO 前兩張(101,102)",
      sorted(disp.handled) == [101, 102])
check("第一輪第 3 張 QUEUED", len(q1) == 1 and q1[0]["issue_id"] == 103)

ev2 = loop.poll_once()
check("第二輪 103 補跑(額度空出)", 103 in disp.handled)
check("第二輪 101/102 不重跑(passthrough skip 終態)",
      disp.handled.count(101) == 1 and disp.handled.count(102) == 1)

print("e2e-gate:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
