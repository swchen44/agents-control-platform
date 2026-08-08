#!/usr/bin/env python3
"""多票並發 demo — 一個 agent-server 進程同時管 N 個 conversation。

這是 OpenHands agent-server 相對 in-process/rawcli 的核心價值:
  in-process/rawcli:N 張票 = N 個獨立 CLI 子進程,無統一生命週期管理。
  agent-server   :N 張票 = 1 個 server 進程管 N 個 conversation(各自
                  workspace + 事件流),原生併發、閒置 evict→rehydrate。

demo:起 1 個長駐 server → 並發 POST N 個 filechain conversation(不同 ws)→
輪詢各自 events 到 finished → 量化:1 個 server PID 管 N 個、並發 wall-clock、
各 ws grader 過。

Usage: caffeinate -i .venv/python demo_concurrent.py [N]  (預設 4,haiku,~$0.12)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "examples", "jira-agent-poc"))
from arcp_poc.grader import FileChecklistGrader  # noqa: E402

HOST, PORT = "127.0.0.1", 18030
BASE = f"http://{HOST}:{PORT}"
KEY = "concurrent-demo-key"
ROOT = os.path.join(HERE, "runtime_concurrent")
EXPECTED = {f"step{n}.txt": "".join(str(i) for i in range(1, n + 1))
            for n in range(1, 4)}
PROMPT = ("在目前工作目錄依序建立三個檔案:step1.txt 內容 1、step2.txt 內容 12、"
          "step3.txt 內容 123。嚴格依序,內容不含引號空白。完成回覆 TASK_DONE。")


def api(method, path, body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"X-Session-API-Key": KEY,
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw.strip() else {}


def wait_ready(deadline=90):
    end = time.time() + deadline
    while time.time() < end:
        try:
            with urllib.request.urlopen(BASE + "/", timeout=5):
                return True
        except (urllib.error.URLError, OSError):
            time.sleep(1)
    return False


def run_conversation(idx: int, agent_json: dict, results: dict):
    ws = os.path.join(ROOT, f"ws{idx}")
    os.makedirs(ws, exist_ok=True)
    t0 = time.time()
    info = api("POST", "/api/conversations", {
        "workspace": {"kind": "LocalWorkspace", "working_dir": ws},
        "agent": agent_json,
        "initial_message": {"role": "user",
                            "content": [{"type": "text", "text": PROMPT}]},
    })
    cid = info.get("id") or info.get("conversation_id")
    status = "running"
    deadline = time.time() + 240
    while time.time() < deadline:
        evs = api("GET", f"/api/conversations/{cid}/events/search?limit=100")
        for e in evs.get("items", evs.get("results", [])):
            if (e.get("kind") == "ConversationStateUpdateEvent"
                    and e.get("key") == "execution_status"):
                status = e.get("value", status)
        if status == "finished":
            break
        time.sleep(2)
    results[idx] = {
        "cid": cid, "status": status, "wall_s": round(time.time() - t0, 1),
        "grader": FileChecklistGrader(EXPECTED).grade(ws).passed}


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    shutil.rmtree(ROOT, ignore_errors=True)
    os.makedirs(ROOT)
    env = dict(os.environ)
    env["OH_SESSION_API_KEYS_0"] = KEY
    env["OH_PERSISTENCE_DIR"] = os.path.join(ROOT, "persist")
    env["OPENHANDS_SUPPRESS_BANNER"] = "1"
    log = open(os.path.join(ROOT, "server.log"), "w")
    server = subprocess.Popen(
        [sys.executable, "-m", "openhands.agent_server",
         "--host", HOST, "--port", str(PORT)],
        env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    try:
        if not wait_ready():
            print("server up: FAIL"); return 2
        print(f"1 個 agent-server 起來了 (PID {server.pid})", flush=True)

        from openhands.sdk.agent import ACPAgent
        from openhands.sdk.settings.acp_providers import ACP_PROVIDERS
        agent_json = ACPAgent(
            acp_command=list(ACP_PROVIDERS["claude-code"].default_command),
            acp_model="haiku").model_dump(mode="json")

        results: dict = {}
        t0 = time.time()
        threads = [threading.Thread(target=run_conversation,
                                    args=(i, agent_json, results))
                   for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        wall = round(time.time() - t0, 1)

        done = sum(1 for r in results.values() if r["status"] == "finished")
        graded = sum(1 for r in results.values() if r["grader"])
        print(f"\n{n} 個 conversation 並發(同一個 server PID {server.pid}):")
        for i in sorted(results):
            r = results[i]
            print(f"  ws{i}: {r['status']:9} grader={r['grader']} "
                  f"wall={r['wall_s']}s cid={r['cid'][:8]}")
        slowest = max(r["wall_s"] for r in results.values())
        print(f"\n總並發 wall-clock: {wall}s(最慢單張 {slowest}s)"
              f" → 並發 ≈ 單張,非 {n}× 串行")
        print(f"完成 {done}/{n}、grader 過 {graded}/{n}")
        print("demo-concurrent:", "PASS" if done == n and graded == n
              else "FAIL")
        return 0 if done == n and graded == n else 1
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        log.close()


if __name__ == "__main__":
    sys.exit(main())
