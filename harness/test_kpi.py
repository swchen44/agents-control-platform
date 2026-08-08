#!/usr/bin/env python3
"""W3.5 — C3 KPI 人力估算 單元測(pytest-compatible,亦自跑)。

涵蓋:dispatcher SUCCESS 帶 human_minutes_saved(est 有才記)、trigger 同、
saved_minutes 彙總(只算 SUCCESS 事件)、dashboard 節省人時卡 + 時薪對比
(env ARCP_HOURLY_RATE 選配)。
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from detail_server import overview_cards, saved_minutes  # noqa: E402

from arcp import dispatcher as dmod  # noqa: E402
from arcp import triggers as tmod  # noqa: E402
from arcp.dispatcher import Dispatcher  # noqa: E402
from arcp.inner_runner import AttemptResult  # noqa: E402
from arcp.profiles import Profile  # noqa: E402
from arcp.store import Store  # noqa: E402
from arcp.ticket import Ticket  # noqa: E402
from arcp.triggers import Trigger, run_trigger  # noqa: E402


class MockSource:
    def add_comment(self, iid, text):
        pass


def _profile(est=None, name="p"):
    return Profile(name=name, workspace_template="empty",
                   workspace_folder="tickets/{issue_id}", skills=[],
                   agent={"backend": "rawcli"}, verify=[], max_attempts=1,
                   on_unknown="pending", human_minutes_est=est)


def _fork_ok(agent_cfg, ws, prompt, artifacts, attempt,
             resume_session_id=None, **kw):
    return AttemptResult(raw_outcome="completed", session_id="s1",
                         truly_resumed=False, cost_usd=0.02, error=None,
                         events_path="", envelope_path="", error_kind=None)


def _ticket():
    return Ticket(id=1, key="P-1", summary="s", state="To Do",
                  assignee=None, assignee_id=None, description="d")


def test_dispatcher_success_records_kpi():
    dmod.run_attempt = _fork_ok
    root = tempfile.mkdtemp()
    d = Dispatcher(MockSource(), Store(os.path.join(root, "s")),
                   {"p": _profile(est=30)}, root=root)
    ev = d.handle(_ticket(), "p")
    resolved = [e for e in ev if e["type"] == "resolved"]
    assert resolved and resolved[0]["human_minutes_saved"] == 30


def test_dispatcher_no_est_uses_default_240():
    # W7(R3):未設 est → 預設 240 分(效益一律算得出),不再省略 key
    dmod.run_attempt = _fork_ok
    root = tempfile.mkdtemp()
    d = Dispatcher(MockSource(), Store(os.path.join(root, "s")),
                   {"p": _profile(est=None)}, root=root)
    ev = d.handle(_ticket(), "p")
    resolved = [e for e in ev if e["type"] == "resolved"]
    assert resolved and resolved[0]["human_minutes_saved"] == 240.0


def test_trigger_success_records_kpi():
    tmod.run_attempt = _fork_ok
    root = tempfile.mkdtemp()
    profs = {"maint": _profile(est=45, name="maint")}
    ev = run_trigger(Trigger("t", "maint", "job", "x", None),
                     profs, Store(os.path.join(root, "s")), root)
    fin = [e for e in ev if e["type"] == "trigger_finished"]
    assert fin and fin[0]["human_minutes_saved"] == 45


def test_saved_minutes_sums_success_only():
    journal = [
        {"type": "resolved", "human_minutes_saved": 30},
        {"type": "trigger_finished", "human_minutes_saved": 45},
        {"type": "resolved"},                          # 無 est
        {"type": "pending", "human_minutes_saved": 999},  # 非 SUCCESS 事件不算
    ]
    assert saved_minutes(journal) == 75


def test_dashboard_tiles():
    journal = [{"type": "resolved", "human_minutes_saved": 90}]
    html = overview_cards({}, journal)
    assert "節省人時" in html and "1.5h" in html
    assert "人力成本對比" not in html                  # 未設時薪不顯金額
    os.environ["ARCP_HOURLY_RATE"] = "40"
    try:
        html2 = overview_cards({}, journal)
        assert "人力成本對比" in html2 and "$60" in html2   # 1.5h × $40
    finally:
        del os.environ["ARCP_HOURLY_RATE"]
    assert "節省人時" not in overview_cards({}, [])    # 無 KPI 事件不顯卡


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
    print("test-kpi:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
