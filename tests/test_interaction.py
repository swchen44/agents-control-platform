"""W11.1:互動服務核心邏輯單元測試(schema / token / 驗證 / 請求狀態)。"""

from __future__ import annotations

import sys

from arcp.interaction import (
    FORM_SCHEMAS,
    PENDING,
    build_request,
    gen_token,
    summarize,
    validate_submission,
)


def test_schemas_present():
    for sid in ("need_info", "decision", "score_and_close"):
        assert sid in FORM_SCHEMAS
        assert FORM_SCHEMAS[sid]["version"] >= 1
        assert FORM_SCHEMAS[sid]["fields"]


def test_token_strong_and_unique():
    a, b = gen_token(), gen_token()
    assert a != b
    assert len(a) >= 32                       # token_urlsafe(32) 約 43 字


def test_build_request_binding_and_ttl():
    r = build_request(10024, "SCRUM-25", "need_info", ttl_sec=3600, now=1000.0)
    assert r.issue_id == 10024 and r.key == "SCRUM-25"
    assert r.schema_id == "need_info" and r.schema_version >= 1
    assert r.token and r.request_id.startswith("req-")
    assert r.status == PENDING
    assert r.expires_at == 4600.0
    assert not r.is_expired(now=4599.0) and r.is_open(now=4599.0)
    assert r.is_expired(now=4600.0) and not r.is_open(now=4600.0)


def test_build_request_no_ttl_never_expires():
    r = build_request(1, "P-1", "decision", now=1000.0)
    assert r.expires_at == 0.0
    assert not r.is_expired(now=10**12)       # 未設 ttl = 不逾期


def test_build_request_unknown_schema():
    try:
        build_request(1, "P-1", "nope")
    except ValueError:
        return
    raise AssertionError("unknown schema 應擲 ValueError")


def test_validate_required():
    ok, errs, _ = validate_submission("need_info", {"answer": "  "})
    assert not ok and any("必填" in e for e in errs)
    ok, _, cleaned = validate_submission("need_info", {"answer": "補上了"})
    assert ok and cleaned["answer"] == "補上了"


def test_validate_int_range():
    ok, errs, _ = validate_submission(
        "score_and_close", {"human_score": 11, "close_decision": "close"})
    assert not ok and any("0–10" in e for e in errs)
    ok, _, cleaned = validate_submission(
        "score_and_close", {"human_score": "8", "close_decision": "close"})
    assert ok and cleaned["human_score"] == 8 and cleaned["close_decision"] == "close"


def test_validate_select_builtin_and_per_request():
    # 內建 options(score_and_close.close_decision)
    ok, errs, _ = validate_submission(
        "score_and_close", {"human_score": 5, "close_decision": "bogus"})
    assert not ok and any("非合法選項" in e for e in errs)
    # per-request options(decision.choice ← payload.options)
    req = build_request(1, "P-1", "decision",
                        payload={"options": ["A", "B"]}, now=1.0)
    ok, _, cleaned = validate_submission("decision", {"choice": "A"}, req)
    assert ok and cleaned["choice"] == "A"
    ok, errs, _ = validate_submission("decision", {"choice": "Z"}, req)
    assert not ok and any("非合法選項" in e for e in errs)


def test_summarize():
    s = summarize("score_and_close",
                  {"human_score": 8, "close_decision": "close"})
    assert "8" in s and "close" in s


def test_store_roundtrip_and_lookup():
    """W11.2:互動請求持久化——upsert / get_interaction(token)/ 依票查 / open。"""
    import tempfile

    from arcp.interaction import SUBMITTED
    from arcp.store import Store
    st = Store(tempfile.mkdtemp())
    r = build_request(10024, "SCRUM-25", "score_and_close",
                      payload={"grader": "SUCCESS", "agent_score": 7},
                      ttl_sec=3600, now=1000.0)
    st.upsert_interaction(r)
    got = st.get_interaction(r.token)
    assert got is not None and got.request_id == r.request_id
    assert got.issue_id == 10024 and got.schema_id == "score_and_close"
    assert got.payload["grader"] == "SUCCESS" and got.payload["agent_score"] == 7
    assert got.status == PENDING and got.expires_at == 4600.0
    assert st.get_interaction("no-such-token") is None
    # 提交後狀態/內容持久
    got.status = SUBMITTED
    got.submission = {"human_score": 8, "close_decision": "close"}
    got.submitted_by = "Shao-wei"
    st.upsert_interaction(got)
    again = st.get_interaction(r.token)
    assert again.status == SUBMITTED and again.submission["human_score"] == 8
    assert again.submitted_by == "Shao-wei"
    # 依票查 + open 過濾(submitted 不算 open)
    assert len(st.interactions_for_ticket(10024)) == 1
    assert st.open_interactions_for_ticket(10024, now=2000.0) == []
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
    print("test-interaction:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
