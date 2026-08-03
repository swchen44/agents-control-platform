#!/usr/bin/env python3
"""B+.0 spike — 消滅前置未知數 U1-U3(agent-server 起得來 + REST 建 conversation)。

自帶啟停 agent-server(本地 SDK,不走 uvx),用 API key 認證,REST 建一個
ACP conversation 跑 trivial 任務,輪詢 events 判終止,對映到 in-process 的
envelope 欄位。GUI(U4)留 B+.2。

Usage: .venv/bin/python spike_agentserver.py   (live,haiku,~$0.05)
Artifacts: runtime_spike_server/{server.log, events.jsonl, spike_result.json}
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")

HOST, PORT = "127.0.0.1", 18010
BASE = f"http://{HOST}:{PORT}"
API_KEY = "spike-key-local-only"
ROOT = os.path.abspath("./runtime_spike_server")


def api(method: str, path: str, body: dict | None = None,
        timeout: float = 30.0) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"X-Session-API-Key": API_KEY,
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw) if raw.strip() else {}


def wait_ready(deadline_s: float = 120) -> bool:
    end = time.time() + deadline_s
    while time.time() < end:
        try:
            with urllib.request.urlopen(BASE + "/", timeout=5):
                return True
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(1.0)
    return False


def main() -> int:
    shutil.rmtree(ROOT, ignore_errors=True)
    ws = os.path.join(ROOT, "ws")
    os.makedirs(ws)

    env = dict(os.environ)
    env["OH_SESSION_API_KEYS_0"] = API_KEY
    env["OH_PERSISTENCE_DIR"] = os.path.join(ROOT, "persist")
    env["OPENHANDS_SUPPRESS_BANNER"] = "1"
    log = open(os.path.join(ROOT, "server.log"), "w")
    server = subprocess.Popen(
        [sys.executable, "-m", "openhands.agent_server",
         "--host", HOST, "--port", str(PORT)],
        env=env, stdout=log, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL)

    result: dict = {"U1_server_up": False, "U2_conversation_created": False,
                    "U3_reached_terminal": False, "session_id": None,
                    "cost_usd": None, "event_kinds": {}}
    try:
        result["U1_server_up"] = wait_ready()
        print(f"U1 server up: {'PASS' if result['U1_server_up'] else 'FAIL'}")
        if not result["U1_server_up"]:
            return 2

        from openhands.sdk.agent import ACPAgent
        from openhands.sdk.settings.acp_providers import ACP_PROVIDERS
        agent = ACPAgent(
            acp_command=list(ACP_PROVIDERS["claude-code"].default_command),
            acp_model="haiku")

        payload = {
            "workspace": {"kind": "LocalWorkspace", "working_dir": ws},
            "agent": agent.model_dump(mode="json"),
            "initial_message": {"role": "user", "content": [{
                "type": "text",
                "text": "用 Write 工具建立 hello.txt,內容 pong。完成回覆 DONE。"}]},
        }
        info = api("POST", "/api/conversations", payload, timeout=120)
        cid = info.get("id") or info.get("conversation_id")
        result["U2_conversation_created"] = bool(cid)
        print(f"U2 conversation created: "
              f"{'PASS' if cid else 'FAIL'} (id={cid})")
        if not cid:
            print("info:", json.dumps(info)[:300]); return 3

        # 輪詢 events 到 workspace 出現產出檔 + agent 停止(spike 判準從簡)
        from collections import Counter
        hist: Counter = Counter()
        deadline = time.time() + 180
        done = False
        while time.time() < deadline:
            evs = api("GET", f"/api/conversations/{cid}/events/search?limit=100")
            items = evs.get("items", evs.get("results", []))
            hist = Counter(e.get("kind", "?") for e in items)
            with open(os.path.join(ROOT, "events.jsonl"), "w") as f:
                for e in items:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
            probe = os.path.join(ws, "hello.txt")
            if os.path.isfile(probe) and open(probe).read().strip() == "pong":
                done = True
                break
            time.sleep(3)
        result["U3_reached_terminal"] = done
        result["event_kinds"] = dict(hist)
        print(f"U3 file probe + events: {'PASS' if done else 'FAIL'} "
              f"(event kinds: {dict(hist)})")
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        log.close()

    with open(os.path.join(ROOT, "spike_result.json"), "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    ok = all([result["U1_server_up"], result["U2_conversation_created"],
              result["U3_reached_terminal"]])
    print("spike-agentserver:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
