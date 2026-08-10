#!/usr/bin/env python3
"""budget 單票 soft 破 → 發 budget_increase 表單 → 自助調高 soft(clamp≤hard)→ resume。
免網:FakeSource 攔 comment/description。pytest-compatible,亦自跑。"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp import dispatcher as dmod  # noqa: E402
from arcp.dispatcher import Dispatcher  # noqa: E402
from arcp.hil import apply_submission  # noqa: E402
from arcp.inner_runner import AttemptResult  # noqa: E402
from arcp.profiles import Profile  # noqa: E402
from arcp.store import Store, TicketSession  # noqa: E402
from arcp.ticket import Ticket  # noqa: E402

ok = fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}")


class FakeSource:
    base_url = "https://jira.example.com"

    def __init__(self):
        self.comments = []
        self.desc = {}

    def add_comment(self, iid, text):
        self.comments.append((iid, text))

    def get_ticket(self, iid):
        t = type("T", (), {})()
        t.id = iid
        t.description = self.desc.get(iid, "")
        return t

    def set_description(self, iid, text):
        self.desc[iid] = text


def _profile(**bud):
    return Profile(name="p", workspace_template="empty",
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


def _fork():
    def _f(*a, **k):
        return AttemptResult(raw_outcome="completed", session_id="sid-1",
                             truly_resumed=False, cost_usd=0.1, error=None,
                             events_path="", envelope_path="", error_kind=None,
                             tokens=100)
    return _f


root = tempfile.mkdtemp()
store = Store(os.path.join(root, "s"))
src = FakeSource()
prof = _profile(ticket_soft_usd=1.0, ticket_hard_usd=5.0)
d = Dispatcher(src, store, {"p": prof}, root=root,
               form_base_url="http://h:8790", mention="acc1")
store.upsert_session(_sess(root, cost_usd=1.0))     # 已達 soft
dmod.run_attempt = _fork()
ev = d.handle(_ticket(), "p")

check("soft 破 → pending ticket-soft",
      any(e["type"] == "pending" and e.get("scope") == "ticket-soft"
          for e in ev))
reqs = [r for r in store.interactions_for_ticket(1)
        if r.schema_id == "budget_increase"]
check("發了 budget_increase 表單", len(reqs) == 1)
check("表單 payload 帶 hard 上限",
      reqs and reqs[0].payload.get("hard_usd") == 5.0)
check("comment 有增額連結",
      any("http://h:8790/form/" in c for _, c in src.comments))

# 自助調高 soft_usd=3.0(≤hard)→ 解 pending
req = reqs[0]
req.submission = {"new_soft_usd": 3.0}
store.upsert_interaction(req)
apply_submission(src, store, req)
sess = store.get_session(1)
check("調高後 soft_usd=3.0", sess.soft_usd == 3.0)
check("調高後解 budget pending", sess.pending_reason is None)

# 再派工:cost 1.0 < 3.0 → 不再卡、會 spawn
ev2 = d.handle(_ticket(), "p")
check("調高後續跑(spawn)", any(e["type"] == "attempt_started" for e in ev2))

# clamp:輸入超過 hard → 封頂到 hard
store.upsert_session(_sess(root, cost_usd=1.0, pending_reason="budget"))
req2 = type(req)(**{**req.__dict__})
req2.request_id = "req-x"; req2.token = "tok-x"
req2.submission = {"new_soft_usd": 99.0}
req2.payload = {"hard_usd": 5.0, "hard_tokens": None}
store.upsert_interaction(req2)
apply_submission(src, store, req2)
check("超過 hard → 封頂到 hard(5.0)", store.get_session(1).soft_usd == 5.0)

store.close()
print(f"test-budget-increase: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
