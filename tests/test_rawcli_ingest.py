#!/usr/bin/env python3
"""rawcli _ingest_claude 蒸餾分類(VIZ 2026-08-16:async/subassistant/memory
預留)。免網、免 spawn——直接餵 stream-json dict 收 emit 事件。
情境:sidechain 傳染、memory beats sidechain、task-notification→async、
一般分類不變、雜訊 user text 不蒸餾。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))
from arcp.rawcli.agent import RawCLIAgent  # noqa: E402

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}  {detail}")


def ingest(*objs):
    """跑一串 stream-json 物件,回收 (category, source, text) 清單。"""
    a = RawCLIAgent(engine="claude")
    got = []
    for o in objs:
        a._ingest_claude(o, lambda e: got.append(
            (e["category"], e["source"],
             e["llm_message"]["content"][0]["text"])))
    return got


def asst(blocks, parent=None):
    return {"type": "assistant", "parent_tool_use_id": parent,
            "message": {"content": blocks}}


def user(blocks, parent=None):
    return {"type": "user", "parent_tool_use_id": parent,
            "message": {"content": blocks}}


MEM = "/Users/x/.claude/projects/-Users-x-proj/memory/MEMORY.md"

# 一般分類不變(baseline)
got = ingest(
    {"type": "system", "subtype": "init", "model": "haiku",
     "permissionMode": "acceptEdits"},
    asst([{"type": "thinking", "thinking": "想"},
          {"type": "text", "text": "好"},
          {"type": "tool_use", "id": "t1", "name": "Bash",
           "input": {"command": "ls"}}]),
    user([{"type": "tool_result", "tool_use_id": "t1", "content": "out"}]))
check("baseline:system/thinking/text/tool/tool_result 分類不變",
      [c for c, _, _ in got] == ["system", "thinking", "text",
                                 "tool", "tool_result"], got)

# sidechain(parent_tool_use_id 非空)→ subassistant 傳染全部內層型別
got = ingest(
    asst([{"type": "text", "text": "sub 說話"},
          {"type": "thinking", "thinking": "sub 想"},
          {"type": "tool_use", "id": "t2", "name": "Read",
           "input": {"file_path": "/tmp/a.txt"}}], parent="task_1"),
    user([{"type": "tool_result", "tool_use_id": "t2", "content": "檔案內容"}],
         parent="task_1"))
check("sidechain:text/thinking/tool/tool_result 全 → subassistant",
      [c for c, _, _ in got] == ["subassistant"] * 4, got)

# memory:Read/Write/Edit 落 memory 路徑 → memory;配對 result 傳染;
# memory beats sidechain
got = ingest(
    asst([{"type": "tool_use", "id": "m1", "name": "Read",
           "input": {"file_path": MEM}}], parent="task_1"),
    user([{"type": "tool_result", "tool_use_id": "m1", "content": "索引"}],
         parent="task_1"),
    asst([{"type": "tool_use", "id": "n1", "name": "Read",
           "input": {"file_path": "/tmp/not-memory.md"}}]))
check("memory:memory 路徑 tool+配對 result → memory(beats sidechain);"
      "非 memory 路徑照舊",
      [c for c, _, _ in got] == ["memory", "memory", "tool"], got)

# task-notification user text → async(source=user);一般 user text 不蒸餾
got = ingest(
    user([{"type": "text",
           "text": "<task-notification>bg 任務完成</task-notification>"}]),
    user([{"type": "text", "text": "一般注入雜訊"}]),
    user([{"type": "text", "text": "<task-notification>x</task-notification>"}],
         parent="task_1"))     # sidechain 的通知不蒸餾(prompt 重複)
check("async:<task-notification> → async/source=user;其他 user text 與 "
      "sidechain 通知不蒸餾",
      len(got) == 1 and got[0][0] == "async" and got[0][1] == "user", got)

print(f"test-rawcli-ingest: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
