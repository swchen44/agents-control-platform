#!/usr/bin/env python3
"""Phase 2 M2 E2E — 第一張票端到端(真實 Jira + 真實 agent 執行)。

  E1 建票(label filechain,描述=step1-3 檔案鏈)→ poll(含 dispatch)
     → store 記 SUCCESS
  E2 workspace 檔案通過 profile 的確定性驗證
  E3 票上出現 [agent] outcome=SUCCESS 證據 comment
  E4 再 poll → 不重派、無重複 comment(冪等)

Usage: caffeinate -i python3 e2e_dispatch.py   (live,haiku,~$0.05)
"""

from __future__ import annotations

import shutil
import sys
import time

from arcp.config import jira_credentials
from arcp.dispatcher import Dispatcher, _grader
from arcp.jira_source import JiraCloudSource
from arcp.paths import config_path
from arcp.poller import OuterLoop
from arcp.profiles import load_profiles
from arcp.routing import load_config
from arcp.store import Store

TASK_DESC = ("在目前工作目錄依序建立三個檔案:step1.txt 內容是字串 1、"
             "step2.txt 內容是字串 12、step3.txt 內容是字串 123。"
             "必須嚴格依序建立,內容不含引號與多餘空白。")


def main() -> int:
    src = JiraCloudSource(*jira_credentials())
    source_cfg, routes = load_config(config_path())
    profiles = load_profiles(config_path())
    shutil.rmtree("./runtime_m2", ignore_errors=True)
    store = Store("./runtime_m2")
    dispatcher = Dispatcher(src, store, profiles, root="./runtime_m2")
    loop = OuterLoop(src, store, routes, source_cfg["jql"],
                     dispatcher=dispatcher)

    t = src.create_ticket("SCRUM", f"[e2e-m2] 檔案鏈任務 {int(time.time())}",
                          description=TASK_DESC,
                          labels=["arcp.filechain"])
    print(f"test ticket: #{t.id} {t.key}", flush=True)

    ev1 = [e for e in loop.poll_once() if e["issue_id"] == t.id]
    print("poll#1 events:", [e["type"] for e in ev1], flush=True)

    sess = store.get_session(t.id)
    e1 = sess is not None and sess.outcome == "SUCCESS"
    print(f"E1 store outcome=SUCCESS: {'PASS' if e1 else 'FAIL'} "
          f"(outcome={getattr(sess, 'outcome', None)}, "
          f"attempts={getattr(sess, 'attempts', 0)}, "
          f"cost=${getattr(sess, 'cost_usd', 0):.4f})")

    e2 = bool(sess) and _grader(profiles["filechain"]).grade(
        sess.workspace).passed
    print(f"E2 workspace 通過確定性驗證: {'PASS' if e2 else 'FAIL'}")

    comments = src.get_comments(t.id)
    success_comments = [c for c in comments
                        if "outcome=SUCCESS" in c.body]
    e3 = len(success_comments) == 1
    print(f"E3 票上有證據 comment: {'PASS' if e3 else 'FAIL'} "
          f"({len(comments)} comment(s))")

    ev2 = [e for e in loop.poll_once() if e["issue_id"] == t.id
           and e["type"] not in ("comment_added",)]  # 自己的回寫留言會被看見
    e4 = not ev2 and len([c for c in src.get_comments(t.id)
                          if "outcome=SUCCESS" in c.body]) == 1
    print(f"E4 再 poll 不重派、無重複 comment: {'PASS' if e4 else 'FAIL'} "
          f"{[e['type'] for e in ev2]}")

    store.close()
    ok = e1 and e2 and e3 and e4
    print("e2e-m2:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
