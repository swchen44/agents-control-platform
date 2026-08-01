#!/usr/bin/env python3
"""Offline end-to-end demo — NO tokens spent.

Feeds the REAL captured event streams (fixtures/*.jsonl) through the exact same
normalize -> state-machine -> journal -> observer pipeline the live supervisor
uses. Proves the cross-CLI trace + control layer works for both claude -p and
codex exec, and shows the unified trace side by side.

Run:  python3 replay_demo.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from arcp_poc.drivers import DRIVERS  # noqa: E402
from arcp_poc.supervisor import Supervisor, RunHandle  # noqa: E402
from arcp_poc.events import AgentEvent  # noqa: E402


def load(path: str):
    for line in open(path):
        line = line.strip()
        if line:
            yield json.loads(line)


def make_observer(label: str):
    def obs(ev: AgentEvent, h: RunHandle) -> None:
        extra = ""
        if ev.text and ev.type.value in ("message", "run.completed", "run.failed",
                                         "waiting.human"):
            extra = f"  “{(ev.text or '')[:60]}”"
        if ev.cost_usd:
            extra += f"  ${ev.cost_usd:.4f}"
        print(f"[{label:6}] {ev.type.value:18} -> state={h.state.value:15}{extra}")
    return obs


def run_fixture(agent: str, fixture: str) -> RunHandle:
    driver = DRIVERS[agent]
    sup = Supervisor(driver, journal_root="./runtime_replay",
                     observers=[make_observer(agent)])
    print(f"\n===== REPLAY: {agent}  ({fixture}) =====")
    h = sup.replay(load(fixture), run_id=f"replay-{agent}")
    print(f"----- final: state={h.state.value} session={h.session_id} "
          f"cost=${h.cost_usd:.4f} result={h.result_text!r}")
    return h


if __name__ == "__main__":
    here = os.path.dirname(__file__)
    run_fixture("claude", os.path.join(here, "fixtures/claude_p_real.jsonl"))
    run_fixture("codex", os.path.join(here, "fixtures/codex_exec_real.jsonl"))
    print("\nBoth workers produced the SAME normalized event vocabulary and "
          "drove the SAME state machine — that is the cross-CLI layer.")
