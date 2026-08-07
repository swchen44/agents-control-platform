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
from collections import Counter, deque
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:                                    # W6.1 系統資訊(純 stdlib);缺也不擋頁
    from arcp_harness.sysinfo import collect as sysinfo_collect
except Exception:  # noqa: BLE001
    sysinfo_collect = None

ROOT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else \
    os.path.abspath("./runtime_live")
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8788   # 8787 讓給 control API
CONTROL = (sys.argv[3] if len(sys.argv) > 3
           else os.environ.get("ARCP_CONTROL_URL", "http://127.0.0.1:8787"))
# W6.1:綁定 host = config,預設 0.0.0.0(內網開放,使用者 2026-08-07 決定;
# ⚠️ dashboard 唯讀但會顯示系統/程序資訊,內網任何人可見)。設 127.0.0.1 可鎖本機。
HOST = os.environ.get("ARCP_DASH_HOST", "0.0.0.0")
# W6.6:連線 IP 環形緩衝(記憶體,重啟清)
_CONNS: deque = deque(maxlen=200)
# 內網/離線:transcript(cclog)本需從 CDN 載 vis-timeline,已 vendor 到本地
_VENDOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "tools", "cclog", "vendor")
# W6.5:Swagger UI(REST API 文件)也 vendor 到本地(內網不外連 CDN)
_SWAGGER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "tools", "vendor", "swagger-ui")
# transcript HTML(外部工具產出)硬擋任何外部載入(只允許同源 + 內嵌 + data:)
_CSP_TRANSCRIPT = ("default-src 'none'; script-src 'self' 'unsafe-inline'; "
                   "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                   "font-src 'self' data:; connect-src 'self'")
# 主頁(我們自寫,已無外部引用)——同樣擋外部,但放行本地 control API 的
# 跨埠 fetch(Evict / 狀態);defense-in-depth,防未來誤加 CDN。
_CSP_MAIN = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
             "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
             "font-src 'self' data:; connect-src 'self' " + CONTROL)
# W6.5:/docs(Swagger UI)專屬——vendored bundle 內含 1 處 new Function
# (bundled lib),需 unsafe-eval;仍只放行同源資產 + 對 control API 的 Try it out。
# 內容為自 host 已審 bundle,unsafe-eval 侷限此頁可接受(defense-in-depth)。
_CSP_DOCS = ("default-src 'self'; "
             "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
             "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
             "font-src 'self' data:; connect-src 'self' " + CONTROL)


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


def build_data(journal, sessions, watch) -> dict:
    """W4.7:/data JSON——前端過濾/圖表/排序的單一資料源。"""
    qpos = queue_positions(sessions)
    hs = handoff_starts(journal)
    hm: dict[int, float] = {}
    first_ts: dict[int, float] = {}
    last_change: dict[int, float] = {}   # W5.2 停留時間:state/assignee 變動
    for e in journal:
        iid = e.get("issue_id")
        if not isinstance(iid, int) or iid == 0:
            continue
        ts = e.get("ts") or 0
        if ts and (iid not in first_ts or ts < first_ts[iid]):
            first_ts[iid] = ts
        if (e.get("type") in ("status_changed", "assignee_changed")
                and ts > last_change.get(iid, 0)):
            last_change[iid] = ts
        if (e.get("type") in ("resolved", "trigger_finished")
                and e.get("human_minutes_saved")):
            hm[iid] = hm.get(iid, 0) + float(e["human_minutes_saved"])
    ids = sorted(set(sessions) | set(watch) | set(first_ts))
    rows = []
    for iid in ids:
        s = sessions.get(iid, {})
        w = watch.get(iid, {})
        label, _cls = session_status(s, qpos) if s else ("-", "")
        rows.append({
            "iid": iid,
            "key": s.get("key") or w.get("key") or f"#{iid}",
            "summary": w.get("summary") or "",
            "desc": w.get("description") or "",
            "profile": s.get("profile") or "-",
            "status": label,
            "outcome": s.get("outcome") or "",
            "assignee": w.get("last_assignee") or "",
            "created": w.get("first_seen_ts") or first_ts.get(iid) or 0,
            "finished": s.get("finished_at") or 0,
            "handoff": hs.get(iid) or 0,
            "attempts": s.get("attempts") or 0,
            "cost": s.get("cost_usd") or 0,
            "human_min": hm.get(iid, 0),
            # W5.2 停留時間基準:最近一次 state/assignee 變動(無變動=created)
            "last_change": last_change.get(iid)
                           or w.get("first_seen_ts")
                           or first_ts.get(iid) or 0,
        })
    rate = os.environ.get("ARCP_HOURLY_RATE")
    return {"rows": rows,
            "rate_default": float(rate) if rate else None}


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


# -- W5.6 DB 瀏覽器(唯讀連線;寫入被引擎層擋,WAL 可讀)------------------- #
def _db_ro():
    db = os.path.join(ROOT, "harness.db")
    if not os.path.exists(db):
        return None
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def db_tables() -> list[dict]:
    con = _db_ro()
    if con is None:
        return []
    out = []
    try:
        for (name,) in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "ORDER BY name"):
            n = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            out.append({"name": name, "rows": n})
    finally:
        con.close()
    return out


def db_table(name: str, limit: int, offset: int) -> dict:
    if name not in {t["name"] for t in db_tables()}:   # 白名單=真實表名
        return {"error": "no such table"}
    con = _db_ro()
    try:
        total = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        cur = con.execute(f'SELECT * FROM "{name}" LIMIT ? OFFSET ?',
                          (limit, offset))
        cols = [d[0] for d in cur.description]
        rows = [list(r) for r in cur.fetchall()]
        return {"columns": cols, "rows": rows, "total": total}
    finally:
        con.close()


def db_query(sql: str) -> dict:
    """唯讀查詢:連線 mode=ro(引擎層擋寫)+ 單語句 + SELECT/WITH/PRAGMA 前綴。"""
    s = (sql or "").strip().rstrip(";")
    low = s.lower()
    if not (low.startswith("select") or low.startswith("with")
            or low.startswith("pragma")):
        return {"error": "只允許 SELECT / WITH / PRAGMA(唯讀)"}
    if ";" in s:
        return {"error": "只允許單一語句"}
    con = _db_ro()
    if con is None:
        return {"error": "db 不存在"}
    try:
        cur = con.execute(s)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = [list(r) for r in cur.fetchmany(500)]
        return {"columns": cols, "rows": rows}
    except Exception as e:  # noqa: BLE001 — 把 SQL 錯誤回給 debug 頁
        return {"error": str(e)}
    finally:
        con.close()


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
table.resiz{table-layout:fixed}
table.resiz td{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rz{position:absolute;top:0;right:0;width:7px;height:100%;cursor:col-resize;user-select:none}
.rz:hover{background:#1f6feb}
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


_CONTROL_JS = ("<script>"
    "const CTL=" + json.dumps(CONTROL) + ";"
    "const $c=id=>document.getElementById(id);"
    "let armed=null;"
    # shutdown 防誤觸:第一次按「武裝」、3 秒內再按才執行(不用 confirm 對話框)
    "function cc(a){"
    "if(a==='shutdown'){"
    "if(armed!=='shutdown'){armed='shutdown';"
    "$c('sd').textContent='⏻ 再按一次確認';"
    "setTimeout(()=>{if(armed==='shutdown'){armed=null;"
    "$c('sd').textContent='⏻ Graceful Shutdown';}},3000);return;}"
    "armed=null;$c('sd').textContent='⏻ Graceful Shutdown';}"
    "fetch(CTL+'/'+a,{method:'POST'}).then(r=>r.json())"
    ".then(j=>{$c('cmsg').textContent=JSON.stringify(j);poll();})"
    ".catch(()=>{$c('cmsg').textContent='control 離線 '+CTL;});}"
    "async function poll(){try{"
    "const j=await (await fetch(CTL+'/status')).json();"
    "const t=[[j.paused?'⏸ 暫停':(j.stopping?'⏻ 關閉中':'▶ 運行'),'狀態'],"
    "[j.in_flight,'in-flight'],[j.queued,'queued'],[j.inactive,'inactive'],"
    "[(j.pending?Object.values(j.pending).reduce((a,b)=>a+b,0):0),'pending'],"
    "['$'+(j.cost_usd||0).toFixed(4),'總 cost'],[j.sessions,'sessions']];"
    "$c('cstatus').innerHTML=t.map(x=>`<div class='stat'><div class='n'>`+"
    "`${x[0]}</div><div class='l'>${x[1]}</div></div>`).join('');"
    "}catch(e){$c('cstatus').innerHTML=\"<span style='color:#f85149'>"
    "control API 離線(\"+CTL+\")——poller 未啟動?</span>\";}}"
    "poll();setInterval(poll,3000);"
    "</script>")


def render_control_page() -> str:
    """W5.8 Control 獨立頁:poller 全域控制(Pause/Resume/Reload/Shutdown)+
    即時狀態。作用於正在跑的 poller 進程(REST /8787,W2.6/W4.5)。"""
    return (f"{_nav('control')}"
            "<header><h1>Control · poller</h1></header><main>"
            "<div class='stats' id='cstatus'>載入中…</div>"
            "<div class='ctl card' style='margin-top:12px'>"
            "<div class='btn' onclick=\"cc('pause')\">⏸ Pause</div>"
            "<div class='btn' onclick=\"cc('resume')\">▶ Resume</div>"
            "<div class='btn' onclick=\"cc('reload')\">🔄 Reload</div>"
            "<div class='btn' id='sd' style='color:#f2a8a8' "
            "onclick=\"cc('shutdown')\">⏻ Graceful Shutdown</div>"
            "<span id='cmsg' style='color:#8b949e;font-size:12px'></span>"
            "</div>"
            "<p style='color:#8b949e;font-size:12px'>"
            "Pause=只 watch 不派新工(正在跑的不中斷);Reload=熱載 routes.yaml"
            "(壞 config 不生效、舊設定續用);Graceful Shutdown=當前輪(含壓縮"
            "打包)跑完後 poller 退出。詳見 DESIGN_hotreload.md。即時 kill 單張"
            "票用 ticket 頁的 Evict。</p></main>"
            f"{_CONTROL_JS}")


def _du_kb(path: str) -> int:
    """目錄磁碟用量(KB,best-effort du)。"""
    import subprocess
    try:
        r = subprocess.run(["du", "-sk", path], capture_output=True,
                           text=True, timeout=8)
        return int(r.stdout.split()[0]) if r.stdout.strip() else 0
    except Exception:  # noqa: BLE001
        return 0


def _workspace_info(s: dict, journal_starts: dict) -> dict:
    """W6.2 per-workspace:skill 名/session/sub-session/transcript/磁碟/跑時間。"""
    import glob
    ws = s.get("workspace") or ""
    base = os.path.dirname(ws) if ws.endswith("/ws") else ws
    skills, subs, tdir = [], [], ""
    if ws and not ws.startswith("("):
        skills = [os.path.basename(p) for p in
                  glob.glob(os.path.join(ws, ".claude", "skills", "*"))]
        td = transcript_dir_of(ws)
        if os.path.isdir(td):
            tdir = td
        # sub-session:~/.claude/projects/<slug>/<sid>/subagents/agent-*.jsonl
        sid = s.get("session_id") or ""
        if sid:
            hits = glob.glob(os.path.expanduser(
                f"~/.claude/projects/*/{sid}/subagents/agent-*.jsonl"))
            subs = [os.path.basename(h).removesuffix(".jsonl") for h in hits]
    started = journal_starts.get(s.get("issue_id"))
    return {
        "iid": s.get("issue_id"), "key": s.get("key"),
        "profile": s.get("profile"), "workspace": ws,
        "skills": skills, "session_id": s.get("session_id") or "",
        "subs": subs, "transcript_dir": tdir,
        "disk_mb": round(_du_kb(base) / 1024, 1) if base
                   and not base.startswith("(") and os.path.isdir(base)
                   else 0,
        "run_since": started,
    }


def build_server_data() -> dict:
    """W6.1/6.2/6.6 Server 頁單一資料源。"""
    data = {"sys": sysinfo_collect() if sysinfo_collect else None}
    data["conns"] = list(_CONNS)[-30:][::-1]        # W6.6 近期連線(新→舊)
    # W6.2:進程 + per-workspace(只掃 active session,省成本)
    procs = []
    try:
        from arcp_harness.sysinfo import processes
        procs = processes()
    except Exception:  # noqa: BLE001
        procs = []
    sessions = read_sessions()
    journal = read_journal()
    starts = {}
    for e in journal:
        if e.get("type") == "attempt_started":
            iid = e.get("issue_id")
            starts.setdefault(iid, e.get("ts"))     # 首個 attempt_started
    active = [s for s in sessions.values()
              if not s.get("outcome") and not s.get("pending_reason")
              and not s.get("inactive")]
    workspaces = [_workspace_info(s, starts) for s in active]
    # 進程對應 workspace(cwd 前綴比對)→ 附 Jira
    for p in procs:
        cwd = p.get("cwd") or ""
        for w in workspaces:
            if w["workspace"] and cwd.startswith(w["workspace"].rstrip("/")):
                p["iid"], p["ticket"] = w["iid"], w["key"]
                break
    data["processes"] = procs
    data["workspaces"] = workspaces
    # W6.3:強制驅逐統計(異常處理健康指標)
    evicts = [(s.get("key"), s.get("evict_count") or 0)
              for s in sessions.values() if s.get("evict_count")]
    data["evict"] = {"total": sum(c for _, c in evicts),
                     "by_ticket": sorted(evicts, key=lambda x: -x[1])}
    return data


_SERVER_JS = ("<script>"
    "const $s=id=>document.getElementById(id);"
    "function esc(x){return (''+x).replace(/&/g,'&amp;').replace(/</g,'&lt;');}"
    "function dur(s){s=+s||0;const d=s/86400|0,h=s%86400/3600|0,"
    "m=s%3600/60|0;return d?d+'d '+h+'h':(h?h+'h '+m+'m':m+'m');}"
    "function gb(b){return ((+b||0)/1e9).toFixed(1)+'GB';}"
    "function tile(n,l){return `<div class='stat'><div class='n'>${n}"
    "</div><div class='l'>${l}</div></div>`;}"
    "function kv(k,v){return `<div class='kv'><b>${k}</b> ${esc(v)}</div>`;}"
    "async function load(){"
    "let d;try{d=await (await fetch('/server/data')).json();}"
    "catch(e){$s('sroot').innerHTML=\"<p style='color:#f85149'>載入失敗</p>\";"
    "return;}"
    "const sy=d.sys||{};const v=sy.versions||{},a=sy.auth||{},"
    "r=sy.resources||{},m=r.mem||{},dk=r.disk||{};"
    "const badge=b=>b?\"<span style='color:#7ee2a8'>✓</span>\":"
    "\"<span style='color:#f2a8a8'>✗</span>\";"
    "let h='';"
    # 強制驅逐統計(W6.3)
    "const ev=d.evict||{total:0,by_ticket:[]};"
    "if(ev.total)h+=\"<div class='card' style='border-color:#4d3d1a'>\"+"
    "`<b style='color:#e2d07e'>⚠ 強制驅逐(異常處理)</b> 總計 ${ev.total} 次`+"
    "ev.by_ticket.map(t=>`<div>• ${esc(t[0])}: ${t[1]} 次</div>`).join('')"
    "+'</div>';"
    # 系統異常
    "if((sy.anomalies||[]).length)h+=\"<div class='card' style='border-color:"
    "#4d1a1a'><b style='color:#f2a8a8'>⚠ 異常</b>\"+sy.anomalies.map("
    "x=>`<div>• ${esc(x)}</div>`).join('')+'</div>';"
    # 資源 tiles
    "h+=\"<h2>資源</h2><div class='stats'>\"+"
    "tile((r.loadavg||[0])[0]+' / '+(r.cpus||'?'),'load / cores')+"
    "tile(gb(m.used)+' / '+gb(m.total),'記憶體')+"
    "tile(gb(m.free),'free mem')+"
    "tile(gb(dk.free)+' / '+gb(dk.total),'磁碟 free/total')+"
    "tile(dur(r.uptime_sec),'uptime')+'</div>';"
    # 版本
    "h+=\"<h2>版本</h2><div class='card'>\"+kv('OS',v.os||'?')+"
    "kv('kernel',v.kernel||'?')+kv('python',v.python||'?')+"
    "kv('claude',v.claude||'?')+kv('codex',v.codex||'?')+"
    "kv('workspace',r.cwd||'?')+'</div>';"
    # 登入狀態(只狀態不顯值)
    "h+=\"<h2>登入 / 金鑰(只顯示狀態,不顯示值)</h2><div class='card'>\"+"
    "`<div class='kv'><b>codex 已登入</b> ${badge(a.codex_logged_in)}</div>`+"
    "`<div class='kv'><b>claude 已設定</b> ${badge(a.claude_configured)}</div>`+"
    "`<div class='kv'><b>ANTHROPIC_API_KEY(env)</b> "
    "${badge(a.anthropic_api_key_env)}</div>`+'</div>';"
    # per-process(W6.2)
    "const ps=d.processes||[];"
    "h+=\"<h2>Agent 進程(claude/codex)</h2><div class='card'>\"+(ps.length?"
    "\"<table id='tix'><thead><tr><td><b>engine</b></td><td><b>Jira</b></td>\"+"
    "\"<td><b>PID</b></td><td><b>CPU%</b></td><td><b>MEM</b></td>\"+"
    "\"<td><b>cwd</b></td></tr></thead><tbody>\"+ps.map(p=>`<tr><td>`+"
    "`${esc(p.engine)}</td><td>${esc(p.ticket||'-')}</td><td>${esc(p.pid)}`+"
    "`</td><td>${p.cpu}</td><td>${p.rss_mb}MB</td><td>${esc(p.cwd||'-')}`+"
    "`</td></tr>`).join('')+'</tbody></table>':"
    "\"<span style='color:#8b949e'>(目前無 claude/codex 進程在跑)</span>\")"
    "+'</div>';"
    # per-workspace(W6.2)
    "const ws=d.workspaces||[];"
    "h+=\"<h2>Workspace(進行中)</h2><div class='card'>\"+(ws.length?"
    "\"<table id='tix'><thead><tr><td><b>Jira</b></td><td><b>profile</b></td>\"+"
    "\"<td><b>skills</b></td><td><b>session</b></td><td><b>sub</b></td>\"+"
    "\"<td><b>磁碟</b></td><td><b>path</b></td></tr></thead><tbody>\"+"
    "ws.map(w=>`<tr><td>${esc(w.key)}</td><td>${esc(w.profile)}</td>`+"
    "`<td>${esc((w.skills||[]).join(',')||'-')}</td>`+"
    "`<td>${esc((w.session_id||'').slice(0,8)||'-')}</td>`+"
    "`<td>${(w.subs||[]).length}</td><td>${w.disk_mb}MB</td>`+"
    "`<td>${esc(w.workspace)}</td></tr>`).join('')+'</tbody></table>':"
    "\"<span style='color:#8b949e'>(目前無進行中 workspace)</span>\")+'</div>';"
    # 連線(W6.6)
    "const cs=d.conns||[];"
    "h+=\"<h2>連線(近期)</h2><div class='card'>\"+(cs.length?"
    "\"<table id='tix'><thead><tr><td><b>時間</b></td><td><b>IP</b></td>\"+"
    "\"<td><b>path</b></td></tr></thead><tbody>\"+cs.map(c=>`<tr><td>`+"
    "`${esc(c.t)}</td><td>${esc(c.ip)}</td><td>${esc(c.path)}</td></tr>`)"
    ".join('')+'</tbody></table>':"
    "\"<span style='color:#8b949e'>(尚無記錄)</span>\")+'</div>';"
    "$s('sroot').innerHTML=h;}"
    "load();setInterval(load,4000);"
    "</script>")


def render_server_page() -> str:
    """W6.1 Server 頁:系統/版本/登入狀態/資源(+ W6.2 程序、W6.6 連線)。"""
    return (f"{_nav('server')}"
            "<header><h1>Server · 系統與程序</h1></header><main>"
            "<p style='color:#8b949e;font-size:12px'>dashboard 綁 "
            f"{esc(HOST)}(內網開放,唯讀);登入/金鑰只顯示狀態,不顯示值。"
            " <a href='/docs' style='color:#58a6ff'>REST API 文件</a></p>"
            "<div id='sroot'>載入中…</div></main>"
            f"{_SERVER_JS}")


_APP_JS = """
<script>
const LS='arcp-v2';
let D={rows:[],rate_default:null};
let S=Object.assign({qr:'all',from:'',to:'',st:'',ksum:'',kdesc:'',
  size:20,page:0,sort:'created',dir:-1,wk1:false,wk2:false,rate:null},
  (()=>{try{return JSON.parse(localStorage.getItem(LS))||{}}catch(e){return{}}})());
function save(){localStorage.setItem(LS,JSON.stringify(S));}
const $=id=>document.getElementById(id);
const C={created:'#58a6ff',closed:'#a371f7',success:'#3fb950',fail:'#f85149',
  ai:'#d29922',human:'#58a6ff',waste:'#f85149'};
// ---- 過濾(置頂,統管全部) ----
function filtered(){
  const now=Date.now()/1000;
  let lo=0,hi=Infinity;
  if(S.from){lo=new Date(S.from+'T00:00:00').getTime()/1000;}
  if(S.to){hi=new Date(S.to+'T23:59:59').getTime()/1000;}
  if(!S.from&&!S.to&&S.qr!=='all'){lo=now-(+S.qr)*86400;}
  const ks=S.ksum.toLowerCase(),kd=S.kdesc.toLowerCase();
  return D.rows.filter(r=>
    r.created>=lo&&r.created<=hi&&
    (!S.st||r.status===S.st)&&
    (!ks||(r.key+' '+r.summary).toLowerCase().includes(ks))&&
    (!kd||r.desc.toLowerCase().includes(kd)));
}
// ---- 統計卡 ----
function renderStats(rows){
  const cost=rows.reduce((a,r)=>a+r.cost,0);
  const oc=o=>rows.filter(r=>r.outcome===o).length;
  const st=p=>rows.filter(r=>r.status.startsWith(p)).length;
  const succ=oc('SUCCESS'),fail=oc('FAILURE'),done=succ+fail;
  const mins=rows.reduce((a,r)=>a+r.human_min,0);
  const t=[["$"+cost.toFixed(4),'總 cost'],[st('active'),'in-flight'],
    [st('QUEUED'),'queued'],[st('INACTIVE'),'inactive'],
    [st('pending'),'pending'],[succ,'SUCCESS'],[fail,'FAILURE'],
    [done?Math.round(fail/done*100)+'%':'–','失敗率']];
  if(mins){t.push([(mins/60).toFixed(1)+'h','節省人時']);
    if(S.rate)t.push(['$'+Math.round(mins/60*S.rate)+' vs $'+cost.toFixed(2),
      '人力成本對比']);}
  $('stats').innerHTML=t.map(([n,l])=>
    `<div class='stat'><div class='n'>${n}</div><div class='l'>${l}</div></div>`).join('');
}
// ---- 分桶(日/週) ----
function bkey(ts,wk){const d=new Date(ts*1000);
  if(wk){const day=(d.getDay()+6)%7;d.setDate(d.getDate()-day);}
  return (d.getMonth()+1+'').padStart(2,'0')+'-'+(d.getDate()+'').padStart(2,'0');}
function bidx(ts,lo,wk){return Math.floor((ts-lo)/(wk?604800:86400));}
function buckets(rows,wk){
  const ts=[];rows.forEach(r=>{if(r.created)ts.push(r.created);
    if(r.finished)ts.push(r.finished);});
  if(!ts.length)return null;
  let lo=Math.min(...ts);const hi=Math.max(...ts);
  const d=new Date(lo*1000);d.setHours(0,0,0,0);
  if(wk){const day=(d.getDay()+6)%7;d.setDate(d.getDate()-day);}
  lo=d.getTime()/1000;
  const n=Math.min(400,bidx(hi,lo,wk)+1);
  const keys=[];for(let i=0;i<n;i++)keys.push(bkey(lo+i*(wk?604800:86400)+43200,false));
  return {lo,n,keys,wk};
}
function cum(a){let s=0;return a.map(v=>(s+=v));}
// ---- SVG 組合圖(長條 + 累積曲線,雙軸) ----
function drawCombo(el,B,bars,lines,fmtL,fmtR){
  if(!B){el.innerHTML='';return;}
  const W=el.parentElement.clientWidth-40||1000,H=240,L=46,R=52,T=8,BM=26;
  const pw=W-L-R,ph=H-T-BM;
  const bmax=Math.max(1,...bars.flatMap(b=>b.vals));
  const lmax=Math.max(1,...lines.flatMap(l=>l.vals));
  const gx=i=>L+pw*(i+0.5)/B.n, gw=Math.max(1,pw/B.n*0.8/Math.max(1,bars.length));
  let s='';
  for(let t=1;t<=3;t++){const y=T+ph-ph*t/3;
    s+=`<line x1='${L}' y1='${y}' x2='${W-R}' y2='${y}' stroke='#21262d'/>`+
    `<text x='${L-4}' y='${y+4}' fill='#8b949e' font-size='10' text-anchor='end'>${fmtL(bmax*t/3)}</text>`+
    `<text x='${W-R+4}' y='${y+4}' fill='#8b949e' font-size='10'>${fmtR(lmax*t/3)}</text>`;}
  bars.forEach((b,bi)=>{b.vals.forEach((v,i)=>{if(!v)return;
    const h=ph*v/bmax,x=gx(i)-gw*bars.length/2+bi*gw;
    s+=`<rect x='${x}' y='${T+ph-h}' width='${gw}' height='${h}' fill='${b.c}' opacity='0.75'/>`;});});
  lines.forEach(l=>{const pts=l.vals.map((v,i)=>gx(i)+','+(T+ph-ph*v/lmax)).join(' ');
    s+=`<polyline points='${pts}' fill='none' stroke='${l.c}' stroke-width='1.8'/>`;});
  const step=Math.ceil(B.n/9);
  for(let i=0;i<B.n;i+=step)
    s+=`<text x='${gx(i)}' y='${H-8}' fill='#8b949e' font-size='10' text-anchor='middle'>${B.keys[i]}</text>`;
  el.setAttribute('viewBox',`0 0 ${W} ${H}`);el.setAttribute('width',W);
  el.setAttribute('height',H);el.innerHTML=s;
}
function legend(el,items){el.innerHTML=items.map(([c,n,dash])=>
  `<span style='margin-right:14px;font-size:11px;color:#8b949e'>`+
  `<span style='display:inline-block;width:${dash?14:9}px;height:${dash?3:9}px;background:${c};`+
  `border-radius:2px;margin-right:4px;vertical-align:middle'></span>${n}</span>`).join('');}
// ---- 時間圖 ----
function renderTime(rows){
  const B=buckets(rows,S.wk1);const el=$('chart-time');
  if(!B){el.innerHTML='';return;}
  const z=()=>Array(B.n).fill(0);
  const cr=z(),cl=z(),su=z(),fa=z();
  rows.forEach(r=>{
    if(r.created){const i=bidx(r.created,B.lo,B.wk);if(i>=0&&i<B.n)cr[i]++;}
    if(r.finished){const i=bidx(r.finished,B.lo,B.wk);
      if(i>=0&&i<B.n){cl[i]++;if(r.outcome==='SUCCESS')su[i]++;
        if(r.outcome==='FAILURE')fa[i]++;}}});
  drawCombo(el,B,
    [{c:C.created,vals:cr},{c:C.closed,vals:cl},{c:C.success,vals:su},{c:C.fail,vals:fa}],
    [{c:C.created,vals:cum(cr)},{c:C.closed,vals:cum(cl)},{c:C.success,vals:cum(su)},{c:C.fail,vals:cum(fa)}],
    v=>Math.round(v),v=>Math.round(v));
  legend($('lg-time'),[[C.created,'Create'],[C.closed,'Close'],
    [C.success,'成功'],[C.fail,'失敗'],['#c9d1d9','(條=單期,線=累積)',1]]);
}
// ---- 金錢圖 ----
function renderMoney(rows){
  const B=buckets(rows,S.wk2);const el=$('chart-money');
  if(!B){el.innerHTML='';return;}
  const z=()=>Array(B.n).fill(0);
  const ai=z(),hu=z(),wa=z();
  const rate=S.rate||0;
  rows.forEach(r=>{if(!r.finished)return;
    const i=bidx(r.finished,B.lo,B.wk);if(i<0||i>=B.n)return;
    ai[i]+=r.cost;hu[i]+=r.human_min/60*rate;
    if(r.outcome==='FAILURE')wa[i]+=r.cost;});
  drawCombo(el,B,[{c:C.ai,vals:ai},{c:C.human,vals:hu}],
    [{c:C.ai,vals:cum(ai)},{c:C.human,vals:cum(hu)},{c:C.waste,vals:cum(wa)}],
    v=>'$'+v.toFixed(2),v=>'$'+v.toFixed(2));
  legend($('lg-money'),[[C.ai,'AI 花費'],[C.human,'人類預估(時薪$'+(rate||'?')+')'],
    [C.waste,'失敗浪費(累積)',1],['#c9d1d9','(條=單期,線=累積)',1]]);
}
// ---- 表格(排序 + 分頁) ----
const COLS=[['key','ticket'],['summary','summary'],['profile','profile'],
  ['status','status'],['assignee','assignee'],['created','created'],
  ['finished','finished'],['handoff','換手起點'],
  ['dwell','停留時間'],['lifetime','lifetime'],['human_cost','人力$'],
  ['attempts','attempts'],['cost','cost']];
// W5.2 計算欄:停留時間(state/assignee 最後變動起算,close 凍結)、
// lifetime(create→close 或→現在)、人力$(預估分鐘×時薪)
function prep(){const now=Date.now()/1000;D.rows.forEach(r=>{
  const end=r.finished||now;
  r.lifetime=r.created?Math.max(0,(end-r.created)/86400):0;
  const lc=r.last_change||r.created;
  r.dwell=lc?Math.max(0,(end-lc)/86400):0;
  r.human_cost=r.human_min/60*(S.rate||0);});}
function fdays(d){return d>=1?d.toFixed(1)+'d':Math.round(d*24)+'h';}
function fmt(ts){if(!ts)return '-';const d=new Date(ts*1000);
  return (d.getMonth()+1+'').padStart(2,'0')+'-'+(d.getDate()+'').padStart(2,'0')
  +' '+(d.getHours()+'').padStart(2,'0')+':'+(d.getMinutes()+'').padStart(2,'0');}
function badgeCls(st){if(st==='SUCCESS'||st==='FAILURE'||st==='UNKNOWN'||st==='ABORTED')return st;
  if(st.startsWith('pending'))return 'pending';if(st.startsWith('QUEUED'))return 'queued';
  if(st==='INACTIVE')return 'inactive';return st==='active'?'running':'';}
function esc(x){return (''+x).replace(/&/g,'&amp;').replace(/</g,'&lt;');}
function renderTable(rows){
  const k=S.sort;
  rows=[...rows].sort((a,b)=>{const x=a[k],y=b[k];
    return (typeof x==='number'?x-y:(''+x).localeCompare(''+y))*S.dir;});
  const pages=Math.max(1,Math.ceil(rows.length/S.size));
  if(S.page>=pages)S.page=pages-1;
  const pg=rows.slice(S.page*S.size,(S.page+1)*S.size);
  $('thead-row').innerHTML=COLS.map(([c,l])=>
    `<td class='sortable' data-col='${c}' style='cursor:pointer'><b>${l}${
      S.sort===c?(S.dir>0?' ▲':' ▼'):''}</b></td>`).join('');
  document.querySelector('#tix tbody').innerHTML=pg.map(r=>
    `<tr><td><a href='/ticket/${r.iid}'>${esc(r.key)}</a></td>`+
    `<td title='${esc(r.summary)}'>${esc(r.summary.slice(0,28))}</td>`+
    `<td>${esc(r.profile)}</td>`+
    `<td><span class='badge ${badgeCls(r.status)}'>${esc(r.status)}</span></td>`+
    `<td>${esc(r.assignee||'-')}</td><td>${fmt(r.created)}</td>`+
    `<td>${fmt(r.finished)}</td><td>${fmt(r.handoff)}</td>`+
    `<td>${r.created?fdays(r.dwell):'-'}</td>`+
    `<td>${r.created?fdays(r.lifetime):'-'}</td>`+
    `<td>${r.human_min?'$'+r.human_cost.toFixed(2):'-'}</td>`+
    `<td>${r.attempts}</td><td>$${r.cost.toFixed(4)}</td></tr>`).join('');
  $('pginfo').textContent=rows.length+' 筆 · 第 '+(S.page+1)+'/'+pages+' 頁';
  resizable($('tix'),'tix');          // W5.7 欄寬可拖曳
}
function render(){prep();const rows=filtered();renderStats(rows);
  renderTime(rows);renderMoney(rows);renderTable(rows);save();}
function pg(d){S.page=Math.max(0,S.page+d);render();}
// ---- W5.6 匯出經 filter+sort 的資料(CSV / JSON)----
const EXCOLS=[['key','ticket'],['summary','summary'],['profile','profile'],
  ['status','status'],['assignee','assignee'],['created','created'],
  ['finished','finished'],['handoff','handoff'],['dwell','dwell_days'],
  ['lifetime','lifetime_days'],['human_cost','human_cost_usd'],
  ['attempts','attempts'],['cost','cost_usd']];
function iso(ts){return ts?new Date(ts*1000).toISOString():'';}
function exval(r,k){
  if(k==='created'||k==='finished'||k==='handoff')return iso(r[k]);
  if(k==='dwell'||k==='lifetime')return r[k]?r[k].toFixed(2):'';
  if(k==='human_cost')return r.human_min?r.human_cost.toFixed(2):'';
  if(k==='cost')return r.cost.toFixed(4);
  return r[k]==null?'':r[k];
}
function expoRows(){prep();return filtered().sort((a,b)=>{
  const x=a[S.sort],y=b[S.sort];
  return (typeof x==='number'?x-y:(''+x).localeCompare(''+y))*S.dir;});}
function dl(blob,name){const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);a.download=name;a.click();
  URL.revokeObjectURL(a.href);}
function expo(fmt){
  const rows=expoRows();
  if(fmt==='json'){
    const arr=rows.map(r=>{const o={};EXCOLS.forEach(([k,l])=>{
      o[l]=(k==='created'||k==='finished'||k==='handoff')?iso(r[k]):
        (k==='human_cost'?(r.human_min?+r.human_cost.toFixed(2):null):r[k]);});
      return o;});
    dl(new Blob([JSON.stringify(arr,null,2)],{type:'application/json'}),
       'arcp-tickets.json');
  }else{
    const q=v=>{v=''+v;return /[",\\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;};
    const head=EXCOLS.map(c=>c[1]).join(',');
    const body=rows.map(r=>EXCOLS.map(c=>q(exval(r,c[0]))).join(',')).join('\\n');
    dl(new Blob([head+'\\n'+body],{type:'text/csv'}),'arcp-tickets.csv');
  }
}
// ---- 事件綁定(shell 元素只綁一次) ----
function bind(){
  $('qr').value=S.qr;$('from').value=S.from;$('to').value=S.to;
  $('ksum').value=S.ksum;$('kdesc').value=S.kdesc;$('psize').value=S.size;
  $('wk1').checked=S.wk1;$('wk2').checked=S.wk2;
  if(S.rate!=null)$('rate').value=S.rate;
  $('qr').onchange=e=>{S.qr=e.target.value;S.page=0;render();};
  $('from').onchange=e=>{S.from=e.target.value;S.page=0;render();};
  $('to').onchange=e=>{S.to=e.target.value;S.page=0;render();};
  $('st').onchange=e=>{S.st=e.target.value;S.page=0;render();};
  $('ksum').oninput=e=>{S.ksum=e.target.value;S.page=0;render();};
  $('kdesc').oninput=e=>{S.kdesc=e.target.value;S.page=0;render();};
  $('psize').onchange=e=>{S.size=+e.target.value;S.page=0;render();};
  $('wk1').onchange=e=>{S.wk1=e.target.checked;render();};
  $('wk2').onchange=e=>{S.wk2=e.target.checked;render();};
  $('rate').oninput=e=>{S.rate=+e.target.value||0;render();};
  document.querySelector('#tix thead').addEventListener('click',e=>{
    const td=e.target.closest('.sortable');if(!td)return;
    const c=td.dataset.col;
    if(S.sort===c)S.dir=-S.dir;else{S.sort=c;S.dir=1;}
    render();});
}
async function tick(){
  try{
    const r=await fetch('/data');D=await r.json();
    if(S.rate==null)S.rate=D.rate_default!=null?D.rate_default:40;
    const sel=$('st'),cur=S.st;
    const sts=[...new Set(D.rows.map(r=>r.status))].sort();
    sel.innerHTML=`<option value=''>全部狀態</option>`+
      sts.map(v=>`<option${v===cur?' selected':''}>${esc(v)}</option>`).join('');
    render();
  }catch(e){}
}
bind();tick();setInterval(tick,5000);
</script>"""


_INPUT = ("style='background:#0d1117;color:#c9d1d9;border:1px solid #30363d;"
          "border-radius:6px;padding:4px 10px'")


_RESIZE_JS = """
<script>
// W5.7 欄寬可拖曳:表頭右緣拖把 → 調欄寬;寬度存 localStorage 跨重載留存。
// 先量測(切 table-layout:fixed 前)並凍結進 store,避免 re-render 後被平均重置。
window.RESW=window.RESW||(function(){try{
  return JSON.parse(localStorage.getItem('arcp-resw'))||{}}catch(e){return{}}})();
function _saveResw(){try{
  localStorage.setItem('arcp-resw',JSON.stringify(window.RESW))}catch(e){}}
function resizable(table,key){
  if(!table||!table.tHead||!table.tHead.rows.length)return;
  const cells=[...table.tHead.rows[0].cells];
  const store=window.RESW[key]||(window.RESW[key]={});
  // 量測需在切 fixed 前(此時 offsetWidth 為自然寬);已存過就沿用
  cells.forEach(function(c,i){if(store[i]==null)store[i]=c.offsetWidth;});
  table.classList.add('resiz');
  function applyW(){var tw=0; cells.forEach(function(c,j){
    c.style.width=store[j]+'px'; tw+=store[j];}); table.style.width=tw+'px';}
  applyW();                            // 不變式:表寬=各欄寬總和(免重分配)
  cells.forEach(function(c,i){
    c.style.position='relative';
    const h=document.createElement('div'); h.className='rz';
    h.addEventListener('click',function(e){e.stopPropagation();});
    h.addEventListener('mousedown',function(e){
      e.preventDefault(); e.stopPropagation();
      const sx=e.clientX, sw=store[i];
      function mv(ev){store[i]=Math.max(40,sw+ev.clientX-sx); applyW();}
      function up(){document.removeEventListener('mousemove',mv);
        document.removeEventListener('mouseup',up);
        document.body.style.userSelect=''; _saveResw();}
      document.addEventListener('mousemove',mv);
      document.addEventListener('mouseup',up);
      document.body.style.userSelect='none';});
    c.appendChild(h);});
}
</script>"""


def _nav(active: str) -> str:
    """W5.6 頂部導覽:Dashboard / DB 兩個 tab。"""
    def tab(key, href, label):
        on = ("background:#1f6feb;color:#fff" if key == active
              else "background:#21262d;color:#c9d1d9")
        return (f"<a href='{href}' style='padding:6px 16px;border-radius:6px;"
                f"text-decoration:none;{on}'>{label}</a>")
    return ("<div style='display:flex;gap:8px;padding:12px 24px;"
            "background:#161b22;border-bottom:1px solid #30363d'>"
            + tab("dash", "/", "📊 Dashboard")
            + tab("db", "/db", "🗃 DB Browser")
            + tab("control", "/control", "🎛 Control")
            + tab("server", "/server", "🖥 Server") + "</div>")


_DB_JS = """
<script>
const $=id=>document.getElementById(id);
let CUR=null, OFF=0, LIM=100, DBMODE='', LASTQ=null;
function esc(x){return (''+x).replace(/&/g,'&amp;').replace(/</g,'&lt;');}
// W5.8 匯出:table 模式抓全表(免分頁截斷),query 模式用查詢結果(≤500)
function _dl(blob,name){const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);a.download=name;a.click();
  URL.revokeObjectURL(a.href);}
async function dbExport(fmt){
  let cols,rows,name;
  if(DBMODE==='table'&&CUR){
    const d=await (await fetch(`/db/table/${CUR}?limit=1000000&offset=0`))
      .json();
    if(d.error){alert(d.error);return;}
    cols=d.columns;rows=d.rows;name=CUR;
  }else if(LASTQ){cols=LASTQ.cols;rows=LASTQ.rows;name='query';}
  else return;
  if(fmt==='json'){
    const arr=rows.map(r=>{const o={};cols.forEach((c,i)=>o[c]=r[i]);return o;});
    _dl(new Blob([JSON.stringify(arr,null,2)],{type:'application/json'}),
        name+'.json');
  }else{
    const q=v=>{if(v==null)return '';v=''+v;
      return /[",\\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;};
    const csv=[cols.join(',')].concat(
      rows.map(r=>r.map(q).join(','))).join('\\n');
    _dl(new Blob([csv],{type:'text/csv'}),name+'.csv');
  }
}
function tbl(cols,rows,total){
  if(!rows.length)return "<p style='color:#8b949e'>(無資料)</p>";
  const h='<tr>'+cols.map(c=>`<td><b>${esc(c)}</b></td>`).join('')+'</tr>';
  const b=rows.map(r=>'<tr>'+r.map(v=>`<td>${v==null?
    "<span style='color:#6e7681'>null</span>":esc((''+v).slice(0,200))}`+
    '</td>').join('')+'</tr>').join('');
  return `<div style='overflow:auto;max-height:60vh'><table id='tix'>`+
    `<thead>${h}</thead><tbody>${b}</tbody></table></div>`+
    (total!=null?`<div style='color:#8b949e;font-size:12px;margin-top:6px'>`+
    `${total} 筆;顯示 ${OFF+1}-${OFF+rows.length}</div>`:'');
}
async function loadTables(){
  const t=await (await fetch('/db/tables')).json();
  $('tlist').innerHTML=t.map(x=>
    `<div class='btn' style='display:block;margin:4px 0;text-align:left' `+
    `onclick="openT('${x.name}')">${esc(x.name)} `+
    `<span style='color:#8b949e;float:right'>${x.rows}</span></div>`).join('');
}
async function openT(name){CUR=name;OFF=0;$('qbox').value='';showTable();}
async function showTable(){
  const d=await (await fetch(`/db/table/${CUR}?limit=${LIM}&offset=${OFF}`))
    .json();
  if(d.error){$('dbout').innerHTML="<p style='color:#f85149'>"+esc(d.error)+
    "</p>";return;}
  DBMODE='table';
  $('dbtitle').textContent='📋 '+CUR;
  $('dbpg').style.display=d.total>LIM?'flex':'none';
  $('dbout').innerHTML=tbl(d.columns,d.rows,d.total);
  resizable(document.querySelector('#dbout table'),'db:'+CUR);  // W5.7
}
function dpg(dir){OFF=Math.max(0,OFF+dir*LIM);showTable();}
async function runQ(){
  const sql=$('qbox').value.trim();if(!sql)return;
  CUR=null;$('dbpg').style.display='none';
  const d=await (await fetch('/db/query',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({sql})})).json();
  if(d.error){$('dbtitle').textContent='⚠ 查詢錯誤';
    $('dbout').innerHTML="<p style='color:#f85149'>"+esc(d.error)+"</p>";return;}
  DBMODE='query';LASTQ={cols:d.columns,rows:d.rows};
  $('dbtitle').textContent='🔎 查詢結果';
  $('dbout').innerHTML=tbl(d.columns,d.rows,null)+
    (d.rows.length>=500?"<p style='color:#d29922;font-size:12px'>"+
    "(上限 500 列)</p>":'');
  resizable(document.querySelector('#dbout table'),'db:query');  // W5.7
}
$('qbox').addEventListener('keydown',e=>{
  if((e.metaKey||e.ctrlKey)&&e.key==='Enter')runQ();});
loadTables();
</script>"""


def render_db_page() -> str:
    """W5.6 SQLite 瀏覽器 tab(唯讀,debug 用)。"""
    return (f"{_nav('db')}"
            f"<header><h1>DB Browser · harness.db "
            f"<span style='color:#8b949e;font-size:13px'>(唯讀)</span>"
            f"</h1></header><main>"
            "<div style='display:flex;gap:16px;align-items:flex-start'>"
            "<div class='card' style='min-width:200px'>"
            "<b style='color:#8b949e'>Tables</b><div id='tlist'></div></div>"
            "<div style='flex:1'>"
            "<div class='card'>"
            "<b style='color:#8b949e'>唯讀查詢</b>"
            "<span style='color:#6e7681;font-size:11px'> "
            "SELECT / WITH / PRAGMA;⌘/Ctrl+Enter 執行</span>"
            "<textarea id='qbox' placeholder='SELECT * FROM ticket_session "
            "WHERE outcome IS NULL' style='width:100%;height:64px;margin-top:"
            "6px;background:#0d1117;color:#c9d1d9;border:1px solid #30363d;"
            "border-radius:6px;padding:8px;font-family:ui-monospace,monospace;"
            "box-sizing:border-box'></textarea>"
            "<div class='btn' style='margin-top:6px' onclick='runQ()'>▶ 執行"
            "</div></div>"
            "<div class='card'>"
            "<div style='display:flex;align-items:center'>"
            "<h2 id='dbtitle' style='margin:0;flex:1'>← 點左側表格</h2>"
            "<div class='btn' onclick='dbExport(\"csv\")'>⬇ CSV</div>"
            "<div class='btn' onclick='dbExport(\"json\")'>⬇ JSON</div></div>"
            "<div id='dbpg' class='ctl' style='display:none;margin:8px 0'>"
            "<div class='btn' onclick='dpg(-1)'>‹ 上頁</div>"
            "<div class='btn' onclick='dpg(1)'>下頁 ›</div></div>"
            "<div id='dbout' style='margin-top:8px'></div></div>"
            "</div></div></main>"
            f"{_RESIZE_JS}{_DB_JS}")


def render_index(journal, sessions, watch=None) -> str:
    """W4.7 dashboard v2:過濾器置頂(統管統計/圖表/表格)+ 時間圖/金錢圖
    + 排序表格。初始表格由 server 渲染(no-JS/e2e 可讀),JS 從 /data 接手。"""
    watch = watch or {}
    import time as _t
    now = _t.time()
    rate = os.environ.get("ARCP_HOURLY_RATE")
    rate = float(rate) if rate else None

    def _days(d: float) -> str:
        return f"{d:.1f}d" if d >= 1 else f"{round(d * 24)}h"

    rows = ""
    for r in build_data(journal, sessions, watch)["rows"]:
        end = r["finished"] or now
        life = _days(max(0.0, (end - r["created"]) / 86400)) \
            if r["created"] else "-"
        dwell = _days(max(0.0, (end - (r["last_change"] or r["created"]))
                          / 86400)) if r["created"] else "-"
        hcost = (f"${r['human_min'] / 60 * rate:.2f}"
                 if r["human_min"] and rate else "-")
        rows += (f"<tr><td><a href='/ticket/{r['iid']}'>{esc(r['key'])}"
                 f"</a></td>"
                 f"<td>{esc(r['summary'][:28])}</td>"
                 f"<td>{esc(r['profile'])}</td>"
                 f"<td><span class='badge'>{esc(r['status'])}</span></td>"
                 f"<td>{esc(r['assignee'] or '-')}</td>"
                 f"<td>{esc(fmt_ts(r['created']))}</td>"
                 f"<td>{esc(fmt_ts(r['finished']))}</td>"
                 f"<td>{esc(fmt_ts(r['handoff']))}</td>"
                 f"<td>{dwell}</td><td>{life}</td><td>{hcost}</td>"
                 f"<td>{r['attempts']}</td>"
                 f"<td>${r['cost']:.4f}</td></tr>")
    filterbar = (
        "<div class='ctl card' style='flex-wrap:wrap'>"
        "<b style='color:#8b949e'>過濾</b>"
        f"<select id='qr' {_INPUT}>"
        "<option value='all'>全部時間</option>"
        "<option value='7'>過去 7 天</option>"
        "<option value='30'>過去 30 天</option>"
        "<option value='60'>過去 60 天</option>"
        "<option value='90'>過去 90 天</option></select>"
        f"<input type='date' id='from' {_INPUT}>~"
        f"<input type='date' id='to' {_INPUT}>"
        f"<select id='st' {_INPUT}><option value=''>全部狀態</option></select>"
        f"<input id='ksum' placeholder='summary keyword…' {_INPUT}>"
        f"<input id='kdesc' placeholder='description keyword…' {_INPUT}>"
        "<span style='color:#6e7681;font-size:11px'>↓ 底下統計/圖表/表格"
        "皆只含過濾後的 Jira</span></div>")
    charts = (
        "<h2>時間圖(Create/Close/成功/失敗)</h2><div class='card'>"
        "<label style='color:#8b949e;font-size:12px'>"
        "<input type='checkbox' id='wk1'> 以每週呈現</label>"
        "<svg id='chart-time'></svg><div id='lg-time'></div></div>"
        "<h2>金錢圖(AI vs 人類)</h2><div class='card'>"
        "<label style='color:#8b949e;font-size:12px'>"
        "<input type='checkbox' id='wk2'> 以每週呈現</label>"
        " <label style='color:#8b949e;font-size:12px'>人類時薪 USD $"
        f"<input type='number' id='rate' min='0' step='1' {_INPUT} "
        "style='width:70px;background:#0d1117;color:#c9d1d9;"
        "border:1px solid #30363d;border-radius:6px;padding:2px 6px'>"
        "</label><svg id='chart-money'></svg><div id='lg-money'></div></div>")
    toolbar = (
        "<div class='ctl card'>"
        f"<select id='psize' {_INPUT}>"
        "<option>10</option><option selected>20</option>"
        "<option>50</option><option>100</option></select>"
        "<div class='btn' onclick='pg(-1)'>‹ 上頁</div>"
        "<div class='btn' onclick='pg(1)'>下頁 ›</div>"
        "<span id='pginfo' style='color:#8b949e;font-size:12px'></span>"
        "<span style='margin-left:auto'></span>"
        "<div class='btn' onclick='expo(\"csv\")'>⬇ CSV</div>"
        "<div class='btn' onclick='expo(\"json\")'>⬇ JSON</div></div>")
    return (f"{_nav('dash')}"
            f"<header><h1>ARCP Dashboard · {esc(ROOT.split('/')[-1])}"
            f"</h1></header><main>"
            f"{filterbar}"
            f"<div class='stats' id='stats'>"
            f"{overview_cards(sessions, journal)}</div>"
            f"{charts}"
            f"<h2>Tickets</h2>{toolbar}"
            f"<div class='card' style='overflow-x:auto'>"
            f"<table id='tix'><thead><tr id='thead-row'>"
            f"<td><b>ticket</b></td><td><b>summary</b></td>"
            f"<td><b>profile</b></td><td><b>status</b></td>"
            f"<td><b>assignee</b></td><td><b>created</b></td>"
            f"<td><b>finished</b></td><td><b>換手起點</b></td>"
            f"<td><b>停留時間</b></td><td><b>lifetime</b></td>"
            f"<td><b>人力$</b></td>"
            f"<td><b>attempts</b></td><td><b>cost</b></td></tr></thead>"
            f"<tbody>{rows}</tbody></table></div>"
            f"<p style='color:#8b949e'>四層 trace:L0 ticket · L1 attempt · "
            f"L2 envelope · L3 conversation events。點 ticket 展開。</p>"
            f"{_RESIZE_JS}{_APP_JS}</main>")


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


# W6.4:reason 代碼 → 人類可讀(meta.json 的產生原因)
_TRANSCRIPT_REASON = {
    "close:SUCCESS": "結案(成功)", "close:FAILURE": "結案(失敗)",
    "close:ABORTED": "結案(撤銷)", "evict": "強制驅逐(killpg)",
    "handoff-human": "轉交人類", "handoff-agent": "換手其他 agent",
    "handoff-cmd": "指令換手(@agent next)", "assignee-inactive": "指派給人類(暫停)",
    "pending:budget": "等待人類(預算耗盡)", "manual": "手動產生(按鈕)",
    "unknown": "未知",
}


def _read_transcript_meta(d: str) -> dict | None:
    """W6.4:讀 transcript/meta.json(產生時間/原因/sub-session)。"""
    p = os.path.join(d, "meta.json")
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def render_transcript_card(iid: int, s: dict) -> str:
    """W4.2/W6.4:transcript 卡。顯示是否已有 HTML、產生時間/原因(meta.json)、
    檢視/下載連結,並提供「產生 transcript」被動按鈕(control /gen_transcript)。
    workspace 為哨值/空 → 無 session 對象,不顯卡。"""
    ws = s.get("workspace") or ""
    if not ws or ws.startswith("("):
        return ""
    d = transcript_dir_of(ws)
    names = sorted(f for f in os.listdir(d)
                   if os.path.isfile(os.path.join(d, f))
                   and not f.startswith(".") and f != "meta.json") \
        if os.path.isdir(d) else []
    meta = _read_transcript_meta(d)

    # 產生資訊列:有 meta 顯示時間 + 原因 + sub-session 數;無產物則提示
    if meta:
        reason = _TRANSCRIPT_REASON.get(meta.get("reason", ""),
                                        meta.get("reason") or "未知")
        nsub = len(meta.get("subs") or [])
        info = (f"<span class='kv'><b>產生於</b> {esc(meta.get('generated_at','?'))}"
                f"</span><span class='kv'><b>原因</b> {esc(reason)}</span>"
                + (f"<span class='kv'><b>sub-session</b> {nsub}</span>"
                   if nsub else ""))
    else:
        info = "<span class='sys'>尚未產生 transcript(可按下方按鈕產生)</span>"

    links = "".join(
        f"<a class='btn' style='text-decoration:none' "
        f"href='/tfile/{iid}/{esc(n)}'"
        f"{' download' if n.endswith('.tgz') else ' target=_blank'}>"
        f"{'📦 ' if n.endswith('.tgz') else '📄 '}{esc(n)}</a>"
        for n in names)

    # 被動產生按鈕:有 session_id 才有東西可渲染(哨值 workspace 已擋在上面)
    gen_btn = ""
    if s.get("session_id"):
        label = "🔄 重新產生" if names else "📄 產生 transcript"
        gen_btn = (
            "<span class='btn' "
            "title='對此票當前 session 立即產生 transcript HTML(定格)。"
            "進行中/等待人類/已完成皆可;會覆蓋既有產物並更新產生時間與原因為"
            "「手動」。' "
            f"onclick=\"this.textContent='產生中…';"
            f"fetch('{CONTROL}/gen_transcript/{iid}',{{method:'POST'}})"
            ".then(r=>r.json()).then(j=>{this.textContent="
            "j.generated?'已產生('+j.files+' 檔),重整中…':'失敗:'+(j.error||'?');"
            "if(j.generated)setTimeout(()=>location.reload(),800);})"
            f".catch(()=>this.textContent='control 離線')\">{label}</span>")

    return (f"<h2>Transcript(可視化 / 下載)</h2>"
            f"<div class='card'><div class='row'>{info}</div>"
            f"<div class='ctl'>{links}{gen_btn}</div></div>")


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
            + (f"<span class='kv'><b>驅逐次數</b> {s.get('evict_count', 0)}</span>"
               if s.get("evict_count") else "")
            + (("<span class='btn' style='margin-left:auto;color:#f2a8a8' "
                "title='強制驅逐:agent 卡住不動或要立即讓出 CPU/記憶體時按。"
                "會 killpg 殺掉此票的 agent 進程組;session 保留,下一輪 poll "
                "自動 native resume 續跑、不重花錢。屬異常處理,發生次數會記錄。' "
                f"onclick=\"if(this.dataset.a!=='1'){{this.dataset.a='1';"
                "this.textContent='⚠ 再按一次確認驅逐';"
                "setTimeout(()=>{this.dataset.a='0';"
                "this.textContent='⏻ 強制驅逐(killpg)';},3000);return;}}"
                f"fetch('{CONTROL}/evict/{iid}',{{method:'POST'}})"
                ".then(r=>r.json()).then(j=>this.textContent="
                "'已驅逐:'+JSON.stringify(j)).catch(()=>this.textContent="
                "'control 離線')\""
                ">⏻ 強制驅逐(killpg)</span>")
               if s and not s.get("outcome")
               and not str(s.get("workspace", "")).startswith("(") else "")
            + f"</div></div>"
            f"{render_transcript_card(iid, s)}"
            f"{render_approval(s, evs)}"
            f"<div class='tabs'>"
            f"<div class='tab on' id='tab-convo' onclick='tab(\"convo\")'>💬 Conversation</div>"
            f"<div class='tab' id='tab-trace' onclick='tab(\"trace\")'>🔍 Trace (L0-L3)</div>"
            f"</div>"
            f"<div class='pane on' id='pane-convo'>{convo_view}</div>"
            f"<div class='pane' id='pane-trace'>{trace_view}</div>"
            f"{tabs_js}</main>")


# ── W6.5:REST API 文件(vendored Swagger UI,離線可用)────────────────────── #
def openapi_spec() -> dict:
    """手寫 OpenAPI 3.1 規格。涵蓋兩個 server:
      - dashboard(唯讀觀測,本頁同源 `/`):/data /server/data /db/* /tfile
      - control-plane(寫入 ⚠️,另一 port CONTROL):/pause /evict /gen_transcript…
    寫入端點以 tag『control-plane ⚠️』標示,並用 operation-level `servers`
    指向 CONTROL,讓 Swagger UI『Try it out』打到正確 host。"""
    ctl = [{"url": CONTROL, "description": "control API(寫入;預設只綁 127.0.0.1)"}]

    def w(summary, desc="", params=None, req=None):
        """寫入端點模板(⚠️ + operation-level control server)。"""
        op = {"tags": ["control-plane ⚠️(寫入)"], "servers": ctl,
              "summary": "⚠️ " + summary, "description": desc,
              "responses": {"200": {"description": "OK",
                                    "content": {"application/json": {}}}}}
        if params:
            op["parameters"] = params
        if req:
            op["responses"]["404"] = {"description": "無此 session / 終態 / 哨值"}
            op["responses"]["400"] = {"description": "issue id 非數字"}
        return op

    iid_param = [{"name": "issue_id", "in": "path", "required": True,
                  "schema": {"type": "integer"},
                  "description": "Jira issue 的數字 id"}]
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "ARCP Harness API",
            "version": "W6.5",
            "description": (
                "Jira 事件驅動 headless coding-agent harness 的 REST 介面。\n\n"
                "**兩個 server**:\n"
                "- *dashboard*(本頁同源):唯讀觀測資料。\n"
                "- *control-plane*(另一 port,預設 `127.0.0.1:8787`):寫入/控制,"
                "端點以 ⚠️ 標示。\n\n"
                "寫入端點會改變 poller 狀態或殺進程,請確認再『Try it out』。"),
        },
        "servers": [{"url": "/", "description": "dashboard(唯讀觀測,本頁同源)"}],
        "tags": [
            {"name": "observability(唯讀)", "description": "儀表板/Server 頁資料源"},
            {"name": "db(唯讀)", "description": "SQLite 瀏覽器(僅 SELECT)"},
            {"name": "artifacts", "description": "transcript 產物與規格"},
            {"name": "control-plane ⚠️(寫入)",
             "description": "poller 控制面(pause/resume/reload/shutdown/evict/"
                            "gen_transcript);打到 control API host。"},
        ],
        "paths": {
            "/data": {"get": {
                "tags": ["observability(唯讀)"],
                "summary": "儀表板單一資料源(所有 ticket session + 彙總)",
                "responses": {"200": {"description": "rows/彙總",
                                      "content": {"application/json": {}}}}}},
            "/server/data": {"get": {
                "tags": ["observability(唯讀)"],
                "summary": "Server 頁資料源(系統/版本/登入/連線/程序/workspace/evict)",
                "responses": {"200": {"description": "sys/conns/processes/"
                                      "workspaces/evict",
                                      "content": {"application/json": {}}}}}},
            "/db/tables": {"get": {
                "tags": ["db(唯讀)"], "summary": "SQLite 資料表清單 + 列數",
                "responses": {"200": {"description": "tables",
                                      "content": {"application/json": {}}}}}},
            "/db/table/{name}": {"get": {
                "tags": ["db(唯讀)"], "summary": "分頁讀取一張表",
                "parameters": [
                    {"name": "name", "in": "path", "required": True,
                     "schema": {"type": "string"}},
                    {"name": "limit", "in": "query",
                     "schema": {"type": "integer", "default": 100}},
                    {"name": "offset", "in": "query",
                     "schema": {"type": "integer", "default": 0}}],
                "responses": {"200": {"description": "cols/rows",
                                      "content": {"application/json": {}}}}}},
            "/db/query": {"post": {
                "tags": ["db(唯讀)"],
                "summary": "唯讀 SQL 查詢(僅允許 SELECT)",
                "requestBody": {"content": {"application/json": {"schema": {
                    "type": "object",
                    "properties": {"sql": {"type": "string"}}}}}},
                "responses": {"200": {"description": "cols/rows 或 error",
                                      "content": {"application/json": {}}}}}},
            "/tfile/{issue_id}/{name}": {"get": {
                "tags": ["artifacts"],
                "summary": "transcript 產物(HTML 檢視 / tgz 下載 / log)",
                "parameters": iid_param + [
                    {"name": "name", "in": "path", "required": True,
                     "schema": {"type": "string"},
                     "description": "final.html / transcript.tgz / *.log"}],
                "responses": {"200": {"description": "檔案內容"},
                              "404": {"description": "無此產物"}}}},
            "/openapi.json": {"get": {
                "tags": ["artifacts"], "summary": "本 OpenAPI 規格(JSON)",
                "responses": {"200": {"description": "spec",
                                      "content": {"application/json": {}}}}}},
            "/health": {"get": {
                "tags": ["control-plane ⚠️(寫入)"], "servers": ctl,
                "summary": "control API 健康檢查", "responses": {"200": {
                    "description": "{ok:true}",
                    "content": {"application/json": {}}}}}},
            "/status": {"get": {
                "tags": ["control-plane ⚠️(寫入)"], "servers": ctl,
                "summary": "poller 狀態彙總(paused/in_flight/queued/cost…)",
                "responses": {"200": {"description": "狀態",
                                      "content": {"application/json": {}}}}}},
            "/pause": {"post": w(
                "暫停派工(graceful:只 watch,不派新工,不中斷正在跑的)")},
            "/resume": {"post": w("恢復派工")},
            "/reload": {"post": w(
                "熱重載 routes.yaml(壞 config 回 400、舊設定續用、不弄死 poller)")},
            "/shutdown": {"post": w(
                "優雅關閉(當前 poll 輪跑完後退出並清理)")},
            "/evict/{issue_id}": {"post": w(
                "強制驅逐(killpg):殺此票 agent 進程組,不耗 attempt,下輪 resume",
                desc="agent 卡住或要立即讓出 CPU/記憶體時用。屬異常處置,"
                     "發生次數會記錄於 session.evict_count。",
                params=iid_param, req=True)},
            "/gen_transcript/{issue_id}": {"post": w(
                "被動產生 transcript(定格 final HTML,reason=manual)",
                desc="進行中/等待人類/已完成皆可;哨值 workspace 或無 session_id → 404。",
                params=iid_param, req=True)},
        },
    }


def render_docs_page() -> str:
    """W6.5:Swagger UI 載入頁(全本地資產:/swagger-assets/* + /openapi.json)。"""
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>ARCP API — Swagger UI</title>"
        "<link rel='stylesheet' href='/swagger-assets/swagger-ui.css'>"
        "<style>body{margin:0}.topbar{display:none}</style></head><body>"
        "<div id='swagger-ui'></div>"
        "<script src='/swagger-assets/swagger-ui-bundle.js'></script>"
        "<script>window.onload=function(){window.ui=SwaggerUIBundle({"
        "url:'/openapi.json',dom_id:'#swagger-ui',deepLinking:true,"
        "presets:[SwaggerUIBundle.presets.apis],layout:'BaseLayout',"
        "tryItOutEnabled:true});};</script></body></html>")


_SWAGGER_CT = {".css": "text/css", ".js": "application/javascript",
               ".txt": "text/plain"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send_json(self, obj) -> None:
        payload = json.dumps(obj, ensure_ascii=False,
                             default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        if self.path == "/db/query":               # W5.6 唯讀查詢
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except (ValueError, TypeError):
                body = {}
            self._send_json(db_query(body.get("sql", "")))
            return
        self.send_response(404)
        self.end_headers()

    def _log_conn(self) -> None:
        """W6.6:記連線 client IP + path + 時間(環形緩衝,排除資料輪詢雜訊)。"""
        if self.path in ("/data", "/server/data") or \
                self.path.startswith(("/tvendor/", "/swagger-assets/")):
            return                              # 高頻輪詢/資產不記,免洗掉 history
        import datetime
        _CONNS.append({"t": datetime.datetime.now().strftime("%m-%d %H:%M:%S"),
                       "ip": self.client_address[0], "path": self.path})

    def do_GET(self):
        self._log_conn()                        # W6.6
        journal, sessions = read_journal(), read_sessions()
        if self.path == "/data":                   # W4.7 前端單一資料源
            self._send_json(build_data(journal, sessions, read_watch()))
            return
        if self.path == "/server/data":            # W6.1 Server 頁資料源
            self._send_json(build_server_data())
            return
        if self.path == "/openapi.json":           # W6.5 REST API 規格
            self._send_json(openapi_spec())
            return
        if self.path == "/docs":                   # W6.5 Swagger UI(本地資產)
            page = render_docs_page()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Security-Policy", _CSP_DOCS)
            self.end_headers()
            self.wfile.write(page.encode())
            return
        if self.path.startswith("/swagger-assets/"):  # W6.5 vendored Swagger UI
            try:
                name = os.path.basename(self.path)     # 防 traversal
                p = os.path.join(_SWAGGER_DIR, name)
                if not os.path.isfile(p):
                    raise FileNotFoundError(p)
                data = open(p, "rb").read()
                _, ext = os.path.splitext(name)
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    f"{_SWAGGER_CT.get(ext, 'application/octet-stream')}"
                    "; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "max-age=86400")
                self.end_headers()
                self.wfile.write(data)
            except Exception:
                self.send_response(404)
                self.end_headers()
            return
        if self.path == "/db/tables":              # W5.6 DB 瀏覽器
            self._send_json(db_tables())
            return
        if self.path.startswith("/db/table/"):
            from urllib.parse import parse_qs, urlparse
            u = urlparse(self.path)
            name = u.path.rsplit("/", 1)[1]
            q = parse_qs(u.query)
            self._send_json(db_table(
                name, int((q.get("limit") or ["100"])[0]),
                int((q.get("offset") or ["0"])[0])))
            return
        if self.path in ("/db", "/control", "/server"):  # 獨立頁
            body = (render_db_page() if self.path == "/db"
                    else render_control_page() if self.path == "/control"
                    else render_server_page())
            page = (f"<!doctype html><html><head><meta charset='utf-8'>"
                    f"<title>ARCP</title><style>{CSS}</style></head>"
                    f"<body>{body}</body></html>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Security-Policy", _CSP_MAIN)
            self.end_headers()
            self.wfile.write(page.encode())
            return
        if self.path.startswith("/tvendor/"):      # W5.9 vendored transcript 資產
            try:
                name = os.path.basename(self.path)  # 防 traversal
                p = os.path.join(_VENDOR_DIR, name)
                if not os.path.isfile(p):
                    raise FileNotFoundError(p)
                data = open(p, "rb").read()
                ct = ("text/css" if name.endswith(".css")
                      else "application/javascript" if name.endswith(".js")
                      else "application/octet-stream")
                self.send_response(200)
                self.send_header("Content-Type", f"{ct}; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "max-age=86400")
                self.end_headers()
                self.wfile.write(data)
            except Exception:
                self.send_response(404)
                self.end_headers()
            return
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
                    # 硬擋外部載入(cclog HTML 曾含 unpkg CDN;內網不可外連)
                    self.send_header("Content-Security-Policy",
                                     _CSP_TRANSCRIPT)
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
        self.send_header("Content-Security-Policy", _CSP_MAIN)
        self.end_headers()
        self.wfile.write(page.encode())


if __name__ == "__main__":
    where = "所有介面(內網開放)" if HOST == "0.0.0.0" else HOST
    print(f"[detail] serving {ROOT} on {HOST}:{PORT} — {where}", flush=True)
    if HOST == "0.0.0.0":
        print("[detail] ⚠️ 內網開放:dashboard 唯讀但會顯示系統/程序資訊;"
              "control API(寫入端點)風險見 /docs。鎖本機:"
              "ARCP_DASH_HOST=127.0.0.1", flush=True)
    HTTPServer((HOST, PORT), Handler).serve_forever()
