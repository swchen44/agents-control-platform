#!/usr/bin/env python3
"""C.1 E2E — RawCLIAgent in-process 跑通 filechain(保底路徑)。

  C1 Conversation(agent=RawCLIAgent) 跑 filechain → FINISHED
  C2 A 路 grader 驗證檔案鏈通過(真 claude -p 建檔)
  C3 agent 暴露 session_id + cost(envelope 前置,C.3 接 runner)
  C4 事件流有 assistant MessageEvent(基本事件;C.2 補細粒度)

Usage: caffeinate -i .venv/python e2e_c1.py  (live,haiku,~$0.05)
"""

from __future__ import annotations

import os
import shutil
import sys

os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "examples", "jira-agent-poc"))

from arcp_poc.grader import FileChecklistGrader  # noqa: E402
from openhands.sdk.conversation import Conversation  # noqa: E402
from openhands.sdk.event.llm_convertible import MessageEvent  # noqa: E402

from arcp_rawcli import RawCLIAgent  # noqa: E402

EXPECTED = {f"step{n}.txt": "".join(str(i) for i in range(1, n + 1))
            for n in range(1, 4)}
PROMPT = ("在目前工作目錄依序建立三個檔案:step1.txt 內容是字串 1、"
          "step2.txt 內容是字串 12、step3.txt 內容是字串 123。"
          "嚴格依序,內容不含引號與空白。完成回覆 TASK_DONE。")


def main() -> int:
    root = os.path.join(HERE, "runtime_c1")
    ws = os.path.join(root, "ws")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(ws)
    captured: list = []

    agent = RawCLIAgent()
    conv = Conversation(agent=agent, workspace=ws, callbacks=[captured.append])
    conv.send_message(PROMPT)
    conv.run()

    c1 = str(conv.state.execution_status).endswith("FINISHED") or \
        conv.state.execution_status.value == "finished"
    print(f"C1 Conversation FINISHED: {'PASS' if c1 else 'FAIL'} "
          f"({conv.state.execution_status})")
    c2 = FileChecklistGrader(EXPECTED).grade(ws).passed
    print(f"C2 A 路 grader 檔案鏈通過: {'PASS' if c2 else 'FAIL'}")
    c3 = bool(agent.session_id)
    print(f"C3 agent 暴露 session_id/cost: {'PASS' if c3 else 'FAIL'} "
          f"(sid={agent.session_id}, cost=${agent._cost_usd})")
    msgs = [e for e in captured
            if isinstance(e, MessageEvent) and e.source == "agent"]
    c4 = len(msgs) >= 1
    print(f"C4 assistant MessageEvent(基本事件): {'PASS' if c4 else 'FAIL'} "
          f"({len(msgs)} 則;captured {len(captured)} events)")

    ok = all([c1, c2, c3, c4])
    print("e2e-c1:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
