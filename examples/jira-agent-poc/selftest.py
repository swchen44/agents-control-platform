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

print("degraded resume (transcript, recovery rung 2):")
from arcp_poc.drivers import Task  # noqa: E402
from arcp_poc.resume_transcript import (  # noqa: E402
    RESUME_CONTEXT_MARKER, build_transcript_resume_task,
    render_resume_transcript)

evs = [
    {"type": "message", "text": "step1 done"},
    {"type": "tool.started", "tool_name": "Write"},
    {"type": "raw", "text": "noise-not-rendered"},
    {"type": "run.failed", "text": "worker exited rc=-9 (crash?)"},
]
t = render_resume_transcript(evs, "依序建立 step1~5")
check("transcript: marker + task + crash forensics, no raw noise",
      t.startswith(RESUME_CONTEXT_MARKER) and "依序建立 step1~5" in t
      and "rc=-9" in t and "noise-not-rendered" not in t)
big = [{"type": "message", "text": f"m{i} " + "x" * 100} for i in range(2000)]
t2 = render_resume_transcript(big, "p", max_chars=5000)
check("transcript: budget truncates oldest-first (tail kept)",
      len(t2) <= 5200 and "m1999" in t2 and "[assistant] m0 " not in t2)
orig = Task(run_id="r", prompt="p", cwd=".", session_id="OLD-ID", model="haiku")
nt = build_transcript_resume_task(orig, evs, "r2")
check("transcript task: claude gets a FRESH session id",
      bool(nt.session_id) and nt.session_id != "OLD-ID"
      and nt.model == "haiku")
check("transcript task: codex stays id-less",
      build_transcript_resume_task(
          Task(run_id="r", prompt="p", cwd="."), evs, "r2").session_id is None)

print("automatic recovery loop (policy via scripted runner):")
from arcp_poc.events import RunState as _RS  # noqa: E402
from arcp_poc.recovery_loop import run_with_recovery  # noqa: E402
from arcp_poc.supervisor import RunHandle  # noqa: E402


def scripted(states, sids):
    it = iter(zip(states, sids))
    calls = []

    def run(task, resume, observers):
        st, sid = next(it)
        calls.append((task.run_id, resume))
        return RunHandle(run_id=task.run_id, agent="fake", cwd=".",
                         session_id=sid, state=st)
    return run, calls


base = Task(run_id="job", prompt="do it", cwd=".", session_id="SID")
nogr = FileChecklistGrader({})  # unused: the scripted runner decides states

r, calls = scripted([_RS.DONE], ["SID"])
res = run_with_recovery(DRIVERS["claude"], base, nogr,
                        journal_root="/tmp/arcp-loop-check", runner=r)
check("loop: first-try done -> single attempt",
      res.succeeded and [a.mode for a in res.attempts] == ["initial"])

r, calls = scripted([_RS.FAILED, _RS.DONE], ["SID", "SID"])
res = run_with_recovery(DRIVERS["claude"], base, nogr,
                        journal_root="/tmp/arcp-loop-check", runner=r)
check("loop: crash -> native resume repairs (resume flag set)",
      res.succeeded and [a.mode for a in res.attempts] == ["initial", "native"]
      and calls[1][1] is True)

r, calls = scripted([_RS.FAILED, _RS.DONE], [None, None])
res = run_with_recovery(DRIVERS["codex"],
                        Task(run_id="job2", prompt="p", cwd="."), nogr,
                        journal_root="/tmp/arcp-loop-check", runner=r)
check("loop: no session id -> skip native, go transcript",
      res.succeeded
      and [a.mode for a in res.attempts] == ["initial", "transcript"])

r, calls = scripted([_RS.FAILED] * 4, ["SID"] * 4)
res = run_with_recovery(DRIVERS["claude"], base, nogr,
                        journal_root="/tmp/arcp-loop-check", runner=r)
check("loop: full ladder then give up (never retries a rung)",
      not res.succeeded and [a.mode for a in res.attempts]
      == ["initial", "native", "transcript", "rerun"])

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
