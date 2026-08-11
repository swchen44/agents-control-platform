#!/usr/bin/env python3
"""W2.3 — 起點審批門 單元測(pytest-compatible;mock source,免 token/真 Jira)。

狀態機:首次貼 plan→awaiting、人持有→awaiting、填對→proceed、填錯→reprompt、
超上限→escalate;dispatcher 集成:awaiting 不 fork、resume 不審。
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp import dispatcher as dmod  # noqa: E402
from arcp.approval import ApprovalGate  # noqa: E402
from arcp.dispatcher import Dispatcher  # noqa: E402
from arcp.inner_runner import AttemptResult  # noqa: E402
from arcp.profiles import Profile  # noqa: E402
from arcp.sections import Section, render  # noqa: E402
from arcp.store import Store, TicketSession  # noqa: E402
from arcp.ticket import Ticket  # noqa: E402

BOT = "BOT-ACCT"


class MockSource:
    def __init__(self):
        self.desc = {}
        self.comments = []
        self.assigns = []
        self.watchers = []

    def set_description(self, iid, text):
        self.desc[iid] = text

    def add_comment(self, iid, text):
        self.comments.append((iid, text))

    def assign(self, iid, acct):
        self.assigns.append((iid, acct))

    def add_watcher(self, iid, acct):     # K:approver watcher
        self.watchers.append((iid, acct))


def _profile(**kw):
    base = dict(name="p", workspace_template="empty",
                workspace_folder="tickets/{issue_id}", skills=[],
                agent={"backend": "rawcli"}, verify=[], max_attempts=1,
                on_unknown="pending", require_approval=True,
                approver="APPROVER", max_revisions=2)
    base.update(kw)
    return Profile(**base)


def _sess():
    return TicketSession(issue_id=1, key="P-1", profile="p", workspace="?",
                         session_id=None, attempts=0, outcome=None,
                         pending_reason=None, cost_usd=0.0)


def _ticket(desc="原始需求", assignee_id=None):
    return Ticket(id=1, key="P-1", summary="s", state="To Do", assignee=None,
                  assignee_id=assignee_id, description=desc)


def _filled(agent_name, human_email=None):
    body = f"agent_name: {agent_name}" if agent_name else "agent_name:"
    if human_email is not None:
        body += f"\nhuman_email: {human_email}"
    # 定案版面:ARCP 區塊置頂,原始需求沉到區塊下方(after)
    return render("", [
        Section("control",
                "template: empty\nprofile: p\nstatus: awaiting-approval\n"
                "revisions: 0"),
        Section("human", body)], "原始需求")


def _gate():
    return ApprovalGate(MockSource(), Store(tempfile.mkdtemp()), BOT)


def test_first_entry_posts_plan():
    g = _gate()
    sess = _sess()
    d = g.gate(_ticket(), _profile(), sess)
    assert d == "awaiting"
    assert sess.pending_reason == "approval"
    assert "owner=control" in g.source.desc[1]
    assert "owner=human" in g.source.desc[1]
    assert g.source.assigns == [(1, "APPROVER")]
    assert any("審批" in c for _, c in g.source.comments)


def test_awaiting_while_human_holds():
    g = _gate()
    d = g.gate(_ticket(desc=_filled(""), assignee_id="APPROVER"),
               _profile(), _sess())
    assert d == "awaiting"          # assignee 還在人手上


def test_proceed_on_valid():
    g = _gate()
    sess = _sess()
    d = g.gate(_ticket(desc=_filled("myagent"), assignee_id=BOT),
               _profile(), sess)
    assert d == "proceed"
    assert sess.pending_reason is None


def test_reprompt_on_missing_agent_name():
    g = _gate()
    sess = _sess()
    d = g.gate(_ticket(desc=_filled(""), assignee_id=BOT), _profile(), sess)
    assert d == "reprompt"
    assert sess.approval_revisions == 1
    assert g.source.assigns[-1] == (1, "APPROVER")   # 退回審批者
    assert any("填表有誤" in c for _, c in g.source.comments)


class MockSourceWithSearch(MockSource):
    """真 Jira 語意的 mock:user-search 只認識 known 名單。"""
    KNOWN = {"boss@x.tw": "ACCT-BOSS"}

    def find_account_id(self, email):
        return self.KNOWN.get(email)


def test_human_email_valid_resolves():
    g = ApprovalGate(MockSourceWithSearch(), Store(tempfile.mkdtemp()), BOT)
    sess = _sess()
    d = g.gate(_ticket(desc=_filled("myagent", human_email="boss@x.tw"),
                       assignee_id=BOT), _profile(), sess)
    assert d == "proceed"                      # 合法帳號 → 放行


def test_human_email_unknown_reprompts():
    g = ApprovalGate(MockSourceWithSearch(), Store(tempfile.mkdtemp()), BOT)
    sess = _sess()
    d = g.gate(_ticket(desc=_filled("myagent", human_email="ghost@x.tw"),
                       assignee_id=BOT), _profile(), sess)
    assert d == "reprompt"                     # user-search 解析不到 → 退回
    assert any("不是合法 Jira 帳號" in c for _, c in g.source.comments)


def test_human_email_empty_ok_fallback():
    g = ApprovalGate(MockSourceWithSearch(), Store(tempfile.mkdtemp()), BOT)
    d = g.gate(_ticket(desc=_filled("myagent"), assignee_id=BOT),
               _profile(), _sess())
    assert d == "proceed"                      # 選填:空=fallback 審批者


def test_approver_email_resolved_on_assign():
    # approver 是 email 時,assign 前經 user-search 解析成 accountId
    g = ApprovalGate(MockSourceWithSearch(), Store(tempfile.mkdtemp()), BOT)
    sess = _sess()
    g.gate(_ticket(), _profile(approver="boss@x.tw"), sess)   # 首次貼 plan
    assert g.source.assigns == [(1, "ACCT-BOSS")]


def test_escalate_over_max_revisions():
    g = _gate()
    sess = _sess()
    prof = _profile(max_revisions=1)
    t = _ticket(desc=_filled(""), assignee_id=BOT)
    assert g.gate(t, prof, sess) == "reprompt"    # rev 1
    d = g.gate(t, prof, sess)                     # rev 2 > 1 → escalate
    assert d == "escalate" and sess.pending_reason == "escalated"


# -- dispatcher 集成 ------------------------------------------------------- #
def _fork_recording():
    calls = []

    def _f(agent_cfg, ws, prompt, artifacts, attempt, resume_session_id=None, **kw):
        calls.append(ws)
        return AttemptResult(raw_outcome="completed", session_id="s1",
                             truly_resumed=False, cost_usd=0.0, error=None,
                             events_path="", envelope_path="", error_kind=None)
    return _f, calls


def test_dispatcher_awaiting_does_not_fork():
    fork, calls = _fork_recording()
    dmod.run_attempt = fork
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    src = MockSource()
    d = Dispatcher(src, store, {"p": _profile()}, root=root,
                   approval=ApprovalGate(src, store, BOT))
    d.handle(_ticket(), "p")
    assert calls == []                            # 首次審批,不 fork
    assert store.get_session(1).pending_reason == "approval"


def test_dispatcher_resume_skips_gate():
    fork, calls = _fork_recording()
    dmod.run_attempt = fork
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    src = MockSource()
    # 已 fork 過的 session(session_id 有值)→ 不該再審批
    store.upsert_session(TicketSession(
        issue_id=1, key="P-1", profile="p", workspace=os.path.join(root, "ws"),
        session_id="existing", attempts=0, outcome=None, pending_reason=None,
        cost_usd=0.0))
    os.makedirs(os.path.join(root, "ws"), exist_ok=True)
    d = Dispatcher(src, store, {"p": _profile()}, root=root,
                   approval=ApprovalGate(src, store, BOT))
    d.handle(_ticket(), "p")
    # 沒走審批門(W7.2 起 SUCCESS/FAILURE 會寫 goal/score 段到 desc,故不再用
    # 「desc 未被寫」當代理判準;直接看 pending_reason 不是 approval)
    assert store.get_session(1).pending_reason != "approval"
    assert calls != []                             # 直接 resume/fork


def test_approver_added_as_watcher():
    """K:首建 session(鎖定 profile)把 profile.approver 加為 Jira watcher。"""
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    src = MockSource()                              # approver=accountId 直接用
    d = Dispatcher(src, store, {"p": _profile()}, root=root)
    evs = d._add_approver_watcher(_ticket(), _profile(approver="ACC-1"))
    assert src.watchers == [(1, "ACC-1")]
    assert any(e["type"] == "watcher_added" for e in evs)
    src2 = MockSourceWithSearch()                  # approver=email → 先解析
    d2 = Dispatcher(src2, store, {"p": _profile()}, root=root)
    d2._add_approver_watcher(_ticket(), _profile(approver="boss@x.tw"))
    assert src2.watchers == [(1, "ACCT-BOSS")]     # KNOWN["boss@x.tw"]
    src3 = MockSource()                            # 無 approver → 不加、不報錯
    d3 = Dispatcher(src3, store, {"p": _profile()}, root=root)
    assert d3._add_approver_watcher(_ticket(), _profile(approver=None)) == []
    assert src3.watchers == []


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
    print("test-approval:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
