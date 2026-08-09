#!/usr/bin/env python3
"""dashboard 過濾 + DB schema 視圖(detail_server 純函式)。確定性、免網。

覆蓋:text_matcher(match 不分大小寫 / regex / 無效 regex)、api_list_tickets 的
?q=&field=&mode= 過濾、db_schema(PRAGMA table_info,空表也列欄)。"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import detail_server as ds  # noqa: E402

from arcp.store import Store, TicketSession  # noqa: E402

ok = fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}")


# ── text_matcher ─────────────────────────────────────────────────────── #
f, e = ds.text_matcher("Foo", "match")
check("match:不分大小寫子字串", f("xxFOOxx") and not f("bar") and e is None)
f, e = ds.text_matcher("^SCRUM-[0-9]+$", "regex")
check("regex:錨定樣式", f("SCRUM-42") and not f("xSCRUM-42") and e is None)
f, e = ds.text_matcher("[bad", "regex")
check("regex 無效:fn 恆 False + 回 error", (not f("anything")) and bool(e))
f, e = ds.text_matcher("", "match")
check("空 q → (None, None)(不過濾)", f is None and e is None)

# ── api_list_tickets 過濾 ────────────────────────────────────────────── #
sess = {1: {"key": "SCRUM-1", "profile": "triage", "outcome": None},
        2: {"key": "SCRUM-2", "profile": "builder", "outcome": "SUCCESS"}}
watch = {1: {"key": "SCRUM-1", "summary": "fix Login bug",
             "description": "auth flow"},
         2: {"key": "SCRUM-2", "summary": "add report", "description": "csv"}}
r = ds.api_list_tickets([], sess, watch)
check("無 q:全列", r["count"] == 2 and "filter" not in r)
r = ds.api_list_tickets([], sess, watch, q="builder", field="profile")
check("field=profile:只 SCRUM-2",
      [t["key"] for t in r["tickets"]] == ["SCRUM-2"]
      and r["filter"]["mode"] == "match")
r = ds.api_list_tickets([], sess, watch, q="LOGIN", field="summary")
check("field=summary + 不分大小寫:只 SCRUM-1",
      [t["key"] for t in r["tickets"]] == ["SCRUM-1"])
r = ds.api_list_tickets([], sess, watch, q="auth", field="desc")
check("field=desc:比對 description", [t["key"] for t in r["tickets"]] == ["SCRUM-1"])
r = ds.api_list_tickets([], sess, watch, q="SCRUM-[0-9]", mode="regex")
check("mode=regex + field=all:兩張都中", r["count"] == 2)
r = ds.api_list_tickets([], sess, watch, q="(bad", mode="regex")
check("無效 regex:count 0 + filter_error", r["count"] == 0 and "filter_error" in r)

# ── db_schema(空表也列欄)──────────────────────────────────────────── #
rt = tempfile.mkdtemp()
st = Store(rt)
st.upsert_session(TicketSession(
    issue_id=1, key="X-1", profile="p", workspace="w", session_id=None,
    attempts=0, outcome=None, pending_reason=None, cost_usd=0.0, base_ref="7"))
st.close()
ds.ROOT = rt
tabs = {t["name"] for t in ds.db_tables()}
check("db_tables:四表齊", {"ticket_session", "ticket_watch", "interactions",
                          "trigger_state"} <= tabs)
cols = {c["name"] for c in ds.db_schema("ticket_session")["columns"]}
check("db_schema:ticket_session 含新欄 base_ref/human_score",
      {"base_ref", "human_score", "clearquest_id"} <= cols)
ie = ds.db_schema("interactions")
check("db_schema:空表也列欄(interactions 含 token)",
      any(c["name"] == "token" for c in ie["columns"]))
check("db_schema:未知表防護", "error" in ds.db_schema("nope"))

print(f"test-api-filter: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
