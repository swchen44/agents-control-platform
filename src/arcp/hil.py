"""W11.4 HIL 整合膠水:發起人類互動(@mention + 一次性連結)+ 套用提交(回寫 + resume)。

把互動服務(interaction/form_server)接進 harness 生命週期,但把「膠水」獨立成純函式
(依賴注入 source/store),可單元測試、不綁 poller。poller/scoring 的實際接線在後續。
"""

from __future__ import annotations

import datetime
import time

import yaml

from .interaction import InteractionRequest, build_request, summarize
from .jira_source import mention_tag_of
from .logutil import get_logger
from .sections import Section, parse, render
from .store import TicketSession
from .workspace import append_human_instruction

log = get_logger("hil")


def form_link(base_url: str, token: str) -> str:
    return f"{base_url.rstrip('/')}/form/{token}"


def provision_command_link(source, store, issue_id: int, key: str,
                           base_url: str, *, now: float | None = None) -> list:
    """票首次成為 create_or_resume 候選時佈建「指令台」:建綁票常駐 command token、
    把連結寫進 description 的 control 段(command_console:<url>,與 approval 共存)+
    貼一則指路 comment。已佈建 → 冪等不重貼。回 events。取代 @agent comment 通道。"""
    if store.get_command_interaction(issue_id) is not None:
        return []                                     # 已佈建(冪等)
    req = store.get_or_create_command_token(issue_id, key, now=now)
    link = form_link(base_url, req.token)
    t = source.get_ticket(issue_id)
    desc = (getattr(t, "description", "") if t else "") or ""
    before, secs, after = parse(desc)
    if not secs and not after:                        # 原本無 ARCP 區塊 → 原述沉底
        before, after = "", before
    by = {s.owner: s for s in secs}
    ctrl = by.get("control")
    lines = [ln for ln in (ctrl.body.splitlines() if ctrl else [])
             if not ln.strip().startswith("command_console:")]
    lines.append(f"command_console: {link}")
    by["control"] = Section("control", "\n".join(lines).strip())
    source.set_description(issue_id, render(before, list(by.values()), after))
    source.add_comment(
        issue_id,
        f"[agent] 指令台(下 run / retry / hold / stop / cancel / next,含各指令"
        f"說明):{link}\n此連結綁本票、到結案前一直有效;請用它下指令(不必手打留言)。")
    log.info("%s 佈建指令台連結", key)
    return [store.journal("command_link_posted", issue_id, key)]


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
    at = (mention_tag_of(source, mention) + " ") if mention else ""
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


def _handoff_choice(schema_id: str, data: dict) -> bool:
    """表單是否選了 a2a handoff(HIL End close_decision / HIL Middle next_step)。"""
    if schema_id == "score_and_close":
        return data.get("close_decision") == "handoff"
    if schema_id == "decision":
        return data.get("next_step") == "handoff"
    return False


def _do_handoff(source, store, sess: TicketSession, req: InteractionRequest,
                data: dict, profiles: dict, now: float) -> list[dict]:
    """W10.3 a2a handoff:人在 HIL 表單選「改派下一棒」。

    kind=next(同票):reset session、鎖定新 profile、workspace 哨值 → 下輪重 provision
    由新 profile 接手同一張票(prompt 已隨表單寫進 description 人類段 → 新 TICKET.md)。
    kind=base(跨票):系統在同 project 建新票、預建其 session(鎖定新 profile + base_ref
    指回本票)→ 本票轉 ABORTED(交接出去,非失敗)→ 新票下輪由 dispatcher 注入 base 脈絡。
    資料不完整(無 kind / profile 無效)→ fail-safe 降級為續跑原 agent(不硬失敗)。
    """
    kind = (data.get("handoff_kind") or "").strip()
    target = (data.get("next_profile") or "").strip()
    prompt = (data.get("handoff_prompt") or "").strip()
    if req.schema_id == "score_and_close" and data.get("human_score") is not None:
        sess.human_score = int(data["human_score"])   # HIL End 仍評了分,先記
    old = sess.profile

    if kind not in ("next", "base") or (profiles and target not in profiles):
        source.add_comment(req.issue_id, (
            f"[agent] 換手資料不完整(種類={kind or '空'}、"
            f"profile={target or '空'}),改為續跑原 agent «{old}»。"))
        sess.outcome = sess.pending_reason = None      # 解終態 → 下輪 resume 原 agent
        sess.inactive = False
        store.upsert_session(sess)
        return [store.journal("handoff_invalid", req.issue_id, req.key,
                              kind=kind, to=target, via="hil")]

    if kind == "next":
        sess.profile = target
        sess.session_id = None
        sess.attempts = 0
        sess.outcome = sess.pending_reason = None
        sess.inactive = False
        sess.workspace = "(handoff)"                   # 下輪重 provision 新 instance
        store.upsert_session(sess)
        log.info("%s HIL handoff(next)%s → %s", req.key, old, target)
        return [store.journal("handoff", req.issue_id, req.key, kind="next",
                              from_profile=old, to=target, via="hil")]

    # kind == "base":跨票交接
    t = source.get_ticket(req.issue_id)
    project = req.key.rsplit("-", 1)[0]              # 同 project(= agent 自己 project)
    labels = list(getattr(t, "labels", None) or [])  # 沿用本票 labels → 新票同 route
    summary = (getattr(t, "summary", "") or req.key)[:100]
    desc = [f"base: {req.key}",
            f"由 {req.key} 跨票交接(a2a handoff base)建立,交由 «{target}» 接手。"]
    if prompt:
        desc += ["", "## 交接指示", prompt]
    new_t = source.create_ticket(project, f"[base:{req.key}] {summary}",
                                 description="\n".join(desc), labels=labels)
    store.upsert_session(TicketSession(          # 預建新票:鎖定 profile + base_ref
        issue_id=new_t.id, key=new_t.key, profile=target, workspace="(handoff)",
        session_id=None, attempts=0, outcome=None, pending_reason=None,
        cost_usd=0.0, base_ref=str(req.issue_id)))
    sess.outcome = "ABORTED"                            # 本票交接出去=終態(非 FAILURE)
    sess.pending_reason = None
    store.upsert_session(sess)
    source.add_comment(req.issue_id, (
        f"[agent] 跨票換手(cross-ticket,base={req.key}):已建立新票 {new_t.key} "
        f"交由 «{target}» 接手;本票結束(ABORTED,非失敗)。兩票可於 dashboard 對照。"))
    source.add_comment(new_t.id, (
        f"[agent] 本票由 {req.key} 跨票換手(cross-ticket)建立,將由 «{target}» 接手;"
        f"來源脈絡下輪佈建後見 workspace 的 BASE_{req.key}/。"))
    log.info("%s HIL handoff(base)→ 新票 %s by %s", req.key, new_t.key, target)
    return [store.journal("handoff", req.issue_id, req.key, kind="base",
                          from_profile=old, to=target, new_ticket=new_t.key,
                          via="hil")]


def _apply_budget_increase(source, store, sess, req: InteractionRequest,
                           data: dict) -> list:
    """budget_increase 提交:把新 soft 寫進 session(clamp ≤ payload 帶的 hard)、
    解 budget pending → 下輪 resume。回事件。"""
    p = req.payload or {}
    hard_u, hard_t = p.get("hard_usd"), p.get("hard_tokens")
    notes = []
    nu, nt = data.get("new_soft_usd"), data.get("new_soft_tokens")
    if nu is not None:
        v = float(nu)
        if hard_u is not None and v > float(hard_u):
            v = float(hard_u)
            notes.append("USD 封頂到 hard")
        sess.soft_usd = v
    if nt is not None:
        v = int(nt)
        if hard_t is not None and v > int(hard_t):
            v = int(hard_t)
            notes.append("token 封頂到 hard")
        sess.soft_tokens = v
    sess.pending_reason = None                      # 解 budget → 下輪 resume 續跑
    store.upsert_session(sess)
    tail = f"({';'.join(notes)})" if notes else ""
    source.add_comment(req.issue_id, (
        f"[agent] 已提高本票上限{tail}:soft usd="
        f"{sess.soft_usd if sess.soft_usd is not None else '未改'}、soft token="
        f"{sess.soft_tokens if sess.soft_tokens is not None else '未改'};下輪續跑。"))
    log.info("%s budget_increase soft_usd=%s soft_tokens=%s",
             req.key, sess.soft_usd, sess.soft_tokens)
    return [store.journal("hil_resumed", req.issue_id, req.key,
                          reason="budget_increase", request_id=req.request_id)]


def apply_submission(source, store, req: InteractionRequest, *,
                     profiles: dict | None = None,
                     now: float | None = None) -> list[dict]:
    """提交後:回寫 human 段 + 稽核 comment + 觸發 resume/評分/關單/handoff。回事件。

    need_info/decision(HIL Middle)→ 清 pending/inactive 讓下輪 resume;
    score_and_close(HIL End)→ 記 human_score;close=系統轉 Done、continue=解終態+
    重置額度回 running。close_decision/next_step=handoff(W10.3)→ 改派下一棒。
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
        if _handoff_choice(req.schema_id, data):       # W10.3 改派下一棒(優先)
            evs.extend(_do_handoff(source, store, sess, req, data,
                                   profiles or {}, now))
        elif req.schema_id == "score_and_close":
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
                    store.invalidate_ticket_commands(req.issue_id)  # 指令台失效
                    evs.append(store.journal(       # closed(有別於 SUCCESS 的
                        "closed", req.issue_id, req.key, by="human",  # resolved)
                        request_id=req.request_id))
        elif req.schema_id == "budget_increase":   # 自助調高本票 soft(≤hard)
            evs.extend(_apply_budget_increase(source, store, sess, req, data))
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
