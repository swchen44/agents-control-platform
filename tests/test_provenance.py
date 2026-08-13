#!/usr/bin/env python3
"""Q 波 — 過程存證 + 結案回寫(provenance)。免網 FakeSource。
情境:TICKET.md hash 比對(變才傳/不變不傳/改後再傳)、結案附件三件套
(timeline/SESSION/transcript 缺檔降級)、description 結果區(result 段置頂、
ABORTED 理由、crid/evidence/server/dashboard 行)、時長計算、附件失敗不擋。
P 波 — {crid} 插值:goal/描述/人類指示/CLAUDE.md 代入、未知占位符保留、
verify cmd 不碰。"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401

from arcp.provenance import (  # noqa: E402
    attach_ticket_md_if_changed,
    finalize_provenance,
)
from arcp.sections import parse  # noqa: E402
from arcp.store import Store, TicketSession  # noqa: E402
from arcp.workspace import interpolate, render_ticket_md, ticket_vars  # noqa: E402

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}  {detail}")


class FakeTicket:
    def __init__(self, key="T-1", description=""):
        self.id, self.key, self.summary = 1, key, "測試"
        self.description = description
        self.state, self.assignee, self.labels = "To Do", None, []


class FakeSource:
    def __init__(self):
        self.attachments: list[str] = []
        self.description = "人寫的任務內容"
        self.fail_attach = False

    def add_attachment(self, issue_id, filepath):
        if self.fail_attach:
            raise RuntimeError("jira down")
        self.attachments.append(os.path.basename(filepath))

    def get_ticket(self, issue_id):
        return FakeTicket(description=self.description)

    def set_description(self, issue_id, text):
        self.description = text


root = tempfile.mkdtemp(prefix="arcp-test-prov-")
store = Store(root)
src = FakeSource()
ws = os.path.join(root, "ws")
os.makedirs(ws)

# --- P 波:插值 --------------------------------------------------------- #
t = FakeTicket(description="crid: CR-9\nemail: a@x.com\n\n請分析 {crid} 的問題")
tv = ticket_vars(t)
check("ticket_vars 取三鍵+key", tv.get("crid") == "CR-9" and tv.get("key") == "T-1")
check("interpolate 代入+未知保留",
      interpolate("處理 {crid} 於 {key};{unknown} 保留", tv)
      == "處理 CR-9 於 T-1;{unknown} 保留")


class _Prof:
    goal = "分析 {crid} 的 Coverity 報告"
    verify = None


md = render_ticket_md(t, _Prof(), human_notes="補充:{crid} 優先")
check("TICKET.md goal/描述/人類指示皆插值",
      "分析 CR-9 的 Coverity 報告" in md
      and "請分析 CR-9 的問題" in md and "補充:CR-9 優先" in md)

# --- 2A:TICKET.md 存證(hash 比對) ------------------------------------ #
with open(os.path.join(ws, "TICKET.md"), "w") as f:
    f.write(md)
evs = attach_ticket_md_if_changed(src, store, 1, "T-1", ws)
check("首次:上傳+journal", len(src.attachments) == 1
      and evs and evs[0]["type"] == "ticket_md_attached"
      and src.attachments[0].startswith("TICKET_T-1_"))
evs = attach_ticket_md_if_changed(src, store, 1, "T-1", ws)
check("內容沒變:不重傳", len(src.attachments) == 1 and evs == [])
with open(os.path.join(ws, "TICKET.md"), "a") as f:
    f.write("\n新指示\n")
evs = attach_ticket_md_if_changed(src, store, 1, "T-1", ws)
check("內容變了:再傳一版", len(src.attachments) == 2)
src.fail_attach = True
with open(os.path.join(ws, "TICKET.md"), "a") as f:
    f.write("again\n")
evs = attach_ticket_md_if_changed(src, store, 1, "T-1", ws)
check("上傳失敗:不擲例外、不記 journal、hash 不推進(下次補傳)",
      evs == [] and len(src.attachments) == 2)
src.fail_attach = False
check("恢復後補傳", len(attach_ticket_md_if_changed(src, store, 1, "T-1", ws)) == 1
      and len(src.attachments) == 3)

# --- 2B/2C:結案回寫 ---------------------------------------------------- #
store.journal("session_created", 1, "T-1")
store.journal("attempt_started", 1, "T-1")
store.journal("attempt_finished", 1, "T-1")
store.journal("pending", 1, "T-1", reason="budget")
store.journal("attempt_started", 1, "T-1")
store.journal("attempt_finished", 1, "T-1")
store.journal("closed", 1, "T-1", by="human")
sess = TicketSession(issue_id=1, key="T-1", profile="p", workspace=ws,
                     session_id="s1", attempts=2, outcome="SUCCESS",
                     pending_reason=None, cost_usd=0.1234, human_score=8,
                     agent_score=9, tokens=45231, clearquest_id="CR-9")
store.upsert_session(sess)
n_att = len(src.attachments)
evs = finalize_provenance(src, store, sess, 1, "T-1",
                          dashboard_url="http://127.0.0.1:8788")
names = src.attachments[n_att:]
check("結案附件:timeline+SESSION(transcript 缺=算了)",
      any(n.startswith("timeline_") for n in names)
      and any(n.startswith("SESSION_") for n in names)
      and not any("final" in n for n in names))
check("journal:provenance_attached + result_written",
      {e["type"] for e in evs} >= {"provenance_attached", "result_written"})
before, secs, after = parse(src.description)
by = {s.owner: s for s in secs}
check("description:result 段存在且排最前",
      "result" in by and secs[0].owner == "result")
body = by.get("result").body if "result" in by else ""
check("結果區欄位:result/score/cost/time/crid/evidence/server/dashboard",
      all(k in body for k in ("result: SUCCESS", "人評 8/10", "agent 自評 9/10",
                              "$0.1234", "45,231 tokens", "2 attempts",
                              "crid: CR-9", "timeline_", "SESSION_",
                              f"server: {ws}",
                              "dashboard: http://127.0.0.1:8788/ticket/T-1")),
      detail=body)
check("原 description 內容保留在區塊外", "人寫的任務內容" in src.description)

# ABORTED 帶理由
sess2 = TicketSession(issue_id=1, key="T-1", profile="p", workspace="",
                      session_id=None, attempts=0, outcome="ABORTED",
                      pending_reason=None, cost_usd=0.0,
                      abort_reason="security")
evs = finalize_provenance(src, store, sess2, 1, "T-1")
before, secs, after = parse(src.description)
by = {s.owner: s for s in secs}
check("ABORTED:result 帶理由、無 ws 也不炸",
      "ABORTED(reason=security)" in by["result"].body)

shutil.rmtree(root, ignore_errors=True)
print(f"test-provenance: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
