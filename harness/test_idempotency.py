#!/usr/bin/env python3
"""W3.2 — A2 冪等分層 單元測(DESIGN_idempotency;pytest-compatible,亦自跑)。

模擬 crash 窗口:
- 盤點 #3:dispatcher 終態後重跑 → 不重派工、不重留言(at-most-once)。
- 盤點 #4:approval gate 外寫途中 crash → revisions 已持久化,escalate 上限
  跨 crash 有效;首貼冪等 key(control 段存在)→ 重跑不重貼 plan/說明。
- 盤點 #2:指令重放(watermark 丟失)→ 狀態操作冪等(cancel 兩次仍 ABORTED)。
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
from arcp_harness.store import Store  # noqa: E402
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


class CrashOnCommentSource(MockSource):
    """外寫途中 crash 模擬:add_comment 一律炸(store 已寫、外寫沒完成)。"""

    def add_comment(self, iid, text):
        raise RuntimeError("simulated crash during external write")


def _profile(**kw):
    base = dict(name="p", workspace_template="empty",
                workspace_folder="tickets/{issue_id}", skills=[],
                agent={"backend": "rawcli"}, verify=[], max_attempts=1,
                on_unknown="pending")
    base.update(kw)
    return Profile(**base)


def _ticket(desc="原始需求", assignee_id=None):
    return Ticket(id=1, key="P-1", summary="s", state="To Do",
                  assignee=None, assignee_id=assignee_id, description=desc)


def _filled_empty_agent_name():
    return render("", [
        Section("control", "template: empty\nprofile: p\n"
                           "status: awaiting-approval\nrevisions: 0"),
        Section("human", "agent_name:")], "原始需求")


def _fork_recorder():
    calls = []

    def _f(agent_cfg, ws, prompt, artifacts, attempt, resume_session_id=None):
        calls.append(ws)
        return AttemptResult(raw_outcome="completed", session_id="s1",
                             truly_resumed=False, cost_usd=0.0, error=None,
                             events_path="", envelope_path="", error_kind=None)
    return _f, calls


# -- 盤點 #3:dispatcher 終態 at-most-once ---------------------------------- #
def test_terminal_rerun_no_duplicate():
    fork, calls = _fork_recorder()
    dmod.run_attempt = fork
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    src = MockSource()
    d = Dispatcher(src, store, {"p": _profile()}, root=root)
    d.handle(_ticket(), "p")                       # → SUCCESS
    n_calls, n_comments = len(calls), len(src.comments)
    assert n_calls == 1
    d.handle(_ticket(), "p")                       # 重跑(= crash 後重啟)
    assert len(calls) == n_calls                   # 不重派工
    assert len(src.comments) == n_comments         # 不重留言


# -- 盤點 #4:approval 先持久化、escalate 上限跨 crash --------------------- #
def test_approval_revisions_survive_crash():
    store = Store(tempfile.mkdtemp())
    prof = _profile(require_approval=True, approver="APPR", max_revisions=2)
    t = _ticket(desc=_filled_empty_agent_name(), assignee_id=BOT)

    def _sess():
        s = store.get_session(1)
        if s is None:
            from arcp_harness.store import TicketSession
            s = TicketSession(issue_id=1, key="P-1", profile="p",
                              workspace="?", session_id=None, attempts=0,
                              outcome=None, pending_reason=None, cost_usd=0.0)
        return s

    # 退回 #1:外寫 crash——revisions 必須已在 store
    g_bad = ApprovalGate(CrashOnCommentSource(), store, BOT)
    try:
        g_bad.gate(t, prof, _sess())
        raise AssertionError("應該炸在外寫")
    except RuntimeError:
        pass
    assert store.get_session(1).approval_revisions == 1   # 已持久化

    # 重啟後退回 #2(正常)→ revisions 2(不是重置回 1)
    g = ApprovalGate(MockSource(), store, BOT)
    assert g.gate(t, prof, _sess()) == "reprompt"
    assert store.get_session(1).approval_revisions == 2

    # 退回 #3 → 超過 max_revisions=2 → escalate(上限跨 crash 有效)
    assert g.gate(t, prof, _sess()) == "escalate"
    assert store.get_session(1).pending_reason == "escalated"


def test_approval_first_entry_idempotency_key():
    store = Store(tempfile.mkdtemp())
    prof = _profile(require_approval=True, approver="APPR", max_revisions=3)
    src = MockSource()
    g = ApprovalGate(src, store, BOT)
    from arcp_harness.store import TicketSession
    sess = TicketSession(issue_id=1, key="P-1", profile="p", workspace="?",
                         session_id=None, attempts=0, outcome=None,
                         pending_reason=None, cost_usd=0.0)
    g.gate(_ticket(), prof, sess)                  # 首貼:plan + 說明 + 指派
    assert len(src.comments) == 1 and 1 in src.desc
    written = src.desc[1]
    # crash 後重跑:description 已有 control 段(冪等 key)→ awaiting,不重貼
    g.gate(_ticket(desc=written, assignee_id="APPR"), prof, sess)
    assert len(src.comments) == 1                  # 說明不重貼
    assert list(src.desc.values()) == [written]    # description 不重寫


# -- 盤點 #2:指令重放冪等 -------------------------------------------------- #
def test_command_replay_idempotent():
    store = Store(tempfile.mkdtemp())
    from arcp_harness.store import TicketSession
    store.upsert_session(TicketSession(
        issue_id=1, key="P-1", profile="p", workspace="ws", session_id="s1",
        attempts=1, outcome=None, pending_reason=None, cost_usd=0.0))
    src = MockSource()
    h = CommandHandler(src, store, ["Boss"])
    c = Comment(id=9, author="Boss", author_id="b1", body="@agent cancel",
                created="t")
    h.handle(_ticket(), c)
    h.handle(_ticket(), c)                         # watermark 丟失 → 重放
    assert store.get_session(1).outcome == "ABORTED"   # 狀態冪等
    assert len(src.comments) == 2                  # ack 重複一則(記錄於盤點 #2,可接受)


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
    print("test-idempotency:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
