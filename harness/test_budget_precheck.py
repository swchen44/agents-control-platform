#!/usr/bin/env python3
"""W7.3 — 預算 spawn 前預檢 + 月上限 + budget_override 單元測。

涵蓋:collect_budget_override(容錯)、store.monthly_cost(當月/跨月/跨 profile)、
dispatcher spawn 前預檢(單次達標不 spawn、override 放寬、月上限擋、attempt_finished
帶 cost+profile)。
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp import dispatcher as dmod  # noqa: E402
from arcp.dispatcher import Dispatcher  # noqa: E402
from arcp.inner_runner import AttemptResult  # noqa: E402
from arcp.profiles import Profile  # noqa: E402
from arcp.scoring import collect_budget_override  # noqa: E402
from arcp.store import Store, TicketSession  # noqa: E402
from arcp.ticket import Ticket  # noqa: E402


class MockSource:
    def __init__(self):
        self.comments = []

    def add_comment(self, iid, text):
        self.comments.append((iid, text))


def _profile(single=None, monthly=None, name="p"):
    return Profile(name=name, workspace_template="empty",
                   workspace_folder="tickets/{issue_id}", skills=[],
                   agent={"backend": "rawcli", "engine": "claude"},
                   verify=[], max_attempts=3, on_unknown="pending",
                   max_budget_usd=single, max_budget_monthly_usd=monthly)


def _sess(root, **kw):
    ws = os.path.join(root, "tickets", "1", "ws")
    os.makedirs(ws, exist_ok=True)
    base = dict(issue_id=1, key="P-1", profile="p", workspace=ws,
                session_id="sid-1", attempts=0, outcome=None,
                pending_reason=None, cost_usd=0.0)
    base.update(kw)
    return TicketSession(**base)


def _ticket(desc="d"):
    return Ticket(id=1, key="P-1", summary="s", state="x", assignee=None,
                  assignee_id=None, description=desc)


def _fork_costly(cost):
    def _f(agent_cfg, ws, prompt, artifacts, attempt, resume_session_id=None,
           preassigned_session_id=None, **kw):
        return AttemptResult(raw_outcome="completed", session_id="sid-1",
                             truly_resumed=False, cost_usd=cost, error=None,
                             events_path="", envelope_path="", error_kind=None)
    return _f


# -- collect_budget_override ------------------------------------------------ #
def _desc(**kv):
    body = "\n".join(f"{k}: {v}" for k, v in kv.items())
    return (f"<!-- ARCP:sections v1 -->\n### [ARCP owner=human]\n"
            f"```yaml\n{body}\n```\n<!-- /ARCP:sections -->\n")


def test_budget_override_parse():
    assert collect_budget_override(_desc(budget_override=5)) == 5.0
    assert collect_budget_override(_desc(budget_override="2.5")) == 2.5
    assert collect_budget_override(_desc(budget_override=0)) is None      # <=0
    assert collect_budget_override(_desc(budget_override="x")) is None
    assert collect_budget_override(_desc(score=8)) is None                # 無此 key
    assert collect_budget_override("no block") is None


# -- store.monthly_cost ----------------------------------------------------- #
def test_monthly_cost_scopes_by_month_and_profile():
    root = tempfile.mkdtemp()
    st = Store(root)
    # 當月 ts=now;上月 ts=now-40天
    now = 1_700_000_000.0
    last_month = now - 40 * 86400
    # 直接寫檔控制 ts(journal() 會蓋現在時間,無法驗月份邏輯)
    import json
    with open(st.journal_path, "w") as f:
        for rec in [
            {"type": "attempt_finished", "ts": now, "cost": 1.0, "profile": "p"},
            {"type": "attempt_finished", "ts": now, "cost": 2.0, "profile": "p"},
            {"type": "attempt_finished", "ts": now, "cost": 9.0, "profile": "q"},
            {"type": "attempt_finished", "ts": last_month, "cost": 5.0,
             "profile": "p"},
            {"type": "resolved", "ts": now, "cost": 3.0, "profile": "p"},
        ]:
            f.write(json.dumps(rec) + "\n")
    assert st.monthly_cost("p", now=now) == 3.0        # 1+2(當月、p、attempt)
    assert st.monthly_cost("q", now=now) == 9.0
    assert st.monthly_cost("p", now=last_month) == 5.0  # 上月只有那筆
    st.close()


# -- dispatcher pre-check --------------------------------------------------- #
def test_single_limit_blocks_before_spawn():
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    store.upsert_session(_sess(root, cost_usd=1.0))     # 已花 1.0
    calls = []
    dmod.run_attempt = lambda *a, **k: calls.append(1)
    src = MockSource()
    d = Dispatcher(src, store, {"p": _profile(single=1.0)}, root=root)
    ev = d.handle(_ticket(), "p")
    assert calls == []                                  # 沒 spawn
    assert store.get_session(1).pending_reason == "budget"
    assert any(e["type"] == "pending" and e.get("scope") == "single"
               for e in ev)


def test_override_raises_single_limit():
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    store.upsert_session(_sess(root, cost_usd=1.0))
    dmod.run_attempt = _fork_costly(0.1)                # 便宜、會過
    src = MockSource()
    d = Dispatcher(src, store, {"p": _profile(single=1.0)}, root=root)
    # human 段放寬到 5.0 → 不再擋(cost 1.0 < 5.0),會 spawn
    ev = d.handle(_ticket(desc=_desc(budget_override=5.0)), "p")
    assert store.get_session(1).pending_reason != "budget"
    assert any(e["type"] == "attempt_started" for e in ev)


def test_monthly_cap_blocks():
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    store.upsert_session(_sess(root, cost_usd=0.0))
    # 先塞當月 profile p 已花 10.0
    import json
    import time
    with open(store.journal_path, "a") as f:
        f.write(json.dumps({"type": "attempt_finished", "ts": time.time(),
                            "cost": 10.0, "profile": "p"}) + "\n")
    calls = []
    dmod.run_attempt = lambda *a, **k: calls.append(1)
    src = MockSource()
    d = Dispatcher(src, store, {"p": _profile(monthly=10.0)}, root=root)
    ev = d.handle(_ticket(), "p")
    assert calls == []                                  # 月上限擋、沒 spawn
    assert store.get_session(1).pending_reason == "budget"
    assert any(e["type"] == "pending" and e.get("scope") == "monthly"
               for e in ev)


def test_attempt_finished_carries_cost_profile():
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    store.upsert_session(_sess(root))
    dmod.run_attempt = _fork_costly(0.33)
    d = Dispatcher(MockSource(), store, {"p": _profile()}, root=root)
    ev = d.handle(_ticket(), "p")
    fin = [e for e in ev if e["type"] == "attempt_finished"]
    assert fin and fin[0]["cost"] == 0.33 and fin[0]["profile"] == "p"


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
