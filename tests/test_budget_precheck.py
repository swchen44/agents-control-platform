#!/usr/bin/env python3
"""budget spawn 前預檢(6 限:per-ticket soft/hard × token/usd + 月/agent + 全站)。

涵蓋:store 月/global 計數(cost+tokens、跨月/跨 profile)、dispatcher 預檢
(ticket-soft/hard、token 維度、session soft 覆寫、月上限、全站上限、per-engine
usd 未量到不誤卡)、attempt_finished 帶 cost+tokens。pytest-compatible。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp import dispatcher as dmod  # noqa: E402
from arcp.dispatcher import Dispatcher  # noqa: E402
from arcp.inner_runner import AttemptResult  # noqa: E402
from arcp.profiles import Profile  # noqa: E402
from arcp.store import Store, TicketSession  # noqa: E402
from arcp.ticket import Ticket  # noqa: E402


class MockSource:
    def __init__(self):
        self.comments = []

    def add_comment(self, iid, text):
        self.comments.append((iid, text))


def _profile(name="p", **bud):
    return Profile(name=name, workspace_template="empty",
                   workspace_folder="tickets/{issue_id}", skills=[],
                   agent={"backend": "rawcli", "engine": "claude"},
                   verify=[], max_attempts=3, on_unknown="pending", **bud)


def _sess(root, **kw):
    ws = os.path.join(root, "tickets", "1", "ws")
    os.makedirs(ws, exist_ok=True)
    base = dict(issue_id=1, key="P-1", profile="p", workspace=ws,
                session_id="sid-1", attempts=0, outcome=None,
                pending_reason=None, cost_usd=0.0)
    base.update(kw)
    return TicketSession(**base)


def _ticket():
    return Ticket(id=1, key="P-1", summary="s", state="x", assignee=None,
                  assignee_id=None, description="d")


def _fork(cost=0.1, tokens=100):
    def _f(agent_cfg, ws, prompt, artifacts, attempt, resume_session_id=None,
           preassigned_session_id=None, **kw):
        return AttemptResult(raw_outcome="completed", session_id="sid-1",
                             truly_resumed=False, cost_usd=cost, error=None,
                             events_path="", envelope_path="", error_kind=None,
                             tokens=tokens)
    return _f


def _disp(root, profile, global_budget=None):
    store = Store(os.path.join(root, "s"))
    d = Dispatcher(MockSource(), store, {"p": profile}, root=root,
                   global_budget=global_budget)
    return store, d


def _scope(ev):
    return [e.get("scope") for e in ev if e["type"] == "pending"]


# -- store 月/global 計數(cost + tokens)---------------------------------- #
def test_monthly_and_global_counters():
    st = Store(tempfile.mkdtemp())
    now = 1_700_000_000.0
    last_month = now - 40 * 86400
    with open(st.journal_path, "w") as f:
        for rec in [
            {"type": "attempt_finished", "ts": now, "cost": 1.0,
             "tokens": 100, "profile": "p"},
            {"type": "attempt_finished", "ts": now, "cost": 2.0,
             "tokens": 200, "profile": "p"},
            {"type": "attempt_finished", "ts": now, "cost": 9.0,
             "tokens": 900, "profile": "q"},
            {"type": "attempt_finished", "ts": last_month, "cost": 5.0,
             "tokens": 500, "profile": "p"},
        ]:
            f.write(json.dumps(rec) + "\n")
    assert st.monthly_cost("p", now=now) == 3.0
    assert st.monthly_tokens("p", now=now) == 300
    assert st.global_monthly_cost(now=now) == 12.0       # p3 + q9
    assert st.global_monthly_tokens(now=now) == 1200
    assert st.monthly_cost("p", now=last_month) == 5.0   # 上月只那筆
    st.close()


# -- per-ticket soft / hard(usd)------------------------------------------- #
def test_ticket_soft_usd_blocks():
    root = tempfile.mkdtemp()
    store, d = _disp(root, _profile(ticket_soft_usd=1.0, ticket_hard_usd=5.0))
    store.upsert_session(_sess(root, cost_usd=1.0))       # 已達 soft
    dmod.run_attempt = _fork()
    ev = d.handle(_ticket(), "p")
    assert store.get_session(1).pending_reason == "budget"
    assert "ticket-soft" in _scope(ev)


def test_ticket_hard_usd_blocks():
    root = tempfile.mkdtemp()
    store, d = _disp(root, _profile(ticket_soft_usd=1.0, ticket_hard_usd=3.0))
    store.upsert_session(_sess(root, cost_usd=3.0))       # 達 hard
    dmod.run_attempt = _fork()
    ev = d.handle(_ticket(), "p")
    assert "ticket-hard" in _scope(ev)                    # hard 優先於 soft


def test_session_soft_overrides_profile():
    root = tempfile.mkdtemp()
    store, d = _disp(root, _profile(ticket_soft_usd=1.0, ticket_hard_usd=10.0))
    # 使用者已把本票 soft 調高到 5.0 → cost 1.0 不再卡、會 spawn
    store.upsert_session(_sess(root, cost_usd=1.0, soft_usd=5.0))
    dmod.run_attempt = _fork()
    ev = d.handle(_ticket(), "p")
    assert store.get_session(1).pending_reason != "budget"
    assert any(e["type"] == "attempt_started" for e in ev)


# -- token 維度 + per-engine(usd 未量到不誤卡)---------------------------- #
def test_token_soft_blocks_and_usd_zero_ignored():
    root = tempfile.mkdtemp()
    # 設了 usd soft,但本票 cost_usd=0(如 codex 未回 cost)→ usd 不誤卡;
    # token 達 soft → 由 token 卡
    store, d = _disp(root, _profile(ticket_soft_usd=1.0, ticket_hard_usd=5.0,
                                    ticket_soft_tokens=300000,
                                    ticket_hard_tokens=800000))
    store.upsert_session(_sess(root, cost_usd=0.0, tokens=300000))
    dmod.run_attempt = _fork()
    ev = d.handle(_ticket(), "p")
    assert "ticket-soft" in _scope(ev)
    assert any("token" in c for _, c in d.source.comments)


# -- 月/agent + 全站 -------------------------------------------------------- #
def test_monthly_blocks():
    root = tempfile.mkdtemp()
    store, d = _disp(root, _profile(monthly_max_usd=10.0))
    store.upsert_session(_sess(root, cost_usd=0.0))
    with open(store.journal_path, "a") as f:
        f.write(json.dumps({"type": "attempt_finished", "ts": time.time(),
                            "cost": 10.0, "tokens": 1, "profile": "p"}) + "\n")
    dmod.run_attempt = _fork()
    ev = d.handle(_ticket(), "p")
    assert "monthly" in _scope(ev)


def test_global_blocks():
    root = tempfile.mkdtemp()
    store, d = _disp(root, _profile(),
                     global_budget={"monthly_max_usd": 10.0})
    store.upsert_session(_sess(root, cost_usd=0.0))
    with open(store.journal_path, "a") as f:
        for prof in ("p", "q"):
            f.write(json.dumps({"type": "attempt_finished", "ts": time.time(),
                                "cost": 6.0, "tokens": 1, "profile": prof})
                    + "\n")
    dmod.run_attempt = _fork()
    ev = d.handle(_ticket(), "p")
    assert "global" in _scope(ev)                         # 全站 12 ≥ 10


def test_no_limits_runs():
    root = tempfile.mkdtemp()
    store, d = _disp(root, _profile())                    # 都不限
    store.upsert_session(_sess(root, cost_usd=99.0, tokens=99999999))
    dmod.run_attempt = _fork()
    ev = d.handle(_ticket(), "p")
    assert store.get_session(1).pending_reason != "budget"
    assert any(e["type"] == "attempt_started" for e in ev)


def test_attempt_finished_carries_cost_and_tokens():
    root = tempfile.mkdtemp()
    store, d = _disp(root, _profile())
    store.upsert_session(_sess(root))
    dmod.run_attempt = _fork(cost=0.33, tokens=4242)
    ev = d.handle(_ticket(), "p")
    fin = [e for e in ev if e["type"] == "attempt_finished"]
    assert fin and fin[0]["cost"] == 0.33 and fin[0]["tokens"] == 4242
    assert store.get_session(1).tokens == 4242            # 累計進 session


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
    print("test-budget-precheck:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
