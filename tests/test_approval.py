#!/usr/bin/env python3
"""W2.3 — 起點審批門 單元測(2026-08-13 表單化版;mock source,免 token)。

狀態機:首次→貼 plan(control 段含表單連結)+發審批表單+指派審批者;
pending:approval→awaiting(等表單,事件驅動);無表單→自癒補發;
表單提交(hil.apply_submission)→清 pending+assignee 收回機器人;
格式錯(agent_name 非 snake_case)→ validate_submission 就地擋。
dispatcher 集成:awaiting 不 fork、resume 不審、approver watcher。
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp import dispatcher as dmod  # noqa: E402
from arcp.approval import ApprovalGate  # noqa: E402
from arcp.dispatcher import Dispatcher  # noqa: E402
from arcp.hil import apply_submission  # noqa: E402
from arcp.inner_runner import AttemptResult  # noqa: E402
from arcp.interaction import validate_submission  # noqa: E402
from arcp.profiles import Profile  # noqa: E402
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

    def get_ticket(self, iid, with_comments=True):   # 表單回寫 human 段用
        return Ticket(id=iid, key="P-1", summary="s", state="To Do",
                      assignee=None, assignee_id=None,
                      description=self.desc.get(iid, ""))


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


def _gate():
    return ApprovalGate(MockSource(), Store(tempfile.mkdtemp()), BOT,
                        form_base_url="http://f:1")


def _approval_req(g, iid=1):
    """該票的待填審批表單(interactions)。"""
    for r in g.store.open_interactions_for_ticket(iid):
        if r.schema_id == "approval":
            return r
    return None


def test_first_entry_posts_plan_and_form():
    g = _gate()
    sess = _sess()
    d = g.gate(_ticket(), _profile(), sess)
    assert d == "awaiting"
    assert sess.pending_reason == "approval"
    assert "owner=control" in g.source.desc[1]
    # 表單化:human 段不再渲染「請填」欄位;表單連結在 control 段(hash 範圍)
    assert "agent_name:" not in g.source.desc[1]
    assert "approval_form: http://f:1/form/" in g.source.desc[1]
    assert g.source.assigns == [(1, "APPROVER")]
    req = _approval_req(g)
    assert req is not None                      # 審批表單已發
    assert any("/form/" in c for _, c in g.source.comments)


def test_awaiting_until_form_submitted():
    g = _gate()
    sess = _sess()
    g.gate(_ticket(), _profile(), sess)         # 首貼
    t2 = _ticket(desc=g.source.desc[1])
    d = g.gate(t2, _profile(), sess)
    assert d == "awaiting"                      # 表單沒填就一直等
    assert len([r for r in g.store.open_interactions_for_ticket(1)
                if r.schema_id == "approval"]) == 1   # 冪等不重發


def test_selfheal_reissues_missing_form():
    g = _gate()
    sess = _sess()
    g.gate(_ticket(), _profile(), sess)
    req = _approval_req(g)
    req.status = "invalidated"                  # 模擬表單遺失/失效
    g.store.upsert_interaction(req)
    d = g.gate(_ticket(desc=g.source.desc[1]), _profile(), sess)
    assert d == "awaiting"
    assert _approval_req(g) is not None         # 自癒補發


def test_submit_form_releases():
    g = _gate()
    sess = _sess()
    g.gate(_ticket(), _profile(), sess)
    req = _approval_req(g)
    req.submission = {"agent_name": "myagent"}
    req.submitted_by = "boss@x.tw"
    evs = apply_submission(g.source, g.store, req, bot_account_id=BOT)
    s = g.store.get_session(1)
    assert s.pending_reason is None             # 提交即放行
    assert (1, BOT) in g.source.assigns         # assignee 收回機器人
    assert any(e["type"] == "approval" and e["decision"] == "proceed"
               and e["agent_name"] == "myagent" for e in evs)
    d = g.gate(_ticket(desc=g.source.desc[1]), _profile(), s)
    assert d == "proceed"                       # 下輪 gate 放行


def test_validate_agent_name_snake_case():
    ok, errs, _ = validate_submission("approval", {"agent_name": "My Agent"})
    assert not ok and any("snake_case" in e for e in errs)
    ok, _, cleaned = validate_submission("approval", {"agent_name": "my_agent"})
    assert ok and cleaned["agent_name"] == "my_agent"
    ok, errs, _ = validate_submission("approval", {})
    assert not ok                               # agent_name 必填


class MockSourceWithSearch(MockSource):
    """真 Jira 語意的 mock:user-search 只認識 known 名單。"""
    KNOWN = {"boss@x.tw": "ACCT-BOSS"}

    def find_account_id(self, email):
        return self.KNOWN.get(email)


def test_approver_email_resolved_on_assign():
    # approver 是 email 時,assign 前經 user-search 解析成 accountId
    g = ApprovalGate(MockSourceWithSearch(), Store(tempfile.mkdtemp()), BOT,
                     form_base_url="http://f:1")
    sess = _sess()
    g.gate(_ticket(), _profile(approver="boss@x.tw"), sess)   # 首次貼 plan
    assert g.source.assigns == [(1, "ACCT-BOSS")]


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
                   approval=ApprovalGate(src, store, BOT,
                                         form_base_url="http://f:1"))
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
                   approval=ApprovalGate(src, store, BOT,
                                         form_base_url="http://f:1"))
    d.handle(_ticket(), "p")
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
