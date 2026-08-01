#!/usr/bin/env python3
"""Crash-recovery matrix test for `claude -p` / `codex exec` (report §9.3-1).

Kills a live run at a controlled point, then resumes (`claude --resume <id>`
or `codex exec resume <id>`) and grades the outcome deterministically.
Reuses the exact PoC pipeline (drivers + Supervisor), so a passing matrix
also validates the resume path that drivers.py only sketched.

Session-id asymmetry (the point of testing both CLIs):
  claude  supervisor PRE-ASSIGNS the uuid via --session-id, so it is durable
          before the process even starts.
  codex   no pre-assignment exists — the thread id must be harvested from the
          `thread.started` event before the crash. If the worker dies earlier,
          there is nothing to resume: that risk is part of what this measures.

Matrix (2x2):
  phase  early    kill on first agent activity, BEFORE step1.txt exists
                  ("thinking / no durable progress yet")
         midtool  kill 1s after step2.txt appears — lands inside the
                  `sleep 3` Bash call ("tool executing, partial progress")
  signal SIGTERM  supervisor-style graceful stop
         SIGKILL  hard crash (no cleanup possible)

Deterministic checks per case (same as the manual probe that pinned the
semantics on 2026-08-01):
  C1  resume run reaches a non-error `result` event
  C2  resume event stream carries the ORIGINAL session id
  C3  step1..step5.txt all exist with the dependent-chain contents
  C4  files written before the crash are NOT rewritten (mtime unchanged)

Usage:
  python3 recovery_test.py                 # claude, full 2x2 matrix, 1 rep each
  python3 recovery_test.py --agent codex
  python3 recovery_test.py --case midtool:SIGKILL --reps 2
Costs tokens (live runs; claude uses model=haiku, ~$0.05/case; codex uses
your configured default model).
Artifacts land in runtime_recovery/<case>/ (events.jsonl + snapshot.json
per run — same journal format as the rest of the PoC).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sys
import threading
import time
import uuid

from arcp_poc.drivers import DRIVERS, Task
from arcp_poc.events import AgentEvent, EventType
from arcp_poc.supervisor import RunHandle, Supervisor

TASK_PROMPT = """任務:在目前目錄依序建立 step1.txt 到 step5.txt,規則:
- step1.txt 內容是字串 1
- stepN.txt 內容是「前一個檔案的內容」後面接上 N(例如 step3.txt 內容是 123)
- 必須嚴格依序,一次只建一個檔,{write_hint}
- 每建完一個檔,執行 sleep 3 之後才做下一個
- 全部建完後回覆 ALL_DONE"""

WRITE_HINTS = {
    "claude": "建檔一律用 Write 工具",
    "codex": "建檔一律用 shell 指令(printf,不要用 heredoc)",
}

RESUME_PROMPT = "繼續完成先前的任務。"

EXPECTED = {f"step{n}.txt": "".join(str(i) for i in range(1, n + 1))
            for n in range(1, 6)}

ACTIVITY = {EventType.THINKING, EventType.MESSAGE, EventType.TOOL_STARTED}

HARD_TIMEOUT = 180.0  # per live run, safety net against hangs


class KillTrigger:
    """Observer that crashes the worker at the phase-defined moment.

    Kills the PROCESS GROUP (CLI + its tool children): killing only the CLI
    pid lets an in-flight shell command survive as an orphan and finish the
    task behind the supervisor's back — measured with codex, which batches
    all five steps into a single `zsh -lc` call.

    midtool arms a filesystem POLLER instead of reacting to events: codex
    emits nothing between item.started and item.completed, so event-driven
    arming would fire only after the whole batched command already finished.
    """

    def __init__(self, phase: str, sig: signal.Signals, ws: str):
        self.phase, self.sig, self.ws = phase, sig, ws
        self.armed = False   # a kill was scheduled or sent
        self.fired = False   # the signal was actually delivered

    def _kill(self, pid: int) -> None:
        try:
            os.killpg(os.getpgid(pid), self.sig)
            self.fired = True
        except ProcessLookupError:
            pass

    def _poll_midtool(self, pid: int) -> None:
        target = os.path.join(self.ws, "step2.txt")
        deadline = time.time() + HARD_TIMEOUT
        while time.time() < deadline:
            if os.path.exists(target):
                time.sleep(1.0)  # land inside the following `sleep 3`
                self._kill(pid)
                return
            time.sleep(0.2)

    def __call__(self, event: AgentEvent, h: RunHandle) -> None:
        if self.armed or h.pid is None:
            return
        if self.phase == "early":
            if event.type in ACTIVITY and \
                    not os.path.exists(os.path.join(self.ws, "step1.txt")):
                self.armed = True
                self._kill(h.pid)
        elif self.phase == "midtool":
            self.armed = True  # first event: start polling the workspace
            threading.Thread(target=self._poll_midtool, args=(h.pid,),
                             daemon=True).start()


def run_with_timeout(sup: Supervisor, task: Task, resume: bool) -> RunHandle:
    timer = threading.Timer(HARD_TIMEOUT, sup.kill)
    timer.start()
    try:
        return sup.run(task, resume=resume)
    finally:
        timer.cancel()


def read_session_ids(events_path: str) -> set[str]:
    sids: set[str] = set()
    if os.path.exists(events_path):
        with open(events_path) as f:
            for line in f:
                try:
                    sid = json.loads(line).get("session_id")
                except json.JSONDecodeError:
                    continue
                if sid:
                    sids.add(sid)
    return sids


def run_case(root: str, agent: str, phase: str, signame: str, rep: int) -> dict:
    case_id = f"{agent}-{phase}-{signame}-r{rep}"
    case_dir = os.path.join(root, case_id)
    ws = os.path.join(case_dir, "ws")
    shutil.rmtree(case_dir, ignore_errors=True)
    os.makedirs(ws)
    driver = DRIVERS[agent]
    prompt = TASK_PROMPT.format(write_hint=WRITE_HINTS[agent])
    if agent == "claude":
        common = dict(cwd=ws, model="haiku",
                      allowed_tools=["Write", "Read", "Bash(sleep:*)"])
        sid = str(uuid.uuid4())          # pre-assigned: durable before spawn
    else:
        common = dict(cwd=ws)            # codex: default model, sandbox ws-write
        sid = None                       # must be harvested from thread.started

    # -- run 1: live until the controlled crash --------------------------- #
    trigger = KillTrigger(phase, getattr(signal, signame), ws)
    sup1 = Supervisor(driver, journal_root=case_dir, observers=[trigger])
    task1 = Task(run_id="run1-crash", prompt=prompt, session_id=sid, **common)
    h1 = run_with_timeout(sup1, task1, resume=False)

    pre_files = {f: os.path.getmtime(os.path.join(ws, f))
                 for f in EXPECTED if os.path.exists(os.path.join(ws, f))}
    sid = sid or h1.session_id           # codex: whatever survived the crash

    # -- run 2: resume the same session ----------------------------------- #
    if sid is None:
        # nothing to resume — grade the case as a recovery failure
        checks = {"C1_resume_completed": False, "C2_same_session_id": False,
                  "C3_files_complete": False, "C4_no_rework": False}
        return {"case": case_id, "agent": agent, "phase": phase,
                "signal": signame, "session_id": None, "killed": trigger.fired,
                "files_at_crash": sorted(pre_files),
                "run1_state": h1.state.value, "run2_state": "(not attempted)",
                "note": "no session id harvested before crash",
                "cost_usd": round(h1.cost_usd, 4),
                "checks": checks, "pass": False}
    sup2 = Supervisor(driver, journal_root=case_dir)
    task2 = Task(run_id="run2-resume", prompt=RESUME_PROMPT, session_id=sid,
                 **common)
    h2 = run_with_timeout(sup2, task2, resume=True)

    # -- deterministic grading -------------------------------------------- #
    resumed_sids = read_session_ids(
        os.path.join(case_dir, "run2-resume", "events.jsonl"))
    files_ok = all(
        os.path.isfile(os.path.join(ws, k))
        and open(os.path.join(ws, k)).read().strip() == v
        for k, v in EXPECTED.items())
    no_rework = all(os.path.getmtime(os.path.join(ws, f)) == m
                    for f, m in pre_files.items())
    checks = {
        "C1_resume_completed": h2.state.value == "done",
        "C2_same_session_id": resumed_sids == {sid},
        "C3_files_complete": files_ok,
        "C4_no_rework": no_rework,
    }
    return {
        "case": case_id, "agent": agent, "phase": phase, "signal": signame,
        "session_id": sid,
        "killed": trigger.fired,
        "files_at_crash": sorted(pre_files),
        "run1_state": h1.state.value, "run2_state": h2.state.value,
        "cost_usd": round(h1.cost_usd + h2.cost_usd, 4),
        "checks": checks, "pass": all(checks.values()),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--agent", choices=sorted(DRIVERS), default="claude")
    ap.add_argument("--case", action="append", metavar="PHASE:SIGNAL",
                    help="e.g. midtool:SIGKILL (default: full 2x2 matrix)")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--root", default="./runtime_recovery")
    args = ap.parse_args()

    cases = ([tuple(c.split(":", 1)) for c in args.case] if args.case else
             [(p, s) for p in ("early", "midtool")
              for s in ("SIGTERM", "SIGKILL")])
    results = []
    for phase, signame in cases:
        for rep in range(1, args.reps + 1):
            print(f"=== case {args.agent} {phase}:{signame} rep {rep} ===",
                  flush=True)
            r = run_case(args.root, args.agent, phase, signame, rep)
            results.append(r)
            print(json.dumps(r, ensure_ascii=False, indent=2), flush=True)

    with open(os.path.join(args.root, f"results-{args.agent}.json"), "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print("\n=== summary ===")
    for r in results:
        flags = " ".join(k for k, v in r["checks"].items() if not v)
        print(f"{'PASS' if r['pass'] else 'FAIL':4} {r['case']:22}"
              f" cost=${r['cost_usd']:.3f} {flags}")
    total = sum(r["cost_usd"] for r in results)
    print(f"total cost ≈ ${total:.3f}")
    return 0 if all(r["pass"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
