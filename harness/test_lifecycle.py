#!/usr/bin/env python3
"""W2.4 — assignee=資源開關 單元測(§6/W12;pytest-compatible,亦自跑)。

涵蓋:assignee→人類=inactive+讓出額度(active_sessions 排除)、assignee→機器人=
清 inactive 可 resume、inactive 期間 dispatcher 不派工、審批中(pending:approval)
不誤標、未配置 bot_account_id 退回舊語義(pending:external)、無 session/終態不管。
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


def test_assignee_to_human_sets_inactive_and_yields_quota():
    store = _store()
    store.upsert_session(_sess())
    pol = _policy(store)
    assert [s.issue_id for s in store.active_sessions()] == [1]  # 原本占額度
    ev = pol.on_assignee_changed(_ticket("HUMAN-1"))
    assert [e["type"] for e in ev] == ["inactive_set"]
    assert store.get_session(1).inactive is True
    assert store.active_sessions() == []                # W8:讓出 F1 額度
    assert any("inactive" in c for _, c in pol.source.comments)


def test_assignee_back_to_bot_clears_inactive():
    store = _store()
    store.upsert_session(_sess(inactive=True))
    pol = _policy(store)
    ev = pol.on_assignee_changed(_ticket(BOT))
    assert [e["type"] for e in ev] == ["inactive_cleared"]
    sess = store.get_session(1)
    assert sess.inactive is False
    assert sess.session_id == "s1"                      # session 留著才能 resume
    assert [s.issue_id for s in store.active_sessions()] == [1]


def test_human_to_human_no_duplicate():
    store = _store()
    store.upsert_session(_sess(inactive=True))
    pol = _policy(store)
    assert pol.on_assignee_changed(_ticket("HUMAN-2")) == []
    assert pol.source.comments == []                    # 不重複留言


def test_bot_while_active_noop():
    store = _store()
    store.upsert_session(_sess())                       # 本來就 active
    pol = _policy(store)
    assert pol.on_assignee_changed(_ticket(BOT)) == []


def test_approval_pending_not_marked_inactive():
    # 審批流用 assignee 當放行信號(W2.3),不可誤標 inactive
    store = _store()
    store.upsert_session(_sess(session_id=None, pending_reason="approval"))
    pol = _policy(store)
    assert pol.on_assignee_changed(_ticket("APPROVER")) == []
    assert store.get_session(1).inactive is False


def test_pending_session_inactive_but_quiet():
    # 已 pending(如 G1 handoff 後 human-decision):inactive 照標(讓出額度)
    # 但不留言——pending comment 已說明怎麼繼續,再留言重複矛盾(SCRUM-22 實測)
    store = _store()
    store.upsert_session(_sess(pending_reason="human-decision"))
    pol = _policy(store)
    ev = pol.on_assignee_changed(_ticket("HUMAN-1"))
    assert [e["type"] for e in ev] == ["inactive_set"]
    assert store.get_session(1).inactive is True
    assert pol.source.comments == []                    # 靜默
    ev2 = pol.on_assignee_changed(_ticket(BOT))         # 回機器人也靜默
    assert [e["type"] for e in ev2] == ["inactive_cleared"]
    assert pol.source.comments == []


def test_legacy_without_bot_id():
    # 未配置機器人身份 → 舊語義:任何變更 = 撤銷授權(pending:external)
    store = _store()
    store.upsert_session(_sess())
    pol = _policy(store, bot=None)
    ev = pol.on_assignee_changed(_ticket("HUMAN-1"))
    assert [e["type"] for e in ev] == ["external_pending"]
    assert store.get_session(1).pending_reason == "external"
    assert store.get_session(1).inactive is False


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
              resume_session_id=None):
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
