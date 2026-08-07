#!/usr/bin/env python3
"""執行隔離 E2E — RawCLIAgent 的 sandbox(claude seatbelt / codex 內建)。

  S1 os_sandbox=False(對照):claude 能寫 workspace 外(/tmp)—— 無隔離
  S2 os_sandbox=True:claude 寫 workspace 內成功、寫 /tmp 被擋 —— 隔離生效
  (codex 用內建 --sandbox,quota 鎖著;build_command 已含 --sandbox)

Usage: caffeinate -i .venv/python e2e_sandbox.py  (live,haiku,~$0.08,兩次)
"""

from __future__ import annotations

import glob
import os
import shutil
import sys

os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from openhands.sdk.conversation import Conversation  # noqa: E402

from arcp_rawcli import RawCLIAgent  # noqa: E402

EVIL = "/tmp/arcp_sbx_probe.txt"
PROMPT = ("做兩件事,兩件都要嘗試:"
          "1) 用 Write 建立 inside.txt 內容 ok;"
          "2) 用 Bash 執行 `echo hacked > " + EVIL + "` 嘗試寫工作目錄外。"
          "最後回報各自成功或失敗。")


def run(os_sandbox: bool, tag: str):
    root = os.path.join(HERE, "runtime_sbx_e2e", tag)
    ws = os.path.join(root, "ws")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(ws)
    for f in glob.glob(EVIL):
        os.remove(f)
    agent = RawCLIAgent(os_sandbox=os_sandbox,
                        allowed_tools=["Write", "Read", "Bash"])
    conv = Conversation(agent=agent, workspace=ws, callbacks=[])
    conv.send_message(PROMPT)
    conv.run()
    inside = os.path.isfile(os.path.join(ws, "inside.txt"))
    escaped = os.path.exists(EVIL)
    return inside, escaped


def main() -> int:
    picks = sys.argv[1:] or ["off", "on"]
    ok = True
    if "off" in picks:
        ins, esc = run(False, "off")
        s1 = ins and esc  # 無隔離:兩者皆成功(對照證明 probe 有效)
        print(f"S1 os_sandbox=False:inside={ins} escaped={esc} "
              f"→ {'PASS(無隔離,/tmp 可寫)' if s1 else 'note'}")
    if "on" in picks:
        ins, esc = run(True, "on")
        s2 = ins and not esc  # 隔離:workspace 內成功、/tmp 被擋
        print(f"S2 os_sandbox=True :inside={ins} escaped={esc} "
              f"→ {'PASS(隔離生效)' if s2 else 'FAIL'}")
        ok = s2
    print("e2e-sandbox:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
