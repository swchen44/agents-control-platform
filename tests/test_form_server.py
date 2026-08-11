"""W11.3:表單 HTTP 服務測試(render / process_submission / 端到端 token 流程)。"""

from __future__ import annotations

import sys
import tempfile
import urllib.parse
import urllib.request

from arcp.form_server import (
    FormServer,
    process_submission,
    render_form_page,
)
from arcp.interaction import SUBMITTED, build_request
from arcp.store import Store


def _store_with(req):
    st = Store(tempfile.mkdtemp())
    st.upsert_interaction(req)
    return st


def test_render_form_has_fields_and_ctx():
    req = build_request(10024, "SCRUM-25", "score_and_close",
                        payload={"title": "修 bug", "question": "評分並裁決",
                                 "grader": "SUCCESS", "agent_score": 7})
    html = render_form_page(req, jira_up=True)
    assert "SCRUM-25" in html and "修 bug" in html
    assert "human_score" in html and "close_decision" in html
    assert "grader=SUCCESS" in html and "7" in html      # 三訊號對照


def test_render_form_jira_down_warns():
    req = build_request(1, "P-1", "need_info", payload={"question": "補資訊"})
    assert "暫勿送出" in render_form_page(req, jira_up=False)
    assert "暫勿送出" not in render_form_page(req, jira_up=True)


def test_process_valid_persists_and_calls_hook():
    req = build_request(1, "P-1", "score_and_close", now=1.0)
    st = _store_with(req)
    hit = []
    ok, errs = process_submission(
        st, req, {"human_score": "8", "close_decision": "close"},
        jira_up=True, on_submit=lambda r: hit.append(r.request_id), now=2.0)
    assert ok and not errs and hit == [req.request_id]
    again = st.get_interaction(req.token)
    assert again.status == SUBMITTED and again.submission["human_score"] == 8
    st.close()


def test_owner_gate_wired():
    """K:FormServer._gate 從 store 取該票 owner_email 做門禁(選填);
    admin_emails_fn 豁免;無 owner_email 的票放行。"""
    from arcp.store import TicketSession

    def _sess(iid, key, owner):
        return TicketSession(
            issue_id=iid, key=key, profile="default", workspace="ws",
            session_id=None, attempts=0, outcome=None, pending_reason=None,
            cost_usd=0.0, owner_email=owner)

    req = build_request(10030, "SCRUM-30", "need_info", payload={"question": "q"})
    st = _store_with(req)
    st.upsert_session(_sess(10030, "SCRUM-30", "boss@x.com"))
    form = FormServer(st, port=0, admin_emails_fn=lambda: ["admin@x.com"])
    assert form._gate(req, "boss@x.com")[0] is True      # 負責人本人
    assert form._gate(req, "  BOSS@X.com ")[0] is True   # 正規化
    assert form._gate(req, "admin@x.com")[0] is True     # 管理者豁免
    assert form._gate(req, "stranger@x.com")[0] is False  # 非授權擋下
    # 無 owner_email 的票 → 門禁未啟用(放行)
    req2 = build_request(10031, "SCRUM-31", "need_info",
                         payload={"question": "q"})
    st.upsert_interaction(req2)
    st.upsert_session(_sess(10031, "SCRUM-31", None))
    assert form._gate(req2, "anyone@x.com")[0] is True
    st.close()


def test_submitted_ip_audited():
    """K:process_submission 記提交 email + 來源 IP(稽核追查)。"""
    req = build_request(1, "P-1", "need_info", now=1.0)
    st = _store_with(req)
    ok, _ = process_submission(st, req, {"answer": "x"}, jira_up=True,
                               by="u@x.com", ip="203.0.113.7", now=2.0)
    got = st.get_interaction(req.token)
    assert ok and got.submitted_by == "u@x.com"
    assert got.submitted_ip == "203.0.113.7"     # 來源 IP 落地
    st.close()


def test_process_jira_down_does_not_persist():
    req = build_request(1, "P-1", "need_info", now=1.0)
    st = _store_with(req)
    ok, errs = process_submission(st, req, {"answer": "ok"},
                                  jira_up=False, now=2.0)
    assert not ok and any("稍後再試" in e for e in errs)
    assert st.get_interaction(req.token).status != SUBMITTED   # 未落地
    st.close()


def test_process_invalid_and_expired():
    req = build_request(1, "P-1", "score_and_close", now=1.0)
    st = _store_with(req)
    ok, errs = process_submission(st, req, {"human_score": "99"},
                                  jira_up=True, now=2.0)
    assert not ok and errs
    # 逾期
    req2 = build_request(1, "P-1", "need_info", ttl_sec=10, now=1.0)
    st.upsert_interaction(req2)
    ok, errs = process_submission(st, req2, {"answer": "x"},
                                  jira_up=True, now=999.0)
    assert not ok and any("逾期" in e or "失效" in e for e in errs)
    st.close()


def test_http_end_to_end():
    req = build_request(10024, "SCRUM-25", "need_info",
                        payload={"question": "缺哪個 API key?"}, now=1.0)
    st = _store_with(req)
    srv = FormServer(st, host="127.0.0.1", port=0)
    srv.start()
    try:
        base = f"http://127.0.0.1:{srv.port}/form/{req.token}"
        g = urllib.request.urlopen(base, timeout=5).read().decode()
        assert "缺哪個 API key?" in g and "<form" in g
        assert "你是誰(email)" in g            # K:HIL 表單有 email 必填欄
        body = urllib.parse.urlencode({"answer": "用 env ANTHROPIC_API_KEY",
                                       "by": "tester@x.com"})
        p = urllib.request.urlopen(base, data=body.encode(),
                                   timeout=5).read().decode()
        assert "已提交" in p
        # 再開 → 唯讀已提交(單次)
        g2 = urllib.request.urlopen(base, timeout=5).read().decode()
        assert "已提交" in g2 and "<form" not in g2
        assert st.get_interaction(req.token).status == SUBMITTED
        # 無效 token → 404 頁
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{srv.port}/form/bogus", timeout=5)
            raise AssertionError("bogus token 應 404")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        srv.stop()
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
    print("test-form-server:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
