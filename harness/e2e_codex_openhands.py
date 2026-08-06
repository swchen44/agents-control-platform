#!/usr/bin/env python3
"""W5.4 E2E — openhands 系 backend 跑 codex 對照(花 codex 額度)。

補上 2026-08-03 被 quota 擋下的最後一塊對照:三 backend × 雙引擎矩陣的
B(openhands-acp in-process)與 B+(openhands-server)codex 欄。
adapter = SDK 釘死表 `codex → npx @agentclientprotocol/codex-acp`。

驗「換 backend/引擎不換契約」:completed / session_id / grader 過;
cost 為 best-effort(ACP 用量回報 gap 已知,不判分——同 e2e_agentserver)。

Usage: caffeinate -i python3 e2e_codex_openhands.py [acp|server]  (預設兩個都跑)
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "examples", "jira-agent-poc"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp_poc.grader import FileChecklistGrader  # noqa: E402

from arcp_harness.inner_runner import run_attempt  # noqa: E402

EXPECTED = {f"step{n}.txt": "".join(str(i) for i in range(1, n + 1))
            for n in range(1, 4)}
PROMPT = ("在目前工作目錄依序建立三個檔案:step1.txt 內容是字串 1、"
          "step2.txt 內容是字串 12、step3.txt 內容是字串 123。"
          "嚴格依序,內容不含引號與空白。完成回覆 TASK_DONE。")

ok = True


def check(name, cond):
    global ok
    ok = ok and bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


def run_backend(backend: str) -> None:
    ws = tempfile.mkdtemp()
    artifacts = tempfile.mkdtemp()
    agent_cfg = {
        "backend": backend,
        "acp_server": "codex",           # SDK 釘死表 → codex-acp adapter
        # acp_model 不給:codex 用帳號預設(claude model 名不適用,W3.1 教訓)
        "venv": "../examples/openhands-acp-poc/.venv",
        "timeout_sec": 420,
    }
    res = run_attempt(agent_cfg, ws, PROMPT, artifacts, 1)
    graded = FileChecklistGrader(EXPECTED).grade(ws).passed
    tag = f"[{backend}+codex]"
    print(f"  {tag} raw={res.raw_outcome} sid={res.session_id} "
          f"cost={res.cost_usd} err={res.error}")
    check(f"{tag} completed(envelope 驅動)", res.raw_outcome == "completed")
    check(f"{tag} session_id 非空(契約欄位)", bool(res.session_id))
    check(f"{tag} grader 檔案鏈通過(差異化層跨 backend 不變)", graded)
    print(f"  {tag} cost(best-effort,不判分): ${res.cost_usd}")


targets = sys.argv[1:] or ["acp", "server"]
for t in targets:
    backend = {"acp": "openhands-acp", "server": "openhands-server"}[t]
    print(f"=== {backend} + codex ===", flush=True)
    run_backend(backend)

print("e2e-codex-openhands:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
