#!/usr/bin/env python3
"""rawcli _build_command 組裝(2026-08-14:command 覆寫/model 省略/extra_args)。
免網、免 spawn——只驗 argv。"""
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


def cmd_of(**kw):
    return RawCLIAgent(**kw)._build_command("PROMPT")


# claude 預設
c = cmd_of(engine="claude", model="haiku")
check("claude: 預設執行檔=claude、有 --model",
      c[0] == "claude" and "--model" in c and c[c.index("--model") + 1] == "haiku")

# command 覆寫(內網包裝路徑)
c = cmd_of(engine="claude", model="haiku", command="/tools/bin/claudeoss")
check("claude: command 覆寫成絕對路徑", c[0] == "/tools/bin/claudeoss")

# model 未設 → 不帶 --model
c = cmd_of(engine="claude", model=None)
check("claude: model 未設不帶 --model", "--model" not in c)

# extra_args 接最後
c = cmd_of(engine="claude", model=None, extra_args=["--flag-x", "val"])
check("claude: extra_args 原樣接 argv 尾", c[-2:] == ["--flag-x", "val"])

# codex 各項
c = cmd_of(engine="codex", model=None, command="/opt/wrap/codexoss",
           extra_args=["--zz"])
check("codex: command 覆寫+無 model 不帶 --model+extra 接尾",
      c[0] == "/opt/wrap/codexoss" and "--model" not in c and c[-1] == "--zz")
c = cmd_of(engine="codex", model="o4-mini")
check("codex: 有 model 才帶", "--model" in c)

# codex resume 分支也吃 command+extra_args
c = cmd_of(engine="codex", command="/opt/wrap/codexoss", session_id="sid-1",
           resume=True, extra_args=["--zz"])
check("codex resume: command 覆寫+extra 接尾",
      c[0] == "/opt/wrap/codexoss" and c[1:3] == ["exec", "resume"]
      and c[-1] == "--zz")

# claude resume 分支
c = cmd_of(engine="claude", session_id="sid-2", resume=True,
           command="claudeoss", extra_args=["--yy"])
check("claude resume: command 覆寫+--resume sid+extra 接尾",
      c[0] == "claudeoss" and "--resume" in c and c[-1] == "--yy")

print(f"test-rawcli-cmd: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
