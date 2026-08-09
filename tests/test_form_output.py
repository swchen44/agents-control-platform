#!/usr/bin/env python3
"""表單頁交付物駕駛艙:安全 md→html、_deliverables_html、_deliverable_files。免網。

設計見 docs/design/agent-output.md。重點:agent 內容先 escape 再套格式(防 XSS)、
下載只服務 OUTPUT.json 宣告且在 workspace 內的檔。"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from arcp import form_server as fs  # noqa: E402
from arcp.interaction import build_request  # noqa: E402
from arcp.store import Store, TicketSession  # noqa: E402

ok = fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}")


# ── _md_to_html:安全 + 格式 ──────────────────────────────────────────── #
h = fs._md_to_html("# 標題\n**粗** 與 `code`\n- a\n- b\n[x](https://ok)")
check("md:標題→<h3>", "<h3>標題</h3>" in h)
check("md:粗體/行內碼", "<strong>粗</strong>" in h and "<code>code</code>" in h)
check("md:清單", "<ul>" in h and "<li>a</li>" in h and "<li>b</li>" in h)
check("md:http 連結放行", "<a href='https://ok'" in h)
xss = fs._md_to_html("<script>alert(1)</script> [e](javascript:evil)")
check("md:XSS <script> 被 escape", "<script>" not in xss and "&lt;script&gt;" in xss)
check("md:javascript: 連結不被當連結", "href='javascript" not in xss)
code = fs._md_to_html("```\n<b>raw</b>\n```")
check("md:code fence 內也 escape", "<pre><code>" in code and "&lt;b&gt;" in code)


# ── _deliverables_html:渲染 + 下載連結 + 無 output 降級 ───────────────── #
def _req(payload):
    r = build_request(1, "SCRUM-1", "score_and_close", payload=payload)
    return r


r = _req({"jira_url": "https://j/browse/SCRUM-1", "cost_usd": 0.5, "attempts": 2,
          "clearquest_id": "CR-9",
          "deliverables": {"summary_md": "# 成果\n做完了",
                           "code": [{"url": "https://g/+/1", "note": "改X"}],
                           "attachments": [{"name": "r.md", "rel": "r.md",
                                            "size": 2048}],
                           "references": [{"label": "R", "path_or_url": "/z"}],
                           "mode": "attach"}})
html = fs._deliverables_html(r)
check("deliv:渲染 summary_md", "<h3>成果</h3>" in html and "做完了" in html)
check("deliv:code Gerrit 連結", "https://g/+/1" in html and "改X" in html)
check("deliv:附件下載連結指向 /files/<token>",
      f"/files/{r.token}?f=r.md" in html)
check("deliv:Jira / CQ 連結", "browse/SCRUM-1" in html and "CR-9" in html)
check("deliv:cost/attempts", "0.5" in html and "attempts 2" in html.replace(" ", " "))
empty = fs._deliverables_html(_req({"jira_url": "https://j/browse/X"}))
check("deliv:無 deliverables → 提示未產出 OUTPUT.json",
      "未產出 OUTPUT.json" in empty)

# ── _deliverable_files:只服務宣告且存在的檔;無 output → 404 ──────────── #
rt = tempfile.mkdtemp()
st = Store(rt)
ws = os.path.join(rt, "tickets", "1", "ws"); os.makedirs(ws)
json.dump({"attachments": ["r.md", "missing.txt"]},
          open(os.path.join(ws, "OUTPUT.json"), "w"))
open(os.path.join(ws, "r.md"), "w").write("hello")
st.upsert_session(TicketSession(
    issue_id=1, key="SCRUM-1", profile="p", workspace=ws, session_id=None,
    attempts=1, outcome="SUCCESS", pending_reason=None, cost_usd=0.0))
req = build_request(1, "SCRUM-1", "score_and_close")
st.upsert_interaction(req)
server = fs.FormServer(st, host="127.0.0.1", port=0)
try:
    code, atts, wsr = server._deliverable_files(req)
    check("files:回可下載附件(只 r.md,missing 跳過)",
          code == 200 and {a.name for a in atts} == {"r.md"})
    # 無 session / 無 output → 404
    req2 = build_request(2, "SCRUM-2", "score_and_close")
    st.upsert_interaction(req2)
    c2, _, _ = server._deliverable_files(req2)
    check("files:無 session → 404", c2 == 404)
finally:
    server._server.server_close()
st.close()

print(f"test-form-output: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
