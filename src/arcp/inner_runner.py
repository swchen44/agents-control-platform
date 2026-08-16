"""Harness-side inner runner: spawn the venv runner, classify raw outcome.

Three-state classification (v5 D3, measured basis in research v3 §9.3):
  completed  runner wrote an envelope with completed=true
  error      runner wrote an envelope with an error (retryable — evidence of
             a clean failure, e.g. quota ConversationErrorEvent)
  unknown    runner died / timed out / left no envelope — side effects CANNOT
             be proven either way; never auto-retried. Exception: when the
             harness itself killed the runner (timeout_sec) the cause IS
             provable → error_kind="timeout", and the dispatcher may re-run
             up to timeout_retry_max times (default 0 = keep v5 D3 behavior)

Exit codes are deliberately ignored for classification: rc=0 proves nothing
(codex SIGTERM lesson) and rc!=0 with a good envelope is still evidence.
Only the envelope counts.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass

from .contract import CONTRACT_SCHEMA
from .isolation import resolve as resolve_isolation
from .paths import find_script, repo_root

# runner 執行的工作區基準(venv 相對路徑 + subprocess cwd)= repo root。
# 由 arcp.paths 定位,搬檔不破(W12.1 曾因 dirname² 指到 src/ 找不到 runner)。
HERE = repo_root() or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# backend → runner script(同 job/envelope 契約)。以 arcp.paths 解析到 scripts/,
# 不綁本檔位置。W12.1 舊寫法 join(dirname²(__file__), ...) 會指到 src/ 找不到 runner。
RUNNERS = {
    "openhands-acp": find_script("inner_acp_runner.py"),
    "openhands-server": find_script("inner_agentserver_runner.py"),
    "rawcli": find_script("inner_rawcli_runner.py"),  # route C
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
    error_kind: str | None = None  # infra | stalled | task | no-terminal (N3)
    structured: dict | None = None  # G1 agent {reason,status,next}
    tokens: int | None = None      # budget:本 attempt 用的 token(input+output+cache)


def run_attempt(agent_cfg: dict, ws: str, prompt: str, artifacts_dir: str,
                attempt: int, resume_session_id: str | None = None,
                preassigned_session_id: str | None = None) -> AttemptResult:
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
        # 執行檔覆寫(內網包裝別名/絕對路徑,如 /tools/bin/claudeoss)+
        # 彈性附加參數(原樣接在 command line 最後)——rawcli
        "command": agent_cfg.get("command"),
        "extra_args": list(agent_cfg.get("extra_args") or []),
        # W3.6(D1):isolation.provider 解析(auto→依 OS;未實作→none+警告);
        # 舊寫法 os_sandbox: true == provider auto。runner 端欄位名不變。
        "os_sandbox": resolve_isolation(agent_cfg) == "seatbelt",
        "sandbox": agent_cfg.get("sandbox", "workspace-write"),  # codex 內建
        # rawcli N13 stall watchdog。預設 3600(2026-08-13 定案;原 0=停用):
        # 卡死一小時自動 killpg→下輪 resume。⚠️ 自訂必須 > 該 profile 最長
        # 單一前景命令時間——長 build 期間事件流完全靜默(R4 實測),設太短
        # 會誤殺好好等 build 的 agent;0=停用;硬上限交給 timeout_sec。
        "stall_seconds": agent_cfg.get("stall_seconds", 3600),
        "output_schema": (CONTRACT_SCHEMA                        # G1 契約(rawcli)
                          if agent_cfg.get("output_schema") else None),
        "server_managed": agent_cfg.get("server_managed", False),  # conc.3
        "resume_session_id": resume_session_id,
        # W5.1 sid 預派(W29):claude --session-id 可指定;crash 後憑它 resume
        "preassigned_session_id": preassigned_session_id,
        # W5.3 E3:control API 寫此檔 → agent 即刻 killpg(實時釋放資源)
        "evict_file": os.path.join(artifacts_dir, "EVICT"),
        "timeout_sec": agent_cfg.get("timeout_sec", 300),
        "events_path": os.path.join(artifacts_dir, f"a{attempt}.events.jsonl"),
        "envelope_path": os.path.join(artifacts_dir, f"a{attempt}.envelope.json"),
        # agent-server backend extras (ignored by the in-process runner)
        "server_port": int(agent_cfg.get("server_port", 18010)),
        "server_api_key": agent_cfg.get("server_api_key", "harness-local-only"),
        "persist_dir": os.path.join(artifacts_dir, f"a{attempt}.persist"),
    }
    # E3/T10 修(2026-08-13):EVICT 檔改由 dispatcher 於 attempt 結束後統一
    # 清理——起跑就刪會把「hold 剛寫、搶在 spawn 前後幾秒」的現役驅逐標記
    # 一併洗掉(race,T10 三輪實測),讓 hold 的 killpg 完全撲空。殘留檔
    # 不再於此清:見 dispatcher run_attempt 返回後的 _clear_evict。
    job_path = os.path.join(artifacts_dir, f"a{attempt}.job.json")
    with open(job_path, "w") as f:
        json.dump(job, f, ensure_ascii=False)

    # W5.5:rawcli 純 stdlib → venv 選配,省略即用系統 python(免 591MB
    # openhands venv);openhands-acp/server 仍需 venv(SDK 在裡面)。
    venv = agent_cfg.get("venv")
    venv_python = (os.path.abspath(os.path.join(HERE, venv, "bin", "python"))
                   if venv else sys.executable)
    timeout = float(agent_cfg.get("timeout_sec", 300)) + 60  # server startup
    timed_out = False
    try:
        subprocess.run([venv_python, runner, job_path],
                       cwd=HERE, timeout=timeout,
                       stdin=subprocess.DEVNULL, capture_output=True)
    except subprocess.TimeoutExpired:
        timed_out = True  # classification below is envelope-driven, not rc-driven

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
    # timeout 是 harness 自己殺的(timeout_sec 到期),原因可證——與真 unknown
    # (行程自己消失)不同,標 error_kind=timeout 讓 dispatcher 依
    # timeout_retry_max 決定是否重跑(仍不能證明副作用,故 raw 維持 unknown)。
    error_kind = envelope.get("error_kind")
    if raw == "unknown" and timed_out and not error_kind:
        error_kind = "timeout"
    return AttemptResult(
        raw_outcome=raw,
        session_id=envelope.get("session_id"),
        truly_resumed=bool(envelope.get("truly_resumed")),
        cost_usd=envelope.get("cost_usd"),
        error=envelope.get("error"),
        events_path=job["events_path"],
        envelope_path=job["envelope_path"],
        error_kind=error_kind,
        structured=envelope.get("structured"),
        tokens=envelope.get("tokens"))
