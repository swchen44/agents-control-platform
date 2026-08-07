#!/usr/bin/env python3
"""C.2 E2E — 細粒度事件:RawCLIAgent 事件數從 5 → 數十(每個帶真實工具參數),
原生 stream-json 全保留(A 級保真度)。

  D1 grader 過(真 claude 建三檔)
  D2 OpenHands 事件數 >> C.1 的 5(細粒度回歸)
  D3 原生 stream-json 全量落檔(raw_events_path,A 級保真/協定回歸)
  D4 事件含 tool(🔧)與 observation(📋)標記(detail page 可分渲染)

Usage: caffeinate -i .venv/python e2e_c2.py  (live,haiku,~$0.05)
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
    root = os.path.join(HERE, "runtime_c2")
    ws = os.path.join(root, "ws")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(ws)
    raw_path = os.path.join(root, "raw.jsonl")
    captured: list = []

    agent = RawCLIAgent(raw_events_path=raw_path)
    conv = Conversation(agent=agent, workspace=ws, callbacks=[captured.append])
    conv.send_message(PROMPT)
    conv.run()

    d1 = FileChecklistGrader(EXPECTED).grade(ws).passed
    print(f"D1 grader 過: {'PASS' if d1 else 'FAIL'}")
    agent_msgs = [_text(e) for e in captured
                  if isinstance(e, MessageEvent) and e.source == "agent"]
    d2 = len(agent_msgs) > 5
    print(f"D2 OpenHands 事件數 >> C.1(5): {'PASS' if d2 else 'FAIL'} "
          f"({len(agent_msgs)} 則 agent 事件)")
    raw_n = sum(1 for _ in open(raw_path)) if os.path.exists(raw_path) else 0
    # A 級保真 = 原生流全量保留,且遠多於蒸餾後的有意義事件(兩層策略)
    d3 = raw_n >= 3 * max(1, len(agent_msgs))
    print(f"D3 原生 stream-json 全量(raw >> 蒸餾事件): {'PASS' if d3 else 'FAIL'} "
          f"({raw_n} 行 raw vs {len(agent_msgs)} 蒸餾事件)")
    has_tool = any(m.startswith("🔧") for m in agent_msgs)
    has_obs = any(m.startswith("📋") for m in agent_msgs)
    d4 = has_tool and has_obs
    print(f"D4 事件含 tool(🔧)+ observation(📋)標記: {'PASS' if d4 else 'FAIL'} "
          f"(tool={has_tool}, obs={has_obs})")

    ok = all([d1, d2, d3, d4])
    print(f"raw {raw_n} 行 → {len(agent_msgs)} 則有意義事件 "
          f"(cost ${agent._cost_usd})")
    print("e2e-c2:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def _text(e) -> str:
    c = (e.llm_message.content if hasattr(e, "llm_message") else []) or []
    return " ".join(x.text for x in c if hasattr(x, "text"))


if __name__ == "__main__":
    sys.exit(main())
