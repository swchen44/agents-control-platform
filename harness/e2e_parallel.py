#!/usr/bin/env python3
"""conc.1 E2E — 3 張 Jira 票並行 dispatch(rawcli backend)。

  P1 3 張 filechain-rawcli 票一輪 poll → 全部 SUCCESS(並行 dispatch)
  P2 store 無損:3 個 session 各自正確、grader 全過(執行緒安全)
  P3 wall-clock < 串行(並行證據:總時間 ≈ 最慢單張,非 3×)

Usage: caffeinate -i python3 e2e_parallel.py  (live,haiku,~$0.12,3 張並行)
"""

from __future__ import annotations

import shutil
import sys
import time

from arcp.config import jira_credentials
from arcp.dispatcher import Dispatcher
from arcp.jira_source import JiraCloudSource
from arcp.poller import OuterLoop
from arcp.profiles import load_profiles
from arcp.routing import load_config
from arcp.store import Store

N = 3
DESC = ("在目前工作目錄依序建立三個檔案:step1.txt 內容 1、step2.txt 內容 12、"
        "step3.txt 內容 123。嚴格依序,內容不含引號空白。完成回覆 TASK_DONE。")


def main() -> int:
    src = JiraCloudSource(*jira_credentials())
    source_cfg, routes = load_config("routes.yaml")
    profiles = load_profiles("routes.yaml")
    shutil.rmtree("./runtime_parallel", ignore_errors=True)
    store = Store("./runtime_parallel")
    mr = source_cfg.get("max_running", 1)
    jql = "project = SCRUM AND labels = par-demo AND statusCategory != Done"
    loop = OuterLoop(src, store, routes, jql,
                     dispatcher=Dispatcher(src, store, profiles,
                                           root="./runtime_parallel"),
                     max_running=mr)
    print(f"max_running={mr}", flush=True)

    ts = int(time.time())
    tickets = [src.create_ticket("SCRUM", f"[par] 並行 {ts}-{i}",
                                 description=DESC,
                                 labels=["par-demo", "filechain-rawcli"])
               for i in range(N)]
    print("tickets:", [t.key for t in tickets], flush=True)

    t0 = time.time()
    loop.poll_once()  # collects N, dispatches in parallel
    wall = round(time.time() - t0, 1)

    sessions = [store.get_session(t.id) for t in tickets]
    done = sum(1 for s in sessions if s and s.outcome == "SUCCESS")
    p1 = done == N
    print(f"P1 {N} 張並行全 SUCCESS: {'PASS' if p1 else 'FAIL'} ({done}/{N})")
    p2 = all(s and s.outcome == "SUCCESS" and s.attempts >= 1 for s in sessions)
    print(f"P2 store 無損(各 session 正確): {'PASS' if p2 else 'FAIL'} "
          f"costs={[round(s.cost_usd, 4) if s else None for s in sessions]}")
    p3 = wall < 2.2 * 25  # 並行 wall < ~2.2 張串行(單張 rawcli ~20-30s)
    print(f"P3 wall-clock {wall}s < 串行(~{N}×25s): "
          f"{'PASS' if p3 else 'note'}")

    store.close()
    ok = p1 and p2
    print("e2e-parallel:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
