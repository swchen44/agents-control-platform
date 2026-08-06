#!/usr/bin/env python3
"""W1.4/W3.1 E2E — 真跑雙引擎 G1 結構化契約。

claude:stream-json + `--json-schema '<inline>'`,result 事件帶 structured_output。
codex:`--output-schema <FILE>`,最終 agent_message 即 schema JSON(W3.1 擴)。
單元測(test_contract)用假輸出;這裡驗真 CLI 端到端。

少量 token(claude haiku / codex 帳號預設 + 極短任務)。
Usage: python3 e2e_contract.py [claude|codex]   (預設兩個都跑)
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
    ok = ok and bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


def run_engine(engine: str) -> None:
    ws = tempfile.mkdtemp()
    artifacts = tempfile.mkdtemp()
    agent_cfg = {
        "backend": "rawcli", "engine": engine,
        "model": "haiku" if engine == "claude" else None,
        "os_sandbox": False, "sandbox": "workspace-write",
        "output_schema": True,
        "timeout_sec": 240,
    }
    prompt = ("Create a file named done.txt containing exactly the text ok. "
              "Then report that the task is complete: status is done, "
              "reason one short sentence.")

    res = run_attempt(agent_cfg, ws, prompt, artifacts, 1)
    print(f"  [{engine}] raw_outcome:", res.raw_outcome,
          "| cost:", res.cost_usd)
    print(f"  [{engine}] structured:", res.structured)

    check(f"[{engine}] structured 非空", res.structured is not None)
    if res.structured:
        v, why = validate_structured(res.structured)
        check(f"[{engine}] structured 合契約({why})", v)
        check(f"[{engine}] status=done",
              res.structured.get("status") == "done")
    check(f"[{engine}] done.txt 建立",
          os.path.isfile(os.path.join(ws, "done.txt")))


engines = sys.argv[1:] or ["claude", "codex"]
for eng in engines:
    run_engine(eng)

print("e2e-contract:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
