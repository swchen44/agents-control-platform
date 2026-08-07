#!/usr/bin/env python3
"""W7.2 — 人類完成度評分 單元測(pytest-compatible,亦自跑)。

涵蓋:write_handoff_sections(goal 寫 agent 段、seed human placeholder、尊重既有
human 段、冪等)、collect_score(容錯/範圍)、ScoreGate(抓分→journal+存 session、
未填週期催評、已評分不重抓、非終態不動)。
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp_harness.scoring import (  # noqa: E402
    ScoreGate,
    collect_score,
    write_handoff_sections,
)
from arcp_harness.store import Store, TicketSession  # noqa: E402
from arcp_harness.ticket import Ticket  # noqa: E402


class _Prof:
    def __init__(self, name="p", goal=None):
        self.name = name
        self.goal = goal


class MockSource:
    def __init__(self, desc=""):
        self.desc = desc
        self.comments = []
        self.described = []

    def add_comment(self, iid, text):
        self.comments.append((iid, text))

    def set_description(self, iid, text):
        self.desc = text
        self.described.append(text)


def _ticket(desc="", **kw):
    base = dict(id=1, key="P-1", summary="s", state="x", assignee=None,
                assignee_id=None, description=desc)
    base.update(kw)
    return Ticket(**base)


# -- write_handoff_sections ------------------------------------------------- #
def test_handoff_writes_goal_and_seeds_score():
    src = MockSource()
    t = _ticket(desc="原始需求描述")
    wrote = write_handoff_sections(src, t, _Prof(goal="修好登入逾時並補測試"))
    assert wrote and src.described
    out = src.desc
    assert "owner=agent:p" in out and "修好登入逾時並補測試" in out
    assert "owner=human" in out and "score:" in out
    assert "原始需求描述" in out            # before/after 區塊外原文保留


def test_handoff_respects_existing_human_section():
    # 已有 human 段(含 human_email)→ 不可被覆蓋,也不硬塞 score
    desc = ("<!-- ARCP:sections v1 -->\n"
            "### [ARCP owner=human]\n```yaml\nhuman_email: a@b.com\n```\n"
            "<!-- /ARCP:sections -->\n")
    src = MockSource()
    write_handoff_sections(src, _ticket(desc=desc), _Prof(goal="G"))
    assert "human_email: a@b.com" in src.desc     # 既有 human 內容保留
    assert src.desc.count("owner=human") == 1     # 沒新增第二個 human 段
    assert "owner=agent:p" in src.desc            # goal 段有加


def test_handoff_idempotent():
    src = MockSource()
    t = _ticket(desc="x")
    write_handoff_sections(src, t, _Prof(goal="G"))
    t2 = _ticket(desc=src.desc)                    # 用產出的描述再跑
    wrote = write_handoff_sections(src, t2, _Prof(goal="G"))
    assert wrote is False                          # 無變更 → 不重寫


# -- collect_score ---------------------------------------------------------- #
def _desc_with_score(val):
    return (f"<!-- ARCP:sections v1 -->\n### [ARCP owner=human]\n"
            f"```yaml\nscore: {val}\n```\n<!-- /ARCP:sections -->\n")


def test_collect_score_valid_and_bounds():
    assert collect_score(_desc_with_score(0)) == 0
    assert collect_score(_desc_with_score(10)) == 10
    assert collect_score(_desc_with_score("7")) == 7      # 字串容錯
    assert collect_score(_desc_with_score(11)) is None    # 超範圍
    assert collect_score(_desc_with_score(-1)) is None
    assert collect_score(_desc_with_score("abc")) is None
    assert collect_score(_desc_with_score("")) is None     # 空 placeholder
    assert collect_score("沒有 ARCP 區塊") is None


# -- ScoreGate -------------------------------------------------------------- #
def _sess(root, **kw):
    ws = os.path.join(root, "tickets", "1", "ws")
    os.makedirs(ws, exist_ok=True)
    base = dict(issue_id=1, key="P-1", profile="p", workspace=ws,
                session_id="sid", attempts=1, outcome="SUCCESS",
                pending_reason=None, cost_usd=0.1)
    base.update(kw)
    return TicketSession(**base)


def _jrn(store):
    import json
    return [json.loads(x) for x in open(store.journal_path) if x.strip()]


def test_gate_captures_score():
    root = tempfile.mkdtemp()
    store = Store(root)
    store.upsert_session(_sess(root))
    src = MockSource()
    gate = ScoreGate(src, store)
    ev = gate.on_poll(_ticket(desc=_desc_with_score(8)), store.get_session(1),
                      now=1000.0)
    assert any(e["type"] == "human_score" and e["score"] == 8
               and e["pct"] == 80 for e in ev)
    assert store.get_session(1).human_score == 8            # 存進 session
    assert any("8/10" in c for _, c in src.comments)        # 回饋留言
    store.close()


def test_gate_already_scored_noop():
    root = tempfile.mkdtemp()
    store = Store(root)
    store.upsert_session(_sess(root, human_score=5))
    gate = ScoreGate(MockSource(), store)
    assert gate.on_poll(_ticket(desc=_desc_with_score(9)),
                        store.get_session(1), now=1e9) == []
    assert store.get_session(1).human_score == 5            # 不覆蓋


def test_gate_non_terminal_noop():
    root = tempfile.mkdtemp()
    store = Store(root)
    store.upsert_session(_sess(root, outcome=None))          # 進行中
    gate = ScoreGate(MockSource(), store)
    assert gate.on_poll(_ticket(desc=_desc_with_score(9)),
                        store.get_session(1), now=1e9) == []


def test_gate_reminder_rate_limited():
    root = tempfile.mkdtemp()
    store = Store(root)
    store.upsert_session(_sess(root, score_reminded_at=0.0))
    src = MockSource()
    gate = ScoreGate(src, store, interval_sec=3600)
    t = _ticket(desc="沒填 score")
    # 首次(now 距 0 已 >1h)→ 催 + 記時間
    ev1 = gate.on_poll(t, store.get_session(1), now=10000.0)
    assert any(e["type"] == "score_reminded" for e in ev1)
    # 30 分後 → 不催(未達間隔)
    ev2 = gate.on_poll(t, store.get_session(1), now=10000.0 + 1800)
    assert ev2 == []
    # 再過 1 小時 → 再催
    ev3 = gate.on_poll(t, store.get_session(1), now=10000.0 + 3700)
    assert any(e["type"] == "score_reminded" for e in ev3)
    store.close()


def test_poller_wires_scoregate():
    """整合:OuterLoop.poll_once 對終態未評分的票會呼 ScoreGate 抓分。"""
    from arcp_harness.poller import OuterLoop
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    store.upsert_session(_sess(root))                    # 終態 SUCCESS、未評分
    t = _ticket(desc=_desc_with_score(9), state="To Do")

    class FS(MockSource):
        def search(self, jql, max_results=50):
            return [t]

        def get_comments(self, iid):
            return []

    src = FS()
    loop = OuterLoop(src, store, [], "jql", scoregate=ScoreGate(src, store))
    ev = loop.poll_once()
    assert any(e["type"] == "human_score" and e["score"] == 9 for e in ev)
    assert store.get_session(1).human_score == 9
    store.close()


if __name__ == "__main__":
    ok = True
    for _name, _fn in list(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print(f"  PASS  {_name}")
            except AssertionError as e:
                ok = False
                print(f"  FAIL  {_name}: {e}")
            except Exception as e:  # noqa: BLE001
                ok = False
                print(f"  ERROR {_name}: {type(e).__name__}: {e}")
    print("test-scoring:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
