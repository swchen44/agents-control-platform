#!/usr/bin/env python3
"""W2.7 E2E — web dashboard(免 token,假資料)。

假 runtime(Store + journal)→ subprocess 起 detail_server → 抓 HTML 驗:
總覽卡(cost/失敗率/in-flight)、狀態徽章(QUEUED FIFO 位置/INACTIVE/pending)、
控制列指向 control API、審批狀態卡;並起真 ControlAPI 驗按鈕背後的端點契約
(POST /pause → paused;JS 打的就是這些)。

Usage: python3 e2e_dashboard.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp.control_api import ControlAPI  # noqa: E402
from arcp.paths import find_script  # noqa: E402
from arcp.store import Store, TicketSession, TicketWatch  # noqa: E402

ok = True


def check(name, cond):
    global ok
    ok = ok and bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def sess(iid, **kw):
    base = dict(issue_id=iid, key=f"P-{iid}", profile="p", workspace="ws",
                session_id=None, attempts=0, outcome=None,
                pending_reason=None, cost_usd=0.0)
    base.update(kw)
    return TicketSession(**base)


# -- 假 runtime ------------------------------------------------------------- #
root = tempfile.mkdtemp()
store = Store(root)
ws1 = os.path.join(root, "tickets", "1", "ws")        # W4.2 transcript 產物
os.makedirs(os.path.join(root, "tickets", "1", "transcript"), exist_ok=True)
os.makedirs(ws1, exist_ok=True)
open(os.path.join(root, "tickets", "1", "transcript", "final.html"),
     "w").write("<html>FINAL-TRANSCRIPT</html>")
open(os.path.join(root, "tickets", "1", "transcript", "transcript.tgz"),
     "wb").write(b"\x1f\x8b_fake")
# W6.4:meta.json sidecar(產生時間/原因/sub-session)——卡片要顯示
open(os.path.join(root, "tickets", "1", "transcript", "meta.json"),
     "w").write(json.dumps({"generated_at": "2026-08-07T09:00:00",
                            "reason": "close:SUCCESS", "session_id": "s1",
                            "subs": ["agent-x"], "files": ["final.html"]}))
# W7.7:attempt dir 的 L3 事件(給 /api/v1 events/logs 驗證)
_ad1 = os.path.join(root, "tickets", "1", "attempts")
os.makedirs(_ad1, exist_ok=True)
open(os.path.join(_ad1, "a1.envelope.json"), "w").write('{"completed":true}')
with open(os.path.join(_ad1, "a1.events.jsonl"), "w") as _f:
    # W9.3:補 timestamp(L3 時間軸 item 需要),真資料一律有;缺則被略過
    _f.write(json.dumps({"kind": "MessageEvent", "source": "user",
                         "timestamp": "2026-08-06T16:47:00"}) + "\n")
    _f.write(json.dumps({"kind": "MessageEvent", "source": "agent",
                         "timestamp": "2026-08-06T16:47:05"}) + "\n")
store.upsert_session(sess(1, session_id="s1", attempts=2, workspace=ws1,
                          outcome="SUCCESS", cost_usd=1.25, human_score=8,
                          clearquest_id="CR-1001"))
store.upsert_session(sess(2, outcome="FAILURE",
                          pending_reason="max-attempts", cost_usd=0.5))
store.upsert_session(sess(3, session_id="s3", attempts=1))       # in-flight
store.upsert_session(sess(4, queued=True, queued_at=200.0))      # FIFO #2
store.upsert_session(sess(5, queued=True, queued_at=100.0))      # FIFO #1
store.upsert_session(sess(6, inactive=True))
store.upsert_session(sess(7, pending_reason="approval",
                          approval_revisions=1))
for iid in range(1, 8):
    store.journal("new_issue", iid, f"P-{iid}")
    store.upsert(TicketWatch(                      # W4.1:assignee/created 來源
        issue_id=iid, key=f"P-{iid}", last_comment_id=0, last_state="To Do",
        last_assignee_id="", route_name=None,
        last_assignee="fox44" if iid == 3 else "",
        summary=f"任務摘要 {iid}", description=f"細節描述 {iid}"))  # W4.7 過濾
store.journal("approval", 7, "P-7", decision="reprompt", revisions=1)
store.journal("handoff", 3, "P-3", kind="agent", to="other")   # 換手起點
# W6.7:harness→Jira 寫入(jira_write)+ 結案,供事件時間軸驗證
store.journal("jira_write", 1, "P-1", action="comment", detail="[agent] done")
store.journal("jira_write", 1, "P-1", action="transition", detail="done")
store.journal("resolved", 1, "P-1", outcome="SUCCESS")


class FakePoller:
    paused = False


ctl = ControlAPI(FakePoller(), store, host="127.0.0.1", port=0)
ctl.start()
ctl_url = f"http://127.0.0.1:{ctl.port}"

# -- subprocess 起 dashboard(用 uv run;全走 CLI flag,--host 綁本機免防火牆彈窗)-- #
port = free_port()
proc = subprocess.Popen(
    ["uv", "run", "python", find_script("detail_server.py"),
     "--runtime", root, "--port", str(port), "--control-url", ctl_url,
     "--host", "127.0.0.1"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(50):                       # 等 server 起來(最多 5s)
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
            break
        except OSError:
            time.sleep(0.1)

    index = urllib.request.urlopen(
        f"http://127.0.0.1:{port}/", timeout=5).read().decode()

    # C4 總覽卡
    check("總覽:總 cost 1.75", "$1.7500" in index)
    check("總覽:失敗率 50%", "50%" in index and "失敗率" in index)
    check("總覽:進行中卡在", "進行中" in index and "HIL(Middle)" in index)
    # F2 徽章 + FIFO 位置(queued_at 100 的 P-5 在前)
    check("徽章:P-5 排隊 #1", "排隊 #1" in index)
    check("徽章:P-4 排隊 #2", "排隊 #2" in index)
    check("徽章:HIL(Middle)·交人(舊 inactive)", "HIL(Middle)·交人" in index)
    check("徽章:HIL(Middle)·審批(舊 pending:approval)",
          "HIL(Middle)·審批" in index)
    check("徽章:HIL(End)·失敗(outcome 優先於 pending)",
          ">HIL(End)·失敗</span>" in index)
    # 控制列指向 control API
    # W5.8:control 移到獨立頁,index 不再有控制列
    check("index 不含控制列(已移 /control)", "Pause" not in index)
    cpage = urllib.request.urlopen(
        f"http://127.0.0.1:{port}/control", timeout=5).read().decode()
    check("Control 頁:Pause/Resume/Reload/Shutdown + 狀態",
          all(x in cpage for x in ("Pause", "Resume", "Reload",
                                   "Shutdown", "cstatus")))
    check("Control 頁:指向 control API", json.dumps(ctl_url) in cpage)
    check("導覽:命令列 tab(DB/Control)",
          "href='/control'" in cpage and "DB Browser" in cpage
          and "class='cmdbar'" in cpage)

    # W4.1/W4.7:新欄位 + 過濾器置頂 + 圖表 + 排序
    check("欄位:summary/assignee/created/finished/換手起點",
          all(x in index for x in ("summary", "assignee", "created",
                                   "finished", "換手起點")))
    check("欄位:停留時間/lifetime/人力$(W5.2)",
          all(x in index for x in ("停留時間", "lifetime", "人力$")))
    check("欄位值:P-3 assignee=fox44", ">fox44</td>" in index)
    check("過濾器置頂:時間快選/自訂 range/狀態/summary/desc", all(
        x in index for x in ("id='qr'", "過去 30 天", "id='from'", "id='to'",
                             "id='st'", "id='ksum'", "id='kdesc'")))
    check("時間圖:svg + 每週勾選 + 圖例", all(
        x in index for x in ("chart-time", "id='wk1'", "lg-time")))
    check("金錢圖:svg + 每週勾選 + 時薪輸入", all(
        x in index for x in ("chart-money", "id='wk2'", "id='rate'",
                             "人類時薪 USD")))
    check("表格排序 JS(sortable/thead-row)",
          "sortable" in index and "thead-row" in index)
    check("index 無整頁 meta refresh", "http-equiv" not in index)
    # W5.6:匯出按鈕 + DB tab 導覽
    check("匯出按鈕 CSV/JSON", "expo(\"csv\")" in index
          and "expo(\"json\")" in index)
    check("導覽:Dashboard/DB tab",
          "DB Browser" in index and "/db" in index)

    # W5.6:DB 瀏覽器頁 + 端點
    dbpage = urllib.request.urlopen(
        f"http://127.0.0.1:{port}/db", timeout=5).read().decode()
    check("DB 頁:表格清單/查詢框", "唯讀查詢" in dbpage and "qbox" in dbpage)
    check("DB 頁:匯出 CSV/JSON 鈕",
          "dbExport(\"csv\")" in dbpage and "dbExport(\"json\")" in dbpage
          and "function dbExport" in dbpage)
    # W5.7:欄寬可拖曳(兩頁都載入 resizable + 呼叫)
    check("欄寬拖曳:index 表 resizable",
          "function resizable" in index and "resizable($('tix')" in index)
    check("欄寬拖曳:DB 表 resizable",
          "function resizable" in dbpage and "#dbout table" in dbpage)
    tabs = json.loads(urllib.request.urlopen(
        f"http://127.0.0.1:{port}/db/tables", timeout=5).read())
    names = {t["name"] for t in tabs}
    check("/db/tables:三表齊", {"ticket_session", "ticket_watch",
                               "trigger_state"} <= names)
    td = json.loads(urllib.request.urlopen(
        f"http://127.0.0.1:{port}/db/table/ticket_session?limit=5",
        timeout=5).read())
    check("/db/table:欄位+資料", "columns" in td and "issue_id" in td["columns"])
    # 唯讀查詢 POST
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/db/query", method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"sql": "SELECT COUNT(*) n FROM ticket_session"})
        .encode())
    qr = json.loads(urllib.request.urlopen(req, timeout=5).read())
    check("/db/query:SELECT 可查", qr.get("columns") == ["n"]
          and qr["rows"][0][0] >= 1)
    # 寫入被擋(唯讀)
    req2 = urllib.request.Request(
        f"http://127.0.0.1:{port}/db/query", method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"sql": "UPDATE ticket_session SET cost_usd=0"})
        .encode())
    qr2 = json.loads(urllib.request.urlopen(req2, timeout=5).read())
    check("/db/query:非 SELECT 擋掉", "error" in qr2)

    # W4.7:/data 單一資料源
    dat = json.loads(urllib.request.urlopen(
        f"http://127.0.0.1:{port}/data", timeout=5).read())
    by = {r["iid"]: r for r in dat["rows"]}
    check("/data:rows 齊 + 欄位", 3 in by and all(
        k in by[3] for k in ("key", "summary", "desc", "status", "created",
                             "finished", "cost", "human_min", "handoff",
                             "last_change")))
    check("/data:summary/desc 可過濾來源", by[3]["summary"] == "任務摘要 3"
          and by[3]["desc"] == "細節描述 3")
    check("/data:rate_default 欄位存在", "rate_default" in dat)
    # W10.1:HIL 模型 6 態 canonical state + 完成度 score 進 /data
    check("/data:canonical state(HIL 6 態)",
          by[1]["state"] == "hil_end" and by[1]["outcome"] == "SUCCESS"
          and by[3]["state"] == "running"
          and by[7]["state"] == "hil_middle")
    check("/data:完成度 score", by[1]["score"] == 8 and by[3]["score"] is None)
    # 首頁:profile filter + 三張 per-profile 圖容器 + 完成度欄
    idx = urllib.request.urlopen(
        f"http://127.0.0.1:{port}/", timeout=5).read().decode()
    check("首頁:profile filter 輸入",
          "id='kprofile'" in idx and "profile keyword" in idx)
    check("首頁:三張 per-profile 圖容器",
          "id='chart-pstate'" in idx and "id='chart-pcost'" in idx
          and "id='chart-pscore'" in idx)
    check("首頁:完成度欄 + 已評分顯示",
          "完成度" in idx and "8/10" in idx)

    # 審批門 ticket 卡
    t7 = urllib.request.urlopen(
        f"http://127.0.0.1:{port}/ticket/7", timeout=5).read().decode()
    check("審批卡:出現在 pending:approval 票", "審批門" in t7)
    check("審批卡:退回次數", "退回次數" in t7 and "reprompt" in t7)
    t1 = urllib.request.urlopen(
        f"http://127.0.0.1:{port}/ticket/1", timeout=5).read().decode()
    check("非審批票:無審批卡", "審批門" not in t1)
    check("詳情頁:來源・連結・用量卡",
          "來源・連結・用量" in t1 and "本票用量 vs soft / hard" in t1
          and "一次性連結" in t1)
    # W5.3/W6.3:強制驅逐按鈕只在 active session 顯示 + 正名 + hover 說明
    t3 = urllib.request.urlopen(
        f"http://127.0.0.1:{port}/ticket/3", timeout=5).read().decode()
    check("強制驅逐按鈕:active 票有 + 正名 + 說明",
          "強制驅逐(killpg)" in t3 and "/evict/3" in t3
          and "title='強制驅逐" in t3)
    check("強制驅逐按鈕:終態票無", "強制驅逐(killpg)" not in t1)
    # W4.1:detail 頁修 auto-collapse(無 meta refresh、fetch 局部更新保展開)
    check("detail 無整頁 meta refresh", "http-equiv" not in t1)
    check("detail 有展開保留更新 JS",
          "DOMParser" in t1 and "details" in t1)
    # W4.2:transcript 卡 + /tfile 服務
    check("transcript 卡:HTML/tgz 連結",
          "/tfile/1/final.html" in t1 and "/tfile/1/transcript.tgz" in t1)
    # W6.4:卡片顯示 meta(產生時間/原因中文化)+ meta.json 不當產物連結
    check("transcript 卡:meta 產生時間/原因",
          "2026-08-07T09:00:00" in t1 and "結案(成功)" in t1
          and "/tfile/1/meta.json" not in t1)
    # W6.4:被動產生按鈕(有 session_id 才有;打 control /gen_transcript)
    check("transcript 卡:被動產生按鈕",
          "/gen_transcript/1" in t1 and "重新產生" in t1)
    # W6.7/W9.2/W9.3:單一事件時間軸(L3 對話 + Jira 生命週期合一,共用時間軸)
    # 收在右下浮動鈕抽屜(vis 離線),左側兩層 nested groups 分類
    check("時間軸:浮動鈕 + 抽屜 + 合一 widget + vendored vis 資產(離線)",
          "id='tlfab'" in t1 and "id='tlwrap'" in t1
          and "L3 對話 + Jira 生命週期" in t1
          and "/tvendor/vis-timeline.min.js" in t1
          and "/tvendor/vis-timeline.min.css" in t1
          and "id='evtl'" in t1 and "id='tl-data'" in t1
          and "id='l3tl'" not in t1)               # 合一後不再有第二個 widget
    check("時間軸:浮動鈕/抽屜在 </main> 之外(刷新不摧毀 widget)",
          "</main>" in t1
          and t1.index("id='tlwrap'") > t1.index("</main>"))
    import re as _re2
    mtl = _re2.search(
        r"<script id='tl-data' type='application/json'>(.*?)</script>",
        t1, _re2.S)
    check("時間軸:合一資料島可解析", bool(mtl))
    tld = json.loads(mtl.group(1)) if mtl else {"groups": [], "items": []}
    gids = {g["id"] for g in tld["groups"]}
    # 兩層:類別列 cat_l3/cat_life + 子列(對話 aN + 生命週期四分組)
    check("時間軸:兩層 nested 分類(對話類別 + 生命週期四分組)",
          {"cat_l3", "cat_life"}.issubset(gids)
          and {"in", "jira", "life", "run"}.issubset(gids)
          and any(g["id"] == "cat_life"
                  and set(g.get("nestedGroups") or [])
                  == {"in", "jira", "life", "run"}
                  for g in tld["groups"]))
    # 生命週期 item id 加 lf- 前綴,與 L3 的 aN-i 不撞;L3 item 用 className l3-
    jira_items = [i for i in tld["items"] if i.get("group") == "jira"]
    check("時間軸:jira_write→jira 組 + 中文標籤 + lf- 前綴",
          any("留言 Jira" in i["content"] for i in jira_items)
          and any("transition" in i["content"] for i in jira_items)
          and all(str(i["id"]).startswith("lf-") for i in jira_items))
    check("時間軸:合一含 L3 對話 item(className l3-*)+ 生命週期 item",
          any(str(i.get("className", "")).startswith("l3-")
              for i in tld["items"])
          and any(i.get("group") == "in" and "新票" in i["content"]
                  for i in tld["items"])
          and any(i.get("group") == "life" and "結案" in i["content"]
                  for i in tld["items"])
          and all(isinstance(i["start"], int) for i in tld["items"]))
    # W9.3 item#1:ticket 頁載入 localizeTimes(過去缺 _nav→時間停在「—」)
    check("trace 事件時間:ticket 頁有 localizeTimes + data-ts 佔位",
          "localizeTimes" in t1 and "data-ts=" in t1)
    fh = urllib.request.urlopen(
        f"http://127.0.0.1:{port}/tfile/1/final.html", timeout=5)
    check("tfile:HTML 內容可讀",
          "FINAL-TRANSCRIPT" in fh.read().decode())
    # W5.9:transcript HTML 帶 CSP 硬擋外部;vendored 資產本地服務
    check("tfile:HTML 有 CSP(擋外部)",
          "default-src 'none'" in (fh.headers.get(
              "Content-Security-Policy") or ""))
    tv = urllib.request.urlopen(
        f"http://127.0.0.1:{port}/tvendor/vis-timeline.min.css", timeout=5)
    check("tvendor:vis-timeline 本地服務(離線可用)",
          tv.status == 200 and int(tv.headers.get("Content-Length") or 0) > 0)
    th = urllib.request.urlopen(
        f"http://127.0.0.1:{port}/tfile/1/transcript.tgz", timeout=5)
    check("tfile:tgz 下載 header",
          "attachment" in (th.headers.get("Content-Disposition") or ""))
    try:
        urllib.request.urlopen(
            f"http://127.0.0.1:{port}/tfile/1/..%2f..%2fsecret", timeout=5)
        check("tfile:traversal 擋掉", False)
    except urllib.error.HTTPError as e:
        check("tfile:traversal 擋掉", e.code == 404)

    # W6.1:Server 頁 + 系統資料源 + 金鑰不外洩
    spage = urllib.request.urlopen(
        f"http://127.0.0.1:{port}/server", timeout=5).read().decode()
    check("Server 頁 + 命令列導覽",
          "id='sroot'" in spage and "class='cmdbar'" in spage
          and "href='/server'" in spage)
    sd = json.loads(urllib.request.urlopen(
        f"http://127.0.0.1:{port}/server/data", timeout=5).read())
    check("/server/data:sys 版本/資源/登入狀態齊",
          all(k in (sd.get("sys") or {}) for k in
              ("versions", "auth", "resources", "anomalies")))
    check("/server/data:perf 含 budget 燈",
          any(i.get("key") == "budget"
              for i in (sd.get("perf") or {}).get("indicators", [])))
    _au = sd["sys"]["auth"]
    check("/server/data:auth 狀態布林 + 方式字串(不外洩金鑰/email)",
          all(isinstance(_au.get(k), bool) for k in
              ("codex_logged_in", "claude_configured", "anthropic_api_key_env"))
          and isinstance(_au.get("claude_method"), str)
          and isinstance(_au.get("codex_method"), str)
          # W9.1:方式只顯方案類別,不含金鑰/token/email/@
          and not any(bad in (_au.get("claude_method", "")
                              + _au.get("codex_method", ""))
                      for bad in ("@", "sk-", "eyJ", "token")))
    check("/server/data:W6.2 processes/workspaces 欄位",
          "processes" in sd and "workspaces" in sd
          and isinstance(sd["processes"], list))
    check("Server 頁:進程/workspace 區塊",
          "Agent 進程" in spage and "Workspace" in spage)
    import re as _re
    check("Server 頁無金鑰值樣式",
          not _re.search(r"sk-ant|eyJ[A-Za-z0-9_-]{20}",
                         json.dumps(sd)))
    check("Server 頁:REST API 文件連結", "href='/docs'" in spage)

    # W6.5:REST API 文件(vendored Swagger UI,離線可用)
    oa = json.loads(urllib.request.urlopen(
        f"http://127.0.0.1:{port}/openapi.json", timeout=5).read())
    check("/openapi.json:3.1 規格 + 關鍵端點",
          oa["openapi"].startswith("3.1")
          and "/evict/{issue_id}" in oa["paths"]
          and "/gen_transcript/{issue_id}" in oa["paths"]
          and "/data" in oa["paths"])
    check("/openapi.json:寫入端點 ⚠️ 標示 + 指向 control server",
          oa["paths"]["/evict/{issue_id}"]["post"]["summary"].startswith("⚠️")
          and oa["paths"]["/evict/{issue_id}"]["post"]["servers"][0]["url"]
          == ctl_url)
    docs = urllib.request.urlopen(
        f"http://127.0.0.1:{port}/docs", timeout=5)
    dhtml = docs.read().decode()
    check("/docs:Swagger UI 載入本地資產",
          "SwaggerUIBundle" in dhtml
          and "/swagger-assets/swagger-ui-bundle.js" in dhtml
          and "/openapi.json" in dhtml)
    check("/docs:CSP 放行本地 + control(無外部 CDN)",
          "default-src 'self'" in (docs.headers.get(
              "Content-Security-Policy") or "")
          and "://" not in (docs.headers.get("Content-Security-Policy") or ""
                            ).replace(ctl_url, ""))
    css = urllib.request.urlopen(
        f"http://127.0.0.1:{port}/swagger-assets/swagger-ui.css", timeout=5)
    check("/swagger-assets:vendored 資產本地服務",
          css.status == 200
          and int(css.headers.get("Content-Length") or 0) > 10000)
    try:
        urllib.request.urlopen(
            f"http://127.0.0.1:{port}/swagger-assets/..%2f..%2fdetail_server.py",
            timeout=5)
        check("/swagger-assets:traversal 擋掉", False)
    except urllib.error.HTTPError as e:
        check("/swagger-assets:traversal 擋掉", e.code == 404)

    # W7.5:Agent Detail tab(harness 設定 + 全 Profile 參數;憑證不外洩)
    apage = urllib.request.urlopen(
        f"http://127.0.0.1:{port}/agent", timeout=5).read().decode()
    check("Agent Detail:頁 + tab + 設定/路由/Profile 區塊",
          "href='/agent'" in apage and "class='cmdbar'" in apage
          and "harness 設定(config.yaml)" in apage
          and "路由(route" in apage and "Profile · " in apage)
    check("Agent Detail:W7 新欄位可見",
          "budget.monthly_max_usd(月/agent)" in apage
          and "est_minutes(有效" in apage and "goal" in apage)
    check("Agent Detail:budget 當月用量 vs 上限卡",
          "budget 當月用量 vs 上限" in apage
          and "全站(global)" in apage)
    check("Agent Detail:不外洩憑證",
          "JIRA_API_TOKEN" not in apage and "api_token" not in apage)
    check("Server 頁導覽含 Agent Detail tab", "href='/agent'" in spage)

    # W7.6:概念/狀態機頁(純 SVG,零依賴)
    cpage = urllib.request.urlopen(
        f"http://127.0.0.1:{port}/concepts", timeout=5).read().decode()
    check("概念頁:狀態機 SVG + HIL 6 態 + a2a + 儲存說明",
          "href='/concepts'" in cpage and "<svg " in cpage
          and "marker id='ah'" in cpage
          and all(s in cpage for s in ("待處理", "進行中", "排隊",
                                       "HIL(Middle)", "HIL(End)", "撤銷"))
          and "跨票換手" in cpage
          and "狀態存在哪" in cpage)
    # W10.4/W10.6:模組架構圖(分層)+ 職責表(補檔名/分層/API 欄)
    check("概念頁:模組架構圖 + 職責表(檔名/分層/API/trigger/上下游)",
          "id='archsvg'" in cpage and "模組架構" in cpage
          and "模組職責表" in cpage and "trigger 時間" in cpage
          and "重要 API" in cpage and "檔名" in cpage
          and "arcp/poller.py" in cpage
          and "OuterLoop.poll_once" in cpage
          and all(s in cpage for s in ("jira_source", "poller", "dispatcher",
                                       "store", "control_api"))
          and all(s in cpage for s in ("輸入層", "決策層", "執行層",
                                       "人機協作層")))
    # W10.6:Introduction 改名 + HIL(End) 三訊號 + 交接兩機制對等
    check("Introduction 改名 + 三訊號 + 交接兩線",
          ">Introduction</a>" in cpage
          and "Introduction ·" in cpage
          and "agent 自評" in cpage and "三訊號" in cpage
          and "同票換手" in cpage and "跨票換手" in cpage)
    # W10.7:node+edge graph 圖 + 多選過濾器 + focus
    check("概念頁:模組 graph(node+edge)+ 過濾器 + focus",
          "id='graphsvg'" in cpage and "id='gfilter'" in cpage
          and "class='gnode" in cpage and "class='gedge'" in cpage
          and "data-from=" in cpage and "data-to=" in cpage
          and "gFocus(" in cpage and "window.gVisible" in cpage
          and "全不選" in cpage)
    # W10.5:svg-pan-zoom 互動(vendored 離線)
    check("概念頁:svg-pan-zoom 互動(離線 vendored)",
          "/tvendor/svg-pan-zoom.min.js" in cpage
          and "svgPanZoom(" in cpage)
    spz = urllib.request.urlopen(
        f"http://127.0.0.1:{port}/tvendor/svg-pan-zoom.min.js", timeout=5)
    check("svg-pan-zoom 資產可離線取(200 + 非空)",
          spz.status == 200
          and int(spz.headers.get("Content-Length") or 0) > 10000)

    # W7.7:REST /api/v1(給 LLM 監控;三合一 ref 解析 + 狀態/事件/log)
    def _api(path):
        return json.loads(urllib.request.urlopen(
            f"http://127.0.0.1:{port}{path}", timeout=5).read())
    lst = _api("/api/v1/tickets")
    check("/api/v1/tickets:列表", lst["count"] >= 1
          and any(t["key"] == "P-1" and t["state"] == "hil_end"
                  for t in lst["tickets"]))
    st1 = _api("/api/v1/tickets/P-1")           # 用 Jira key
    check("/api/v1/tickets/{key}:單票狀態",
          st1["iid"] == 1 and st1["state"] == "hil_end"
          and st1["score"] == 8 and st1["completion_pct"] == 80
          and st1["clearquest_id"] == "CR-1001" and st1["timeline"])
    check("/api/v1:三合一 ref(CR id / 內部 id 都解析同票)",
          _api("/api/v1/tickets/CR-1001")["iid"] == 1
          and _api("/api/v1/tickets/1")["iid"] == 1)
    ev1 = _api("/api/v1/tickets/1/events")
    check("/api/v1/{ref}/events:L3 事件",
          ev1["attempts"] and ev1["attempts"][0]["count"] == 2)
    lg1 = _api("/api/v1/tickets/1/logs")
    names = [x["name"] for x in lg1["logs"]]
    check("/api/v1/{ref}/logs:清單含 attempt/transcript",
          "attempt/a1.events.jsonl" in names
          and "transcript/final.html" in names)
    raw = urllib.request.urlopen(
        f"http://127.0.0.1:{port}/api/v1/tickets/1/logs/"
        f"attempt/a1.events.jsonl?tail=1", timeout=5).read().decode()
    check("/api/v1/{ref}/logs/{name}:raw + tail", raw.strip().startswith("{")
          and raw.count("\n") <= 1 and "agent" in raw)
    try:
        urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/v1/tickets/NOPE-9", timeout=5)
        check("/api/v1:未知票 404", False)
    except urllib.error.HTTPError as e:
        check("/api/v1:未知票 404", e.code == 404)
    check("/openapi.json:含 /api/v1 端點",
          "/api/v1/tickets/{ref}" in oa["paths"]
          and "/api/v1/tickets/{ref}/logs/{name}" in oa["paths"])

    # 按鈕背後的端點契約(JS fetch 打的就是這些)
    req = urllib.request.Request(f"{ctl_url}/pause", method="POST")
    body = json.loads(urllib.request.urlopen(req, timeout=5).read())
    st = json.loads(urllib.request.urlopen(
        f"{ctl_url}/status", timeout=5).read())
    check("控制端點:POST /pause → paused",
          body.get("paused") is True and st.get("paused") is True)
    check("控制端點:CORS header(跨 port fetch 可讀)",
          urllib.request.urlopen(f"{ctl_url}/health", timeout=5)
          .headers.get("Access-Control-Allow-Origin") == "*")
finally:
    proc.terminate()
    proc.wait(timeout=5)
    ctl.stop()

print("e2e-dashboard:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
