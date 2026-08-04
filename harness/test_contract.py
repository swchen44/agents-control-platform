#!/usr/bin/env python3
"""W1.4 — G1 結構化契約 單元測(免 token,mock run_attempt)。

  C1 validate_structured / summarize:形狀 + enum 輕驗
  C2 envelope 有 structured → dispatcher comment 帶 agent 自評(reason);且
     **grader 終審不被 agent status 覆寫**(agent 說 done、grade 失敗仍 FAILURE)——G2 精神
  C3 無 structured(未開 schema)→ comment 無自評(向後相容)

真跑 claude 的端到端(stream-json + --json-schema)見 e2e_contract.py(少量 token)。

Usage: <venv>/python test_contract.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp_harness import dispatcher as dmod  # noqa: E402
from arcp_harness.contract import (CONTRACT_SCHEMA, summarize,  # noqa: E402
                                   validate_structured)
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


# -- C1:validate / summarize ----------------------------------------------- #
check("C1 合法物件過", validate_structured(
    {"reason": "done it", "status": "done"})[0])
check("C1 非法 status 擋", not validate_structured(
    {"reason": "x", "status": "weird"})[0])
check("C1 缺 reason 擋", not validate_structured({"status": "done"})[0])
check("C1 next 合法過", validate_structured(
    {"reason": "r", "status": "handoff",
     "next": {"to": "bob", "kind": "human"}})[0])
check("C1 next.kind 非法擋", not validate_structured(
    {"reason": "r", "status": "handoff", "next": {"kind": "alien"}})[0])
check("C1 summarize 含 status/reason/next",
      "status=handoff" in (s := summarize(
          {"reason": "交給人", "status": "handoff",
           "next": {"to": "swchen44", "kind": "human"}}))
      and "交給人" in s and "human:swchen44" in s)
check("C1 schema required 有 reason+status",
      set(CONTRACT_SCHEMA["required"]) == {"reason", "status"})


# -- C2/C3:dispatcher 整合 ------------------------------------------------- #
class FakeSource:
    def __init__(self):
        self.comments = []

    def add_comment(self, issue_id, text):
        self.comments.append((issue_id, text))


def fake_run(structured):
    def _f(agent_cfg, ws, prompt, artifacts, attempt, resume_session_id=None):
        return AttemptResult(
            raw_outcome="completed", session_id="s1", truly_resumed=False,
            cost_usd=0.01, error=None, events_path="", envelope_path="",
            error_kind=None, structured=structured)
    return _f


def _profile():
    # verify 查不存在的檔 → grade 必失敗(測 grader 終審)
    return Profile(name="p", workspace_template="empty",
                   workspace_folder="tickets/{issue_id}", skills=[],
                   agent={"backend": "rawcli"},
                   verify=[VerifyStep(name="v", files={"never.txt": "x"})],
                   max_attempts=1, on_unknown="pending")


def _run(structured):
    dmod.run_attempt = fake_run(structured)
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "state"))
    src = FakeSource()
    Dispatcher(src, store, {"p": _profile()}, root=root).handle(
        Ticket(id=1, key="P-1", summary="s", state="To Do",
               assignee=None, assignee_id=None), "p")
    return store.get_session(1), src


# C2:agent 自評 status=done,但 grade 失敗 → 仍 FAILURE(grader 終審)
sess, src = _run({"reason": "我改完了", "status": "done", "next": None})
blob = "\n".join(t for _, t in src.comments)
check("C2 comment 帶 agent 自評", "agent 自評" in blob)
check("C2 comment 含 agent reason 原文", "我改完了" in blob)
check("C2 grader 終審不被 agent status 覆寫(FAILURE)",
      sess.pending_reason == "max-attempts")

# C3:未開 schema(structured=None)→ comment 無自評
_, src3 = _run(None)
blob3 = "\n".join(t for _, t in src3.comments)
check("C3 無 structured 時 comment 無自評", "agent 自評" not in blob3)

print("test-contract:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
