#!/usr/bin/env python3
"""Phase 2 殘項 — fault-injection E2E:retry 迴路與 UNKNOWN 路徑的 live 證據。

F1 retry+evidence(票只說 step1.txt,verify 還要 extra.txt):
   attempt1 完成但驗證失敗 → 失敗證據餵回 attempt2(native resume,
   envelope.truly_resumed=true)→ 補齊 → SUCCESS,attempts==2
F2 UNKNOWN(timeout 10s < ACP 啟動時間):
   runner 被殺無 envelope → outcome=UNKNOWN、pending:unknown、
   票上 pending comment;再 poll 不重試(只有人能解,v5 D3)

Usage: caffeinate -i python3 e2e_fault.py   (live,haiku,~$0.1)
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time

from arcp_harness.config import jira_credentials
from arcp_harness.dispatcher import Dispatcher
from arcp_harness.jira_source import JiraCloudSource
from arcp_harness.poller import OuterLoop
from arcp_harness.profiles import load_profiles
from arcp_harness.routing import load_config
from arcp_harness.store import Store

F1_DESC = ("在目前工作目錄建立 step1.txt,內容是字串 1。"
           "(不要建立任何其他檔案,除非之後有回饋要求。)")
F2_DESC = "在目前工作目錄建立 never.txt,內容是字串 x。"


def main() -> int:
    src = JiraCloudSource(*jira_credentials())
    source_cfg, routes = load_config("routes.yaml")
    profiles = load_profiles("routes.yaml")
    shutil.rmtree("./runtime_fault", ignore_errors=True)
    store = Store("./runtime_fault")
    test_jql = ("project = SCRUM AND labels in (faultretry, faultdead) "
            "AND statusCategory != Done")  # scope 自己的票,防重派舊票(lesson #9)
    loop = OuterLoop(src, store, routes, test_jql,
                     dispatcher=Dispatcher(src, store, profiles,
                                           root="./runtime_fault"))

    ts = int(time.time())
    t1 = src.create_ticket("SCRUM", f"[e2e-f1] retry 迴路 {ts}",
                           description=F1_DESC, labels=["faultretry"],
                           issue_type="任務")
    t2 = src.create_ticket("SCRUM", f"[e2e-f2] unknown 路徑 {ts}",
                           description=F2_DESC, labels=["faultdead"],
                           issue_type="任務")
    print(f"tickets: {t1.key}(retry) {t2.key}(unknown)", flush=True)

    loop.poll_once()

    # ---- F1:retry + evidence + native resume --------------------------- #
    s1 = store.get_session(t1.id)
    env2_path = os.path.join("./runtime_fault", f"tickets/{t1.id}",
                             "attempts", "a2.envelope.json")
    truly_resumed = (json.load(open(env2_path)).get("truly_resumed")
                     if os.path.exists(env2_path) else None)
    f1a = s1 and s1.outcome == "SUCCESS" and s1.attempts == 2
    print(f"F1a 敗一次後補齊(SUCCESS, attempts=2): "
          f"{'PASS' if f1a else 'FAIL'} "
          f"(outcome={getattr(s1, 'outcome', None)}, "
          f"attempts={getattr(s1, 'attempts', 0)})")
    f1b = truly_resumed is True
    print(f"F1b attempt2 native resume(truly_resumed): "
          f"{'PASS' if f1b else 'FAIL'} ({truly_resumed})")
    extra = os.path.join("./runtime_fault", f"tickets/{t1.id}", "ws",
                         "extra.txt")
    f1c = os.path.isfile(extra)   # existence-only(lesson #10)
    print(f"F1c extra.txt 由 feedback 補建: {'PASS' if f1c else 'FAIL'}")

    # ---- F2:UNKNOWN → pending:unknown ---------------------------------- #
    s2 = store.get_session(t2.id)
    f2a = s2 and s2.outcome == "UNKNOWN" and s2.pending_reason == "unknown"
    print(f"F2a outcome=UNKNOWN + pending:unknown: "
          f"{'PASS' if f2a else 'FAIL'} "
          f"(outcome={getattr(s2, 'outcome', None)}, "
          f"reason={getattr(s2, 'pending_reason', None)})")
    f2b = any("outcome=UNKNOWN" in c.body for c in src.get_comments(t2.id))
    print(f"F2b 票上有 pending 說明 comment: {'PASS' if f2b else 'FAIL'}")

    before = (store.get_session(t2.id).attempts,
              store.get_session(t1.id).attempts)
    loop.poll_once()
    after = (store.get_session(t2.id).attempts,
             store.get_session(t1.id).attempts)
    f3 = before == after
    print(f"F3 再 poll:UNKNOWN 不自動重試、SUCCESS 不重派: "
          f"{'PASS' if f3 else 'FAIL'} {before}→{after}")

    store.close()
    ok = all([f1a, f1b, f1c, f2a, f2b, f3])
    print("e2e-fault:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
