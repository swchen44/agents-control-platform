#!/usr/bin/env python3
"""W1.3 — A4 budget 花費上限 單元測(免 token,mock run_attempt)。

驗 dispatcher 的 budget 閘(決策 W4:超支=pending:budget,只有人能解除):
  B1 累計未超上限 → 跑滿 max_attempts → FAILURE(不進 budget)
  B2 一次 attempt 後超上限 → pending:budget、停在該次(不再 attempt)
  B3 max_budget_usd=None → 不檢查(跑滿 attempts)

用假 run_attempt(回固定 cost、completed)+ 故意失敗的 verify(查不存在的檔)驅動
重試迴圈;真 Store/provision/grader。

Usage: <venv>/python test_budget.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp_harness import dispatcher as dmod  # noqa: E402
from arcp_harness.dispatcher import Dispatcher  # noqa: E402
from arcp_harness.inner_runner import AttemptResult  # noqa: E402
from arcp_harness.profiles import Profile, VerifyStep  # noqa: E402
from arcp_harness.store import Store  # noqa: E402
from arcp_harness.ticket import Ticket  # noqa: E402

ok = True


def check(name, cond):
    global ok
    ok = ok and cond
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


class FakeSource:
    def __init__(self):
        self.comments = []

    def add_comment(self, issue_id, text):
        self.comments.append((issue_id, text))


def fake_run(cost):
    def _f(agent_cfg, ws, prompt, artifacts, attempt, resume_session_id=None, **kw):
        return AttemptResult(
            raw_outcome="completed", session_id="s1", truly_resumed=False,
            cost_usd=cost, error=None, events_path="", envelope_path="",
            error_kind=None)
    return _f


def _ticket():
    return Ticket(id=1, key="P-1", summary="s", state="To Do",
                  assignee=None, assignee_id=None)


def _profile(budget):
    # verify 查一個不存在的檔 → grade 必失敗 → 驅動重試/預算迴圈
    return Profile(name="p", workspace_template="empty",
                   workspace_folder="tickets/{issue_id}", skills=[],
                   agent={"backend": "rawcli"},
                   verify=[VerifyStep(name="v", files={"never.txt": "x"})],
                   max_attempts=2, on_unknown="pending", max_budget_usd=budget)


def _run(budget, cost):
    dmod.run_attempt = fake_run(cost)
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "state"))
    src = FakeSource()
    d = Dispatcher(src, store, {"p": _profile(budget)}, root=root)
    d.handle(_ticket(), "p")
    return store.get_session(1), src


# B1:未超(budget 1.0,cost 0.1×2=0.2)→ 跑滿 2 次 → max-attempts
sess, _ = _run(1.0, 0.1)
check("B1 未超預算跑滿 attempts(=2)", sess.attempts == 2)
check("B1 落 FAILURE/max-attempts(非 budget)",
      sess.pending_reason == "max-attempts")

# B2:超(budget 0.05,cost 0.1)→ attempt 1 後就 pending:budget
sess, src = _run(0.05, 0.1)
check("B2 超預算停在 attempt 1", sess.attempts == 1)
check("B2 pending:budget", sess.pending_reason == "budget")
check("B2 comment 提示 pending:budget",
      any("pending:budget" in t for _, t in src.comments))

# B3:budget=None → 不檢查 → 跑滿 2 次
sess, _ = _run(None, 0.1)
check("B3 budget=None 不檢查(跑滿 attempts)",
      sess.attempts == 2 and sess.pending_reason == "max-attempts")

print("test-budget:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
