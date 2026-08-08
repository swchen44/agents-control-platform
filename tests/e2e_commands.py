#!/usr/bin/env python3
"""Phase 3 E2E — 指令通道端到端(真實 Jira,人機協作閉環)。

  C1 cmddemo 票 → dispatch → UNKNOWN → pending:unknown comment
  C2 @agent dance → 收到「不認得指令」說明(§6-14)
  C3 @agent retry → ack、pending 解除、同輪重派(再一次 attempt)
  C4 @agent cancel → ABORTED、ack
  C5 再 poll → 不再派工

Usage: caffeinate -i python3 e2e_commands.py  (live;兩次 10s-timeout attempt,近零成本)
"""

from __future__ import annotations

import json
import shutil
import sys
import time

from arcp.commands import CommandHandler, ExternalChangePolicy
from arcp.config import jira_credentials
from arcp.dispatcher import Dispatcher
from arcp.jira_source import JiraCloudSource
from arcp.paths import config_path
from arcp.poller import OuterLoop
from arcp.profiles import load_profiles
from arcp.routing import load_config
from arcp.store import Store


def attempts_in_journal(path: str, issue_id: int) -> int:
    try:
        return sum(1 for l in open(path)
                   if (e := json.loads(l))["type"] == "attempt_finished"
                   and e["issue_id"] == issue_id)
    except FileNotFoundError:
        return 0


def main() -> int:
    src = JiraCloudSource(*jira_credentials())
    _, routes = load_config(config_path())
    profiles = load_profiles(config_path())
    shutil.rmtree("./runtime_cmd", ignore_errors=True)
    store = Store("./runtime_cmd")
    jql = "project = SCRUM AND labels = cmddemo AND statusCategory != Done"
    loop = OuterLoop(
        src, store, routes, jql,
        dispatcher=Dispatcher(src, store, profiles, root="./runtime_cmd"),
        commands=CommandHandler(src, store, ["Shao-wei Chen"]),
        external=ExternalChangePolicy(src, store, ["完成", "Done"]))

    t = src.create_ticket("SCRUM", f"[e2e-cmd] 指令通道 {int(time.time())}",
                          description="測試指令通道(會 timeout 進 pending)",
                          labels=["cmddemo"])
    print(f"ticket: #{t.id} {t.key}", flush=True)
    journal = "./runtime_cmd/events.jsonl"

    loop.poll_once()
    s = store.get_session(t.id)
    c1 = s and s.outcome == "UNKNOWN" and s.pending_reason == "unknown"
    print(f"C1 dispatch→UNKNOWN pending: {'PASS' if c1 else 'FAIL'}")

    src.add_comment(t.id, "@agent dance")
    loop.poll_once()
    c2 = any("不認得" in c.body for c in src.get_comments(t.id))
    print(f"C2 不認得指令→說明回覆: {'PASS' if c2 else 'FAIL'}")

    src.add_comment(t.id, "@agent retry")
    loop.poll_once()
    n_attempts = attempts_in_journal(journal, t.id)
    acked = sum(1 for c in src.get_comments(t.id)
                if c.body.startswith("[agent] ack: retry"))
    c3 = n_attempts == 2 and acked == 1
    print(f"C3 retry→ack+同輪重派(attempts_in_journal={n_attempts}): "
          f"{'PASS' if c3 else 'FAIL'}")

    src.add_comment(t.id, "@agent cancel")
    loop.poll_once()
    s = store.get_session(t.id)
    c4 = s and s.outcome == "ABORTED" and any(
        c.body.startswith("[agent] ack: cancel")
        for c in src.get_comments(t.id))
    print(f"C4 cancel→ABORTED+ack: {'PASS' if c4 else 'FAIL'}")

    loop.poll_once()
    c5 = attempts_in_journal(journal, t.id) == 2
    print(f"C5 ABORTED 後不再派工: {'PASS' if c5 else 'FAIL'}")

    store.close()
    ok = all([c1, c2, c3, c4, c5])
    print("e2e-commands:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
