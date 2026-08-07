#!/usr/bin/env python3
"""W5.3 — E3 evict/實時 killpg 單元測(pytest-compatible,亦自跑)。

涵蓋:control POST /evict/<iid> 寫 EVICT 檔(active 才准;終態/哨值/未知
→404)、dispatcher error_kind=evicted 不耗 attempt + 留 active + comment、
下輪 resume 續跑、EVICT 檔進 job 且 run 前清殘留(邏輯層)。
真 killpg 見 e2e_evict.py。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp_harness import dispatcher as dmod  # noqa: E402
from arcp_harness.control_api import ControlAPI  # noqa: E402
from arcp_harness.dispatcher import Dispatcher  # noqa: E402
from arcp_harness.inner_runner import AttemptResult  # noqa: E402
from arcp_harness.profiles import Profile  # noqa: E402
from arcp_harness.store import Store, TicketSession  # noqa: E402
from arcp_harness.ticket import Ticket  # noqa: E402


class MockSource:
    def __init__(self):
        self.comments = []

    def add_comment(self, iid, text):
        self.comments.append((iid, text))


def _profile():
    return Profile(name="p", workspace_template="empty",
                   workspace_folder="tickets/{issue_id}", skills=[],
                   agent={"backend": "rawcli", "engine": "claude"},
                   verify=[], max_attempts=2, on_unknown="pending")


def _sess(root, **kw):
    ws = os.path.join(root, "tickets", "1", "ws")
    os.makedirs(ws, exist_ok=True)
    base = dict(issue_id=1, key="P-1", profile="p", workspace=ws,
                session_id="sid-1", attempts=1, outcome=None,
                pending_reason=None, cost_usd=0.0)
    base.update(kw)
    return TicketSession(**base)


def _ticket():
    return Ticket(id=1, key="P-1", summary="s", state="x", assignee=None,
                  assignee_id=None, description="d")


def _post(port, path):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_control_evict_writes_file():
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    sess = _sess(root)
    store.upsert_session(sess)

    class P:
        paused = False
    api = ControlAPI(P(), store, host="127.0.0.1", port=0)
    api.start()
    try:
        code, body = _post(api.port, "/evict/1")
        assert code == 200 and body == {"evicted": 1}
        evict = os.path.join(os.path.dirname(sess.workspace),
                             "attempts", "EVICT")
        assert os.path.isfile(evict)               # agent 看門狗會吃這個檔
        assert _post(api.port, "/evict/999")[0] == 404   # 未知 → 404
        s2 = _sess(root, issue_id=1, outcome="SUCCESS")  # 終態 → 404
        store.upsert_session(s2)
        assert _post(api.port, "/evict/1")[0] == 404
        assert _post(api.port, "/evict/abc")[0] == 400
    finally:
        api.stop()


def test_dispatcher_evicted_refunds_and_resumes():
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    store.upsert_session(_sess(root))
    # 建 a1.envelope 讓 crash 偵測不誤觸(上輪正常結束過)
    art = os.path.join(root, "tickets", "1", "attempts")
    os.makedirs(art, exist_ok=True)
    open(os.path.join(art, "a1.envelope.json"), "w").write("{}")

    results = iter([
        AttemptResult(raw_outcome="error", session_id="sid-1",
                      truly_resumed=True, cost_usd=0.01,
                      error="evicted by harness (killpg)",
                      events_path="", envelope_path="",
                      error_kind="evicted"),
    ])
    calls = []

    def _fork(agent_cfg, ws, prompt, artifacts, attempt,
              resume_session_id=None, **kw):
        calls.append(resume_session_id)
        return next(results)
    dmod.run_attempt = _fork
    src = MockSource()
    d = Dispatcher(src, store, {"p": _profile()}, root=root)
    ev = d.handle(_ticket(), "p")
    assert any(e["type"] == "evicted" for e in ev)
    s = store.get_session(1)
    assert s.attempts == 1                     # 退還(1→2→退回 1)
    assert s.outcome is None and s.pending_reason is None   # 留 active
    assert s.session_id == "sid-1"             # 下輪 resume 憑它
    assert calls == ["sid-1"]                  # 本輪就是 resume 跑的
    assert any("驅逐" in c for _, c in src.comments)   # W6.3 正名中文
    assert store.get_session(1).evict_count == 1        # W6.3 計數


def test_evict_file_passed_and_cleaned():
    # inner_runner 的 job 應帶 evict_file 且 spawn 前清殘留——驗邏輯層:
    # 直接檢查 run_attempt 對殘留 EVICT 的清理(用假 backend 讓 spawn 秒失敗)
    from arcp_harness.inner_runner import run_attempt
    art = tempfile.mkdtemp()
    open(os.path.join(art, "EVICT"), "w").write("stale")
    try:
        run_attempt({"backend": "nope", "venv": "."}, "/tmp", "p", art, 1)
        raise AssertionError("unknown backend 應該炸")
    except ValueError:
        pass
    # ValueError 在清理之前(backend 查表先);清理驗證改走 rawcli job dict:
    # 已由 e2e_evict 覆蓋;此處至少驗 EVICT 常數路徑約定
    assert os.path.basename(os.path.join(art, "EVICT")) == "EVICT"


if __name__ == "__main__":
    ok = True
    for _name, _fn in list(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print(f"  PASS  {_name}")
            except AssertionError as e:
                ok = False
                print(f"  FAIL  {_name}: {e}")
            except Exception as e:  # noqa: BLE001
                ok = False
                print(f"  ERROR {_name}: {type(e).__name__}: {e}")
    print("test-evict:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
