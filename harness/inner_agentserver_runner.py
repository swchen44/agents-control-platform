#!/usr/bin/env python3
"""Inner runner, agent-server backend (B+.1) — RUNS INSIDE the openhands venv.

Same job/envelope contract as inner_acp_runner.py, so the harness (dispatcher,
grader, three-state logic) does not change a line — swapping route B's
in-process ACP for agent-server is a profile `backend:` switch (survival rule 2).

Difference from the in-process runner: instead of ACPAgent+Conversation
in-process, this starts (or reuses) a local agent-server, creates the
conversation over REST with the ACPAgent serialized into the `agent` field,
and polls events until execution_status==finished. The conversation therefore
lives in a server → visible to the OpenHands GUI (B+.2 harvest).

Envelope mapping (spike B+.0):
    completed      ← execution_status == "finished"  (no error event)
    session_id     ← agent_state.acp_session_id
    truly_resumed  ← agent_state._resumed_existing_session (best-effort)
    cost_usd       ← GET /conversations/{id}.metrics.accumulated_cost
    error          ← ConversationErrorEvent.detail  (structured, e.g. quota)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")

HOST = "127.0.0.1"


def _api(base, key, method, path, body=None, timeout=60.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"X-Session-API-Key": key,
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw) if raw.strip() else {}


def _wait_ready(base, deadline_s=120):
    end = time.time() + deadline_s
    while time.time() < end:
        try:
            with urllib.request.urlopen(base + "/", timeout=5):
                return True
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(1.0)
    return False


def main() -> int:
    job = json.load(open(sys.argv[1]))
    envelope = {"completed": False, "session_id": None,
                "truly_resumed": False, "cost_usd": None, "error": None}
    port = int(job.get("server_port", 18010))
    key = job.get("server_api_key", "harness-local-only")
    base = f"http://{HOST}:{port}"

    # start a private server for this attempt (simple + isolated; a long-lived
    # shared server is a B+.2 optimization once the GUI wants persistence)
    env = dict(os.environ)
    env["OH_SESSION_API_KEYS_0"] = key
    env["OH_PERSISTENCE_DIR"] = job["persist_dir"]
    env["OPENHANDS_SUPPRESS_BANNER"] = "1"
    log = open(os.path.join(os.path.dirname(job["events_path"]),
                            "server.log"), "w")
    server = subprocess.Popen(
        [sys.executable, "-m", "openhands.agent_server",
         "--host", HOST, "--port", str(port)],
        env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    try:
        if not _wait_ready(base):
            envelope["error"] = "agent-server did not become ready"
            raise SystemExit
        from openhands.sdk.agent import ACPAgent
        from openhands.sdk.settings.acp_providers import ACP_PROVIDERS
        kwargs = {"acp_command": list(
            ACP_PROVIDERS[job["acp_server"]].default_command)}
        if job.get("acp_model"):
            kwargs["acp_model"] = job["acp_model"]
        if job.get("resume_session_id"):
            kwargs["acp_resume_session_id"] = job["resume_session_id"]
        agent = ACPAgent(**kwargs)

        info = _api(base, key, "POST", "/api/conversations", {
            "workspace": {"kind": "LocalWorkspace",
                          "working_dir": os.path.abspath(job["ws"])},
            "agent": agent.model_dump(mode="json"),
            "initial_message": {"role": "user",
                                "content": [{"type": "text",
                                             "text": job["prompt"]}]},
        }, timeout=120)
        cid = info.get("id") or info.get("conversation_id")
        if not cid:
            envelope["error"] = f"no conversation id: {json.dumps(info)[:200]}"
            raise SystemExit

        deadline = time.time() + float(job.get("timeout_sec", 300))
        status = "running"
        agent_state = {}
        while time.time() < deadline:
            evs = _api(base, key, "GET",
                       f"/api/conversations/{cid}/events/search?limit=100")
            items = evs.get("items", evs.get("results", []))
            with open(job["events_path"], "w") as f:
                for e in items:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
            for e in items:
                if e.get("kind") == "ConversationStateUpdateEvent":
                    if e.get("key") == "execution_status":
                        status = e.get("value", status)
                    if e.get("key") == "agent_state" \
                            and isinstance(e.get("value"), dict):
                        agent_state = e["value"]
                if e.get("kind") == "ConversationErrorEvent":
                    envelope["error"] = str(e.get("detail", ""))[:300]
            if status == "finished" or envelope["error"]:
                break
            time.sleep(2)

        envelope["completed"] = (status == "finished"
                                 and not envelope["error"])
        envelope["session_id"] = agent_state.get("acp_session_id")
        envelope["truly_resumed"] = bool(
            agent_state.get("_resumed_existing_session", False))
        try:
            ci = _api(base, key, "GET", f"/api/conversations/{cid}")
            metrics = ci.get("metrics") or {}
            envelope["cost_usd"] = float(metrics.get("accumulated_cost") or 0)
        except Exception:
            pass
    except SystemExit:
        pass
    except Exception as e:
        envelope["error"] = f"{type(e).__name__}: {e}"[:300]
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        log.close()

    with open(job["envelope_path"], "w") as f:
        json.dump(envelope, f, ensure_ascii=False)
    return 0 if envelope["completed"] else 1


if __name__ == "__main__":
    sys.exit(main())
