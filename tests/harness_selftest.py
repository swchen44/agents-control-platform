#!/usr/bin/env python3
"""Zero-cost harness self-tests: routing semantics, config guards, command
channel logic. No network, no tokens. Run: python3 harness_selftest.py"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))

from arcp.commands import CommandHandler, parse  # noqa: E402
from arcp.paths import config_dir  # noqa: E402
from arcp.profiles import load_profiles  # noqa: E402
from arcp.routing import ConfigError, load_config, match  # noqa: E402
from arcp.store import Store, TicketSession  # noqa: E402
from arcp.ticket import Comment, Ticket  # noqa: E402

ok = fail = 0


def check(name: str, cond: bool) -> None:
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}")


def t(**kw) -> Ticket:
    base = dict(id=1, key="SCRUM-9", summary="", state="待辦", assignee=None,
                assignee_id=None, labels=[], description="", comments=[])
    base.update(kw)
    return Ticket(**base)


print("routing (using the real routes.yaml):")
# 這些 route 斷言綁定「真 routes.yaml」的內容,固定讀它(不跟 ARCP_CONFIG,
# 否則 CI 的 routes.example.yaml 缺這些 route 會誤判失敗)。
_, routes = load_config(os.path.join(config_dir(), "routes.yaml"))
check("label agent -> agent-labeled(notify_only)",
      (r := match(t(labels=["agent"]), routes)) is not None
      and r.name == "agent-labeled" and r.on_match == "notify_only")
check("label no-agent 優先於其他 -> ignore",
      match(t(labels=["no-agent", "agent"]), routes).on_match == "ignore")
check("summary bug -> triage-keyword",
      match(t(summary="fix login bug"), routes).name == "triage-keyword")
check("無條件命中 -> None", match(t(summary="plain"), routes) is None)
check("label filechain -> create_or_resume",
      match(t(labels=["filechain"]), routes).on_match == "create_or_resume")

print("config guards (fail-fast):")
with tempfile.TemporaryDirectory() as tmp:
    bad = os.path.join(tmp, "bad.yaml")
    open(bad, "w").write(
        "outer_loop:\n  routes:\n    - name: x\n      steps: [a, b]\n")
    try:
        load_config(bad); check("steps: 越界被拒(C1 護欄)", False)
    except ConfigError:
        check("steps: 越界被拒(C1 護欄)", True)
    bad2 = os.path.join(tmp, "bad2.yaml")
    open(bad2, "w").write(
        "inner_loop:\n  profiles:\n    p:\n      agent: {backend: x}\n"
        "      loop: {on_unknown: retry}\n")
    try:
        load_profiles(bad2); check("on_unknown: retry 被拒(v5 D3)", False)
    except ConfigError:
        check("on_unknown: retry 被拒(v5 D3)", True)

print("command parse:")
check("run/RETRY/hold/cancel 解析",
      parse("@agent run now") == "run" and parse("@Agent RETRY") == "retry"
      and parse("@agent hold") == "stop" and parse("@agent cancel x") == "cancel")
check("@agent dance -> unknown", parse("@agent dance") == "unknown")
check("[agent] 自家留言不解析", parse("[agent] ack: run") is None)
check("一般留言不解析", parse("just a note about @agent stuff") is None)

print("command channel semantics (FakeSource, tmp store):")


class FakeSource:
    def __init__(self):
        self.comments: list[tuple[int, str]] = []

    def add_comment(self, issue_id, text):
        self.comments.append((issue_id, text))


with tempfile.TemporaryDirectory() as tmp:
    store = Store(tmp)
    src = FakeSource()
    h = CommandHandler(src, store, allowed_commenters=["Boss"])
    tk = t(id=7)
    c_bad = Comment(id=1, author="Rando", author_id="r1",
                    body="@agent cancel", created="")
    h.handle(tk, c_bad)
    check("白名單外 -> 拒絕留言、session 不變",
          "未授權" in src.comments[-1][1] and store.get_session(7) is None)
    store.upsert_session(TicketSession(
        issue_id=7, key="SCRUM-9", profile="p", workspace="/w",
        session_id="s", attempts=2, outcome="UNKNOWN",
        pending_reason="unknown", cost_usd=0.1))
    h.handle(tk, Comment(id=2, author="Boss", author_id="b1",
                         body="@agent retry", created=""))
    s = store.get_session(7)
    check("retry 解除 pending 並歸零 attempts",
          s.outcome is None and s.pending_reason is None and s.attempts == 0)
    h.handle(tk, Comment(id=3, author="Boss", author_id="b1",
                         body="@agent cancel", created=""))
    check("cancel -> ABORTED", store.get_session(7).outcome == "ABORTED")
    h.handle(tk, Comment(id=4, author="Boss", author_id="b1",
                         body="@agent dance", created=""))
    check("不認得的指令收到說明", "可用" in src.comments[-1][1])
    store.close()

print("external-change policy:")
from arcp.commands import ExternalChangePolicy  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    store = Store(tmp)
    src = FakeSource()
    pol = ExternalChangePolicy(src, store, cancel_states=["完成"])
    store.upsert_session(TicketSession(
        issue_id=8, key="SCRUM-8", profile="p", workspace="/w",
        session_id=None, attempts=1, outcome="UNKNOWN",
        pending_reason="unknown", cost_usd=0))
    pol.on_status_changed(t(id=8), "完成")
    check("out-of-band 關票 -> ABORTED",
          store.get_session(8).outcome == "ABORTED")
    store.upsert_session(TicketSession(
        issue_id=9, key="SCRUM-9", profile="p", workspace="/w",
        session_id=None, attempts=1, outcome=None,
        pending_reason=None, cost_usd=0))
    pol.on_assignee_changed(t(id=9))
    s9 = store.get_session(9)
    check("assignee 改走 -> 告警提醒(W11:不改回、不動狀態)",
          s9.pending_reason is None and s9.inactive is False
          and "表單" in src.comments[-1][1])
    store.close()

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
