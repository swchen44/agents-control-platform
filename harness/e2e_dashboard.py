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
from arcp_harness.control_api import ControlAPI  # noqa: E402
from arcp_harness.store import Store, TicketSession, TicketWatch  # noqa: E402

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
store.upsert_session(sess(1, session_id="s1", attempts=2, workspace=ws1,
                          outcome="SUCCESS", cost_usd=1.25))
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


class FakePoller:
    paused = False


ctl = ControlAPI(FakePoller(), store, host="127.0.0.1", port=0)
ctl.start()
ctl_url = f"http://127.0.0.1:{ctl.port}"

# -- subprocess 起 dashboard ------------------------------------------------ #
port = free_port()
proc = subprocess.Popen(
    [sys.executable, "detail_server.py", root, str(port), ctl_url],
    cwd=os.path.dirname(os.path.abspath(__file__)),
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
    check("總覽:in-flight 卡在", "in-flight" in index)
    # F2 徽章 + FIFO 位置(queued_at 100 的 P-5 在前)
    check("徽章:P-5 QUEUED #1", "QUEUED #1" in index)
    check("徽章:P-4 QUEUED #2", "QUEUED #2" in index)
    check("徽章:INACTIVE", "INACTIVE" in index)
    check("徽章:pending:approval", "pending:approval" in index)
    check("徽章:FAILURE(outcome 優先於 pending)",
          ">FAILURE</span>" in index)
    # 控制列指向 control API
    check("控制列:Pause/Resume/Reload 按鈕",
          all(x in index for x in ("Pause", "Resume", "Reload")))
    check("控制列:指向 control API", json.dumps(ctl_url) in index)

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

    # 審批門 ticket 卡
    t7 = urllib.request.urlopen(
        f"http://127.0.0.1:{port}/ticket/7", timeout=5).read().decode()
    check("審批卡:出現在 pending:approval 票", "審批門" in t7)
    check("審批卡:退回次數", "退回次數" in t7 and "reprompt" in t7)
    t1 = urllib.request.urlopen(
        f"http://127.0.0.1:{port}/ticket/1", timeout=5).read().decode()
    check("非審批票:無審批卡", "審批門" not in t1)
    # W4.1:detail 頁修 auto-collapse(無 meta refresh、fetch 局部更新保展開)
    check("detail 無整頁 meta refresh", "http-equiv" not in t1)
    check("detail 有展開保留更新 JS",
          "DOMParser" in t1 and "details" in t1)
    # W4.2:transcript 卡 + /tfile 服務
    check("transcript 卡:HTML/tgz 連結",
          "/tfile/1/final.html" in t1 and "/tfile/1/transcript.tgz" in t1)
    fh = urllib.request.urlopen(
        f"http://127.0.0.1:{port}/tfile/1/final.html", timeout=5)
    check("tfile:HTML 內容可讀",
          "FINAL-TRANSCRIPT" in fh.read().decode())
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
