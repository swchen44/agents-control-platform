#!/usr/bin/env python3
"""rerun 指令(2026-08-15 use case:資訊更新後同票乾淨重跑)。免網。
情境:aborted 復活/各態可用性/reset 欄位/舊 workspace 刪除/note→human 段/
哨值→重佈建路徑/journal。"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))
from arcp.commands import apply_command, available_commands  # noqa: E402
from arcp.store import Store, TicketSession  # noqa: E402

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}  {detail}")


class FakeTicket:
    def __init__(self, description=""):
        self.description = description


class FakeSource:
    def __init__(self):
        self.description = "原任務描述(人已更新過)"
        self.comments: list = []

    def get_ticket(self, iid):
        return FakeTicket(self.description)

    def set_description(self, iid, text):
        self.description = text

    def add_comment(self, iid, body):
        self.comments.append(body)


root = tempfile.mkdtemp(prefix="arcp-test-rerun-")
store = Store(root)
src = FakeSource()

# 建一個 ABORTED 票(有真實 workspace 含舊產出)
ws = os.path.join(root, "tickets", "p__T-1__1", "ws")
os.makedirs(ws)
open(os.path.join(ws, "ARTICLE.md"), "w").write("舊產出(會騙過 verify)")
sess = TicketSession(issue_id=1, key="T-1", profile="p", workspace=ws,
                     session_id="old-sid", attempts=2, outcome="ABORTED",
                     pending_reason=None, cost_usd=0.1, abort_reason="cancel")
store.upsert_session(sess)

check("可用性:aborted 態開放 rerun(復活路徑)",
      "rerun" in available_commands(sess))
check("可用性:hil_end 態開放 rerun",
      "rerun" in available_commands(TicketSession(
          issue_id=9, key="T-9", profile="p", workspace="w", session_id="s",
          attempts=1, outcome="FAILURE", pending_reason=None, cost_usd=0)))
check("可用性:todo(無 session)不開放",
      available_commands(None) == [])

okc, msg, evs = apply_command(src, store, {"p": None}, 1, "rerun",
                              {"note": "改用新版 spec 重做"}, by="me@x.tw")
check("rerun 執行 ok", okc, detail=msg)
s2 = store.get_session(1)
check("reset:session_id/attempts/outcome/abort_reason 全清",
      s2.session_id is None and s2.attempts == 0 and s2.outcome is None
      and s2.abort_reason is None and s2.pending_reason is None)
check("哨值 (rerun) → 下輪 health_check 不健康觸發重佈建",
      s2.workspace == "(rerun)")
check("舊 workspace 已刪(殘檔不會騙過 verify)", not os.path.isdir(ws))
check("note 寫進 description human 段(渲染進新 TICKET.md)",
      "[rerun] 改用新版 spec 重做" in src.description
      and "原任務描述" in src.description)
check("journal:rerun 事件(帶 author/note)",
      any(e.get("type") == "rerun" and e.get("author") == "me@x.tw"
          and e.get("note") is True for e in evs))

# 無 note 版:description 不動
src2 = FakeSource()
sess3 = TicketSession(issue_id=2, key="T-2", profile="p", workspace="(adopted)",
                      session_id="s", attempts=1, outcome="FAILURE",
                      pending_reason=None, cost_usd=0)
store.upsert_session(sess3)
okc, msg, evs = apply_command(src2, store, {"p": None}, 2, "rerun", {},
                              by="me@x.tw")
check("無 note:ok、description 不動、哨值 workspace 不炸",
      okc and src2.description == "原任務描述(人已更新過)"
      and store.get_session(2).workspace == "(rerun)")

shutil.rmtree(root, ignore_errors=True)
print(f"test-rerun: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
