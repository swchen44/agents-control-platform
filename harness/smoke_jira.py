#!/usr/bin/env python3
"""Phase 0 smoke: auth works, AGT is reachable, the Ticket model round-trips.

Read-only against Jira (no ticket creation, no comments).
Usage: python3 smoke_jira.py
"""

from __future__ import annotations

import sys

from arcp_harness.config import jira_credentials
from arcp_harness.jira_source import JiraCloudSource

PROJECT = "AGT"


def main() -> int:
    src = JiraCloudSource(*jira_credentials())

    me = src.myself()
    ok_auth = bool(me.get("accountId"))
    print(f"auth: {'PASS' if ok_auth else 'FAIL'} "
          f"(displayName={me.get('displayName', '?')})")

    tickets = src.search(f"project = {PROJECT} ORDER BY created DESC")
    print(f"search: PASS ({len(tickets)} issue(s) in {PROJECT})")
    for t in tickets[:5]:
        print(f"  #{t.id} {t.key} [{t.state}] "
              f"assignee={t.assignee or '-'} :: {t.summary[:60]}")
        ok_model = t.id > 0 and t.key.startswith(PROJECT)
        if not ok_model:
            print("  MODEL CHECK FAIL"); return 1

    return 0 if ok_auth else 1


if __name__ == "__main__":
    sys.exit(main())
