#!/usr/bin/env python3
"""conc.3 E2E — 長駐共享 server + server 掛掉重起續(N1/N3,使用者問的)。

  S1 長駐共享:2 張 filechain-server 票走 harness → 共用 1 個 server PID
  S2 server 中途掛掉:kill server → 正在跑的票 error_kind=infra →
     pending:external(不消耗 attempt)
  S3 server 重起續:下次 poll → ServerManager 重起(同 persistence,rehydrate)→
     pending:external 自動解除 → 票 resume 續 → SUCCESS(不漏)

Usage: caffeinate -i python3 e2e_server_restart.py  (live,haiku,~$0.15)
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time

from arcp_harness.config import jira_credentials
from arcp_harness.dispatcher import Dispatcher
from arcp_harness.jira_source import JiraCloudSource
from arcp_harness.poller import OuterLoop
from arcp_harness.profiles import load_profiles
from arcp_harness.routing import load_config
from arcp_harness.server_manager import ServerManager
from arcp_harness.store import Store

HERE = os.path.dirname(os.path.abspath(__file__))
VENV = os.path.join(HERE, "..", "examples", "openhands-acp-poc", ".venv",
                    "bin", "python")
ROOT = os.path.join(HERE, "runtime_srvrestart")
DESC = ("在目前工作目錄依序建立三個檔案:step1.txt 內容 1、step2.txt 內容 12、"
        "step3.txt 內容 123。嚴格依序,內容不含引號空白。完成回覆 TASK_DONE。")


def main() -> int:
    src = JiraCloudSource(*jira_credentials())
    _, routes = load_config("routes.yaml")
    profiles = load_profiles("routes.yaml")
    shutil.rmtree(ROOT, ignore_errors=True)
    store = Store(os.path.join(ROOT, "store"))
    mgr = ServerManager(VENV, os.path.join(ROOT, "server"), port=18040,
                        api_key="restart-demo-key")
    disp = Dispatcher(src, store, profiles, root=os.path.join(ROOT, "work"),
                      server_manager=mgr)
    jql = "project = SCRUM AND labels = srv-restart AND statusCategory != Done"
    loop = OuterLoop(src, store, routes, jql, dispatcher=disp, max_running=3)

    try:
        # ---- S1: 長駐共享(2 張正常票,共用 1 server)------------------- #
        ts = int(time.time())
        base = [src.create_ticket("SCRUM", f"[srv] 共享 {ts}-{i}",
                                  description=DESC,
                                  labels=["srv-restart", "filechain-server"])
                for i in range(2)]
        loop.poll_once()
        pid1 = mgr.pid
        s1 = pid1 is not None and all(
            store.get_session(t.id) and store.get_session(t.id).outcome
            == "SUCCESS" for t in base)
        print(f"S1 長駐共享 2 張(server PID {pid1}): {'PASS' if s1 else 'FAIL'}")

        # ---- S2: server 中途掛掉 → infra pending:external -------------- #
        # 開一張新票,在它跑 conversation 時 kill server
        victim = src.create_ticket("SCRUM", f"[srv] 受害 {ts}",
                                   description=DESC,
                                   labels=["srv-restart", "filechain-server"])

        def kill_soon():
            time.sleep(6)  # 讓 conversation 起來後殺 server
            mgr.close()
            print(f"   [注入] kill server PID {pid1}", flush=True)

        threading.Thread(target=kill_soon, daemon=True).start()
        loop.poll_once()  # victim dispatch 中途 server 被殺
        sv = store.get_session(victim.id)
        s2 = sv and sv.pending_reason == "external" and sv.attempts == 0
        print(f"S2 server 掛→pending:external 不消耗 attempt: "
              f"{'PASS' if s2 else 'FAIL'} "
              f"(reason={getattr(sv,'pending_reason',None)}, "
              f"attempts={getattr(sv,'attempts',None)})")

        # ---- S3: 下次 poll → 重起 rehydrate → 自動解除 → resume 續 ----- #
        loop.poll_once()  # ServerManager.ensure 重起 + external_cleared + resume
        pid2 = mgr.pid
        sv2 = store.get_session(victim.id)
        s3 = (pid2 is not None and pid2 != pid1
              and sv2 and sv2.outcome == "SUCCESS")
        print(f"S3 重起(新 PID {pid2})→自動續→SUCCESS(不漏): "
              f"{'PASS' if s3 else 'FAIL'} (outcome={getattr(sv2,'outcome',None)})")

        ok = s1 and s2 and s3
        print("e2e-server-restart:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        mgr.close()
        store.close()


if __name__ == "__main__":
    sys.exit(main())
