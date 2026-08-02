#!/usr/bin/env python3
"""Live demo: the automatic recovery loop repairs a real crash end to end.

Kills the first attempt at a controlled point (same KillTrigger as
recovery_test.py), then lets run_with_recovery escalate the ladder until the
FileChecklistGrader's evidence passes. Two instructive scenarios:

  python3 loop_demo.py claude midtool:SIGKILL
      hard crash mid-task -> loop repairs via native --resume
  python3 loop_demo.py codex midtool:SIGTERM
      the measured rc=0 fake-DONE: worker "exits cleanly" mid-task, the
      grader vetoes DONE, the loop resumes the thread and finishes the job

Costs tokens (haiku for claude / your codex default). Run under caffeinate.
Artifacts: runtime_loop/<agent>-<case>/.
"""

from __future__ import annotations

import os
import shutil
import signal
import sys
import uuid

from arcp_poc.drivers import DRIVERS, Task
from arcp_poc.grader import FileChecklistGrader
from arcp_poc.recovery_loop import run_with_recovery
from recovery_test import EXPECTED, KillTrigger, TASK_PROMPT, WRITE_HINTS


def main() -> int:
    agent = sys.argv[1] if len(sys.argv) > 1 else "claude"
    phase, signame = (sys.argv[2] if len(sys.argv) > 2
                      else "midtool:SIGKILL").split(":", 1)
    case_dir = os.path.abspath(f"./runtime_loop/{agent}-{phase}-{signame}")
    ws = os.path.join(case_dir, "ws")
    shutil.rmtree(case_dir, ignore_errors=True)
    os.makedirs(ws)

    task = Task(
        run_id="job", prompt=TASK_PROMPT.format(write_hint=WRITE_HINTS[agent]),
        cwd=ws,
        model="haiku" if agent == "claude" else None,
        session_id=str(uuid.uuid4()) if agent == "claude" else None,
        allowed_tools=(["Write", "Read", "Bash(sleep:*)"]
                       if agent == "claude" else None),
    )
    trigger = KillTrigger(phase, getattr(signal, signame), ws)
    result = run_with_recovery(
        DRIVERS[agent], task, FileChecklistGrader(EXPECTED),
        journal_root=case_dir, first_attempt_observers=[trigger])

    print(f"\nkilled_first_attempt={trigger.fired}")
    for a in result.attempts:
        print(f"  {a.mode:11} {a.run_id:20} -> {a.state}"
              f"  ({(a.detail or '')[:80]})")
    print(result.summary())
    return 0 if result.succeeded else 1


if __name__ == "__main__":
    sys.exit(main())
