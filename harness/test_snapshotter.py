#!/usr/bin/env python3
"""W4.3 — 快照器 單元測(pytest-compatible,亦自跑)。

涵蓋:interval tick 觸發 snapshot(只掃有 sid 的 active)、哨值 workspace 跳過、
thread start/stop 乾淨、離手事件(dispatcher handoff / commands next /
assignee 交人)呼 finalize(pack=False)。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp_harness import commands as cmod  # noqa: E402
from arcp_harness import dispatcher as dmod  # noqa: E402
from arcp_harness import snapshotter as smod  # noqa: E402
from arcp_harness.commands import ExternalChangePolicy  # noqa: E402
from arcp_harness.dispatcher import Dispatcher  # noqa: E402
from arcp_harness.inner_runner import AttemptResult  # noqa: E402
from arcp_harness.profiles import Profile  # noqa: E402
from arcp_harness.snapshotter import Snapshotter  # noqa: E402
from arcp_harness.store import Store, TicketSession  # noqa: E402
from arcp_harness.ticket import Ticket  # noqa: E402

BOT = "BOT"


def _profile(name="p", engine="claude"):
    return Profile(name=name, workspace_template="empty",
                   workspace_folder="tickets/{issue_id}", skills=[],
                   agent={"backend": "rawcli", "engine": engine}, verify=[],
                   max_attempts=2, on_unknown="pending")


def _sess(iid=1, **kw):
    base = dict(issue_id=iid, key=f"P-{iid}", profile="p", workspace="/tmp/x/ws",
                session_id="sid-1", attempts=1, outcome=None,
                pending_reason=None, cost_usd=0.0)
    base.update(kw)
    return TicketSession(**base)


def test_tick_snapshots_active_with_sid():
    store = Store(tempfile.mkdtemp())
    store.upsert_session(_sess(1))                       # active + sid
    store.upsert_session(_sess(2, session_id=None))      # 無 sid → 跳過
    store.upsert_session(_sess(3, workspace="(handoff)"))  # 哨值 → 跳過
    store.upsert_session(_sess(4, outcome="SUCCESS"))    # 終態 → 不掃
    calls = []
    smod.snapshot = lambda sid, eng, ws: calls.append((sid, eng, ws)) or ["x"]
    snap = Snapshotter(store, lambda: {"p": _profile(engine="codex")},
                       interval_sec=1)
    n = snap._tick()
    assert n == 1 and calls == [("sid-1", "codex", "/tmp/x/ws")]


def test_thread_interval_and_stop():
    store = Store(tempfile.mkdtemp())
    store.upsert_session(_sess(1))
    calls = []
    smod.snapshot = lambda sid, eng, ws: calls.append(time.time()) or ["x"]
    snap = Snapshotter(store, lambda: {"p": _profile()}, interval_sec=1)
    snap.interval = 0.1                                  # 測試加速
    snap.start()
    time.sleep(0.45)
    snap.stop()
    assert len(calls) >= 2                               # 多輪 tick
    n = len(calls)
    time.sleep(0.25)
    assert len(calls) == n                               # stop 後不再 tick


def _record_finalize(module):
    calls = []

    def _f(sid, engine, ws, pack=False):
        calls.append({"sid": sid, "engine": engine, "ws": ws, "pack": pack})
        return []
    module.finalize_transcript = _f
    return calls


def test_dispatcher_handoff_finalizes():
    calls = _record_finalize(dmod)
    dmod.run_attempt = lambda *a, **k: AttemptResult(
        raw_outcome="completed", session_id="sid-9", truly_resumed=False,
        cost_usd=0.0, error=None, events_path="", envelope_path="",
        error_kind=None, structured={"reason": "r", "status": "handoff",
                                     "next": {"to": "q", "kind": "agent"}})

    class Src:
        def add_comment(self, *a):
            pass

    root = tempfile.mkdtemp()
    d = Dispatcher(Src(), Store(os.path.join(root, "s")),
                   {"p": _profile("p"), "q": _profile("q")}, root=root)
    d.handle(Ticket(id=1, key="P-1", summary="s", state="x", assignee=None,
                    assignee_id=None, description="d"), "p")
    assert calls and calls[0]["sid"] == "sid-9"          # 換手前定格舊 session
    assert calls[0]["pack"] is False


def test_commands_next_finalizes():
    calls = _record_finalize(cmod)
    store = Store(tempfile.mkdtemp())
    store.upsert_session(_sess(1))
    from arcp_harness.commands import CommandHandler
    from arcp_harness.ticket import Comment
    h = CommandHandler(type("S", (), {"add_comment": lambda *a: None})(),
                       store, ["Boss"], profiles={"p": _profile(),
                                                  "q": _profile("q")})
    h.handle(Ticket(id=1, key="P-1", summary="s", state="x", assignee=None,
                    assignee_id=None, description=""),
             Comment(id=9, author="Boss", author_id="b", body="@agent next q",
                     created="t"))
    assert calls and calls[0]["sid"] == "sid-1" and calls[0]["pack"] is False


def test_inactive_finalizes():
    calls = _record_finalize(cmod)
    store = Store(tempfile.mkdtemp())
    store.upsert_session(_sess(1))
    pol = ExternalChangePolicy(
        type("S", (), {"add_comment": lambda *a: None})(), store, ["Done"],
        bot_account_id=BOT, profiles={"p": _profile(engine="codex")})
    pol.on_assignee_changed(Ticket(id=1, key="P-1", summary="s", state="x",
                                   assignee="h", assignee_id="HUMAN",
                                   description=""))
    assert calls and calls[0]["engine"] == "codex"
    assert calls[0]["pack"] is False


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
    print("test-snapshotter:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
