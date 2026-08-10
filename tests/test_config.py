#!/usr/bin/env python3
"""憑證載入:jira_credentials 的 base_url 優先序(config override > ~/.env)+ 缺項報錯。
email/token 一律只從 ~/.env。pytest-compatible,亦自跑。"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp.config import jira_credentials, load_env  # noqa: E402

ok = fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}")


def _env(**kv):
    fd, p = tempfile.mkstemp(suffix=".env")
    with os.fdopen(fd, "w") as f:
        for k, v in kv.items():
            f.write(f"{k}={v}\n")
    return p


# load_env 容錯:export 前綴 + 引號
p = _env()
open(p, "w").write("export JIRA_EMAIL='a@x.tw'\nJIRA_API_TOKEN=\"tok\"\n")
e = load_env(p)
check("load_env:export/引號容錯",
      e["JIRA_EMAIL"] == "a@x.tw" and e["JIRA_API_TOKEN"] == "tok")

# base_url:~/.env 有 → 用它
p1 = _env(JIRA_BASE_URL="https://env.example.net/", JIRA_EMAIL="a@x.tw",
          JIRA_API_TOKEN="tok")
b, em, tk = jira_credentials(p1)
check("base_url 從 ~/.env(去尾斜線)", b == "https://env.example.net"
      and em == "a@x.tw" and tk == "tok")

# base_url:config override 優先於 ~/.env
b2, _, _ = jira_credentials(p1, base_url_override="https://cfg.example.net")
check("base_url config override 優先", b2 == "https://cfg.example.net")

# base_url:~/.env 沒有、但 config 有 → 用 config(email/token 仍需 ~/.env)
p2 = _env(JIRA_EMAIL="a@x.tw", JIRA_API_TOKEN="tok")
b3, _, _ = jira_credentials(p2, base_url_override="https://only-cfg.net")
check("base_url 只在 config 也可(~/.env 只需 email/token)",
      b3 == "https://only-cfg.net")

# 缺 base_url(兩處都沒)→ 報錯
try:
    jira_credentials(p2)
    check("缺 base_url → 報錯", False)
except RuntimeError as ex:
    check("缺 base_url → 報錯(提示 config/env)",
          "jira_base_url" in str(ex) or "JIRA_BASE_URL" in str(ex))

# 缺 email/token → 報錯
p3 = _env(JIRA_BASE_URL="https://x.net")
try:
    jira_credentials(p3)
    check("缺 email/token → 報錯", False)
except RuntimeError as ex:
    check("缺 email/token → 報錯", "JIRA_EMAIL" in str(ex))

print(f"test-config: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
