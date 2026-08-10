#!/usr/bin/env python3
"""指令核心(取代 @agent comment 通道):apply_command / available_commands /
command token 生命週期。免網:FakeSource 攔 comment。pytest 相容,亦自跑。"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from arcp.commands import apply_command, available_commands  # noqa: E402
from arcp.interaction import INVALIDATED  # noqa: E402
from arcp.store import Store, TicketSession  # noqa: E402

ok = fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}")


class FakeSource:
    def __init__(self):
        self.comments = []

    def add_comment(self, iid, text):
        self.comments.append((iid, text))

    def get_ticket(self, iid):
        return None


class _Prof:
    def __init__(self, name):
        self.name = name
        self.agent = {"backend": "rawcli", "tag": name}


PROFILES = {"main": _Prof("main"), "alt": _Prof("alt")}


def _sess(**kw):
    base = dict(issue_id=1, key="P-1", profile="main", workspace="old-ws",
                session_id="s1", attempts=1, outcome=None,
                pending_reason=None, cost_usd=0.0)
    base.update(kw)
    return TicketSession(**base)


# ── available_commands 依推導狀態 ─────────────────────────────────────── #
check("avail:todo(無 session)→ 空", available_commands(None) == [])
check("avail:running → hold/stop/cancel/next",
      available_commands(_sess()) == ["hold", "stop", "cancel", "next"])
check("avail:queued → stop/cancel/next",
      available_commands(_sess(queued=True)) == ["stop", "cancel", "next"])
check("avail:hil_middle(pending)→ run/retry/cancel/next",
      available_commands(_sess(pending_reason="approval"))
      == ["run", "retry", "cancel", "next"])
check("avail:hil_end(終態)→ retry/cancel/next",
      available_commands(_sess(outcome="SUCCESS"))
      == ["retry", "cancel", "next"])
check("avail:aborted → 空(不再接指令)",
      available_commands(_sess(outcome="ABORTED")) == [])


# ── apply_command:效果 + 稽核 comment + journal ────────────────────────── #
def _fresh():
    st = Store(tempfile.mkdtemp()); src = FakeSource()
    st.upsert_session(_sess(pending_reason="budget"))   # hil_middle → run 可用
    return st, src


st, src = _fresh()
o, msg, evs = apply_command(src, st, PROFILES, 1, "run", by="a@x.tw")
check("run:ok + 清 pending", o and st.get_session(1).pending_reason is None)
check("run:稽核 comment 帶 by",
      any("run" in c and "a@x.tw" in c for _, c in src.comments))
check("run:journal command_accepted(author=email)",
      any(e["type"] == "command_accepted" and e["author"] == "a@x.tw"
          for e in evs))

st, src = _fresh()
apply_command(src, st, PROFILES, 1, "retry", by="a@x.tw")
check("retry:attempts 歸零", st.get_session(1).attempts == 0)

st, src = _fresh()
o, _, _ = apply_command(src, st, PROFILES, 1, "cancel", by="a@x.tw")
check("cancel:outcome=ABORTED", o and st.get_session(1).outcome == "ABORTED")

# running 狀態才可 stop/hold/next
st = Store(tempfile.mkdtemp()); src = FakeSource()
st.upsert_session(_sess())                              # running
o, _, _ = apply_command(src, st, PROFILES, 1, "stop", by="a@x.tw")
check("stop:pending human-decision",
      o and st.get_session(1).pending_reason == "human-decision")

st = Store(tempfile.mkdtemp()); src = FakeSource()
st.upsert_session(_sess())
o, msg, evs = apply_command(src, st, PROFILES, 1, "next",
                            args={"profile": "alt"}, by="a@x.tw")
check("next:換 profile + 重置 session", o
      and st.get_session(1).profile == "alt"
      and st.get_session(1).session_id is None
      and st.get_session(1).workspace == "(handoff)")
check("next:journal handoff(kind=command)",
      any(e["type"] == "handoff" and e.get("kind") == "command" for e in evs))

st = Store(tempfile.mkdtemp()); src = FakeSource()
st.upsert_session(_sess())
o, msg, evs = apply_command(src, st, PROFILES, 1, "next",
                            args={"profile": "nope"}, by="a@x.tw")
check("next:無效 profile → 拒絕、不動", (not o) and evs == []
      and st.get_session(1).profile == "main")

st = Store(tempfile.mkdtemp()); src = FakeSource()
st.upsert_session(_sess())
o, msg, evs = apply_command(src, st, PROFILES, 1, "hold", by="a@x.tw")
check("hold:pending=hold + 開 hold 表單(comment 有連結)", o
      and st.get_session(1).pending_reason == "hold"
      and any("連結" in c or "form" in c for _, c in src.comments))

# 狀態不適用:running 不能 run
st = Store(tempfile.mkdtemp()); src = FakeSource()
st.upsert_session(_sess())                              # running
o, msg, evs = apply_command(src, st, PROFILES, 1, "run", by="a@x.tw")
check("不適用:running 下 run → 拒絕", (not o) and evs == []
      and "不適用" in msg)

# 無 session
st = Store(tempfile.mkdtemp()); src = FakeSource()
o, msg, evs = apply_command(src, st, PROFILES, 99, "cancel", by="a@x.tw")
check("無 session → 拒絕", (not o) and evs == [])


# ── command token 生命週期(綁票、可重複用、close 失效)─────────────────── #
st = Store(tempfile.mkdtemp())
t1 = st.get_or_create_command_token(1, "P-1")
t2 = st.get_or_create_command_token(1, "P-1")
check("token:每票一個(重呼回同一個)", t1.token == t2.token
      and t1.kind == "command")
check("token:可由 token 查回", st.get_interaction(t1.token) is not None)
st.invalidate_ticket_commands(1)
check("token:close 失效 → INVALIDATED",
      st.get_command_interaction(1).status == INVALIDATED)
st.close()

print(f"test-command-core: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
