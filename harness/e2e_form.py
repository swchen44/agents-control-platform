#!/usr/bin/env python3
"""W11 互動服務端到端(fake Jira source + 真 HTTP 打整條:form_server → process_
submission → on_submit=hil.apply_submission → store/source)。

對應瀏覽器手測的 6 場景,固化成可重跑回歸:score_and_close(close 轉 Done)、need_info
(resume)、decision(per-request options)、Jira down(暫勿送出/不落地,不做 queue)、
單次性(重開唯讀)、逾期(410)。**不碰真 Jira**(fake source 記錄 comment/desc/transition)。
"""

from __future__ import annotations

import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from arcp import hil
from arcp.form_server import FormServer
from arcp.interaction import build_request
from arcp.store import Store, TicketSession

_ok = True


def check(name: str, cond: bool) -> None:
    global _ok
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    _ok = _ok and bool(cond)


class _FT:
    def __init__(self, desc: str):
        self.description = desc


class FakeSource:
    """記錄 harness→Jira 的寫入(comment/desc/transition),供斷言。"""

    def __init__(self):
        self.comments: list[tuple] = []
        self.descs: dict[int, str] = {}
        self.transitions: list[tuple] = []

    def add_comment(self, iid, text):
        self.comments.append((iid, text))

    def get_ticket(self, iid, **kw):
        return _FT(self.descs.get(int(iid), ""))

    def set_description(self, iid, text):
        self.descs[int(iid)] = text

    def transition(self, iid, cat):
        self.transitions.append((int(iid), cat))
        return True


def _get(base, token):
    try:
        r = urllib.request.urlopen(f"{base}/form/{token}", timeout=5)
        return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _post(base, token, data):
    body = urllib.parse.urlencode(data).encode()
    try:
        r = urllib.request.urlopen(f"{base}/form/{token}", data=body, timeout=5)
        return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def main() -> int:
    root = tempfile.mkdtemp()
    store = Store(root)
    src = FakeSource()
    src.descs[10024] = "原始需求:修 login 逾時 bug"
    jira_up = {"v": True}

    def _sess(iid, key, **kw):
        b = dict(issue_id=iid, key=key, profile="p", workspace="/ws",
                 session_id="s", attempts=2, outcome=None,
                 pending_reason=None, cost_usd=0.0)
        b.update(kw)
        store.upsert_session(TicketSession(**b))

    _sess(10024, "SCRUM-25", outcome="SUCCESS")
    _sess(10025, "SCRUM-26", pending_reason="need-info")
    _sess(10026, "SCRUM-27", pending_reason="triage")

    r_score = build_request(10024, "SCRUM-25", "score_and_close", ttl_sec=3600,
                            payload={"title": "修 login 逾時 bug",
                                     "grader": "SUCCESS", "agent_score": 8,
                                     "question": "請評分並裁決"})
    r_need = build_request(10025, "SCRUM-26", "need_info", ttl_sec=3600,
                           payload={"question": "缺哪個 API key?"})
    r_dec = build_request(10026, "SCRUM-27", "decision", ttl_sec=3600,
                          payload={"question": "選哪個 agent?",
                                   "options": ["reviewer", "fixer"]})
    r_exp = build_request(10024, "SCRUM-25", "need_info", ttl_sec=10,
                          now=1.0)                    # now 遠古 → 逾期
    for r in (r_score, r_need, r_dec, r_exp):
        store.upsert_interaction(r)

    srv = FormServer(store, host="127.0.0.1", port=0,
                     jira_health_fn=lambda: jira_up["v"],
                     on_submit=lambda r: hil.apply_submission(src, store, r))
    srv.start()
    base = f"http://127.0.0.1:{srv.port}"
    try:
        # 1) score_and_close:表單有三訊號 + 欄位
        st, html = _get(base, r_score.token)
        check("score 表單:三訊號 + 欄位",
              st == 200 and "grader=SUCCESS" in html and "agent 自評=8" in html
              and "human_score" in html and "close_decision" in html)
        # 2) close 提交 → 已提交 + 後端效果(轉 Done/回寫/評分)
        st, html = _post(base, r_score.token,
                         {"human_score": "9", "close_decision": "close",
                          "note": "LGTM"})
        check("score close:已提交頁", st == 200 and "已提交" in html)
        check("score close:系統轉 Jira Done", (10024, "done") in src.transitions)
        check("score close:稽核 comment",
              any("已收到表單回填" in c for _i, c in src.comments))
        check("score close:回寫 human 段(updated+值,區塊外原文保留)",
              "owner=human" in src.descs[10024] and "human_score: 9" in
              src.descs[10024] and "原始需求" in src.descs[10024])
        check("score close:session.human_score=9",
              store.get_session(10024).human_score == 9)
        rr = store.get_interaction(r_score.token)
        check("score close:interaction=submitted",
              rr.status == "submitted" and rr.submission["human_score"] == 9)
        # 3) need_info:提交 → resume(pending 清空)
        st, html = _post(base, r_need.token, {"answer": "用 env KEY"})
        check("need_info:已提交", st == 200 and "已提交" in html)
        check("need_info:resume(pending_reason 清空)",
              store.get_session(10025).pending_reason is None)
        # 4) decision:per-request options 驗證(非法選項擋、合法過)
        st, html = _post(base, r_dec.token, {"choice": "bogus"})
        check("decision:非法選項被擋 + 回表單", st == 200 and "非合法選項" in html)
        st, html = _post(base, r_dec.token, {"choice": "reviewer"})
        check("decision:合法選項提交",
              st == 200 and "已提交" in html
              and store.get_interaction(r_dec.token).status == "submitted")
        # 5) Jira down:暫勿送出 + 送出→稍後再試 + 不落地
        jira_up["v"] = False
        r_down = build_request(10026, "SCRUM-27", "need_info", ttl_sec=3600)
        store.upsert_interaction(r_down)
        st, html = _get(base, r_down.token)
        check("Jira down:表單顯示暫勿送出", "暫勿送出" in html)
        st, html = _post(base, r_down.token, {"answer": "x"})
        check("Jira down:送出→稍後再試 + 不落地",
              "稍後再試" in html
              and store.get_interaction(r_down.token).status == "pending")
        jira_up["v"] = True
        # 6) 單次性 + 逾期
        st, html = _get(base, r_score.token)
        check("單次性:重開已提交→唯讀(無表單)",
              st == 200 and "已提交" in html and "<form" not in html)
        st, html = _get(base, r_exp.token)
        check("逾期:410 + 已逾期(無表單)",
              st == 410 and "已逾期" in html and "<form" not in html)
        # 7) 無效 token → 404
        st, _ = _get(base, "bogus-token")
        check("無效 token → 404", st == 404)
    finally:
        srv.stop()
        store.close()

    print("e2e-form:", "PASS" if _ok else "FAIL")
    return 0 if _ok else 1


if __name__ == "__main__":
    sys.exit(main())
