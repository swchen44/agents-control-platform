#!/usr/bin/env python3
"""Inner runner, rawcli backend — W5.5 起純 stdlib(零 OpenHands,跑系統 python)。

Same job/envelope contract as inner_acp_runner.py / inner_agentserver_runner.py,
so the harness (dispatcher/grader/three-state) does not change a line — rawcli
is a profile `backend: rawcli` switch.

RawCLIAgent.run() spawns `claude -p`/`codex exec` directly, distils the native
stream-json into fine-grained event dicts (captured to events_path for the
detail page's conversation view — same JSONL shape as the old SDK MessageEvent),
and preserves the full native stream separately (raw.jsonl) for A-level fidelity.

Envelope: completed/session_id/truly_resumed/cost/error — identical shape.
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # arcp_rawcli lives here


def main() -> int:
    job = json.load(open(sys.argv[1]))
    envelope = {"completed": False, "session_id": None,
                "truly_resumed": False, "cost_usd": None, "error": None,
                "error_kind": None,   # stalled | task | no-terminal | None
                "structured": None}   # G1 agent 結構化自評

    def capture(event: dict) -> None:      # W5.5:event 已是 dict(零 SDK)
        with open(job["events_path"], "a") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    try:
        from arcp_rawcli import RawCLIAgent

        raw_path = os.path.join(os.path.dirname(job["events_path"]),
                                os.path.basename(job["events_path"])
                                .replace(".events.jsonl", ".raw.jsonl"))
        resume_sid = job.get("resume_session_id")
        engine = job.get("acp_server_engine", "claude")
        # model 預設是 engine 相依的:haiku 是 claude 的;codex 不給 model
        # (None → 不帶 --model,用帳號預設),避免 claude model 名塞給 codex
        agent = RawCLIAgent(
            engine=engine,
            model=job.get("acp_model") or ("haiku" if engine == "claude"
                                           else None),
            # W5.1(W29):resume 優先;否則用 harness 預派的 sid(claude
            # --session-id)——crash 後 harness 憑已持久化的 sid resume
            session_id=resume_sid or job.get("preassigned_session_id"),
            resume=bool(resume_sid),
            raw_events_path=raw_path,
            os_sandbox=job.get("os_sandbox", False),         # claude seatbelt
            sandbox=job.get("sandbox", "workspace-write"),    # codex --sandbox
            stall_seconds=float(job.get("stall_seconds", 0)),  # N13 watchdog
            evict_file=job.get("evict_file"),                # E3 實時 killpg
            output_schema=job.get("output_schema"))            # G1 契約(dict|None)
        agent.run(job["prompt"], os.path.abspath(job["ws"]), capture)
        # completed = terminal event seen (not just process ended); a crash
        # kills the child before the terminal event → completed False
        envelope["completed"] = agent._got_terminal and not agent._error
        envelope["session_id"] = agent.session_id
        envelope["truly_resumed"] = bool(resume_sid)  # native --resume used
        envelope["cost_usd"] = agent._cost_usd
        envelope["error"] = agent._error
        envelope["structured"] = agent._structured   # G1
        # error_kind (N13/N3): stalled → dispatcher resumes; no-terminal =
        # crash/kill; task = agent ran but reported error
        if agent._stalled:
            envelope["error_kind"] = "stalled"
        elif agent._evicted:                     # E3:主動驅逐(非故障)
            envelope["error_kind"] = "evicted"
        elif not agent._got_terminal:
            envelope["error_kind"] = "no-terminal"
        elif agent._error:
            envelope["error_kind"] = "task"
    except Exception as e:
        envelope["error"] = f"{type(e).__name__}: {e}"[:300]

    with open(job["envelope_path"], "w") as f:
        json.dump(envelope, f, ensure_ascii=False)
    return 0 if envelope["completed"] else 1


if __name__ == "__main__":
    sys.exit(main())
