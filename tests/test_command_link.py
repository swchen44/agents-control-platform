#!/usr/bin/env python3
"""指令台連結佈建:provision_command_link 寫進 description control 段 + 指路 comment
+ 建 command token + journal;冪等;approval 重建 control 段時保留 command_console。"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from arcp.approval import ApprovalGate  # noqa: E402
from arcp.hil import provision_command_link  # noqa: E402
from arcp.profiles import Profile  # noqa: E402
from arcp.sections import parse  # noqa: E402
from arcp.store import Store, TicketSession  # noqa: E402

ok = fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}")


class _T:
    def __init__(self, iid, desc):
        self.id = iid; self.key = f"P-{iid}"; self.description = desc


class FakeSource:
    def __init__(self, desc=""):
        self.desc = {1: desc}
        self.comments = []

    def get_ticket(self, iid):
        return _T(iid, self.desc.get(iid, ""))

    def set_description(self, iid, text):
        self.desc[iid] = text

    def add_comment(self, iid, text):
        self.comments.append((iid, text))

    def assign(self, iid, acct):
        pass


# ── 佈建:寫 control 段 + 指路 comment + token + journal ─────────────────── #
st = Store(tempfile.mkdtemp())
src = FakeSource("登入頁在 Safari 崩潰")
evs = provision_command_link(src, st, 1, "P-1", "http://host:8790")
tok = st.get_command_interaction(1)
check("token 建立(kind=command)", tok is not None and tok.kind == "command")
check("description 寫入 command_console + 連結",
      "command_console:" in src.desc[1] and tok.token in src.desc[1])
check("control 段存在", "owner=control" in src.desc[1])
check("原始描述保留", "Safari" in src.desc[1])
check("指路 comment(含連結)",
      any("指令台" in c and tok.token in c for _, c in src.comments))
check("journal command_link_posted",
      any(e["type"] == "command_link_posted" for e in evs))

# 冪等:第二次 no-op、不重貼
evs2 = provision_command_link(src, st, 1, "P-1", "http://host:8790")
check("冪等:第二次無事件 + 只一行 command_console",
      evs2 == [] and src.desc[1].count("command_console:") == 1
      and len(src.comments) == 1)


# ── approval 重建 control 段時保留 command_console ──────────────────────── #
prof = Profile(name="p", workspace_template="empty",
               workspace_folder="tickets/{issue_id}", skills=[],
               agent={"backend": "rawcli"}, verify=[], max_attempts=2,
               on_unknown="pending", require_approval=True, approver="APPR")
sess = TicketSession(issue_id=1, key="P-1", profile="p", workspace="ws",
                     session_id=None, attempts=0, outcome=None,
                     pending_reason=None, cost_usd=0.0)
ag = ApprovalGate(src, st, "BOT")
ag._write_plan(src.get_ticket(1), prof, sess, form_url="")
_before, secs, _after = parse(src.desc[1])
ctrl = next((s for s in secs if s.owner == "control"), None)
check("approval 寫 plan 後 control 有 plan(profile 行)",
      ctrl is not None and ctrl.data().get("profile") == "p")
check("approval 保留 command_console(連結還在)",
      ctrl is not None and tok.token in (ctrl.data().get("command_console") or ""))

st.close()
print(f"test-command-link: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
