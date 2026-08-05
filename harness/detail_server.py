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

W2.7 dashboard 擴充(F2 排隊 + C4 總覽 + 控制):index 加總覽卡(cost/outcome/
失敗率/in-flight/queued)、狀態徽章(QUEUED 含 FIFO 位置 / INACTIVE / pending:*)、
控制列(Pause/Resume/Reload → fetch POST 到 W2.6 control API,離線顯示提示);
審批門 ticket 顯示審批狀態卡(sections 表單本體在 Jira description)。

Usage: python3 detail_server.py [runtime_dir] [port] [control_url]
       (預設 runtime_live、8788、http://127.0.0.1:8787;
        亦可 env ARCP_CONTROL_URL 指 control API)
"""

from __future__ import annotations

import html
import json
import os
import sqlite3
import sys
from collections import Counter
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else \
    os.path.abspath("./runtime_live")
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8788   # 8787 讓給 control API
CONTROL = (sys.argv[3] if len(sys.argv) > 3
           else os.environ.get("ARCP_CONTROL_URL", "http://127.0.0.1:8787"))


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


def read_watch() -> dict[int, dict]:
    """W4.1:assignee(displayName)/created(first_seen_ts)來源。舊庫缺欄容錯。"""
    db = os.path.join(ROOT, "harness.db")
    out: dict[int, dict] = {}
    if not os.path.exists(db):
        return out
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        for r in con.execute("SELECT * FROM ticket_watch"):
            out[r["issue_id"]] = dict(r)
    except sqlite3.OperationalError:
        pass
    con.close()
    return out


def fmt_ts(ts) -> str:
    """epoch → 'MM-DD HH:MM';0/None → '-'。"""
    if not ts:
        return "-"
    import datetime
    return datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


def handoff_starts(journal: list[dict]) -> dict[int, float]:
    """W4.1「最新換手起點」:每票最近一次 handoff / inactive_cleared 的時間
    (換手或交回機器人後重新開跑的起點)。"""
    out: dict[int, float] = {}
    for e in journal:
        if e.get("type") in ("handoff", "inactive_cleared"):
            iid = e.get("issue_id")
            if isinstance(iid, int):
                out[iid] = max(out.get(iid, 0), e.get("ts") or 0)
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
.queued{background:#1a2f4d;color:#7ea8e2}.inactive{background:#30363d;color:#8b949e}
.running{background:#1a3a4d;color:#7ed0e2}
.stats{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}
.stat{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:10px 18px;text-align:center;min-width:70px}
.stat .n{font-size:20px;font-weight:700;color:#58a6ff}.stat .l{font-size:11px;color:#8b949e}
.ctl{display:flex;gap:8px;align-items:center}
.btn{padding:4px 14px;border-radius:6px;background:#21262d;cursor:pointer;border:1px solid #30363d;user-select:none}
.btn:hover{background:#30363d}
.ev{font-family:ui-monospace,monospace;font-size:12px;padding:3px 0;border-bottom:1px solid #21262d}
.ev .t{color:#8b949e}.ev .k{color:#58a6ff}.layer{border-left:3px solid #30363d;padding-left:12px}
.L0{border-color:#a371f7}.L1{border-color:#58a6ff}.L2{border-color:#3fb950}.L3{border-color:#d29922}
table{border-collapse:collapse;width:100%;font-size:12px}td{padding:3px 8px;border-bottom:1px solid #21262d;vertical-align:top}
.tabs{display:flex;gap:8px;margin:16px 0 8px}.tab{padding:4px 14px;border-radius:6px;background:#21262d;cursor:pointer;user-select:none}.tab.on{background:#1f6feb;color:#fff}
.pane{display:none}.pane.on{display:block}
.msg{margin:8px 0;display:flex}.msg.user{justify-content:flex-end}
.bubble{max-width:80%;padding:8px 12px;border-radius:12px;white-space:pre-wrap;word-break:break-word}
.msg.user .bubble{background:#1f6feb;color:#fff;border-bottom-right-radius:3px}
.msg.agent .bubble{background:#21262d;border-bottom-left-radius:3px}
.tool{margin:6px 0 6px 20px;border-left:2px solid #d29922;padding:4px 10px;background:#161b22;border-radius:0 6px 6px 0;font-size:12px}
.tool .ti{color:#e2d07e;font-weight:600}.tool .st{float:right;font-size:11px;color:#8b949e}
.tool .io{font-family:ui-monospace,monospace;color:#8b949e;margin-top:2px}
.think{margin:6px 0 6px 20px;color:#8b949e;font-style:italic;font-size:12px}
.sys{color:#8b949e;font-size:11px;text-align:center;margin:8px 0}
"""


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(c.get("text", "") for c in content
                        if isinstance(c, dict) and c.get("type") == "text")
    return ""


def render_conversation(items: list[dict]) -> str:
    """L3 events → chat-style conversation view (OpenHands-UI-like)."""
    out = ""
    for e in items:
        k = e.get("kind")
        if k == "MessageEvent":
            src = e.get("source", "agent")
            txt = _text_of((e.get("llm_message") or {}).get("content"))
            if not txt.strip():
                continue
            # RawCLIAgent (route C) encodes fine-grained units via markers
            if txt.startswith("🔧"):
                out += f"<div class='tool'><span class='ti'>{esc(txt)}</span></div>"
            elif txt.startswith("📋"):
                out += (f"<div class='tool' style='border-color:#3fb950'>"
                        f"<span class='io'>{esc(txt)}</span></div>")
            elif txt.startswith("💭"):
                out += f"<div class='think'>{esc(txt)}</div>"
            else:
                out += (f"<div class='msg {esc(src)}'><div class='bubble'>"
                        f"{esc(txt)}</div></div>")
        elif k == "SystemPromptEvent":
            n = len(e.get("tools") or [])
            out += f"<div class='sys'>— system prompt · {n} tools —</div>"
        elif k == "ActionEvent":
            th = (e.get("thought") or "").strip()
            if th:
                out += f"<div class='think'>💭 {esc(th[:400])}</div>"
        elif k == "ACPToolCallEvent":
            title = e.get("title") or e.get("tool_kind") or "tool"
            status = e.get("status") or ""
            ri = e.get("raw_input")
            io = ""
            if isinstance(ri, dict):
                fp = ri.get("file_path") or ri.get("command") or ""
                if fp:
                    io = f"<div class='io'>{esc(str(fp)[:120])}</div>"
            err = " ⚠️" if e.get("is_error") else ""
            out += (f"<div class='tool'><span class='st'>{esc(status)}{err}"
                    f"</span><span class='ti'>🔧 {esc(title)}</span>"
                    f"<span style='color:#6e7681'> · {esc(e.get('tool_kind',''))}"
                    f"</span>{io}</div>")
    return out or "<div class='sys'>(no conversation events)</div>"


def queue_positions(sessions: dict[int, dict]) -> dict[int, int]:
    """F2:queued sessions 依 queued_at FIFO 排 → {issue_id: 1-based 位置}。"""
    q = [s for s in sessions.values()
         if s.get("queued") and not s.get("outcome")]
    q.sort(key=lambda s: s.get("queued_at") or 0)
    return {s["issue_id"]: i + 1 for i, s in enumerate(q)}


def session_status(s: dict, qpos: dict[int, int]) -> tuple[str, str]:
    """→ (徽章文字, css class)。優先序:outcome > pending > queued > inactive。"""
    oc = s.get("outcome")
    if oc:
        return oc, oc if oc in ("SUCCESS", "FAILURE", "UNKNOWN",
                                "ABORTED") else ""
    pr = s.get("pending_reason")
    if pr:
        return f"pending:{pr}", "pending"
    if s.get("queued"):
        return f"QUEUED #{qpos.get(s.get('issue_id'), '?')}", "queued"
    if s.get("inactive"):
        return "INACTIVE", "inactive"
    return "active", "running"


def saved_minutes(journal: list[dict]) -> float:
    """W3.5 C3:累計節省人時(分)。SUCCESS 事件(resolved / trigger_finished)
    帶 human_minutes_saved(profile 估時,公式 v1 平計)。"""
    return sum(e.get("human_minutes_saved") or 0 for e in journal
               if e.get("type") in ("resolved", "trigger_finished"))


def overview_cards(sessions: dict[int, dict],
                   journal: list[dict] | None = None) -> str:
    """C4 總覽卡:cost / outcome 計數 / 失敗率 / in-flight / queued / inactive
    + W3.5 節省人時(有 est 的 profile 才累計;時薪 env ARCP_HOURLY_RATE
    選配 → 顯示人力成本對比)。"""
    vals = list(sessions.values())
    oc = Counter(s.get("outcome") for s in vals if s.get("outcome"))
    succ, fail = oc.get("SUCCESS", 0), oc.get("FAILURE", 0)
    done = succ + fail
    fail_rate = f"{fail / done * 100:.0f}%" if done else "–"
    in_flight = sum(1 for s in vals
                    if not s.get("outcome") and not s.get("pending_reason")
                    and not s.get("queued") and not s.get("inactive"))
    live = [s for s in vals if not s.get("outcome")]
    total_cost = sum(s.get("cost_usd") or 0 for s in vals)
    stats = [
        (f"${total_cost:.4f}", "總 cost"),
        (in_flight, "in-flight"),
        (sum(1 for s in live if s.get("queued")), "queued"),
        (sum(1 for s in live if s.get("inactive")), "inactive"),
        (sum(1 for s in live if s.get("pending_reason")), "pending"),
        (succ, "SUCCESS"), (fail, "FAILURE"), (fail_rate, "失敗率"),
    ]
    mins = saved_minutes(journal or [])
    if mins:
        stats.append((f"{mins / 60:.1f}h", "節省人時"))
        rate = os.environ.get("ARCP_HOURLY_RATE")
        if rate:
            try:
                human_cost = mins / 60 * float(rate)
                stats.append((f"${human_cost:.0f} vs ${total_cost:.2f}",
                              "人力成本對比"))
            except ValueError:
                pass
    return "<div class='stats'>" + "".join(
        f"<div class='stat'><div class='n'>{esc(n)}</div>"
        f"<div class='l'>{esc(label)}</div></div>" for n, label in stats
    ) + "</div>"


def control_bar() -> str:
    """控制列:Pause/Resume/Reload → fetch POST 到 control API(W2.6)。"""
    return (
        "<div class='ctl card'><b style='color:#8b949e'>Control</b>"
        "<div class='btn' onclick=\"ctl('pause')\">⏸ Pause</div>"
        "<div class='btn' onclick=\"ctl('resume')\">▶ Resume</div>"
        "<div class='btn' onclick=\"ctl('reload')\">🔄 Reload</div>"
        "<span id='ctl-state' style='color:#8b949e;font-size:12px'></span>"
        "</div><script>"
        "const CTL=" + json.dumps(CONTROL) + ";"
        "const ST=document.getElementById('ctl-state');"
        "function off(){ST.textContent='control 離線('+CTL+')';}"
        "function ctl(a){fetch(CTL+'/'+a,{method:'POST'})"
        ".then(r=>r.json()).then(j=>ST.textContent=JSON.stringify(j))"
        ".catch(off);}"
        "fetch(CTL+'/status').then(r=>r.json()).then(j=>{"
        "ST.textContent=(j.paused?'⏸ paused':'▶ running')"
        "+' · in-flight '+j.in_flight+' · queued '+j.queued;})"
        ".catch(off);"
        "</script>")


_INDEX_JS = """
<script>
const LS='arcp-idx';
function state(){try{return JSON.parse(localStorage.getItem(LS))||{}}catch(e){return{}}}
function save(s){localStorage.setItem(LS,JSON.stringify(s));}
function applyFilters(){
  const s=state();
  const kw=(s.kw||'').toLowerCase(), st=s.st||'', size=+(s.size||20);
  let page=+(s.page||0);
  const rows=[...document.querySelectorAll('#tix tbody tr')];
  const vis=rows.filter(r=>{
    const okKw=!kw||r.textContent.toLowerCase().includes(kw);
    const okSt=!st||(r.dataset.status===st);
    return okKw&&okSt;});
  rows.forEach(r=>r.style.display='none');
  const pages=Math.max(1,Math.ceil(vis.length/size));
  if(page>=pages)page=pages-1;
  vis.slice(page*size,(page+1)*size).forEach(r=>r.style.display='');
  document.getElementById('pginfo').textContent=
    vis.length+' 筆 · 第 '+(page+1)+'/'+pages+' 頁';
  s.page=page;save(s);
}
function initIdx(){
  const s=state();
  const sel=document.getElementById('st');
  const sts=[...new Set([...document.querySelectorAll('#tix tbody tr')]
    .map(r=>r.dataset.status))].sort();
  sts.forEach(v=>{const o=document.createElement('option');
    o.value=v;o.textContent=v;sel.appendChild(o);});
  document.getElementById('kw').value=s.kw||'';
  sel.value=s.st||'';
  document.getElementById('psize').value=s.size||20;
  document.getElementById('kw').addEventListener('input',e=>{
    const s=state();s.kw=e.target.value;s.page=0;save(s);applyFilters();});
  sel.addEventListener('change',e=>{
    const s=state();s.st=e.target.value;s.page=0;save(s);applyFilters();});
  document.getElementById('psize').addEventListener('change',e=>{
    const s=state();s.size=+e.target.value;s.page=0;save(s);applyFilters();});
  applyFilters();
}
function pg(d){const s=state();s.page=Math.max(0,(+(s.page||0))+d);save(s);applyFilters();}
initIdx();
// 局部更新(W4.1):只換統計卡與表身,工具列/輸入框不動——打字不被打斷
setInterval(async()=>{try{
  const r=await fetch(location.pathname);
  const doc=new DOMParser().parseFromString(await r.text(),'text/html');
  const nb=doc.querySelector('#tix tbody'), ob=document.querySelector('#tix tbody');
  if(nb&&ob&&nb.innerHTML!==ob.innerHTML){ob.innerHTML=nb.innerHTML;}
  const ns=doc.querySelector('.stats'), os=document.querySelector('.stats');
  if(ns&&os&&ns.innerHTML!==os.innerHTML){os.innerHTML=ns.innerHTML;}
  applyFilters();
}catch(e){}},5000);
</script>"""


def render_index(journal, sessions, watch=None) -> str:
    watch = watch or {}
    ids = sorted({e["issue_id"] for e in journal} | set(sessions))
    qpos = queue_positions(sessions)
    hs = handoff_starts(journal)
    rows = ""
    for iid in ids:
        s = sessions.get(iid, {})
        w = watch.get(iid, {})
        key = s.get("key") or w.get("key") or next(
            (e["key"] for e in journal if e["issue_id"] == iid), f"#{iid}")
        label, cls = session_status(s, qpos) if s else ("-", "")
        rows += (f"<tr data-status='{esc(label)}'>"
                 f"<td><a href='/ticket/{iid}'>{esc(key)}</a></td>"
                 f"<td>{esc(s.get('profile','-'))}</td>"
                 f"<td><span class='badge {cls}'>{esc(label)}</span></td>"
                 f"<td>{esc(w.get('last_assignee') or '-')}</td>"
                 f"<td>{esc(fmt_ts(w.get('first_seen_ts')))}</td>"
                 f"<td>{esc(fmt_ts(s.get('finished_at')))}</td>"
                 f"<td>{esc(fmt_ts(hs.get(iid)))}</td>"
                 f"<td>{esc(s.get('attempts', 0))}</td>"
                 f"<td>${s.get('cost_usd', 0) or 0:.4f}</td></tr>")
    toolbar = (
        "<div class='ctl card'>"
        "<input id='kw' placeholder='keyword…' style='background:#0d1117;"
        "color:#c9d1d9;border:1px solid #30363d;border-radius:6px;"
        "padding:4px 10px'>"
        "<select id='st' style='background:#0d1117;color:#c9d1d9;"
        "border:1px solid #30363d;border-radius:6px;padding:4px'>"
        "<option value=''>全部狀態</option></select>"
        "<select id='psize' style='background:#0d1117;color:#c9d1d9;"
        "border:1px solid #30363d;border-radius:6px;padding:4px'>"
        "<option>10</option><option selected>20</option>"
        "<option>50</option><option>100</option></select>"
        "<div class='btn' onclick='pg(-1)'>‹ 上頁</div>"
        "<div class='btn' onclick='pg(1)'>下頁 ›</div>"
        "<span id='pginfo' style='color:#8b949e;font-size:12px'></span></div>")
    return (f"<header><h1>ARCP Dashboard · {esc(ROOT.split('/')[-1])}"
            f"</h1></header><main>"
            f"{overview_cards(sessions, journal)}{control_bar()}"
            f"<h2>Tickets</h2>{toolbar}<div class='card'>"
            f"<table id='tix'><thead>"
            f"<tr><td><b>ticket</b></td><td><b>profile</b></td>"
            f"<td><b>status</b></td><td><b>assignee</b></td>"
            f"<td><b>created</b></td><td><b>finished</b></td>"
            f"<td><b>換手起點</b></td><td><b>attempts</b></td>"
            f"<td><b>cost</b></td></tr></thead><tbody>{rows}</tbody>"
            f"</table></div>"
            f"<p style='color:#8b949e'>四層 trace:L0 ticket · L1 attempt · "
            f"L2 envelope · L3 conversation events。點 ticket 展開。</p>"
            f"{_INDEX_JS}</main>")


def render_approval(s: dict, evs: list[dict]) -> str:
    """W2.3 審批門狀態卡。sections 表單本體在 Jira description(人在 Jira 填);
    此頁顯示 store 側的審批軌跡(decision/退回次數)。"""
    appr = [e for e in evs if e.get("type") == "approval"]
    if (s.get("pending_reason") not in ("approval", "escalated")
            and not s.get("approval_revisions") and not appr):
        return ""
    rows = "".join(
        f"<div class='ev'><span class='k'>{esc(e.get('decision','?'))}</span> "
        f"<span class='t'>revisions={esc(e.get('revisions', 0))}</span></div>"
        for e in appr)
    state = s.get("pending_reason") or "-"
    return (f"<h2>審批門(W2.3)</h2><div class='card layer L0'>"
            f"<div class='row'>"
            f"<span class='kv'><b>狀態</b> <span class='badge pending'>"
            f"{esc(state)}</span></span>"
            f"<span class='kv'><b>退回次數</b> "
            f"{esc(s.get('approval_revisions', 0))}</span>"
            f"<span class='kv' style='color:#8b949e'>填表區段在 Jira "
            f"description(human 段),assignee 交回機器人即放行</span>"
            f"</div>{rows}</div>")


def transcript_dir_of(workspace: str) -> str:
    """W4.2:instance transcript 目錄(workspace=<base>/ws → <base>/transcript)。"""
    base = os.path.dirname(workspace) if workspace.endswith("/ws") \
        else workspace
    return os.path.join(base, "transcript")


def render_transcript_card(iid: int, s: dict) -> str:
    """W4.2:transcript 產物卡(HTML 檢視連結 + tgz 下載)。無產物不顯卡。"""
    ws = s.get("workspace") or ""
    if not ws or ws.startswith("("):
        return ""
    d = transcript_dir_of(ws)
    if not os.path.isdir(d):
        return ""
    names = sorted(f for f in os.listdir(d)
                   if os.path.isfile(os.path.join(d, f))
                   and not f.startswith("."))
    if not names:
        return ""
    links = "".join(
        f"<a class='btn' style='text-decoration:none' "
        f"href='/tfile/{iid}/{esc(n)}'"
        f"{' download' if n.endswith('.tgz') else ' target=_blank'}>"
        f"{'📦 ' if n.endswith('.tgz') else '📄 '}{esc(n)}</a>"
        for n in names)
    return (f"<h2>Transcript(可視化 / 下載)</h2>"
            f"<div class='card'><div class='ctl'>{links}</div></div>")


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

    # L2/L3 per attempt — build BOTH a trace view and a conversation view
    ad = attempt_dir(iid)
    trace_layers, convo_panes = "", ""
    if os.path.isdir(ad):
        envs = sorted(f for f in os.listdir(ad) if f.endswith(".envelope.json"))
        for ef in envs:
            n = ef.split(".")[0]
            env = json.load(open(os.path.join(ad, ef)))
            trace_layers += (
                f"<h2>{esc(n)} · L2 envelope</h2><div class='card layer L2'>"
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
                hist = Counter(i.get("kind") or i.get("type") or "?"
                               for i in items)
                rows = ""
                for i in items:
                    kind = i.get("kind") or i.get("type") or "?"
                    src = i.get("source", "")
                    txt = ""
                    if kind == "ConversationStateUpdateEvent":
                        txt = f"{i.get('key','')}={str(i.get('value',''))[:60]}"
                    elif kind == "ACPToolCallEvent":
                        txt = str(i.get("title") or i.get("tool_kind") or "")[:60]
                    rows += (f"<div class='ev'><span class='k'>{esc(kind)}</span> "
                             f"<span class='t'>{esc(src)} {esc(txt)}</span></div>")
                trace_layers += (
                    f"<h2>{esc(n)} · L3 events ({sum(hist.values())}) "
                    f"{esc(dict(hist))}</h2>"
                    f"<div class='card layer L3'>{rows}</div>")
                convo_panes += (f"<h2>{esc(n)}</h2><div class='card'>"
                                f"{render_conversation(items)}</div>")

    trace_view = (f"<h2>L0/L1 · ticket & attempt 事件(harness journal)</h2>"
                  f"<div class='card layer L0'>{l0}</div>{trace_layers}")
    convo_view = convo_panes or "<div class='sys'>(此 backend 無 conversation 事件)</div>"

    # tab state in the URL hash so the 5s live-refresh keeps the current tab
    tabs_js = ("<script>function tab(n){location.hash=n;"
               "for(const p of document.querySelectorAll('.pane'))p.classList.remove('on');"
               "for(const t of document.querySelectorAll('.tab'))t.classList.remove('on');"
               "document.getElementById('pane-'+n).classList.add('on');"
               "document.getElementById('tab-'+n).classList.add('on');}"
               "tab((location.hash||'#convo').slice(1));</script>")
    return (f"<header><h1><a href='/'>← </a>{esc(key)} · "
            f"<span class='badge {esc(s.get('outcome') or '')}'>"
            f"{esc(s.get('outcome') or '-')}</span></h1></header><main>"
            f"<div class='card'><div class='row'>"
            f"<span class='kv'><b>profile</b> {esc(s.get('profile','-'))}</span>"
            f"<span class='kv'><b>attempts</b> {esc(s.get('attempts',0))}</span>"
            f"<span class='kv'><b>cost</b> ${s.get('cost_usd',0):.4f}</span>"
            f"<span class='kv'><b>workspace</b> {esc(s.get('workspace','-'))}</span>"
            f"</div></div>"
            f"{render_transcript_card(iid, s)}"
            f"{render_approval(s, evs)}"
            f"<div class='tabs'>"
            f"<div class='tab on' id='tab-convo' onclick='tab(\"convo\")'>💬 Conversation</div>"
            f"<div class='tab' id='tab-trace' onclick='tab(\"trace\")'>🔍 Trace (L0-L3)</div>"
            f"</div>"
            f"<div class='pane on' id='pane-convo'>{convo_view}</div>"
            f"<div class='pane' id='pane-trace'>{trace_view}</div>"
            f"{tabs_js}</main>")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        journal, sessions = read_journal(), read_sessions()
        if self.path.startswith("/tfile/"):        # W4.2 transcript 產物服務
            try:
                _, _, iid_s, name = self.path.split("/", 3)
                iid = int(iid_s)
                name = os.path.basename(name)      # 防 traversal
                ws = (sessions.get(iid) or {}).get("workspace") or ""
                p = os.path.join(transcript_dir_of(ws), name)
                if not (ws and os.path.isfile(p)):
                    raise FileNotFoundError(p)
                data = open(p, "rb").read()
                self.send_response(200)
                if name.endswith(".html"):
                    self.send_header("Content-Type",
                                     "text/html; charset=utf-8")
                elif name.endswith(".log"):        # W4.4 script log 檢視
                    self.send_header("Content-Type",
                                     "text/plain; charset=utf-8")
                else:
                    self.send_header("Content-Type", "application/gzip")
                    self.send_header("Content-Disposition",
                                     f"attachment; filename={name}")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception:
                self.send_response(404)
                self.end_headers()
            return
        if self.path.startswith("/ticket/"):
            iid = int(self.path.split("/")[-1])
            body = render_ticket(iid, journal, sessions)
            # W4.1 修 auto-collapse bug:原 <meta refresh> 整頁重載會重置
            # 展開/捲動 → 改 fetch 局部更新,保留 <details> 展開狀態與分頁籤
            body += ("<script>setInterval(async()=>{try{"
                     "const r=await fetch(location.pathname);"
                     "const doc=new DOMParser().parseFromString("
                     "await r.text(),'text/html');"
                     "const nu=doc.querySelector('main'),"
                     "cur=document.querySelector('main');"
                     "if(!nu||!cur||nu.innerHTML===cur.innerHTML)return;"
                     "const open=[...cur.querySelectorAll('details')]"
                     ".map(d=>d.open);"
                     "cur.innerHTML=nu.innerHTML;"
                     "[...cur.querySelectorAll('details')].forEach((d,i)=>{"
                     "if(open[i])d.open=true});"
                     "if(typeof tab==='function')"
                     "tab((location.hash||'#convo').slice(1));"
                     "}catch(e){}},5000);</script>")
        else:
            body = render_index(journal, sessions, read_watch())
        # live 更新一律走 fetch 局部替換(index 表身/統計卡、ticket main),
        # 不再整頁 meta refresh(W4.1)
        page = (f"<!doctype html><html><head><meta charset='utf-8'>"
                f"<title>ARCP Detail</title><style>{CSS}</style></head>"
                f"<body>{body}</body></html>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(page.encode())


if __name__ == "__main__":
    print(f"[detail] serving {ROOT} at http://127.0.0.1:{PORT}/", flush=True)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
