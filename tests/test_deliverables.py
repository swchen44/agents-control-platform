#!/usr/bin/env python3
"""交付物貼回 Jira:ADF builder + build_comment_adf + post_deliverables。確定性、免網。

設計見 docs/design/agent-output.md。用 FakeSource 攔 add_comment_adf / add_attachment。"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from arcp import adf  # noqa: E402
from arcp.deliverables import build_comment_adf, post_deliverables  # noqa: E402
from arcp.output import ATTACH_TOTAL_LIMIT, load_output  # noqa: E402
from arcp.store import Store, TicketSession  # noqa: E402
from arcp.ticket import Ticket  # noqa: E402

ok = fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}")


def _texts(node) -> str:
    """遞迴抓 ADF 內所有 text(方便斷言內容出現)。"""
    out = []
    if isinstance(node, dict):
        if node.get("type") == "text":
            out.append(node.get("text", ""))
        for v in node.get("content", []) or []:
            out.append(_texts(v))
        # link href 也抓
        for m in node.get("marks", []) or []:
            if m.get("type") == "link":
                out.append(m["attrs"]["href"])
    elif isinstance(node, list):
        for v in node:
            out.append(_texts(v))
    return " ".join(out)


class FakeSource:
    base_url = "https://x.atlassian.net"

    def __init__(self):
        self.adf_comments = []
        self.attachments = []

    def add_comment_adf(self, iid, adf_doc, detail=""):
        self.adf_comments.append((iid, adf_doc, detail))

    def add_attachment(self, iid, path):
        self.attachments.append((iid, os.path.basename(path)))
        return {"filename": os.path.basename(path)}


def _ws(output=None, files=None):
    d = tempfile.mkdtemp()
    ws = os.path.join(d, "ws"); os.makedirs(ws)
    if output is not None:
        json.dump(output, open(os.path.join(ws, "OUTPUT.json"), "w"))
    for rel, size in (files or {}).items():
        p = os.path.join(ws, rel)
        os.makedirs(os.path.dirname(p) or ws, exist_ok=True)
        open(p, "wb").write(b"x" * size)
    return d, ws


def _sess(iid, ws):
    return TicketSession(issue_id=iid, key=f"SCRUM-{iid}", profile="p",
                         workspace=ws, session_id="s1", attempts=2,
                         outcome="SUCCESS", pending_reason=None, cost_usd=0.5)


def _tk(iid):
    return Ticket(id=iid, key=f"SCRUM-{iid}", summary="s", state="進行中",
                  assignee=None, assignee_id=None, labels=[], description="")


# ── adf builder ──────────────────────────────────────────────────────── #
d = adf.doc(adf.heading("H", 3), adf.paragraph(adf.strong("b:"), " x"),
            adf.bullet_list([[adf.link("L", "http://u")]]))
check("adf:doc/heading/paragraph/bulletList/link 結構",
      d["type"] == "doc" and d["content"][0]["type"] == "heading"
      and "http://u" in _texts(d) and "L" in _texts(d))

# ── build_comment_adf ────────────────────────────────────────────────── #
o = load_output(_ws({
    "summary_md": "narrative",
    "code": [{"system": "gerrit", "url": "http://g/+/1", "note": "改了X"}],
    "attachments": ["a.md"], "references": [{"label": "R", "path_or_url": "/z"}]})[1])
body = build_comment_adf(outcome="SUCCESS", attempt=2, cost_usd=0.5,
                        self_summary="完成A未做B", output=o,
                        attach_names=["a.md"], mode="attach",
                        download_url=None, base_url=None, key="SCRUM-1")
t = _texts(body)
check("build:含 outcome/自報/程式碼/附件", "SUCCESS" in t and "完成A未做B" in t
      and "http://g/+/1" in t and "改了X" in t and "a.md" in t)
body_link = build_comment_adf(outcome="SUCCESS", attempt=1, cost_usd=0,
                             self_summary="", output=o, attach_names=["big.zip"],
                             mode="link", download_url="http://h/files/tok",
                             base_url=None, key="SCRUM-1")
check("build:link 模式含下載連結", "http://h/files/tok" in _texts(body_link))

# ── post_deliverables:有 OUTPUT.json + 小檔 → 附 + ADF comment + journal ─ #
rt = tempfile.mkdtemp(); st = Store(rt); src = FakeSource()
d1, ws1 = _ws({"summary_md": "x", "attachments": ["r.md", "d.png"]},
              files={"r.md": 100, "d.png": 200})
evs = post_deliverables(src, st, _tk(1), _sess(1, ws1), outcome="SUCCESS",
                        self_summary="done")
check("post attach:兩個小檔都附到 issue",
      {n for _, n in src.attachments} == {"r.md", "d.png"})
check("post attach:貼了一則 ADF comment", len(src.adf_comments) == 1)
check("post attach:journal deliverables_posted(has_output,mode=attach)",
      any(e["type"] == "deliverables_posted" and e["has_output"]
          and e["mode"] == "attach" and e["n_attachments"] == 2 for e in evs))

# ── ≥6MB → link 模式:不附、mode=link ─────────────────────────────────── #
src2 = FakeSource()
d2, ws2 = _ws({"attachments": ["big.bin"]},
              files={"big.bin": ATTACH_TOTAL_LIMIT + 10})
evs2 = post_deliverables(src2, st, _tk(2), _sess(2, ws2), outcome="SUCCESS",
                         self_summary="")
check("post link:大檔不附到 issue", src2.attachments == [])
check("post link:journal mode=link", any(
    e["type"] == "deliverables_posted" and e["mode"] == "link" for e in evs2))

# ── 無 OUTPUT.json → 不貼 comment、journal has_output=false ────────────── #
src3 = FakeSource()
d3, ws3 = _ws(output=None)
evs3 = post_deliverables(src3, st, _tk(3), _sess(3, ws3), outcome="FAILURE",
                         self_summary="沒產出")
check("post 無 output:不貼 ADF comment", src3.adf_comments == [])
check("post 無 output:journal has_output=false", any(
    e["type"] == "deliverables_posted" and not e["has_output"] for e in evs3))

# ── 路徑穿越附件被跳過 + 記 skipped ──────────────────────────────────── #
src4 = FakeSource()
d4, ws4 = _ws({"attachments": ["ok.md", "../evil"]}, files={"ok.md": 50})
open(os.path.join(d4, "evil"), "w").write("pwn")
evs4 = post_deliverables(src4, st, _tk(4), _sess(4, ws4), outcome="SUCCESS",
                         self_summary="")
check("post 安全:越界附件被跳過(只附 ok.md)",
      {n for _, n in src4.attachments} == {"ok.md"}
      and any(e.get("skipped") == 1 for e in evs4
              if e["type"] == "deliverables_posted"))
st.close()

print(f"test-deliverables: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
