#!/usr/bin/env python3
"""Agent Detail Page (v5 §4.7 雛形)— 視覺化收割,B+.2。

只讀、stdlib http.server。把一張 ticket 的四層 trace 拼成一頁:
  L0 ticket / routing / 人工事件   ← harness journal(events.jsonl)
  L1 attempt 狀態轉移 / outcome     ← ticket_session + journal
  L2 invocation envelope           ← attempts/aN.envelope.json
  L3 conversation 原生事件          ← attempts/aN.events.jsonl(agent-server
                                      的 ACPToolCall/Message/StateUpdate…)

這正是 OpenHands GUI 給不了的視角:它只有 L3(conversation);L0/L2 的
ticket 語意、grader 判準、成本是 harness 的。detail page 把兩者對齊。

Usage: python3 detail_server.py [runtime_dir] [port]
       (預設 runtime_live、8787;開瀏覽器看 http://127.0.0.1:8787/)
"""

from __future__ import annotations

import html
import json
import os
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else \
    os.path.abspath("./runtime_live")
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8787


def read_journal() -> list[dict]:
    p = os.path.join(ROOT, "events.jsonl")
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in open(p) if l.strip()]


def read_sessions() -> dict[int, dict]:
    db = os.path.join(ROOT, "harness.db")
    out: dict[int, dict] = {}
    if not os.path.exists(db):
        return out
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        for r in con.execute("SELECT * FROM ticket_session"):
            out[r["issue_id"]] = dict(r)
    except sqlite3.OperationalError:
        pass
    con.close()
    return out


def attempt_dir(issue_id: int) -> str:
    return os.path.join(ROOT, "tickets", str(issue_id), "attempts")


def esc(x) -> str:
    return html.escape(str(x))


CSS = """
body{font:14px/1.5 -apple-system,system-ui,sans-serif;margin:0;background:#0d1117;color:#c9d1d9}
header{background:#161b22;padding:16px 24px;border-bottom:1px solid #30363d}
h1{margin:0;font-size:18px}h2{color:#58a6ff;font-size:14px;margin:20px 0 8px}
a{color:#58a6ff;text-decoration:none}main{padding:0 24px 40px;max-width:1100px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 16px;margin:8px 0}
.row{display:flex;gap:16px;flex-wrap:wrap}.kv{margin:2px 12px 2px 0}
.kv b{color:#8b949e;font-weight:500}
.badge{padding:1px 8px;border-radius:10px;font-size:12px;font-weight:600}
.SUCCESS{background:#1a4d2e;color:#7ee2a8}.FAILURE,.UNKNOWN,.ABORTED{background:#4d1a1a;color:#f2a8a8}
.pending{background:#4d3d1a;color:#e2d07e}
.ev{font-family:ui-monospace,monospace;font-size:12px;padding:3px 0;border-bottom:1px solid #21262d}
.ev .t{color:#8b949e}.ev .k{color:#58a6ff}.layer{border-left:3px solid #30363d;padding-left:12px}
.L0{border-color:#a371f7}.L1{border-color:#58a6ff}.L2{border-color:#3fb950}.L3{border-color:#d29922}
table{border-collapse:collapse;width:100%;font-size:12px}td{padding:3px 8px;border-bottom:1px solid #21262d;vertical-align:top}
"""


def render_index(journal, sessions) -> str:
    ids = sorted({e["issue_id"] for e in journal} | set(sessions))
    rows = ""
    for iid in ids:
        s = sessions.get(iid, {})
        key = s.get("key") or next((e["key"] for e in journal
                                    if e["issue_id"] == iid), f"#{iid}")
        oc = s.get("outcome") or "-"
        cls = oc if oc in ("SUCCESS", "FAILURE", "UNKNOWN", "ABORTED") else ""
        rows += (f"<tr><td><a href='/ticket/{iid}'>{esc(key)}</a></td>"
                 f"<td>{esc(s.get('profile','-'))}</td>"
                 f"<td><span class='badge {cls}'>{esc(oc)}</span></td>"
                 f"<td>{esc(s.get('attempts',0))}</td>"
                 f"<td>${s.get('cost_usd',0):.4f}</td></tr>")
    return (f"<header><h1>ARCP Agent Detail · {esc(ROOT.split('/')[-1])}"
            f"</h1></header><main><h2>Tickets</h2><div class='card'><table>"
            f"<tr><td><b>ticket</b></td><td><b>profile</b></td>"
            f"<td><b>outcome</b></td><td><b>attempts</b></td>"
            f"<td><b>cost</b></td></tr>{rows}</table></div>"
            f"<p style='color:#8b949e'>四層 trace:L0 ticket · L1 attempt · "
            f"L2 envelope · L3 conversation events。點 ticket 展開。</p></main>")


def render_ticket(iid, journal, sessions) -> str:
    s = sessions.get(iid, {})
    key = s.get("key") or f"#{iid}"
    evs = [e for e in journal if e["issue_id"] == iid]

    # L0/L1 journal
    l0 = ""
    for e in evs:
        extra = {k: v for k, v in e.items()
                 if k not in ("ts", "type", "issue_id", "key")}
        l0 += (f"<div class='ev'><span class='k'>{esc(e['type'])}</span> "
               f"<span class='t'>{esc(json.dumps(extra, ensure_ascii=False))}"
               f"</span></div>")

    # L2/L3 per attempt
    ad = attempt_dir(iid)
    layers = ""
    if os.path.isdir(ad):
        envs = sorted(f for f in os.listdir(ad) if f.endswith(".envelope.json"))
        for ef in envs:
            n = ef.split(".")[0]
            env = json.load(open(os.path.join(ad, ef)))
            layers += (f"<h2>{esc(n)} · L2 envelope</h2><div class='card layer L2'>"
                       f"<div class='row'>"
                       f"<span class='kv'><b>completed</b> {esc(env.get('completed'))}</span>"
                       f"<span class='kv'><b>session</b> {esc(env.get('session_id'))}</span>"
                       f"<span class='kv'><b>resumed</b> {esc(env.get('truly_resumed'))}</span>"
                       f"<span class='kv'><b>cost</b> ${esc(env.get('cost_usd'))}</span>"
                       f"<span class='kv'><b>error</b> {esc(env.get('error'))}</span>"
                       f"</div></div>")
            evp = os.path.join(ad, f"{n}.events.jsonl")
            if os.path.exists(evp):
                items = [json.loads(l) for l in open(evp) if l.strip()]
                from collections import Counter
                hist = Counter(i.get("kind") or i.get("type") or "?"
                               for i in items)
                rows = ""
                for i in items[:60]:
                    kind = i.get("kind") or i.get("type") or "?"
                    src = i.get("source", "")
                    txt = ""
                    if kind == "ConversationStateUpdateEvent":
                        txt = f"{i.get('key','')}={str(i.get('value',''))[:60]}"
                    elif kind == "ACPToolCallEvent":
                        txt = str(i.get("tool_name", ""))[:60]
                    rows += (f"<div class='ev'><span class='k'>{esc(kind)}</span> "
                             f"<span class='t'>{esc(src)} {esc(txt)}</span></div>")
                layers += (f"<h2>{esc(n)} · L3 conversation events "
                           f"({sum(hist.values())}) {esc(dict(hist))}</h2>"
                           f"<div class='card layer L3'>{rows}</div>")

    return (f"<header><h1><a href='/'>← </a>{esc(key)} · "
            f"<span class='badge {esc(s.get('outcome') or '')}'>"
            f"{esc(s.get('outcome') or '-')}</span></h1></header><main>"
            f"<div class='card'><div class='row'>"
            f"<span class='kv'><b>profile</b> {esc(s.get('profile','-'))}</span>"
            f"<span class='kv'><b>attempts</b> {esc(s.get('attempts',0))}</span>"
            f"<span class='kv'><b>cost</b> ${s.get('cost_usd',0):.4f}</span>"
            f"<span class='kv'><b>workspace</b> {esc(s.get('workspace','-'))}</span>"
            f"</div></div>"
            f"<h2>L0/L1 · ticket & attempt 事件(harness journal)</h2>"
            f"<div class='card layer L0'>{l0}</div>{layers}</main>")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        journal, sessions = read_journal(), read_sessions()
        if self.path.startswith("/ticket/"):
            iid = int(self.path.split("/")[-1])
            body = render_ticket(iid, journal, sessions)
        else:
            body = render_index(journal, sessions)
        # live 刷新:每 5s 自動重載(只讀頁,最簡可靠;live conversation 進行中
        # 也能看到事件逐步增加)
        refresh = "<meta http-equiv='refresh' content='5'>"
        page = (f"<!doctype html><html><head><meta charset='utf-8'>{refresh}"
                f"<title>ARCP Detail</title><style>{CSS}</style></head>"
                f"<body>{body}</body></html>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(page.encode())


if __name__ == "__main__":
    print(f"[detail] serving {ROOT} at http://127.0.0.1:{PORT}/", flush=True)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
