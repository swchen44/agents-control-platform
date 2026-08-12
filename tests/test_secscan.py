#!/usr/bin/env python3
"""M3:TICKET.md 安全掃描 + HIL 安全審。免網、fake scanner(sh 產 JSON)。
情境:功能關/命中判定/fail_on 門檻/掃描器失敗 fail-closed/dispatcher 門
(擋派工+發表單+hash 快取+人審放行)/hil 裁決(abort=security、continue=
修訂 sidecar)/表單脈絡卡/TICKET.md 修訂取代。pytest 相容,亦自跑。"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from arcp.dispatcher import Dispatcher  # noqa: E402
from arcp.hil import _apply_security_review  # noqa: E402
from arcp.interaction import build_request  # noqa: E402
from arcp.secscan import content_hash, scan_text, sort_findings  # noqa: E402
from arcp.store import Store, TicketSession  # noqa: E402
from arcp.ticket import Ticket  # noqa: E402
from arcp.workspace import DESC_OVERRIDE, render_ticket_md  # noqa: E402

ok = fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}")


def _fake_scanner(findings) -> str:
    """產一個假 skill-scanner:把 findings JSON 寫到 --output 指到的檔。"""
    d = tempfile.mkdtemp()
    p = os.path.join(d, "scan.sh")
    doc = json.dumps({"findings": findings})
    open(p, "w").write(
        "#!/bin/sh\nout=''\nprev=''\n"
        'for a in "$@"; do [ "$prev" = "--output" ] && out="$a"; prev="$a"; done\n'
        f"printf '%s' '{doc}' > \"$out\"\n")
    os.chmod(p, 0o755)
    return p


HIT = [{"severity": "high", "rule_id": "inj-1", "title": "prompt 注入",
        "description": "疑似指令覆寫", "snippet": "ignore previous"}]

# ── scan_text ───────────────────────────────────────────────────────── #
check("沒配置 → None(功能關)", scan_text("x", {}) is None)
r = scan_text("內容", {"command": _fake_scanner([])})
check("乾淨 → ok + hash", r.ok and r.findings == []
      and r.content_hash == content_hash("內容"))
r = scan_text("內容", {"command": _fake_scanner(HIT)})
check("high 命中(預設 fail_on=high)→ 不 ok + findings 齊",
      (not r.ok) and r.findings[0]["rule_id"] == "inj-1"
      and r.findings[0]["severity"] == "high")
r = scan_text("內容", {"command": _fake_scanner(HIT), "fail_on": "critical"})
check("fail_on=critical:high 不擋(仍記 findings)",
      r.ok and len(r.findings) == 1)
r = scan_text("內容", {"command": "/nonexistent/scanner"})
check("掃描器執行失敗 → fail-closed(ok=False + error)",
      (not r.ok) and r.error)

# ── dispatcher._security_gate ──────────────────────────────────────── #


class _Src:
    base_url = "https://x"

    def __init__(self):
        self.comments = []

    def add_comment(self, iid, text):
        self.comments.append(text)


def _mk(scan_cfg):
    root = tempfile.mkdtemp()
    st = Store(os.path.join(root, "s"))
    src = _Src()
    disp = Dispatcher(src, st, {}, root=root)
    disp.security_scan = scan_cfg
    ws = os.path.join(root, "ws")
    os.makedirs(ws)
    open(os.path.join(ws, "TICKET.md"), "w").write("# P-1\n\n做 X\n")
    sess = TicketSession(issue_id=1, key="P-1", profile="p", workspace=ws,
                         session_id=None, attempts=0, outcome=None,
                         pending_reason=None, cost_usd=0.0)
    st.upsert_session(sess)
    tk = Ticket(id=1, key="P-1", summary="s", state="待辦", assignee=None,
                assignee_id=None, description="做 X")
    return disp, st, src, sess, tk


disp, st, src, sess, tk = _mk({})
check("gate:沒配 → 放行", disp._security_gate(tk, sess, []) is True)

disp, st, src, sess, tk = _mk({"command": _fake_scanner([])})
evs = []
check("gate:乾淨 → 放行 + hash 快取存 session",
      disp._security_gate(tk, sess, evs) is True
      and st.get_session(1).sec_scanned_hash
      and any(e["type"] == "security_scan" and e["ok"] for e in evs))
sess = st.get_session(1)
disp.security_scan = {"command": "/nonexistent"}   # 同內容:不重掃(壞命令也過)
check("gate:同 hash 不重掃", disp._security_gate(tk, sess, []) is True)

disp, st, src, sess, tk = _mk({"command": _fake_scanner(HIT)})
evs = []
blocked = disp._security_gate(tk, sess, evs)
s2 = st.get_session(1)
check("gate:命中 → 擋(False)+ pending=security + journal blocked",
      blocked is False and s2.pending_reason == "security"
      and any(e["type"] == "security_blocked" for e in evs))
check("gate:發 security_review 表單(comment 有連結)+ payload 帶脈絡",
      any("/form/" in c for c in src.comments))
opens = st.open_interactions_for_ticket(1)
sec_req = next((r for r in opens if r.schema_id == "security_review"), None)
check("gate:interactions 有 security_review + findings/ticket_md",
      sec_req is not None and sec_req.payload.get("findings")
      and "做 X" in sec_req.payload.get("ticket_md", ""))
s2.sec_reviewed_at = 123.0                       # 人審放行 → 不再擋
st.upsert_session(s2)
check("gate:人審過(sec_reviewed_at)→ 放行不掃",
      disp._security_gate(tk, st.get_session(1), []) is True)

# ── hil._apply_security_review ─────────────────────────────────────── #
disp, st, src, sess, tk = _mk({})
req = build_request(1, "P-1", "security_review",
                    payload={"findings": HIT, "ticket_md": "x"})
req.submission = {"decision": "abort"}
evs = _apply_security_review(src, st, st.get_session(1), req, req.submission, 5.0)
s3 = st.get_session(1)
check("裁決 abort → ABORTED + abort_reason=security + journal",
      s3.outcome == "ABORTED" and s3.abort_reason == "security"
      and any(e["type"] == "aborted" and e["reason"] == "security"
              for e in evs))

disp, st, src, sess, tk = _mk({})
sess = st.get_session(1)
sess.pending_reason = "security"
req.submission = {"decision": "continue", "revised_text": "修訂後的乾淨描述"}
evs = _apply_security_review(src, st, sess, req, req.submission, 7.0)
s4 = st.get_session(1)
ov = os.path.join(sess.workspace, DESC_OVERRIDE)
check("裁決 continue → sidecar 寫修訂 + sec_reviewed_at + 清 pending",
      s4.sec_reviewed_at == 7.0 and s4.pending_reason is None
      and os.path.isfile(ov) and "乾淨描述" in open(ov).read()
      and any(e["type"] == "security_approved" and e["revised"] for e in evs))

# ── TICKET.md 修訂取代描述段 ───────────────────────────────────────── #
md = render_ticket_md(tk, None, None, "", desc_override="修訂後的乾淨描述")
check("TICKET.md:desc_override 取代描述段(標註人工修訂)",
      "修訂後的乾淨描述" in md and "做 X" not in md and "人工安全審修訂" in md)

# ── 表單脈絡卡 ─────────────────────────────────────────────────────── #
from arcp.form_server import _security_html  # noqa: E402

req2 = build_request(1, "P-1", "security_review",
                     payload={"findings": HIT, "ticket_md": "# 原文",
                              "scan_error": ""})
h = _security_html(req2)
check("表單卡:命中表(嚴重度/規則/片段)+ 原文內容",
      "inj-1" in h and "high" in h and "ignore previous" in h
      and "# 原文" in h)
req3 = build_request(1, "P-1", "security_review",
                     payload={"findings": [], "scan_error": "timeout"})
check("表單卡:掃描器異常標明 fail-closed", "掃描器異常" in _security_html(req3))

# ── sort_findings:嚴重度降冪(critical 在前;摘要才有用)──────────── #
_mix = [{"severity": "low", "rule_id": "a"}, {"severity": "critical", "rule_id": "b"},
        {"severity": "info", "rule_id": "c"}, {"severity": "high", "rule_id": "d"}]
check("sort_findings:critical→high→low→info",
      [x["rule_id"] for x in sort_findings(_mix)] == ["b", "d", "a", "c"])
check("sort_findings:空 → 空", sort_findings([]) == [])

print(f"test-secscan: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
