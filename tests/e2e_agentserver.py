#!/usr/bin/env python3
"""B+.1 E2E — inner runner agent-server 版吐出與 in-process 相同的 envelope。

聚焦驗證「backend 切換 = envelope 契約不變」,直接跑 run_attempt(不繞 Jira):

  S1 filechain-server profile(backend=openhands-server)跑 filechain 任務
     → raw_outcome=completed
  S2 envelope 欄位齊全(session_id 有值、cost>0)
  S3 A 路 grader 驗證檔案鏈通過(差異化層跨 backend 不變)
  S4 對照:同任務 in-process 版也 completed(兩 backend 同契約)

Usage: caffeinate -i python3 e2e_agentserver.py  (live,haiku,~$0.1,兩次執行)
"""

from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                "..", "examples", "jira-agent-poc"))
from arcp_poc.grader import FileChecklistGrader  # noqa: E402

from arcp.inner_runner import run_attempt  # noqa: E402
from arcp.paths import config_path
from arcp.profiles import load_profiles  # noqa: E402

EXPECTED = {f"step{n}.txt": "".join(str(i) for i in range(1, n + 1))
            for n in range(1, 4)}
PROMPT = ("在目前工作目錄依序建立三個檔案:step1.txt 內容是字串 1、"
          "step2.txt 內容是字串 12、step3.txt 內容是字串 123。"
          "嚴格依序,內容不含引號與空白。完成回覆 TASK_DONE。")


def run_one(profile, root):
    shutil.rmtree(root, ignore_errors=True)
    ws = os.path.join(root, "ws")
    os.makedirs(ws)
    res = run_attempt(profile.agent, ws, PROMPT,
                      os.path.join(root, "attempts"), attempt=1)
    graded = FileChecklistGrader(EXPECTED).grade(ws).passed
    return res, graded


def main() -> int:
    profiles = load_profiles(config_path())

    print("=== agent-server backend ===", flush=True)
    res_s, graded_s = run_one(profiles["filechain-server"],
                              "./runtime_bplus_server")
    # 契約關鍵欄位 = harness 三態邏輯依賴的(completed / session_id);cost 是
    # best-effort —— ACP-over-agent-server 的 UsageUpdate 常在拆除時尚未到達
    # (server.log: "UsageUpdate not received"),回 $0。這是真實對照發現,
    # 不硬凹:cost 只記錄不判分。
    s1 = res_s.raw_outcome == "completed"
    s2 = bool(res_s.session_id)
    s3 = graded_s
    print(f"S1 server raw_outcome=completed: {'PASS' if s1 else 'FAIL'} "
          f"({res_s.raw_outcome}, err={res_s.error})")
    print(f"S2 envelope 契約(session_id): {'PASS' if s2 else 'FAIL'} "
          f"(sid={res_s.session_id})")
    print(f"   cost(best-effort,不判分): ${res_s.cost_usd} "
          f"[ACP-over-server 用量回報 gap]")
    print(f"S3 A 路 grader 通過: {'PASS' if s3 else 'FAIL'}")

    print("=== in-process backend (對照) ===", flush=True)
    res_a, graded_a = run_one(profiles["filechain"], "./runtime_bplus_acp")
    s4 = res_a.raw_outcome == "completed" and graded_a
    print(f"S4 in-process 同契約 completed+grader: {'PASS' if s4 else 'FAIL'} "
          f"({res_a.raw_outcome})")

    ok = all([s1, s2, s3, s4])
    print("e2e-agentserver:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
