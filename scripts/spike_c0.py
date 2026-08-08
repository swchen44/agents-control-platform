#!/usr/bin/env python3
"""C.0 gate spike — server 端能否實例化自製 AgentBase 子類?(UC1)

起 agent-server(啟動時 import c0_stub_agent 註冊 StubRawAgent),POST 一個
StubRawAgent 的 conversation,看:
  G1 反序列化成功(不是 "Unknown kind")→ 201 + conversation id
  G2 跑通到 finished + 發出 C0_STUB_OK 訊息

PASS → C 可上 agent-server(集大成:A 級細粒度 + B+ 可視化)。
FAIL → C 走 in-process(spike 已證),B+ 收割改走 journal→detail page。

免 token(stub 不 spawn CLI)。Usage: .venv/python spike_c0.py
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

HERE = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.abspath(os.path.join(
    HERE, "..", "examples", "openhands-acp-poc", ".venv", "bin", "python"))
HOST, PORT = "127.0.0.1", 18020
BASE = f"http://{HOST}:{PORT}"
KEY = "c0-spike-key"


def api(method, path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"X-Session-API-Key": KEY,
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
        return r.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:400]}


def wait_ready(deadline=90):
    end = time.time() + deadline
    while time.time() < end:
        try:
            with urllib.request.urlopen(BASE + "/", timeout=5):
                return True
        except (urllib.error.URLError, OSError):
            time.sleep(1)
    return False


def main() -> int:
    root = os.path.join(HERE, "runtime_c0")
    ws = os.path.join(root, "ws")
    os.makedirs(ws, exist_ok=True)
    env = dict(os.environ)
    env["OH_SESSION_API_KEYS_0"] = KEY
    env["OH_PERSISTENCE_DIR"] = os.path.join(root, "persist")
    env["PYTHONPATH"] = HERE + os.pathsep + env.get("PYTHONPATH", "")
    log = open(os.path.join(root, "server.log"), "w")
    server = subprocess.Popen(
        [VENV_PY, os.path.join(HERE, "c0_server_launcher.py"), HOST, str(PORT)],
        env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    try:
        if not wait_ready():
            print("G0 server up: FAIL"); return 2
        print("G0 server up: PASS")

        # build StubRawAgent payload via the venv python (has our module + sdk)
        dump = subprocess.check_output([VENV_PY, "-c",
            "import sys; sys.path.insert(0, %r);" % HERE +
            "import c0_stub_agent, json;"
            "print(c0_stub_agent.StubRawAgent().model_dump_json())"],
            env=env, text=True).strip()
        agent = json.loads(dump)
        print("agent kind:", agent.get("kind"))

        code, info = api("POST", "/api/conversations", {
            "workspace": {"kind": "LocalWorkspace", "working_dir": ws},
            "agent": agent,
            "initial_message": {"role": "user",
                                "content": [{"type": "text", "text": "hi"}]},
        }, timeout=60)
        cid = info.get("id") or info.get("conversation_id")
        g1 = code in (200, 201) and bool(cid)
        print(f"G1 自製 agent 反序列化+建 conversation: "
              f"{'PASS' if g1 else 'FAIL'} (code={code}, "
              f"detail={info.get('error','')[:120]})")
        if not g1:
            return 3

        g2 = False
        deadline = time.time() + 30
        while time.time() < deadline:
            _, evs = api("GET",
                         f"/api/conversations/{cid}/events/search?limit=100")
            items = evs.get("items", evs.get("results", []))
            for e in items:
                if e.get("kind") == "MessageEvent":
                    c = (e.get("llm_message") or {}).get("content") or []
                    if any("C0_STUB_OK" in x.get("text", "")
                           for x in c if isinstance(x, dict)):
                        g2 = True
            if g2:
                break
            time.sleep(2)
        print(f"G2 自製 agent step() 跑通(C0_STUB_OK): {'PASS' if g2 else 'FAIL'}")
        ok = g1 and g2
        print("spike-c0:", "PASS — C 可上 agent-server(集大成)"
              if ok else "FAIL — C 走 in-process")
        return 0 if ok else 1
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        log.close()


if __name__ == "__main__":
    sys.exit(main())
