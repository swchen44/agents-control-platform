#!/usr/bin/env python3
"""W2.6 — REST 控制面 單元測(W13;pytest-compatible,亦自跑)。

真 HTTP(ephemeral port 0)打真 ThreadingHTTPServer:health、status 彙總正確、
pause/resume 切旗標、reload 生效 / 壞 config 回 400 不死、paused poller 只
watch 不派工(watch 事件照記、resume 後補派)。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp_harness.control_api import ControlAPI  # noqa: E402
from arcp_harness.poller import OuterLoop  # noqa: E402
from arcp_harness.routing import Route  # noqa: E402
from arcp_harness.store import Store, TicketSession  # noqa: E402
from arcp_harness.ticket import Ticket  # noqa: E402


class FakePoller:
    paused = False


def _sess(iid, **kw):
    base = dict(issue_id=iid, key=f"P-{iid}", profile="p", workspace="ws",
                session_id=None, attempts=0, outcome=None,
                pending_reason=None, cost_usd=0.0)
    base.update(kw)
    return TicketSession(**base)


def _api(store=None, poller=None, reload_fn=None):
    """port=0 → ephemeral;呼叫端負責 stop()。"""
    api = ControlAPI(poller or FakePoller(), store or Store(tempfile.mkdtemp()),
                     reload_fn=reload_fn, host="127.0.0.1", port=0)
    api.start()
    return api


def _get(api, path):
    with urllib.request.urlopen(
            f"http://127.0.0.1:{api.port}{path}", timeout=5) as r:
        return r.status, json.loads(r.read())


def _post(api, path):
    req = urllib.request.Request(
        f"http://127.0.0.1:{api.port}{path}", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_health():
    api = _api()
    try:
        code, body = _get(api, "/health")
        assert code == 200 and body == {"ok": True}
    finally:
        api.stop()


def test_status_aggregates():
    store = Store(tempfile.mkdtemp())
    store.upsert_session(_sess(1, session_id="s1", attempts=1))  # in-flight
    store.upsert_session(_sess(2, queued=True))                  # queued
    store.upsert_session(_sess(3, outcome="SUCCESS", cost_usd=1.25))
    store.upsert_session(_sess(4, pending_reason="approval"))
    store.upsert_session(_sess(5, inactive=True, cost_usd=0.5))
    api = _api(store=store)
    try:
        code, st = _get(api, "/status")
        assert code == 200
        assert st["paused"] is False
        assert st["in_flight"] == 1          # 只有 1 是 active(W8 語意)
        assert st["queued"] == 1
        assert st["inactive"] == 1
        assert st["sessions"] == 5
        assert st["outcomes"] == {"SUCCESS": 1}
        assert st["pending"] == {"approval": 1}
        assert abs(st["cost_usd"] - 1.75) < 1e-9
    finally:
        api.stop()


def test_pause_resume():
    p = FakePoller()
    api = _api(poller=p)
    try:
        code, body = _post(api, "/pause")
        assert code == 200 and body["paused"] is True and p.paused is True
        _, st = _get(api, "/status")
        assert st["paused"] is True
        code, body = _post(api, "/resume")
        assert code == 200 and body["paused"] is False and p.paused is False
    finally:
        api.stop()


def test_reload_ok_and_error():
    calls = []

    def _fn():
        calls.append(1)
        if len(calls) > 1:
            raise ValueError("壞 config")
        return {"routes": 2, "profiles": 3}

    api = _api(reload_fn=_fn)
    try:
        code, body = _post(api, "/reload")
        assert code == 200
        assert body["reloaded"] is True and body["routes"] == 2
        code, body = _post(api, "/reload")     # 第二次:壞 config → 400 不死
        assert code == 400 and "壞 config" in body["error"]
        code, _ = _get(api, "/health")         # server 還活著
        assert code == 200
    finally:
        api.stop()


def test_shutdown_sets_stopping():
    p = FakePoller()
    api = _api(poller=p)
    try:
        code, body = _post(api, "/shutdown")
        assert code == 200 and body["stopping"] is True
        assert p.stopping is True                  # W4.5 graceful shutdown
        _, st = _get(api, "/status")
        assert st["stopping"] is True
    finally:
        api.stop()


def test_gen_transcript_manual():
    """W6.4 被動按鈕:POST /gen_transcript/<id> → finalize(reason=manual)。
    monkeypatch transcript.finalize 避免真渲染;驗查 engine、記 journal、404 分支。"""
    from arcp_harness import transcript as tmod
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    ws = os.path.join(root, "tickets", "1", "ws")
    os.makedirs(ws, exist_ok=True)
    store.upsert_session(_sess(1, session_id="sid-1", workspace=ws))
    store.upsert_session(_sess(2, session_id=None, workspace=ws))   # 無 sid
    store.upsert_session(_sess(3, session_id="s3", workspace="(handoff)"))

    class _Prof:
        agent = {"backend": "rawcli", "engine": "codex"}
    seen = {}
    _orig = tmod.finalize

    def _fake_finalize(sid, engine, workspace, pack=False, reason=""):
        seen.update(sid=sid, engine=engine, reason=reason, pack=pack)
        return ["final.html"]
    tmod.finalize = _fake_finalize
    api = _api(store=store)
    api.profiles_fn = lambda: {"p": _Prof()}
    try:
        code, body = _post(api, "/gen_transcript/1")
        assert code == 200 and body["generated"] == 1 and body["files"] == 1
        assert seen == {"sid": "sid-1", "engine": "codex",
                        "reason": "manual", "pack": False}
        jrn = [json.loads(x) for x in open(store.journal_path) if x.strip()]
        assert any(e["type"] == "transcript_packed" and e["reason"] == "manual"
                   for e in jrn)
        assert _post(api, "/gen_transcript/2")[0] == 404   # 無 session_id
        assert _post(api, "/gen_transcript/3")[0] == 404   # 哨值 workspace
        assert _post(api, "/gen_transcript/999")[0] == 404  # 無此票
        assert _post(api, "/gen_transcript/abc")[0] == 400  # 非數字
    finally:
        tmod.finalize = _orig
        api.stop()


def test_unknown_paths_404():
    api = _api()
    try:
        try:
            _get(api, "/nope")
            raise AssertionError("應該 404")
        except urllib.error.HTTPError as e:
            assert e.code == 404
        code, _ = _post(api, "/nope")
        assert code == 404
    finally:
        api.stop()


# -- paused poller:只 watch 不派工 ----------------------------------------- #
class FakeSource:
    def __init__(self, tickets):
        self._t = tickets

    def search(self, jql, max_results=50):
        return self._t

    def get_comments(self, iid):
        return []


class FakeDispatcher:
    def __init__(self):
        self.profiles = {}
        self.handled = []

    def handle(self, t, prof):
        self.handled.append(t.id)
        return []


def test_paused_poller_skips_dispatch():
    tickets = [Ticket(id=1, key="P-1", summary="s", state="To Do",
                      assignee=None, assignee_id=None, labels=["go"])]
    routes = [Route(name="r", when={"labels": ["go"]}, profile="p",
                    on_match="create_or_resume")]
    disp = FakeDispatcher()
    loop = OuterLoop(FakeSource(tickets), Store(tempfile.mkdtemp()), routes,
                     "jql", dispatcher=disp,
                     concurrency={"max_running": 4, "per_engine": {},
                                  "per_profile": {}})
    loop.paused = True
    ev = loop.poll_once()
    assert disp.handled == []                          # 不派新工
    assert any(e["type"] == "new_issue" for e in ev)   # watch 照記
    loop.paused = False
    loop.poll_once()                                   # resume 後補派
    assert disp.handled == [1]


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
    print("test-control-api:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
