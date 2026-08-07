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
import re
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
# W7.5:Agent Detail 讀 routes.yaml(harness 設定 + profiles)。憑證在 ~/.env 不在此。
_CONFIG_PATH = os.environ.get("ARCP_CONFIG", "routes.yaml")


def _instance_name() -> str:
    """W8.6:此 Control Plane 實例名(routes.yaml source.name;多實例分辨用)。
    輕量讀取、壞掉不擋站。ARCP_NAME 環境變數可覆寫。"""
    env = os.environ.get("ARCP_NAME")
    if env:
        return env
    try:
        import yaml
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
        return str((((doc.get("outer_loop") or {}).get("source") or {})
                    .get("name")) or "")
    except Exception:  # noqa: BLE001
        return ""


_INSTANCE_NAME = _instance_name()
_TITLE_TAIL = (" · " + _INSTANCE_NAME) if _INSTANCE_NAME else ""
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
            # W7(R1):人類完成度評分 0-10(None=未評分);pct=score×10
            "score": s.get("human_score"),
            "state": canonical_state(s or None),   # W7(R4):8 態 key(per-profile 圖)
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
/* W8.1 暖色編輯風(claude.com/blog)+ 明暗雙主題。token-based:light 為預設,
   dark 由 prefers-color-scheme + [data-theme] 覆寫;元件一律走 var() 不直接寫色。 */
:root{
  color-scheme:light;
  --bg:#F1EEE6;--panel:#FBFAF5;--panel-2:#F6F3EA;--raise:#FFFFFF;
  --line:#E4E0D4;--line-2:#D7D2C2;
  --ink:#191712;--ink-dim:#3C382F;--muted:#6E695C;--faint:#9C968A;
  --accent:#B4552F;--accent-ink:#8F4222;--accent-soft:rgba(180,85,47,.10);
  --s-success:#3F8F52;--s-failure:#C0432E;--s-pending:#A9761B;
  --s-running:#3C6FB0;--s-queued:#7A50C0;--s-inactive:#8B857A;
  --s-todo:#9A948A;--s-aborted:#7C766B;
  --shadow:0 1px 2px rgba(25,23,18,.04),0 8px 24px -14px rgba(25,23,18,.14);
  --font-display:ui-serif,"Tiempos Text",Georgia,"Iowan Old Style","Palatino Linotype",serif;
  --font-body:ui-sans-serif,system-ui,-apple-system,"Segoe UI","Helvetica Neue",sans-serif;
  --font-mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,monospace;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  color-scheme:dark;
  --bg:#1E1C19;--panel:#262420;--panel-2:#2B2925;--raise:#2E2C27;
  --line:#37342D;--line-2:#423E36;
  --ink:#EEE8DB;--ink-dim:#D0CABB;--muted:#A39C8C;--faint:#746E61;
  --accent:#D98A61;--accent-ink:#E8A17C;--accent-soft:rgba(217,138,97,.15);
  --s-success:#6BBE7C;--s-failure:#E57A6B;--s-pending:#DBA23E;
  --s-running:#6BA4E6;--s-queued:#B189E6;--s-inactive:#928B7B;
  --s-todo:#9C9689;--s-aborted:#847D6F;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 12px 30px -16px rgba(0,0,0,.6);
}}
:root[data-theme="dark"]{
  color-scheme:dark;
  --bg:#1E1C19;--panel:#262420;--panel-2:#2B2925;--raise:#2E2C27;
  --line:#37342D;--line-2:#423E36;
  --ink:#EEE8DB;--ink-dim:#D0CABB;--muted:#A39C8C;--faint:#746E61;
  --accent:#D98A61;--accent-ink:#E8A17C;--accent-soft:rgba(217,138,97,.15);
  --s-success:#6BBE7C;--s-failure:#E57A6B;--s-pending:#DBA23E;
  --s-running:#6BA4E6;--s-queued:#B189E6;--s-inactive:#928B7B;
  --s-todo:#9C9689;--s-aborted:#847D6F;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 12px 30px -16px rgba(0,0,0,.6);
}
body{font:14px/1.55 var(--font-body);margin:0;background:var(--bg);color:var(--ink);
  -webkit-font-smoothing:antialiased;transition:background .3s,color .3s}
a{color:var(--accent-ink);text-decoration:none}a:hover{text-decoration:underline}
main{padding:0 26px 56px;max-width:1160px;margin:0 auto}
header{padding:22px 26px 4px;max-width:1160px;margin:0 auto}
h1{margin:0;font-family:var(--font-display);font-size:23px;font-weight:600;
  letter-spacing:-.01em;text-wrap:balance}
h1 a{color:var(--muted)}
h2{color:var(--ink);font-family:var(--font-display);font-size:18px;font-weight:600;
  margin:32px 0 12px;letter-spacing:-.01em;text-wrap:balance}
/* W8.5 觸控:去點兩下縮放延遲 + 自訂點按高亮 */
button,a,.sortable,input,select{touch-action:manipulation;
  -webkit-tap-highlight-color:transparent}
/* W8.4 skip link(平時隱藏,聚焦才現) */
.skip{position:absolute;left:-9999px;top:8px;z-index:20;background:var(--accent);
  color:#fff;padding:8px 14px;border-radius:7px;text-decoration:none}
.skip:focus{left:8px}
.sys{color:var(--muted);font-size:11.5px;text-align:center;margin:8px 0}
code{font-family:var(--font-mono);font-size:.92em;background:var(--panel-2);
  border:1px solid var(--line);border-radius:5px;padding:1px 5px}
/* 命令列(nav) */
.cmdbar{display:flex;align-items:center;gap:20px;height:58px;padding:0 26px;
  border-bottom:1px solid var(--line);background:var(--bg);position:sticky;top:0;z-index:6}
.cmdbar .brand{font-family:var(--font-display);font-size:20px;font-weight:600;
  color:var(--ink);letter-spacing:-.01em}
.cmdbar .brand .sub{font-family:var(--font-body);font-size:10px;letter-spacing:.16em;
  text-transform:uppercase;color:var(--muted);margin-left:8px}
.cmdbar .brand .iname{font-family:var(--font-mono);font-size:12px;
  color:var(--accent);background:var(--accent-soft);
  border:1px solid color-mix(in srgb,var(--accent) 30%,transparent);
  border-radius:20px;padding:2px 10px;margin-left:10px;letter-spacing:0}
.cmdbar .live{display:flex;align-items:center;gap:6px;font-size:10px;letter-spacing:.14em;
  text-transform:uppercase;color:var(--accent)}
.cmdbar .live i{width:7px;height:7px;border-radius:50%;background:var(--accent);
  animation:pulse 2.4s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 var(--accent-soft)}
  70%{box-shadow:0 0 0 8px transparent}100%{box-shadow:0 0 0 0 transparent}}
.navtabs{display:flex;gap:1px;flex-wrap:wrap}
.navtabs a{position:relative;padding:8px 12px;border-radius:6px;color:var(--muted);
  font-size:13.5px}
.navtabs a:hover{color:var(--ink-dim);background:var(--panel-2);text-decoration:none}
.navtabs a.on{color:var(--ink);font-weight:500}
.navtabs a.on::after{content:"";position:absolute;left:12px;right:12px;bottom:-1px;
  height:2px;border-radius:2px;background:var(--accent)}
.tgl{margin-left:auto;display:inline-flex;align-items:center;gap:7px;cursor:pointer;
  border:1px solid var(--line-2);background:var(--panel);color:var(--ink-dim);
  border-radius:20px;padding:6px 13px;font-size:12.5px;font-family:var(--font-body)}
.tgl:hover{border-color:var(--accent);color:var(--accent)}
/* 卡片 */
.card{background:var(--panel);border:1px solid var(--line);border-radius:9px;
  padding:14px 18px;margin:10px 0;box-shadow:var(--shadow)}
.row{display:flex;gap:16px;flex-wrap:wrap}.kv{margin:2px 12px 2px 0}
.kv b{color:var(--muted);font-weight:500}
/* 狀態 pill */
.badge{display:inline-flex;align-items:center;gap:6px;padding:2px 10px 2px 8px;
  border-radius:20px;font-size:11.5px;font-weight:600;border:1px solid var(--line-2);
  color:var(--muted);background:var(--panel-2)}
.badge::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor}
.SUCCESS{color:var(--s-success);background:color-mix(in srgb,var(--s-success) 12%,transparent);border-color:color-mix(in srgb,var(--s-success) 30%,transparent)}
.FAILURE,.UNKNOWN,.ABORTED{color:var(--s-failure);background:color-mix(in srgb,var(--s-failure) 12%,transparent);border-color:color-mix(in srgb,var(--s-failure) 30%,transparent)}
.pending{color:var(--s-pending);background:color-mix(in srgb,var(--s-pending) 14%,transparent);border-color:color-mix(in srgb,var(--s-pending) 32%,transparent)}
.queued{color:var(--s-queued);background:color-mix(in srgb,var(--s-queued) 12%,transparent);border-color:color-mix(in srgb,var(--s-queued) 30%,transparent)}
.inactive{color:var(--s-inactive);background:color-mix(in srgb,var(--s-inactive) 14%,transparent);border-color:color-mix(in srgb,var(--s-inactive) 30%,transparent)}
.running{color:var(--s-running);background:color-mix(in srgb,var(--s-running) 12%,transparent);border-color:color-mix(in srgb,var(--s-running) 30%,transparent)}
/* KPI */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(128px,1fr));gap:12px;margin:14px 0}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:9px;
  padding:14px 16px;text-align:left;box-shadow:var(--shadow)}
.stat .n{font-family:var(--font-display);font-size:25px;font-weight:600;color:var(--ink);
  letter-spacing:-.02em;font-variant-numeric:tabular-nums;line-height:1.15}
.stat .l{font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-top:6px}
/* 按鈕 / 分頁籤 */
.ctl{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.btn{padding:6px 14px;border-radius:7px;background:var(--panel);cursor:pointer;
  border:1px solid var(--line-2);user-select:none;color:var(--ink-dim);
  font-size:13px;font-family:var(--font-body)}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.tabs{display:flex;gap:6px;margin:18px 0 10px}
.tab{padding:6px 14px;border-radius:7px;background:var(--panel);border:1px solid var(--line);
  cursor:pointer;user-select:none;color:var(--muted);font:inherit;font-size:14px}
.tab.on{background:var(--accent);color:#fff;border-color:var(--accent)}
.pane{display:none}.pane.on{display:block}
/* trace */
.ev{font-family:var(--font-mono);font-size:12px;padding:3px 0;border-bottom:1px solid var(--line)}
.ev .t{color:var(--muted)}.ev .k{color:var(--accent-ink)}
.layer{border-left:3px solid var(--line-2);padding-left:12px}
.L0{border-color:var(--s-queued)}.L1{border-color:var(--s-running)}
.L2{border-color:var(--s-success)}.L3{border-color:var(--s-pending)}
/* 表格 */
table{border-collapse:collapse;width:100%;font-size:12.5px}
td{padding:8px 12px;border-bottom:1px solid var(--line);vertical-align:top}
thead td,thead th{color:var(--muted);font-size:10.5px;letter-spacing:.07em;
  text-transform:uppercase;font-weight:600;border-bottom:1px solid var(--line-2)}
tbody tr:hover{background:var(--accent-soft)}
table.resiz{table-layout:fixed}
table.resiz td{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rz{position:absolute;top:0;right:0;width:7px;height:100%;cursor:col-resize;user-select:none}
.rz:hover{background:var(--accent)}
/* conversation */
.msg{margin:8px 0;display:flex}.msg.user{justify-content:flex-end}
.bubble{max-width:80%;padding:9px 13px;border-radius:13px;white-space:pre-wrap;word-break:break-word}
.msg.user .bubble{background:var(--accent);color:#fff;border-bottom-right-radius:3px}
.msg.agent .bubble{background:var(--panel-2);border:1px solid var(--line);border-bottom-left-radius:3px}
.tool{margin:6px 0 6px 20px;border-left:2px solid var(--s-pending);padding:5px 11px;
  background:var(--panel-2);border-radius:0 7px 7px 0;font-size:12px}
.tool .ti{color:var(--s-pending);font-weight:600}.tool .st{float:right;font-size:11px;color:var(--muted)}
.tool .io{font-family:var(--font-mono);color:var(--muted);margin-top:2px}
.think{margin:6px 0 6px 20px;color:var(--muted);font-style:italic;font-size:12px}
/* 事件時間軸(vis-timeline;token 化,兩主題適用) */
.tlsec{padding:8px 0 24px}.tlsec h2{margin:8px 0}.tlsec .sys{text-align:left}
#evtl{background:var(--panel);border:1px solid var(--line);border-radius:9px}
.vis-timeline{border-color:var(--line)!important}
.vis-item{border-radius:6px;color:#fff;font-size:12px;border:none}
.vis-item.tl-in{background:var(--s-running)}.vis-item.tl-jira{background:var(--s-pending)}
.vis-item.tl-life{background:var(--s-queued)}.vis-item.tl-run{background:var(--s-success)}
.vis-item .vis-item-content{padding:3px 8px}
.vis-labelset .vis-label,.vis-time-axis .vis-text{color:var(--muted)}
.vis-panel,.vis-grid.vis-vertical,.vis-time-axis .vis-grid.vis-minor{border-color:var(--line)!important}
.vis-grid.vis-minor{border-color:var(--line)!important}
.vis-current-time{background:var(--s-failure)}
.vis-item.l3-user{background:var(--s-running);color:#fff}
.vis-item.l3-agent{background:var(--s-success);color:#fff}
/* W9.2 時間軸浮動鈕 + 抽屜(仿 transcript;右下角開/關) */
#tlfab{position:fixed;right:18px;bottom:18px;z-index:30;background:var(--accent);
  color:#fff;border:none;border-radius:24px;padding:11px 18px;cursor:pointer;
  box-shadow:var(--shadow);font-family:var(--font-body);font-size:13px;font-weight:500}
#tlfab:hover{filter:brightness(1.08)}
#tlwrap{display:none;position:fixed;left:16px;right:16px;bottom:68px;z-index:29;
  max-height:72vh;overflow:auto;background:var(--panel);border:1px solid var(--line);
  border-radius:12px;box-shadow:var(--shadow);padding:14px 18px}
#tlwrap.on{display:block}
#tlwrap #evtl,#tlwrap #l3tl{background:var(--bg);border:1px solid var(--line);
  border-radius:8px}
/* W8.2 狀態機 SVG(用 CSS 上色,隨明暗主題;--nc=節點色) */
#smsvg .sm-box{fill:var(--panel);stroke:var(--nc,var(--line-2));stroke-width:1.6}
#smsvg .sm-lbl{fill:var(--nc,var(--ink));font-weight:600}
#smsvg .sm-edge{stroke:var(--muted)}
#smsvg .sm-elabel{fill:var(--muted)}
#smsvg .sm-arrow{fill:var(--muted)}
#smsvg .st-todo{--nc:var(--s-todo)}#smsvg .st-running{--nc:var(--s-running)}
#smsvg .st-queued{--nc:var(--s-queued)}#smsvg .st-pending{--nc:var(--s-pending)}
#smsvg .st-inactive{--nc:var(--s-inactive)}#smsvg .st-success{--nc:var(--s-success)}
#smsvg .st-failure{--nc:var(--s-failure)}#smsvg .st-aborted{--nc:var(--s-aborted)}
#smsvg .st-exit{--nc:var(--accent)}
/* W8.3 可及性:全域可見焦點環(勿只靠 hover;鍵盤使用者需要) */
a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,
textarea:focus-visible,[tabindex]:focus-visible,.sortable:focus-visible{
  outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}
button{font-family:var(--font-body)}   /* 按鈕預設不繼承字型,補上 */
@media (prefers-reduced-motion:reduce){
  .cmdbar .live i{animation:none}
  *{transition-duration:0.01ms!important}
}
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
                out += (f"<div class='tool' style='border-color:var(--s-success)'>"
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
                    f"<span style='color:var(--faint)'> · {esc(e.get('tool_kind',''))}"
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


def canonical_state(s: dict | None) -> str:
    """W7(R4/R6):把 (outcome, pending_reason, queued, inactive, 有無 session)
    收斂成單一 8 態 key(dashboard per-profile 圖 + R6 狀態機共用)。
    優先序:終態(成功/失敗/撤銷)> UNKNOWN/pending(等待人類)> 排隊 > 交人 > 進行中。
    無 session = 待處理。"""
    if not s:
        return "todo"
    oc = s.get("outcome")
    if oc == "SUCCESS":
        return "success"
    if oc == "FAILURE":
        return "failure"
    if oc == "ABORTED":
        return "aborted"
    if oc == "UNKNOWN" or s.get("pending_reason"):
        return "pending"                 # 等待人類
    if s.get("queued"):
        return "queued"
    if s.get("inactive"):
        return "inactive"                # 交人
    return "running"                     # 進行中


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
    "const up=j.started_at?(Date.now()/1000-j.started_at):0;"
    "const hh=Math.floor(up/3600),mm=Math.floor(up%3600/60);"
    "const upTxt=up?(hh+'h'+String(mm).padStart(2,'0')+'m'):'—';"
    "const t=[[j.paused?'⏸ 暫停':(j.stopping?'⏻ 關閉中':'▶ 運行'),'狀態'],"
    "[(j.poll_count||0),'已 poll 次數'],[upTxt,'連續運行'],"
    "[(j.poll_interval?j.poll_interval+'s':'—'),'poll 間隔'],"
    "[j.in_flight,'in-flight'],[j.queued,'queued'],[j.inactive,'inactive'],"
    "[(j.pending?Object.values(j.pending).reduce((a,b)=>a+b,0):0),'pending'],"
    "['$'+(j.cost_usd||0).toFixed(4),'總 cost'],[j.sessions,'sessions']];"
    "$c('cstatus').innerHTML=t.map(x=>`<div class='stat'><div class='n'>`+"
    "`${x[0]}</div><div class='l'>${x[1]}</div></div>`).join('');"
    "}catch(e){$c('cstatus').innerHTML=\"<span style='color:var(--s-failure)'>"
    "control API 離線(\"+CTL+\")——poller 未啟動?</span>\";}}"
    "poll();setInterval(poll,3000);"
    "</script>")


def render_control_page() -> str:
    """W5.8 Control 獨立頁:poller 全域控制(Pause/Resume/Reload/Shutdown)+
    即時狀態。作用於正在跑的 poller 進程(REST /8787,W2.6/W4.5)。"""
    return (f"{_nav('control')}"
            "<header><h1>Control · poller</h1></header><main id='main' tabindex='-1'>"
            "<div class='stats' id='cstatus' aria-live='polite'>載入中…</div>"
            "<div class='ctl card' style='margin-top:12px'>"
            "<button type='button' class='btn' onclick=\"cc('pause')\">⏸ Pause</button>"
            "<button type='button' class='btn' onclick=\"cc('resume')\">▶ Resume</button>"
            "<button type='button' class='btn' onclick=\"cc('reload')\">🔄 Reload</button>"
            "<button type='button' class='btn' id='sd' style='color:var(--s-failure)' "
            "onclick=\"cc('shutdown')\">⏻ Graceful Shutdown</div>"
            "<span id='cmsg' aria-live='polite' style='color:var(--muted);font-size:12px'></span>"
            "</div>"
            "<p style='color:var(--muted);font-size:12px'>"
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
    "catch(e){$s('sroot').innerHTML=\"<p style='color:var(--s-failure)'>載入失敗</p>\";"
    "return;}"
    "const sy=d.sys||{};const v=sy.versions||{},a=sy.auth||{},"
    "r=sy.resources||{},m=r.mem||{},dk=r.disk||{};"
    "const badge=b=>b?\"<span style='color:var(--s-success)'>✓</span>\":"
    "\"<span style='color:var(--s-failure)'>✗</span>\";"
    "let h='';"
    # 強制驅逐統計(W6.3)
    "const ev=d.evict||{total:0,by_ticket:[]};"
    "if(ev.total)h+=\"<div class='card' style='border-color:var(--s-pending)'>\"+"
    "`<b style='color:var(--s-pending)'>⚠ 強制驅逐(異常處理)</b> 總計 ${ev.total} 次`+"
    "ev.by_ticket.map(t=>`<div>• ${esc(t[0])}: ${t[1]} 次</div>`).join('')"
    "+'</div>';"
    # 系統異常
    "if((sy.anomalies||[]).length)h+=\"<div class='card' style='border-color:"
    "var(--s-failure)'><b style='color:var(--s-failure)'>⚠ 異常</b>\"+sy.anomalies.map("
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
    "h+=\"<h2>登入 / 認證方式(只顯示帳號類別,不顯示金鑰/email)</h2>\"+"
    "\"<div class='card'>\"+"
    "`<div class='kv'><b>claude</b> ${badge(a.claude_configured)} `+"
    "`<span class='mono' style='color:var(--accent-ink)'>`+"
    "`${esc(a.claude_method||'?')}</span></div>`+"
    "`<div class='kv'><b>codex</b> ${badge(a.codex_logged_in)} `+"
    "`<span class='mono' style='color:var(--accent-ink)'>`+"
    "`${esc(a.codex_method||'?')}</span></div>`+"
    "`<div class='kv' style='color:var(--faint);font-size:11px'>`+"
    "`OAuth=帳號登入(claude_max=個人 Max、enterprise=企業);`+"
    "`ChatGPT 帳號=codex 訂閱登入;API key=用金鑰</div>`+'</div>';"
    # per-process(W6.2)
    "const ps=d.processes||[];"
    "h+=\"<h2>Agent 進程(claude/codex)</h2><div class='card'>\"+(ps.length?"
    "\"<table id='tix'><thead><tr><td><b>engine</b></td><td><b>Jira</b></td>\"+"
    "\"<td><b>PID</b></td><td><b>CPU%</b></td><td><b>MEM</b></td>\"+"
    "\"<td><b>cwd</b></td></tr></thead><tbody>\"+ps.map(p=>`<tr><td>`+"
    "`${esc(p.engine)}</td><td>${esc(p.ticket||'-')}</td><td>${esc(p.pid)}`+"
    "`</td><td>${p.cpu}</td><td>${p.rss_mb}MB</td><td>${esc(p.cwd||'-')}`+"
    "`</td></tr>`).join('')+'</tbody></table>':"
    "\"<span style='color:var(--muted)'>(目前無 claude/codex 進程在跑)</span>\")"
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
    "\"<span style='color:var(--muted)'>(目前無進行中 workspace)</span>\")+'</div>';"
    # 連線(W6.6)
    "const cs=d.conns||[];"
    "h+=\"<h2>連線(近期)</h2><div class='card'>\"+(cs.length?"
    "\"<table id='tix'><thead><tr><td><b>時間</b></td><td><b>IP</b></td>\"+"
    "\"<td><b>path</b></td></tr></thead><tbody>\"+cs.map(c=>`<tr><td>`+"
    "`${esc(c.t)}</td><td>${esc(c.ip)}</td><td>${esc(c.path)}</td></tr>`)"
    ".join('')+'</tbody></table>':"
    "\"<span style='color:var(--muted)'>(尚無記錄)</span>\")+'</div>';"
    "$s('sroot').innerHTML=h;}"
    "load();setInterval(load,4000);"
    "</script>")


def render_server_page() -> str:
    """W6.1 Server 頁:系統/版本/登入狀態/資源(+ W6.2 程序、W6.6 連線)。"""
    return (f"{_nav('server')}"
            "<header><h1>Server · 系統與程序</h1></header><main id='main' tabindex='-1'>"
            "<p style='color:var(--muted);font-size:12px'>dashboard 綁 "
            f"{esc(HOST)}(內網開放,唯讀);登入/金鑰只顯示狀態,不顯示值。"
            " <a href='/docs' style='color:var(--accent-ink)'>REST API 文件</a></p>"
            "<div id='sroot'>載入中…</div></main>"
            f"{_SERVER_JS}")


_APP_JS = """
<script>
const LS='arcp-v2';
let D={rows:[],rate_default:null};
let S=Object.assign({qr:'all',from:'',to:'',st:'',ksum:'',kdesc:'',kprofile:'',
  size:20,page:0,sort:'created',dir:-1,wk1:false,wk2:false,rate:null},
  (()=>{try{return JSON.parse(localStorage.getItem(LS))||{}}catch(e){return{}}})());
// W8.5:URL query 反映過濾/排序/分頁狀態(可深連/分享);載入時 URL 優先於 localStorage。
const _URLK=['qr','from','to','st','kprofile','ksum','kdesc','sort','dir',
  'page','size'];
(function(){const q=new URLSearchParams(location.search);
  _URLK.forEach(k=>{if(q.has(k)){const v=q.get(k);
    S[k]=(k==='dir'||k==='page'||k==='size')?(+v):v;}});})();
function save(){localStorage.setItem(LS,JSON.stringify(S));
  const q=new URLSearchParams();
  _URLK.forEach(k=>{const d={qr:'all',sort:'created',dir:-1,page:0,size:20};
    if(S[k]!==''&&S[k]!=null&&S[k]!==d[k])q.set(k,S[k]);});
  const u=location.pathname+(q.toString()?'?'+q:'');
  history.replaceState(null,'',u);}
const $=id=>document.getElementById(id);
// W8.1:圖表色改讀 CSS 變數(隨明暗主題切換);render() 開頭 syncPalette()。
const cssv=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
let C={created:'#3C6FB0',closed:'#7A50C0',success:'#3F8F52',fail:'#C0432E',
  ai:'#A9761B',human:'#3C6FB0',waste:'#C0432E'};
let STATE8=[];
function syncPalette(){
  C={created:cssv('--s-running'),closed:cssv('--s-queued'),
    success:cssv('--s-success'),fail:cssv('--s-failure'),ai:cssv('--s-pending'),
    human:cssv('--s-running'),waste:cssv('--s-failure'),
    txt:cssv('--muted'),grid:cssv('--line'),ink:cssv('--ink')};
  STATE8=[['todo',['待處理',cssv('--s-todo')]],['running',['進行中',cssv('--s-running')]],
    ['queued',['排隊',cssv('--s-queued')]],['pending',['等待人類',cssv('--s-pending')]],
    ['inactive',['交人',cssv('--s-inactive')]],['success',['成功',cssv('--s-success')]],
    ['failure',['失敗',cssv('--s-failure')]],['aborted',['撤銷',cssv('--s-aborted')]]];
}
// ---- 過濾(置頂,統管全部) ----
function filtered(){
  const now=Date.now()/1000;
  let lo=0,hi=Infinity;
  if(S.from){lo=new Date(S.from+'T00:00:00').getTime()/1000;}
  if(S.to){hi=new Date(S.to+'T23:59:59').getTime()/1000;}
  if(!S.from&&!S.to&&S.qr!=='all'){lo=now-(+S.qr)*86400;}
  const ks=S.ksum.toLowerCase(),kd=S.kdesc.toLowerCase();
  const kp=(S.kprofile||'').toLowerCase();
  return D.rows.filter(r=>
    r.created>=lo&&r.created<=hi&&
    (!S.st||r.status===S.st)&&
    (!kp||(r.profile||'').toLowerCase().includes(kp))&&
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
  const t=[[money(cost),'總 cost'],[st('active'),'in-flight'],
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
    s+=`<line x1='${L}' y1='${y}' x2='${W-R}' y2='${y}' stroke='${C.grid}'/>`+
    `<text x='${L-4}' y='${y+4}' fill='${C.txt}' font-size='10' text-anchor='end'>${fmtL(bmax*t/3)}</text>`+
    `<text x='${W-R+4}' y='${y+4}' fill='${C.txt}' font-size='10'>${fmtR(lmax*t/3)}</text>`;}
  bars.forEach((b,bi)=>{b.vals.forEach((v,i)=>{if(!v)return;
    const h=ph*v/bmax,x=gx(i)-gw*bars.length/2+bi*gw;
    s+=`<rect x='${x}' y='${T+ph-h}' width='${gw}' height='${h}' fill='${b.c}' opacity='0.75'/>`;});});
  lines.forEach(l=>{const pts=l.vals.map((v,i)=>gx(i)+','+(T+ph-ph*v/lmax)).join(' ');
    s+=`<polyline points='${pts}' fill='none' stroke='${l.c}' stroke-width='1.8'/>`;});
  const step=Math.ceil(B.n/9);
  for(let i=0;i<B.n;i+=step)
    s+=`<text x='${gx(i)}' y='${H-8}' fill='${C.txt}' font-size='10' text-anchor='middle'>${B.keys[i]}</text>`;
  el.setAttribute('viewBox',`0 0 ${W} ${H}`);el.setAttribute('width',W);
  el.setAttribute('height',H);el.innerHTML=s;
}
function legend(el,items){el.innerHTML=items.map(([c,n,dash])=>
  `<span style='margin-right:14px;font-size:11px;color:var(--muted)'>`+
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
    [C.success,'成功'],[C.fail,'失敗'],['var(--muted)','(條=單期,線=累積)',1]]);
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
    [C.waste,'失敗浪費(累積)',1],['var(--muted)','(條=單期,線=累積)',1]]);
}
// ---- W7.4 per-profile 圖(縱=profile,橫=數量/花費/完成度)----
// 8 態色由 syncPalette() 從 CSS 變數填(見上;隨主題切換)。
function byProfile(rows){const m={};rows.forEach(r=>{
  (m[r.profile||'-']=m[r.profile||'-']||[]).push(r);});
  return Object.keys(m).sort().map(p=>[p,m[p]]);}
// 水平條(每列一 profile;segs=[{c,v,label}] 堆疊或並列)。fmt 標總量。
function drawHBar(el,groups,segsOf,fmt,{stacked=true,minTick=0,labelOf=null}={}){
  if(!groups.length){el.innerHTML='<div class="sys">(無資料)</div>';return;}
  const rowH=26,gap=10,padL=110,padR=54,W=760,padT=6;
  const H=padT+groups.length*(rowH+gap);
  const totals=groups.map(([,rs])=>segsOf(rs).reduce((a,s)=>a+s.v,0));
  const max=Math.max(minTick,...totals)||1;
  const bw=W-padL-padR;let y=padT;const parts=[];
  groups.forEach(([name,rs],gi)=>{
    const segs=segsOf(rs);let x=padL;const tot=totals[gi];
    parts.push(`<text x='${padL-8}' y='${y+rowH/2+4}' text-anchor='end' `+
      `fill='${C.txt}' font-size='11'>${esc(name).slice(0,15)}</text>`);
    if(stacked){segs.forEach(s=>{if(s.v<=0)return;const w=s.v/max*bw;
      parts.push(`<rect x='${x.toFixed(1)}' y='${y}' width='${w.toFixed(1)}' `+
        `height='${rowH}' fill='${s.c}'><title>${esc(name)} · ${esc(s.label)}: `+
        `${s.v}</title></rect>`);x+=w;});
    }else{const n=segs.length,sh=rowH/n;let yy=y;segs.forEach(s=>{
      const w=Math.max(0,s.v)/max*bw;
      parts.push(`<rect x='${padL}' y='${yy}' width='${w.toFixed(1)}' `+
        `height='${sh-1}' fill='${s.c}'><title>${esc(name)} · ${esc(s.label)}: `+
        `${fmt(s.v)}</title></rect>`);yy+=sh;});}
    const lbl=labelOf?labelOf(segs,tot):fmt(tot);
    parts.push(`<text x='${padL+bw+6}' y='${y+rowH/2+4}' fill='${C.ink}' `+
      `font-size='11'>${esc(lbl)}</text>`);
    y+=rowH+gap;});
  el.innerHTML=`<svg viewBox='0 0 ${W} ${H}' width='100%' `+
    `preserveAspectRatio='xMinYMin meet' style='max-height:${H}px'>`+
    parts.join('')+`</svg>`;
}
function renderPState(rows){
  drawHBar($('chart-pstate'),byProfile(rows),
    rs=>STATE8.map(([k,[lb,c]])=>({c,label:lb,
      v:rs.filter(r=>r.state===k).length})),v=>Math.round(v),{minTick:1});
  legend($('lg-pstate'),STATE8.map(([,[lb,c]])=>[c,lb]));
}
function renderPCost(rows){
  const rate=S.rate||0;
  drawHBar($('chart-pcost'),byProfile(rows),rs=>{
    const ai=rs.reduce((a,r)=>a+r.cost,0);
    const hu=rs.reduce((a,r)=>a+r.human_min/60*rate,0);
    return [{c:C.ai,label:'AI 花費',v:ai},{c:C.human,label:'人力$',v:hu}];
  },v=>'$'+v.toFixed(2),{stacked:false,labelOf:segs=>{
    const ai=segs[0].v,hu=segs[1].v;   // 右標=效益(人力−AI)
    return '效益 $'+(hu-ai).toFixed(2);
  }});
  legend($('lg-pcost'),[[C.ai,'AI 花費'],[C.human,'人力$(時薪$'+(rate||'?')+')'],
    ['var(--muted)','右側=效益(人力−AI)',1]]);
}
function renderPScore(rows){
  drawHBar($('chart-pscore'),byProfile(rows),rs=>{
    const sc=rs.filter(r=>r.score!=null);
    const avg=sc.length?sc.reduce((a,r)=>a+r.score,0)/sc.length*10:0;
    return [{c:C.human,label:'平均完成度',v:avg}];
  },v=>Math.round(v)+'%',{stacked:false,minTick:100});
  legend($('lg-pscore'),[[C.human,'平均完成度%(僅計已評分)']]);
}
// ---- 表格(排序 + 分頁) ----
const COLS=[['key','ticket'],['summary','summary'],['profile','profile'],
  ['status','status'],['score','完成度'],['assignee','assignee'],
  ['created','created'],['finished','finished'],['handoff','換手起點'],
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
// W8.4:locale-aware 日期/金額(Intl.*,不硬編格式)
const _DT=new Intl.DateTimeFormat(undefined,{month:'2-digit',day:'2-digit',
  hour:'2-digit',minute:'2-digit',hour12:false});
const _TM=new Intl.DateTimeFormat(undefined,{hour:'2-digit',minute:'2-digit',
  second:'2-digit',hour12:false});
const _MONEY=new Intl.NumberFormat(undefined,{style:'currency',currency:'USD',
  currencyDisplay:'narrowSymbol',minimumFractionDigits:2,maximumFractionDigits:4});
function fmt(ts){return ts?_DT.format(new Date(ts*1000)):'-';}
function money(v){return _MONEY.format(v||0);}
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
  $('thead-row').innerHTML=COLS.map(([c,l])=>{
    const so=S.sort===c?(S.dir>0?'ascending':'descending'):'none';
    return `<td class='sortable' data-col='${c}' tabindex='0' `+
      `role='columnheader' aria-sort='${so}' style='cursor:pointer' `+
      `title='點擊或按 Enter 排序'><b>${l}${
      S.sort===c?(S.dir>0?' ▲':' ▼'):''}</b></td>`;}).join('');
  document.querySelector('#tix tbody').innerHTML=pg.map(r=>
    `<tr><td><a href='/ticket/${r.iid}' translate='no'>${esc(r.key)}</a></td>`+
    `<td title='${esc(r.summary)}'>${esc(r.summary.slice(0,28))}</td>`+
    `<td>${esc(r.profile)}</td>`+
    `<td><span class='badge ${badgeCls(r.status)}'>${esc(r.status)}</span></td>`+
    `<td>${r.score!=null?r.score+'/10':'<span style="color:var(--faint)">未評</span>'}</td>`+
    `<td>${esc(r.assignee||'-')}</td><td>${fmt(r.created)}</td>`+
    `<td>${fmt(r.finished)}</td><td>${fmt(r.handoff)}</td>`+
    `<td>${r.created?fdays(r.dwell):'-'}</td>`+
    `<td>${r.created?fdays(r.lifetime):'-'}</td>`+
    `<td>${r.human_min?'$'+r.human_cost.toFixed(2):'-'}</td>`+
    `<td>${r.attempts}</td><td>${money(r.cost)}</td></tr>`).join('');
  $('pginfo').textContent=rows.length+' 筆 · 第 '+(S.page+1)+'/'+pages+' 頁';
  resizable($('tix'),'tix');          // W5.7 欄寬可拖曳
}
function render(){syncPalette();prep();const rows=filtered();renderStats(rows);var _u=$('upd');if(_u)_u.textContent='更新於 '+_TM.format(new Date());
  renderTime(rows);renderMoney(rows);
  renderPState(rows);renderPCost(rows);renderPScore(rows);   // W7.4 per-profile
  renderTable(rows);save();}
function pg(d){S.page=Math.max(0,S.page+d);render();}
// ---- W5.6 匯出經 filter+sort 的資料(CSV / JSON)----
const EXCOLS=[['key','ticket'],['summary','summary'],['profile','profile'],
  ['status','status'],['score','completion_0_10'],['assignee','assignee'],
  ['created','created'],
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
  $('kprofile').value=S.kprofile||'';
  $('wk1').checked=S.wk1;$('wk2').checked=S.wk2;
  if(S.rate!=null)$('rate').value=S.rate;
  $('qr').onchange=e=>{S.qr=e.target.value;S.page=0;render();};
  $('from').onchange=e=>{S.from=e.target.value;S.page=0;render();};
  $('to').onchange=e=>{S.to=e.target.value;S.page=0;render();};
  $('st').onchange=e=>{S.st=e.target.value;S.page=0;render();};
  $('ksum').oninput=e=>{S.ksum=e.target.value;S.page=0;render();};
  $('kdesc').oninput=e=>{S.kdesc=e.target.value;S.page=0;render();};
  $('kprofile').oninput=e=>{S.kprofile=e.target.value;S.page=0;render();};
  $('psize').onchange=e=>{S.size=+e.target.value;S.page=0;render();};
  $('wk1').onchange=e=>{S.wk1=e.target.checked;render();};
  $('wk2').onchange=e=>{S.wk2=e.target.checked;render();};
  $('rate').oninput=e=>{S.rate=+e.target.value||0;render();};
  const th=document.querySelector('#tix thead');
  const doSort=td=>{if(!td)return;const c=td.dataset.col;
    if(S.sort===c)S.dir=-S.dir;else{S.sort=c;S.dir=1;}render();};
  th.addEventListener('click',e=>doSort(e.target.closest('.sortable')));
  th.addEventListener('keydown',e=>{           // 鍵盤排序(Enter/Space)
    if(e.key!=='Enter'&&e.key!==' ')return;
    const td=e.target.closest('.sortable');if(!td)return;
    e.preventDefault();doSort(td);});
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


_INPUT = ("autocomplete='off' spellcheck='false' "
          "style='background:var(--raise);color:var(--ink);"
          "border:1px solid var(--line-2);border-radius:7px;padding:6px 10px;"
          "font-family:var(--font-body);font-size:13px'")


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


# W8.1:主題切換(套 data-theme + 記 localStorage + 綁 toggle;Dashboard 切換時
# re-render 讓 SVG 圖重讀 CSS 色)。每頁 _nav 都含它,故每次頁載入自動套用。
_THEME_JS = """
<script>
(function(){var r=document.documentElement;
  function apply(t){r.setAttribute('data-theme',t);
    var i=document.getElementById('arcp-tgi'),n=document.getElementById('arcp-tgn');
    if(i)i.textContent=(t==='dark'?'☀':'☾');
    if(n)n.textContent=(t==='dark'?'淺色':'深色');
    try{localStorage.setItem('arcp-theme',t)}catch(e){}
    if(typeof render==='function'){try{render()}catch(e){}}}
  var s;try{s=localStorage.getItem('arcp-theme')}catch(e){}
  apply(s||(matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light'));
  var b=document.getElementById('arcp-tgl');
  if(b)b.addEventListener('click',function(){
    var cur=r.getAttribute('data-theme')||'light';
    apply(cur==='dark'?'light':'dark');});})();
// W9.1:時區本地化——時間一律存 epoch(UTC-based),在此用「瀏覽器時區」顯示
// (台灣=Asia/Taipei=+8)。所有 <span data-ts='<epoch_ms>'> 都會被格式化。
window._TZFMT=new Intl.DateTimeFormat(undefined,{month:'2-digit',day:'2-digit',
  hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
window.localizeTimes=function(root){
  (root||document).querySelectorAll('[data-ts]').forEach(function(el){
    var ms=+el.getAttribute('data-ts');
    if(ms)el.textContent=window._TZFMT.format(new Date(ms));});};
if(document.readyState!=='loading')localizeTimes();
else document.addEventListener('DOMContentLoaded',function(){localizeTimes();});
</script>"""


def _nav(active: str) -> str:
    """W5.6/W8.1 命令列:品牌 + Live + 分頁 + 明暗切換。"""
    def tab(key, href, label):
        return (f"<a class='{'on' if key == active else ''}' "
                f"href='{href}'>{label}</a>")
    tabs = (tab("dash", "/", "Dashboard") + tab("db", "/db", "DB Browser")
            + tab("control", "/control", "Control")
            + tab("agent", "/agent", "Agent Detail")
            + tab("server", "/server", "Server")
            + tab("concepts", "/concepts", "概念"))
    nm = (f"<span class='iname'>{esc(_INSTANCE_NAME)}</span>"
          if _INSTANCE_NAME else "")
    return ("<a class='skip' href='#main'>跳到主要內容</a>"
            "<div class='cmdbar'>"
            "<span class='brand' translate='no'>ARCP"
            "<span class='sub'>Control&nbsp;Plane</span>" + nm
            + "</span><span class='live'><i aria-hidden='true'></i>Live</span>"
            f"<nav class='navtabs'>{tabs}</nav>"
            "<button class='tgl' id='arcp-tgl' type='button' aria-label='切換明暗主題'>"
            "<span id='arcp-tgi'>☾</span><span id='arcp-tgn'>深色</span>"
            "</button></div>" + _THEME_JS)


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
  if(!rows.length)return "<p style='color:var(--muted)'>(無資料)</p>";
  const h='<tr>'+cols.map(c=>`<td><b>${esc(c)}</b></td>`).join('')+'</tr>';
  const b=rows.map(r=>'<tr>'+r.map(v=>`<td>${v==null?
    "<span style='color:var(--faint)'>null</span>":esc((''+v).slice(0,200))}`+
    '</td>').join('')+'</tr>').join('');
  return `<div style='overflow:auto;max-height:60vh'><table id='tix'>`+
    `<thead>${h}</thead><tbody>${b}</tbody></table></div>`+
    (total!=null?`<div style='color:var(--muted);font-size:12px;margin-top:6px'>`+
    `${total} 筆;顯示 ${OFF+1}-${OFF+rows.length}</div>`:'');
}
async function loadTables(){
  const t=await (await fetch('/db/tables')).json();
  $('tlist').innerHTML=t.map(x=>
    `<button type='button' class='btn' style='display:block;margin:4px 0;text-align:left' `+
    `onclick="openT('${x.name}')">${esc(x.name)} `+
    `<span style='color:var(--muted);float:right'>${x.rows}</span></div>`).join('');
}
async function openT(name){CUR=name;OFF=0;$('qbox').value='';showTable();}
async function showTable(){
  const d=await (await fetch(`/db/table/${CUR}?limit=${LIM}&offset=${OFF}`))
    .json();
  if(d.error){$('dbout').innerHTML="<p style='color:var(--s-failure)'>"+esc(d.error)+
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
    $('dbout').innerHTML="<p style='color:var(--s-failure)'>"+esc(d.error)+"</p>";return;}
  DBMODE='query';LASTQ={cols:d.columns,rows:d.rows};
  $('dbtitle').textContent='🔎 查詢結果';
  $('dbout').innerHTML=tbl(d.columns,d.rows,null)+
    (d.rows.length>=500?"<p style='color:var(--s-pending);font-size:12px'>"+
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
            f"<span style='color:var(--muted);font-size:13px'>(唯讀)</span>"
            f"</h1></header><main id='main' tabindex='-1'>"
            "<div style='display:flex;gap:16px;align-items:flex-start'>"
            "<div class='card' style='min-width:200px'>"
            "<b style='color:var(--muted)'>Tables</b><div id='tlist'></div></div>"
            "<div style='flex:1'>"
            "<div class='card'>"
            "<b style='color:var(--muted)'>唯讀查詢</b>"
            "<span style='color:var(--faint);font-size:11px'> "
            "SELECT / WITH / PRAGMA;⌘/Ctrl+Enter 執行</span>"
            "<textarea id='qbox' aria-label='唯讀 SQL 查詢' spellcheck='false' placeholder='SELECT * FROM ticket_session "
            "WHERE outcome IS NULL' style='width:100%;height:64px;margin-top:"
            "6px;background:var(--raise);color:var(--ink);border:1px solid var(--line-2);"
            "border-radius:6px;padding:8px;font-family:ui-monospace,monospace;"
            "box-sizing:border-box'></textarea>"
            "<button type='button' class='btn' style='margin-top:6px' onclick='runQ()'>▶ 執行"
            "</button></div>"
            "<div class='card'>"
            "<div style='display:flex;align-items:center'>"
            "<h2 id='dbtitle' aria-live='polite' style='margin:0;flex:1'>← 點左側表格</h2>"
            "<button type='button' class='btn' onclick='dbExport(\"csv\")'>⬇ CSV</button>"
            "<button type='button' class='btn' onclick='dbExport(\"json\")'>⬇ JSON</button></div>"
            "<div id='dbpg' class='ctl' style='display:none;margin:8px 0'>"
            "<button type='button' class='btn' onclick='dpg(-1)'>‹ 上頁</button>"
            "<button type='button' class='btn' onclick='dpg(1)'>下頁 ›</button></div>"
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
                 f"<td>{(str(r['score']) + '/10') if r['score'] is not None else '未評'}</td>"
                 f"<td>{esc(r['assignee'] or '-')}</td>"
                 f"<td>{esc(fmt_ts(r['created']))}</td>"
                 f"<td>{esc(fmt_ts(r['finished']))}</td>"
                 f"<td>{esc(fmt_ts(r['handoff']))}</td>"
                 f"<td>{dwell}</td><td>{life}</td><td>{hcost}</td>"
                 f"<td>{r['attempts']}</td>"
                 f"<td>${r['cost']:.4f}</td></tr>")
    filterbar = (
        "<div class='ctl card' style='flex-wrap:wrap' role='search'>"
        "<b style='color:var(--muted)'>過濾</b>"
        f"<select id='qr' aria-label='時間範圍' {_INPUT}>"
        "<option value='all'>全部時間</option>"
        "<option value='7'>過去 7 天</option>"
        "<option value='30'>過去 30 天</option>"
        "<option value='60'>過去 60 天</option>"
        "<option value='90'>過去 90 天</option></select>"
        f"<input type='date' id='from' aria-label='起始日期' {_INPUT}>~"
        f"<input type='date' id='to' aria-label='結束日期' {_INPUT}>"
        f"<select id='st' aria-label='狀態' {_INPUT}>"
        "<option value=''>全部狀態</option></select>"
        f"<input id='kprofile' aria-label='profile 關鍵字' "
        f"placeholder='profile keyword…' {_INPUT}>"
        f"<input id='ksum' aria-label='summary 關鍵字' "
        f"placeholder='summary keyword…' {_INPUT}>"
        f"<input id='kdesc' aria-label='description 關鍵字' "
        f"placeholder='description keyword…' {_INPUT}>"
        "<span style='color:var(--faint);font-size:11px'>↓ 底下統計/圖表/表格"
        "皆只含過濾後的 Jira</span></div>")
    charts = (
        "<h2>時間圖(Create/Close/成功/失敗)</h2><div class='card'>"
        "<label style='color:var(--muted);font-size:12px'>"
        "<input type='checkbox' id='wk1'> 以每週呈現</label>"
        "<svg id='chart-time'></svg><div id='lg-time'></div></div>"
        "<h2>金錢圖(AI vs 人類)</h2><div class='card'>"
        "<label style='color:var(--muted);font-size:12px'>"
        "<input type='checkbox' id='wk2'> 以每週呈現</label>"
        " <label style='color:var(--muted);font-size:12px'>人類時薪 USD $"
        f"<input type='number' id='rate' min='0' step='1' "
        f"aria-label='人類時薪(USD)' inputmode='numeric' {_INPUT} "
        "style='width:70px;background:var(--raise);color:var(--ink);"
        "border:1px solid var(--line-2);border-radius:7px;padding:4px 8px'>"
        "</label><svg id='chart-money'></svg><div id='lg-money'></div></div>"
        # W7.4 per-profile 三圖(縱=profile)
        "<h2>各 Profile · 票數(依狀態堆疊)</h2><div class='card'>"
        "<div id='chart-pstate'></div><div id='lg-pstate'></div></div>"
        "<h2>各 Profile · 花費 vs 人力$ vs 效益</h2><div class='card'>"
        "<div id='chart-pcost'></div><div id='lg-pcost'></div></div>"
        "<h2>各 Profile · 平均完成度</h2><div class='card'>"
        "<div id='chart-pscore'></div><div id='lg-pscore'></div></div>")
    toolbar = (
        "<div class='ctl card'>"
        f"<select id='psize' aria-label='每頁筆數' {_INPUT}>"
        "<option>10</option><option selected>20</option>"
        "<option>50</option><option>100</option></select>"
        "<button type='button' class='btn' onclick='pg(-1)'>‹ 上頁</button>"
        "<button type='button' class='btn' onclick='pg(1)'>下頁 ›</button>"
        "<span id='pginfo' aria-live='polite' style='color:var(--muted);font-size:12px'></span><span id='upd' class='sys' aria-live='polite' style='margin:0 0 0 8px'></span>"
        "<span style='margin-left:auto'></span>"
        "<button type='button' class='btn' onclick='expo(\"csv\")'>⬇ CSV</button>"
        "<button type='button' class='btn' onclick='expo(\"json\")'>⬇ JSON</button></div>")
    return (f"{_nav('dash')}"
            f"<header><h1>ARCP Dashboard · {esc(ROOT.split('/')[-1])}"
            f"</h1></header><main id='main' tabindex='-1'>"
            f"{filterbar}"
            f"<div class='stats' id='stats'>"
            f"{overview_cards(sessions, journal)}</div>"
            f"{charts}"
            f"<h2>Tickets</h2>{toolbar}"
            f"<div class='card' style='overflow-x:auto'>"
            f"<table id='tix'><thead><tr id='thead-row'>"
            f"<td><b>ticket</b></td><td><b>summary</b></td>"
            f"<td><b>profile</b></td><td><b>status</b></td>"
            f"<td><b>完成度</b></td>"
            f"<td><b>assignee</b></td><td><b>created</b></td>"
            f"<td><b>finished</b></td><td><b>換手起點</b></td>"
            f"<td><b>停留時間</b></td><td><b>lifetime</b></td>"
            f"<td><b>人力$</b></td>"
            f"<td><b>attempts</b></td><td><b>cost</b></td></tr></thead>"
            f"<tbody>{rows}</tbody></table></div>"
            f"<p style='color:var(--muted)'>四層 trace:L0 ticket · L1 attempt · "
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
            f"<span class='kv' style='color:var(--muted)'>填表區段在 Jira "
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
            "<button type='button' class='btn' "
            "title='對此票當前 session 立即產生 transcript HTML(定格)。"
            "進行中/等待人類/已完成皆可;會覆蓋既有產物並更新產生時間與原因為"
            "「手動」。' "
            f"onclick=\"this.textContent='產生中…';"
            f"fetch('{CONTROL}/gen_transcript/{iid}',{{method:'POST'}})"
            ".then(r=>r.json()).then(j=>{this.textContent="
            "j.generated?'已產生('+j.files+' 檔),重整中…':'失敗:'+(j.error||'?');"
            "if(j.generated)setTimeout(()=>location.reload(),800);})"
            f".catch(()=>this.textContent='control 離線')\">{label}</button>")

    return (f"<h2>Transcript(可視化 / 下載)</h2>"
            f"<div class='card'><div class='row'>{info}</div>"
            f"<div class='ctl'>{links}{gen_btn}</div></div>")


# ── W6.7:事件時間軸(per-ticket,只 harness/Jira 生命週期,不含 agent 對話)── #
_TL_GROUPS = [
    {"id": "in", "content": "外部輸入(人/Jira)"},
    {"id": "jira", "content": "Jira 寫入(harness→)"},
    {"id": "life", "content": "生命週期 / 決策"},
    {"id": "run", "content": "執行 / 產物"},
]
# 事件型別 → (group, 中文標籤)。jira_write 另依 action 細分(見下)。
_TL_MAP = {
    "new_issue": ("in", "🆕 新票"),
    "comment_added": ("in", "💬 收到留言"),
    "assignee_changed": ("in", "👤 assignee 變更"),
    "status_changed": ("in", "📋 status 變更"),
    "route_matched": ("life", "🎯 路由命中"),
    "session_created": ("life", "🎬 session 建立"),
    "queued": ("life", "⏳ 排隊"),
    "resolved": ("life", "✅ 結案"),
    "pending": ("life", "⏸ 等待人類"),
    "handoff": ("life", "🔀 換手"),
    "handoff_invalid": ("life", "⚠ 換手無效"),
    "inactive_set": ("life", "😴 交人類(讓出額度)"),
    "inactive_cleared": ("life", "▶ 回機器人(resume)"),
    "external_abort": ("life", "🛑 外部撤銷"),
    "external_pending": ("life", "⏸ 外部變更暫停"),
    "external_cleared": ("life", "▶ 外部恢復"),
    "command_accepted": ("life", "✔ 指令接受"),
    "command_denied": ("life", "🚫 指令拒絕(未授權)"),
    "command_rejected": ("life", "🚫 指令拒絕"),
    "command_unknown": ("life", "❓ 指令不明"),
    "approval": ("life", "🔐 審批"),
    "adopted": ("life", "📌 認養(baseline)"),
    "workspace_reclaimed": ("life", "♻ workspace 回收"),
    "workspace_unhealthy": ("life", "⚠ workspace 異常"),
    "attempt_started": ("run", "▶ attempt 開始"),
    "attempt_finished": ("run", "⏹ attempt 結束"),
    "attempt_crash_recovered": ("run", "🔧 crash 復原"),
    "evicted": ("run", "⏻ 強制驅逐"),
    "dispatch_error": ("run", "💥 派工錯誤"),
    "transcript_packed": ("run", "📄 transcript 產出"),
    "script_run_started": ("run", "📜 script 開始"),
    "script_run_finished": ("run", "📜 script 結束"),
    "trigger_started": ("run", "⏱ trigger 開始"),
    "trigger_finished": ("run", "⏱ trigger 結束"),
    "trigger_error": ("run", "⏱ trigger 錯誤"),
}
_TL_JIRA = {"comment": "💬 留言 Jira", "assign": "👤 改 assignee",
            "transition": "📋 transition", "description": "📝 改 description"}


def timeline_data(evs: list[dict]) -> dict:
    """journal 事件 → vis-timeline items/groups。時間戳 epoch→ms;完整欄位進
    tooltip(title);className 依 group 上色。只含 harness/Jira 生命週期。"""
    items = []
    for i, e in enumerate(evs):
        et = e.get("type", "?")
        if et == "jira_write":
            grp, label = "jira", _TL_JIRA.get(e.get("action", ""), "✍ Jira 寫入")
        else:
            grp, label = _TL_MAP.get(et, ("life", et))
        extra = {k: v for k, v in e.items()
                 if k not in ("ts", "type", "issue_id", "key")}
        det = json.dumps(extra, ensure_ascii=False)
        items.append({
            "id": i, "group": grp, "content": label,
            "start": int(float(e.get("ts") or 0) * 1000),
            "className": "tl-" + grp,
            "title": f"{et} · {det}"[:400],
        })
    return {"groups": _TL_GROUPS, "items": items}


def l3_timeline_data(iid: int) -> dict:
    """W9.2:agent 對話(L3)→ vis-timeline;group=attempt(a1/a2…),item=每則訊息
    (start=timestamp,content=source+摘要,className 依 user/agent 上色)。"""
    ad = attempt_dir(iid)
    groups, items = [], []
    if os.path.isdir(ad):
        for fn in sorted(f for f in os.listdir(ad)
                         if f.endswith(".events.jsonl")):
            n = fn.split(".")[0]
            groups.append({"id": n, "content": n})
            try:
                with open(os.path.join(ad, fn)) as f:
                    for j, line in enumerate(f):
                        if not line.strip():
                            continue
                        e = json.loads(line)
                        ms = _iso_to_ms(e.get("timestamp"))
                        if not ms:
                            continue
                        src = e.get("source", "")
                        txt = " ".join(_text_of(
                            (e.get("llm_message") or {}).get("content")).split())
                        items.append({
                            "id": f"{n}-{j}", "group": n, "start": ms,
                            "content": ("🧑 " if src == "user" else "🤖 ")
                            + (txt[:24] or src),
                            "className": "l3-" + ("user" if src == "user"
                                                  else "agent"),
                            "title": f"{src}: {txt}"[:400]})
            except (OSError, ValueError):
                pass
    return {"groups": groups, "items": items}


def render_timeline_section(evs: list[dict], iid: int) -> str:
    """W6.7/W9.2:兩條時間軸(agent 對話 L3 + Jira 生命週期),收在**右下浮動鈕**
    切換的抽屜裡(仿 transcript)。**刻意放 <main> 之外**——ticket 頁每 5s 整段換
    main.innerHTML,widget 在裡面會被反覆摧毀;放外面只初始化一次,刷新只抽資料島更新。"""
    life = json.dumps(timeline_data(evs), ensure_ascii=False)
    l3 = json.dumps(l3_timeline_data(iid), ensure_ascii=False)
    return (
        "<button type='button' id='tlfab' aria-expanded='false' "
        "aria-controls='tlwrap'>🕑 時間軸</button>"
        "<section id='tlwrap' aria-hidden='true'>"
        "<h2 style='margin:4px 0 6px'>💬 agent 對話時間軸(L3)</h2>"
        "<p class='sys' style='margin:0 0 6px;text-align:left'>每則訊息隨時間"
        "(🧑=user/harness prompt、🤖=agent 回覆);滾輪縮放按 Ctrl。</p>"
        "<div id='l3tl'></div>"
        f"<script id='l3tl-data' type='application/json'>{l3}</script>"
        "<h2 style='margin:16px 0 6px'>📅 Jira 生命週期時間軸</h2>"
        "<p class='sys' style='margin:0 0 6px;text-align:left'>harness↔Jira "
        "生命週期事件(留言/派工/結案…)。</p>"
        "<div id='evtl'></div>"
        f"<script id='evtl-data' type='application/json'>{life}</script>"
        "</section>"
        "<link rel='stylesheet' href='/tvendor/vis-timeline.min.css'>"
        "<script src='/tvendor/vis-timeline.min.js'></script>"
        "<script>(function(){"
        "function rd(id){try{return JSON.parse("
        "document.getElementById(id).textContent)}catch(e){return{groups:[],items:[]}}}"
        "function build(el,d){if(!el||!window.vis)return null;"
        "var items=new vis.DataSet(d.items),groups=new vis.DataSet(d.groups);"
        "var tl=new vis.Timeline(el,items,groups,{stack:true,orientation:'top',"
        "zoomKey:'ctrlKey',margin:{item:6},tooltip:{followMouse:true},maxHeight:340});"
        "return{tl:tl,items:items};}"
        "var L3=build(document.getElementById('l3tl'),rd('l3tl-data'));"
        "var LF=build(document.getElementById('evtl'),rd('evtl-data'));"
        "window.__evtlUpdate=function(nd){if(LF){LF.items.clear();LF.items.add(nd.items);}};"
        "var fab=document.getElementById('tlfab'),wrap=document.getElementById('tlwrap');"
        "function setOpen(o){wrap.classList.toggle('on',o);"
        "fab.setAttribute('aria-expanded',o);wrap.setAttribute('aria-hidden',!o);"
        "fab.textContent=o?'✕ 收起時間軸':'🕑 時間軸';"
        "try{localStorage.setItem('arcp-tl',o?'1':'0')}catch(e){}"
        "if(o){setTimeout(function(){if(L3)L3.tl.redraw();if(LF)LF.tl.redraw();},30);}}"
        "fab.addEventListener('click',function(){"
        "setOpen(!wrap.classList.contains('on'));});"
        "var s;try{s=localStorage.getItem('arcp-tl')}catch(e){}"
        "if(s==='1')setOpen(true);"
        "})();</script>")


def _ts_ms_span(ms: int) -> str:
    """W9.1:時間占位(存 epoch ms),client 端 localizeTimes() 依瀏覽器時區顯示。"""
    return (f"<time class='evt' data-ts='{int(ms)}' "
            f"style='color:var(--faint);font-variant-numeric:tabular-nums;"
            f"margin-right:8px'>—</time>")


def _iso_to_ms(iso) -> int:
    import datetime
    try:
        return int(datetime.datetime.fromisoformat(str(iso)).timestamp() * 1000)
    except (ValueError, TypeError):
        return 0


def render_ticket(iid, journal, sessions) -> str:
    s = sessions.get(iid, {})
    key = s.get("key") or f"#{iid}"
    evs = [e for e in journal if e["issue_id"] == iid]

    # L0/L1 journal(W9.1:前置本地時間)
    l0 = ""
    for e in evs:
        extra = {k: v for k, v in e.items()
                 if k not in ("ts", "type", "issue_id", "key")}
        l0 += (f"<div class='ev'>{_ts_ms_span(float(e.get('ts') or 0) * 1000)}"
               f"<span class='k'>{esc(e['type'])}</span> "
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
                    # W9.1:補訊息摘要 + 啟用 skills(原本只印 kind+source,太空)
                    txt = _text_of((i.get("llm_message") or {}).get("content"))
                    if not txt and kind == "ConversationStateUpdateEvent":
                        txt = f"{i.get('key','')}={str(i.get('value',''))[:60]}"
                    elif not txt and kind == "ACPToolCallEvent":
                        txt = str(i.get("title") or i.get("tool_kind") or "")
                    txt = " ".join(txt.split())[:120]
                    sk = i.get("activated_skills") or []
                    sk_html = (f" <span style='color:var(--accent-ink)'>"
                               f"⚙ {esc(','.join(sk))}</span>" if sk else "")
                    ms = _iso_to_ms(i.get("timestamp"))
                    tm = _ts_ms_span(ms) if ms else ""
                    tag = ("var(--s-running)" if src == "user"
                           else "var(--s-success)")
                    rows += (f"<div class='ev'>{tm}"
                             f"<span class='k' style='color:{tag}'>{esc(src or kind)}"
                             f"</span> <span class='t'>{esc(txt)}{sk_html}</span></div>")
                trace_layers += (
                    f"<h2>{esc(n)} · L3 conversation 事件({sum(hist.values())} 則:"
                    f"user=harness 給的 prompt、agent=模型回覆)</h2>"
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
            f"{esc(s.get('outcome') or '-')}</span></h1></header><main id='main' tabindex='-1'>"
            f"<div class='card'><div class='row'>"
            f"<span class='kv'><b>profile</b> {esc(s.get('profile','-'))}</span>"
            f"<span class='kv'><b>attempts</b> {esc(s.get('attempts',0))}</span>"
            f"<span class='kv'><b>cost</b> ${s.get('cost_usd',0):.4f}</span>"
            f"<span class='kv'><b>workspace</b> {esc(s.get('workspace','-'))}</span>"
            + (f"<span class='kv'><b>驅逐次數</b> {s.get('evict_count', 0)}</span>"
               if s.get("evict_count") else "")
            + (("<button type='button' class='btn' style='margin-left:auto;color:var(--s-failure)' "
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
                ">⏻ 強制驅逐(killpg)</button>")
               if s and not s.get("outcome")
               and not str(s.get("workspace", "")).startswith("(") else "")
            + f"</div></div>"
            f"{render_transcript_card(iid, s)}"
            f"{render_approval(s, evs)}"
            f"<div class='tabs'>"
            f"<button type='button' class='tab on' id='tab-convo' onclick='tab(\"convo\")'>💬 Conversation</button>"
            f"<button type='button' class='tab' id='tab-trace' onclick='tab(\"trace\")'>🔍 Trace (L0-L3)</button>"
            f"</div>"
            f"<div class='pane on' id='pane-convo'>{convo_view}</div>"
            f"<div class='pane' id='pane-trace'>{trace_view}</div>"
            f"{tabs_js}</main>"
            # W6.7:時間軸刻意在 </main> 之外(5s 刷新只換 main,widget 存活)
            f"{render_timeline_section(evs, iid)}")


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
            {"name": "llm-api(唯讀)",
             "description": "給 LLM 監控:ticket-keyed 狀態 + L3 事件 + 原始 log"
                            "(三合一 ref:Jira key/id/CR id)"},
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
            "/api/v1/tickets": {"get": {
                "tags": ["llm-api(唯讀)"],
                "summary": "票列表(精簡:key/profile/8態/outcome/cost/score)",
                "responses": {"200": {"description": "tickets[]",
                                      "content": {"application/json": {}}}}}},
            "/api/v1/tickets/{ref}": {"get": {
                "tags": ["llm-api(唯讀)"],
                "summary": "單票完整狀態 JSON(含時間軸摘要 + 可取 log 清單)",
                "description": "ref = Jira key(SCRUM-42)/ 內部 id / ClearQuest CR id。",
                "parameters": [{"name": "ref", "in": "path", "required": True,
                                "schema": {"type": "string"}}],
                "responses": {"200": {"description": "狀態",
                                      "content": {"application/json": {}}},
                              "404": {"description": "查無此票"}}}},
            "/api/v1/tickets/{ref}/events": {"get": {
                "tags": ["llm-api(唯讀)"],
                "summary": "L3 conversation 事件(各 attempt aN.events.jsonl → JSON)",
                "parameters": [{"name": "ref", "in": "path", "required": True,
                                "schema": {"type": "string"}}],
                "responses": {"200": {"description": "attempts[]",
                                      "content": {"application/json": {}}}}}},
            "/api/v1/tickets/{ref}/logs": {"get": {
                "tags": ["llm-api(唯讀)"],
                "summary": "可取原始 log 清單(attempt/ transcript/ source/)",
                "parameters": [{"name": "ref", "in": "path", "required": True,
                                "schema": {"type": "string"}}],
                "responses": {"200": {"description": "logs[]",
                                      "content": {"application/json": {}}}}}},
            "/api/v1/tickets/{ref}/logs/{name}": {"get": {
                "tags": ["llm-api(唯讀)"],
                "summary": "原始 log 內容(text/plain;?tail=N 只取末 N 行)",
                "parameters": [
                    {"name": "ref", "in": "path", "required": True,
                     "schema": {"type": "string"}},
                    {"name": "name", "in": "path", "required": True,
                     "schema": {"type": "string"},
                     "description": "logs 清單裡的 name(如 attempt/a1.events.jsonl)"},
                    {"name": "tail", "in": "query",
                     "schema": {"type": "integer"}}],
                "responses": {"200": {"description": "raw 檔內容"},
                              "404": {"description": "查無此 log"}}}},
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
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='theme-color' content='#F1EEE6' media='(prefers-color-scheme:light)'><meta name='theme-color' content='#1E1C19' media='(prefers-color-scheme:dark)'>"
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


# ── W7.5:Agent Detail — harness 設定 + 全 Profile 參數(唯讀,server-render)── #
def _redact(d):
    """遮蔽疑似敏感 key(routes.yaml 本無憑證,防禦性;憑證在 ~/.env)。"""
    sens = re.compile(r"token|secret|password|api[_-]?key", re.I)
    if isinstance(d, dict):
        return {k: ("***" if sens.search(str(k)) else _redact(v))
                for k, v in d.items()}
    if isinstance(d, list):
        return [_redact(x) for x in d]
    return d


def _kv_table(pairs) -> str:
    """(label, value) → 小表格(value 為 dict/list 時 JSON 呈現)。"""
    rows = ""
    for k, v in pairs:
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False, default=str)
        rows += (f"<tr><td style='color:var(--muted);padding:2px 12px 2px 0;"
                 f"white-space:nowrap;vertical-align:top'>{esc(k)}</td>"
                 f"<td style='font-family:ui-monospace,monospace;font-size:12px'>"
                 f"{esc('' if v is None else v)}</td></tr>")
    return f"<table>{rows}</table>"


def render_agent_page() -> str:
    """W7.5:harness 設定(routes.yaml)+ 每個 Profile 全參數。憑證不在此檔。"""
    try:
        import sys as _sys
        _here = os.path.dirname(os.path.abspath(__file__))
        if _here not in _sys.path:
            _sys.path.insert(0, _here)
        from arcp_harness.profiles import load_profiles
        from arcp_harness.routing import load_config
        src, routes = load_config(_CONFIG_PATH)
        profiles = load_profiles(_CONFIG_PATH)
        err = None
    except Exception as e:  # noqa: BLE001 — 壞 config/缺套件不擋頁
        src, routes, profiles, err = {}, [], {}, str(e)

    head = (f"{_nav('agent')}<header><h1>Agent Detail · 設定與 Profile</h1>"
            f"</header><main id='main' tabindex='-1'><p class='sys' style='text-align:left'>"
            f"來源 <code>{esc(_CONFIG_PATH)}</code>(唯讀;憑證在 ~/.env,不顯示)。"
            f"</p>")
    if err:
        return (head + f"<div class='card'><b style='color:var(--s-failure)'>"
                f"讀取設定失敗</b><div class='sys' style='text-align:left'>"
                f"{esc(err)}</div></div></main>")

    # harness 設定(source/concurrency/control/commands/external_change…)
    cfg = _redact(src)
    cfg_card = ("<h2>harness 設定(routes.yaml)</h2><div class='card'>"
                + _kv_table(sorted(cfg.items())) + "</div>")

    # 路由
    rrows = "".join(
        f"<tr><td>{esc(r.name)}</td>"
        f"<td style='font-family:ui-monospace,monospace;font-size:12px'>"
        f"{esc(json.dumps(r.when, ensure_ascii=False, default=str))}</td>"
        f"<td>{esc(r.profile or '-')}</td><td>{esc(r.on_match)}</td></tr>"
        for r in routes)
    routes_card = (
        "<h2>路由(route → profile)</h2><div class='card'>"
        "<table><thead><tr><td><b>route</b></td><td><b>when</b></td>"
        "<td><b>profile</b></td><td><b>on_match</b></td></tr></thead>"
        f"<tbody>{rrows or '<tr><td>(無)</td></tr>'}</tbody></table></div>")

    # 每個 profile 全參數
    pcards = ""
    for name in sorted(profiles):
        p = profiles[name]
        verify = [{"name": v.name, "files": v.files, "cmd": v.cmd}
                  for v in p.verify]
        pcards += (
            f"<h2>Profile · {esc(name)}</h2><div class='card'>"
            + _kv_table([
                ("goal", p.goal or "(未設)"),
                ("agent", p.agent),
                ("workspace_template", p.workspace_template),
                ("workspace_folder", p.workspace_folder),
                ("skills", p.skills),
                ("verify", verify),
                ("max_attempts", p.max_attempts),
                ("max_budget_usd(單次)", p.max_budget_usd),
                ("max_budget_monthly_usd(月)", p.max_budget_monthly_usd),
                ("human_minutes_est", p.human_minutes_est),
                ("est_minutes(有效,未設→240)", p.est_minutes()),
                ("require_approval", p.require_approval),
                ("approver", p.approver),
                ("max_revisions", p.max_revisions),
                ("retention_days", p.retention_days),
                ("on_unknown", p.on_unknown),
            ]) + "</div>")

    return head + cfg_card + routes_card + pcards + "</main>"


# ── W7.6:概念/生命週期/狀態機頁(純 SVG,零依賴)────────────────────────── #
# 8 態節點:key → (cx, cy, 中文)。座標經手調,盡量少交叉。
# 顏色改由 CSS class st-<key>(見 CSS #smsvg 區)驅動,隨明暗主題變。
_SM_NODES = {
    "todo": (95, 250, "待處理"),
    "running": (300, 250, "進行中"),
    "queued": (300, 370, "排隊"),
    "pending": (510, 130, "等待人類"),
    "inactive": (510, 370, "交人(inactive)"),
    "success": (720, 95, "成功"),
    "failure": (720, 205, "失敗"),
    "aborted": (720, 370, "撤銷"),
    "exit": (880, 150, "人評分→關票→離開"),
}
# 轉移:(from, to, 標籤)
_SM_EDGES = [
    ("todo", "running", "路由命中·派工"),
    ("running", "queued", "額滿"),
    ("queued", "running", "有額度"),
    ("running", "pending", "UNKNOWN/預算/交人決定/審批"),
    ("pending", "running", "@agent run·retry / budget_override"),
    ("running", "inactive", "assignee→人"),
    ("inactive", "running", "assignee→機器人"),
    ("running", "success", "verify 過"),
    ("running", "failure", "max-attempts"),
    ("running", "aborted", "cancel / 外部關 Done"),
    ("success", "exit", "人評分(0–10)→關 Done"),
    ("failure", "exit", ""),
]


def _sm_svg() -> str:
    """8 態狀態機 SVG:中心→中心連線裁切到矩形邊界 + 箭頭 + 雙向邊垂直偏移。"""
    hw, hh = 62, 22           # 節點半寬/半高
    W, H = 980, 440
    out = ["<svg id='smsvg' viewBox='0 0 %d %d' width='100%%' "
           "preserveAspectRatio='xMinYMin meet' "
           "style='max-height:%dpx;font-size:11px'>" % (W, H, H),
           "<defs><marker id='ah' viewBox='0 0 10 10' refX='9' refY='5' "
           "markerWidth='7' markerHeight='7' orient='auto-start-reverse'>"
           "<path class='sm-arrow' d='M0,0 L10,5 L0,10 z'/></marker></defs>"]

    def trim(cx, cy, tx, ty):
        """從 (cx,cy) 往 (tx,ty),回落在來源節點矩形邊界的點。"""
        dx, dy = tx - cx, ty - cy
        if dx == 0 and dy == 0:
            return cx, cy
        sx = hw / abs(dx) if dx else 9e9
        sy = hh / abs(dy) if dy else 9e9
        t = min(sx, sy)
        return cx + dx * t, cy + dy * t

    for a, b, label in _SM_EDGES:
        ax, ay = _SM_NODES[a][:2]
        bx, by = _SM_NODES[b][:2]
        dx, dy = bx - ax, by - ay
        ln = (dx * dx + dy * dy) ** 0.5 or 1
        # 垂直於行進方向偏移 7px(讓雙向邊分開、不重疊)
        ox, oy = -dy / ln * 7, dx / ln * 7
        x1, y1 = trim(ax + ox, ay + oy, bx + ox, by + oy)
        x2, y2 = trim(bx + ox, by + oy, ax + ox, ay + oy)
        out.append(
            f"<line class='sm-edge' x1='{x1:.0f}' y1='{y1:.0f}' "
            f"x2='{x2:.0f}' y2='{y2:.0f}' stroke-width='1.3' "
            f"marker-end='url(#ah)'/>")
        if label:
            mx, my = (x1 + x2) / 2 + ox, (y1 + y2) / 2 + oy
            out.append(
                f"<text class='sm-elabel' x='{mx:.0f}' y='{my:.0f}' "
                f"text-anchor='middle'>{esc(label)}</text>")

    for _k, (cx, cy, lb) in _SM_NODES.items():
        out.append(
            f"<rect class='sm-box st-{_k}' x='{cx - hw}' y='{cy - hh}' "
            f"width='{hw * 2}' height='{hh * 2}' rx='8' stroke-width='1.6'/>"
            f"<text class='sm-lbl st-{_k}' x='{cx}' y='{cy + 4}' "
            f"text-anchor='middle'>{esc(lb)}</text>")
    out.append("</svg>")
    return "".join(out)


# W9.1:第三欄=此態如何由 DB 欄位推導(canonical_state 的判斷來源)。
_STATE_DOC = [
    ("待處理 todo", "被 watch 到、尚無 session(還沒派工或不歸任何 route)。",
     "ticket_watch 有列、但 ticket_session 無此 issue_id"),
    ("進行中 running", "有 active session 正在跑 attempt(占機器額度)。",
     "ticket_session.outcome=NULL 且 pending_reason=NULL 且 queued=0 且 inactive=0"),
    ("排隊 queued", "本輪並發額滿,下輪重評(F1 分層閘門)。",
     "ticket_session.queued=1"),
    ("等待人類 pending", "需要人:UNKNOWN、預算上限、交人決定、審批門、max-attempts。",
     "ticket_session.pending_reason 非空(或 outcome='UNKNOWN')"),
    ("交人 inactive", "assignee 在人類手上→不派工、讓出額度(改回機器人即 resume)。",
     "ticket_session.inactive=1"),
    ("成功 success", "verify(grader)通過=SUCCESS(證據型停止,非 agent 自稱)。",
     "ticket_session.outcome='SUCCESS'"),
    ("失敗 failure", "max_attempts 用盡仍未過驗證。",
     "ticket_session.outcome='FAILURE'"),
    ("撤銷 aborted", "人在看板關成 Done/Cancelled,或 @agent cancel。",
     "ticket_session.outcome='ABORTED'"),
]


def render_concepts_page() -> str:
    """W7.6:系統概念/資料流生命週期/狀態機(純 SVG)——使用說明書。"""
    doc_rows = (
        "<tr><td><b>態</b></td><td><b>意義</b></td>"
        "<td><b>DB 判斷來源</b></td></tr>"
        + "".join(
            f"<tr><td style='white-space:nowrap;color:var(--ink)'>{esc(k)}</td>"
            f"<td class='sys' style='text-align:left'>{esc(v)}</td>"
            f"<td class='mono' style='font-size:11px;color:var(--muted)'>"
            f"{esc(db)}</td></tr>"
            for k, v, db in _STATE_DOC))
    return (
        f"{_nav('concepts')}<header><h1>概念 · 資料流生命週期 · 狀態機</h1>"
        f"</header><main id='main' tabindex='-1'>"
        "<h2>一句話</h2><div class='card'><p>ARCP 讓 <code>claude -p</code> / "
        "<code>codex exec</code> 由 <b>Jira 事件驅動</b>、可觀測、可控制。搞定系統"
        "先搞定<b>資料流的生命週期</b>——下面是一張票從進來到離開的狀態流動。</p></div>"
        "<h2>Jira ticket 狀態機(harness 內部 8 態)</h2>"
        f"<div class='card'>{_sm_svg()}</div>"
        "<h2>8 態說明</h2><div class='card'><table>" + doc_rows + "</table></div>"
        "<h2>狀態存在哪(重要)</h2><div class='card'>"
        "<ul style='line-height:1.8'>"
        "<li><b>Jira 這邊</b>:真正的 <code>status</code>(To Do/進行中/Done)存 "
        "Jira,harness 只讀進來鏡射到 DB <code>ticket_watch.last_state</code>。</li>"
        "<li><b>我們系統這邊</b>:內部判定 <code>outcome</code>"
        "(SUCCESS/FAILURE/ABORTED/UNKNOWN)+ <code>pending_reason</code> 只存 DB "
        "<code>ticket_session</code>,<b>不寫回 Jira</b>。上面 8 態就是由這些欄位"
        "(加 queued/inactive/有無 session)推導的單一 canonical 狀態。</li>"
        "<li><b>harness 不主動 transition Jira 狀態</b>(只留言);關票=人做"
        "(W7:成功/失敗後交人評分,人填 <code>score</code> 再關)。</li>"
        "<li><b>生命週期事件</b>都記在 journal <code>events.jsonl</code>"
        "(new_issue/attempt_*/resolved/pending/handoff/jira_write/human_score…),"
        "ticket 詳情頁的<b>事件時間軸</b>即由它繪製。</li>"
        "</ul></div>"
        "<p class='sys' style='text-align:left'>同內容見 repo 根 "
        "<code>README.md</code>「資料流生命週期 / 狀態機」段。</p></main>")


# ── W7.7:REST /api/v1(唯讀,給 LLM 監控)────────────────────────────────── #
def _resolve_ref(ref: str, sessions: dict, watch: dict) -> int | None:
    """三合一解析器:Jira key(SCRUM-42)/ 內部 id / ClearQuest CR id → issue_id。"""
    if ref.isdigit() and (int(ref) in sessions or int(ref) in watch):
        return int(ref)
    for iid, s in sessions.items():
        if s.get("key") == ref or (s.get("clearquest_id") or "") == ref:
            return iid
    for iid, w in watch.items():
        if w.get("key") == ref:
            return iid
    return None


def _profile_engine(profile_name: str | None) -> str:
    """profile → engine(claude/codex);查不到→claude。給原始 source 檔解析。"""
    if not profile_name:
        return "claude"
    try:
        from arcp_harness.profiles import load_profiles
        p = load_profiles(_CONFIG_PATH).get(profile_name)
        return (p.agent.get("engine", "claude") if p else "claude")
    except Exception:  # noqa: BLE001
        return "claude"


def _api_logs_index(iid: int, s: dict) -> list[dict]:
    """此票可取的原始 log 清單:attempt(L2/L3)+ transcript 產物 + 原始 session jsonl。
    name 帶前綴命名空間(attempt/ transcript/ source/),供 /logs/{name} 取回。"""
    out: list[dict] = []

    def _add(prefix, path):
        try:
            out.append({"name": f"{prefix}/{os.path.basename(path)}",
                        "bytes": os.path.getsize(path)})
        except OSError:
            pass
    ad = attempt_dir(iid)
    if os.path.isdir(ad):
        for n in sorted(os.listdir(ad)):
            if os.path.isfile(os.path.join(ad, n)):
                _add("attempt", os.path.join(ad, n))
    ws = s.get("workspace") or ""
    if ws and not ws.startswith("("):
        td = transcript_dir_of(ws)
        if os.path.isdir(td):
            for n in sorted(os.listdir(td)):
                if os.path.isfile(os.path.join(td, n)) and n != "meta.json":
                    _add("transcript", os.path.join(td, n))
    try:
        from arcp_harness.transcript import source_files
        for p in source_files(s.get("session_id"),
                              _profile_engine(s.get("profile"))):
            _add("source", p)
    except Exception:  # noqa: BLE001
        pass
    return out


def _log_path(iid: int, s: dict, name: str) -> str | None:
    """name(attempt/…|transcript/…|source/…)→ 實際檔路徑(防 traversal)。"""
    if "/" not in name:
        return None
    kind, base = name.split("/", 1)
    base = os.path.basename(base)          # 防 traversal
    if kind == "attempt":
        p = os.path.join(attempt_dir(iid), base)
    elif kind == "transcript":
        ws = s.get("workspace") or ""
        if not ws or ws.startswith("("):
            return None
        p = os.path.join(transcript_dir_of(ws), base)
    elif kind == "source":
        try:
            from arcp_harness.transcript import source_files
            srcs = source_files(s.get("session_id"),
                                _profile_engine(s.get("profile")))
        except Exception:  # noqa: BLE001
            srcs = []
        p = next((x for x in srcs if os.path.basename(x) == base), None)
    else:
        return None
    return p if p and os.path.isfile(p) else None


def api_ticket_status(iid: int, journal: list, sessions: dict,
                      watch: dict) -> dict:
    """單票完整狀態 JSON(結構化;含時間軸摘要 + 可取 log 清單)。"""
    s = sessions.get(iid, {})
    w = watch.get(iid, {})
    score = s.get("human_score")
    tl = [{"ts": e.get("ts"), "type": e.get("type"),
           **{k: v for k, v in e.items()
              if k not in ("ts", "type", "issue_id", "key")}}
          for e in journal if e.get("issue_id") == iid]
    return {
        "iid": iid, "key": s.get("key") or w.get("key") or f"#{iid}",
        "clearquest_id": s.get("clearquest_id"),
        "profile": s.get("profile"), "state": canonical_state(s or None),
        "outcome": s.get("outcome"), "pending_reason": s.get("pending_reason"),
        "attempts": s.get("attempts") or 0,
        "cost_usd": s.get("cost_usd") or 0,
        "score": score,
        "completion_pct": (score * 10 if score is not None else None),
        "session_id": s.get("session_id"),
        "inactive": bool(s.get("inactive")), "queued": bool(s.get("queued")),
        "evict_count": s.get("evict_count") or 0,
        "assignee": w.get("last_assignee") or "",
        "summary": w.get("summary") or "",
        "workspace": s.get("workspace") or "",
        "finished_at": s.get("finished_at") or 0,
        "timeline": tl,
        "logs": _api_logs_index(iid, s),
    }


def api_list_tickets(journal: list, sessions: dict, watch: dict) -> dict:
    """精簡票列表(給 LLM 先掃全景)。"""
    ids = sorted(set(sessions) | set(watch))
    items = []
    for iid in ids:
        s = sessions.get(iid, {})
        w = watch.get(iid, {})
        items.append({
            "iid": iid, "key": s.get("key") or w.get("key") or f"#{iid}",
            "clearquest_id": s.get("clearquest_id"),
            "profile": s.get("profile"), "state": canonical_state(s or None),
            "outcome": s.get("outcome"), "cost_usd": s.get("cost_usd") or 0,
            "score": s.get("human_score")})
    return {"count": len(items), "tickets": items}


def api_events(iid: int) -> dict:
    """此票各 attempt 的 L3 conversation 事件(aN.events.jsonl → JSON)。"""
    ad = attempt_dir(iid)
    attempts = []
    if os.path.isdir(ad):
        for fn in sorted(f for f in os.listdir(ad) if f.endswith(".events.jsonl")):
            evs = []
            try:
                with open(os.path.join(ad, fn)) as f:
                    for line in f:
                        if line.strip():
                            evs.append(json.loads(line))
            except (OSError, ValueError):
                pass
            attempts.append({"attempt": fn.split(".")[0], "count": len(evs),
                             "events": evs})
    return {"iid": iid, "attempts": attempts}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send_json(self, obj, code: int = 200) -> None:
        payload = json.dumps(obj, ensure_ascii=False,
                             default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")   # W7.7 給 LLM 用
        self.end_headers()
        self.wfile.write(payload)

    def _serve_raw(self, path: str, tail: int = 0) -> None:
        """W7.7:原始 log 檔以 text/plain 回;tail>0 只回最後 N 行。"""
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            return self._send_json({"error": "not readable"}, 404)
        if tail > 0:
            data = b"".join(data.splitlines(keepends=True)[-tail:])
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _api_v1(self, journal, sessions) -> None:
        """W7.7 /api/v1/tickets[/{ref}[/events|/logs[/{name}]]](唯讀)。"""
        from urllib.parse import parse_qs, urlparse
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]   # api v1 tickets [ref] [sub]
        watch = read_watch()
        if len(parts) == 3:                            # /api/v1/tickets
            return self._send_json(api_list_tickets(journal, sessions, watch))
        ref = parts[3]
        iid = _resolve_ref(ref, sessions, watch)
        if iid is None:
            return self._send_json({"error": "ticket not found", "ref": ref},
                                   404)
        if len(parts) == 4:                            # /api/v1/tickets/{ref}
            return self._send_json(
                api_ticket_status(iid, journal, sessions, watch))
        if len(parts) == 5 and parts[4] == "events":
            return self._send_json(api_events(iid))
        if len(parts) == 5 and parts[4] == "logs":
            return self._send_json(
                {"iid": iid, "logs": _api_logs_index(iid, sessions.get(iid, {}))})
        if len(parts) >= 6 and parts[4] == "logs":     # /logs/{name…}
            name = "/".join(parts[5:])
            p = _log_path(iid, sessions.get(iid, {}), name)
            if not p:
                return self._send_json({"error": "log not found",
                                        "name": name}, 404)
            tail = parse_qs(u.query).get("tail")
            return self._serve_raw(p, int(tail[0]) if (tail and tail[0].isdigit())
                                   else 0)
        return self._send_json({"error": "bad api path"}, 404)

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
        if self.path.startswith("/api/v1/tickets"):  # W7.7 LLM 監控 API
            self._api_v1(journal, sessions)
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
        if self.path in ("/db", "/control", "/server", "/agent",
                         "/concepts"):  # 獨立頁
            body = (render_db_page() if self.path == "/db"
                    else render_control_page() if self.path == "/control"
                    else render_agent_page() if self.path == "/agent"
                    else render_concepts_page() if self.path == "/concepts"
                    else render_server_page())
            page = (f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='theme-color' content='#F1EEE6' media='(prefers-color-scheme:light)'><meta name='theme-color' content='#1E1C19' media='(prefers-color-scheme:dark)'>"
                    f"<title>ARCP{_TITLE_TAIL}</title><style>{CSS}</style></head>"
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
                     "if(nu&&cur&&nu.innerHTML!==cur.innerHTML){"
                     "const open=[...cur.querySelectorAll('details')]"
                     ".map(d=>d.open);"
                     "cur.innerHTML=nu.innerHTML;"
                     "[...cur.querySelectorAll('details')].forEach((d,i)=>{"
                     "if(open[i])d.open=true});"
                     "if(window.localizeTimes)localizeTimes(cur);"  # W9.1 重localize
                     "if(typeof tab==='function')"
                     "tab((location.hash||'#convo').slice(1));}"
                     # W6.7:時間軸在 main 之外——單獨抽資料島更新(不摧毀 widget)
                     "const nd=doc.querySelector('#evtl-data'),"
                     "live=document.querySelector('#evtl-data');"
                     "if(nd&&window.__evtlUpdate&&(!live||"
                     "live.textContent!==nd.textContent)){"
                     "if(live)live.textContent=nd.textContent;"
                     "window.__evtlUpdate(JSON.parse(nd.textContent));}"
                     "}catch(e){}},5000);</script>")
        else:
            body = render_index(journal, sessions, read_watch())
        # live 更新一律走 fetch 局部替換(index 表身/統計卡、ticket main),
        # 不再整頁 meta refresh(W4.1)
        page = (f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='theme-color' content='#F1EEE6' media='(prefers-color-scheme:light)'><meta name='theme-color' content='#1E1C19' media='(prefers-color-scheme:dark)'>"
                f"<title>ARCP Detail{_TITLE_TAIL}</title><style>{CSS}</style></head>"
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
