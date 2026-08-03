#!/usr/bin/env python3
"""Inner runner, rawcli backend (C.3) — RUNS INSIDE the openhands venv.

Same job/envelope contract as inner_acp_runner.py / inner_agentserver_runner.py,
so the harness (dispatcher/grader/three-state) does not change a line — route C
is a profile `backend: rawcli` switch.

RawCLIAgent runs in-process inside an OpenHands Conversation: it spawns
`claude -p`/`codex exec`, distils the native stream-json into fine-grained
OpenHands MessageEvents (captured to events_path for the detail page's
conversation view), and preserves the full native stream separately
(raw.jsonl) for A-level fidelity / protocol regression.

Envelope: completed/session_id/truly_resumed/cost/error — identical shape.
"""

from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # arcp_rawcli lives here


def main() -> int:
    job = json.load(open(sys.argv[1]))
    envelope = {"completed": False, "session_id": None,
                "truly_resumed": False, "cost_usd": None, "error": None}

    def capture(event) -> None:
        try:
            line = event.model_dump_json()
        except Exception:
            line = json.dumps({"kind": type(event).__name__})
        with open(job["events_path"], "a") as f:
            f.write(line + "\n")

    try:
        from arcp_rawcli import RawCLIAgent
        from openhands.sdk.conversation import Conversation

        raw_path = os.path.join(os.path.dirname(job["events_path"]),
                                os.path.basename(job["events_path"])
                                .replace(".events.jsonl", ".raw.jsonl"))
        resume_sid = job.get("resume_session_id")
        agent = RawCLIAgent(
            engine=job.get("acp_server_engine", "claude"),
            model=job.get("acp_model") or "haiku",
            session_id=resume_sid,
            resume=bool(resume_sid),
            raw_events_path=raw_path)
        conv = Conversation(agent=agent, workspace=os.path.abspath(job["ws"]),
                            callbacks=[capture])
        conv.send_message(job["prompt"])
        conv.run()
        # completed = terminal event seen (not just process ended); a crash
        # kills the child before the terminal event → completed False
        envelope["completed"] = agent._got_terminal and not agent._error
        envelope["session_id"] = agent.session_id
        envelope["truly_resumed"] = bool(resume_sid)  # native --resume used
        envelope["cost_usd"] = agent._cost_usd
        envelope["error"] = agent._error
    except Exception as e:
        envelope["error"] = f"{type(e).__name__}: {e}"[:300]

    with open(job["envelope_path"], "w") as f:
        json.dump(envelope, f, ensure_ascii=False)
    return 0 if envelope["completed"] else 1


if __name__ == "__main__":
    sys.exit(main())
