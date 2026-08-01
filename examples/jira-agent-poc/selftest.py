#!/usr/bin/env python3
"""Zero-cost self-tests: rule engine, event normalization, state machine,
and replay reaching terminal states. Run: python3 selftest.py"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from arcp_poc.rules import Issue, RuleEngine  # noqa: E402
from arcp_poc.drivers import DRIVERS  # noqa: E402
from arcp_poc.supervisor import Supervisor  # noqa: E402
from arcp_poc.events import RunState  # noqa: E402

here = os.path.dirname(__file__)
ok = 0
fail = 0


def check(name: str, cond: bool) -> None:
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}")


def load(path: str):
    for line in open(path):
        line = line.strip()
        if line:
            yield json.loads(line)


print("rule engine:")
eng = RuleEngine.from_file(os.path.join(here, "rules.json"))
d1 = eng.evaluate(Issue("A-1", "Login crash on submit", "swchen.tw", "stacktrace"))
check("bug+assignee -> codex/jira-bugfix", d1.matched and d1.agent == "codex"
      and "jira-bugfix" in (d1.skills or []))
d2 = eng.evaluate(Issue("A-2", "Update README docs", "someone", ""))
check("docs keyword -> claude", d2.matched and d2.agent == "claude")
d3 = eng.evaluate(Issue("A-3", "Plain task", "nobody", ""))
check("no match -> matched=False", not d3.matched)

print("event normalization + state machine (replay to terminal):")
for agent, fx in [("claude", "fixtures/claude_p_real.jsonl"),
                  ("codex", "fixtures/codex_exec_real.jsonl")]:
    sup = Supervisor(DRIVERS[agent], journal_root="./runtime_selftest")
    h = sup.replay(load(os.path.join(here, fx)), run_id=f"st-{agent}")
    check(f"{agent} reaches DONE", h.state == RunState.DONE)
    check(f"{agent} captured session_id", bool(h.session_id))

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
