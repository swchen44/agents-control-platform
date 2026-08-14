"""指令核心 apply_command + external-change policy (v5 §4.1, §6-10~14)。

人 → agent 的指令走「指令台」表單、自動化走 per-ticket REST API,兩者共用本檔的
apply_command(取代舊 @agent comment 通道 + 白名單;見 docs/design/interaction.md §16)。
指令台頁面/佈建在 form_server/hil;本檔只放純效果核心 + 說明表 + 狀態可用性。

指令語意(對 ticket_session 的狀態操作;dispatch 由同一輪 poll 稍後執行):
  run    解除 pending、續跑(pending 的人工解除機制——含 pending:unknown)
  retry  歸零 attempts + 解除 pending,從頭再試
  hold   立即 evict(killpg)→ HIL(Middle):開 hold 表單給新指示、不耗 attempt
  stop   交還人工:pending:human-decision
  cancel 撤銷:outcome=ABORTED,此後不再派工
  next <profile>  F3 換手:重置 session、鎖定新 profile(dispatcher 以
         session.profile 優先於 route)→ 下輪重新排隊;目標 require_approval
         則重走審批門;workspace 置哨值 → 下輪重 provision(新 instance)

External-change policy(v5 §6-10/11 + W12 假設更新):
  status → 終止類狀態(人在看板上直接關票)= out-of-band 撤銷 → ABORTED
  assignee = 資源開關(DESIGN §6):交人類 → inactive(不再派工、讓出 F1 額度);
  回機器人 → 清 inactive(下輪 resume)。未配置 bot_account_id 時退回舊語義
  (任何 assignee 變更 = 撤銷授權 → pending:external)。
  註:同步架構下 inactive=「不再拉起」(agent 每 attempt 跑完自然釋放進程);
  實時 killpg 長駐 agent 留未來異步架構(§6 完整版)。
"""

from __future__ import annotations

import os

from .identity import normalize_email_list, resolve_user_id
from .jira_source import JiraCloudSource
from .lifecycle_state import canonical_state
from .logutil import get_logger
from .store import Store
from .ticket import Ticket
from .transcript import engine_of_agent
from .transcript import finalize as finalize_transcript

log = get_logger("commands")


def _finalize_leaving(sess, profiles: dict | None, reason: str) -> None:
    """W4.3 離手定格:session 交出去(換手/交人)前產 final HTML(不打包)。"""
    prof = (profiles or {}).get(sess.profile)
    engine = engine_of_agent(prof.agent) if prof is not None else "claude"
    finalize_transcript(sess.session_id, engine, sess.workspace,
                        pack=False, reason=reason)


def _write_evict(sess) -> None:
    """寫 EVICT 檔 → agent 看門狗 killpg(同 control /evict);無 workspace 則略。"""
    ws = getattr(sess, "workspace", "") or ""
    if ws in ("", "(adopted)", "(handoff)", "(rerun)"):
        return
    artifacts = os.path.join(os.path.dirname(ws), "attempts")
    try:
        os.makedirs(artifacts, exist_ok=True)
        with open(os.path.join(artifacts, "EVICT"), "w") as f:
            f.write("evict")
    except OSError as e:               # 寫不了不擋指令(可能還沒 spawn)
        log.warning("evict 寫檔失敗 %s: %s", getattr(sess, "key", "?"), e)


# ── 指令核心(表單 console + REST API 共用;取代 @agent comment 通道)────────── #
# COMMAND_INFO:每個指令的用途/時機/副作用/效果 —— console 說明表與文件共用。
COMMAND_INFO: dict[str, dict] = {
    "run": {
        "label": "續跑(run)", "purpose": "解除 pending,讓 agent 接著跑",
        "when": "卡在等待(pending:budget / unknown / 放行後)",
        "side_effect": "清除 pending 原因;attempts 不歸零",
        "effect": "下輪 poll 重新派工續跑(沿用同一 session)"},
    "retry": {
        "label": "重試(retry)", "purpose": "attempts 歸零並解除 pending,從頭再試",
        "when": "失敗或卡住、想整輪重來",
        "side_effect": "attempts 歸零、清 pending;沿用同一 session",
        "effect": "下輪 poll 重新派工,從第一次 attempt 起算"},
    "hold": {
        "label": "強制中斷(hold)", "purpose": "立刻停住正在跑的 agent 並轉人",
        "when": "agent 正在跑、但你要立即喊停給新指示",
        "side_effect": "立即 killpg 當前 attempt(不耗 attempt);進 HIL(Middle)",
        "effect": "另開一張 hold 表單請你給新指示;填完 agent 帶著它 resume"},
    "stop": {
        "label": "交還人工(stop)", "purpose": "把票交回人,暫不再派 agent",
        "when": "想先讓人接手、不要 agent 繼續",
        "side_effect": "pending:human-decision;讓出 F1 併發額度",
        "effect": "不再派工,直到 run/retry 或人處理"},
    "cancel": {
        "label": "取消(cancel)", "purpose": "撤銷本票,此後不再派工",
        "when": "這票不做了 / 誤開 / 判不出",
        "side_effect": "outcome=ABORTED(終態);破壞性、需二次確認",
        "effect": "永久停派;要復活請走 HIL(End) 的『續跑』"},
    "next": {
        "label": "換手(next)", "purpose": "同票換一個 agent profile 接手",
        "when": "想換 profile / 引擎繼續同一件事",
        "side_effect": "重置 session(session_id/attempts 歸零)、重建 workspace",
        "effect": "下輪由新 profile 接手同一張票(目標若需放行則重走審批門)"},
    "rerun": {
        "label": "乾淨重跑(rerun)",
        "purpose": "資訊已更新(description/CRID/欄位)後,同 profile 從頭重來",
        "when": "先把新資訊改進 Jira description(或 CQ),再按 rerun;"
                "ABORTED 的票也能用(=復活路徑)",
        "side_effect": "重置 session(忘掉舊對話)、**刪除舊工作區**重佈建、"
                       "重渲染 TICKET.md(吃到新描述/插值);破壞性、需二次確認",
        "effect": "下輪同 profile 全新開始;可選填補充指示(寫進人類指示段)"},
    "set_email": {
        "label": "改負責人(set_email)",
        "purpose": "整組更改本票負責人 email(逗號分隔多個;門禁比對 + @mention 對象)",
        "when": "負責人交接、填錯 email、加/減共同負責人、或換人接手 HIL 表單",
        "side_effect": "整組取代 session.owner_email_list(留空=清空、門禁解除);"
                       "敏感、需二次確認",
        "effect": "@mention 每位新負責人並重貼待填表單;此後只有名單內 email"
                  "(或管理者/審批者)能操作本票"},
}
# 破壞性指令:console 要二次確認(set_email 改負責人也算敏感)
DESTRUCTIVE = ("cancel", "stop", "set_email", "rerun")


def available_commands(sess) -> list[str]:
    """依當前推導狀態列出此刻適用的指令(console 動態選單 + apply 再驗共用)。"""
    st = canonical_state(sess)
    if st == "todo":
        return []                                  # 無 session:不接指令
    if st == "aborted":
        # rerun use case(2026-08-15):中止票在人工更新資訊後可乾淨復活
        return ["rerun", "set_email"]
    base = {
        "running": ["hold", "stop", "cancel", "next"],
        "queued": ["stop", "cancel", "next", "rerun"],
        "hil_middle": ["run", "retry", "cancel", "next", "rerun"],
        "hil_end": ["retry", "cancel", "next", "rerun"],  # 關單/續跑走 HIL 表單
    }.get(st, ["cancel"])
    return [*base, "set_email"]                    # K:改負責人在任何活著狀態都可下


def apply_command(source, store, profiles, issue_id: int, cmd: str,
                  args: dict | None = None, by: str = "", *,
                  base_url: str = "", mention: str = "",
                  ip: str = "", user_map: dict | None = None,
                  username_rule: str = "",
                  dashboard_url: str = "") -> tuple[bool, str, list]:
    """執行一個指令(人的表單 console 與自動化 REST API 共用的唯一核心)。

    回 (ok, 人看的結果訊息, events)。by=提交者身分(email,稽核)、ip=來源 IP(稽核)。
    args={profile:…}
    供 next。在 poller 行程內呼叫 → hold 能正確 killpg。狀態不適用 / 目標無效 →
    (False, 原因, [])。"""
    args = args or {}
    if cmd not in COMMAND_INFO:
        return False, f"未知指令:{cmd}", []
    sess = store.get_session(issue_id)
    if sess is None:
        return False, "此票尚無 session(尚未開始處理),暫無可下指令。", []
    key = sess.key
    if cmd not in available_commands(sess):
        return (False,
                f"指令「{cmd}」在目前狀態({canonical_state(sess)})不適用。", [])

    def _audit(extra: str = "") -> None:
        _ip = f" ip={ip}" if ip else ""            # K:來源 IP 稽核
        source.add_comment(issue_id, f"[agent] 指令 {cmd} by {by or '—'}{_ip}"
                                     f"(via 指令表單){extra}")

    if cmd == "next":
        target = str(args.get("profile") or "").strip()
        if not target or (profiles is not None and target not in profiles):
            avail = (", ".join(sorted(profiles)) if profiles else "—")
            return False, f"next 目標 profile 無效:'{target}'(可用:{avail})", []
        _finalize_leaving(sess, profiles, "handoff-cmd")
        sess.profile = target
        sess.session_id = None
        sess.attempts = 0
        sess.outcome, sess.pending_reason = None, None
        sess.inactive, sess.queued, sess.queued_at = False, False, 0.0
        sess.approval_revisions = 0
        sess.workspace = "(handoff)"
        store.upsert_session(sess)
        _audit(f" → {target}")
        log.info("%s 換手指令 → %s by %s", key, target, by)
        return (True, f"已換手 → {target};下輪重新排隊接手。",
                [store.journal("handoff", issue_id, key, kind="command",
                               to=target, author=by, ip=ip)])

    if cmd == "rerun":
        # 乾淨重跑(2026-08-15 use case):人已更新資訊(description/CRID/欄位)
        # → 忘掉舊對話、刪舊工作區、同 profile 從頭來。ABORTED 也可用(復活)。
        note = str(args.get("note") or "").strip()
        old_ws = sess.workspace or ""
        if os.path.isdir(old_ws) and f"{os.sep}tickets{os.sep}" in old_ws:
            try:                     # 刪舊產出——否則殘檔會直接騙過 verify
                import shutil
                shutil.rmtree(old_ws)
            except OSError as e:     # 刪不掉不擋(重佈建仍會刷 TICKET.md)
                log.warning("rerun 清舊 workspace 失敗 %s: %s", key, e)
        if note:                     # 補充指示 → description human 段(rmtree 後
            try:                     # sidecar 已不在;human 段會渲染進新 TICKET.md)
                from .sections import Section, parse, render
                t = source.get_ticket(issue_id)
                before, secs, after = parse(t.description or "")
                by_owner = {s.owner: s for s in secs}
                hb = by_owner.get("human")
                line = f"- [rerun] {note}"
                if hb:
                    hb.body = (hb.body + "\n" + line).strip()
                else:
                    secs.append(Section(owner="human", body=line))
                source.set_description(issue_id,
                                       render(before, secs, after))
            except Exception as e:  # noqa: BLE001 — 指示寫不進不擋重跑
                log.warning("rerun note 回寫失敗 %s: %s", key, e)
        sess.session_id = None
        sess.attempts = 0
        sess.outcome, sess.pending_reason = None, None
        sess.abort_reason = None
        sess.inactive, sess.queued, sess.queued_at = False, False, 0.0
        sess.approval_revisions = 0
        sess.workspace = "(rerun)"       # 哨值:health_check 不健康→重佈建
        store.upsert_session(sess)
        _audit(" (rerun)" + (f" note={note[:40]}" if note else ""))
        log.info("%s rerun by %s(乾淨重跑)", key, by)
        return (True, "已排入乾淨重跑:舊對話/工作區已捨棄,"
                "下輪以更新後的資訊重新開始。",
                [store.journal("rerun", issue_id, key, author=by, ip=ip,
                               note=bool(note))])

    if cmd == "set_email":
        from .hil import form_link  # lazy:避免 import 期耦合
        new_list = normalize_email_list(args.get("email"))   # K6:整組取代
        emails = [e for e in new_list.split(",") if e]
        bad = [e for e in emails if "@" not in e]
        if bad:
            return False, f"無效 email:{'、'.join(bad)}(逗號分隔多個)。", []
        old = sess.owner_email_list or "(無)"
        if new_list == normalize_email_list(sess.owner_email_list):
            return False, f"負責人已是 {new_list or '(無)'},無需更改。", []
        sess.owner_email_list = new_list
        store.upsert_session(sess)
        accts, missing = [], []             # re-tag:每個 email → 識別碼(L6 查序)
        for e in emails:
            acct = resolve_user_id(e, source, store, user_map, username_rule)
            (accts if acct else missing).append(acct or e)
        from .jira_source import mention_tag_of
        at = "".join(mention_tag_of(source, a) + " " for a in accts)
        shown = new_list or "(已清空,門禁解除)"
        tail = f"(Jira 查無 {'、'.join(missing)},以 email 文字標示)" if missing else ""
        _ipd = f" ip={ip}" if ip else ""
        opens = [r for r in store.open_interactions_for_ticket(issue_id)
                 if getattr(r, "kind", "hil") != "command"]   # 待填 HIL 表單
        if opens:                                   # 重發:@mention 新人 + 貼連結
            links = "、".join(form_link(base_url, r.token) for r in opens)
            source.add_comment(issue_id, f"{at}[agent] 本票負責人已改為 {shown}"
                               f"(by {by or '—'}{_ipd}){tail}。"
                               f"你有待填表單:{links}")
        else:                                       # 純 re-tag 通知
            source.add_comment(issue_id, f"{at}[agent] 本票負責人已改為 {shown}"
                               f"(by {by or '—'}{_ipd}){tail}")
        log.info("%s set_email %s→%s by %s", key, old, new_list, by)
        return (True, f"已將負責人改為 {shown}" + tail
                + (f";已重貼 {len(opens)} 張待填表單" if opens else "") + "。",
                [store.journal("owner_changed", issue_id, key, old=old,
                               new=new_list, author=by, ip=ip,
                               retagged=len(accts), reissued=len(opens))])

    if cmd == "hold":
        _write_evict(sess)                         # 立即 killpg(不耗 attempt)
        sess.pending_reason = "hold"
        store.upsert_session(sess)
        from .hil import request_human  # lazy:避免 import 期耦合
        request_human(
            source, store, issue_id, key, "hold",
            question="人類強制中斷,請給 agent 新指示(填完 agent 會帶著它 resume)",
            base_url=base_url, mention=mention)
        _audit()
        log.info("%s hold:evict + 開 hold 表單 by %s", key, by)
        return (True, "已中斷,agent 已停;請再填 hold 表單給新指示。",
                [store.journal("command_accepted", issue_id, key,
                               command="hold", author=by, ip=ip)])

    if cmd in ("run", "retry"):
        if cmd == "retry":
            sess.attempts = 0
        sess.outcome, sess.pending_reason = None, None
    elif cmd == "stop":
        sess.pending_reason = "human-decision"
    elif cmd == "cancel":
        sess.outcome, sess.pending_reason = "ABORTED", None
        sess.abort_reason = "cancel"               # M2:中止理由泛化
    store.upsert_session(sess)
    _audit()
    log.info("%s 指令 %s by %s", key, cmd, by)
    evs = [store.journal("command_accepted", issue_id, key,
                         command=cmd, author=by, ip=ip)]
    if cmd == "cancel":
        evs.append(store.journal("aborted", issue_id, key,
                                 reason="cancel", author=by))
        from .provenance import finalize_provenance  # Q 波:cancel 也留結案存證
        evs.extend(finalize_provenance(source, store, sess, issue_id, key,
                                       dashboard_url=dashboard_url))
    return True, f"已執行:{cmd}。", evs


class ExternalChangePolicy:
    def __init__(self, source: JiraCloudSource, store: Store,
                 cancel_states: list[str],
                 bot_account_id: str | None = None,
                 profiles: dict | None = None):
        self.source = source
        self.store = store
        self.cancel_states = cancel_states
        # W12:知道機器人 accountId 才能判 assignee 方向;None = 舊語義
        self.bot_account_id = bot_account_id
        self.profiles = profiles       # W4.3:離手定格的 engine 查表

    def on_status_changed(self, t: Ticket, new_state: str) -> list[dict]:
        sess = self.store.get_session(t.id)
        if (new_state in self.cancel_states and sess
                and sess.outcome not in ("SUCCESS", "ABORTED")):
            sess.outcome, sess.pending_reason = "ABORTED", None
            sess.abort_reason = "external"         # M2:看板直接關票
            self.store.upsert_session(sess)
            return [self.store.journal("external_abort", t.id, t.key,
                                       state=new_state),
                    self.store.journal("aborted", t.id, t.key,
                                       reason="external", state=new_state)]
        return []

    def on_assignee_changed(self, t: Ticket) -> list[dict]:
        """W11:assignee **恆定=Agent**,不再當資源開關/觸發(人機互動改走一次性
        表單)。被改離 agent → 記告警 + 貼一次提醒(**不強制改回**,避免搶 assignee +
        revert→通知噪音);改回 agent → 靜默記錄。poller 只在 assignee 實際變動時呼此,
        故一次變動只提醒一次(冪等)。"""
        sess = self.store.get_session(t.id)
        if sess is None or sess.outcome is not None:
            return []                       # 無 session / 已終態:不管
        if self.bot_account_id and (t.assignee_id or "") == self.bot_account_id:
            log.info("%s assignee 改回 agent(靜默)", t.key)
            return [self.store.journal("assignee_restored", t.id, t.key)]
        self.source.add_comment(
            t.id, "[agent] 提醒:本票由 agent 處理,assignee 請保持為 agent。"
                  "人類要介入,請用 agent 貼出的一次性表單連結(請勿改 assignee)。")
        log.info("%s assignee 被改離 agent → 告警(不改回)", t.key)
        return [self.store.journal("assignee_alert", t.id, t.key,
                                   assignee=t.assignee or "")]
