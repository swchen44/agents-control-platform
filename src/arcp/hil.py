"""W11.4 HIL 整合膠水:發起人類互動(@mention + 一次性連結)+ 套用提交(回寫 + resume)。

把互動服務(interaction/form_server)接進 harness 生命週期,但把「膠水」獨立成純函式
(依賴注入 source/store),可單元測試、不綁 poller。poller/scoring 的實際接線在後續。
"""

from __future__ import annotations

import datetime
import time

import yaml

from .interaction import InteractionRequest, build_request, summarize
from .logutil import get_logger
from .sections import Section, parse, render
from .workspace import append_human_instruction

log = get_logger("hil")


def form_link(base_url: str, token: str) -> str:
    return f"{base_url.rstrip('/')}/form/{token}"


def request_human(source, store, issue_id: int, key: str, schema_id: str, *,
                  question: str = "", payload_extra: dict | None = None,
                  base_url: str = "", mention: str = "", ttl_sec: float = 0.0,
                  now: float | None = None) -> InteractionRequest:
    """建立一次性請求 → 貼 @mention comment(含連結)→ 記 journal。回 request。

    通知只用 comment + @mention(不動 assignee、不轉 state)。mention 為 accountId。
    """
    payload = {"question": question}
    if payload_extra:
        payload.update(payload_extra)
    req = build_request(issue_id, key, schema_id, payload=payload,
                        ttl_sec=ttl_sec, now=now)
    store.upsert_interaction(req)
    at = f"[~accountid:{mention}] " if mention else ""
    link = form_link(base_url, req.token)
    source.add_comment(
        issue_id,
        f"[agent] {at}需要你處理:{question or schema_id}\n"
        f"請填此一次性連結(單次有效):{link}\nRequest ID:{req.request_id}")
    store.journal("hil_requested", issue_id, key, schema=schema_id,
                  request_id=req.request_id)
    log.info("hil requested ticket=%s schema=%s req=%s",
             key, schema_id, req.request_id)
    return req


def _write_human_section(source, issue_id: int, data: dict, now_iso: str) -> None:
    """把表單結果寫進 description 的 human 段(updated=日期;系統寫,單一寫入者)。"""
    t = source.get_ticket(issue_id)
    desc = (getattr(t, "description", "") if t else "") or ""
    before, secs, after = parse(desc)
    body = yaml.safe_dump(data, allow_unicode=True,
                          default_flow_style=False).strip()
    for s in secs:
        if s.owner == "human":
            s.body, s.updated = body, now_iso
            break
    else:
        secs.append(Section(owner="human", body=body, updated=now_iso))
    source.set_description(issue_id, render(before, secs, after))


def apply_submission(source, store, req: InteractionRequest, *,
                     now: float | None = None) -> list[dict]:
    """提交後:回寫 human 段 + 稽核 comment + 觸發 resume/評分/關單。回 journal 事件。

    need_info/decision(HIL Middle)→ 清 pending/inactive 讓下輪 resume;
    score_and_close(HIL End)→ 記 human_score;close=系統轉 Done、continue=解終態+
    重置額度回 running。
    """
    now = time.time() if now is None else now
    iso = datetime.datetime.fromtimestamp(now).isoformat(timespec="seconds")
    data = req.submission or {}
    evs: list[dict] = []
    _write_human_section(source, req.issue_id, data, iso)
    source.add_comment(
        req.issue_id,
        f"[agent] 已收到表單回填({req.schema_id}):"
        f"{summarize(req.schema_id, data)}"
        f"(by {req.submitted_by or '—'};Request {req.request_id})")
    sess = store.get_session(req.issue_id)
    if sess is not None:
        # Q10:人類補充指示 → 累加寫進 workspace 人類指示段(agent resume 後重讀)
        hp = (data.get("human_prompt") or "").strip()
        if hp and sess.workspace and sess.workspace not in ("(adopted)", "(handoff)"):
            try:
                append_human_instruction(sess.workspace, hp, now=now)
            except OSError as e:      # workspace 不在也不擋提交(降級)
                log.warning("寫人類指示失敗 ticket=%s: %s", req.key, e)
        if req.schema_id == "score_and_close":
            sc = data.get("human_score")
            if sc is not None:
                sess.human_score = int(sc)
            if data.get("close_decision") == "continue":
                sess.outcome = None            # 解終態(HIL End 路徑 B)
                sess.pending_reason = None
                sess.attempts = 0              # 重置額度:人明確叫續做
                store.upsert_session(sess)
                evs.append(store.journal(
                    "hil_resumed", req.issue_id, req.key, reason="continue",
                    request_id=req.request_id))
            else:                              # close:人授權 → 系統轉 Done
                store.upsert_session(sess)
                # transition() 比對 statusCategory key(小寫 new/indeterminate/
                # done),非狀態名——真 Jira curl 測抓到:須傳 "done" 非 "Done"
                if source.transition(req.issue_id, "done"):
                    evs.append(store.journal(       # closed(有別於 SUCCESS 的
                        "closed", req.issue_id, req.key, by="human",  # resolved)
                        request_id=req.request_id))
        else:                                  # need_info / decision → resume
            sess.pending_reason = None
            sess.inactive = False
            store.upsert_session(sess)
            evs.append(store.journal(
                "hil_resumed", req.issue_id, req.key, schema=req.schema_id,
                request_id=req.request_id))
    evs.append(store.journal("hil_submitted", req.issue_id, req.key,
                             schema=req.schema_id, request_id=req.request_id))
    log.info("hil applied ticket=%s schema=%s req=%s",
             req.key, req.schema_id, req.request_id)
    return evs
