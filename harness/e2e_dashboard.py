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
from arcp_harness.store import Store, TicketSession  # noqa: E402

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
store.upsert_session(sess(1, session_id="s1", attempts=2,
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
store.journal("approval", 7, "P-7", decision="reprompt", revisions=1)


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

    # 審批門 ticket 卡
    t7 = urllib.request.urlopen(
        f"http://127.0.0.1:{port}/ticket/7", timeout=5).read().decode()
    check("審批卡:出現在 pending:approval 票", "審批門" in t7)
    check("審批卡:退回次數", "退回次數" in t7 and "reprompt" in t7)
    t1 = urllib.request.urlopen(
        f"http://127.0.0.1:{port}/ticket/1", timeout=5).read().decode()
    check("非審批票:無審批卡", "審批門" not in t1)

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
