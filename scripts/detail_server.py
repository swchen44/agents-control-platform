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

Usage(-h 看完整說明;一律用 uv run 執行):
    uv run python scripts/detail_server.py [--port N] [--host H]
        [--runtime DIR] [--control-url URL] [--log-level LEVEL]
  預設 :8788、綁 0.0.0.0(內網開放)、runtime=repo/runtime、control=127.0.0.1:8787。
  全走 CLI flag(不再讀 env;--host 127.0.0.1 鎖本機、--control-url 指向 control API)。
"""

from __future__ import annotations

import html
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter, deque
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:                                    # W6.1 系統資訊(純 stdlib);缺也不擋頁
    from arcp.sysinfo import collect as sysinfo_collect
except Exception:  # noqa: BLE001
    sysinfo_collect = None

# W7.5:Agent Detail 讀 config/config.yaml(設定 + profiles)。憑證在 ~/.env 不在此。
# 路徑一律走 arcp.paths(repo-root 相對),dashboard 在 scripts/ 仍定位得到 config/vendor/runtime。
try:
    from arcp.paths import config_path as _arcp_config_path
    from arcp.paths import runtime_dir as _arcp_runtime_dir
    from arcp.paths import vendor_dir as _arcp_vendor_dir
    _CONFIG_PATH = _arcp_config_path()
    _VENDOR = _arcp_vendor_dir() or os.path.dirname(os.path.abspath(__file__))
    _RUNTIME = _arcp_runtime_dir() or os.path.abspath("./runtime")
except Exception:  # noqa: BLE001  (arcp 缺 → 退回 cwd 相對)
    _CONFIG_PATH = os.environ.get("ARCP_CONFIG", "config.yaml")
    _VENDOR = os.path.dirname(os.path.abspath(__file__))
    _RUNTIME = os.path.abspath("./runtime")

# 預設值(可被 __main__ 的 argparse flag 覆寫;import 時不吃 sys.argv,才不會擋 -h/--flag)。
# 被 import 當模組時(如 e2e_dashboard / 測試)保持預設;實際起服務時 __main__ 用 flag 覆寫。
# 全走 CLI flag,不再讀 env(ARCP_DASH_HOST / ARCP_CONTROL_URL 已移除)。
ROOT = _RUNTIME
PORT = 8788                                              # 8787 讓給 control API
CONTROL = "http://127.0.0.1:8787"                        # --control-url 覆寫
# W6.1:綁定 host,預設 0.0.0.0(內網開放,使用者 2026-08-07 決定;⚠️ dashboard 唯讀
# 但會顯示系統/程序資訊,內網任何人可見)。--host 127.0.0.1 可鎖本機。
HOST = "0.0.0.0"


def _instance_name() -> str:
    """W8.6:此 Control Plane 實例名(config.yaml source.name;多實例分辨用)。
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
# 內網/離線:transcript(cclog)本需從 CDN 載 vis-timeline,已 vendor 到本地(vendor/cclog/vendor)
_VENDOR_DIR = os.path.join(_VENDOR, "cclog", "vendor")
# W6.5:Swagger UI(REST API 文件)也 vendor 到本地(內網不外連 CDN;vendor/swagger-ui)
_SWAGGER_DIR = os.path.join(_VENDOR, "swagger-ui")
# transcript HTML(外部工具產出)硬擋任何外部載入(只允許同源 + 內嵌 + data:)
_CSP_TRANSCRIPT = ("default-src 'none'; script-src 'self' 'unsafe-inline'; "
                   "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                   "font-src 'self' data:; connect-src 'self'")
# _CSP_MAIN / _CSP_DOCS 含 CONTROL,故由 _apply_control() 產(import 時算一次;
# __main__ 用 --control-url 覆寫 CONTROL 後會再算一次,避免烤進舊值)。
# 主頁(我們自寫,已無外部引用)——同樣擋外部,但放行本地 control API 的
# 跨埠 fetch(Evict / 狀態);defense-in-depth,防未來誤加 CDN。
# W6.5:/docs(Swagger UI)專屬——vendored bundle 內含 1 處 new Function(bundled
# lib),需 unsafe-eval;仍只放行同源資產 + 對 control API 的 Try it out。
_CSP_MAIN = _CSP_DOCS = ""       # 由 _apply_control() 填


def _apply_control() -> None:
    """(重)算依賴 CONTROL 的衍生字串。import 時 + __main__ 覆寫 CONTROL 後各呼一次。"""
    global _CSP_MAIN, _CSP_DOCS
    _CSP_MAIN = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
                 "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                 "font-src 'self' data:; connect-src 'self' " + CONTROL)
    _CSP_DOCS = ("default-src 'self'; "
                 "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
                 "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                 "font-src 'self' data:; connect-src 'self' " + CONTROL)


_apply_control()


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


def read_interactions(iid: int) -> list[dict]:
    """該票的一次性請求(interactions:HIL 表單 / 指令台 / budget 增額);舊庫容錯。"""
    db = os.path.join(ROOT, "harness.db")
    out: list[dict] = []
    if not os.path.exists(db):
        return out
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        for r in con.execute("SELECT * FROM interactions WHERE issue_id=?"
                             " ORDER BY created_at", (int(iid),)):
            out.append(dict(r))
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
            "status_cls": _cls,               # W10.1:徽章 class(語意名)
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
    from arcp.kpi import compute_kpi  # C3:KPI 框架
    now = time.time()
    sess_list = list(sessions.values())
    kpi3 = {"all": compute_kpi(journal, sess_list, now=now),
            "d7": compute_kpi(journal, sess_list, now=now,
                              since=now - 7 * 86400),
            "d30": compute_kpi(journal, sess_list, now=now,
                               since=now - 30 * 86400)}
    return {"rows": rows,
            "kpi3": kpi3,
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


def db_schema(name: str) -> dict:
    """表的欄位定義(PRAGMA table_info):即使 0 列也看得到有哪些欄位/型別/預設。
    最近常加欄位(base_ref/human_score/…),schema 視圖方便 debug 對照。"""
    if name not in {t["name"] for t in db_tables()}:   # 白名單=真實表名
        return {"error": "no such table"}
    con = _db_ro()
    try:
        cols = [{"name": r[1], "type": r[2] or "", "notnull": bool(r[3]),
                 "default": r[4], "pk": bool(r[5])}
                for r in con.execute(f'PRAGMA table_info("{name}")')]
        return {"table": name, "columns": cols}
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
.SUCCESS,.success{color:var(--s-success);background:color-mix(in srgb,var(--s-success) 12%,transparent);border-color:color-mix(in srgb,var(--s-success) 30%,transparent)}
.FAILURE,.failure{color:var(--s-failure);background:color-mix(in srgb,var(--s-failure) 12%,transparent);border-color:color-mix(in srgb,var(--s-failure) 30%,transparent)}
/* W10.1:HIL(End)·未定 灰、HIL(Middle) 琥珀、撤銷 用 aborted 色 */
.UNKNOWN,.unknown{color:var(--s-inactive);background:color-mix(in srgb,var(--s-inactive) 14%,transparent);border-color:color-mix(in srgb,var(--s-inactive) 30%,transparent)}
.ABORTED,.aborted{color:var(--s-aborted);background:color-mix(in srgb,var(--s-aborted) 14%,transparent);border-color:color-mix(in srgb,var(--s-aborted) 30%,transparent)}
.pending,.hilmid{color:var(--s-pending);background:color-mix(in srgb,var(--s-pending) 14%,transparent);border-color:color-mix(in srgb,var(--s-pending) 32%,transparent)}
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
/* W9.3 兩層分類:類別列(對話/生命週期)粗體 accent;子列略縮排 */
.vis-labelset .vis-label.vis-nesting-group{font-weight:700;color:var(--accent-ink)}
.vis-labelset .vis-label.vis-nested-group .vis-inner{padding-left:6px}
/* W9.2 時間軸浮動鈕 + 抽屜(仿 transcript;右下角開/關) */
#tlfab{position:fixed;right:18px;bottom:18px;z-index:30;background:var(--accent);
  color:#fff;border:none;border-radius:24px;padding:11px 18px;cursor:pointer;
  box-shadow:var(--shadow);font-family:var(--font-body);font-size:13px;font-weight:500}
#tlfab:hover{filter:brightness(1.08)}
#tlwrap{display:none;position:fixed;left:16px;right:16px;bottom:68px;z-index:29;
  max-height:72vh;overflow:auto;background:var(--panel);border:1px solid var(--line);
  border-radius:12px;box-shadow:var(--shadow);padding:14px 18px}
#tlwrap.on{display:block}
#tlwrap #evtl{background:var(--bg);border:1px solid var(--line);
  border-radius:8px}
/* C5 粗看:全域時間軸(/timeline)——色帶沿用六態色票 + 側欄 + 說明卡 */
.vis-item.vis-background.ov-run{background:var(--s-running);opacity:.30}
.vis-item.vis-background.ov-wait{background:var(--s-pending);opacity:.40}
.vis-item.vis-background.ov-queue{background:var(--s-queued);opacity:.30}
.vis-item.vis-background.ov-idle{background:var(--s-inactive);opacity:.14}
.vis-item.ov-ev{background:transparent;border:none;font-size:13px;color:var(--ink)}
.vis-item.ov-end-ok{background:transparent;border:none;font-weight:700;
  font-size:15px;color:var(--s-success)}
.vis-item.ov-end-bad{background:transparent;border:none;font-weight:700;
  font-size:15px;color:var(--s-failure)}
.ovp{color:var(--muted);font-size:11px;font-weight:400}
#ovtl{padding:6px}
#ovwrap{display:flex;gap:14px;align-items:flex-start}
#ovwrap>#ovtl{flex:1;min-width:0}
#ovside{width:310px;flex:none;display:none;position:sticky;top:10px;
  overflow-wrap:anywhere}
#ovside.on{display:block}
#ovside .mono{font-family:var(--font-mono);font-size:11px}
.ovkv{width:100%;border-collapse:collapse}
.ovkv td{padding:3px 6px 3px 0;vertical-align:top;border-bottom:1px solid var(--line)}
.ovkv td:first-child{color:var(--muted);white-space:nowrap;width:72px}
.ovgo{font-weight:600}
.ovbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.ovwin a{display:inline-block;padding:3px 10px;border:1px solid var(--line-2);
  border-radius:14px;margin-right:4px;font-size:12.5px}
.ovwin a.on{background:var(--accent);color:#fff;border-color:var(--accent)}
.howto summary{cursor:pointer;font-weight:600;color:var(--accent-ink)}
.howto .lg{display:inline-block;width:14px;height:10px;border-radius:2px;
  margin:0 4px 0 10px;vertical-align:baseline}
.howto kbd{border:1px solid var(--line-2);border-radius:4px;padding:0 4px;
  font-family:var(--font-mono);font-size:11px}
.ckpre{max-height:420px;overflow:auto;background:var(--panel-2);
  border:1px solid var(--line);border-radius:8px;padding:10px;
  font-size:12px;white-space:pre-wrap}
@media (max-width:840px){#ovwrap{flex-direction:column}#ovside{width:100%;
  position:static}}
/* W8.2 狀態機 SVG(用 CSS 上色,隨明暗主題;--nc=節點色) */
#smsvg .sm-box{fill:var(--panel);stroke:var(--nc,var(--line-2));stroke-width:1.6}
#smsvg .sm-lbl{fill:var(--nc,var(--ink));font-weight:600}
#smsvg .sm-edge{stroke:var(--muted)}
#smsvg .sm-elabel{fill:var(--muted)}
#smsvg .sm-arrow{fill:var(--muted)}
#smsvg .st-todo{--nc:var(--s-todo)}#smsvg .st-running{--nc:var(--s-running)}
#smsvg .st-queued{--nc:var(--s-queued)}#smsvg .st-hil_middle{--nc:var(--s-pending)}
#smsvg .st-hil_end{--nc:var(--s-success)}#smsvg .st-aborted{--nc:var(--s-aborted)}
#smsvg .st-closed{--nc:var(--s-queued)}
#smsvg .st-exit{--nc:var(--accent)}
/* W10.4 模組架構圖(分層;隨明暗主題) */
#archsvg .a-band{fill:color-mix(in srgb,var(--accent) 5%,transparent);stroke:var(--line)}
#archsvg .a-rail{fill:color-mix(in srgb,var(--accent) 78%,var(--panel))}
#archsvg .a-rname{fill:#fff;font-weight:700;font-size:12px}
#archsvg .a-rdesc{fill:color-mix(in srgb,#fff 82%,transparent);font-size:10px}
#archsvg .a-chip{fill:var(--panel);stroke:var(--line-2);stroke-width:1.3}
#archsvg .a-name{fill:var(--ink);font-weight:600}
#archsvg .a-flow{stroke:var(--muted);stroke-width:1.6}
#archsvg .a-arrow{fill:var(--muted)}
/* W10.7 模組 graph 圖(node+edge)+ 多選過濾器 + focus 高亮 */
#graphsvg .gnode rect{fill:var(--panel);stroke:var(--line-2);stroke-width:1.4;cursor:pointer}
#graphsvg .gnode text{fill:var(--ink);font-weight:600;pointer-events:none}
#graphsvg .gnode.gl-0 rect{stroke:var(--s-running)}
#graphsvg .gnode.gl-1 rect{stroke:var(--s-queued)}
#graphsvg .gnode.gl-2 rect{stroke:var(--s-success)}
#graphsvg .gnode.gl-3 rect{stroke:var(--s-pending)}
#graphsvg .gnode.gl-4 rect{stroke:var(--s-inactive)}
#graphsvg .gnode.foc rect{stroke:var(--accent);stroke-width:2.6}
#graphsvg .gedge line{stroke:var(--muted);stroke-width:1;opacity:.65}
#graphsvg .gedge text{fill:var(--faint);font-size:9px}
#graphsvg .g-arrow{fill:var(--muted)}
#graphsvg .dim{opacity:.08}
#graphsvg .gedge.hi line{stroke:var(--accent);stroke-width:2;opacity:1}
#graphsvg .gedge.hi text{fill:var(--accent-ink);font-weight:600}
.gfilter{margin-bottom:10px}
.gfilter .gbtns{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:6px}
.gfilter .glayer{display:flex;gap:8px 10px;align-items:center;flex-wrap:wrap;margin:3px 0}
.gfilter .glname{font-weight:600;color:var(--accent-ink);background:none;
  border:1px solid var(--line);border-radius:6px;padding:2px 8px;cursor:pointer}
.gfilter .gchk{font-size:12px;color:var(--muted);display:inline-flex;gap:3px;align-items:center}
.gfilter .gbtns button{cursor:pointer;border:1px solid var(--line);border-radius:6px;
  background:var(--panel);color:var(--ink);padding:2px 10px}
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


# W10.1:HIL(End) 結果 / HIL(Middle) 原因 → 中文短語(徽章顯示用)
_HILEND_RESULT = {"SUCCESS": "成功", "FAILURE": "失敗", "UNKNOWN": "未定"}
_HILMID_REASON = {
    "awaiting-approval": "審批", "approval": "審批", "triage": "待審視",
    "budget": "預算", "budget-cap": "預算", "max-attempts": "重試上限",
    "handoff": "換手", "external": "外部變更",
}


def session_status(s: dict, qpos: dict[int, int]) -> tuple[str, str]:
    """→ (徽章文字, css class)。W10.1 HIL 模型:success/failure/unknown 顯示成
    「HIL(End)·結果」;inactive+pending 併成「HIL(Middle)·原因」。class 用小寫語意名
    (success/failure/unknown/hilmid/queued/running/aborted),與 badgeCls / CSS 對齊。"""
    oc = s.get("outcome")
    if oc == "ABORTED":
        return "撤銷", "aborted"
    if oc in ("SUCCESS", "FAILURE", "UNKNOWN"):
        return (f"HIL(End)·{_HILEND_RESULT[oc]}",
                {"SUCCESS": "success", "FAILURE": "failure",
                 "UNKNOWN": "unknown"}[oc])
    pr = s.get("pending_reason")
    if pr:
        rs = _HILMID_REASON.get(pr, pr)
        return f"HIL(Middle)·{rs}", "hilmid"
    if s.get("queued"):
        return f"排隊 #{qpos.get(s.get('issue_id'), '?')}", "queued"
    if s.get("inactive"):
        return "HIL(Middle)·交人", "hilmid"
    return "進行中", "running"


# canonical_state 已抽到 arcp.lifecycle_state(單一真相來源;dashboard/console/
# commands 共用)。此處 re-export 保留 detail_server.canonical_state 名稱相容。
from arcp.lifecycle_state import canonical_state  # noqa: E402


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
    succ, fail, unk = (oc.get("SUCCESS", 0), oc.get("FAILURE", 0),
                       oc.get("UNKNOWN", 0))
    done = succ + fail
    fail_rate = f"{fail / done * 100:.0f}%" if done else "–"
    in_flight = sum(1 for s in vals
                    if not s.get("outcome") and not s.get("pending_reason")
                    and not s.get("queued") and not s.get("inactive"))
    live = [s for s in vals if not s.get("outcome")]
    # W10.1 HIL 模型:hil_middle = 交人(inactive)+ 過程中 pending;hil_end =
    # 終態評分中(成功/失敗/未定)。失敗率仍由 outcome 直接算(獨立於顯示態)。
    hil_middle = sum(1 for s in live
                     if s.get("inactive") or s.get("pending_reason"))
    hil_end = succ + fail + unk
    total_cost = sum(s.get("cost_usd") or 0 for s in vals)
    stats = [
        (f"${total_cost:.4f}", "總 cost"),
        (in_flight, "進行中"),
        (sum(1 for s in live if s.get("queued")), "排隊"),
        (hil_middle, "HIL(Middle)"),
        (hil_end, "HIL(End)"),
        (succ, "成功"), (fail, "失敗"), (fail_rate, "失敗率"),
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
    "const CTL=__CTL__;"          # __CTL__ 於 render 時代入(讀當前 CONTROL,見 line 891)
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
    "const t=[[j.degraded?'⚠ Jira 降級':(j.paused?'⏸ 暫停':"
    "(j.stopping?'⏻ 關閉中':'▶ 運行')),'狀態'],"
    "[(j.poll_count||0),'已 poll 次數'],[upTxt,'連續運行'],"
    "[(j.poll_interval?j.poll_interval+'s':'—'),'poll 間隔'],"
    # W10.1 HIL 模型:in-flight=進行中;inactive+pending 併顯為 HIL(Middle)
    "[j.in_flight,'進行中'],[j.queued,'排隊'],"
    "[((j.inactive||0)+(j.pending?Object.values(j.pending)"
    ".reduce((a,b)=>a+b,0):0)),'HIL(Middle)'],"
    "['$'+(j.cost_usd||0).toFixed(4),'總 cost'],[j.sessions,'sessions']];"
    "$c('cstatus').innerHTML=t.map(x=>`<div class='stat'><div class='n'>`+"
    "`${x[0]}</div><div class='l'>${x[1]}</div></div>`).join('');"
    "}catch(e){$c('cstatus').innerHTML=\"<span style='color:var(--s-failure)'>"
    "control API 離線(\"+CTL+\")——poller 未啟動?</span>\";}}"
    "poll();setInterval(poll,3000);"
    "</script>")


def _light(value, yellow, red, reverse=False) -> str:
    """value 對門檻回燈色。reverse=False:越大越糟(≥red 紅、≥yellow 黃)。"""
    if value is None:
        return "gray"
    if not reverse:
        return "red" if value >= red else ("yellow" if value >= yellow else "green")
    return "red" if value <= red else ("yellow" if value <= yellow else "green")


def perf_metrics(journal: list[dict], sessions: dict, watch: dict,
                 sysinfo: dict | None, journal_bytes: int,
                 now: float | None = None, budget: dict | None = None) -> dict:
    """Q5 效能指標(紅黃綠燈)+ per-profile 細節。純函式(可離線單測)。
    全用內部資料:journal / ticket_session / ticket_watch / sysinfo / journal 大小。"""
    now = time.time() if now is None else now
    HR = 3600.0
    fin = [e for e in journal if e.get("type") == "attempt_finished"]
    recent_fin = fin[-50:]
    fails = sum(1 for e in recent_fin
                if e.get("raw") in ("error", "unknown"))
    fail_rate = (100.0 * fails / len(recent_fin)) if recent_fin else 0.0

    queued = sum(1 for s in sessions.values() if s.get("queued"))
    evict_1h = sum(1 for e in journal if e.get("type") == "evicted"
                   and now - e.get("ts", 0) <= HR)
    cost_1h = sum(float(e.get("cost") or 0) for e in fin
                  if now - e.get("ts", 0) <= HR)
    err_1h = sum(1 for e in journal
                 if e.get("type") in ("dispatch_error", "trigger_error")
                 and now - e.get("ts", 0) <= HR)

    # 最舊未終態票等待(小時):非 SUCCESS/ABORTED 的 session,取 watch first_seen 最早
    open_ages = []
    for iid, s in sessions.items():
        if s.get("outcome") in ("SUCCESS", "ABORTED"):
            continue
        fs = (watch.get(iid) or {}).get("first_seen_ts")
        if fs:
            open_ages.append((now - fs) / HR)
    oldest_h = max(open_ages) if open_ages else 0.0

    # 系統資源:mem/disk/cpu-load 取最糟 %
    res_pct = None
    res = (sysinfo or {}).get("resources") or {}
    pcts = []
    mem = res.get("mem") or {}
    if mem.get("total"):
        pcts.append(100.0 * mem.get("used", 0) / mem["total"])
    disk = res.get("disk") or {}
    if disk.get("total"):
        pcts.append(100.0 * disk.get("used", 0) / disk["total"])
    la = res.get("loadavg") or []
    if la and res.get("cpus"):
        pcts.append(100.0 * la[0] / res["cpus"])
    if pcts:
        res_pct = max(pcts)

    jmb = journal_bytes / 1e6
    ind = [
        {"key": "fail_rate", "label": "attempt 失敗率(近 50)",
         "value": f"{fail_rate:.0f}%", "light": _light(fail_rate, 10, 30)},
        {"key": "queue", "label": "排隊深度",
         "value": str(queued), "light": _light(queued, 1, 6)},
        {"key": "oldest", "label": "最舊未終態票等待",
         "value": f"{oldest_h:.1f}h", "light": _light(oldest_h, 1, 24)},
        {"key": "evict", "label": "evict 次數(近 1h)",
         "value": str(evict_1h), "light": _light(evict_1h, 1, 4)},
        {"key": "cost", "label": "花費速率(近 1h)",
         "value": f"${cost_1h:.2f}", "light": _light(cost_1h, 1, 5)},
        {"key": "errors", "label": "錯誤事件(近 1h)",
         "value": str(err_1h), "light": _light(err_1h, 1, 4)},
        {"key": "sysres", "label": "系統資源(最糟)",
         "value": (f"{res_pct:.0f}%" if res_pct is not None else "—"),
         "light": _light(res_pct, 70, 90)},
        {"key": "journal", "label": "journal 大小",
         "value": f"{jmb:.0f}MB", "light": _light(jmb, 50, 200)},
    ]

    # budget:當月用量對月上限的最高利用率(綠<80% 黃≥80% 紅≥100%);全站 + 各 profile
    import datetime
    _ref = datetime.datetime.fromtimestamp(now)

    def _msum(field, profile=None):
        t = 0.0
        for e in fin:
            if not e.get(field):
                continue
            if profile is not None and e.get("profile") != profile:
                continue
            edt = datetime.datetime.fromtimestamp(e.get("ts") or 0)
            if edt.year == _ref.year and edt.month == _ref.month:
                t += float(e[field])
        return t

    util, worst, any_cap = 0.0, "—", False
    if budget:
        g = budget.get("global") or {}
        checks = [("全站$", _msum("cost"), g.get("monthly_max_usd")),
                  ("全站tok", _msum("tokens"), g.get("monthly_max_tokens"))]
        for nm, caps in (budget.get("profiles") or {}).items():
            checks.append((f"{nm}$", _msum("cost", nm),
                           (caps or {}).get("monthly_max_usd")))
            checks.append((f"{nm}tok", _msum("tokens", nm),
                           (caps or {}).get("monthly_max_tokens")))
        for label, used, cap in checks:
            if cap:
                any_cap = True
                pct = 100.0 * used / cap
                if pct > util:
                    util, worst = pct, label
    ind.append({
        "key": "budget", "label": "budget 月用量(最高)",
        "value": (f"{util:.0f}% {worst}" if any_cap else "—(無月上限)"),
        "light": _light(util, 80, 100) if any_cap else "gray"})

    # per-profile 細節:attempts / 失敗率 / 平均時長 / 累計$ / 最後活動
    starts = {}          # (issue,attempt) → start ts
    for e in journal:
        if e.get("type") == "attempt_started":
            starts[(e.get("issue_id"), e.get("attempt"))] = e.get("ts")
    prof: dict = {}
    for e in fin:
        p = e.get("profile") or "-"
        d = prof.setdefault(p, {"attempts": 0, "fails": 0, "cost": 0.0,
                                "durs": [], "last": 0.0})
        d["attempts"] += 1
        if e.get("raw") in ("error", "unknown"):
            d["fails"] += 1
        d["cost"] += float(e.get("cost") or 0)
        d["last"] = max(d["last"], e.get("ts", 0))
        st = starts.get((e.get("issue_id"), e.get("attempt")))
        if st and e.get("ts"):
            d["durs"].append(e["ts"] - st)
    profiles = []
    for p, d in sorted(prof.items()):
        avg = (sum(d["durs"]) / len(d["durs"])) if d["durs"] else None
        profiles.append({
            "profile": p, "attempts": d["attempts"],
            "fail_rate": round(100.0 * d["fails"] / d["attempts"]) if d["attempts"] else 0,
            "avg_sec": round(avg) if avg is not None else None,
            "cost": round(d["cost"], 4), "last": d["last"]})
    return {"indicators": ind, "profiles": profiles}


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
            "<button type='button' class='btn' onclick=\"cc('recover')\" "
            "title='Jira 異常自動降級;恢復後可自動解除,或按此手動解除'>"
            "🩺 Recover</button>"
            "<button type='button' class='btn' id='sd' style='color:var(--s-failure)' "
            "onclick=\"cc('shutdown')\">⏻ Graceful Shutdown</div>"
            "<span id='cmsg' aria-live='polite' style='color:var(--muted);font-size:12px'></span>"
            "</div>"
            "<p style='color:var(--muted);font-size:12px'>"
            "Pause=只 watch 不派新工(正在跑的不中斷);Reload=熱載 config.yaml"
            "(壞 config 不生效、舊設定續用);Graceful Shutdown=當前輪(含壓縮"
            "打包)跑完後 poller 退出。詳見 docs/design/hotreload.md。即時 kill 單張"
            "票用 ticket 頁的 Evict。</p></main>"
            + _CONTROL_JS.replace("__CTL__", json.dumps(CONTROL)))  # 代入當前 CONTROL


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
    """W6.1/6.2/6.6 Server 頁單一資料源(+ Q5 效能指標)。"""
    data = {"sys": sysinfo_collect() if sysinfo_collect else None}
    # Q5:效能監控(紅黃綠燈 + per-profile 細節),整合進 Server 頁
    _jp = os.path.join(ROOT, "events.jsonl")
    _jb = os.path.getsize(_jp) if os.path.exists(_jp) else 0
    _budget = None
    try:                                    # budget 燈:載 config 的月/全站上限
        from arcp.profiles import load_profiles
        from arcp.routing import load_config
        _src, _ = load_config(_CONFIG_PATH)
        _profs = load_profiles(_CONFIG_PATH)
        _budget = {"global": _src.get("budget") or {},
                   "profiles": {n: {"monthly_max_usd": p.monthly_max_usd,
                                    "monthly_max_tokens": p.monthly_max_tokens}
                                for n, p in _profs.items()}}
    except Exception:                       # noqa: BLE001 — 壞 config 不擋頁
        _budget = None
    data["perf"] = perf_metrics(read_journal(), read_sessions(), read_watch(),
                                data["sys"], _jb, budget=_budget)
    data["conns"] = list(_CONNS)[-30:][::-1]        # W6.6 近期連線(新→舊)
    # W6.2:進程 + per-workspace(只掃 active session,省成本)
    procs = []
    try:
        from arcp.sysinfo import processes
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
    # Q5 效能監控(紅黃綠燈 + per-profile 細節),整合在 Server 頁
    "const pf=d.perf||{indicators:[],profiles:[]};"
    "const lc={green:'var(--s-success)',yellow:'var(--s-pending)',"
    "red:'var(--s-failure)',gray:'var(--muted)'};"
    "const dot=c=>`<span style='color:${lc[c]||lc.gray}'>●</span>`;"
    "h+='<h2>效能監控(Performance)</h2>';"
    "h+=\"<div class='stats'>\"+pf.indicators.map(i=>"
    "`<div class='stat' style='border-left:3px solid ${lc[i.light]||lc.gray}'>"
    "<div class='n'>${dot(i.light)} ${esc(i.value)}</div>"
    "<div class='l'>${esc(i.label)}</div></div>`).join('')+'</div>';"
    "h+=\"<p style='color:var(--muted);font-size:12px'>瓶頸幾乎都在 ① agent 執行時長"
    "(model,非 ARCP)② Jira API 延遲/降級 ③ 並發飽和(排隊)。看上面的燈 + 下方各 "
    "profile 時長/$ 找熱點。<b>budget 月用量</b>燈黃≥80%/紅≥100%(有 profile 或全站達"
    "月上限→票 pending:budget;調設定 + hot reload)。詳見 Agent Detail 頁用量卡。</p>\";"
    "if(pf.profiles.length){h+='<h2>各 profile 效能</h2>';"
    "h+=\"<style>.ptbl td,.ptbl th{border-bottom:1px solid var(--line);"
    "padding:4px 8px;text-align:left;font-size:13px}</style>\";"
    "h+=\"<table style='width:100%;border-collapse:collapse' class='ptbl'>"
    "<tr><th>profile</th><th>attempts</th><th>失敗率</th><th>平均時長</th>"
    "<th>累計$</th><th>最後活動</th></tr>\"+pf.profiles.map(p=>"
    "`<tr><td>${esc(p.profile)}</td><td>${p.attempts}</td><td>${p.fail_rate}%</td>"
    "<td>${p.avg_sec!=null?dur(p.avg_sec):'—'}</td><td>$${p.cost}</td>"
    "<td>${p.last?new Date(p.last*1000).toLocaleString():'—'}</td></tr>`).join('')"
    "+'</table>';}"
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
  rx:false,size:20,page:0,sort:'created',dir:-1,wk1:false,wk2:false,rate:null},
  (()=>{try{return JSON.parse(localStorage.getItem(LS))||{}}catch(e){return{}}})());
// W8.5:URL query 反映過濾/排序/分頁狀態(可深連/分享);載入時 URL 優先於 localStorage。
const _URLK=['qr','from','to','st','kprofile','ksum','kdesc','rx','sort','dir',
  'page','size'];
(function(){const q=new URLSearchParams(location.search);
  _URLK.forEach(k=>{if(q.has(k)){const v=q.get(k);
    S[k]=(k==='dir'||k==='page'||k==='size')?(+v):(k==='rx')?(v==='1'||v==='true'):v;}});})();
function save(){localStorage.setItem(LS,JSON.stringify(S));
  const q=new URLSearchParams();
  _URLK.forEach(k=>{const d={qr:'all',sort:'created',dir:-1,page:0,size:20,rx:false};
    if(S[k]!==''&&S[k]!=null&&S[k]!==d[k])q.set(k,k==='rx'?(S[k]?'1':'0'):S[k]);});
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
  // W10.1 HIL 模型:6 態 canonical(todo/running/queued/hil_middle/hil_end/
  // aborted),per-profile 圖再把 hil_end 依 outcome 拆成 成功/失敗/未定(保留
  // 每 profile 的失敗可視);每格帶 predicate(r)判定歸屬。
  STATE8=[
    ['todo',['待處理',cssv('--s-todo'),r=>r.state==='todo']],
    ['running',['進行中',cssv('--s-running'),r=>r.state==='running']],
    ['queued',['排隊',cssv('--s-queued'),r=>r.state==='queued']],
    ['hil_middle',['HIL·過程中',cssv('--s-pending'),r=>r.state==='hil_middle']],
    ['hil_success',['HIL·成功',cssv('--s-success'),
      r=>r.state==='hil_end'&&r.outcome==='SUCCESS']],
    ['hil_failure',['HIL·失敗',cssv('--s-failure'),
      r=>r.state==='hil_end'&&r.outcome==='FAILURE']],
    ['hil_unknown',['HIL·未定',cssv('--s-inactive'),
      r=>r.state==='hil_end'&&r.outcome==='UNKNOWN']],
    ['aborted',['撤銷',cssv('--s-aborted'),r=>r.state==='aborted']]];
}
// ---- 過濾(置頂,統管全部) ----
// 二選一比對器:S.rx 勾 → 正則(不分大小寫);否 → 一般字串包含(不分大小寫)。
// 無效正則 → 回 null(該格視為不過濾)+ 標紅提示,不讓畫面整個空掉。
function matcher(pat){
  if(!pat)return null;
  if(S.rx){try{const re=new RegExp(pat,'i');return s=>re.test(s||'');}
    catch(e){return null;}}
  const low=pat.toLowerCase();return s=>(s||'').toLowerCase().includes(low);
}
function _rxBad(pat){if(!pat||!S.rx)return false;
  try{new RegExp(pat,'i');return false;}catch(e){return true;}}
function filtered(){
  const now=Date.now()/1000;
  let lo=0,hi=Infinity;
  if(S.from){lo=new Date(S.from+'T00:00:00').getTime()/1000;}
  if(S.to){hi=new Date(S.to+'T23:59:59').getTime()/1000;}
  if(!S.from&&!S.to&&S.qr!=='all'){lo=now-(+S.qr)*86400;}
  const mp=matcher(S.kprofile),ms=matcher(S.ksum),md=matcher(S.kdesc);
  return D.rows.filter(r=>
    r.created>=lo&&r.created<=hi&&
    (!S.st||r.status===S.st)&&
    (!mp||mp(r.profile||''))&&
    (!ms||ms(r.key+' '+r.summary))&&
    (!md||md(r.desc||'')));
}
// ---- 統計卡 ----
function renderStats(rows){
  // W10.1 HIL 模型:改用 canonical r.state 計數(原本靠 status 文字前綴,
  // 中文化後失效);失敗率仍由 outcome 直接算。
  const cost=rows.reduce((a,r)=>a+r.cost,0);
  const oc=o=>rows.filter(r=>r.outcome===o).length;
  const sc=k=>rows.filter(r=>r.state===k).length;
  const succ=oc('SUCCESS'),fail=oc('FAILURE'),done=succ+fail;
  const mins=rows.reduce((a,r)=>a+r.human_min,0);
  const t=[[money(cost),'總 cost'],[sc('running'),'進行中'],
    [sc('queued'),'排隊'],[sc('hil_middle'),'HIL(Middle)'],
    [sc('hil_end'),'HIL(End)'],[succ,'成功'],[fail,'失敗'],
    [done?Math.round(fail/done*100)+'%':'–','失敗率']];
  if(mins){t.push([(mins/60).toFixed(1)+'h','節省人時']);
    if(S.rate)t.push(['$'+Math.round(mins/60*S.rate)+' vs $'+cost.toFixed(2),
      '人力成本對比']);}
  $('stats').innerHTML=t.map(([n,l])=>
    `<div class='stat'><div class='n'>${n}</div><div class='l'>${l}</div></div>`).join('');
}
// ---- C3 KPI(北極星+制衡;v5 §10:只建基線、效率配制衡)----
function kfmt(v,suf){return v==null?'–':v+(suf||'');}
function renderKpi3(){const K=D.kpi3;if(!K)return;
 const tr=[['d7','7 天'],['d30','30 天'],['all','全歷史']].map(([k,l])=>{
   const n=K[k].north_star,e=K[k].efficiency,g=K[k].guard;
   return `<tr><td><b>${l}</b></td>`+
     `<td><b>${kfmt(n.first_pass_close_rate_strict,'%')}</b> (${n.first_pass}/${n.closed})</td>`+
     `<td>${kfmt(n.first_pass_close_rate_progress,'%')} (/${n.resolved})</td>`+
     `<td>${kfmt(e.cycle_time_min_med,'m')} / ${kfmt(e.cycle_time_min_p90,'m')}</td>`+
     `<td>${kfmt(e.attempts_per_close_med)}</td>`+
     `<td>${e.cost_per_close_med==null?'–':'$'+e.cost_per_close_med}</td>`+
     `<td>${kfmt(g.continue_rate,'%')}</td>`+
     `<td>${kfmt(g.human_score_med)} (n=${g.human_score_n})</td>`+
     `<td>${kfmt(g.unknown_rate,'%')}</td>`+
     `<td>${kfmt(g.abandonment_rate,'%')}</td></tr>`;}).join('');
 const ab=Object.entries(K.all.guard.abort_reasons||{})
   .map(([k,v])=>k+'×'+v).join(' · ')||'–';
 $('kpi3').innerHTML=`<h2>KPI · 北極星+制衡(C3;P1 只建基線不設目標)</h2>
 <div class='card' style='overflow-x:auto'><table><thead><tr>
 <td><b>窗</b></td><td><b>First-pass close(嚴格)</b></td>
 <td><b>進行版(÷終態)</b></td><td><b>Cycle med/p90</b></td>
 <td><b>Attempts</b></td><td><b>$/close</b></td><td><b>打回率</b></td>
 <td><b>人評 med</b></td><td><b>UNKNOWN</b></td><td><b>放棄率</b></td>
 </tr></thead><tbody>${tr}</tbody></table>
 <div class='sys' style='text-align:left'>⚖️ 制衡(v5 §10.5):First-pass 升但
 人評/打回率變差 = 在「調鬆 verify」作弊;UNKNOWN 率勿單看(壓低它最快的
 方法是誤判成 FAILURE)。中止原因:${ab}。automation coverage
 ${kfmt(K.all.coverage.automation_coverage,'%')}(routed
 ${K.all.coverage.routed}/${K.all.coverage.new_issues})。
 一次到位 = 無 retry / 打回(continue)/ 換手。</div></div>`;}
// ---- C6 A/B 對照(手選 profiles 比 KPI;非隨機分流僅供參考)----
let AB_SEL=new Set(),AB_CACHE={};
function renderAbBar(){const profs=[...new Set(D.rows.map(r=>r.profile).filter(Boolean))].sort();
 if(profs.length<2){$('ab').innerHTML='';return;}
 const boxes=profs.map(p=>`<label style='margin-right:10px;font-weight:400'>
  <input type='checkbox' ${AB_SEL.has(p)?'checked':''} onchange='abToggle("${p}")'> ${p}</label>`).join('');
 $('ab').innerHTML=`<h2>A/B 對照(選 2+ 個 profile 比 KPI)</h2>
  <div class='card'><div>${boxes}</div><div id='abtbl' style='overflow-x:auto;margin-top:8px'></div>
  <div class='sys' style='text-align:left'>⚠️ 手選 profile 的對照<b>非隨機分流</b>
  ——差異可能來自任務不同質,僅供參考;樣本小(n&lt;10)差異無意義。同一
  select 家族隨機分流的腿才是統計可比的真 A/B。</div></div>`;
 renderAbTbl();}
async function abToggle(p){AB_SEL.has(p)?AB_SEL.delete(p):AB_SEL.add(p);
 if(!AB_CACHE[p]&&AB_SEL.has(p)){
  try{AB_CACHE[p]=await (await fetch('/api/v1/kpi?profile='+encodeURIComponent(p))).json();}
  catch(e){AB_CACHE[p]=null;}}
 renderAbTbl();}
function renderAbTbl(){const el=$('abtbl');if(!el)return;
 const sel=[...AB_SEL];
 if(sel.length<2){el.innerHTML="<span style='color:var(--muted)'>(勾選 2 個以上開始對照)</span>";return;}
 const rows=[["First-pass(嚴格)",k=>kfmt(k.north_star.first_pass_close_rate_strict,'%')+` (${k.north_star.first_pass}/${k.north_star.closed})`],
  ["First-pass(進行)",k=>kfmt(k.north_star.first_pass_close_rate_progress,'%')],
  ["Cycle med/p90(分)",k=>kfmt(k.efficiency.cycle_time_min_med)+' / '+kfmt(k.efficiency.cycle_time_min_p90)],
  ["Attempts/close",k=>kfmt(k.efficiency.attempts_per_close_med)],
  ["$/close med",k=>k.efficiency.cost_per_close_med==null?'–':'$'+k.efficiency.cost_per_close_med],
  ["打回率",k=>kfmt(k.guard.continue_rate,'%')],
  ["人評 med(n)",k=>kfmt(k.guard.human_score_med)+` (n=${k.guard.human_score_n})`],
  ["UNKNOWN 率",k=>kfmt(k.guard.unknown_rate,'%')],
  ["放棄率",k=>kfmt(k.guard.abandonment_rate,'%')],
  ["樣本(終態/closed)",k=>`${k.north_star.resolved} / ${k.north_star.closed}`]];
 const head='<tr><td></td>'+sel.map(p=>`<td><b>${p}</b></td>`).join('')+'</tr>';
 const body=rows.map(([l,f])=>'<tr><td><b>'+l+'</b></td>'+sel.map(p=>{
   const k=AB_CACHE[p];return '<td>'+(k?f(k):'…')+'</td>';}).join('')+'</tr>').join('');
 el.innerHTML=`<table><thead>${head}</thead><tbody>${body}</tbody></table>`;}
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
    rs=>STATE8.map(([k,[lb,c,fn]])=>({c,label:lb,
      v:rs.filter(fn).length})),v=>Math.round(v),{minTick:1});
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
// W10.1:徽章 class 直接用後端算好的 r.status_cls(不再解析中文文字);保留舊
// badgeCls 作為容錯 fallback(僅認得的英文碼)
function badgeCls(st){return {SUCCESS:'success',FAILURE:'failure',UNKNOWN:'unknown',
  ABORTED:'aborted'}[st]||'';}
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
    `<td><span class='badge ${r.status_cls||badgeCls(r.status)}'>${esc(r.status)}</span></td>`+
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
function render(){syncPalette();prep();const rows=filtered();renderStats(rows);renderKpi3();renderAbBar();var _u=$('upd');if(_u)_u.textContent='更新於 '+_TM.format(new Date());
  renderTime(rows);renderMoney(rows);
  renderPState(rows);renderPCost(rows);renderPScore(rows);   // W7.4 per-profile
  renderTable(rows);markRx();save();}
// 正則模式下,把無效 pattern 的關鍵字框標紅(border),提示使用者修正
function markRx(){[['kprofile',S.kprofile],['ksum',S.ksum],['kdesc',S.kdesc]]
  .forEach(([id,v])=>{const el=$(id);if(!el)return;
    el.style.borderColor=_rxBad(v)?'var(--s-failure)':'';
    el.title=_rxBad(v)?'無效的正則(regex);此格暫不套用過濾':'';});}
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
  if($('rx'))$('rx').checked=!!S.rx;
  if(S.rate!=null)$('rate').value=S.rate;
  $('qr').onchange=e=>{S.qr=e.target.value;S.page=0;render();};
  $('from').onchange=e=>{S.from=e.target.value;S.page=0;render();};
  $('to').onchange=e=>{S.to=e.target.value;S.page=0;render();};
  $('st').onchange=e=>{S.st=e.target.value;S.page=0;render();};
  $('ksum').oninput=e=>{S.ksum=e.target.value;S.page=0;render();};
  $('kdesc').oninput=e=>{S.kdesc=e.target.value;S.page=0;render();};
  $('kprofile').oninput=e=>{S.kprofile=e.target.value;S.page=0;render();};
  if($('rx'))$('rx').onchange=e=>{S.rx=e.target.checked;S.page=0;render();};
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
            + tab("timeline", "/timeline", "Timeline")
            + tab("control", "/control", "Control")
            + tab("agent", "/agent", "Agent Detail")
            + tab("server", "/server", "Server")
            + tab("concepts", "/concepts", "Introduction"))
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
// schema 視圖:欄名/型別/notnull/預設/pk —— 即使 0 列也看得到全部欄位(debug 用)
function schemaHtml(cols){
  if(!cols||!cols.length)return '';
  const h="<tr><td><b>欄位</b></td><td><b>型別</b></td><td><b>notnull</b></td>"+
    "<td><b>預設</b></td><td><b>pk</b></td></tr>";
  const b=cols.map(c=>`<tr><td>${esc(c.name)}</td><td>`+
    `<span style='color:var(--muted)'>${esc(c.type||'-')}</span></td>`+
    `<td>${c.notnull?'✓':''}</td><td>${c.default==null?'':esc(c.default)}</td>`+
    `<td>${c.pk?'🔑':''}</td></tr>`).join('');
  return "<details style='margin-bottom:10px' open><summary style='cursor:pointer;"+
    `color:var(--muted)'>schema · ${cols.length} 欄</summary>`+
    `<div style='overflow:auto'><table id='tix'><thead>${h}</thead>`+
    `<tbody>${b}</tbody></table></div></details>`;
}
async function showTable(){
  const [d,sc]=await Promise.all([
    (await fetch(`/db/table/${CUR}?limit=${LIM}&offset=${OFF}`)).json(),
    (await fetch(`/db/schema/${CUR}`)).json()]);
  if(d.error){$('dbout').innerHTML="<p style='color:var(--s-failure)'>"+esc(d.error)+
    "</p>";return;}
  DBMODE='table';
  $('dbtitle').textContent='📋 '+CUR;
  $('dbpg').style.display=d.total>LIM?'flex':'none';
  $('dbout').innerHTML=schemaHtml(sc.columns)+tbl(d.columns,d.rows,d.total);
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
                 f"<td><span class='badge {esc(r.get('status_cls') or '')}'>"
                 f"{esc(r['status'])}</span></td>"
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
        # 過濾模式:預設一般字串(不分大小寫);勾選 → 正則(profile/summary/desc 三格同時套用)
        "<label class='sys' style='display:inline-flex;gap:4px;align-items:center;"
        "font-size:12px;white-space:nowrap' title='勾=正則(不分大小寫);不勾=一般字串包含"
        "(不分大小寫)'><input type='checkbox' id='rx'> 🔤 Regex</label>"
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
            f"<div id='kpi3'></div>"
            f"<div id='ab'></div>"
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
    "handoff-cmd": "指令換手(指令台 next)", "assignee-inactive": "指派給人類(暫停)",
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


# ── C5 粗看:全域跨票時間軸(/timeline)──────────────────────────────── #
# 色帶沿用全站六態色票(--s-*):run=執行中(藍)、wait=等人(黃)、
# queue=排隊(紫)、idle=間歇(灰)。事件點只留關鍵少數,細節進單票細看頁。
_OV_WAIT = {"pending", "inactive_set", "external_pending"}
_OV_IDLE = {"session_created", "attempt_finished", "handoff", "adopted",
            "inactive_cleared", "external_cleared"}
_OV_EVPTS = {   # 粗看疊加的關鍵事件點(type → (圖示, 中文說明))
    "session_created": ("🎬", "session 建立(開始接管)"),
    "pending": ("⏸", "等待人類(HIL 表單發出)"),
    "handoff": ("🔀", "換手(換 profile 接手)"),
    "dispatch_error": ("💥", "派工錯誤"),
    "owner_changed": ("👥", "改負責人"),
}


def lane_segments(evs: list[dict], now: float, outcome=None,
                  finished_at: float = 0.0) -> list[tuple]:
    """單票 journal 事件 → 狀態區段 [(start, end, state)];state ∈
    run/wait/queue/idle。resolved/external_abort 關段(retry 後再開新段);
    活著的最後一段延伸到 now、已終態(outcome+finished_at)截到 finished_at。
    純函式(可測)。"""
    segs: list[tuple] = []
    cur, start = None, 0.0

    def _close(ts):
        nonlocal cur, start
        if cur is not None and ts > start:
            segs.append((start, ts, cur))
        cur = None

    for e in sorted(evs, key=lambda x: float(x.get("ts") or 0)):
        ts, et = float(e.get("ts") or 0), e.get("type")
        if et == "attempt_started":
            nxt = "run"
        elif et in _OV_WAIT or (et == "approval"
                                and e.get("decision") != "proceed"):
            nxt = "wait"
        elif et == "queued":
            nxt = "queue"
        elif et in _OV_IDLE or (et == "approval"):
            nxt = "idle"
        elif et in ("resolved", "external_abort"):
            _close(ts)
            continue
        else:
            continue
        if cur is None:
            cur, start = nxt, ts
        elif nxt != cur:
            _close(ts)
            cur, start = nxt, ts
    if cur is not None:
        end = (float(finished_at) if outcome and (finished_at or 0) > start
               else now)
        _close(end)
    return segs


def _form_base() -> str:
    """表單服務 base URL(config form.base_url / host:port);壞 config 回空。"""
    try:
        from arcp.routing import load_config
        f = (load_config(_CONFIG_PATH)[0].get("form") or {})
        return (f.get("base_url")
                or f"http://{f.get('host', '127.0.0.1')}:{f.get('port', 8790)}")
    except Exception:      # noqa: BLE001
        return ""


def overview_data(journal: list, sessions: dict, watch: dict,
                  since: float = 0.0, q: str = "", mode: str = "match",
                  now: float | None = None) -> dict:
    """C5 粗看資料:窗內有活動的票 → vis-timeline groups(每票一列,新活動在上)
    + items(background 色帶 + 關鍵事件點 + ✔/✘ 終點)+ tickets(側欄摘要:
    owner/用量/處理時間/workspace/可操作連結)。"""
    now = time.time() if now is None else now
    match_fn, _err = text_matcher(q, mode)
    by_iid: dict[int, list] = {}
    for e in journal:
        if e.get("issue_id") is not None:
            by_iid.setdefault(e["issue_id"], []).append(e)
    form_base = _form_base()
    groups, items, tickets = [], [], {}
    for iid, evs in by_iid.items():
        last_ts = max(float(e.get("ts") or 0) for e in evs)
        if since and last_ts < since:
            continue                                 # 窗內無活動
        s = sessions.get(iid, {}) or {}
        w = watch.get(iid, {}) or {}
        key = s.get("key") or w.get("key") or f"#{iid}"
        if match_fn is not None and not match_fn(
                f"{key} {w.get('summary') or ''} {s.get('profile') or ''}"):
            continue
        segs = lane_segments(evs, now, s.get("outcome"),
                             float(s.get("finished_at") or 0))
        first_ts = min(float(e.get("ts") or 0) for e in evs)
        st = canonical_state(s or None)
        groups.append({"id": iid, "order": -last_ts,
                       "content": f"<b>{esc(key)}</b> "
                                  f"<span class='ovp'>{esc(s.get('profile') or '')}</span>"})
        for i, (a, b, state) in enumerate(segs):
            items.append({"id": f"bg{iid}-{i}", "group": iid,
                          "start": int(a * 1000), "end": int(b * 1000),
                          "type": "background", "className": f"ov-{state}"})
        for j, e in enumerate(evs):
            et = e.get("type")
            if et not in _OV_EVPTS:
                continue
            icon, lab = _OV_EVPTS[et]
            extra = {k: v for k, v in e.items()
                     if k not in ("ts", "type", "issue_id", "key")}
            items.append({"id": f"ep{iid}-{j}", "group": iid,
                          "start": int(float(e.get("ts") or 0) * 1000),
                          "content": icon, "className": "ov-ev",
                          "title": f"{lab} · "
                                   f"{json.dumps(extra, ensure_ascii=False)}"[:300]})
        if s.get("outcome"):                          # ✔/✘ 終點
            ok2 = s["outcome"] == "SUCCESS"
            fts = float(s.get("finished_at") or 0) or last_ts
            items.append({"id": f"end{iid}", "group": iid,
                          "start": int(fts * 1000),
                          "content": "✔" if ok2 else "✘",
                          "className": "ov-end-" + ("ok" if ok2 else "bad"),
                          "title": f"結束:{s['outcome']}"})
        links = []
        for r in read_interactions(iid):
            if r.get("status") != "pending":
                continue                              # 側欄只列此刻可操作的
            kind, sid = r.get("kind") or "hil", r.get("schema_id") or ""
            lab = (_IX_SCHEMA_LABEL.get(sid) or _IX_SCHEMA_LABEL.get(kind)
                   or sid or kind)
            links.append({"label": lab,
                          "url": f"{form_base}/form/{r.get('token')}"})
        run_secs = sum(b - a for a, b, state in segs if state == "run")
        tickets[str(iid)] = {
            "iid": iid, "key": key, "state": st,
            "summary": w.get("summary") or "",
            "profile": s.get("profile") or "",
            "owner_email_list": s.get("owner_email_list") or "",
            "cost_usd": s.get("cost_usd") or 0,
            "tokens": s.get("tokens") or 0,
            "attempts": s.get("attempts") or 0,
            "outcome": s.get("outcome"),
            "pending_reason": s.get("pending_reason"),
            "score": s.get("human_score"),
            "workspace": s.get("workspace") or "",
            "run_secs": int(run_secs),
            "first_ts": first_ts, "last_ts": last_ts,
            "links": links,
        }
    win_start = since if since else min(
        (t["first_ts"] for t in tickets.values()), default=now - 86400)
    return {"groups": groups, "items": items, "tickets": tickets,
            "win_start": int(win_start * 1000), "win_end": int(now * 1000)}


_OV_HOWTO = """
<details class='card howto' id='ovhow'>
<summary>📖 怎麼看這張圖(圖例・操作・判讀)</summary>
<div class='row' style='margin-top:8px'><b>這頁是什麼</b></div>
<p style='margin:4px 0'>每張被 ARCP 接管的票一列,橫軸是時間。色帶=該時段這張票
處於什麼狀態;疊在上面的小圖示=關鍵事件。這頁是「粗看」——看並行度和卡點;
單票細節(每次 attempt、對話、log)請點進該票的細看頁。</p>
<div class='row'><b>圖例</b></div>
<p style='margin:4px 0'>
<span class='lg' style='background:var(--s-running)'></span>執行中(agent 在跑)
<span class='lg' style='background:var(--s-pending)'></span>等人(HIL 表單/審批待填)
<span class='lg' style='background:var(--s-queued)'></span>排隊(等資源)
<span class='lg' style='background:var(--s-inactive)'></span>間歇(attempt 之間)<br>
🎬 開始接管 · ⏸ 發出表單等人 · 🔀 換手 · 💥 派工錯誤 · 👥 改負責人 ·
<b style='color:var(--s-success)'>✔</b> 成功結束 ·
<b style='color:var(--s-failure)'>✘</b> 失敗/撤銷</p>
<div class='row'><b>操作</b></div>
<p style='margin:4px 0'>拖曳=平移時間、<kbd>Ctrl</kbd>+滾輪=縮放、
<b>點任一列=右側開該票摘要</b>(狀態/負責人/用量/可操作連結),
摘要底部「完整詳情」進單票細看頁。上方按鈕切時間窗、輸入關鍵字過濾票。</p>
<div class='row'><b>判讀範例</b></div>
<ul style='margin:4px 0 2px 18px;padding:0'>
<li><b>一列黃段很長</b>=卡在等人:點開側欄看是哪張表單沒填,連結可直接開。</li>
<li><b>一列藍/灰反覆交替</b>=反覆重試:進細看頁看每次 attempt 為何失敗。</li>
<li><b>多列同時藍</b>=並行度高:系統正忙,搭配 Dashboard 的 budget 卡看花費。</li>
</ul></details>"""


def render_timeline_page(journal, sessions, watch, win: str = "7",
                         q: str = "") -> str:
    """C5 粗看頁:全域跨票時間軸(色帶+關鍵事件點)+ 點列側欄摘要 + 說明卡。
    win ∈ 1/7/30/all(天);q=關鍵字過濾(key/summary/profile)。"""
    now = time.time()
    days = {"1": 1, "7": 7, "30": 30}.get(win)
    since = now - days * 86400 if days else 0.0
    data = overview_data(journal, sessions, watch, since=since, q=q, now=now)
    n = len(data["groups"])
    wins = "".join(
        f"<a class='{'on' if win == k else ''}' "
        f"href='/timeline?win={k}&q={html.escape(q, quote=True)}'>{lab}</a>"
        for k, lab in (("1", "24 小時"), ("7", "7 天"),
                       ("30", "30 天"), ("all", "全部")))
    toolbar = (
        f"<div class='card ovbar'><span class='ovwin'>{wins}</span>"
        f"<form method='GET' action='/timeline' style='display:inline'>"
        f"<input type='hidden' name='win' value='{esc(win)}'>"
        f"<input type='search' name='q' value='{html.escape(q, quote=True)}' "
        f"placeholder='過濾:key / 摘要 / profile' style='max-width:230px'>"
        f"<button type='submit'>過濾</button></form>"
        f"<span class='sys' style='margin-left:auto'>{n} 張票 · "
        f"點任一列看摘要</span></div>")
    empty = ("" if n else "<div class='card'><p>此時間窗內沒有活動的票。"
                          "換個時間窗或清掉過濾條件試試。</p></div>")
    dj = json.dumps(data, ensure_ascii=False)
    return (
        _nav("timeline")
        + "<header><h1>全域時間軸(粗看)</h1>"
          "<p class='sys' style='text-align:left'>每票一列:色帶=狀態、圖示="
          "關鍵事件;點列開摘要、進細看。</p></header>"
        + "<main id='main'>" + _OV_HOWTO + toolbar + empty
        + "<div id='ovwrap'><div id='ovtl' class='card'></div>"
          "<aside id='ovside' class='card'></aside></div>"
        + f"<script id='ov-data' type='application/json'>{dj}</script>"
        + "<link rel='stylesheet' href='/tvendor/vis-timeline.min.css'>"
          "<script src='/tvendor/vis-timeline.min.js'></script>"
        + """<script>(function(){
function esc(x){return (''+(x==null?'':x)).replace(/&/g,'&amp;')
 .replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function dur(s){if(!s)return '0m';var h=Math.floor(s/3600),
 m=Math.round(s%3600/60);return (h?h+'h ':'')+m+'m';}
function fts(t){return t?new Date(t*1000).toLocaleString():'-';}
var d;try{d=JSON.parse(document.getElementById('ov-data').textContent)}
 catch(e){return}
var el=document.getElementById('ovtl');if(!el||!window.vis)return;
var tl=new vis.Timeline(el,new vis.DataSet(d.items),
 new vis.DataSet(d.groups),{stack:false,orientation:'top',zoomKey:'ctrlKey',
 maxHeight:600,tooltip:{followMouse:true},
 groupOrder:function(a,b){return a.order-b.order;},
 start:new Date(d.win_start),end:new Date(d.win_end)});
var side=document.getElementById('ovside');
var SL={running:'執行中',queued:'排隊',hil_middle:'等人(中場)',
 hil_end:'等人(收尾)',success:'成功',failure:'失敗',aborted:'已撤銷',
 todo:'未接管',inactive:'交人類'};
function openSide(iid){var t=d.tickets[String(iid)];if(!t)return;
 var links=(t.links||[]).map(function(l){return '<a href="'+esc(l.url)
  +'" rel="noopener" target="_blank">'+esc(l.label)+' ↗</a>';}).join(' · ');
 side.innerHTML='<h3 style="margin:0 0 6px"><a href="/ticket/'+t.iid+'">'
  +esc(t.key)+'</a> <span class="ovp">'+esc(SL[t.state]||t.state)+'</span></h3>'
  +(t.summary?'<p style="margin:0 0 6px">'+esc(t.summary)+'</p>':'')
  +'<table class="ovkv"><tbody>'
  +'<tr><td>profile</td><td>'+esc(t.profile||'—')+'</td></tr>'
  +'<tr><td>負責人</td><td>'+esc(t.owner_email_list||'(未設定,門禁未啟用)')
  +'</td></tr>'
  +'<tr><td>用量</td><td>$'+(+t.cost_usd).toFixed(4)+' · '
  +(+t.tokens).toLocaleString()+' tok</td></tr>'
  +'<tr><td>attempts</td><td>'+t.attempts
  +(t.score!=null?' · 評分 '+t.score:'')+'</td></tr>'
  +'<tr><td>執行時間</td><td>'+dur(t.run_secs)+'(色帶藍段加總)</td></tr>'
  +'<tr><td>生命期</td><td>'+fts(t.first_ts)+' ~ '+fts(t.last_ts)+'</td></tr>'
  +(t.outcome?'<tr><td>outcome</td><td>'+esc(t.outcome)+'</td></tr>':'')
  +(t.pending_reason?'<tr><td>等待原因</td><td>'+esc(t.pending_reason)
   +'</td></tr>':'')
  +'<tr><td>workspace</td><td class="mono">'+esc(t.workspace||'—')
  +'</td></tr></tbody></table>'
  +(links?'<div style="margin-top:6px"><b>可操作連結</b> '+links
   +'<div class="sys" style="text-align:left">⚠️ capability 連結,有連結即可'
   +'操作,勿外流。</div></div>':'')
  +'<p style="margin:10px 0 0"><a class="ovgo" href="/ticket/'+t.iid
  +'">完整詳情(細看頁)→</a></p>';
 side.classList.add('on');}
tl.on('click',function(p){if(p.group!=null)openSide(p.group);});
var hw=document.getElementById('ovhow');
try{if(localStorage.getItem('arcp-ovhow')!=='0')hw.open=true;
 hw.addEventListener('toggle',function(){
  localStorage.setItem('arcp-ovhow',hw.open?'1':'0');});}catch(e){}
})();</script>"""
        + "</main>")


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


def merged_timeline_data(evs: list[dict], iid: int, sess=None) -> dict:
    """W9.3:L3 對話 + Jira 生命週期合成**單一** vis-timeline 的 groups/items。

    共用一條時間軸 → 拖曳/縮放兩區一起動;左側仍以兩層 nested groups 分類:
    類別列『💬 對話(L3)』(各 attempt aN 為子列)+『📅 生命週期』(外部輸入/
    Jira 寫入/決策/執行 四子列)。item id 生命週期加 lf- 前綴,與 L3 的 aN-i 不撞。
    C5b:再疊全高狀態色帶背景(lane_segments,同粗看頁顏色語言)。"""
    l3 = l3_timeline_data(iid)
    life = timeline_data(evs)
    groups: list[dict] = []
    a_ids = [g["id"] for g in l3["groups"]]
    if a_ids:                                    # 有 attempt 才放對話類別
        groups.append({"id": "cat_l3", "content": "💬 對話(L3)",
                       "nestedGroups": a_ids, "showNested": True,
                       "treeLevel": 1})
        for g in l3["groups"]:
            groups.append({"id": g["id"], "content": g["content"],
                           "treeLevel": 2})
    life_ids = [g["id"] for g in life["groups"]]
    groups.append({"id": "cat_life", "content": "📅 生命週期",
                   "nestedGroups": life_ids, "showNested": True,
                   "treeLevel": 1})
    for g in life["groups"]:
        groups.append({"id": g["id"], "content": g["content"], "treeLevel": 2})
    items = list(l3["items"])
    for it in life["items"]:
        it = dict(it)
        it["id"] = f"lf-{it['id']}"
        items.append(it)
    sd = sess or {}
    for i2, (a, b, state) in enumerate(lane_segments(
            evs, time.time(), sd.get("outcome"),
            float(sd.get("finished_at") or 0))):
        items.append({"id": f"ovbg-{i2}", "start": int(a * 1000),
                      "end": int(b * 1000), "type": "background",
                      "className": f"ov-{state}"})   # 全高色帶(同粗看顏色)
    return {"groups": groups, "items": items}


def render_timeline_section(evs: list[dict], iid: int, sess=None) -> str:
    """W6.7/W9.2/W9.3:單一事件時間軸(L3 對話 + Jira 生命週期合一,共用時間軸),
    收在**右下浮動鈕**切換的抽屜裡。**刻意放 <main> 之外**——ticket 頁每 5s 整段換
    main.innerHTML,widget 在裡面會被反覆摧毀;放外面只初始化一次,刷新只抽資料島更新。
    C5b:狀態色帶背景 + 完整「怎麼看」說明卡(圖例/操作/判讀,同粗看頁顏色語言)。"""
    data = json.dumps(merged_timeline_data(evs, iid, sess), ensure_ascii=False)
    return (
        "<button type='button' id='tlfab' aria-expanded='false' "
        "aria-controls='tlwrap'>🕑 時間軸</button>"
        "<section id='tlwrap' aria-hidden='true'>"
        "<h2 style='margin:4px 0 6px'>🕑 事件時間軸(L3 對話 + Jira 生命週期)</h2>"
        "<details class='howto' style='margin:0 0 6px'>"
        "<summary>📖 怎麼看這張圖(圖例・操作・判讀)</summary>"
        "<p style='margin:4px 0'><b>這是什麼</b>:這張票自己的時間軸(細看)。"
        "上半『💬 對話(L3)』每個 attempt(a1/a2…)一列:🧑=harness 餵給 agent "
        "的 prompt、🤖=agent 回覆;下半『📅 生命週期』分四列:外部輸入(人/Jira "
        "來的)、Jira 寫入(harness 寫出去的)、決策、執行。</p>"
        "<p style='margin:4px 0'><b>背景色帶</b>(與全域 Timeline 頁同一套顏色):"
        "<span class='lg' style='background:var(--s-running)'></span>執行中 "
        "<span class='lg' style='background:var(--s-pending)'></span>等人 "
        "<span class='lg' style='background:var(--s-queued)'></span>排隊 "
        "<span class='lg' style='background:var(--s-inactive)'></span>間歇。</p>"
        "<p style='margin:4px 0'><b>操作</b>:拖曳=平移、<kbd>Ctrl</kbd>+滾輪="
        "縮放、滑鼠停留=事件全文;兩區共用同一條時間軸、一起動。</p>"
        "<p style='margin:4px 0 2px'><b>判讀</b>:黃色(等人)區間裡沒有對話="
        "agent 停著等表單,去催;藍色區間對話密集但生命週期沒進展=同一個 "
        "attempt 長跑中;對話一列結束又開新列=重試或換手,看生命週期列的"
        "事件找原因。</p></details>"
        "<div id='evtl'></div>"
        f"<script id='tl-data' type='application/json'>{data}</script>"
        "</section>"
        "<link rel='stylesheet' href='/tvendor/vis-timeline.min.css'>"
        "<script src='/tvendor/vis-timeline.min.js'></script>"
        "<script>(function(){"
        "function rd(id){try{return JSON.parse("
        "document.getElementById(id).textContent)}catch(e){return{groups:[],items:[]}}}"
        "var el=document.getElementById('evtl');if(!el||!window.vis)return;"
        "var d=rd('tl-data');"
        "var items=new vis.DataSet(d.items),groups=new vis.DataSet(d.groups);"
        "var tl=new vis.Timeline(el,items,groups,{stack:true,orientation:'top',"
        "zoomKey:'ctrlKey',margin:{item:6},tooltip:{followMouse:true},maxHeight:440});"
        "window.__tlUpdate=function(nd){items.clear();items.add(nd.items);"
        "groups.clear();groups.add(nd.groups);};"
        "var fab=document.getElementById('tlfab'),wrap=document.getElementById('tlwrap');"
        "function setOpen(o){wrap.classList.toggle('on',o);"
        "fab.setAttribute('aria-expanded',o);wrap.setAttribute('aria-hidden',!o);"
        "fab.textContent=o?'✕ 收起時間軸':'🕑 時間軸';"
        "try{localStorage.setItem('arcp-tl',o?'1':'0')}catch(e){}"
        "if(o)setTimeout(function(){tl.redraw();},30);}"
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


_IX_SCHEMA_LABEL = {"command": "指令台", "score_and_close": "評分/裁決",
                    "decision": "決策", "need_info": "補資訊",
                    "hold": "hold 中斷", "budget_increase": "budget 增額"}
_IX_STATUS_LABEL = {"pending": "待填/有效", "submitted": "已提交",
                    "expired": "已逾期", "invalidated": "已失效"}


def _usage_bar(used, cap, is_usd) -> str:
    def _f(v):
        return f"${v:.4f}" if is_usd else f"{int(v):,}"
    if not cap:
        return f"<span class='rid'>{_f(used)} / —(未設)</span>"
    pct = 100.0 * used / cap
    col = ("s-failure" if used >= cap else
           "s-unknown" if used >= 0.8 * cap else "s-success")
    return (f"<div style='background:var(--line);border-radius:4px;height:8px;"
            f"overflow:hidden;max-width:200px'><div style='width:"
            f"{min(100.0, pct):.0f}%;height:100%;background:var(--{col})'></div>"
            f"</div><span class='rid'>{_f(used)} / {_f(cap)}({pct:.0f}%)</span>")


def _ticket_meta_card(iid, s, evs) -> str:
    """詳情頁『來源・連結・用量』卡:來源推導 + Jira/CR 連結 + 一次性連結清單(完整
    token,dashboard 須鎖存取)+ per-ticket token/usd 用量 vs soft/hard。全唯讀。"""
    key = s.get("key") or f"#{iid}"
    form_base, cq_base, prof = "", "", None
    try:                                    # 壞 config 不擋頁
        from arcp.profiles import load_profiles
        from arcp.routing import load_config
        src, _ = load_config(_CONFIG_PATH)
        f = src.get("form") or {}
        form_base = (f.get("base_url")
                     or f"http://{f.get('host', '127.0.0.1')}:"
                        f"{f.get('port', 8790)}")
        cq_base = ((src.get("cq_writeback") or {}).get("base_url") or "")
        jira_base = (src.get("source") or {}).get("jira_base_url") or ""
        prof = load_profiles(_CONFIG_PATH).get(s.get("profile"))
    except Exception:                       # noqa: BLE001
        jira_base = ""
    if not jira_base:                       # 後備:~/.env 的 JIRA_BASE_URL(只取 base)
        try:
            from arcp.config import load_env
            jira_base = load_env().get("JIRA_BASE_URL", "")
        except Exception:                   # noqa: BLE001
            jira_base = ""
    jira_base = jira_base.rstrip("/")

    if s.get("clearquest_id"):
        origin = f"ClearQuest CR «{esc(s['clearquest_id'])}»"
    elif s.get("base_ref"):
        origin = f"跨票交接子票(base 母票 issue_id={esc(s['base_ref'])})"
    else:
        jf = next((e for e in evs if e.get("type") == "job_fired"), None)
        origin = (f"排程 / 單次 job «{esc(jf.get('run_name') or jf.get('job') or '?')}»"
                  if jf else "人開 / route 撿票")

    links = [f"<a href='{jira_base}/browse/{esc(key)}' rel='noopener' "
             f"target='_blank'>Jira {esc(key)}</a>" if jira_base
             else f"Jira {esc(key)}(設 source.jira_base_url 才成連結)"]
    if s.get("clearquest_id"):
        cid = esc(s["clearquest_id"])
        links.append(f"<a href='{esc(cq_base)}/{cid}' rel='noopener' "
                     f"target='_blank'>CR {cid}</a>" if cq_base
                     else f"CR {cid}(CQ base_url 待設)")

    ix_rows = ""
    for r in read_interactions(iid):
        kind, sid = r.get("kind") or "hil", r.get("schema_id") or ""
        lab = (_IX_SCHEMA_LABEL.get(sid) or _IX_SCHEMA_LABEL.get(kind)
               or sid or kind)
        st_lab = _IX_STATUS_LABEL.get(r.get("status")) or r.get("status") or "?"
        url = f"{form_base}/form/{r.get('token')}"
        tok = str(r.get("token") or "")
        # 提交稽核三欄(K 期已入庫:時間/email/IP;未提交顯示 —)
        sub_at = r.get("submitted_at") or 0
        ix_rows += (f"<tr><td>{esc(lab)}</td><td>{esc(st_lab)}</td>"
                    f"<td>{esc(fmt_ts(r.get('created_at')))}</td>"
                    f"<td>{esc(fmt_ts(sub_at)) if sub_at else '—'}</td>"
                    f"<td>{esc(r.get('submitted_by') or '—')}</td>"
                    f"<td>{esc(r.get('submitted_ip') or '—')}</td>"
                    f"<td><a href='{esc(url)}' rel='noopener' target='_blank'>開啟"
                    f"</a> <span class='rid'>…{esc(tok[-8:])}</span></td></tr>")
    ix_table = (("<table><thead><tr><td><b>類型</b></td><td><b>狀態</b></td>"
                 "<td><b>建立</b></td><td><b>提交時間</b></td>"
                 "<td><b>提交者</b></td><td><b>IP</b></td>"
                 "<td><b>連結</b></td></tr></thead><tbody>"
                 + ix_rows + "</tbody></table><div class='sys' style='text-align:"
                 "left'>⚠️ 這些是 capability 連結(有連結即可操作/下載),dashboard "
                 "請鎖本機/內網存取(見操作手冊 §8)。大檔下載頁在各表單內 "
                 "/files/&lt;token&gt;。</div>")
                if ix_rows else "<div class='sys'>(尚無一次性連結)</div>")

    used_usd, used_tok = float(s.get("cost_usd") or 0), int(s.get("tokens") or 0)
    soft_usd = s.get("soft_usd")
    if soft_usd is None and prof is not None:
        soft_usd = prof.ticket_soft_usd
    soft_tok = s.get("soft_tokens")
    if soft_tok is None and prof is not None:
        soft_tok = prof.ticket_soft_tokens
    hard_usd = prof.ticket_hard_usd if prof else None
    hard_tok = prof.ticket_hard_tokens if prof else None

    return ("<div class='card'><h2>來源・連結・用量</h2>"
            f"<div class='row'><span class='kv'><b>來源</b> {origin}</span></div>"
            f"<div class='row'><span class='kv'><b>連結</b> "
            f"{' · '.join(links)}</span></div>"
            "<h3 style='margin:10px 0 4px'>一次性連結(本票發過的)</h3>" + ix_table
            + "<h3 style='margin:10px 0 4px'>本票用量 vs soft / hard</h3>"
            f"<div class='row'><span class='kv'><b>USD soft</b> "
            f"{_usage_bar(used_usd, soft_usd, True)}</span>"
            f"<span class='kv'><b>USD hard</b> "
            f"{_usage_bar(used_usd, hard_usd, True)}</span></div>"
            f"<div class='row'><span class='kv'><b>token soft</b> "
            f"{_usage_bar(used_tok, soft_tok, False)}</span>"
            f"<span class='kv'><b>token hard</b> "
            f"{_usage_bar(used_tok, hard_tok, False)}</span></div></div>")


# ── C5b 細看:Session 駕駛艙(DB 全欄位 + TICKET.md)──────────────────── #
_CK_LABELS = [   # 顯示順序 + 中文標籤;schema 新欄位落在表尾 fallback,不漏列
    ("key", "Jira key"), ("issue_id", "內部 issue id"),
    ("profile", "agent profile"), ("outcome", "outcome(終態)"),
    ("abort_reason", "中止理由(cancel/external/untriageable/handoff/security)"),
    ("pending_reason", "等待原因"),
    ("owner_email_list", "負責人 email(門禁)"),
    ("session_id", "engine session id"), ("attempts", "attempts(本輪)"),
    ("approval_revisions", "審批退回次數"), ("cost_usd", "花費 USD"),
    ("tokens", "tokens(累計)"), ("soft_usd", "soft USD 上限(本票)"),
    ("soft_tokens", "soft tokens 上限(本票)"),
    ("human_score", "人類評分(0-10)"), ("agent_score", "agent 自評(0-10)"),
    ("clearquest_id", "ClearQuest CR"), ("base_ref", "base 母票"),
    ("queued", "排隊中"), ("queued_at", "排隊時間"),
    ("inactive", "交人類(讓出額度)"), ("evict_count", "強制驅逐次數"),
    ("score_reminded_at", "上次催評分"), ("finished_at", "結束時間"),
    ("workspace", "workspace 路徑"),
]


def _ticket_md(ws: str) -> str:
    """workspace/TICKET.md(agent 開工讀的任務簡報);哨值/缺檔回空。"""
    p = os.path.join(ws or "", "TICKET.md")
    if not (ws and not ws.startswith("(") and os.path.isfile(p)):
        return ""
    try:
        txt = open(p, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""
    return txt[:60000] + ("\n…(截斷)" if len(txt) > 60000 else "")


def _dur_h(secs: int) -> str:
    return (f"{secs // 3600}h {secs % 3600 // 60}m" if secs >= 3600
            else f"{secs // 60}m {secs % 60}s")


def _session_cockpit_card(iid, s, evs) -> str:
    """C5b:Session 駕駛艙卡——DB session 全欄位清楚列出 + 處理時間
    (run/wait 段加總,同粗看色帶語意)+ TICKET.md 內容(摺疊)。"""
    if not s:
        return ""
    now = time.time()
    segs = lane_segments(evs, now, s.get("outcome"),
                         float(s.get("finished_at") or 0))
    run_s = int(sum(b - a for a, b, st2 in segs if st2 == "run"))
    wait_s = int(sum(b - a for a, b, st2 in segs if st2 == "wait"))
    tss = [float(e.get("ts") or 0) for e in evs]

    def _fmt(k, v):
        if v is None or v == "":
            return "—"
        if k in ("queued_at", "finished_at", "score_reminded_at"):
            return fmt_ts(v)
        if k in ("queued", "inactive"):
            return "✓" if v else "—"
        if k == "cost_usd":
            return f"${float(v):.4f}"
        return str(v)

    rows, shown = "", set()
    for k, lab in _CK_LABELS:
        shown.add(k)
        rows += f"<tr><td>{lab}</td><td>{esc(_fmt(k, s.get(k)))}</td></tr>"
    for k in sorted(set(s) - shown):        # 新欄位不漏列(越清楚越好)
        rows += f"<tr><td>{esc(k)}</td><td>{esc(_fmt(k, s.get(k)))}</td></tr>"
    tmd = _ticket_md(s.get("workspace") or "")
    md_html = ((f"<details><summary>📄 TICKET.md(agent 開工讀的任務簡報,"
                f"{len(tmd)} 字,點開看)</summary>"
                f"<pre class='ckpre'>{esc(tmd)}</pre></details>") if tmd
               else "<div class='sys'>(TICKET.md 不存在:尚未佈建或已回收)"
                    "</div>")
    return ("<div class='card'><h2>Session 駕駛艙(DB 全欄位)</h2>"
            "<div class='row'>"
            f"<span class='kv'><b>執行時間</b> {_dur_h(run_s)}(agent 實跑)"
            "</span>"
            f"<span class='kv'><b>等人時間</b> {_dur_h(wait_s)}</span>"
            f"<span class='kv'><b>生命期</b> {fmt_ts(min(tss) if tss else 0)}"
            f" ~ {fmt_ts(max(tss) if tss else 0)}</span></div>"
            f"<div style='overflow-x:auto'><table class='ovkv'><tbody>{rows}"
            "</tbody></table></div>" + md_html + "</div>")


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
    _htxt, _hcls = session_status(s, {}) if s else ("-", "")
    return (f"<header><h1><a href='/'>← </a>{esc(key)} · "
            f"<span class='badge {esc(_hcls)}'>"
            f"{esc(_htxt)}</span></h1></header><main id='main' tabindex='-1'>"
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
            f"{_session_cockpit_card(iid, s, evs)}"
            f"{_ticket_meta_card(iid, s, evs)}"
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
            f"{render_timeline_section(evs, iid, s)}")


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
            "/api/v1/kpi": {"get": {
                "tags": ["llm-api(唯讀)"],
                "summary": "C3 KPI:北極星(First-pass close 雙報)+效率+"
                           "制衡(打回率/人評/UNKNOWN/放棄)+coverage",
                "parameters": [
                    {"name": "days", "in": "query", "required": False,
                     "description": "時間窗天數(省略=全歷史)",
                     "schema": {"type": "number"}},
                    {"name": "profile", "in": "query", "required": False,
                     "description": "只計該 profile 的票(C6 A/B 對照;"
                                    "非隨機分流僅供參考)",
                     "schema": {"type": "string"}}],
                "responses": {"200": {"description":
                                      "north_star/efficiency/guard/coverage",
                                      "content": {"application/json": {}}}}}},
            "/api/v1/tickets": {"get": {
                "tags": ["llm-api(唯讀)"],
                "summary": "票列表(精簡:key/profile/6態/outcome/cost/score)"
                           ";可選 ?q=&field=&mode= 過濾",
                "parameters": [
                    {"name": "q", "in": "query", "required": False,
                     "description": "關鍵字或正則(空=不過濾)",
                     "schema": {"type": "string"}},
                    {"name": "field", "in": "query", "required": False,
                     "description": "比對欄位",
                     "schema": {"type": "string", "default": "all",
                                "enum": ["all", "key", "summary", "profile",
                                         "desc"]}},
                    {"name": "mode", "in": "query", "required": False,
                     "description": "match=不分大小寫子字串;regex=正則(亦不分大小寫)",
                     "schema": {"type": "string", "default": "match",
                                "enum": ["match", "regex"]}}],
                "responses": {"200": {"description": "tickets[](含 filter/filter_error)",
                                      "content": {"application/json": {}}}}}},
            "/api/v1/tickets/{ref}": {"get": {
                "tags": ["llm-api(唯讀)"],
                "summary": "單票完整狀態 JSON(含時間軸摘要 + 可取 log 清單"
                           " + owner_email_list 負責人名單)",
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
                "熱重載 config.yaml(壞 config 回 400、舊設定續用、不弄死 poller)")},
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
    """遮蔽疑似敏感 key(config.yaml 本無憑證,防禦性;憑證在 ~/.env)。"""
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
    """W7.5:harness 設定(config.yaml)+ 每個 Profile 全參數。憑證不在此檔。"""
    try:
        import sys as _sys
        _here = os.path.dirname(os.path.abspath(__file__))
        if _here not in _sys.path:
            _sys.path.insert(0, _here)
        from arcp.profiles import load_profiles
        from arcp.routing import load_config
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
    cfg_card = ("<h2>harness 設定(config.yaml)</h2><div class='card'>"
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
                ("budget.ticket_soft_usd", p.ticket_soft_usd),
                ("budget.ticket_hard_usd", p.ticket_hard_usd),
                ("budget.ticket_soft_tokens", p.ticket_soft_tokens),
                ("budget.ticket_hard_tokens", p.ticket_hard_tokens),
                ("budget.monthly_max_usd(月/agent)", p.monthly_max_usd),
                ("budget.monthly_max_tokens(月/agent)", p.monthly_max_tokens),
                ("human_minutes_est", p.human_minutes_est),
                ("est_minutes(有效,未設→240)", p.est_minutes()),
                ("require_approval", p.require_approval),
                ("approver", p.approver),
                ("max_revisions", p.max_revisions),
                ("retention_days", p.retention_days),
                ("on_unknown", p.on_unknown),
            ]) + "</div>")

    budget_card = _budget_usage_card(read_journal(), src.get("budget") or {},
                                     profiles)
    return head + cfg_card + budget_card + routes_card + pcards + "</main>"


def _budget_usage_card(journal, budget_cfg, profiles) -> str:
    """budget 當月用量 vs 上限(月/agent + 全站):綠<80% 黃≥80% 紅≥100%。"""
    import datetime
    ref = datetime.datetime.now()

    def _sum(field, profile=None):
        t = 0.0
        for e in journal:
            if e.get("type") != "attempt_finished" or not e.get(field):
                continue
            if profile is not None and e.get("profile") != profile:
                continue
            edt = datetime.datetime.fromtimestamp(e.get("ts") or 0)
            if edt.year == ref.year and edt.month == ref.month:
                t += float(e[field])
        return t

    def _cell(used, cap, is_usd):
        fmt = (lambda v: f"${v:,.2f}") if is_usd else (lambda v: f"{int(v):,}")
        if not cap:
            return f"{fmt(used)} / —"
        pct = used / cap * 100
        col = ("s-failure" if pct >= 100 else
               "s-unknown" if pct >= 80 else "s-success")
        return (f"<span style='color:var(--{col})'>{fmt(used)} / {fmt(cap)}"
                f"({pct:.0f}%)</span>")

    g = budget_cfg or {}
    rows = [f"<tr><td><b>全站(global)</b></td>"
            f"<td>{_cell(_sum('cost'), g.get('monthly_max_usd'), True)}</td>"
            f"<td>{_cell(_sum('tokens'), g.get('monthly_max_tokens'), False)}"
            f"</td></tr>"]
    for name in sorted(profiles):
        p = profiles[name]
        rows.append(
            f"<tr><td>{esc(name)}</td>"
            f"<td>{_cell(_sum('cost', name), p.monthly_max_usd, True)}</td>"
            f"<td>{_cell(_sum('tokens', name), p.monthly_max_tokens, False)}"
            f"</td></tr>")
    return ("<h2>budget 當月用量 vs 上限(月/agent + 全站)</h2><div class='card'>"
            "<table><thead><tr><td><b>範圍</b></td><td><b>USD 用量/上限</b></td>"
            "<td><b>token 用量/上限</b></td></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table>"
            "<div class='sys' style='text-align:left'>綠 &lt;80% · 黃 ≥80% · 紅 "
            "≥100%。<b>token 管理 / max 管理</b>:每票有 soft/hard 兩層(token+usd);"
            "達 <b>per-ticket soft</b> → 使用者自助增額表單(≤hard);達 <b>hard / 月 / 全站</b>"
            " → 只管理者改 config + hot reload,達上限即 pending:budget。上表是月/全站 hard;"
            "per-ticket soft/hard 見各 Profile 卡。</div></div>")


# ── W7.6:概念/生命週期/狀態機頁(純 SVG,零依賴)────────────────────────── #
# 8 態節點:key → (cx, cy, 中文)。座標經手調,盡量少交叉。
# 顏色改由 CSS class st-<key>(見 CSS #smsvg 區)驅動,隨明暗主題變。
# W10.1 HIL 模型狀態機(目標設計;含 triage 閘與 base 跨票交接,行為 W10.2/W10.3
# 才接線,此圖先作為 spec)。節點:(cx, cy, 標籤)
_SM_NODES = {
    "todo": (95, 255, "待處理"),
    "hil_middle": (325, 90, "HIL(Middle)"),
    "running": (325, 255, "進行中"),
    "queued": (325, 415, "排隊"),
    "hil_end": (590, 170, "HIL(End)"),
    "aborted": (590, 410, "撤銷/交接"),
    "closed": (865, 170, "關票·離開"),
}
# 轉移:(from, to, 標籤)。標籤縮短(完整語意見下方表格/說明);HIL(Middle) 兼
# 「開跑前 triage/審批」與「過程中等人」;HIL(End) 結果=成功/失敗/未定,人評分後
# (A)關票 或 (B)重置額度續跑。
_SM_EDGES = [
    ("todo", "running", "路由·派工"),
    ("todo", "hil_middle", "triage/審批"),
    ("hil_middle", "running", "resume"),
    ("running", "hil_middle", "需人"),
    ("running", "queued", "額滿"),
    ("queued", "running", "有額度"),
    ("running", "hil_end", "完成/未定"),
    ("hil_end", "running", "(B)續跑 / 同票換手"),
    ("hil_end", "closed", "(A)關票"),
    ("hil_middle", "aborted", "decline"),
    ("running", "aborted", "cancel/關Done"),
    ("hil_end", "aborted", "跨票交接(base)"),
]


def _sm_svg() -> str:
    """狀態機 SVG(7 節點=6 態 + 概念終點 closed):中心→中心連線裁切到矩形邊界 +
    箭頭 + 雙向邊垂直偏移。W10.3:aborted 節點兼「撤銷/交接」、hil_end→aborted=跨票換手。"""
    hw, hh = 62, 22           # 節點半寬/半高
    W, H = 1000, 490
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
            # 標籤放邊的 40% 處(非中點):雙向邊(a→b 與 b→a)因起點相反,分別落在
            # 40% 與 60%,自然錯開不重疊;再加 2× 垂直偏移拉開左右
            mx = x1 + (x2 - x1) * 0.4 + ox * 2
            my = y1 + (y2 - y1) * 0.4 + oy * 2
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


# ── W10.4:模組架構(分層 + 核心模組)──────────────────────────────────────── #
# 每模組:(顯示名, 職責, trigger 時間, input, output, 上游, 下游)。
_ARCH_MODULES = {
    "jira_source": ("jira_source", "Jira Cloud 讀寫封裝(search/comment/"
                    "transition/set_description,含 write_retry + on_write 回呼)",
                    "poller 每輪 / 各政策要寫入時", "JQL、issue_id、寫入動作",
                    "Ticket/Comment 物件、寫入結果", "poller·dispatcher·各政策",
                    "Jira Cloud REST"),
    "triggers": ("triggers", "內部排程觸發源(scheduled agent / script)",
                 "poller 每輪查 due", "trigger 定義 + store 上次執行時間",
                 "到期 trigger → 派工/跑 script", "poller", "dispatcher·script"),
    "poller": ("poller(OuterLoop)", "外圈輪詢:diff 變更→journal→協調派工/指令/"
               "政策/評分", "run_poller 定時迴圈(interval 秒)",
               "JQL 搜到的票 + store watch 狀態",
               "journal 事件流 + 派工決策", "run_poller",
               "routing·gate·dispatcher·commands·external·scoring·store"),
    "routing": ("routing", "票 → route/profile 比對(when 條件式)",
                "poller 每票", "Ticket 欄位 + config.yaml",
                "Route(profile, on_match)", "poller", "poller·dispatcher"),
    "gate": ("gate(F1)", "分層並發額度閘(global / per-engine / per-profile)",
             "poller 有候選待派時", "候選清單 + in-flight 計數",
             "selected / queued 劃分", "poller", "dispatcher"),
    "dispatcher": ("dispatcher", "派工:審批門→provision workspace→跑 attempt→"
                   "寫 envelope→更新 session",
                   "poller 選中候選(create_or_resume)",
                   "Ticket + profile + store session",
                   "attempt 執行 + envelope + session/journal 更新",
                   "poller·gate", "approval·workspace·inner_runner·contract·store"),
    "inner_runner": ("inner_runner", "實跑 claude -p / codex exec 一個 attempt"
                     "(看門狗 / killpg / native resume)",
                     "dispatcher 呼叫", "prompt、workspace、session_id",
                     "raw 結果 + cost + session_id", "dispatcher",
                     "claude / codex CLI"),
    "workspace": ("workspace / isolation", "template→workspace instance "
                  "provision(不變 id 綁 cwd)+ 隔離",
                  "dispatcher 首次 fork", "profile.template + issue_id",
                  "workspace 路徑", "dispatcher", "檔案系統"),
    "contract": ("contract", "envelope 結構化契約 + grader 三態判定"
                 "(證據型停止)", "attempt 結束", "raw agent 輸出",
                 "envelope + outcome(SUCCESS/FAILURE/UNKNOWN)",
                 "dispatcher·inner_runner", "store"),
    "approval": ("approval", "起點審批 / triage 閘(寫 plan 進 description、等 "
                 "human 段 agent_name;可 decline)",
                 "dispatcher fork 前(require_approval / 全域 triage)",
                 "Ticket.description + profile",
                 "proceed/awaiting/reprompt + description 寫入",
                 "dispatcher", "sections·jira_source·store"),
    "scoring": ("scoring(ScoreGate)", "HIL(End) 人評分(seed score 佔位、讀 "
                "score、週期催評)", "poller 每輪對終態未評票",
                "description human 段 + session",
                "human_score + journal", "poller",
                "sections·jira_source·store"),
    "commands": ("commands", "指令核心 apply_command(run/retry/hold/stop/cancel/"
                 "next):人走指令台表單、自動化走 REST API",
                 "form_server / control_api 呼叫", "issue_id + cmd + by(email)",
                 "指令效果(解 pending / 換手…)+ journal", "form_server·control_api",
                 "store·jira_source"),
    "external": ("external(離手政策)", "assignee/status 政策:交人讓額度、回機器人"
                 "resume、外部關 Done=撤銷", "poller 偵測 status/assignee 變更",
                 "Ticket 變更 + bot_id", "inactive/abort/resume + journal",
                 "poller", "store·jira_source"),
    "sections": ("sections", "description 三方分段(human/control/agent:<名>)"
                 "parse/render + hash 防篡改", "approval/scoring/commands 讀寫時",
                 "description 文字", "Section 物件 / 組回 description",
                 "approval·scoring·commands", "(純函式)"),
    "store": ("store", "SQLite 狀態(ticket_watch / ticket_session)+ append-only "
              "journal(events.jsonl)", "各模組讀寫", "watch/session upsert、"
              "journal 事件", "持久狀態 + 事件流", "幾乎全部模組", "SQLite / 檔案"),
    "control_api": ("control_api", "內嵌 REST 控制面(pause/resume/reload/"
                    "shutdown/evict/gen_transcript/status)", "人經 dashboard/"
                    "curl POST(即時)", "HTTP 請求",
                    "poller 控制副作用 + status JSON", "人 / dashboard",
                    "poller·store·transcript"),
    "detail_server": ("detail_server(本檔)", "唯讀觀測 dashboard:KPI/圖/表/"
                      "狀態機/概念/REST /api/v1", "瀏覽器請求 + 5s live 刷新",
                      "store(runtime 目錄)", "HTML / JSON", "人",
                      "store(唯讀)"),
    "transcript": ("transcript", "session → final HTML / 打包(換手/交人/evict/"
                   "close / 被動按鈕)", "finalize 事件 或 control gen_transcript",
                   "session 原始 log", "final.html / transcript.tgz",
                   "dispatcher·commands·control_api", "檔案系統"),
    "retention": ("retention", "workspace 回收(過期 / 終態,釋放磁碟)",
                  "poller 週期(約每 240 輪 ≈ 每小時)",
                  "store session + profile 保留策略", "回收 workspace + journal",
                  "poller", "檔案系統 / store"),
    "interaction": ("interaction", "受控表單 schema + 一次性 token + 提交驗證"
                    "(純邏輯,零副作用)", "hil / form_server 呼叫時",
                    "schema_id + 提交欄位", "InteractionRequest / 驗證結果",
                    "hil·form_server", "(純函式)"),
    "hil": ("hil", "HIL 整合膠水:發起一次性表單(@mention+連結)+ 套用提交"
            "(回寫 human 段 + resume / 評分 / 關單 / handoff)",
            "scoring·commands 發起時 · 表單提交回呼時",
            "issue_id + schema + 提交資料",
            "InteractionRequest + journal(hil_*/handoff/base_injected)",
            "scoring·commands·form_server",
            "interaction·store·jira_source·workspace"),
    "form_server": ("form_server", "一次性 token 表單 HTTP 服務(人面向、獨立 port);"
                    "Jira 健康把關,提交回呼 hil", "人開一次性連結(即時)",
                    "HTTP GET/POST + token", "表單頁 / 提交 → on_submit",
                    "人(瀏覽器)", "store·hil"),
}
_ARCH_LAYERS = [
    ("輸入層", "Jira / 觸發源", ["jira_source", "triggers"]),
    ("決策層", "輪詢 · 路由 · 額度閘", ["poller", "routing", "gate"]),
    ("執行層", "派工 · 執行 · 工作區", ["dispatcher", "inner_runner",
                                       "workspace", "contract"]),
    ("人機協作層", "HIL(審批·評分·指令·離手·表單)", ["approval", "scoring",
                                                     "commands", "external",
                                                     "sections", "interaction",
                                                     "hil", "form_server"]),
    ("狀態·觀測·控制層", "持久 · 觀測 · 控制", ["store", "control_api",
                                               "detail_server", "transcript",
                                               "retention"]),
]


# W10.7:模組 graph 的邊(from, to, 資料名)。dataflow=input/output;store 為樞紐。
_ARCH_EDGES = [
    ("jira_source", "poller", "Ticket/Comment"),
    ("triggers", "poller", "到期trigger"),
    ("poller", "jira_source", "search/寫入"),
    ("poller", "routing", "Ticket"),
    ("routing", "gate", "Route"),
    ("poller", "gate", "候選"),
    ("gate", "dispatcher", "selected"),
    ("dispatcher", "approval", "Ticket/profile"),
    ("approval", "sections", "description"),
    ("approval", "jira_source", "plan/comment"),
    ("dispatcher", "workspace", "provision"),
    ("workspace", "inner_runner", "ws 路徑"),
    ("dispatcher", "inner_runner", "prompt/session"),
    ("inner_runner", "contract", "raw 輸出"),
    ("contract", "dispatcher", "envelope/outcome"),
    ("dispatcher", "store", "session/journal"),
    ("dispatcher", "transcript", "finalize"),
    ("poller", "commands", "新 comment"),
    ("commands", "jira_source", "指令回覆"),
    ("commands", "store", "指令效果"),
    ("commands", "transcript", "handoff"),
    ("poller", "external", "status/assignee"),
    ("external", "jira_source", "留言/assign"),
    ("external", "store", "inactive/abort"),
    ("poller", "scoring", "終態票"),
    ("scoring", "sections", "human 段"),
    ("scoring", "jira_source", "評分 comment"),
    ("scoring", "store", "human_score"),
    ("poller", "retention", "週期掃描"),
    ("retention", "store", "回收/journal"),
    ("control_api", "poller", "pause/evict"),
    ("control_api", "store", "status 讀"),
    ("control_api", "transcript", "gen_transcript"),
    ("store", "detail_server", "唯讀狀態/journal"),
    ("store", "control_api", "status 計數"),
    # W11/W10.3:HIL 一次性表單(scoring/commands 發起 → form_server 提交 → hil 套用)
    ("scoring", "hil", "發 score_and_close"),
    ("commands", "hil", "發 hold 表單"),
    ("form_server", "hil", "提交回呼"),
    ("store", "form_server", "取請求(token)"),
    ("hil", "interaction", "建請求/驗證"),
    ("hil", "store", "interaction/journal"),
    ("hil", "jira_source", "回寫 human 段/建票"),
    ("hil", "workspace", "人類指示/base 注入"),
]


def _arch_svg() -> str:
    """W10.4:分層模組架構圖(手繪 SVG,隨明暗主題;svg-pan-zoom 於 W10.5 掛上)。
    5 個橫向分層帶,由上而下=資料流方向;左側層標籤軌,右側模組 chip。"""
    W, bandH, top = 1040, 96, 16
    railW, chipWmax, chipH, gap = 150, 156, 46, 14
    cx0 = 16 + railW + 20
    avail = W - cx0 - 16                       # chip 可用寬(扣掉左軌與右邊距)
    H = top * 2 + len(_ARCH_LAYERS) * bandH
    out = ["<svg id='archsvg' viewBox='0 0 %d %d' width='100%%' "
           "preserveAspectRatio='xMinYMin meet' "
           "style='max-height:%dpx;font-size:11px'>" % (W, H, H),
           "<defs><marker id='aah' viewBox='0 0 10 10' refX='9' refY='5' "
           "markerWidth='7' markerHeight='7' orient='auto-start-reverse'>"
           "<path class='a-arrow' d='M0,0 L10,5 L0,10 z'/></marker></defs>"]
    for i, (lname, ldesc, mods) in enumerate(_ARCH_LAYERS):
        by = top + i * bandH
        # 分層背景帶
        out.append(f"<rect class='a-band' x='8' y='{by}' width='{W - 16}' "
                   f"height='{bandH - 12}' rx='10'/>")
        # 左側層標籤軌
        out.append(
            f"<rect class='a-rail' x='16' y='{by + 8}' width='{railW}' "
            f"height='{bandH - 28}' rx='8'/>"
            f"<text class='a-rname' x='{16 + railW / 2}' y='{by + 30}' "
            f"text-anchor='middle'>{esc(lname)}</text>"
            f"<text class='a-rdesc' x='{16 + railW / 2}' y='{by + 48}' "
            f"text-anchor='middle'>{esc(ldesc)}</text>")
        # 模組 chip:寬度隨該層模組數自適應(多模組層縮小以免溢出 viewBox)
        n = len(mods)
        chipW = min(chipWmax, (avail - gap * (n - 1)) / n) if n else chipWmax
        cy = by + (bandH - 12 - chipH) / 2
        for j, mk in enumerate(mods):
            x = cx0 + j * (chipW + gap)
            nm = _ARCH_MODULES[mk][0]
            out.append(
                f"<rect class='a-chip' x='{x}' y='{cy}' width='{chipW}' "
                f"height='{chipH}' rx='8'/>"
                f"<text class='a-name' x='{x + chipW / 2}' "
                f"y='{cy + chipH / 2 + 4}' text-anchor='middle'>"
                f"{esc(nm)}</text>")
        # 層與層之間的資料流箭頭(左緣)
        if i < len(_ARCH_LAYERS) - 1:
            ax = 16 + railW / 2
            out.append(
                f"<line class='a-flow' x1='{ax}' y1='{by + bandH - 20}' "
                f"x2='{ax}' y2='{by + bandH + 2}' marker-end='url(#aah)'/>")
    out.append("</svg>")
    return "".join(out)


def _graph_node_pos() -> dict:
    """W10.7:graph 節點座標——依 _ARCH_LAYERS 分 5 列(rowH),每列在寬度上平均分佈。"""
    W, rowH, top = 1300, 140, 44
    margin = 90
    pos = {}
    for i, (_ln, _ld, mods) in enumerate(_ARCH_LAYERS):
        y = top + i * rowH + rowH / 2
        n = len(mods)
        for j, mk in enumerate(mods):
            x = margin + (W - 2 * margin) * (j + 0.5) / n
            pos[mk] = (x, y, i)
    return pos, W, top * 2 + len(_ARCH_LAYERS) * rowH


def _graph_svg() -> str:
    """W10.7:模組 node+edge graph(手繪 SVG)。節點依層著色;邊=input/output 並標
    資料名;每個 node/edge 帶 data-* 供過濾器與 focus 高亮;svg-pan-zoom 於下方掛上。"""
    pos, W, H = _graph_node_pos()
    hw, hh = 64, 19
    out = ["<svg id='graphsvg' viewBox='0 0 %d %d' width='100%%' "
           "preserveAspectRatio='xMinYMin meet' "
           "style='max-height:%dpx;font-size:11px'>" % (W, H, H),
           "<defs><marker id='gah' viewBox='0 0 10 10' refX='9' refY='5' "
           "markerWidth='7' markerHeight='7' orient='auto-start-reverse'>"
           "<path class='g-arrow' d='M0,0 L10,5 L0,10 z'/></marker></defs>"]

    def trim(cx, cy, tx, ty):
        dx, dy = tx - cx, ty - cy
        if dx == 0 and dy == 0:
            return cx, cy
        sx = hw / abs(dx) if dx else 9e9
        sy = hh / abs(dy) if dy else 9e9
        t = min(sx, sy)
        return cx + dx * t, cy + dy * t

    # 先畫邊(在節點下層),各帶 data-from/data-to;雙向偏移避免重疊
    for a, b, label in _ARCH_EDGES:
        if a not in pos or b not in pos:
            continue
        ax, ay, _ = pos[a]
        bx, by, _ = pos[b]
        dx, dy = bx - ax, by - ay
        ln = (dx * dx + dy * dy) ** 0.5 or 1
        ox, oy = -dy / ln * 5, dx / ln * 5
        x1, y1 = trim(ax + ox, ay + oy, bx + ox, by + oy)
        x2, y2 = trim(bx + ox, by + oy, ax + ox, ay + oy)
        mx = x1 + (x2 - x1) * 0.5 + ox * 1.6
        my = y1 + (y2 - y1) * 0.5 + oy * 1.6
        out.append(
            f"<g class='gedge' data-from='{a}' data-to='{b}'>"
            f"<line x1='{x1:.0f}' y1='{y1:.0f}' x2='{x2:.0f}' y2='{y2:.0f}' "
            f"marker-end='url(#gah)'/>"
            f"<text x='{mx:.0f}' y='{my:.0f}' text-anchor='middle'>"
            f"{esc(label)}</text></g>")
    # 再畫節點(上層可點),data-mod + data-layer + 層色 class gl-<i>
    for i, (_ln, _ld, mods) in enumerate(_ARCH_LAYERS):
        for mk in mods:
            cx, cy, _ = pos[mk]
            out.append(
                f"<g class='gnode gl-{i}' data-mod='{mk}' data-layer='{i}' "
                f"onclick='gFocus(\"{mk}\")'>"
                f"<rect x='{cx - hw:.0f}' y='{cy - hh:.0f}' width='{hw * 2}' "
                f"height='{hh * 2}' rx='7'/>"
                f"<text x='{cx:.0f}' y='{cy + 4:.0f}' text-anchor='middle'>"
                f"{esc(mk)}</text></g>")
    out.append("</svg>")
    return "".join(out)


def render_graph_section() -> str:
    """W10.7:graph 圖 + 多選過濾器(依層分組)+ focus 高亮。"""
    # 過濾器:全選/全不選 + 每層一組 checkbox(預設全開)
    groups = []
    for i, (lname, _ld, mods) in enumerate(_ARCH_LAYERS):
        chips = "".join(
            f"<label class='gchk'><input type='checkbox' checked "
            f"data-mod='{mk}' data-layer='{i}' onchange='gVisible()'> "
            f"{esc(mk)}</label>" for mk in mods)
        groups.append(
            f"<div class='glayer'><button type='button' class='glname' "
            f"onclick='gLayer({i})'>{esc(lname)}</button>{chips}</div>")
    fil = (
        "<div id='gfilter' class='gfilter'>"
        "<div class='gbtns'>"
        "<button type='button' onclick='gAll(true)'>全選</button>"
        "<button type='button' onclick='gAll(false)'>全不選</button>"
        "<span class='sys'>點模組方塊 = 只亮它的 in/out 邊;取消勾選 = 隱藏該模組與其邊</span>"
        "</div>" + "".join(groups) + "</div>")
    return (
        "<h2>模組 graph(node + edge · 邊=input/output)</h2>"
        f"<div class='card'>{fil}{_graph_svg()}"
        "<p class='sys' style='text-align:left;margin-top:6px'>"
        "🖐 拖曳平移、角落鈕縮放;<b>很密沒關係</b>——用上面過濾器挑要看的模組,或點方塊 "
        "focus。<b>store</b> 是樞紐(多數模組讀寫它)。</p></div>"
        + _GRAPH_JS)


# W10.1 HIL 模型:6 態 + closed 概念終點。第三欄=此態如何由 DB 欄位推導
# (canonical_state 唯讀映射,不改 runtime)。
_STATE_DOC = [
    ("待處理 todo", "被 watch 到、尚無 session(還沒派工或不歸任何 route)。",
     "ticket_watch 有列、但 ticket_session 無此 issue_id"),
    ("進行中 running", "有 active session 正在跑 attempt(占機器額度)。",
     "outcome=NULL 且 pending_reason=NULL 且 queued=0 且 inactive=0"),
    ("排隊 queued", "本輪並發額滿,下輪重評(F1 分層閘門)。",
     "ticket_session.queued=1"),
    ("HIL(Middle) 過程中等人",
     "合併舊「交人 + 等待人類」:開跑前 triage/審批,或過程中需人(預算/交人)。"
     "不佔額度;assignee→機器人、且 description human 段條件滿足才查排隊+resume。"
     "原因(審批/待審視/預算/交人)供徽章顯示。",
     "inactive=1 或 pending_reason 非空(且 outcome 非終態)"),
    ("HIL(End) 終點交人",
     "跑完(成功/失敗/未定)轉人評分(0–10):人續做→關票,或判可續→重置額度續跑。"
     "結果=outcome。",
     "outcome ∈ {SUCCESS, FAILURE, UNKNOWN}"),
    ("撤銷/交接 aborted",
     "人在看板關 Done/Cancelled、指令台 cancel,或交接→新票被 supersede。",
     "ticket_session.outcome='ABORTED'"),
    ("關票·離開 closed(概念終點)",
     "HIL(End) 後人關 Jira(Done)→ 票離開 jql 視野;非 DB 態,session 保留最後"
     "result+score 供稽核。",
     "(無 DB 欄位;Jira status=Done 從 jql 消失)"),
]


# W10.5:svg-pan-zoom(vendored,離線)掛上狀態機 + 架構圖 → 可拖曳平移 / 按鈕縮放。
# mouseWheelZoomEnabled:false 避免劫持頁面滾動;控制圖示(+/-/reset)由 lib 內建。
_SVGPZ_JS = (
    "<script src='/tvendor/svg-pan-zoom.min.js'></script>"
    "<script>(function(){"
    "function pz(id){var el=document.getElementById(id);"
    "if(!el||!window.svgPanZoom)return;"
    "var mh=getComputedStyle(el).maxHeight;"
    "if(mh&&mh!=='none')el.style.height=mh;el.style.width='100%';"
    "try{svgPanZoom(el,{zoomEnabled:true,controlIconsEnabled:true,"
    "panEnabled:true,dblClickZoomEnabled:true,mouseWheelZoomEnabled:false,"
    "fit:true,center:true,contain:true,minZoom:0.4,maxZoom:12,"
    "zoomScaleSensitivity:0.35});}catch(e){}}"
    "function go(){pz('smsvg');pz('archsvg');pz('graphsvg');}"
    "if(document.readyState!=='loading')go();"
    "else document.addEventListener('DOMContentLoaded',go);"
    "})();</script>")

# W10.7:graph 過濾器(依 data-mod 顯/隱)+ focus 高亮(點節點只亮其 in/out 邊)
_GRAPH_JS = (
    "<script>(function(){"
    "function q(s){return document.querySelectorAll(s);}"
    "window.gVisible=function(){var on={};"
    "q('#gfilter input[data-mod]').forEach(function(c){on[c.dataset.mod]=c.checked;});"
    "q('#graphsvg .gnode').forEach(function(n){"
    "n.style.display=on[n.dataset.mod]?'':'none';});"
    "q('#graphsvg .gedge').forEach(function(e){"
    "e.style.display=(on[e.dataset.from]&&on[e.dataset.to])?'':'none';});};"
    "window.gAll=function(v){q('#gfilter input[data-mod]')"
    ".forEach(function(c){c.checked=v;});gVisible();};"
    "window.gLayer=function(i){var cs=q('#gfilter input[data-layer=\"'+i+'\"]');"
    "var any=[].some.call(cs,function(c){return !c.checked;});"
    "cs.forEach(function(c){c.checked=any;});gVisible();};"
    "var gFoc=null;"
    "window.gFocus=function(mod){gFoc=(gFoc===mod)?null:mod;var nb={};"
    "if(gFoc){q('#graphsvg .gedge').forEach(function(e){"
    "if(e.dataset.from===gFoc)nb[e.dataset.to]=1;"
    "if(e.dataset.to===gFoc)nb[e.dataset.from]=1;});}"
    "q('#graphsvg .gnode').forEach(function(n){"
    "var keep=!gFoc||n.dataset.mod===gFoc||nb[n.dataset.mod];"
    "n.classList.toggle('dim',!!gFoc&&!keep);"
    "n.classList.toggle('foc',gFoc===n.dataset.mod);});"
    "q('#graphsvg .gedge').forEach(function(e){"
    "var hit=!!gFoc&&(e.dataset.from===gFoc||e.dataset.to===gFoc);"
    "e.classList.toggle('dim',!!gFoc&&!hit);"
    "e.classList.toggle('hi',hit);});};"
    "})();</script>")


# W10.6:職責表補「檔名 / 重要 API」(分層由 _ARCH_LAYERS 提供)。旁掛不動 _ARCH_MODULES。
_ARCH_META = {
    "jira_source": ("arcp/jira_source.py",
                    "search / get_comments / add_comment / transition / "
                    "set_description / assign"),
    "triggers": ("arcp/triggers.py",
                 "load_triggers / parse_cron / due / run_trigger"),
    "poller": ("arcp/poller.py", "OuterLoop.poll_once"),
    "routing": ("arcp/routing.py", "load_config / match"),
    "gate": ("arcp/gate.py", "select_dispatchable / engine_of"),
    "dispatcher": ("arcp/dispatcher.py", "Dispatcher.handle"),
    "inner_runner": ("arcp/inner_runner.py",
                     "run_attempt / AttemptResult"),
    "workspace": ("arcp/workspace.py, isolation.py",
                  "provision / health_check / isolation.resolve"),
    "contract": ("arcp/contract.py", "validate_structured / summarize"),
    "approval": ("arcp/approval.py", "ApprovalGate.gate"),
    "scoring": ("arcp/scoring.py",
                "ScoreGate.on_poll / collect_score / write_handoff_sections"),
    "commands": ("arcp/commands.py", "apply_command(指令台 + REST API)"),
    "external": ("arcp/commands.py",
                 "ExternalChangePolicy.on_status_changed / on_assignee_changed"),
    "sections": ("arcp/sections.py",
                 "parse / render / verify_and_restore"),
    "store": ("arcp/store.py",
              "Store.upsert / journal / get_session / all_sessions"),
    "control_api": ("arcp/control_api.py",
                    "ControlAPI.status (+POST /pause /resume /evict …)"),
    "detail_server": ("detail_server.py",
                      "render_index / render_ticket / /data / /api/v1"),
    "transcript": ("arcp/transcript.py",
                   "finalize / engine_of_agent"),
    "retention": ("arcp/retention.py", "reclaim"),
    "interaction": ("arcp/interaction.py",
                    "FORM_SCHEMAS / build_request / validate_submission / "
                    "gen_token"),
    "hil": ("arcp/hil.py",
            "request_human / apply_submission / _do_handoff"),
    "form_server": ("arcp/form_server.py",
                    "FormServer / process_submission"),
}


def _arch_doc_table() -> str:
    """W10.4/W10.6:模組職責表(依 _ARCH_LAYERS 分層順序;欄=模組/檔名/分層/職責/
    重要 API/trigger/輸入/輸出/上游/下游)。"""
    head = ("<tr><td><b>模組</b></td><td><b>檔名</b></td><td><b>分層</b></td>"
            "<td><b>職責</b></td><td><b>重要 API</b></td>"
            "<td><b>trigger 時間</b></td><td><b>輸入</b></td>"
            "<td><b>輸出</b></td><td><b>上游</b></td><td><b>下游</b></td></tr>")
    rows = []
    for lname, _ldesc, mods in _ARCH_LAYERS:
        for mk in mods:
            nm, job, trig, inp, outp, up, down = _ARCH_MODULES[mk]
            fn, api = _ARCH_META.get(mk, ("", ""))
            rows.append(
                f"<tr><td style='white-space:nowrap;color:var(--ink)'>"
                f"<b>{esc(nm)}</b></td>"
                f"<td class='mono' style='font-size:11px;text-align:left;"
                f"color:var(--muted)'>{esc(fn)}</td>"
                f"<td class='sys' style='white-space:nowrap'>{esc(lname)}</td>"
                f"<td class='sys' style='text-align:left'>{esc(job)}</td>"
                f"<td class='mono' style='font-size:11px;text-align:left;"
                f"color:var(--accent-ink)'>{esc(api)}</td>"
                f"<td class='sys' style='text-align:left'>{esc(trig)}</td>"
                f"<td class='sys' style='text-align:left'>{esc(inp)}</td>"
                f"<td class='sys' style='text-align:left'>{esc(outp)}</td>"
                f"<td class='sys' style='text-align:left'>{esc(up)}</td>"
                f"<td class='sys' style='text-align:left'>{esc(down)}</td></tr>")
    return ("<table style='font-size:12px;min-width:1180px'>" + head
            + "".join(rows) + "</table>")


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
        f"{_nav('concepts')}<header><h1>Introduction · 資料流生命週期 · 狀態機</h1>"
        f"</header><main id='main' tabindex='-1'>"
        "<h2>一句話</h2><div class='card'><p>ARCP 讓 <code>claude -p</code> / "
        "<code>codex exec</code> 由 <b>Jira 事件驅動</b>、可觀測、可控制。搞定系統"
        "先搞定<b>資料流的生命週期</b>——下面是一張票從進來到離開的狀態流動。</p></div>"
        "<h2>進場 · 選型 · 排程 · 指令 · 額度(靜態概念)</h2><div class='card'>"
        "<p class='sys' style='text-align:left'>先懂「票怎麼進來、由誰做、怎麼控」,"
        "再看下面的動態生命週期。</p>"
        "<ul style='line-height:1.8'>"
        "<li><b>label = 入場券</b>:poller 只撿「<b>有命中某條 route 的 label</b>」的票"
        "去派工;沒命中的 label(或沒 label)票再有效也不跑。ARCP 自己的 label 一律 "
        "<code>arcp.</code> 前綴(命名空間,避免撞團隊既有 label,如 "
        "<code>arcp.filechain</code>)。<b>建票那一刻</b>決定要不要給入場券"
        "(CR→Jira bridge / job / 人)。</li>"
        "<li><b>選型(route → select → 鎖定 profile)</b>:route 命中決定<b>初始</b> "
        "profile;若該 profile 設了 <code>select</code>,派工前先「選型」決定<b>最終</b>"
        "鎖哪個 —— <code>random</code>(同族 A/B 分流)或 <code>script</code>(腳本讀"
        "票/CRID 回一個 profile,可回<b>任何已定義 profile</b>、還能<b>遞歸</b>多層 "
        "triage,最多 10 層防繞圈)。選定後<b>鎖進 session</b>(resume 不重選)。</li>"
        "<li><b>job(排程來源,非 Jira 驅動)</b>:config 的 <code>triggers</code> 由"
        "排程觸發。<code>agent-job</code> 跑腳本吐 JSON 任務 → <b>像人一樣建 Jira 票</b>"
        "(不建 session、不鎖 profile)→ 交回 poller 走 route/triage;"
        "<code>script-job</code> 純做事不開票。兩者 stdout/stderr log 都存 runs/… "
        "可在 dashboard 看可下載。</li>"
        "<li><b>指令台(command console)</b>:人要對某票下指令(暫停/恢復/取消/換手…),"
        "走該票專屬的<b>指令台表單連結</b>(capability-URL、綁票常駐到 close),表單依"
        "<b>當前狀態</b>列出可用指令並說明用途/副作用;自動化改走 REST。<b>取代</b>舊的"
        "「Jira 留言 @agent 指令」。</li>"
        "<li><b>budget(額度護欄)</b>:每票 / 每月每 agent / 全站 × token/usd 共 <b>6 層"
        "上限</b>,每輪派工前 precheck。per-ticket <b>soft</b> 破 → 使用者<b>自助增額"
        "表單</b>(≤hard);<b>hard / 月 / 全站</b> 破 → 只管理者能改(hot reload)。</li>"
        "<li><b>身分門禁(選填)</b>:票的 description 填了 <code>email</code>(可逗號"
        "多個,存負責人名單)就<b>上鎖</b>——HIL 表單 / 指令台提交的 email 要 ∈ 負責人"
        "名單 / ∈ 全站 <code>admin_emails</code> / == 該 profile 審批者才放行(沒填的票"
        "不受限)。全程留 email + 來源 IP 稽核。改負責人走指令台 <code>set_email</code>"
        "(<b>整組取代</b>,表單預填現值;re-tag 每位新人 + 重貼待填表單);開票時把 "
        "profile 審批者加為 Jira watcher。</li>"
        "</ul></div>"
        "<h2>Jira ticket 狀態機(harness 內部 · HIL 模型 6 態 + 概念終點)</h2>"
        f"<div class='card'>{_sm_svg()}"
        "<p class='sys' style='text-align:left;margin-top:6px'>"
        "🖐 拖曳平移、角落 <b>+ / − / ⟳</b> 鈕縮放/重置(離線 svg-pan-zoom)。</p>"
        "</div>"
        "<h2>HIL(Human In the Loop)模型</h2><div class='card'>"
        "<ul style='line-height:1.8'>"
        "<li><b>合併</b>:舊「交人 inactive」與「等待人類 pending」語意一致,合併成 "
        "<b>HIL(Middle)</b>(過程中等人:開跑前 triage/審批,或跑到一半需人給預算/交人)。"
        "帶「原因」供徽章區分。</li>"
        "<li><b>HIL(End)</b>:跑完(成功/失敗/未定,<code>outcome</code> 三態即"
        "「結果」屬性,不再是頂層狀態)轉人評分。<b>三訊號並列對照</b>:"
        "<code>grader</code>(S/F/U,證據型、決定狀態)+ <b>agent 自評 0–10</b>"
        "(自報)+ <b>人類 0–10</b>(human_score)。人評分後 <b>(A)</b> 續做關票、"
        "或 <b>(B)</b> 判可續 → native resume + 重置額度回「進行中」。</li>"
        "<li><b>resume 觸發</b>(W11):<b>HIL 一次性表單提交=唯一信號</b>——"
        "提交後清 pending、下輪 native resume(assignee 恆定為 bot,不靠切 "
        "assignee;審批門也是表單提交即放行)。</li>"
        "<li><b>closed</b> 是概念終點:人關 Jira(Done)→ 票離開 jql 視野(非 DB 態)。</li>"
        "</ul></div>"
        "<h2>等人的全部狀況(pending 原因 × 處理)</h2><div class='card' "
        "style='overflow-x:auto'><table>"
        "<tr><th>原因</th><th>什麼時候</th><th>人要做什麼</th></tr>"
        "<tr><td><code>approval</code></td><td>profile 要求審批才開跑</td>"
        "<td>審批表單看 plan、填 agent 名 → <b>提交即放行</b></td></tr>"
        "<tr><td><code>security</code></td><td>TICKET.md 安全掃描命中/掃描器故障"
        "(fail-closed)</td><td>表單看原文+命中理由:修文字→繼續,或 abort"
        "(Security)</td></tr>"
        "<tr><td><code>budget</code></td><td>單票 soft 上限破</td><td>增額表單自助調高"
        "(≤hard);超 hard/月/全站→管理者改 config</td></tr>"
        "<tr><td><code>hold</code></td><td>人按了 hold(先 killpg,不耗 attempt)</td>"
        "<td>表單填新指示 → agent 帶著 resume</td></tr>"
        "<tr><td><code>human-decision</code></td><td>agent 交人(handoff)或 stop</td>"
        "<td>人工接手;要 agent 續 → 指令台 run/retry</td></tr>"
        "<tr><td><code>unknown</code></td><td>行程消失、副作用無法證明</td>"
        "<td>查 transcript/workspace 後下指令(<b>不自動重試</b>)</td></tr>"
        "<tr><td><code>external</code></td><td>infra 故障(server 掛)</td>"
        "<td>不用動:修好自動續、不耗 attempt</td></tr>"
        "<tr><td><b>HIL(End)</b></td><td>終態等評分</td><td>score_and_close:評分 "
        "0–10 必填 + 關單/打回/換手(或 profile 設 auto_close 全自動)</td></tr>"
        "</table><p class='sys' style='text-align:left;margin-top:6px'>"
        "共用:一次性 token 連結(自癒補發)、@mention 通知、email 身分門禁、"
        "狀態同步(Middle→Pending、End→Resolve)。正本:docs/design/interaction.md "
        "§3.2。</p></div>"
        "<h2>TICKET.md(agent 的任務簡報)與 description 變數</h2><div class='card'>"
        "<p class='sys' style='text-align:left;line-height:1.8'>"
        "agent <b>不連 Jira</b>,只讀工作區的 TICKET.md——資訊進得去的通道:"
        "<b>① description 本文</b>(自然語言下指令,整段進「描述」段;頂部可加 "
        "yaml 變數行,<b>只認三鍵</b>:<code>crid:</code> 外部 CR 編號(去重/查票)、"
        "<code>email:</code> 負責人(可逗號多位,表單/指令台上鎖)、"
        "<code>prompt:</code> 任務指令(通常 agent-job 腳本寫));"
        "<b>② profile</b>(目標+驗收標準);<b>③ HIL 表單文字</b>(累加進"
        "「人類指示」段);<b>④ 跨票交接 BASE_ 資料夾</b>。"
        "⚠️ <b>票上留言不會進 TICKET.md</b>(M2 起;要補資訊請走 hold 表單)。"
        "組成正本:docs/design/workspace.md。</p></div>"
        "<h2>6 態說明</h2><div class='card'><table>" + doc_rows + "</table></div>"
        "<h2>模組架構(分層 · 資料流由上而下)</h2>"
        f"<div class='card'>{_arch_svg()}"
        "<p class='sys' style='text-align:left;margin-top:8px'>"
        "<b>store</b> 是狀態主幹:幾乎所有模組都讀寫它(SQLite 狀態 + journal "
        "事件流)。上圖只畫分層與資料流方向,逐模組的 trigger/輸入/輸出/上下游見"
        "下表。</p></div>"
        "<h2>模組職責表(trigger · 輸入 · 輸出 · 上下游)</h2>"
        "<div class='card' style='overflow-x:auto'>" + _arch_doc_table()
        + "</div>"
        + render_graph_section()
        + "<h2>agent↔agent 交接(W10.3,由 HIL 表單驅動;見 docs/design/architecture.md §4)</h2>"
        "<div class='card'>"
        "<p class='sys' style='text-align:left'>觸發:人在 HIL(End) "
        "<code>score_and_close</code> 或 HIL(Middle) <code>decision</code> 表單選"
        "「改派下一棒」,再選換手種類 + 下一棒 profile(下拉,候選=載入的全部 profile)"
        "+ 交接指示;人也可在<b>指令台</b>下 <code>next</code>、或 agent 自發"
        "(envelope <code>status=handoff</code>)——皆僅同票換手。</p>"
        "<ul style='line-height:1.8'>"
        "<li><b>同票換手(next)→ 回進行中</b>:<b>同一張 Jira</b>,重置 session"
        "(<code>session_id</code>=None、<code>attempts</code>=0)、鎖定新 profile、"
        "重新 provision workspace(新 profile 的 template)→ 回「進行中」由新 profile "
        "接手。脈絡留在 Jira 票(留言/description/人類指示 → 新 TICKET.md);"
        "<b>非 native resume</b>(新 profile 重新開始,不重跑舊 session)。</li>"
        "<li><b>跨票換手(base)→ 舊票撤銷</b>:<b>系統</b>(非人手建)用 "
        "<code>create_ticket</code> 在同 project 開新票、預建其 session(鎖定新 profile "
        "+ <code>base_ref</code> 指回本票),本票收成 <b>ABORTED(交接,非 failure)</b>。"
        "新票下輪首次佈建時 dispatcher 注入 base 脈絡(複製 base 的 TICKET.md/最後 "
        "envelope 進 <code>ws/BASE_&lt;key&gt;/</code> + 人類指示段指路)—— 適合"
        "「換引擎/走錯路/乾淨重來/跨專案,但要保留前輪脈絡」。</li>"
        "<li class='sys'>一句話:<b>同票換手=同一張票換 profile 重跑;跨票換手=系統另開"
        "新票、帶前輪敘事乾淨重來</b>。資料不完整 → fail-safe 降級續跑原 agent。</li>"
        "</ul></div>"
        "<h2>狀態存在哪(重要)</h2><div class='card'>"
        "<ul style='line-height:1.8'>"
        "<li><b>Jira 這邊</b>:真正的 <code>status</code>(To Do/進行中/Done)存 "
        "Jira,harness 只讀進來鏡射到 DB <code>ticket_watch.last_state</code>。</li>"
        "<li><b>我們系統這邊</b>:內部判定 <code>outcome</code>"
        "(SUCCESS/FAILURE/ABORTED/UNKNOWN)+ <code>pending_reason</code> 只存 DB "
        "<code>ticket_session</code>,<b>不寫回 Jira</b>。上面 6 態就是由這些欄位"
        "(加 queued/inactive/有無 session)推導的單一 canonical 狀態。</li>"
        "<li><b>harness 不主動 transition Jira 狀態</b>(只留言);關票=人做"
        "(成功/失敗後交人評分,人填 <code>score</code> 再關)。</li>"
        "<li><b>生命週期事件</b>都記在 journal <code>events.jsonl</code>"
        "(new_issue/attempt_*/resolved/pending/handoff/jira_write/human_score…),"
        "ticket 詳情頁的<b>事件時間軸</b>即由它繪製。</li>"
        "</ul></div>"
        "<p class='sys' style='text-align:left'>同內容見 repo 根 "
        "<code>README.md</code>「資料流生命週期 / 狀態機」段。</p></main>"
        + _SVGPZ_JS)


# ── W7.7:REST /api/v1(唯讀,給 LLM 監控)────────────────────────────────── #
def _resolve_ref(ref: str, sessions: dict, watch: dict) -> int | None:
    """三合一解析器:Jira key(SCRUM-42)/ 內部 id / ClearQuest CR id → issue_id。
    CR id 也查 watch description 的 yaml `crid:` 行——補「票剛開、session 未建」
    的時窗,讓 CQ scan 的 REST 預濾(GET /api/v1/tickets/<CRID>)始終準確。"""
    if ref.isdigit() and (int(ref) in sessions or int(ref) in watch):
        return int(ref)
    for iid, s in sessions.items():
        if s.get("key") == ref or (s.get("clearquest_id") or "") == ref:
            return iid
    crid_pat = re.compile(rf"^crid:\s*{re.escape(ref)}\s*$", re.M)
    for iid, w in watch.items():
        if w.get("key") == ref or crid_pat.search(w.get("description") or ""):
            return iid
    return None


def _profile_engine(profile_name: str | None) -> str:
    """profile → engine(claude/codex);查不到→claude。給原始 source 檔解析。"""
    if not profile_name:
        return "claude"
    try:
        from arcp.profiles import load_profiles
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
        from arcp.transcript import source_files
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
            from arcp.transcript import source_files
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
        "abort_reason": s.get("abort_reason"),         # M2:中止理由泛化
        "attempts": s.get("attempts") or 0,
        "cost_usd": s.get("cost_usd") or 0,
        "score": score,
        "completion_pct": (score * 10 if score is not None else None),
        "session_id": s.get("session_id"),
        "owner_email_list": s.get("owner_email_list") or "",  # K6:負責人名單
        "inactive": bool(s.get("inactive")), "queued": bool(s.get("queued")),
        "evict_count": s.get("evict_count") or 0,
        "assignee": w.get("last_assignee") or "",
        "summary": w.get("summary") or "",
        "workspace": s.get("workspace") or "",
        "finished_at": s.get("finished_at") or 0,
        "timeline": tl,
        "logs": _api_logs_index(iid, s),
    }


def text_matcher(q: str, mode: str = "match"):
    """回 (fn, error)。fn(s)->bool;二選一 mode:
      match = 一般字串「包含」比對,**不分大小寫**(預設);
      regex = 正則(re.search,亦 IGNORECASE)。無效 regex → fn 恆 False + error 訊息。
    q 空 → (None, None)(不過濾)。dashboard 前端亦提供對等的 regex/不分大小寫兩模式。"""
    if not q:
        return None, None
    if mode == "regex":
        try:
            rx = re.compile(q, re.IGNORECASE)
        except re.error as e:
            return (lambda s: False), f"bad regex: {e}"
        return (lambda s: bool(rx.search(s or ""))), None
    ql = q.lower()
    return (lambda s: ql in (s or "").lower()), None


def api_list_tickets(journal: list, sessions: dict, watch: dict, q: str = "",
                     field: str = "all", mode: str = "match") -> dict:
    """精簡票列表(給 LLM 先掃全景)。可選過濾:
      q     — 關鍵字/正則(空=不過濾)
      field — 比對欄位:key / summary / profile / desc / all(預設 all)
      mode  — match(不分大小寫子字串,預設)或 regex(正則,亦不分大小寫)"""
    match_fn, err = text_matcher(q, mode)
    ids = sorted(set(sessions) | set(watch))
    items = []
    for iid in ids:
        s = sessions.get(iid, {})
        w = watch.get(iid, {})
        key = s.get("key") or w.get("key") or f"#{iid}"
        profile = s.get("profile") or ""
        summary = w.get("summary") or ""
        if match_fn is not None:
            hay = {"key": key, "summary": summary, "profile": profile,
                   "desc": w.get("description") or ""}.get(
                field) or f"{key} {summary} {profile} {w.get('description') or ''}"
            if not match_fn(hay):
                continue
        items.append({
            "iid": iid, "key": key,
            "clearquest_id": s.get("clearquest_id"),
            "profile": s.get("profile"), "state": canonical_state(s or None),
            "outcome": s.get("outcome"), "cost_usd": s.get("cost_usd") or 0,
            "score": s.get("human_score")})
    out = {"count": len(items), "tickets": items}
    if q:
        out["filter"] = {"q": q, "field": field, "mode": mode}
    if err:
        out["filter_error"] = err
    return out


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
        if len(parts) == 3:                            # /api/v1/tickets(?q=&field=&mode=)
            qs = parse_qs(u.query)
            return self._send_json(api_list_tickets(
                journal, sessions, watch,
                q=(qs.get("q") or [""])[0],
                field=(qs.get("field") or ["all"])[0],
                mode=(qs.get("mode") or ["match"])[0]))
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
        if self.path.startswith("/api/v1/kpi"):   # C3:KPI(?days=N&profile=X)
            from urllib.parse import parse_qs, urlparse

            from arcp.kpi import compute_kpi
            qs = parse_qs(urlparse(self.path).query)
            days = qs.get("days", [None])[0]
            since = (time.time() - float(days) * 86400) if days else 0.0
            self._send_json(compute_kpi(
                journal, list(sessions.values()), since=since,
                profile=qs.get("profile", [None])[0]))   # C6:A/B 對照
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
        if self.path.startswith("/db/schema/"):        # 欄位定義(含空表)
            from urllib.parse import unquote
            self._send_json(db_schema(unquote(self.path.rsplit("/", 1)[1])))
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
        if self.path.startswith("/timeline"):      # C5 粗看:全域跨票時間軸
            from urllib.parse import parse_qs, urlparse
            u = urlparse(self.path)
            qs = parse_qs(u.query)
            body = render_timeline_page(
                journal, sessions, read_watch(),
                win=(qs.get("win") or ["7"])[0],
                q=(qs.get("q") or [""])[0])
            page = (f"<!doctype html><html><head><meta charset='utf-8'>"
                    f"<meta name='viewport' content='width=device-width,"
                    f"initial-scale=1'>"
                    f"<title>ARCP Timeline{_TITLE_TAIL}</title>"
                    f"<style>{CSS}</style></head><body>{body}</body></html>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Security-Policy", _CSP_MAIN)
            self.end_headers()
            self.wfile.write(page.encode())
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
            # W9.3:ticket 頁無 _nav(),過去因此缺 localizeTimes → trace 事件時間
            # 停在「—」佔位。補進 _THEME_JS(內含 localizeTimes + 主題套用),
            # 讓每個事件前的 data-ts 都轉成瀏覽器時區時間。
            body = render_ticket(iid, journal, sessions) + _THEME_JS
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
                     # W6.7/W9.3:時間軸在 main 之外——單獨抽資料島更新(不摧毀
                     # widget);合一後只剩一個資料島 #tl-data
                     "const nd=doc.querySelector('#tl-data'),"
                     "live=document.querySelector('#tl-data');"
                     "if(nd&&window.__tlUpdate&&(!live||"
                     "live.textContent!==nd.textContent)){"
                     "if(live)live.textContent=nd.textContent;"
                     "window.__tlUpdate(JSON.parse(nd.textContent));}"
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


def _parse_args(argv):
    import argparse
    p = argparse.ArgumentParser(
        prog="detail_server.py",
        description="ARCP 唯讀觀測 dashboard(狀態機 / KPI 圖表 / DB 瀏覽 / 概念頁 / "
                    "REST /api/v1)。零外部依賴、內網可跑。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="範例(一律用 uv run):\n"
               "  uv run python scripts/detail_server.py                    "
               "# 預設 :8788、內網開放\n"
               "  uv run python scripts/detail_server.py --port 9000 --host 127.0.0.1"
               "  # 換 port、鎖本機\n"
               "  uv run python scripts/detail_server.py --runtime /data/runtime"
               " --log-level DEBUG")
    p.add_argument("--port", type=int, default=None, metavar="PORT",
                   help="dashboard 埠(預設 8788)")
    p.add_argument("--host", default=None, metavar="HOST",
                   help="綁定 host(預設 0.0.0.0 內網開放;127.0.0.1 鎖本機)")
    p.add_argument("--config", default=None, metavar="FILE",
                   help="設定檔(純檔名=config/ 下,如 config.test.yaml)。"
                        "與 poller 的 --config 配對(測試/正式整組隔離)")
    p.add_argument("--runtime", default=None, metavar="DIR",
                   help="runtime 目錄(harness.db/events/workspaces;預設 repo/runtime)")
    p.add_argument("--control-url", default=None, metavar="URL",
                   help="control API URL(狀態頁連它;預設 http://127.0.0.1:8787)")
    p.add_argument("--log-level", default=None,
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                   help="日誌層級(等同設 ARCP_LOG_LEVEL;預設 INFO)")
    return p.parse_args(argv)


if __name__ == "__main__":
    _a = _parse_args(sys.argv[1:])
    if _a.log_level:
        os.environ["ARCP_LOG_LEVEL"] = _a.log_level
    if _a.config:
        try:
            from arcp.paths import resolve_config_file as _rcf
            _CONFIG_PATH = _rcf(_a.config)
        except ImportError:
            _CONFIG_PATH = _a.config
    if _a.runtime:
        ROOT = os.path.abspath(_a.runtime)
    PORT = _a.port or PORT
    CONTROL = _a.control_url or CONTROL
    HOST = _a.host or HOST
    _apply_control()                 # CONTROL 可能被覆寫 → 重算 CSP(_CONTROL_JS 於 render 代入)
    # 帶 http:// 完整 URL——終端會渲染成可點連結,直接開 browser。
    # 0.0.0.0 綁所有介面,可點的入口用 localhost 呈現。
    click_host = "localhost" if HOST == "0.0.0.0" else HOST
    where = "(綁 0.0.0.0,內網開放)" if HOST == "0.0.0.0" else ""
    print(f"[detail] serving {ROOT} on http://{click_host}:{PORT}{where}",
          flush=True)
    if HOST == "0.0.0.0":
        print("[detail] ⚠️ 內網開放:dashboard 唯讀但會顯示系統/程序資訊;"
              "control API(寫入端點)風險見 /docs。鎖本機:--host 127.0.0.1", flush=True)
    HTTPServer((HOST, PORT), Handler).serve_forever()
