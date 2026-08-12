#!/usr/bin/env python3
"""C5 粗看:全域跨票時間軸。lane_segments(journal→狀態區段)純函式 +
overview_data(groups/items/tickets 側欄)+ render_timeline_page smoke。
免網、唯讀。pytest-compatible,亦自跑。"""
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


def _ev(ts, typ, iid=1, **kw):
    return {"ts": ts, "type": typ, "issue_id": iid, "key": f"P-{iid}", **kw}


NOW = 1000.0

# ── lane_segments:狀態區段推導 ──────────────────────────────────────── #
segs = ds.lane_segments([
    _ev(100, "session_created"), _ev(110, "attempt_started"),
    _ev(200, "attempt_finished"), _ev(210, "pending", reason="approval"),
], NOW)
check("segments:idle→run→idle→wait(延伸到 now)",
      segs == [(100, 110, "idle"), (110, 200, "run"),
               (200, 210, "idle"), (210, NOW, "wait")])

segs = ds.lane_segments([
    _ev(100, "attempt_started"), _ev(200, "resolved"),
], NOW)
check("segments:resolved 關段(不延伸到 now)", segs == [(100, 200, "run")])

segs = ds.lane_segments([
    _ev(100, "attempt_started"), _ev(200, "resolved"),
    _ev(300, "attempt_started"),                       # retry 再開新段
], NOW)
check("segments:resolved 後 retry 再開段",
      segs == [(100, 200, "run"), (300, NOW, "run")])

segs = ds.lane_segments([_ev(100, "attempt_started")], NOW,
                        outcome="SUCCESS", finished_at=500)
check("segments:outcome+finished_at 截斷", segs == [(100, 500, "run")])

segs = ds.lane_segments([_ev(100, "queued"), _ev(150, "attempt_started")], NOW)
check("segments:queued 段", segs[0] == (100, 150, "queue"))

check("segments:空事件 → 空", ds.lane_segments([], NOW) == [])

# ── overview_data:groups/items/tickets ─────────────────────────────── #
tmp = tempfile.mkdtemp()
ds.ROOT = tmp
st = Store(tmp)
st.upsert_session(TicketSession(
    issue_id=1, key="P-1", profile="alpha", workspace="/w/1",
    session_id="s1", attempts=2, outcome=None, pending_reason="approval",
    cost_usd=0.5, tokens=12000, owner_email_list="a@x.com,b@y.tw"))
st.upsert_session(TicketSession(
    issue_id=2, key="P-2", profile="beta", workspace="/w/2",
    session_id="s2", attempts=1, outcome="SUCCESS", pending_reason=None,
    cost_usd=0.1, finished_at=920.0))
st.upsert_interaction(build_request(1, "P-1", "command", kind="command"))
journal = [
    _ev(100, "session_created", 1), _ev(110, "attempt_started", 1),
    _ev(200, "pending", 1, reason="approval"),
    _ev(50, "session_created", 2), _ev(60, "attempt_started", 2),
    _ev(900, "resolved", 2, outcome="SUCCESS"),
]
sessions = ds.read_sessions()
watch = {1: {"summary": "修 bug"}, 2: {"summary": "寫報告"}}
d = ds.overview_data(journal, sessions, watch, since=0, now=NOW)
check("overview:兩票各一列", {g["id"] for g in d["groups"]} == {1, 2})
bg1 = [i for i in d["items"] if str(i["id"]).startswith("bg1-")]
check("overview:票1 有背景色帶(run→wait)",
      [i["className"] for i in bg1][-1] == "ov-wait"
      and all(i["type"] == "background" for i in bg1))
check("overview:關鍵事件點(🎬/⏸)有畫",
      any(i.get("content") == "🎬" for i in d["items"])
      and any(i.get("content") == "⏸" for i in d["items"]))
check("overview:票2 有 ✔ 終點",
      any(i.get("content") == "✔" and i["group"] == 2 for i in d["items"]))
t1 = d["tickets"]["1"]
check("overview:側欄含 owner_email_list / workspace / 用量",
      t1["owner_email_list"] == "a@x.com,b@y.tw"
      and t1["workspace"] == "/w/1" and t1["tokens"] == 12000)
check("overview:側欄 run_secs=run 段加總", t1["run_secs"] == 200 - 110)
check("overview:OPEN 指令台連結進側欄",
      len(t1["links"]) == 1 and "/form/" in t1["links"][0]["url"])

d2 = ds.overview_data(journal, sessions, watch, since=500, now=NOW)
check("overview:since 窗過濾(票1 最後活動 200 < 500 → 排除;票2 留)",
      {g["id"] for g in d2["groups"]} == {2})
d3 = ds.overview_data(journal, sessions, watch, q="alpha", now=NOW)
check("overview:q 過濾 profile", {g["id"] for g in d3["groups"]} == {1})

# ── C5b 細看:駕駛艙卡 + 抽屜色帶 + 抽屜說明卡 ─────────────────────── #
ws = os.path.join(tmp, "ws1")
os.makedirs(ws, exist_ok=True)
open(os.path.join(ws, "TICKET.md"), "w").write("# 任務\n修好那個 bug")
s1 = dict(sessions[1]); s1["workspace"] = ws
ck = ds._session_cockpit_card(1, s1, [e for e in journal
                                      if e["issue_id"] == 1])
check("駕駛艙:全欄位含 owner/soft 上限/評分",
      "負責人 email(門禁)" in ck and "a@x.com,b@y.tw" in ck
      and "soft USD 上限" in ck and "人類評分" in ck
      and "workspace 路徑" in ck)
check("駕駛艙:執行/等人時間 + 生命期", "執行時間" in ck and "等人時間" in ck
      and "生命期" in ck)
check("駕駛艙:TICKET.md 內容(摺疊)",
      "TICKET.md" in ck and "修好那個 bug" in ck)
ck2 = ds._session_cockpit_card(2, sessions[2], [])
check("駕駛艙:無 TICKET.md → 註明", "尚未佈建或已回收" in ck2)

mg = ds.merged_timeline_data([e for e in journal if e["issue_id"] == 1],
                             1, sessions[1])
check("細看抽屜:疊全高狀態色帶(ov- 同粗看顏色)",
      any(str(i["id"]).startswith("ovbg-")
          and i.get("type") == "background"
          and str(i["className"]).startswith("ov-") for i in mg["items"]))
sec = ds.render_timeline_section([e for e in journal if e["issue_id"] == 1],
                                 1, sessions[1])
check("細看抽屜:說明卡(圖例/操作/判讀)",
      "怎麼看這張圖" in sec and "背景色帶" in sec and "判讀" in sec)

# ── render_timeline_page smoke(說明卡 + 工具列)──────────────────── #
html_out = ds.render_timeline_page(journal, sessions, watch, win="all", q="")
check("page:含說明卡(怎麼看+圖例+判讀)",
      "怎麼看這張圖" in html_out and "判讀範例" in html_out
      and "等人(HIL 表單/審批待填)" in html_out)
check("page:時間窗按鈕 + 過濾表單",
      "win=7" in html_out and "name='q'" in html_out)
check("page:資料島 + vis 資產",
      "ov-data" in html_out and "vis-timeline.min.js" in html_out)
st.close()

print(f"test-timeline-overview: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
