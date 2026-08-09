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
from arcp.scoring import (  # noqa: E402
    ScoreGate,
    collect_score,
    write_handoff_sections,
)
from arcp.store import Store, TicketSession  # noqa: E402
from arcp.ticket import Ticket  # noqa: E402


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


def test_gate_requests_score_form():
    # W11:終態未評分 + 無既有請求 → 發 score_and_close 表單(@mention+連結)
    root = tempfile.mkdtemp()
    store = Store(root)
    store.upsert_session(_sess(root))
    src = MockSource()
    gate = ScoreGate(src, store, base_url="http://x:8790", mention="acc-1")
    ev = gate.on_poll(_ticket(summary="修 login"), store.get_session(1),
                      now=1000.0)
    assert any(e["type"] == "score_requested" for e in ev)
    reqs = store.interactions_for_ticket(1)
    assert len(reqs) == 1 and reqs[0].schema_id == "score_and_close"
    assert reqs[0].payload["grader"] == "SUCCESS"           # 三訊號之一
    _iid, body = src.comments[0]
    assert f"http://x:8790/form/{reqs[0].token}" in body     # 一次性連結
    assert "[~accountid:acc-1]" in body                      # @mention
    # 再 poll:已有請求且未逾期、未達催辦間隔 → 不重發
    ev2 = gate.on_poll(_ticket(summary="修 login"), store.get_session(1),
                       now=1000.0)
    assert ev2 == [] and len(store.interactions_for_ticket(1)) == 1
    store.close()


def test_gate_already_scored_noop():
    root = tempfile.mkdtemp()
    store = Store(root)
    store.upsert_session(_sess(root, human_score=5))
    gate = ScoreGate(MockSource(), store)
    assert gate.on_poll(_ticket(), store.get_session(1), now=1e9) == []
    assert store.get_session(1).human_score == 5            # 不覆蓋


def test_gate_non_terminal_noop():
    root = tempfile.mkdtemp()
    store = Store(root)
    store.upsert_session(_sess(root, outcome=None))          # 進行中
    gate = ScoreGate(MockSource(), store)
    assert gate.on_poll(_ticket(), store.get_session(1), now=1e9) == []
    assert store.interactions_for_ticket(1) == []            # 不發表單


def test_gate_reminder_rate_limited():
    root = tempfile.mkdtemp()
    store = Store(root)
    store.upsert_session(_sess(root))
    src = MockSource()
    gate = ScoreGate(src, store, base_url="http://x", interval_sec=3600)
    t = _ticket(summary="s")
    # 首輪:發表單(score_requested)
    ev0 = gate.on_poll(t, store.get_session(1), now=10000.0)
    assert any(e["type"] == "score_requested" for e in ev0)
    # 距上次 <1h → 不催
    assert gate.on_poll(t, store.get_session(1), now=10000.0 + 1800) == []
    # 過 1 小時 → 催辦(第 1 次)
    ev1 = gate.on_poll(t, store.get_session(1), now=10000.0 + 3700)
    assert any(e["type"] == "score_reminded" and e["reminders"] == 1
               for e in ev1)
    store.close()


def test_gate_stall_after_many_reminders():
    root = tempfile.mkdtemp()
    store = Store(root)
    store.upsert_session(_sess(root))
    src = MockSource()
    gate = ScoreGate(src, store, base_url="http://x", interval_sec=1,
                     stall_after=3)
    t = _ticket(summary="s")
    gate.on_poll(t, store.get_session(1), now=0.0)       # 發表單
    stalled = False
    for i in range(1, 6):
        ev = gate.on_poll(t, store.get_session(1), now=float(i))
        if any(e["type"] == "hil_stalled" for e in ev):
            stalled = True
    assert stalled                                        # 多次無回應 → 記異常
    store.close()


def test_poller_wires_scoregate():
    """整合:OuterLoop.poll_once 對終態未評分的票會呼 ScoreGate 發表單請求。"""
    from arcp.poller import OuterLoop
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    store.upsert_session(_sess(root))                    # 終態 SUCCESS、未評分
    t = _ticket(summary="s", state="To Do")

    class FS(MockSource):
        def search(self, jql, max_results=50):
            return [t]

        def get_comments(self, iid):
            return []

    src = FS()
    loop = OuterLoop(src, store, [], "jql",
                     scoregate=ScoreGate(src, store, base_url="http://x"))
    ev = loop.poll_once()
    assert any(e["type"] == "score_requested" for e in ev)
    assert len(store.interactions_for_ticket(1)) == 1
    store.close()


# -- auto_close(profile 政策)-------------------------------------------- #
class _AProf:
    def __init__(self, name="p", auto_close="off"):
        self.name = name
        self.auto_close = auto_close


class _ACSource(MockSource):
    def __init__(self):
        super().__init__()
        self.transitioned = []

    def transition(self, iid, cat):
        self.transitioned.append((iid, cat))
        return True


def _term_sess(outcome="SUCCESS", agent_score=8):
    return TicketSession(issue_id=1, key="P-1", profile="p", workspace="w",
                         session_id="s", attempts=1, outcome=outcome,
                         pending_reason=None, cost_usd=0.0,
                         agent_score=agent_score)


def _sg(src, store, profiles):
    return ScoreGate(src, store, base_url="http://x",
                     profiles_fn=lambda: profiles)


def test_autoclose_on_success_closes_success():
    store = Store(tempfile.mkdtemp())
    s = _term_sess("SUCCESS", 9); store.upsert_session(s)
    src = _ACSource()
    ev = _sg(src, store, {"p": _AProf(auto_close="on_success")}).on_poll(
        _ticket(), store.get_session(1))
    assert src.transitioned == [(1, "done")]              # 轉 Done
    assert store.get_session(1).human_score == 9          # human=agent 自評
    assert any(e["type"] == "closed" and e["by"] == "auto" for e in ev)
    # 沒發 score_and_close 表單
    assert not [r for r in store.interactions_for_ticket(1)
                if r.schema_id == "score_and_close"]
    store.close()


def test_autoclose_on_success_failure_goes_hil():
    store = Store(tempfile.mkdtemp())
    store.upsert_session(_term_sess("FAILURE", 3))
    src = _ACSource()
    _sg(src, store, {"p": _AProf(auto_close="on_success")}).on_poll(
        _ticket(), store.get_session(1))
    assert src.transitioned == []                          # 失敗不自動關
    assert store.get_session(1).human_score is None        # 沒評分
    assert [r for r in store.interactions_for_ticket(1)
            if r.schema_id == "score_and_close"]           # 走 HIL 發表單
    store.close()


def test_autoclose_all_closes_failure_outcome_preserved():
    store = Store(tempfile.mkdtemp())
    store.upsert_session(_term_sess("FAILURE", 2))
    src = _ACSource()
    ev = _sg(src, store, {"p": _AProf(auto_close="all")}).on_poll(
        _ticket(), store.get_session(1))
    assert src.transitioned == [(1, "done")]               # all → 失敗也關
    assert store.get_session(1).outcome == "FAILURE"       # outcome 保留(KPI 誠實)
    assert any(e["type"] == "closed" and e["by"] == "auto"
               and e["outcome"] == "FAILURE" for e in ev)
    store.close()


def test_autoclose_off_default_goes_hil():
    store = Store(tempfile.mkdtemp())
    store.upsert_session(_term_sess("SUCCESS", 8))
    src = _ACSource()
    _sg(src, store, {"p": _AProf(auto_close="off")}).on_poll(
        _ticket(), store.get_session(1))
    assert src.transitioned == []                          # off → 不自動關
    assert [r for r in store.interactions_for_ticket(1)
            if r.schema_id == "score_and_close"]           # 走 HIL
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
