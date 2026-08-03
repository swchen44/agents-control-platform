"""Harness-side inner runner: spawn the venv runner, classify raw outcome.

Three-state classification (v5 D3, measured basis in research v3 §9.3):
  completed  runner wrote an envelope with completed=true
  error      runner wrote an envelope with an error (retryable — evidence of
             a clean failure, e.g. quota ConversationErrorEvent)
  unknown    runner died / timed out / left no envelope — side effects CANNOT
             be proven either way; never auto-retried

Exit codes are deliberately ignored for classification: rc=0 proves nothing
(codex SIGTERM lesson) and rc!=0 with a good envelope is still evidence.
Only the envelope counts.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# backend → venv runner script (same job/envelope contract for all)
RUNNERS = {
    "openhands-acp": os.path.join(HERE, "inner_acp_runner.py"),
    "openhands-server": os.path.join(HERE, "inner_agentserver_runner.py"),
    "rawcli": os.path.join(HERE, "inner_rawcli_runner.py"),  # route C
}


@dataclass
class AttemptResult:
    raw_outcome: str               # completed | error | unknown
    session_id: str | None
    truly_resumed: bool
    cost_usd: float | None
    error: str | None
    events_path: str
    envelope_path: str


def run_attempt(agent_cfg: dict, ws: str, prompt: str, artifacts_dir: str,
                attempt: int, resume_session_id: str | None = None
                ) -> AttemptResult:
    os.makedirs(artifacts_dir, exist_ok=True)
    backend = agent_cfg.get("backend", "openhands-acp")
    runner = RUNNERS.get(backend)
    if runner is None:
        raise ValueError(f"unknown agent backend: {backend!r} "
                         f"(known: {sorted(RUNNERS)})")
    job = {
        "ws": os.path.abspath(ws),
        "prompt": prompt,
        "acp_server": agent_cfg.get("acp_server", "claude-code"),
        "acp_server_engine": agent_cfg.get("engine", "claude"),  # rawcli
        "acp_model": agent_cfg.get("acp_model") or agent_cfg.get("model"),
        "resume_session_id": resume_session_id,
        "timeout_sec": agent_cfg.get("timeout_sec", 300),
        "events_path": os.path.join(artifacts_dir, f"a{attempt}.events.jsonl"),
        "envelope_path": os.path.join(artifacts_dir, f"a{attempt}.envelope.json"),
        # agent-server backend extras (ignored by the in-process runner)
        "server_port": int(agent_cfg.get("server_port", 18010)),
        "server_api_key": agent_cfg.get("server_api_key", "harness-local-only"),
        "persist_dir": os.path.join(artifacts_dir, f"a{attempt}.persist"),
    }
    job_path = os.path.join(artifacts_dir, f"a{attempt}.job.json")
    with open(job_path, "w") as f:
        json.dump(job, f, ensure_ascii=False)

    venv_python = os.path.abspath(
        os.path.join(HERE, agent_cfg["venv"], "bin", "python"))
    timeout = float(agent_cfg.get("timeout_sec", 300)) + 60  # server startup
    try:
        subprocess.run([venv_python, runner, job_path],
                       cwd=HERE, timeout=timeout,
                       stdin=subprocess.DEVNULL, capture_output=True)
    except subprocess.TimeoutExpired:
        pass  # classification below is envelope-driven, not rc-driven

    envelope: dict = {}
    if os.path.exists(job["envelope_path"]):
        try:
            envelope = json.load(open(job["envelope_path"]))
        except json.JSONDecodeError:
            envelope = {}
    if envelope.get("completed"):
        raw = "completed"
    elif envelope.get("error"):
        raw = "error"
    else:
        raw = "unknown"           # dead runner / no envelope → cannot prove
    return AttemptResult(
        raw_outcome=raw,
        session_id=envelope.get("session_id"),
        truly_resumed=bool(envelope.get("truly_resumed")),
        cost_usd=envelope.get("cost_usd"),
        error=envelope.get("error"),
        events_path=job["events_path"],
        envelope_path=job["envelope_path"])
