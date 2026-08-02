#!/usr/bin/env python3
"""Workspace-relocation recovery test (report §9.3: the #48835 trap, folder form).

claude scopes its session store to the STARTING cwd (~/.claude/projects/
<encoded-path>/). If the workspace folder moves — or the supervisor restarts
with a differently-named per-issue folder — native `--resume` looks in the
wrong project bucket and reports "No conversation found". This measures that
trap and proves the ladder's rung 2 repairs it, because ARCP's journal lives
with the run (journal_root), not with claude's cwd-derived bucket.

Phases:
  1. crash a run mid-task inside ws-A (same KillTrigger as recovery_test)
  2. mv ws-A -> ws-B                  (workspace relocation)
  3. native resume with cwd=ws-B     -> EXPECTED to fail (the trap)
  4. transcript resume in ws-B       -> expected to finish without rework

Deterministic checks:
  W1 native resume from the moved path fails (and we capture the CLI's words)
  W2 transcript rung completes (grader-verified DONE)
  W3 file chain complete in ws-B
  W4 no rework: files created before the crash keep their mtimes (mv preserves)

Costs ~ $0.05 (haiku). Run under caffeinate. Artifacts: runtime_workspace/.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import sys
import threading
import uuid

from arcp_poc.drivers import DRIVERS, Task
from arcp_poc.grader import FileChecklistGrader
from arcp_poc.resume_transcript import (build_transcript_resume_task,
                                        load_journal_events)
from arcp_poc.supervisor import Supervisor
from recovery_test import (EXPECTED, HARD_TIMEOUT, KillTrigger, RESUME_PROMPT,
                           TASK_PROMPT, WRITE_HINTS)


def run_with_timeout(sup: Supervisor, task: Task, resume: bool):
    timer = threading.Timer(HARD_TIMEOUT, sup.kill)
    timer.start()
    try:
        return sup.run(task, resume=resume)
    finally:
        timer.cancel()


def main() -> int:
    root = os.path.abspath("./runtime_workspace")
    shutil.rmtree(root, ignore_errors=True)
    ws_a = os.path.join(root, "ws-A")
    ws_b = os.path.join(root, "ws-B")
    os.makedirs(ws_a)
    driver = DRIVERS["claude"]
    sid = str(uuid.uuid4())
    common = dict(model="haiku", session_id=sid,
                  allowed_tools=["Write", "Read", "Bash(sleep:*)"])
    grader = FileChecklistGrader(EXPECTED)

    # -- phase 1: crash inside ws-A --------------------------------------- #
    trigger = KillTrigger("midtool", signal.SIGKILL, ws_a)
    sup1 = Supervisor(driver, journal_root=root, observers=[trigger])
    task1 = Task(run_id="run1-crash", cwd=ws_a,
                 prompt=TASK_PROMPT.format(write_hint=WRITE_HINTS["claude"]),
                 **common)
    h1 = run_with_timeout(sup1, task1, resume=False)
    pre = {f: os.path.getmtime(os.path.join(ws_a, f))
           for f in EXPECTED if os.path.exists(os.path.join(ws_a, f))}
    print(f"phase1 crash: state={h1.state.value} killed={trigger.fired} "
          f"files={sorted(pre)}")

    # -- phase 2: relocate the workspace ---------------------------------- #
    os.rename(ws_a, ws_b)

    # -- phase 3: native resume from the NEW path (the trap) --------------- #
    sup2 = Supervisor(driver, journal_root=root, grader=grader)
    task2 = Task(run_id="run2-native", cwd=ws_b, prompt=RESUME_PROMPT, **common)
    h2 = run_with_timeout(sup2, task2, resume=True)
    stderr_path = os.path.join(root, "run2-native", "stderr.log")
    stderr = open(stderr_path).read() if os.path.exists(stderr_path) else ""
    native_failed = h2.state.value != "done"
    print(f"phase3 native-from-moved-path: state={h2.state.value} "
          f"stderr={stderr.strip()[:120]!r}")

    # -- phase 4: transcript rung repairs it ------------------------------- #
    events = load_journal_events(os.path.join(root, "run1-crash",
                                              "events.jsonl"))
    sup3 = Supervisor(driver, journal_root=root, grader=grader)
    task3 = build_transcript_resume_task(
        Task(run_id="x", cwd=ws_b,
             prompt=TASK_PROMPT.format(write_hint=WRITE_HINTS["claude"]),
             **common),
        events, run_id="run3-transcript")
    h3 = run_with_timeout(sup3, task3, resume=False)
    print(f"phase4 transcript: state={h3.state.value}")

    checks = {
        "W1_native_resume_fails_after_move": native_failed,
        "W2_transcript_rung_completes": h3.state.value == "done",
        "W3_files_complete": grader.grade(ws_b).passed,
        "W4_no_rework": all(
            os.path.getmtime(os.path.join(ws_b, f)) == m
            for f, m in pre.items()),
    }
    result = {"session_id": sid, "files_at_crash": sorted(pre),
              "native_state": h2.state.value,
              "native_stderr": stderr.strip()[:200],
              "checks": checks, "pass": all(checks.values())}
    with open(os.path.join(root, "results-workspace.json"), "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    for k, v in checks.items():
        print(("PASS " if v else "FAIL "), k)
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
