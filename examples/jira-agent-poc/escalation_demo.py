#!/usr/bin/env python3
"""Live demo: the escalation loop end to end (report §9.3-6, FR-C4/L5).

Runs claude headless in dontAsk mode with no allowlist so both probes get
denied, and watches the loop react:

  in-stream denial  -> WAITING_PERMISSION event -> escalation ticket opened
  second denial     -> comment appended to the same ticket
  terminal result   -> outcome comment on the originating issue, carrying the
                       STRUCTURED permission_denials list + a resume command

Jira side is DryRunJiraClient (JSONL outbox) so the demo is inspectable and
costs nothing beyond the haiku run (~$0.02).
Artifacts: runtime_escalation/{jira_outbox.jsonl, esc-demo/}.
"""

from __future__ import annotations

import json
import os
import shutil
import sys

from arcp_poc.drivers import DRIVERS, Task
from arcp_poc.escalation import DryRunJiraClient, EscalationObserver
from arcp_poc.supervisor import Supervisor
from permission_matrix import PROMPT


def main() -> int:
    case_dir = os.path.abspath("./runtime_escalation")
    ws = os.path.join(case_dir, "ws")
    shutil.rmtree(case_dir, ignore_errors=True)
    os.makedirs(ws)
    outbox = os.path.join(case_dir, "jira_outbox.jsonl")

    esc = EscalationObserver(DryRunJiraClient(outbox), issue_key="OPS-42")
    sup = Supervisor(DRIVERS["claude"], journal_root=case_dir, observers=[esc])
    h = sup.run(Task(run_id="esc-demo", prompt=PROMPT, cwd=ws,
                     model="haiku", permission_mode="dontAsk"))

    print(f"\nrun state: {h.state.value}  cost ${h.cost_usd:.4f}")
    records = [json.loads(l) for l in open(outbox)] if os.path.exists(outbox) else []
    for r in records:
        head = r.get("summary") or r.get("body", "")
        print(f"  {r['action']:14} {r.get('key') or r.get('issue_key')}: "
              f"{head.splitlines()[0][:90]}")
    tickets = [r for r in records if r["action"] == "create_ticket"]
    ok = bool(tickets) and records[-1]["action"] == "comment" \
        and "resume" in records[-1]["body"]
    print("escalation demo:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
