#!/usr/bin/env python3
"""timeout 重跑(timeout_retry_max)單元測(免 token,mock run_attempt)。

attempt 超時(harness 殺行程→無 envelope→unknown+error_kind=timeout)時:
  T1 default(0)→ 現行為不變:UNKNOWN pending(v5 D3)
  T2 global=2 → 重跑 2 次(不消耗 attempt、session 留 active),第 3 次落 UNKNOWN
  T3 profile agent.timeout_retry_max=1 覆蓋 global 0 → 重跑 1 次
  T4 profile agent.timeout_retry_max=0 覆蓋 global 2 → 直接 UNKNOWN
  T5 真 unknown(error_kind=None)不吃 timeout 重跑 → 直接 UNKNOWN

用假 run_attempt(回 unknown+timeout)+ 真 Store 驅動;仿 test_budget 模式。

Usage: <venv>/python test_timeout_retry.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))
from arcp import dispatcher as dmod  # noqa: E402
from arcp.dispatcher import Dispatcher  # noqa: E402
from arcp.inner_runner import AttemptResult  # noqa: E402
from arcp.profiles import Profile, VerifyStep  # noqa: E402
from arcp.store import Store  # noqa: E402
from arcp.ticket import Ticket  # noqa: E402

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


def fake_run(error_kind):
    def _f(agent_cfg, ws, prompt, artifacts, attempt, resume_session_id=None,
           **kw):
        return AttemptResult(
            raw_outcome="unknown", session_id="s1", truly_resumed=False,
            cost_usd=0.01, error=None, events_path="", envelope_path="",
            error_kind=error_kind)
    return _f


def _ticket():
    return Ticket(id=1, key="P-1", summary="s", state="To Do",
                  assignee=None, assignee_id=None)


def _profile(agent_extra=None):
    agent = {"backend": "rawcli"}
    agent.update(agent_extra or {})
    return Profile(name="p", workspace_template="empty",
                   workspace_folder="tickets/{issue_id}", skills=[],
                   agent=agent,
                   verify=[VerifyStep(name="v", files={"never.txt": "x"})],
                   max_attempts=2, on_unknown="pending")


def _mk(global_max, agent_extra=None, error_kind="timeout"):
    dmod.run_attempt = fake_run(error_kind)
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "state"))
    src = FakeSource()
    d = Dispatcher(src, store, {"p": _profile(agent_extra)}, root=root)
    d.timeout_retry_max = global_max
    return d, store, src


# T1:default 0 → 直接 UNKNOWN(現行為不變)
d, store, src = _mk(0)
d.handle(_ticket(), "p")
sess = store.get_session(1)
check("T1 default 0:直接 UNKNOWN pending", sess.outcome == "UNKNOWN"
      and sess.pending_reason == "unknown" and sess.attempts == 1)
check("T1 留言含超時 cause", any("超時" in t for _, t in src.comments))

# T2:global=2 → handle×2 都重跑(attempt 不消耗、session 留 active),
#    第 3 次 UNKNOWN
d, store, src = _mk(2)
d.handle(_ticket(), "p")
sess = store.get_session(1)
check("T2 第 1 次:重跑(不消耗 attempt、留 active)",
      sess.timeout_retries == 1 and sess.attempts == 0
      and sess.outcome is None and sess.pending_reason is None)
check("T2 重跑留言 1/2", any("1/2" in t for _, t in src.comments))
d.handle(_ticket(), "p")
sess = store.get_session(1)
check("T2 第 2 次:重跑 2/2", sess.timeout_retries == 2 and sess.attempts == 0)
d.handle(_ticket(), "p")
sess = store.get_session(1)
check("T2 第 3 次:用完落 UNKNOWN", sess.outcome == "UNKNOWN"
      and sess.timeout_retries == 2 and sess.attempts == 1)
check("T2 UNKNOWN 留言含已用次數",
      any("已用 2 次" in t for _, t in src.comments))

# T3:profile agent.timeout_retry_max=1 覆蓋 global 0 → 重跑 1 次
d, store, src = _mk(0, {"timeout_retry_max": 1})
d.handle(_ticket(), "p")
sess = store.get_session(1)
check("T3 profile 覆蓋 global(0→1):第 1 次重跑",
      sess.timeout_retries == 1 and sess.outcome is None)
d.handle(_ticket(), "p")
sess = store.get_session(1)
check("T3 第 2 次:用完落 UNKNOWN", sess.outcome == "UNKNOWN")

# T4:profile agent.timeout_retry_max=0 覆蓋 global 2 → 直接 UNKNOWN
d, store, src = _mk(2, {"timeout_retry_max": 0})
d.handle(_ticket(), "p")
sess = store.get_session(1)
check("T4 profile 0 覆蓋 global 2:直接 UNKNOWN",
      sess.outcome == "UNKNOWN" and sess.timeout_retries == 0)

# T5:真 unknown(error_kind=None)不吃 timeout 重跑
d, store, src = _mk(2, error_kind=None)
d.handle(_ticket(), "p")
sess = store.get_session(1)
check("T5 真 unknown 不重跑:直接 UNKNOWN",
      sess.outcome == "UNKNOWN" and sess.timeout_retries == 0)
check("T5 留言是行程消失(非超時)",
      any("行程消失" in t for _, t in src.comments))

print("test-timeout-retry:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
