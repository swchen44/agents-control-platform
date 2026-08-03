#!/usr/bin/env python3
"""Inner runner, route-B implementation — RUNS INSIDE the openhands venv.

Invoked by arcp_harness.inner_runner with a JSON job file:
    {"ws": ..., "prompt": ..., "acp_server": "claude-code",
     "acp_model": "haiku"|null, "resume_session_id": null|str,
     "events_path": ..., "envelope_path": ...}

Everything lands in FILES (lesson #7: stdout is for humans, files are truth):
    events_path    OpenHands event stream (JSONL)
    envelope_path  L2 result envelope:
        {"completed": bool, "session_id": str|null, "truly_resumed": bool,
         "cost_usd": float|null, "error": str|null}

The harness classifies the three-state outcome; this script only reports raw
facts. A missing/partial envelope means the harness CANNOT prove what
happened → UNKNOWN (v5 D3), which is exactly right for a dead runner.

B→C note: route C swaps this file for a rawcli runner (claude -p print mode);
the job/envelope contract stays identical.
"""

from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")


def main() -> int:
    job = json.load(open(sys.argv[1]))
    envelope: dict = {"completed": False, "session_id": None,
                      "truly_resumed": False, "cost_usd": None, "error": None}

    def capture(event) -> None:
        try:
            line = event.model_dump_json()
        except Exception:
            line = json.dumps({"kind": type(event).__name__})
        with open(job["events_path"], "a") as f:
            f.write(line + "\n")

    try:
        from openhands.sdk.agent import ACPAgent
        from openhands.sdk.conversation import Conversation
        from openhands.sdk.settings.acp_providers import ACP_PROVIDERS

        kwargs: dict = {"acp_command": list(
            ACP_PROVIDERS[job["acp_server"]].default_command)}
        if job.get("acp_model"):
            kwargs["acp_model"] = job["acp_model"]
        if job.get("resume_session_id"):
            kwargs["acp_resume_session_id"] = job["resume_session_id"]
        agent = ACPAgent(**kwargs)
        try:
            conv = Conversation(agent=agent, workspace=job["ws"],
                                callbacks=[capture])
            conv.send_message(job["prompt"])
            conv.run()
            envelope["completed"] = True
            try:
                envelope["cost_usd"] = float(
                    conv.conversation_stats.get_combined_metrics()
                    .accumulated_cost or 0)
            except Exception:
                pass
        finally:
            envelope["session_id"] = getattr(agent, "_session_id", None)
            envelope["truly_resumed"] = bool(
                getattr(agent, "_resumed_existing_session", False))
            try:
                agent.close()
            except Exception:
                pass
    except Exception as e:  # raw fact, not a verdict
        envelope["error"] = f"{type(e).__name__}: {e}"[:400]

    with open(job["envelope_path"], "w") as f:
        json.dump(envelope, f, ensure_ascii=False)
    return 0 if envelope["completed"] else 1


if __name__ == "__main__":
    sys.exit(main())
