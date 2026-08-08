#!/usr/bin/env python3
"""Phase 1 E2E — grey rollout against the REAL AGT project.

Proves the poller records-but-never-acts, and that re-polling is idempotent:

  G1 create a test ticket (label 'agent') → poll #1 journals
     new_issue + route_matched(agent-labeled, notify_only)
  G2 poll #2 immediately → zero events (state diff idempotent)
  G3 add a comment → poll #3 → exactly one comment_added
  G4 poll #4 → zero events (comment watermark holds)

The test ticket stays in AGT as evidence (summary prefixed [e2e-gray]).
Usage: python3 e2e_gray.py
"""

from __future__ import annotations

import shutil
import sys
import time

from arcp.config import jira_credentials
from arcp.jira_source import JiraCloudSource
from arcp.paths import config_path
from arcp.poller import OuterLoop
from arcp.routing import load_config
from arcp.store import Store


def main() -> int:
    src = JiraCloudSource(*jira_credentials())
    source_cfg, routes = load_config(config_path())
    shutil.rmtree("./runtime_outer", ignore_errors=True)
    store = Store("./runtime_outer")
    loop = OuterLoop(src, store, routes, source_cfg["jql"])

    t = src.create_ticket("SCRUM", f"[e2e-gray] agent smoke {int(time.time())}",
                          description="Phase 1 灰度驗證測試票(可關閉)",
                          labels=["agent"])
    print(f"test ticket: #{t.id} {t.key}")

    ev1 = loop.poll_once()
    types1 = [(e["type"], e.get("route")) for e in ev1 if e["issue_id"] == t.id]
    g1 = ("new_issue", None) in types1 and \
         ("route_matched", "agent-labeled") in types1
    print(f"G1 new_issue+route_matched(notify_only): "
          f"{'PASS' if g1 else 'FAIL'} {types1}")

    ev2 = [e for e in loop.poll_once() if e["issue_id"] == t.id]
    g2 = not ev2
    print(f"G2 re-poll idempotent: {'PASS' if g2 else 'FAIL'} {ev2}")

    src.add_comment(t.id, "e2e: 這是一則測試留言")
    ev3 = [e for e in loop.poll_once() if e["issue_id"] == t.id]
    g3 = len(ev3) == 1 and ev3[0]["type"] == "comment_added"
    print(f"G3 exactly one comment_added: {'PASS' if g3 else 'FAIL'} "
          f"{[(e['type']) for e in ev3]}")

    ev4 = [e for e in loop.poll_once() if e["issue_id"] == t.id]
    g4 = not ev4
    print(f"G4 comment watermark holds: {'PASS' if g4 else 'FAIL'} {ev4}")

    store.close()
    ok = g1 and g2 and g3 and g4
    print("e2e-gray:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
