#!/usr/bin/env python3
"""C.5 — A/B/C 三方對照(同任務同 grader,同機同日,claude haiku)。

  A raw supervisor   (examples/jira-agent-poc)  — normalized 逐 stream_event
  B agent-server ACP (harness filechain-server) — ACP 語意層(粗)
  C RawCLIAgent      (harness filechain-rawcli)  — 蒸餾有意義事件 + 原生保真

量測:normalized 事件數、原生保真行數、cost、completed、grader。
輸出 runtime_abc/results.json 供 COMPARISON.md。

Usage: caffeinate -i .venv/python compare_abc.py  (live,haiku,~$0.15)
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import uuid

os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "examples", "jira-agent-poc"))

from arcp_poc.grader import FileChecklistGrader  # noqa: E402

EXPECTED = {f"step{n}.txt": "".join(str(i) for i in range(1, n + 1))
            for n in range(1, 4)}
PROMPT = ("在目前工作目錄依序建立三個檔案:step1.txt 內容是字串 1、"
          "step2.txt 內容是字串 12、step3.txt 內容是字串 123。"
          "嚴格依序,內容不含引號與空白。完成回覆 TASK_DONE。")
ROOT = os.path.join(HERE, "runtime_abc")
GR = FileChecklistGrader(EXPECTED)


def fresh(name):
    d = os.path.join(ROOT, name)
    ws = os.path.join(d, "ws")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(ws)
    return d, ws


def route_a():
    from arcp_poc.drivers import DRIVERS, Task
    from arcp_poc.supervisor import Supervisor
    d, ws = fresh("A")
    sup = Supervisor(DRIVERS["claude"], journal_root=d, grader=GR)
    h = sup.run(Task(run_id="run", prompt=PROMPT, cwd=ws, model="haiku",
                     session_id=str(uuid.uuid4()),
                     allowed_tools=["Write", "Read", "Bash(sleep:*)"]))
    n = sum(1 for _ in open(os.path.join(d, "run", "events.jsonl")))
    return {"route": "A-raw", "normalized_events": n, "raw_fidelity": n,
            "cost_usd": round(h.cost_usd, 4), "completed": h.state.value == "done",
            "grader": GR.grade(ws).passed}


def route_bc(profile_name, tag):
    from arcp.inner_runner import run_attempt
    from arcp.profiles import load_profiles
    prof = load_profiles("routes.yaml")[profile_name]
    d, ws = fresh(tag)
    res = run_attempt(prof.agent, ws, PROMPT, os.path.join(d, "attempts"), 1)
    ev = res.events_path
    n = sum(1 for _ in open(ev)) if os.path.exists(ev) else 0
    raw = ev.replace(".events.jsonl", ".raw.jsonl")
    rn = sum(1 for _ in open(raw)) if os.path.exists(raw) else 0
    return {"route": tag, "normalized_events": n, "raw_fidelity": rn,
            "cost_usd": res.cost_usd, "completed": res.raw_outcome == "completed",
            "grader": GR.grade(ws).passed}


def main() -> int:
    picks = sys.argv[1:] or ["A", "B", "C"]
    runners = {"A": route_a,
               "B": lambda: route_bc("filechain-server", "B-acp-server"),
               "C": lambda: route_bc("filechain-rawcli", "C-rawcli")}
    results = []
    for p in picks:
        print(f"=== {p} ===", flush=True)
        r = runners[p]()
        results.append(r)
        print(json.dumps(r, ensure_ascii=False), flush=True)
    os.makedirs(ROOT, exist_ok=True)
    json.dump(results, open(os.path.join(ROOT, "results.json"), "w"),
              ensure_ascii=False, indent=2)
    print(f"\n{'route':14} {'norm-ev':>8} {'raw':>6} {'cost':>9} "
          f"{'done':>5} {'grade':>6}")
    for r in results:
        print(f"{r['route']:14} {r['normalized_events']:>8} "
              f"{r['raw_fidelity']:>6} ${r['cost_usd'] or 0:>7.4f} "
              f"{str(r['completed']):>5} {str(r['grader']):>6}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
