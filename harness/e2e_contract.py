#!/usr/bin/env python3
"""W1.4 E2E — 真跑 claude(stream-json + --json-schema),驗 envelope.structured
端到端。單元測(test_contract)用假輸出;這裡驗 stream-json 模式下 result 事件
真的帶 structured_output(探針是 --output-format json,rawcli 走 stream-json)。

少量 token(haiku + 極短任務)。Usage: <venv>/python e2e_contract.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp_harness.contract import validate_structured  # noqa: E402
from arcp_harness.inner_runner import run_attempt  # noqa: E402

ok = True


def check(name, cond):
    global ok
    ok = ok and cond
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


ws = tempfile.mkdtemp()
artifacts = tempfile.mkdtemp()
agent_cfg = {
    "backend": "rawcli", "engine": "claude", "model": "haiku",
    "os_sandbox": False, "output_schema": True,
    "venv": "../examples/openhands-acp-poc/.venv", "timeout_sec": 150,
}
prompt = ("Create a file named done.txt containing exactly the text ok. "
          "Then report that the task is complete: status is done, "
          "reason one short sentence.")

res = run_attempt(agent_cfg, ws, prompt, artifacts, 1)
print("  raw_outcome:", res.raw_outcome, "| cost:", res.cost_usd)
print("  structured:", res.structured)

check("E2E stream-json 帶 structured(非空)", res.structured is not None)
if res.structured:
    v, why = validate_structured(res.structured)
    check(f"E2E structured 合契約({why})", v)
    check("E2E status=done", res.structured.get("status") == "done")
check("E2E done.txt 建立", os.path.isfile(os.path.join(ws, "done.txt")))

print("e2e-contract:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
