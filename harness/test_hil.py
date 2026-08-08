"""W11.4a:HIL 膠水測試(request_human 通知 / apply_submission 回寫+resume+關單)。"""

from __future__ import annotations

import sys
import tempfile

from arcp_harness.hil import apply_submission, request_human
from arcp_harness.interaction import build_request
from arcp_harness.store import Store, TicketSession


class FakeTicket:
    def __init__(self, desc=""):
        self.description = desc


class FakeSource:
    def __init__(self, desc=""):
        self.comments = []
        self.desc = desc
        self.transitions = []

    def add_comment(self, iid, text):
        self.comments.append((iid, text))

    def get_ticket(self, iid, **kw):
        return FakeTicket(self.desc)

    def set_description(self, iid, text):
        self.desc = text

    def transition(self, iid, cat):
        self.transitions.append((iid, cat))
        return True


def _store():
    return Store(tempfile.mkdtemp())


def _sess(store, iid=1, **kw):
    s = TicketSession(issue_id=iid, key=f"P-{iid}", profile="p",
                      workspace="/ws", session_id="s", attempts=2,
                      outcome=None, pending_reason=None, cost_usd=0.0)
    for k, v in kw.items():
        setattr(s, k, v)
    store.upsert_session(s)
    return s


def test_request_human_notifies_via_comment_link():
    st = _store()
    src = FakeSource()
    req = request_human(src, st, 10024, "SCRUM-25", "need_info",
                        question="缺哪個 key?", base_url="http://x:8790",
                        mention="acc-123", ttl_sec=3600, now=1.0)
    assert st.get_interaction(req.token) is not None      # 已持久
    assert len(src.comments) == 1
    _iid, body = src.comments[0]
    assert "缺哪個 key?" in body
    assert f"http://x:8790/form/{req.token}" in body      # 一次性連結
    assert req.request_id in body
    assert "[~accountid:acc-123]" in body                 # @mention 不動 assignee
    st.close()


def test_apply_need_info_writes_section_and_resumes():
    st = _store()
    src = FakeSource(desc="原始需求")
    _sess(st, 1, pending_reason="need-info", inactive=True)
    req = build_request(1, "P-1", "need_info", now=1.0)
    req.submission = {"answer": "用 env ANTHROPIC_API_KEY"}
    req.submitted_by = "Shao-wei"
    st.upsert_interaction(req)
    evs = apply_submission(src, st, req, now=100.0)
    assert "用 env ANTHROPIC_API_KEY" in src.desc          # 回寫 human 段
    assert "owner=human" in src.desc and "updated=" in src.desc
    assert "原始需求" in src.desc                          # 區塊外不碰
    assert any("已收到表單回填" in b for _i, b in src.comments)   # 稽核 comment
    s = st.get_session(1)
    assert s.pending_reason is None and s.inactive is False   # resume
    assert any(e["type"] == "hil_resumed" for e in evs)
    st.close()


def test_apply_score_close_records_score_and_transitions_done():
    st = _store()
    src = FakeSource()
    _sess(st, 1, outcome="SUCCESS")
    req = build_request(1, "P-1", "score_and_close", now=1.0)
    req.submission = {"human_score": 8, "close_decision": "close"}
    st.upsert_interaction(req)
    evs = apply_submission(src, st, req, now=100.0)
    assert st.get_session(1).human_score == 8
    assert (1, "done") in src.transitions                 # 系統授權轉 Done
    assert any(e["type"] == "closed" for e in evs)
    st.close()


def test_apply_score_continue_untermiantes_and_resets():
    st = _store()
    src = FakeSource()
    _sess(st, 1, outcome="FAILURE", attempts=3)
    req = build_request(1, "P-1", "score_and_close", now=1.0)
    req.submission = {"human_score": 4, "close_decision": "continue"}
    st.upsert_interaction(req)
    evs = apply_submission(src, st, req, now=100.0)
    s = st.get_session(1)
    assert s.human_score == 4
    assert s.outcome is None and s.attempts == 0          # 解終態 + 重置額度
    assert not src.transitions                            # 續跑不關單
    assert any(e["type"] == "hil_resumed" for e in evs)
    st.close()


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
    print("test-hil:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
