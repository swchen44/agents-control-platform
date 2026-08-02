#!/usr/bin/env python3
"""Route-B resume test: crash the ACP adapter mid-task, reattach via
`acp_resume_session_id` (ACP session/load). Claude side (codex is quota-bound).

Mirrors route A's midtool scenario so the rungs are comparable:

  run 1  sequential-file task, SIGKILL the adapter subprocess ~1s after
         step2.txt appears (filesystem poller — same trigger as route A)
  run 2  fresh ACPAgent with acp_resume_session_id=<harvested id>; the SDK
         calls session/load. `_resumed_existing_session` is the SDK's own
         "truly resumed" flag — that plus the session id equality is our C2.

Checks (same shape as route A's recovery_test):
  C1 resume run completes and the grader passes upstream evidence
  C2 session/load ACTUALLY resumed (SDK flag + same session id)
  C3 file chain complete
  C4 no rework (pre-crash files keep their mtimes)

Costs ~$1 (adapter default model). Run under caffeinate.
Artifacts: runtime_resume/{run1,run2}.events.jsonl + results.json.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "jira-agent-poc"))
os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")

from arcp_poc.grader import FileChecklistGrader  # noqa: E402
from openhands.sdk.agent import ACPAgent  # noqa: E402
from openhands.sdk.conversation import Conversation  # noqa: E402
from openhands.sdk.settings.acp_providers import ACP_PROVIDERS  # noqa: E402

EXPECTED = {f"step{n}.txt": "".join(str(i) for i in range(1, n + 1))
            for n in range(1, 6)}
PROMPT = """任務:在目前目錄依序建立 step1.txt 到 step5.txt,規則:
- step1.txt 內容是字串 1
- stepN.txt 內容是「前一個檔案的內容」後面接上 N(例如 step3.txt 內容是 123)
- 必須嚴格依序,一次只建一個檔
- 每建完一個檔,先等待 3 秒(執行 sleep 3)再做下一個
- 全部建完後回覆 ALL_DONE"""
RESUME_PROMPT = "繼續完成先前的任務,不要重做已完成的步驟。"

ROOT = os.path.join(HERE, "runtime_resume")
CMD = list(ACP_PROVIDERS["claude-code"].default_command)


def capture_to(path: str):
    def cb(event) -> None:
        try:
            line = event.model_dump_json()
        except Exception:
            line = json.dumps({"kind": type(event).__name__}, ensure_ascii=False)
        with open(path, "a") as f:
            f.write(line + "\n")
    return cb


def main() -> int:
    shutil.rmtree(ROOT, ignore_errors=True)
    ws = os.path.join(ROOT, "ws")
    os.makedirs(ws)

    # -- run 1: crash the adapter mid-task -------------------------------- #
    agent1 = ACPAgent(acp_command=CMD)
    conv1 = Conversation(agent=agent1, workspace=ws,
                         callbacks=[capture_to(os.path.join(ROOT, "run1.events.jsonl"))])
    conv1.send_message(PROMPT)
    err_holder: list[BaseException] = []

    def run1() -> None:
        try:
            conv1.run()
        except BaseException as e:  # transport death is the EXPECTED outcome
            err_holder.append(e)

    # Trigger/delay tunable: the adapter proved FASTER than a step2+1s trigger
    # (all five files existed at kill time), so default to the tightest window:
    # kill the moment step1.txt exists.
    trigger_file = sys.argv[1] if len(sys.argv) > 1 else "step1.txt"
    delay = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0

    t = threading.Thread(target=run1, daemon=True)
    t.start()
    sid = None
    killed = False
    deadline = time.time() + 240
    while time.time() < deadline and t.is_alive():
        if os.path.exists(os.path.join(ws, trigger_file)):
            sid = getattr(agent1, "_session_id", None)
            if delay:
                time.sleep(delay)
            proc = getattr(agent1, "_process", None)
            if proc is not None:
                try:
                    os.kill(proc.pid, signal.SIGKILL)
                    killed = True
                except ProcessLookupError:
                    pass
            break
        time.sleep(0.05)
    t.join(timeout=60)
    try:
        agent1.close()
    except Exception:
        pass
    pre = {f: os.path.getmtime(os.path.join(ws, f))
           for f in EXPECTED if os.path.exists(os.path.join(ws, f))}
    print(f"run1: killed={killed} sid={sid} files_at_crash={sorted(pre)} "
          f"run_error={type(err_holder[0]).__name__ if err_holder else None}")
    if not (killed and sid):
        print("FATAL: crash setup did not complete"); return 2

    # -- run 2: reattach via acp_resume_session_id ------------------------- #
    agent2 = ACPAgent(acp_command=CMD, acp_resume_session_id=sid)
    try:
        conv2 = Conversation(agent=agent2, workspace=ws,
                             callbacks=[capture_to(os.path.join(ROOT, "run2.events.jsonl"))])
        conv2.send_message(RESUME_PROMPT)
        conv2.run()
        run2_done = True
    except Exception as e:
        print(f"run2 error: {e}")
        run2_done = False
    truly_resumed = bool(getattr(agent2, "_resumed_existing_session", False))
    sid2 = getattr(agent2, "_session_id", None)
    try:
        agent2.close()
    except Exception:
        pass

    checks = {
        "C1_resume_completed": run2_done,
        "C2_session_load_truly_resumed": truly_resumed and sid2 == sid,
        "C3_files_complete": FileChecklistGrader(EXPECTED).grade(ws).passed,
        "C4_no_rework": all(os.path.getmtime(os.path.join(ws, f)) == m
                            for f, m in pre.items()),
    }
    result = {"session_id": sid, "resumed_session_id": sid2,
              "sdk_truly_resumed_flag": truly_resumed,
              "files_at_crash": sorted(pre),
              "checks": checks, "pass": all(checks.values())}
    with open(os.path.join(ROOT, "results.json"), "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    for k, v in checks.items():
        print(("PASS " if v else "FAIL "), k)
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
