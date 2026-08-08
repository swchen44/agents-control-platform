#!/usr/bin/env python3
"""C.4 E2E — crash→resume in RawCLIAgent(對照 A 路 midtool 矩陣)。

  attempt1: RawCLIAgent(fault_kill_on_file=step2.txt)→ claude 子進程在 step2
            出現後被 SIGKILL → 無 terminal 事件 → completed=False(A 路
            SIGTERM-rc=0 教訓的 RawCLIAgent 版:進程死≠完成)
  attempt2: RawCLIAgent(resume=True, session_id=同)→ --resume 重接 → 續建
            step3 → grader 過

  R1 attempt1 crash:completed=False(_got_terminal=False)
  R2 attempt2 resume 完成:_got_terminal=True、grader 過
  R3 同 session_id(--resume 重接原對話)
  R4 不重工:crash 前 step1/step2 的 mtime 不變

Usage: caffeinate -i .venv/python e2e_c4.py  (live,haiku,~$0.1,兩 attempt)
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

from arcp.rawcli import RawCLIAgent  # noqa: E402

EXPECTED = {f"step{n}.txt": "".join(str(i) for i in range(1, n + 1))
            for n in range(1, 4)}
PROMPT = ("在目前工作目錄依序建立三個檔案:step1.txt 內容是字串 1、"
          "step2.txt 內容是字串 12、step3.txt 內容是字串 123。"
          "必須嚴格依序,每建一個檔先執行 sleep 2 再做下一個。"
          "內容不含引號與空白。完成回覆 TASK_DONE。")
RESUME_PROMPT = "繼續完成先前的任務,不要重做已完成的步驟。完成回覆 TASK_DONE。"


def run(agent, ws, prompt):
    conv = Conversation(agent=agent, workspace=ws, callbacks=[])
    conv.send_message(prompt)
    conv.run()
    return agent


def main() -> int:
    import uuid
    root = os.path.join(HERE, "runtime_c4")
    ws = os.path.join(root, "ws")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(ws)
    sid = str(uuid.uuid4())

    # attempt1: crash mid-task (kill claude 1s after step2.txt appears)
    a1 = RawCLIAgent(session_id=sid, fault_kill_on_file="step2.txt",
                     fault_delay=1.0)
    run(a1, ws, PROMPT)
    pre = {f: os.path.getmtime(os.path.join(ws, f))
           for f in EXPECTED if os.path.exists(os.path.join(ws, f))}
    r1 = not a1._got_terminal
    print(f"R1 attempt1 crash(completed=False): {'PASS' if r1 else 'FAIL'} "
          f"(_got_terminal={a1._got_terminal}, files={sorted(pre)})")

    # attempt2: resume the SAME session
    a2 = RawCLIAgent(session_id=sid, resume=True)
    run(a2, ws, RESUME_PROMPT)
    r2 = a2._got_terminal and FileChecklistGrader(EXPECTED).grade(ws).passed
    print(f"R2 attempt2 resume 完成+grader: {'PASS' if r2 else 'FAIL'} "
          f"(_got_terminal={a2._got_terminal})")
    r3 = a2.session_id == sid
    print(f"R3 同 session_id(--resume 重接): {'PASS' if r3 else 'FAIL'} "
          f"({a2.session_id})")
    r4 = all(os.path.getmtime(os.path.join(ws, f)) == m
             for f, m in pre.items())
    print(f"R4 不重工(crash 前檔案 mtime 不變): {'PASS' if r4 else 'FAIL'}")

    ok = all([r1, r2, r3, r4])
    print("e2e-c4:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
