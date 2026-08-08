#!/usr/bin/env python3
"""W11 — assignee 恆定=Agent 單元測(取代 W2.4 資源開關;亦自跑)。

W11 起 assignee 不再是資源開關/觸發(人機互動改走一次性表單)。涵蓋:assignee 被改離
agent → 告警 + 提醒(不強制改回)、改回 agent → 靜默記錄、未配置 bot_id → 一律告警、
無 session/終態不管、dispatcher 仍略過 inactive session。
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp_harness import dispatcher as dmod  # noqa: E402
from arcp_harness.commands import ExternalChangePolicy  # noqa: E402
from arcp_harness.dispatcher import Dispatcher  # noqa: E402
from arcp_harness.inner_runner import AttemptResult  # noqa: E402
from arcp_harness.profiles import Profile  # noqa: E402
from arcp_harness.store import Store, TicketSession  # noqa: E402
from arcp_harness.ticket import Ticket  # noqa: E402

BOT = "BOT-ACCT"


class MockSource:
    def __init__(self):
        self.comments = []

    def add_comment(self, iid, text):
        self.comments.append((iid, text))


def _store():
    return Store(tempfile.mkdtemp())


def _policy(store, bot=BOT):
    return ExternalChangePolicy(MockSource(), store, ["Done"],
                                bot_account_id=bot)


def _sess(**kw):
    base = dict(issue_id=1, key="P-1", profile="p", workspace="ws",
                session_id="s1", attempts=1, outcome=None,
                pending_reason=None, cost_usd=0.0)
    base.update(kw)
    return TicketSession(**base)


def _ticket(assignee_id):
    return Ticket(id=1, key="P-1", summary="s", state="To Do",
                  assignee="someone", assignee_id=assignee_id, description="")


def test_assignee_away_alerts_no_revert():
    # W11:被改離 agent → 告警 + 提醒 comment,不強制改回、不動 session 狀態
    store = _store()
    store.upsert_session(_sess())
    pol = _policy(store)
    ev = pol.on_assignee_changed(_ticket("HUMAN-1"))
    assert [e["type"] for e in ev] == ["assignee_alert"]
    assert any("assignee" in c and "表單" in c for _, c in pol.source.comments)
    s = store.get_session(1)
    assert s.inactive is False and s.pending_reason is None   # 不改回、不動狀態
    assert [x.issue_id for x in store.active_sessions()] == [1]  # 仍占額度


def test_assignee_back_to_bot_quiet():
    store = _store()
    store.upsert_session(_sess())
    pol = _policy(store)
    ev = pol.on_assignee_changed(_ticket(BOT))
    assert [e["type"] for e in ev] == ["assignee_restored"]
    assert pol.source.comments == []                    # 靜默,不留言


def test_assignee_no_bot_id_alerts():
    # 未配置機器人身份 → 無法確認是否為 agent,一律告警
    store = _store()
    store.upsert_session(_sess())
    pol = _policy(store, bot=None)
    ev = pol.on_assignee_changed(_ticket("HUMAN-1"))
    assert [e["type"] for e in ev] == ["assignee_alert"]


def test_no_session_or_terminal_untouched():
    store = _store()
    pol = _policy(store)
    assert pol.on_assignee_changed(_ticket("HUMAN-1")) == []   # 無 session
    store.upsert_session(_sess(outcome="SUCCESS"))
    assert pol.on_assignee_changed(_ticket("HUMAN-1")) == []   # 終態
    assert store.get_session(1).inactive is False


def test_dispatcher_skips_inactive():
    calls = []

    def _fork(agent_cfg, ws, prompt, artifacts, attempt,
              resume_session_id=None, **kw):
        calls.append(ws)
        return AttemptResult(raw_outcome="completed", session_id="s1",
                             truly_resumed=False, cost_usd=0.0, error=None,
                             events_path="", envelope_path="", error_kind=None)

    dmod.run_attempt = _fork
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    store.upsert_session(_sess(workspace=os.path.join(root, "ws"),
                               inactive=True, attempts=0))
    prof = Profile(name="p", workspace_template="empty",
                   workspace_folder="tickets/{issue_id}", skills=[],
                   agent={"backend": "rawcli"}, verify=[], max_attempts=1,
                   on_unknown="pending")
    d = Dispatcher(MockSource(), store, {"p": prof}, root=root)
    ev = d.handle(_ticket(None), "p")
    assert calls == [] and ev == []                     # inactive:不派工


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
    print("test-lifecycle:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
