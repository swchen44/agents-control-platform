#!/usr/bin/env python3
"""W1.2 — A3 Jira 寫入限速退避 單元測(免 token,mock 429,不發真 HTTP)。

驗 _request 退避(決策 W3:只包寫、讀不退避):
  R1 寫撞 429 兩次後成功 → 共 3 次呼叫、退避 2 次(不拋)
  R2 寫連續 429 超上限 → 放棄拋錯(不無限)、共 max+1 次、退避 max 次
  R3 寫一次過 → 不退避
  R4 讀(GET)撞 429 → 立即拋(不重試,讀靠 poll 自帶重試)

Usage: <venv>/python test_ratelimit.py
"""
from __future__ import annotations

import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp import jira_source  # noqa: E402
from arcp.jira_source import JiraCloudSource  # noqa: E402

ok = True


def check(name, cond):
    global ok
    ok = ok and cond
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


class _Resp:
    def __init__(self, payload=b"{}"):
        self._p = payload

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _install(script):
    """script: list of 'ok' | int(status). Returns a live call counter."""
    st = {"n": 0}

    def fake_urlopen(req, timeout=None, context=None):
        i = st["n"]
        st["n"] += 1
        item = script[i] if i < len(script) else script[-1]
        if item == "ok":
            return _Resp(b"{}")
        raise urllib.error.HTTPError(
            getattr(req, "full_url", "http://x"), item, f"code {item}", {}, None)

    jira_source.urllib.request.urlopen = fake_urlopen
    return st


sleeps = []
jira_source.time.sleep = lambda s: sleeps.append(s)

src = JiraCloudSource("https://x", "e@x", "tok",
                      write_retry_max=5, write_retry_base=0.01)

# -- R1:寫 429×2 後成功 ---------------------------------------------------- #
sleeps.clear()
st = _install([429, 429, "ok"])
err = None
try:
    src.add_comment(1, "hi")
except Exception as e:
    err = e
check("R1 寫 429×2 後成功(不拋)", err is None)
check("R1 共 3 次呼叫", st["n"] == 3)
check("R1 退避 2 次", len(sleeps) == 2)

# -- R2:寫連續 429 超上限 → 拋 -------------------------------------------- #
sleeps.clear()
st = _install([429] * 10)
err = None
try:
    src.add_comment(1, "hi")
except urllib.error.HTTPError as e:
    err = e
check("R2 超上限拋錯(不無限)", err is not None)
check("R2 共 max+1=6 次呼叫", st["n"] == 6)
check("R2 退避 max=5 次", len(sleeps) == 5)

# -- R3:寫一次過 → 不退避 ------------------------------------------------- #
sleeps.clear()
st = _install(["ok"])
src.add_comment(1, "hi")
check("R3 一次過不退避", st["n"] == 1 and len(sleeps) == 0)

# -- R4:讀(GET)撞 429 → 立即拋(不重試) ---------------------------------- #
sleeps.clear()
st = _install([429, "ok"])   # 若重試會用到第二項;不重試則第一次就拋
err = None
try:
    src.search("project=X")
except urllib.error.HTTPError as e:
    err = e
check("R4 讀 429 立即拋(不重試)", err is not None and st["n"] == 1)
check("R4 讀不退避", len(sleeps) == 0)

# -- W6.7:on_write 回呼(harness→Jira 寫入補記 jira_write) ------------------ #
writes = []
src.on_write = lambda action, iid, detail="": writes.append((action, iid, detail))
_install(["ok"])
src.add_comment(7, "hello")
check("W6.7 add_comment 觸發 on_write(comment)",
      writes == [("comment", 7, "hello")])
_install(["ok"])
src.assign(7, "acct-9")
check("W6.7 assign 觸發 on_write(assign)", writes[-1] == ("assign", 7, "acct-9"))
_install(["ok"])
src.assign(7, None)     # 取消指派
check("W6.7 assign(None) 記(取消指派)", writes[-1][0] == "assign"
      and "取消" in writes[-1][2])
# 回呼壞掉不可影響寫入本身(only warn)
src.on_write = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
_install(["ok"])
err = None
try:
    src.add_comment(7, "x")
except Exception as e:
    err = e
check("W6.7 on_write 拋錯不影響 Jira 寫入", err is None)
# 預設 on_write=None → 不記、不炸
src2 = JiraCloudSource("https://x", "e@x", "tok")
_install(["ok"])
src2.add_comment(1, "y")
check("W6.7 預設 on_write=None 安全", src2.on_write is None)

print("test-ratelimit:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
