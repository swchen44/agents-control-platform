#!/usr/bin/env python3
"""W2.5 — F3 換手 單元測(@agent next + G1 next 驅動;pytest-compatible,亦自跑)。

涵蓋:@agent next 換 profile(重置 session + pin)、無效目標拒絕、dispatcher 用
pin 的 profile 並重 provision、G1 handoff next.kind=agent 自動換手、kind=human
交人(pending:human-decision + assign)、換手到 require_approval profile 重走審批門。
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp_harness import dispatcher as dmod  # noqa: E402
from arcp_harness.approval import ApprovalGate  # noqa: E402
from arcp_harness.commands import CommandHandler  # noqa: E402
from arcp_harness.dispatcher import Dispatcher  # noqa: E402
from arcp_harness.inner_runner import AttemptResult  # noqa: E402
from arcp_harness.profiles import Profile  # noqa: E402
from arcp_harness.sections import Section, render  # noqa: E402
from arcp_harness.store import Store, TicketSession  # noqa: E402
from arcp_harness.ticket import Comment, Ticket  # noqa: E402

BOT = "BOT-ACCT"


class MockSource:
    def __init__(self):
        self.desc = {}
        self.comments = []
        self.assigns = []

    def set_description(self, iid, text):
        self.desc[iid] = text

    def add_comment(self, iid, text):
        self.comments.append((iid, text))

    def assign(self, iid, acct):
        self.assigns.append((iid, acct))


def _profile(name, **kw):
    base = dict(name=name, workspace_template="empty",
                workspace_folder=f"tickets/{name}-{{issue_id}}", skills=[],
                agent={"backend": "rawcli", "tag": name}, verify=[],
                max_attempts=2, on_unknown="pending")
    base.update(kw)
    return Profile(**base)


def _sess(**kw):
    base = dict(issue_id=1, key="P-1", profile="p", workspace="(handoff)",
                session_id=None, attempts=0, outcome=None,
                pending_reason=None, cost_usd=0.0)
    base.update(kw)
    return TicketSession(**base)


def _ticket(desc="原始需求"):
    return Ticket(id=1, key="P-1", summary="s", state="To Do",
                  assignee=None, assignee_id=BOT, description=desc)


def _comment(body):
    return Comment(id=9, author="Boss", author_id="b1", body=body,
                   created="2026-08-05T00:00:00Z")


def _handler(store, profiles):
    return CommandHandler(MockSource(), store, ["Boss"], profiles=profiles)


PROFILES = {"p": _profile("p"), "other": _profile("other")}


def _fork_recorder(structured=None):
    calls = []

    def _f(agent_cfg, ws, prompt, artifacts, attempt, resume_session_id=None, **kw):
        calls.append((agent_cfg, ws))
        return AttemptResult(raw_outcome="completed", session_id="s-new",
                             truly_resumed=False, cost_usd=0.0, error=None,
                             events_path="", envelope_path="",
                             error_kind=None, structured=structured)
    return _f, calls


# -- @agent next 指令 ------------------------------------------------------- #
def test_command_next_switches_profile():
    store = Store(tempfile.mkdtemp())
    store.upsert_session(_sess(workspace="old-ws", session_id="s1",
                               attempts=2, outcome="FAILURE",
                               pending_reason="max-attempts"))
    h = _handler(store, PROFILES)
    ev = h.handle(_ticket(), _comment("@agent next other"))
    assert [e["type"] for e in ev] == ["handoff"]
    sess = store.get_session(1)
    assert sess.profile == "other"
    assert sess.session_id is None and sess.attempts == 0
    assert sess.outcome is None and sess.pending_reason is None
    assert sess.workspace == "(handoff)"        # 下輪重 provision 新 instance
    assert any("next → other" in c for _, c in h.source.comments)


def test_command_next_invalid_target():
    store = Store(tempfile.mkdtemp())
    store.upsert_session(_sess(profile="p", workspace="old-ws",
                               session_id="s1", attempts=1))
    h = _handler(store, PROFILES)
    ev = h.handle(_ticket(), _comment("@agent next nosuch"))
    assert [e["type"] for e in ev] == ["command_rejected"]
    sess = store.get_session(1)
    assert sess.profile == "p" and sess.session_id == "s1"   # 原封不動
    assert any("無效" in c for _, c in h.source.comments)


def test_command_next_bare_rejected():
    store = Store(tempfile.mkdtemp())
    store.upsert_session(_sess())
    h = _handler(store, PROFILES)
    ev = h.handle(_ticket(), _comment("@agent next"))
    assert [e["type"] for e in ev] == ["command_rejected"]


# -- dispatcher:pin 優先 + 重 provision ------------------------------------ #
def test_dispatcher_uses_pinned_profile_and_reprovisions():
    fork, calls = _fork_recorder()
    dmod.run_attempt = fork
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    store.upsert_session(_sess(profile="other"))    # 換手後:pin=other、哨值 ws
    d = Dispatcher(MockSource(), store, dict(PROFILES), root=root)
    d.handle(_ticket(), "p")                        # route 仍說 p
    assert len(calls) == 1
    agent_cfg, ws = calls[0]
    assert agent_cfg["tag"] == "other"              # 用 pin 的 profile
    assert "other-1" in ws                          # 新 instance 路徑(other 的命名)
    assert store.get_session(1).workspace == ws     # 路徑回存


# -- G1 next 驅動 ----------------------------------------------------------- #
def test_g1_handoff_to_agent():
    fork, calls = _fork_recorder(structured={
        "reason": "需要另一個專長", "status": "handoff",
        "next": {"to": "other", "kind": "agent"}})
    dmod.run_attempt = fork
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    d = Dispatcher(MockSource(), store, dict(PROFILES), root=root)
    ev = d.handle(_ticket(), "p")
    assert len(calls) == 1                          # 跑了一次就換手,不 grade
    assert any(e["type"] == "handoff" and e.get("kind") == "agent"
               for e in ev)
    sess = store.get_session(1)
    assert sess.profile == "other"                  # pin 新 profile
    assert sess.session_id is None and sess.attempts == 0
    assert sess.workspace == "(handoff)"
    assert any("handoff→other" in c for _, c in d.source.comments)


def test_g1_handoff_to_human():
    fork, calls = _fork_recorder(structured={
        "reason": "需要人工決策", "status": "handoff",
        "next": {"to": "老闆", "kind": "human"}})
    dmod.run_attempt = fork
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    d = Dispatcher(MockSource(), store, dict(PROFILES), root=root)
    # W11:assignee 恆定,handoff→human 不再 assign(人機互動改走一次性表單)
    desc = render("", [Section("human",
                               "agent_name: p\nhuman_email: boss@x.tw")],
                  "原始需求")
    ev = d.handle(_ticket(desc=desc), "p")
    assert any(e["type"] == "handoff" and e.get("kind") == "human"
               for e in ev)
    sess = store.get_session(1)
    assert sess.pending_reason == "human-decision"  # 交人,不排 agent 隊列
    assert sess.profile == "p"                      # 不換 profile
    assert sess.session_id == "s-new"               # session 留著可 resume
    assert d.source.assigns == []                   # W11:不再改 assignee


def test_g1_handoff_to_human_no_assign():
    # W11:handoff→human 不論有無 human_email/approver,一律不改 assignee
    fork, _ = _fork_recorder(structured={
        "reason": "x", "status": "handoff",
        "next": {"to": "誰", "kind": "human"}})
    dmod.run_attempt = fork
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    profiles = {"p": _profile("p", approver="APPR-ACCT")}
    d = Dispatcher(MockSource(), store, profiles, root=root)
    d.handle(_ticket(), "p")
    assert d.source.assigns == []                   # 不亂指派
    assert store.get_session(1).pending_reason == "human-decision"


def test_handoff_to_approval_profile_regates():
    fork, calls = _fork_recorder()
    dmod.run_attempt = fork
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    profiles = {"p": _profile("p"),
                "appr": _profile("appr", require_approval=True,
                                 approver="APPR", max_revisions=2)}
    # 換手後 pin=appr(如 @agent next appr 之後的狀態)
    store.upsert_session(_sess(profile="appr"))
    src = MockSource()
    d = Dispatcher(src, store, profiles, root=root,
                   approval=ApprovalGate(src, store, BOT))
    ev = d.handle(_ticket(), "p")
    assert calls == []                              # 重走審批門,不 fork
    assert store.get_session(1).pending_reason == "approval"
    assert src.assigns == [(1, "APPR")]             # 指派審批者
    assert any(e["type"] == "approval" for e in ev)


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
    print("test-handoff:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
