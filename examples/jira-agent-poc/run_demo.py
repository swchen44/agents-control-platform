#!/usr/bin/env python3
"""Live end-to-end demo — SPENDS TOKENS on your claude/codex subscription.

Simulates one Jira issue (no Jira server needed), runs it through the full
pipeline: rule match -> provision workspace + skills -> supervise a real
`claude -p` / `codex exec` run -> live unified trace + control.

Usage:
  python3 run_demo.py claude   # runs `claude -p` (uses Claude subscription)
  python3 run_demo.py codex    # runs `codex exec` (uses Codex subscription)

Uses a trivial prompt by default to keep cost near zero. Pass a custom prompt:
  python3 run_demo.py claude "Create hello.txt containing the word done"
"""

from __future__ import annotations

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(__file__))

from arcp_poc.drivers import DRIVERS, Task  # noqa: E402
from arcp_poc.rules import Issue, RuleEngine  # noqa: E402
from arcp_poc.workspace import provision  # noqa: E402
from arcp_poc.supervisor import Supervisor, RunHandle  # noqa: E402
from arcp_poc.events import AgentEvent  # noqa: E402


def observer(ev: AgentEvent, h: RunHandle) -> None:
    extra = ""
    if ev.text and ev.type.value in ("message", "run.completed", "run.failed",
                                     "waiting.human", "tool.started"):
        extra = f"  “{(ev.text or ev.tool_name or '')[:70]}”"
    if ev.cost_usd:
        extra += f"  ${ev.cost_usd:.4f}"
    print(f"  {ev.type.value:18} -> {h.state.value:15}{extra}")


def main() -> None:
    agent = sys.argv[1] if len(sys.argv) > 1 else "claude"
    prompt = sys.argv[2] if len(sys.argv) > 2 else "Reply with exactly the word: pong"
    if agent not in DRIVERS:
        print(f"unknown agent {agent!r}; choose from {list(DRIVERS)}"); sys.exit(1)

    here = os.path.dirname(__file__)

    # 1. A fake incoming Jira issue
    issue = Issue(key="DEMO-1", summary=f"[{agent}] {prompt}",
                  assignee="swchen.tw", description=prompt, status="Open")

    # 2. Rule match -> Decision (agent/skills/repo)
    engine = RuleEngine.from_file(os.path.join(here, "rules.json"))
    decision = engine.evaluate(issue)
    print(f"rule matched: {decision.rule_name!r} -> agent={decision.agent} "
          f"skills={decision.skills}")

    # 3. Provision an isolated workspace + install the selected skills
    prov = provision(run_root=os.path.join(here, "runtime_live/workspaces"),
                     issue_key=issue.key,
                     skills=decision.skills or [],
                     skills_source_dir=os.path.join(here, "skills"))
    print(f"workspace: {prov.cwd}  installed_skills={prov.installed_skills}")

    # 4. Supervise a real run with live unified trace
    driver = DRIVERS[agent]  # honor CLI arg; a full system would use decision.agent
    task = Task(run_id=f"{issue.key}-{uuid.uuid4().hex[:8]}",
                prompt=prompt, cwd=prov.cwd,
                model="claude-haiku-4-5-20251001" if agent == "claude" else None,
                session_id=str(uuid.uuid4()) if agent == "claude" else None)
    sup = Supervisor(driver, journal_root=os.path.join(here, "runtime_live"),
                     stall_seconds=45, observers=[observer])
    print(f"\n--- live run ({agent}) ---")
    h = sup.run(task)
    print(f"--- final: state={h.state.value} session={h.session_id} "
          f"cost=${h.cost_usd:.4f} result={h.result_text!r} ---")
    print(f"trace journal: {os.path.join(sup.journal_root, task.run_id)}/events.jsonl")


if __name__ == "__main__":
    main()
