#!/usr/bin/env python3
"""詳情頁『來源・連結・用量』卡:來源推導(CR/job/人)、一次性連結清單(完整 token)、
per-ticket 用量 vs soft/hard。唯讀讀 store/interactions。pytest-compatible,亦自跑。"""
from __future__ import annotations

import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "scripts"))
sys.path.insert(0, _HERE)

import detail_server as ds  # noqa: E402

from arcp.interaction import build_request  # noqa: E402
from arcp.store import Store, TicketSession  # noqa: E402

ok = fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}")


tmp = tempfile.mkdtemp()
ds.ROOT = tmp
st = Store(tmp)

# 票 5:CR 來源 + 用量 + 兩個一次性連結(指令台 + 評分)
st.upsert_session(TicketSession(
    issue_id=5, key="SCRUM-5", profile="default", workspace="ws",
    session_id="s", attempts=2, outcome=None, pending_reason=None,
    cost_usd=0.8, tokens=250000, clearquest_id="CR-42"))
cmd = build_request(5, "SCRUM-5", "command", kind="command")
st.upsert_interaction(cmd)
sc = build_request(5, "SCRUM-5", "score_and_close")
st.upsert_interaction(sc)

# read_interactions
ix = ds.read_interactions(5)
check("read_interactions:讀到 2 筆", len(ix) == 2)

s5 = ds.read_sessions()[5]
card = ds._ticket_meta_card(5, s5, evs=[])
check("來源推導=ClearQuest CR", "ClearQuest CR" in card and "CR-42" in card)
check("一次性連結列出 指令台 + 評分/裁決",
      "指令台" in card and "評分/裁決" in card)
check("連結含完整 token URL(/form/<token>)",
      f"/form/{cmd.token}" in card and cmd.token[-8:] in card)
check("capability 安全提醒", "capability" in card and "鎖本機/內網" in card)
check("用量 bar:USD/token soft+hard", "USD soft" in card
      and "token soft" in card and "token hard" in card)
check("用量值:$0.8 + 250,000 token",
      "$0.8000" in card and "250,000" in card)

# 票 6:job 來源(有 job_fired 事件、無 CR/base_ref)
card6 = ds._ticket_meta_card(
    6, {"key": "SCRUM-6", "profile": "default", "cost_usd": 0, "tokens": 0},
    evs=[{"type": "job_fired", "issue_id": 6, "run_name": "nightly"}])
check("來源推導=排程/單次 job «nightly»", "job «nightly»" in card6)
check("票 6 無一次性連結 → 提示", "尚無一次性連結" in card6)

# 票 7:跨票交接子票(base_ref)
card7 = ds._ticket_meta_card(
    7, {"key": "SCRUM-7", "profile": "default", "cost_usd": 0, "tokens": 0,
        "base_ref": "3"}, evs=[])
check("來源推導=跨票交接子票(base 母票 3)",
      "跨票交接子票" in card7 and "issue_id=3" in card7)

st.close()
print(f"test-ticket-detail: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
