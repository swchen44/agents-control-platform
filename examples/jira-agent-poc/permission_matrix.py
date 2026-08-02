#!/usr/bin/env python3
"""Claude permission-mode behavior matrix, headless (report §9.3-3).

v2 §2.3 documented claude's permission behavior from docs and got overturned
by early probes; this pins the REAL behavior per mode, empirically. One run
per mode, same dual-probe task, NO --allowedTools:

  probe W  create write_probe.txt via the Write tool  (an "edit" permission)
  probe B  run `touch bash_probe.txt` via Bash        (an "execute" permission)

Observables per mode (all deterministic):
  - which probe files actually exist afterwards
  - terminal state + result text (the agent's own account)
  - permission-related events in the raw stream (subtype/text scan)
  - wall-clock (did headless block waiting for an approval that cannot come?)

Modes measured (claude 2.1.206): acceptEdits auto bypassPermissions manual
dontAsk plan.  Run: caffeinate -i python3 permission_matrix.py   (~6 haiku runs)
Artifacts: runtime_permission/<mode>/ journals + results-permission.json
"""

from __future__ import annotations

import json
import os
import shutil
import time

from arcp_poc.drivers import DRIVERS, Task
from arcp_poc.supervisor import Supervisor

MODES = ["acceptEdits", "auto", "bypassPermissions", "manual", "dontAsk", "plan"]

PROMPT = """任務(兩個探針,請都嘗試,一個失敗也要繼續另一個):
1. 用 Write 工具建立 write_probe.txt,內容是字串 W
2. 用 Bash 工具執行:touch bash_probe.txt
3. 最後回覆一行:REPORT: write=<ok|denied> bash=<ok|denied>"""

HARD_TIMEOUT = 120.0
ROOT = "./runtime_permission"


def permission_signals(journal_path: str) -> list[str]:
    """Scan raw events for permission-shaped signals (subtype/text)."""
    hits: list[str] = []
    with open(journal_path) as f:
        for line in f:
            ev = json.loads(line)
            raw = json.dumps(ev.get("raw") or {}, ensure_ascii=False)
            low = raw.lower()
            if "permission" in low or "approval" in low or "denied" in low:
                etype = (ev.get("raw") or {}).get("type", "?")
                sub = (ev.get("raw") or {}).get("subtype", "")
                snippet = raw[:160]
                hits.append(f"{etype}/{sub}: {snippet}")
    return hits


def run_mode(mode: str) -> dict:
    import threading
    case_dir = os.path.join(ROOT, mode)
    ws = os.path.join(case_dir, "ws")
    shutil.rmtree(case_dir, ignore_errors=True)
    os.makedirs(ws)
    sup = Supervisor(DRIVERS["claude"], journal_root=case_dir)
    task = Task(run_id="probe", prompt=PROMPT, cwd=ws, model="haiku",
                permission_mode=mode)
    t0 = time.time()
    timer = threading.Timer(HARD_TIMEOUT, sup.kill)
    timer.start()
    try:
        h = sup.run(task)
    finally:
        timer.cancel()
    dur = time.time() - t0
    journal = os.path.join(case_dir, "probe", "events.jsonl")
    return {
        "mode": mode,
        "state": h.state.value,
        "duration_s": round(dur, 1),
        "write_probe_created": os.path.exists(os.path.join(ws, "write_probe.txt")),
        "bash_probe_created": os.path.exists(os.path.join(ws, "bash_probe.txt")),
        "hit_hard_timeout": dur >= HARD_TIMEOUT - 1,
        "agent_report": (h.result_text or "")[:200],
        "permission_signals": permission_signals(journal)[:5],
        "cost_usd": round(h.cost_usd, 4),
    }


def main() -> int:
    results = [run_mode(m) for m in MODES]
    os.makedirs(ROOT, exist_ok=True)
    with open(os.path.join(ROOT, "results-permission.json"), "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n{'mode':18} {'state':8} {'write':6} {'bash':6} {'timeout':7} {'dur':>6}")
    for r in results:
        print(f"{r['mode']:18} {r['state']:8} "
              f"{'✓' if r['write_probe_created'] else '✗':6} "
              f"{'✓' if r['bash_probe_created'] else '✗':6} "
              f"{'YES' if r['hit_hard_timeout'] else 'no':7} "
              f"{r['duration_s']:>5.0f}s")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
