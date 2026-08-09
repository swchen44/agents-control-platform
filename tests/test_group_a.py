#!/usr/bin/env python3
"""Group A 人機互動(2026-08-09 定案):Q10 人類 prompt→TICKET.md sidecar、
Q11 @agent hold→evict+HIL 表單、Q13 ScoreGate 自評 hook。免 token、確定性。"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from arcp.commands import CommandHandler, parse  # noqa: E402
from arcp.hil import apply_submission  # noqa: E402
from arcp.interaction import build_request  # noqa: E402
from arcp.scoring import ScoreGate  # noqa: E402
from arcp.store import Store, TicketSession  # noqa: E402
from arcp.ticket import Comment, Ticket  # noqa: E402
from arcp.workspace import (  # noqa: E402
    _read_human_notes,
    append_human_instruction,
    render_ticket_md,
)

ok = fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}")


class FakeSource:
    def __init__(self):
        self.comments = []; self.descs = {}; self.transitioned = []
    base_url = "https://x.atlassian.net"
    def add_comment(self, iid, body): self.comments.append((iid, body))
    def get_ticket(self, iid):
        return Ticket(id=iid, key="SCRUM-1", summary="s", state="進行中",
                      assignee=None, assignee_id=None, labels=[],
                      description=self.descs.get(iid, ""), comments=[])
    def set_description(self, iid, d): self.descs[iid] = d
    def transition(self, iid, cat): self.transitioned.append((iid, cat)); return True


def _tk(iid=1):
    return Ticket(id=iid, key="SCRUM-1", summary="做事", state="進行中",
                  assignee=None, assignee_id=None, labels=["agent"],
                  description="做 X", comments=[])


def _sess(iid, ws):
    return TicketSession(issue_id=iid, key="SCRUM-1", profile="p", workspace=ws,
                         session_id="s1", attempts=1, outcome=None,
                         pending_reason=None, cost_usd=0.0)


# ── Q10-a:sidecar 累加 → render_ticket_md 出「人類指示」段 ────────────── #
ws = tempfile.mkdtemp()
append_human_instruction(ws, "改用 X 方法")
append_human_instruction(ws, "別碰 Y")
md = render_ticket_md(_tk(), None, None, _read_human_notes(ws))
check("Q10 render:含人類指示段 + 兩條累加",
      "人類指示" in md and "改用 X 方法" in md and "別碰 Y" in md)

# ── Q10-b:apply_submission 的 human_prompt → 寫進 sidecar ───────────── #
rt = tempfile.mkdtemp(); st = Store(rt); src = FakeSource()
ws2 = os.path.join(rt, "tickets", "1", "ws"); os.makedirs(ws2)
st.upsert_session(_sess(1, ws2))
req = build_request(1, "SCRUM-1", "need_info", payload={"question": "?"})
req.submission = {"answer": "補的資訊", "human_prompt": "請改走 API v3"}
st.upsert_interaction(req)
apply_submission(src, st, req)
check("Q10 apply_submission:human_prompt 進 sidecar",
      "請改走 API v3" in _read_human_notes(ws2))
check("Q10:need_info 提交後 pending 清除(resume)",
      st.get_session(1).pending_reason is None)
st.close()

# ── Q11:@agent hold → evict + pending=hold + 開 hold 表單 ───────────── #
rt = tempfile.mkdtemp(); st = Store(rt); src = FakeSource()
ws3 = os.path.join(rt, "tickets", "1", "ws"); os.makedirs(ws3)
st.upsert_session(_sess(1, ws3))
ch = CommandHandler(src, st, allowed_commenters=["me"],
                    base_url="http://h:8790", mention="acc1")
check("Q11 parse:@agent hold → hold", parse("@agent hold") == "hold")
ch.handle(_tk(), Comment(id=9, author="me", author_id="me",
                         body="@agent hold", created="2026-08-09T00:00:00"))
evict_file = os.path.join(rt, "tickets", "1", "attempts", "EVICT")
holds = [r for r in st.interactions_for_ticket(1) if r.schema_id == "hold"]
check("Q11:寫了 EVICT 檔(evict)", os.path.isfile(evict_file))
check("Q11:pending_reason=hold(進 HIL)", st.get_session(1).pending_reason == "hold")
check("Q11:開了一張 hold 表單", len(holds) == 1)
# hold 表單提交(human_prompt)→ 寫 sidecar + resume(pending 清)
holds[0].submission = {"human_prompt": "改成先跑測試"}
st.upsert_interaction(holds[0])
apply_submission(src, st, holds[0])
check("Q11:hold 提交 → 人類指示進 sidecar",
      "改成先跑測試" in _read_human_notes(ws3))
check("Q11:hold 提交 → pending 清除回排隊",
      st.get_session(1).pending_reason is None)
st.close()

# ── Q13:ScoreGate self_score_fn → 自評進 score_and_close payload ─────── #
rt = tempfile.mkdtemp(); st = Store(rt); src = FakeSource()
sess = _sess(1, os.path.join(rt, "ws")); sess.outcome = "SUCCESS"
st.upsert_session(sess)
sg = ScoreGate(src, st, base_url="http://h:8790", mention="acc1",
               self_score_fn=lambda s: 8)
sg.on_poll(_tk(), st.get_session(1))
sac = [r for r in st.interactions_for_ticket(1) if r.schema_id == "score_and_close"]
check("Q13:發了 score_and_close 表單", len(sac) == 1)
check("Q13:agent 自評(self_score_fn=8)進 payload",
      sac and sac[0].payload.get("agent_score") == 8)
# 沒給 self_score_fn → agent_score=None(不擋流程)
rt2 = tempfile.mkdtemp(); st2 = Store(rt2)
s2 = _sess(2, os.path.join(rt2, "ws")); s2.issue_id = 2; s2.outcome = "FAILURE"
st2.upsert_session(s2)
ScoreGate(src, st2).on_poll(_tk(2), st2.get_session(2))
sac2 = [r for r in st2.interactions_for_ticket(2) if r.schema_id == "score_and_close"]
check("Q13:無 self_score_fn → agent_score=None(仍發表單)",
      sac2 and sac2[0].payload.get("agent_score") is None)
st2.close()

print(f"test-group-a: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
