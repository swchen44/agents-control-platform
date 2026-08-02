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
from arcp_poc.grader import AllOf, CommandGrader, FileChecklistGrader  # noqa: E402
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

print("evidence-based stop (grader):")
import tempfile  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    g = FileChecklistGrader({"a.txt": "1", "b.txt": None})
    check("files grader: missing files -> fail", not g.grade(tmp).passed)
    open(os.path.join(tmp, "a.txt"), "w").write("1\n")
    open(os.path.join(tmp, "b.txt"), "w").write("anything")
    check("files grader: files+content ok -> pass", g.grade(tmp).passed)
    open(os.path.join(tmp, "a.txt"), "w").write("WRONG")
    check("files grader: wrong content -> fail", not g.grade(tmp).passed)
    check("command grader: rc decides",
          CommandGrader(["true"]).grade(tmp).passed
          and not CommandGrader(["false"]).grade(tmp).passed)
    check("all-of composes", not AllOf(CommandGrader(["true"]), g).grade(tmp).passed)

# The measured trap this layer closes: a stream can END in success (claude's
# `result`, or codex's rc=0-on-SIGTERM) while the task evidence is absent.
# Evidence must outrank self-report: DONE + failing grader => FAILED.
with tempfile.TemporaryDirectory() as tmp:
    sup = Supervisor(DRIVERS["claude"], journal_root="./runtime_selftest",
                     grader=FileChecklistGrader({"never_created.txt": None}))
    h = sup.replay(load(os.path.join(here, "fixtures/claude_p_real.jsonl")),
                   run_id="st-evidence-fail", cwd=tmp)
    check("DONE stream + failing evidence -> overridden to FAILED",
          h.state == RunState.FAILED and "evidence FAIL" in (h.result_text or ""))
    open(os.path.join(tmp, "wanted.txt"), "w").write("x")
    sup2 = Supervisor(DRIVERS["claude"], journal_root="./runtime_selftest",
                      grader=FileChecklistGrader({"wanted.txt": None}))
    h2 = sup2.replay(load(os.path.join(here, "fixtures/claude_p_real.jsonl")),
                     run_id="st-evidence-pass", cwd=tmp)
    check("DONE stream + passing evidence -> stays DONE",
          h2.state == RunState.DONE)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
