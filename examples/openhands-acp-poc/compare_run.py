#!/usr/bin/env python3
"""Phase 3: A/B comparison on the SAME task, graded the SAME way.

Runs the sequential-file chain (route A's canonical probe, minus the sleep
padding that only existed to open kill windows) through:

  A  raw supervisor (examples/jira-agent-poc arcp_poc, claude haiku / codex)
  B  OpenHands ACPAgent (claude-agent-acp / codex-acp, adapter defaults)

and records per run: duration, event count + type histogram, grader verdict,
reported cost, session id availability. Output: runtime_compare/results.json
plus per-run event journals for the granularity table in COMPARISON.md.

Usage:  .venv/bin/python compare_run.py [a-claude|a-codex|b-claude|b-codex ...]
        (no args = all four, ~2-4 min, costs a few cents)
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
import uuid
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "jira-agent-poc"))
os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")

from arcp_poc.drivers import DRIVERS, Task  # noqa: E402
from arcp_poc.grader import FileChecklistGrader  # noqa: E402
from arcp_poc.supervisor import Supervisor  # noqa: E402

EXPECTED = {f"step{n}.txt": "".join(str(i) for i in range(1, n + 1))
            for n in range(1, 6)}
PROMPT = """任務:在目前目錄依序建立 step1.txt 到 step5.txt,規則:
- step1.txt 內容是字串 1
- stepN.txt 內容是「前一個檔案的內容」後面接上 N(例如 step3.txt 內容是 123)
- 必須嚴格依序建立
- 全部建完後回覆 ALL_DONE"""

ROOT = os.path.join(HERE, "runtime_compare")
GRADER = FileChecklistGrader(EXPECTED)
HARD_TIMEOUT = 240.0


def fresh_ws(name: str) -> tuple[str, str]:
    case_dir = os.path.join(ROOT, name)
    ws = os.path.join(case_dir, "ws")
    shutil.rmtree(case_dir, ignore_errors=True)
    os.makedirs(ws)
    return case_dir, ws


def run_route_a(agent: str) -> dict:
    case_dir, ws = fresh_ws(f"a-{agent}")
    sup = Supervisor(DRIVERS[agent], journal_root=case_dir, grader=GRADER)
    task = Task(run_id="run", prompt=PROMPT, cwd=ws,
                model="haiku" if agent == "claude" else None,
                session_id=str(uuid.uuid4()) if agent == "claude" else None)
    timer = threading.Timer(HARD_TIMEOUT, sup.kill)
    timer.start()
    t0 = time.time()
    try:
        h = sup.run(task)
    finally:
        timer.cancel()
    hist = Counter(json.loads(l)["type"]
                   for l in open(os.path.join(case_dir, "run", "events.jsonl")))
    return {"route": "A-raw", "agent": agent, "state": h.state.value,
            "duration_s": round(time.time() - t0, 1),
            "events": sum(hist.values()), "event_types": dict(hist),
            "grader": GRADER.grade(ws).passed,
            "cost_usd": round(h.cost_usd, 4),
            "session_id_known": bool(h.session_id)}


def run_route_b(agent: str) -> dict:
    from openhands.sdk.agent import ACPAgent
    from openhands.sdk.conversation import Conversation
    from openhands.sdk.settings.acp_providers import ACP_PROVIDERS

    kind = {"claude": "claude-code", "codex": "codex"}[agent]
    case_dir, ws = fresh_ws(f"b-{agent}")
    events_path = os.path.join(case_dir, "events.jsonl")

    def capture(event) -> None:
        try:
            line = event.model_dump_json()
        except Exception:
            line = json.dumps({"kind": type(event).__name__,
                               "repr": repr(event)[:300]}, ensure_ascii=False)
        with open(events_path, "a") as f:
            f.write(line + "\n")

    acp = ACPAgent(acp_command=list(ACP_PROVIDERS[kind].default_command))
    t0 = time.time()
    session_id = None
    try:
        conversation = Conversation(agent=acp, workspace=ws,
                                    callbacks=[capture])
        conversation.send_message(PROMPT)
        conversation.run()
        cost = conversation.conversation_stats.get_combined_metrics() \
            .accumulated_cost
        session_id = getattr(acp, "_session_id", None) or \
            (acp.acp_resume_session_id if hasattr(acp, "acp_resume_session_id")
             else None)
    finally:
        acp.close()
    hist: Counter = Counter()
    if os.path.exists(events_path):
        for l in open(events_path):
            e = json.loads(l)
            hist[e.get("kind") or "?"] += 1
    return {"route": "B-openhands", "agent": agent, "state": "done",
            "duration_s": round(time.time() - t0, 1),
            "events": sum(hist.values()), "event_types": dict(hist),
            "grader": GRADER.grade(ws).passed,
            "cost_usd": round(float(cost or 0), 4),
            "session_id_known": bool(session_id)}


RUNNERS = {"a-claude": lambda: run_route_a("claude"),
           "a-codex": lambda: run_route_a("codex"),
           "b-claude": lambda: run_route_b("claude"),
           "b-codex": lambda: run_route_b("codex")}


def main() -> int:
    picks = sys.argv[1:] or list(RUNNERS)
    os.makedirs(ROOT, exist_ok=True)
    results = []
    for name in picks:
        print(f"=== {name} ===", flush=True)
        try:
            r = RUNNERS[name]()
        except Exception as e:  # one route failing must not kill the batch
            r = {"route": name, "agent": name.split("-")[1], "state": "error",
                 "error": str(e)[:300], "grader": False,
                 "duration_s": 0, "events": 0, "event_types": {},
                 "cost_usd": 0, "session_id_known": False}
        results.append(r)
        print(json.dumps(r, ensure_ascii=False), flush=True)
        with open(os.path.join(ROOT, "results.json"), "w") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)  # incremental
    print(f"\n{'route':13} {'agent':7} {'grader':7} {'dur':>6} {'events':>7} {'cost':>8}")
    for r in results:
        print(f"{r['route']:13} {r['agent']:7} "
              f"{'PASS' if r['grader'] else 'FAIL':7} {r['duration_s']:>5.0f}s "
              f"{r['events']:>7} ${r['cost_usd']:>7.4f}")
    return 0 if all(r["grader"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
