#!/usr/bin/env python3
"""主題 N:內部態 → Jira 狀態同步(KP2 型 workflow)。免網 mock。
情境:transition_to 精確名稱(不 fallback category)、_sync_key 特判
(UNKNOWN→Pending、queued/inactive 不動)、_sync_status(skip 同名/轉不到
不炸)、close 兩步保險(Closed 不可達→先 Resolve)。pytest 相容,亦自跑。"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from arcp.dispatcher import Dispatcher  # noqa: E402
from arcp.hil import _close_transition  # noqa: E402
from arcp.jira_source import JiraCloudSource  # noqa: E402
from arcp.store import Store, TicketSession  # noqa: E402
from arcp.ticket import Ticket  # noqa: E402

ok = fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}")


# ── transition_to:精確名稱、不 fallback ───────────────────────────── #
src = JiraCloudSource("https://x.atlassian.net", "a@x", "t")
_posts = []


def _fake(method, path, params=None, body=None):
    if method == "POST":
        _posts.append(body)
        return {}
    return {"transitions": [                    # KP2 In Progress 的可達集
        {"id": "1", "to": {"name": "Resolve",
                           "statusCategory": {"key": "indeterminate"}}},
        {"id": "2", "to": {"name": "Cancelled",
                           "statusCategory": {"key": "done"}}}]}


src._request = _fake
check("transition_to:名稱命中 → 轉",
      src.transition_to(1, "resolve") is True
      and _posts[-1] == {"transition": {"id": "1"}})
n = len(_posts)
check("transition_to:名稱不在(Closed)→ False、不亂轉",
      src.transition_to(1, "Closed") is False and len(_posts) == n)

# ── _sync_key 特判 ─────────────────────────────────────────────────── #
def _s(**kw):
    base = dict(issue_id=1, key="KP2-9", profile="p", workspace="ws",
                session_id="s", attempts=1, outcome=None,
                pending_reason=None, cost_usd=0.0)
    base.update(kw)
    return TicketSession(**base)


K = Dispatcher._sync_key
check("sync_key:running / pending / 終態 / abort",
      K(_s()) == "running" and K(_s(pending_reason="approval")) == "hil_middle"
      and K(_s(outcome="SUCCESS")) == "hil_end"
      and K(_s(outcome="FAILURE")) == "hil_end"
      and K(_s(outcome="ABORTED")) == "aborted")
check("sync_key:UNKNOWN→hil_middle(定案 Pending,非 Resolve)",
      K(_s(outcome="UNKNOWN")) == "hil_middle")
check("sync_key:queued / inactive / 無 session → 不動",
      K(_s(queued=True)) is None and K(_s(inactive=True)) is None
      and K(None) is None)


# ── _sync_status(dispatcher 收尾)─────────────────────────────────── #
class _Src:
    base_url = "https://x"

    def __init__(self, reachable=("In Progress", "Pending", "Cancelled")):
        self.calls, self.reachable = [], reachable

    def transition_to(self, iid, name):
        self.calls.append(name)
        return name in self.reachable

    def add_comment(self, iid, text):
        pass


SYNC = {"running": "In Progress", "hil_middle": "Pending",
        "hil_end": "Resolve", "closed": "Closed", "aborted": "Cancelled"}


def _mk(sess, sync=SYNC):
    root = tempfile.mkdtemp()
    st = Store(os.path.join(root, "s"))
    st.upsert_session(sess)
    d = Dispatcher(_Src(), st, {}, root=root)
    d.status_sync = dict(sync)
    return d


def _tk(state="To Do"):
    return Ticket(id=1, key="KP2-9", summary="s", state=state, assignee=None,
                  assignee_id=None, description="x")


d = _mk(_s())
evs = []
d._sync_status(_tk("To Do"), evs)
check("sync:running(To Do)→ 轉 In Progress + journal status_synced",
      d.source.calls == ["In Progress"]
      and any(e["type"] == "status_synced" and e["to"] == "In Progress"
              for e in evs))
d = _mk(_s())
d._sync_status(_tk("In Progress"), [])
check("sync:已在目標狀態 → 不轉", d.source.calls == [])
d = _mk(_s(outcome="SUCCESS"))
d._sync_status(_tk("In Progress"), [])          # Resolve 不在 reachable mock
check("sync:轉不到(workflow 限制)→ 只 log 不炸",
      d.source.calls == ["Resolve"])
d = _mk(_s(outcome="ABORTED"))
d._sync_status(_tk("Pending"), [])
check("sync:aborted → Cancelled(任何狀態可進)",
      d.source.calls == ["Cancelled"])
d = _mk(_s(), sync={})
d._sync_status(_tk("To Do"), [])
check("sync:沒設 status_sync → 全關", d.source.calls == [])

# ── close 兩步保險 ─────────────────────────────────────────────────── #
s1 = _Src(reachable=("Closed",))
check("close:Closed 直達 → 一步", _close_transition(s1, 1, SYNC) is True
      and s1.calls == ["Closed"])
s2 = _Src(reachable=("Resolve", "Closed2"))


class _Src2(_Src):
    def __init__(self):
        super().__init__()
        self.seq = []

    def transition_to(self, iid, name):
        self.seq.append(name)
        # 第一次 Closed 失敗(還在 In Progress);轉 Resolve 後 Closed 可達
        if name == "Closed":
            return "Resolve" in self.seq
        return name == "Resolve"


s2 = _Src2()
check("close:Closed 不可達 → 先 Resolve 再 Closed(兩步)",
      _close_transition(s2, 1, SYNC) is True
      and s2.seq == ["Closed", "Resolve", "Closed"])


class _SrcDone(_Src):
    def __init__(self):
        super().__init__()
        self.done_called = False

    def transition(self, iid, cat):
        self.done_called = cat == "done"
        return True


s3 = _SrcDone()
check("close:沒設 status_sync → 原行為(category done)",
      _close_transition(s3, 1, None) is True and s3.done_called)

print(f"test-status-sync: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
