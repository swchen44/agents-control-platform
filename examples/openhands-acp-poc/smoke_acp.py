#!/usr/bin/env python3
"""Phase 1/2 smoke: run claude or codex headless via OpenHands ACPAgent.

Usage:  .venv/bin/python smoke_acp.py [claude|codex]

What it measures (PLAN.md V1/V2):
  - does the ACP adapter run headless end to end with LOCAL CLI auth
    (no ANTHROPIC_API_KEY exported) — the auth question is part of the test;
  - the OpenHands event stream, captured verbatim to runtime_smoke/<agent>/
    events.jsonl for the Phase 3 granularity comparison against route A;
  - a deterministic file probe graded the same way route A grades.

The adapter command comes from the SDK's own pinned ACP_PROVIDERS table —
we deliberately do NOT hardcode npx package versions here.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time

os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")

from openhands.sdk.agent import ACPAgent  # noqa: E402
from openhands.sdk.conversation import Conversation  # noqa: E402
from openhands.sdk.settings.acp_providers import ACP_PROVIDERS  # noqa: E402

KIND = {"claude": "claude-code", "codex": "codex"}

PROMPT = ("在目前工作目錄建立 hello.txt,內容是字串 pong(不含引號、不含換行以外的東西)。"
          "建立完成後回覆一行 ALL_DONE。")


def main() -> int:
    agent_name = sys.argv[1] if len(sys.argv) > 1 else "claude"
    provider = ACP_PROVIDERS[KIND[agent_name]]
    case_dir = os.path.abspath(f"./runtime_smoke/{agent_name}")
    ws = os.path.join(case_dir, "ws")
    shutil.rmtree(case_dir, ignore_errors=True)
    os.makedirs(ws)
    events_path = os.path.join(case_dir, "events.jsonl")

    def capture(event) -> None:
        try:
            line = event.model_dump_json()
        except Exception:
            line = json.dumps({"repr": repr(event)[:500]}, ensure_ascii=False)
        with open(events_path, "a") as f:
            f.write(line + "\n")

    print(f"adapter command: {list(provider.default_command)}")
    agent = ACPAgent(acp_command=list(provider.default_command))
    t0 = time.time()
    try:
        conversation = Conversation(agent=agent, workspace=ws,
                                    callbacks=[capture])
        conversation.send_message(PROMPT)
        conversation.run()
    finally:
        agent.close()
    dur = time.time() - t0

    probe = os.path.join(ws, "hello.txt")
    file_ok = os.path.isfile(probe) and open(probe).read().strip() == "pong"
    n_events = sum(1 for _ in open(events_path)) if os.path.exists(events_path) else 0
    print(f"\nagent={agent_name} dur={dur:.0f}s events={n_events} "
          f"file_probe={'PASS' if file_ok else 'FAIL'}")
    return 0 if file_ok else 1


if __name__ == "__main__":
    sys.exit(main())
