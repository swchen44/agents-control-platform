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
from arcp import dispatcher as dmod  # noqa: E402
from arcp.approval import ApprovalGate  # noqa: E402
from arcp.dispatcher import Dispatcher  # noqa: E402
from arcp.inner_runner import AttemptResult  # noqa: E402
from arcp.profiles import Profile  # noqa: E402
from arcp.sections import Section, render  # noqa: E402
from arcp.store import Store  # noqa: E402
from arcp.ticket import Ticket  # noqa: E402

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

    def _f(agent_cfg, ws, prompt, artifacts, attempt, resume_session_id=None, **kw):
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
            from arcp.store import TicketSession
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
    from arcp.store import TicketSession
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


# -- W5.1:sid 預派 + crash 偵測(盤點 #5)---------------------------------- #
def test_sid_preassigned_and_persisted_before_spawn():
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    seen = {}

    def _fork(agent_cfg, ws, prompt, artifacts, attempt,
              resume_session_id=None, preassigned_session_id=None, **kw):
        s = store.get_session(1)                  # spawn 當下讀 store
        seen.update(persisted_sid=s.session_id, persisted_attempts=s.attempts,
                    resume=resume_session_id, pre=preassigned_session_id)
        return AttemptResult(raw_outcome="completed",
                             session_id=preassigned_session_id,
                             truly_resumed=False, cost_usd=0.0, error=None,
                             events_path="", envelope_path="",
                             error_kind=None)
    dmod.run_attempt = _fork
    d = Dispatcher(MockSource(), store,
                   {"p": _profile(agent={"backend": "rawcli",
                                         "engine": "claude"})}, root=root)
    ev = d.handle(_ticket(), "p")
    assert seen["persisted_sid"] is not None          # spawn 前 sid 已落 store
    assert seen["persisted_attempts"] == 1            # attempts 也先持久化
    assert seen["pre"] == seen["persisted_sid"]       # 預派的就是持久化的
    assert seen["resume"] is None                     # 首跑非 resume
    assert any(e["type"] == "attempt_started" for e in ev)


def test_crash_with_sid_refunds_and_resumes():
    from arcp.store import TicketSession
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    base = os.path.join(root, "tickets", "1")
    os.makedirs(os.path.join(base, "ws"), exist_ok=True)
    # 上輪:attempts=1 已持久化、sid 有,但 a1.envelope.json 不存在 = 中途死
    store.upsert_session(TicketSession(
        issue_id=1, key="P-1", profile="p",
        workspace=os.path.join(base, "ws"), session_id="sid-crash",
        attempts=1, outcome=None, pending_reason=None, cost_usd=0.0))
    calls = []

    def _fork(agent_cfg, ws, prompt, artifacts, attempt,
              resume_session_id=None, preassigned_session_id=None, **kw):
        calls.append(resume_session_id)
        return AttemptResult(raw_outcome="completed", session_id="sid-crash",
                             truly_resumed=True, cost_usd=0.0, error=None,
                             events_path="", envelope_path="",
                             error_kind=None)
    dmod.run_attempt = _fork
    d = Dispatcher(MockSource(), store, {"p": _profile()}, root=root)
    ev = d.handle(_ticket(), "p")
    assert any(e["type"] == "attempt_crash_recovered" for e in ev)
    assert calls == ["sid-crash"]                 # 退還後以原 sid resume
    assert store.get_session(1).attempts == 1     # 退還再 +1,不重複消耗
    assert store.get_session(1).outcome == "SUCCESS"


def test_crash_without_sid_goes_unknown():
    from arcp.store import TicketSession
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    base = os.path.join(root, "tickets", "1")
    os.makedirs(os.path.join(base, "ws"), exist_ok=True)
    store.upsert_session(TicketSession(
        issue_id=1, key="P-1", profile="p",
        workspace=os.path.join(base, "ws"), session_id=None,
        attempts=1, outcome=None, pending_reason=None, cost_usd=0.0))
    calls = []
    dmod.run_attempt = lambda *a, **k: calls.append(1)
    src = MockSource()
    d = Dispatcher(src, store, {"p": _profile()}, root=root)
    d.handle(_ticket(), "p")
    assert calls == []                            # 不能證明 → 不重跑
    s = store.get_session(1)
    assert s.outcome == "UNKNOWN" and s.pending_reason == "unknown"
    assert any("無法證明" in c for _, c in src.comments)


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
