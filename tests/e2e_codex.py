#!/usr/bin/env python3
"""W3.1 E2E — codex 真跑(rawcli 第二引擎,D2/W17)。

驗兩件事:
1. envelope 契約與 claude 版同形:completed/session_id(thread_id)/
   truly_resumed/cost_usd/error —— 「換引擎不換契約」。
2. native resume:`codex exec resume <thread_id>` 續同 session(a2
   truly_resumed=True、同 thread 記得 a1 的上下文)。

花 token(codex 帳號預設 model、極短任務)。Usage: python3 e2e_codex.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp.inner_runner import run_attempt  # noqa: E402

ok = True


def check(name, cond):
    global ok
    ok = ok and bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


def content(ws, name):
    p = os.path.join(ws, name)
    return open(p).read().strip() if os.path.isfile(p) else None


ws = tempfile.mkdtemp()
artifacts = tempfile.mkdtemp()
agent_cfg = {
    "backend": "rawcli", "engine": "codex", "sandbox": "workspace-write",
    "timeout_sec": 240,
}

r1 = run_attempt(agent_cfg, ws,
                 "Create a file named step1.txt containing exactly the "
                 "text 1 and nothing else. Do not create other files.",
                 artifacts, 1)
print("  a1:", r1.raw_outcome, "| sid:", r1.session_id,
      "| cost:", r1.cost_usd, "| err:", r1.error)
check("a1 completed(envelope 驅動)", r1.raw_outcome == "completed")
check("a1 session_id(thread_id)非空", bool(r1.session_id))
check("a1 truly_resumed=False(首跑)", r1.truly_resumed is False)
check("a1 step1.txt=1", content(ws, "step1.txt") == "1")

r2 = run_attempt(agent_cfg, ws,
                 "Continue the previous task: now create step2.txt whose "
                 "content is step1's content followed by 2 (so exactly 12).",
                 artifacts, 2, resume_session_id=r1.session_id)
print("  a2:", r2.raw_outcome, "| sid:", r2.session_id,
      "| cost:", r2.cost_usd, "| err:", r2.error)
check("a2 completed", r2.raw_outcome == "completed")
check("a2 truly_resumed=True(native resume)", r2.truly_resumed is True)
check("a2 同 thread(session_id 不變)", r2.session_id == r1.session_id)
check("a2 step2.txt=12(靠 session 上下文)", content(ws, "step2.txt") == "12")

print("e2e-codex:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
