#!/usr/bin/env python3
"""triage 判不出(select 回 notfound)→ dispatcher 中止(ABORTED)+ Jira 取消。

設計見 docs/design/selection.md。免真 agent:notfound 在建 session 前就 return,不 spawn。"""
from __future__ import annotations

import os
import stat
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from arcp.dispatcher import Dispatcher  # noqa: E402
from arcp.profiles import Profile  # noqa: E402
from arcp.store import Store  # noqa: E402
from arcp.ticket import Ticket  # noqa: E402

ok = fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}")


class FakeSource:
    base_url = "https://x.atlassian.net"

    def __init__(self):
        self.comments = []
        self.transitions = []

    def add_comment(self, iid, body):
        self.comments.append((iid, body))

    def transition(self, iid, cat, prefer_status=None):
        self.transitions.append((iid, cat, prefer_status))
        return True


def _profile(name, select=None):
    return Profile(name=name, workspace_template="empty",
                   workspace_folder="tickets/{issue_id}", skills=[],
                   agent={"backend": "rawcli", "tag": name}, verify=[],
                   max_attempts=2, on_unknown="pending", select=select)


def _tk():
    return Ticket(id=1, key="SCRUM-1", summary="s", state="待辦", assignee=None,
                  assignee_id=None, labels=["arcp.agent"], description="做 X")


d = tempfile.mkdtemp()
nf = os.path.join(d, "nf.sh")
open(nf, "w").write('#!/bin/sh\ncat >/dev/null\n'
                    'echo \'{"profile":"notfound","reason":"沒有適用的 agent"}\'\n')
os.chmod(nf, os.stat(nf).st_mode | stat.S_IEXEC)

root = tempfile.mkdtemp()
store = Store(os.path.join(root, "s"))
src = FakeSource()
main = _profile("triage", {"candidates": ["triage_fast"], "method": "script",
                           "script": nf})
profiles = {"triage": main, "triage_fast": _profile("triage_fast")}
disp = Dispatcher(src, store, profiles, root=root, cancel_status="Cancelled")

ev = disp.handle(_tk(), "triage")

sess = store.get_session(1)
check("session outcome=ABORTED", sess is not None and sess.outcome == "ABORTED")
check("session profile=notfound(推導 + 可查失敗理由)", sess.profile == "notfound")
check("journal aborted(reason=untriageable, detail 帶 reason)",
      any(e["type"] == "aborted" and e["reason"] == "untriageable"
          and "沒有適用" in e.get("detail", "") for e in ev))
check("留言說明中止", any("triage 判不出" in c for _, c in src.comments))
check("Jira transition 帶 cancel_status=Cancelled(優先)",
      src.transitions == [(1, "done", "Cancelled")])
check("沒有 profile_selected(沒選中真 profile)",
      not any(e["type"] == "profile_selected" for e in ev))
store.close()

print(f"test-triage-abort: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
