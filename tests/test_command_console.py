#!/usr/bin/env python3
"""指令台(command console)頁:GET 依狀態列可用指令 + 說明表 + email;POST 驗
email/可用性/破壞性確認 → command_fn;綁票不翻 SUBMITTED;close 失效。pytest 相容。"""
from __future__ import annotations

import os
import sys
import tempfile
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from arcp.form_server import FormServer  # noqa: E402
from arcp.store import Store, TicketSession  # noqa: E402

ok = fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}")


def _sess(**kw):
    base = dict(issue_id=1, key="P-1", profile="main", workspace="ws",
                session_id="s1", attempts=1, outcome=None,
                pending_reason=None, cost_usd=0.0)
    base.update(kw)
    return TicketSession(**base)


def _server(store, calls):
    def _cmd_fn(iid, cmd, args, by, ip=""):
        calls.append((iid, cmd, args, by))   # ip 稽核另測(test_form_server)
        return True, f"已執行:{cmd}", []
    return FormServer(store, host="127.0.0.1", port=0,
                      command_fn=_cmd_fn,
                      profiles_fn=lambda: {"main": 1, "alt": 2})


# ── GET:running 狀態 → hold/stop/cancel/next + email + 說明表 ────────────── #
st = Store(tempfile.mkdtemp())
st.upsert_session(_sess())
tok = st.get_or_create_command_token(1, "P-1")
calls = []
srv = _server(st, calls)
code, html = srv._command_view(tok)
check("GET:200", code == 200)
check("GET:顯示目前狀態=進行中", "進行中" in html)
check("GET:email 欄(type=email + autocomplete)",
      "type='email'" in html and "autocomplete='email'" in html)
check("GET:列出可用指令(hold/cancel)",
      "強制中斷" in html and "取消" in html)
check("GET:附全指令說明表(用途/時機/副作用/效果)",
      "指令說明(全部)" in html and "副作用" in html)
check("GET:破壞性確認框", "confirm" in html and "破壞性" in html)
check("GET:next profile 下拉(候選 alt)", "alt" in html)


# ── POST:缺 email → 錯誤重顯;有效 run → 呼叫 command_fn ─────────────────── #
code, html = srv._command_submit(tok, {"cmd": "cancel"})   # 缺 email
check("POST:缺 email → 200 錯誤重顯", code == 200 and "請填 email" in html
      and calls == [])

# hil_middle(pending)才可 run
st2 = Store(tempfile.mkdtemp())
st2.upsert_session(_sess(pending_reason="budget"))
tok2 = st2.get_or_create_command_token(1, "P-1")
calls2 = []
srv2 = _server(st2, calls2)
code, html = srv2._command_submit(tok2, {"cmd": "run", "by": "a@x.tw"})
check("POST:有效 run → 呼叫 command_fn + 結果頁",
      calls2 == [(1, "run", {"profile": ""}, "a@x.tw")] and "已送出" in html)

# 破壞性 cancel 未勾確認 → 擋
code, html = srv._command_submit(tok, {"cmd": "cancel", "by": "a@x.tw"})
check("POST:cancel 未確認 → 擋、不呼叫", "破壞性" in html and calls == [])
# 勾了確認 → 執行
code, html = srv._command_submit(
    tok, {"cmd": "cancel", "by": "a@x.tw", "confirm": "yes"})
check("POST:cancel 已確認 → 執行", calls == [(1, "cancel", {"profile": ""},
                                              "a@x.tw")])

# 不可用指令(running 下 run)→ 擋
code, html = srv._command_submit(tok, {"cmd": "run", "by": "a@x.tw"})
check("POST:不可用指令 → 擋", "請選一個目前可用" in html)

# next 帶 profile
calls.clear()
code, html = srv._command_submit(
    tok, {"cmd": "next", "by": "a@x.tw", "profile": "alt"})
check("POST:next 帶 profile", calls == [(1, "next", {"profile": "alt"},
                                          "a@x.tw")])


# ── close 失效:INVALIDATED → 410 唯讀 ──────────────────────────────────── #
st.invalidate_ticket_commands(1)
tok_i = st.get_command_interaction(1)
code, html = srv._command_view(tok_i)
check("close 後:410 唯讀", code == 410 and "已結案" in html)
code, _ = srv._command_submit(tok_i, {"cmd": "cancel", "by": "a@x.tw",
                                      "confirm": "yes"})
check("close 後:POST 也擋(410)", code == 410)


# ── 真 HTTP GET:/form/<token> 依 kind 路由到 console ────────────────────── #
st3 = Store(tempfile.mkdtemp())
st3.upsert_session(_sess())
tok3 = st3.get_or_create_command_token(1, "P-1")
srv3 = _server(st3, [])
srv3.start()
try:
    with urllib.request.urlopen(
            f"http://127.0.0.1:{srv3.port}/form/{tok3.token}", timeout=5) as r:
        body = r.read().decode("utf-8")
    check("HTTP GET:/form/<token> 命令 kind → 指令台", "指令台" in body
          and r.status == 200)
finally:
    srv3.stop()

st.close(); st2.close(); st3.close()
print(f"test-command-console: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
