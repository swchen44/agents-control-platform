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
import threading
import time
from dataclasses import dataclass

from .contract import CONTRACT_SCHEMA
from .isolation import resolve as resolve_isolation
from .logutil import get_logger
from .paths import find_script, repo_root

log = get_logger("attempt")

# heartbeat:attempt 執行中每 30s 旁路 stat 輸出檔印一行 content-free 進度
# (bytes/行數/事件型別/idle 秒數;絕不落 prompt 或模型輸出內容)。旁路唯讀,
# 不動 child 的 stdio 接法(避免 buffering/backpressure 改變 headless 語意)。
_HB_INTERVAL_S = 30.0

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
    error_kind: str | None = None  # infra | stalled | task | no-terminal | timeout
    structured: dict | None = None  # G1 agent {reason,status,next}
    tokens: int | None = None      # budget:本 attempt 用的 token(input+output+cache)
    timeout_kind: str | None = None  # no_output_timeout | stalled_output_timeout
    progress: dict | None = None   # content-free 進度診斷(非健康 verdict)


def _tail_last_event(events_path: str) -> tuple[int, str]:
    """回 (事件行數, 最後事件 category)。content-free:只讀 metadata 欄。"""
    n, last = 0, ""
    try:
        with open(events_path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                n += 1
                tail = line
        if n:
            last = str(json.loads(tail).get("category") or "")
    except (OSError, json.JSONDecodeError, UnboundLocalError):
        pass
    return n, last


def _timeout_kind_from_disk(raw_path: str) -> str:
    """harness timeout(runner 被殺、無 envelope)時從磁碟判輸出樣態:
    raw 流有內容=有輸出後停滯;沒有=從頭零輸出(啟動/認證問題方向)。"""
    try:
        has_output = os.path.getsize(raw_path) > 0
    except OSError:
        has_output = False
    return "stalled_output_timeout" if has_output else "no_output_timeout"


def _heartbeat(stop: threading.Event, label: str,
               raw_path: str, events_path: str) -> None:
    """旁路唯讀監看輸出檔,每 _HB_INTERVAL_S 印一行 content-free 進度。
    僅供人看 log 診斷「還有沒有在動」——不是健康 verdict,不據此 kill。"""
    t0 = time.time()
    prev = -1
    while not stop.wait(_HB_INTERVAL_S):
        try:
            sz = os.path.getsize(raw_path)
        except OSError:
            sz = 0
        try:
            idle = time.time() - os.path.getmtime(raw_path)
        except OSError:
            idle = time.time() - t0
        n, last = _tail_last_event(events_path)
        log.info("[hb] %s raw=%dB(%+d) events=%d last=%s idle=%.0fs "
                 "elapsed=%.0fs", label, sz, sz - max(prev, 0), n,
                 last or "-", idle, time.time() - t0)
        prev = sz


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
    raw_path = job["events_path"].replace(".events.jsonl", ".raw.jsonl")
    label = (f"{os.path.basename(os.path.dirname(artifacts_dir))}"
             f" a{attempt}")
    hb_stop = threading.Event()
    threading.Thread(target=_heartbeat, daemon=True,
                     args=(hb_stop, label, raw_path,
                           job["events_path"])).start()
    timed_out = False
    try:
        subprocess.run([venv_python, runner, job_path],
                       cwd=HERE, timeout=timeout,
                       stdin=subprocess.DEVNULL, capture_output=True)
    except subprocess.TimeoutExpired:
        timed_out = True  # classification below is envelope-driven, not rc-driven
    finally:
        hb_stop.set()

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
    timeout_kind = envelope.get("timeout_kind")   # stall 時 runner 已分類
    if raw == "unknown" and timed_out and not error_kind:
        error_kind = "timeout"
        timeout_kind = _timeout_kind_from_disk(raw_path)
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
        tokens=envelope.get("tokens"),
        timeout_kind=timeout_kind,
        progress=envelope.get("progress"))
