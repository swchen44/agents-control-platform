#!/usr/bin/env python3
"""W5.3 E2E — 真 killpg evict(花少量 token)。

claude(haiku)被指示跑 `sleep 90`;約 8 秒後 harness 寫 EVICT 檔 →
evict watchdog killpg → envelope error_kind=evicted、总時長遠小於 90s、
任務檔未產出(副作用確實被中斷)。Usage: python3 e2e_evict.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp_harness.inner_runner import run_attempt  # noqa: E402

ok = True


def check(name, cond):
    global ok
    ok = ok and bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


ws = tempfile.mkdtemp()
artifacts = tempfile.mkdtemp()
agent_cfg = {
    "backend": "rawcli", "engine": "claude", "model": "haiku",
    "os_sandbox": False,
    "timeout_sec": 150,
}
prompt = ("Run the bash command `sleep 90` and wait for it to finish. "
          "After it finishes, create a file named done.txt containing ok.")


def evict_later():
    time.sleep(8)                          # 讓 claude 起來並進入 sleep
    open(os.path.join(artifacts, "EVICT"), "w").write("evict")
    print("  [e2e] EVICT 檔已寫(t+8s)")


threading.Thread(target=evict_later, daemon=True).start()
t0 = time.time()
res = run_attempt(agent_cfg, ws, prompt, artifacts, 1)
dur = time.time() - t0
print(f"  raw={res.raw_outcome} error_kind={res.error_kind} "
      f"dur={dur:.1f}s error={res.error}")

check("error_kind=evicted(看門狗觸發)", res.error_kind == "evicted")
check("即刻終止(遠小於 sleep 90)", dur < 45)
check("副作用被中斷(done.txt 未產出)",
      not os.path.isfile(os.path.join(ws, "done.txt")))
check("raw=error(envelope 驅動,非 completed)", res.raw_outcome == "error")

print("e2e-evict:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
