"""Dispatcher: the inner evidence loop, ticket-side (v5 §4.6).

    create_or_resume → workspace(provision/health) → attempt → GRADE
        → SUCCESS: evidence comment back to Jira, stop
        → FAILURE: bounded retry with evidence-only feedback (native resume)
        → UNKNOWN: pending:unknown comment, stop — ONLY a human releases it

Do not loop on confidence, loop on evidence: the runner's "completed" alone
never closes a ticket — the profile's deterministic verify does. Graders are
reused from the A-route PoC (arcp_poc.grader), proving the B-phase survival
rule that the differentiation layer is runtime-agnostic.
"""

from __future__ import annotations

import os
import uuid

from .contract import agent_score, summarize
from .deliverables import post_deliverables
from .grader import AllOf, CommandGrader, FileChecklistGrader, JsonGrader
from .identity import normalize_email_list
from .inner_runner import run_attempt
from .jira_source import JiraCloudSource
from .logutil import get_logger
from .profiles import Profile
from .store import Store, TicketSession
from .ticket import Ticket
from .transcript import engine_of_agent
from .transcript import finalize as finalize_transcript
from .triggers import parse_ticket_meta
from .workspace import health_check, inject_base_context, provision

BASE_PROMPT = ("請先閱讀工作目錄裡的 TICKET.md,完成其中「描述」段落交付的任務。"
               "完成後回覆一行 TASK_DONE。")

log = get_logger("dispatcher")

# timeout 輸出樣態(inner_runner timeout_kind)→ 留言的人話描述
_TIMEOUT_KIND_DESC = {
    "no_output_timeout": "(CLI 從頭到尾無輸出——查啟動/認證方向)",
    "stalled_output_timeout": "(有輸出後停滯——查長工具呼叫/卡死方向)",
}


def _grader(profile: Profile):
    parts = []
    for step in profile.verify:
        if step.files:
            parts.append(FileChecklistGrader(step.files))
        if step.cmd:
            parts.append(CommandGrader(step.cmd))
        if step.json:                          # C1:JSON 形狀檢查
            parts.append(JsonGrader(step.json.get("file"),
                                    step.json.get("require"),
                                    step.json.get("types")))
    return AllOf(*parts)


def _resume_hint(sess: TicketSession) -> str:
    lines = [f"workspace: {sess.workspace}"]
    if sess.session_id:
        lines.append(f"acp_resume_session_id: {sess.session_id}")
    return "\n".join(lines)


class Dispatcher:
    def __init__(self, source: JiraCloudSource, store: Store,
                 profiles: dict[str, Profile], root: str,
                 server_manager=None, approval=None, cancel_status: str = "",
                 global_budget: dict | None = None,
                 form_base_url: str = "", mention: str = ""):
        self.source = source
        self.store = store
        self.profiles = profiles
        self.root = root
        self.server_manager = server_manager   # conc.3 long-lived shared server
        self.approval = approval               # W2.3 ApprovalGate | None
        self.cancel_status = cancel_status     # triage 失敗時 Jira 想轉的「取消」狀態名
        # budget:全站月度上限 dict{monthly_max_tokens, monthly_max_usd}(可 reload)
        self.global_budget = global_budget or {}
        self.form_base_url = form_base_url      # budget soft 破→發增額表單連結
        self.mention = mention
        self.admin_emails: list[str] = []       # K:全站管理者 email(門禁豁免;可 reload)
        self.user_map: dict = {}                # L6:email→識別碼手動映射(可 reload)
        self.username_rule: str = ""            # L6:查無時推導(local/{local} 模板)
        self.security_scan: dict = {}           # M3:TICKET.md 掃描 config(可 reload)
        self.status_sync: dict = {}             # N:內部態→Jira 狀態同步(可 reload)
        # timeout 重跑上限(global;profile agent.timeout_retry_max 覆蓋;
        # 0=維持 v5 D3「timeout→UNKNOWN 交人」。可 reload)
        self.timeout_retry_max: int = 0
        self.dashboard_url: str = ""            # Q 波:結案回寫 ticket 連結(選配)

    def _abort_untriageable(self, ticket: Ticket, meta: dict,
                            events: list[dict]) -> list[dict]:
        """triage 判不出適用 profile → 中止:寫 profile=notfound + ABORTED、journal
        aborted(reason=untriageable)、留言、Jira 轉取消(cancel_status;沒有則優雅退回
        done-category)。不跑 agent。"""
        reason = meta.get("reason") or ""
        sess = TicketSession(
            issue_id=ticket.id, key=ticket.key, profile="notfound",
            workspace="(untriaged)", session_id=None, attempts=0,
            outcome="ABORTED", pending_reason=None, cost_usd=0.0,
            abort_reason="untriageable")
        self.store.upsert_session(sess)
        events.append(self.store.journal(
            "aborted", ticket.id, ticket.key, reason="untriageable",
            detail=reason[:200]))
        from .provenance import finalize_provenance  # Q 波:abort 也留結案存證
        events.extend(finalize_provenance(
            self.source, self.store, sess, ticket.id, ticket.key,
            dashboard_url=self.dashboard_url))
        self.source.add_comment(ticket.id, (
            "[agent] triage 判不出適用的 agent profile → 中止(ABORTED,不派工)。"
            + (f"原因:{reason}" if reason else "")))
        # Jira 取消:優先轉 cancel_status(config),沒有就退回 done-category
        self.source.transition(ticket.id, "done",
                               prefer_status=self.cancel_status or None)
        log.info("%s triage untriageable → ABORTED", ticket.key)
        return events

    def _add_approver_watcher(self, ticket: Ticket, profile: Profile) -> list:
        """K:首建 session(鎖定 profile)時把該 profile.approver 加為 Jira watcher
        (best-effort;approver 為 email 先轉 accountId、已是 accountId 直接用;
        查不到 / 失敗都不擋派工)。"""
        approver = getattr(profile, "approver", None)
        if not approver:
            return []
        try:
            from .identity import resolve_user_id
            acct = (resolve_user_id(approver, self.source, self.store,
                                    self.user_map, self.username_rule)
                    if "@" in str(approver) else approver)
            if not acct:
                return []
            self.source.add_watcher(ticket.id, acct)
            return [self.store.journal("watcher_added", ticket.id, ticket.key,
                                       approver=str(approver))]
        except Exception as e:  # noqa: BLE001 — watcher 是加值,失敗不擋派工
            log.warning("%s 加 approver watcher 失敗:%s", ticket.key, e)
            return []

    def _security_gate(self, ticket: Ticket, sess: TicketSession,
                       events: list) -> bool:
        """M3:spawn 前掃 TICKET.md(prompt injection 防線)。True=放行。
        命中 >= fail_on 或掃描器異常(fail-closed)→ pending:security +
        security_review 表單交人裁決,回 False。沒配 security_scan=功能關;
        人審放行過(sec_reviewed_at)或同內容掃過且通過(hash)→ 不重掃。"""
        cfg = self.security_scan or {}
        if not cfg.get("command") or sess.sec_reviewed_at:
            return True
        p = os.path.join(sess.workspace, "TICKET.md")
        try:
            text = open(p, encoding="utf-8").read()
        except OSError:
            return True                    # 哨值 workspace / 無 TICKET.md 不擋
        from .secscan import content_hash, scan_text
        if sess.sec_scanned_hash == content_hash(text):
            return True                    # 同內容掃過且通過
        res = scan_text(text, cfg)
        if res is None:
            return True
        events.append(self.store.journal(
            "security_scan", ticket.id, ticket.key, ok=res.ok,
            n_findings=len(res.findings), error=(res.error or "")[:200]))
        if res.ok:
            sess.sec_scanned_hash = res.content_hash
            self.store.upsert_session(sess)
            return True
        sess.pending_reason = "security"
        self.store.upsert_session(sess)
        from .secscan import sort_findings
        fs = sort_findings(res.findings)          # critical 在前(摘要才有用)
        top = "; ".join(f"[{x['severity']}] {x['title'] or x['rule_id']}"
                        for x in fs[:3])
        why = ("掃描器異常(fail-closed,非必為威脅):" + res.error
               if res.error else top or "掃描命中")
        from .hil import request_human
        request_human(
            self.source, self.store, ticket.id, ticket.key, "security_review",
            question=f"TICKET.md 安全掃描未過,請人工裁決:{why[:300]}",
            payload_extra={"findings": fs[:20],
                           "ticket_md": text[:8000],
                           "scan_error": res.error},
            base_url=self.form_base_url, mention=self.mention)
        events.append(self.store.journal(
            "security_blocked", ticket.id, ticket.key,
            n_findings=len(res.findings), scanner_error=bool(res.error)))
        log.warning("%s 安全掃描未過(%d 命中%s)→ pending:security",
                    ticket.key, len(res.findings),
                    ",掃描器異常" if res.error else "")
        return False

    def _post_deliverables(self, sess: TicketSession, ticket: Ticket,
                           outcome: str, res, events: list[dict]) -> None:
        """終態:記 agent 自評分(contract.score)+ 貼交付物(OUTPUT.json → ADF + 附件)。
        交付物 best-effort;agent_score 一定記(auto_close 要用)。
        """
        structured = res.structured if res is not None else None
        sc = agent_score(structured) if structured else None
        if sc != sess.agent_score:               # agent 數字自評 → auto_close/三訊號
            sess.agent_score = sc
            self.store.upsert_session(sess)
        self_summary = (structured or {}).get("summary", "") if structured else ""
        try:
            events.extend(post_deliverables(
                self.source, self.store, ticket, sess, outcome=outcome,
                self_summary=self_summary,
                base_url=getattr(self.source, "base_url", None)))
        except Exception as e:  # noqa: BLE001 — 交付物是加值,壞了不擋
            log.warning("%s 貼交付物失敗:%s", ticket.key, e)

    def _pack_transcript(self, sess: TicketSession, profile: Profile,
                         ticket: Ticket, events: list[dict]) -> None:
        """W4.2(B2):close(成敗都)定格 final HTML + tgz 打包;失敗不擋流程。"""
        arts = finalize_transcript(sess.session_id,
                                   engine_of_agent(profile.agent),
                                   sess.workspace, pack=True,
                                   reason=f"close:{sess.outcome}")
        if arts:
            events.append(self.store.journal(
                "transcript_packed", ticket.id, ticket.key,
                files=[os.path.basename(a) for a in arts]))

    @staticmethod
    def _fmt(metric: str, v) -> str:
        if v is None:
            return "—"
        return f"${float(v):.4f}" if metric == "usd" else f"{int(v):,} tok"

    @staticmethod
    def _field(metric: str, base: str) -> str:
        return f"{base}_{'usd' if metric == 'usd' else 'tokens'}"

    @staticmethod
    def _first_hit(checks):
        """回第一個 (metric, used, cap):cap 有設且 used≥cap。不可量的 metric used=0
        →自然略過(codex 可能只有 token → usd 用量 0 不會誤卡)。"""
        for metric, used, cap in checks:
            if cap is not None and used is not None and float(used) >= float(cap):
                return (metric, used, cap)
        return None

    def _budget_precheck(self, ticket: Ticket, profile: Profile,
                         sess: TicketSession) -> list[dict]:
        """budget 閘(每輪 attempt/resume 前):per-ticket(hard→soft)→ 月/agent →
        全站,誰先破誰卡 → pending:budget。soft 破 = 使用者可自助增額(scope
        ticket-soft);hard/月/全站 = 只管理者能改(留言通知)。兩 metric 都量到就都
        檢查、任一破就卡;只量到一種就用那種。回 pending 事件(空=可續跑)。"""
        used_usd, used_tok = sess.cost_usd, sess.tokens
        soft_usd = (sess.soft_usd if sess.soft_usd is not None
                    else profile.ticket_soft_usd)
        soft_tok = (sess.soft_tokens if sess.soft_tokens is not None
                    else profile.ticket_soft_tokens)

        # 1) per-ticket hard(絕對上限;優先於 soft)
        hit = self._first_hit([("usd", used_usd, profile.ticket_hard_usd),
                               ("token", used_tok, profile.ticket_hard_tokens)])
        if hit:
            m, u, c = hit
            fld = self._field(m, "ticket_hard")
            return self._budget_block(ticket, profile, sess, "ticket-hard", (
                f"[agent] pending:budget(單票 hard/{m})— 已用 {self._fmt(m, u)} "
                f"達 hard 上限 {self._fmt(m, c)}。此為絕對上限:需**管理者**調高 "
                f"profile «{profile.name}» 的 `budget.{fld}` 後 hot reload,自動續跑。"
                f"\n{_resume_hint(sess)}"))

        # 2) per-ticket soft → 使用者自助增額(發 budget_increase 一次性表單)
        hit = self._first_hit([("usd", used_usd, soft_usd),
                               ("token", used_tok, soft_tok)])
        if hit:
            return self._budget_soft_form(ticket, profile, sess, hit[0])

        # 3) 月/agent hard(只管理者能改)
        hit = self._first_hit([
            ("usd", self.store.monthly_cost(profile.name),
             profile.monthly_max_usd),
            ("token", self.store.monthly_tokens(profile.name),
             profile.monthly_max_tokens)])
        if hit:
            m, u, c = hit
            return self._budget_block(ticket, profile, sess, "monthly", (
                f"[agent] pending:budget(月/agent/{m})— profile «{profile.name}» "
                f"當月已用 {self._fmt(m, u)} 達上限 {self._fmt(m, c)}。僅**管理者**能改"
                f"(`budget.{self._field(m, 'monthly_max')}` + hot reload),本票才續跑。"
                f"\n{_resume_hint(sess)}"))

        # 4) 全站月度 hard(只管理者能改)
        g = self.global_budget or {}
        hit = self._first_hit([
            ("usd", self.store.global_monthly_cost(), g.get("monthly_max_usd")),
            ("token", self.store.global_monthly_tokens(),
             g.get("monthly_max_tokens"))])
        if hit:
            m, u, c = hit
            return self._budget_block(ticket, profile, sess, "global", (
                f"[agent] pending:budget(全站/{m})— 全站當月已用 {self._fmt(m, u)} "
                f"達 global 上限 {self._fmt(m, c)}。僅**管理者**能改"
                f"(`outer_loop.budget.{self._field(m, 'monthly_max')}` + hot reload)。"
                f"\n{_resume_hint(sess)}"))
        return []

    def _budget_block(self, ticket: Ticket, profile: Profile,
                      sess: TicketSession, scope: str, msg: str) -> list[dict]:
        sess.pending_reason = "budget"
        self.store.upsert_session(sess)
        self.source.add_comment(ticket.id, msg)
        ev = [self.store.journal("pending", ticket.id, ticket.key,
                                 reason="budget", scope=scope,
                                 cost_usd=sess.cost_usd, tokens=sess.tokens)]
        finalize_transcript(sess.session_id,          # W6.4 等人類也產 transcript
                            engine_of_agent(profile.agent),
                            sess.workspace, pack=False, reason="pending:budget")
        return ev

    def _budget_soft_form(self, ticket: Ticket, profile: Profile,
                          sess: TicketSession, metric: str) -> list[dict]:
        """單票 soft 破:pending:budget + 發 budget_increase 一次性表單(自助調高
        ≤hard)。表單顯示 已用/soft/hard(token+usd)+ 目前 summary + Jira 連結。"""
        from .deliverables import snapshot_for_form
        from .hil import request_human
        sess.pending_reason = "budget"
        self.store.upsert_session(sess)
        ev = [self.store.journal("pending", ticket.id, ticket.key,
                                 reason="budget", scope="ticket-soft",
                                 cost_usd=sess.cost_usd, tokens=sess.tokens)]
        finalize_transcript(sess.session_id, engine_of_agent(profile.agent),
                            sess.workspace, pack=False, reason="pending:budget")
        soft_u = sess.soft_usd if sess.soft_usd is not None else profile.ticket_soft_usd
        soft_t = (sess.soft_tokens if sess.soft_tokens is not None
                  else profile.ticket_soft_tokens)
        base = getattr(self.source, "base_url", "") or ""
        question = (
            f"本票已達 soft 上限({metric})。目前用量 → "
            f"USD ${sess.cost_usd:.4f}(soft {self._fmt('usd', soft_u)} / "
            f"hard {self._fmt('usd', profile.ticket_hard_usd)})、"
            f"token {sess.tokens:,}(soft {self._fmt('token', soft_t)} / "
            f"hard {self._fmt('token', profile.ticket_hard_tokens)})。可提高本票上限"
            f"(不得超過 hard;超過 hard 需通知管理者改 profile)。")
        request_human(
            self.source, self.store, ticket.id, ticket.key, "budget_increase",
            question=question, base_url=self.form_base_url, mention=self.mention,
            payload_extra={
                "deliverables": snapshot_for_form(sess.workspace),
                "jira_url": (f"{base.rstrip('/')}/browse/{ticket.key}"
                             if base else ""),
                "hard_usd": profile.ticket_hard_usd,
                "hard_tokens": profile.ticket_hard_tokens})
        return ev

    def _inject_base(self, sess: TicketSession, ticket: Ticket,
                     profile: Profile, events: list[dict]) -> None:
        """W10.3 跨票 base:把來源票(sess.base_ref = 其 issue_id)脈絡注入本子票 ws,
        注入後清 base_ref(一次性)。來源 session 不在則跳過(降級,不擋子票)。"""
        try:
            base = self.store.get_session(int(sess.base_ref))
        except (TypeError, ValueError):
            base = None
        if base is None:
            log.warning("%s base_ref=%s 找不到來源 session,跳過脈絡注入",
                        ticket.key, sess.base_ref)
        else:
            dest = inject_base_context(
                sess.workspace, base.workspace, base.key,
                getattr(self.source, "base_url", None))
            # 注入會往 human sidecar 追一行指向 BASE_/;立刻刷新 TICKET.md 讓「人類指示」
            # 段當輪就顯示(否則要等下一輪 health_check 才刷,agent 首跑會看不到)。
            health_check(sess.workspace, ticket, profile,
                         getattr(self.source, "base_url", None))
            events.append(self.store.journal(
                "base_injected", ticket.id, ticket.key, base=base.key,
                dest=os.path.basename(dest)))
            log.info("%s 注入 base 脈絡 ← %s", ticket.key, base.key)
        sess.base_ref = None                     # 一次性:注入後清除
        self.store.upsert_session(sess)

    def _effective_agent(self, profile: Profile) -> dict:
        """Inject shared-server info for openhands-server backend (conc.3)."""
        agent = dict(profile.agent)
        if (self.server_manager is not None
                and agent.get("backend") == "openhands-server"):
            self.server_manager.ensure()          # lazy start / restart (N1)
            agent["server_managed"] = True
            agent["server_port"] = self.server_manager.port
            agent["server_api_key"] = self.server_manager.api_key
        return agent

    def handle(self, ticket: Ticket, profile_name: str) -> list[dict]:
        """Idempotent: terminal/pending sessions are skipped silently.
        主題 N:收尾統一做 Jira 狀態同步(config status_sync;沒設=關)。"""
        events = self._handle_core(ticket, profile_name)
        try:
            self._sync_status(ticket, events)
        except Exception as e:  # noqa: BLE001 — 同步 best-effort,不擋派工
            log.warning("%s Jira 狀態同步失敗:%s", ticket.key, e)
        return events

    @staticmethod
    def _sync_key(sess) -> str | None:
        """session → status_sync 的鍵。特判(2026-08-12 定案):UNKNOWN=交人查
        → hil_middle(Pending);queued(排隊)與 inactive(交人類)不動。"""
        if sess is None:
            return None
        if sess.outcome == "ABORTED":
            return "aborted"
        if sess.outcome == "UNKNOWN":
            return "hil_middle"
        if sess.outcome in ("SUCCESS", "FAILURE"):
            return "hil_end"
        if sess.pending_reason:
            return "hil_middle"
        if sess.queued or sess.inactive:
            return None
        return "running"

    def _sync_status(self, ticket: Ticket, events: list) -> None:
        """依 session 推導態把 Jira 票轉到 config status_sync 對應狀態。
        目標=現況 → skip;transition_to 精確按名稱、轉不到只記 log(workflow
        限制/名稱打錯都不擋 harness)。"""
        cfg = self.status_sync or {}
        if not cfg:
            return
        sess = self.store.get_session(ticket.id)
        key = self._sync_key(sess)
        target = cfg.get(key) if key else None
        if not target or (ticket.state or "").strip().lower() \
                == target.strip().lower():
            return
        moved = self.source.transition_to(ticket.id, target)
        if not moved and key == "hil_end" and cfg.get("running"):
            # 中繼:agent 快到一輪內 To Do→終態,漏了 running 的 Jira 中繼
            # (workflow 常要求 Resolve 只能從 In Progress 進)→ 補走一步再試
            moved = (self.source.transition_to(ticket.id, cfg["running"])
                     and self.source.transition_to(ticket.id, target))
        if moved:
            events.append(self.store.journal(
                "status_synced", ticket.id, ticket.key,
                state=key, to=target))
        else:
            log.warning("%s 狀態同步 %s→%r:當前 workflow 轉不到(略過)",
                        ticket.key, ticket.state, target)

    def _handle_core(self, ticket: Ticket, profile_name: str) -> list[dict]:
        events: list[dict] = []
        profile = self.profiles[profile_name]
        sess = self.store.get_session(ticket.id)
        # F3(W2.5):session 鎖定的 profile 優先於 route 推導——換手後 route
        # 標籤仍指舊 profile,session 存在即以其 profile 為準
        if (sess is not None and sess.profile != profile.name
                and sess.profile in self.profiles):
            profile = self.profiles[sess.profile]
        # Q16:首次派工(尚無 session)且 main profile 有 select → 選一個實際 profile
        # (A/B 測試 / 泛化 triage);選中的由下方 session 建立時 鎖定,resume 不重選。
        elif sess is None and getattr(profile, "select", None):
            from .selection import UNTRIAGEABLE, select_profile
            chosen, meta = select_profile(
                ticket, profile, self.profiles,
                clearquest_id=parse_ticket_meta(ticket.description).get("crid"))
            if chosen == UNTRIAGEABLE:            # triage 判不出 → 中止,不跑 agent
                return self._abort_untriageable(ticket, meta, events)
            if chosen != profile.name and chosen in self.profiles:
                events.append(self.store.journal(
                    "profile_selected", ticket.id, ticket.key,
                    original=profile.name, chosen=chosen,
                    method=meta.get("method")))
                profile = self.profiles[chosen]
        # auto-recover pending:external once infra is back (N1/N3): server
        # healthy again → clear the block and resume this poll (不漏)
        if (sess and sess.pending_reason == "external"
                and self.server_manager is not None
                and self.server_manager.ensure()):
            sess.pending_reason = None
            self.store.upsert_session(sess)
            events.append(self.store.journal(
                "external_cleared", ticket.id, ticket.key, cause="server-back"))

        # W2.3 起點審批門:require_approval 時 fork 前先審(新建/換手;resume 不審)。
        # pending:approval 仍要跑(偵測 assignee 交回);escalated 由下方通用 skip 擋。
        if (profile.require_approval and self.approval is not None
                and (sess is None
                     or (sess.session_id is None and sess.outcome is None
                         and not sess.inactive
                         and sess.pending_reason in (None, "approval")))):
            if sess is None:
                _meta = parse_ticket_meta(ticket.description)
                sess = TicketSession(
                    issue_id=ticket.id, key=ticket.key, profile=profile.name,
                    workspace="(pending-approval)", session_id=None, attempts=0,
                    outcome=None, pending_reason=None, cost_usd=0.0,
                    clearquest_id=_meta.get("crid"),
                    owner_email_list=normalize_email_list(_meta.get("email")))
                events.extend(self._add_approver_watcher(ticket, profile))
            decision = self.approval.gate(ticket, profile, sess)
            self.store.upsert_session(sess)
            events.append(self.store.journal(
                "approval", ticket.id, ticket.key, decision=decision,
                revisions=sess.approval_revisions))
            if decision != "proceed":
                return events
            sess.workspace = provision(self.root, ticket, profile,
                                      getattr(self.source, "base_url", None))
            self.store.upsert_session(sess)

        if sess and (sess.outcome in ("SUCCESS", "ABORTED")
                     or sess.pending_reason or sess.inactive):
            # done/cancelled、awaiting a human,或 W12 inactive(assignee 在
            # 人類手上 = 資源開關關閉,不派工)— nothing to do
            return events

        if sess is None:
            ws = provision(self.root, ticket, profile,
                                      getattr(self.source, "base_url", None))
            _meta = parse_ticket_meta(ticket.description)
            sess = TicketSession(
                issue_id=ticket.id, key=ticket.key, profile=profile.name,
                workspace=ws, session_id=None, attempts=0,
                outcome=None, pending_reason=None, cost_usd=0.0,
                clearquest_id=_meta.get("crid"),
                owner_email_list=normalize_email_list(_meta.get("email")))
            self.store.upsert_session(sess)
            events.append(self.store.journal(
                "session_created", ticket.id, ticket.key,
                profile=profile.name, workspace=ws))
            events.extend(self._add_approver_watcher(ticket, profile))
        else:
            healthy, reason = health_check(
                sess.workspace, ticket, profile,
                getattr(self.source, "base_url", None))
            if not healthy:
                # 重建(empty-template 安全)/ 換手哨值「(handoff)」→ 依現行
                # profile 重 provision 新 instance;路徑要回存(換手後路徑不同)
                events.append(self.store.journal(
                    "workspace_unhealthy", ticket.id, ticket.key,
                    reason=reason))
                sess.workspace = provision(self.root, ticket, profile,
                                      getattr(self.source, "base_url", None))
                self.store.upsert_session(sess)

        # W10.3 跨票 base:子票首次佈建完成後,注入來源票脈絡(一次性;注入後清 base_ref,
        # 之後 resume 不再重注)。workspace 已於上方 provision/health 解析為實體目錄。
        if sess.base_ref and os.path.isdir(sess.workspace):
            self._inject_base(sess, ticket, profile, events)

        # Q 波:TICKET.md 內容真變才上傳 Jira 附件存證(hash 比對,無變更零成本;
        # 首建/安全審修訂/人類指示後各留一版——Jira 上可回放 agent 當時看到什麼)
        from .provenance import attach_ticket_md_if_changed
        events.extend(attach_ticket_md_if_changed(
            self.source, self.store, ticket.id, ticket.key, sess.workspace))

        # M3:TICKET.md 安全掃描門(spawn 前最後一道;涵蓋 description /
        # agent-job prompt / 人類指示全部來源)。沒配=關;人審過不再擋。
        if not self._security_gate(ticket, sess, events):
            return events

        grader = _grader(profile)
        artifacts = os.path.join(os.path.dirname(sess.workspace), "attempts")
        agent_cfg = self._effective_agent(profile)  # conc.3 server injection
        feedback: str | None = None
        res = None  # 若 attempts 已達 max、while 一次都沒跑,FAILURE 段仍安全

        # W5.1 crash 偵測(W30):attempts 已計但該輪 envelope 缺 = 上輪
        # harness 於 attempt 中途死。有 sid(任一引擎)→ 退還該 attempt、
        # native resume 續跑(transcript 重放不重工);無 sid → 不能證明
        # 副作用 → UNKNOWN 交人(loop on evidence)。
        if sess.attempts > 0 and not os.path.exists(
                os.path.join(artifacts, f"a{sess.attempts}.envelope.json")):
            if sess.session_id:
                sess.attempts -= 1
                self.store.upsert_session(sess)
                events.append(self.store.journal(
                    "attempt_crash_recovered", ticket.id, ticket.key,
                    resume=sess.session_id))
                log.info("%s attempt 中途 crash → 退還並 resume(%s)",
                         ticket.key, sess.session_id)
            else:
                sess.outcome, sess.pending_reason = "UNKNOWN", "unknown"
                self.store.upsert_session(sess)
                self.source.add_comment(ticket.id, (
                    f"[agent] outcome=UNKNOWN:harness 於 attempt "
                    f"{sess.attempts} 中途中斷且無 session id 可續,"
                    f"無法證明副作用。請人工檢查後下指令。"
                    f"\n{_resume_hint(sess)}"))
                events.append(self.store.journal(
                    "pending", ticket.id, ticket.key, reason="unknown",
                    cause="harness-crash"))
                return events

        while sess.attempts < profile.max_attempts:
            # budget 閘:spawn 前檢查 per-ticket(soft/hard)/月/全站 → pending:budget、
            # 不 spawn(跑前擋才不多燒;soft 可自助增額、hard/月/全站只管理者能改)
            blocked = self._budget_precheck(ticket, profile, sess)
            if blocked:
                events.extend(blocked)
                return events
            # race 閘(2026-08-13 T10 實測):REST hold/stop/cancel 與本輪
            # dispatch 併發——指令執行緒剛設 pending/outcome,本執行緒已越過
            # 入口檢查正要 spawn(spawn 還會清舊 EVICT 檔,讓 hold 的 killpg
            # 完全撲空)。spawn 前從 store 重讀,搶進的指令贏。
            fresh = self.store.get_session(ticket.id)
            if fresh and (fresh.pending_reason or fresh.outcome == "ABORTED"):
                log.info("%s spawn 前偵測到並發指令(pending=%s outcome=%s)"
                         "→ 本輪放棄派工", ticket.key,
                         fresh.pending_reason, fresh.outcome)
                return events
            sess.attempts += 1
            # W5.1 sid 預派(W29):rawcli+claude 首跑先派 uuid,attempt 狀態
            # 先持久化再 spawn——crash 後可 resume;快照器首 attempt 就有 sid
            resume_sid = sess.session_id
            preassigned = None
            if (resume_sid is None
                    and agent_cfg.get("backend") == "rawcli"
                    and agent_cfg.get("engine", "claude") == "claude"):
                preassigned = str(uuid.uuid4())
                sess.session_id = preassigned
            self.store.upsert_session(sess)
            events.append(self.store.journal(
                "attempt_started", ticket.id, ticket.key,
                attempt=sess.attempts, preassigned=bool(preassigned)))
            prompt = BASE_PROMPT if not feedback else (
                f"{BASE_PROMPT}\n\n上次嘗試未通過驗證,失敗證據:\n{feedback}\n"
                f"請只修正缺失的部分,不要重做已完成的部分。")
            # T10/T12 修:HIL 表單剛給的新指示 → prompt 顯式帶上(單次消費;
            # TICKET.md 人類指示段仍有全history,這裡保證 resume 輪一定看到)
            from .workspace import pop_resume_note
            note = pop_resume_note(sess.workspace)
            if note:
                prompt += f"\n\n人類最新指示(最優先遵循):\n{note}"
            # race 閘第二道(T10 二輪實測:第一道 while 頂的 fresh read 仍輸
            # 給毫秒窗——handle 執行緒中途的 upsert 也可能舊物件覆蓋)。spawn
            # 前最後一瞬再讀;搶進的 hold/cancel 贏,本 attempt 回滾不計。
            fresh = self.store.get_session(ticket.id)
            if fresh and (fresh.pending_reason or fresh.outcome == "ABORTED"):
                sess.attempts -= 1
                sess.pending_reason = fresh.pending_reason
                sess.outcome = fresh.outcome or sess.outcome
                self.store.upsert_session(sess)
                events.append(self.store.journal(
                    "attempt_skipped", ticket.id, ticket.key,
                    reason="concurrent-command",
                    pending=fresh.pending_reason or ""))
                log.info("%s spawn 最後一瞬偵測到並發指令 → 放棄本 attempt",
                         ticket.key)
                return events
            res = run_attempt(agent_cfg, sess.workspace, prompt,
                              artifacts, sess.attempts,
                              resume_session_id=resume_sid,
                              preassigned_session_id=preassigned)
            # T10 修:attempt 結束統一清 EVICT 檔(取代 inner_runner 起跑刪
            # ——那會洗掉 hold 現役標記)。這裡清=下一 attempt 乾淨不誤殺。
            try:
                os.remove(os.path.join(artifacts, "EVICT"))
            except OSError:
                pass
            sess.session_id = res.session_id or sess.session_id
            sess.cost_usd += res.cost_usd or 0.0
            sess.tokens += res.tokens or 0            # budget:累計 token
            events.append(self.store.journal(
                "attempt_finished", ticket.id, ticket.key,
                attempt=sess.attempts, raw=res.raw_outcome,
                error_kind=res.error_kind,
                timeout_kind=res.timeout_kind,   # 輸出樣態(診斷,非 verdict)
                progress=res.progress,           # content-free 進度診斷
                truly_resumed=res.truly_resumed,
                structured=res.structured,               # G1:agent 自評(記錄)
                envelope=res.envelope_path,
                cost=res.cost_usd or 0.0,                # 月/global 預算彙總資料源
                tokens=res.tokens or 0,                  # 同上(token 維度)
                profile=profile.name))

            # E3(W5.3):被主動驅逐(control /evict → killpg)——非故障,
            # 不消耗 attempt;session 留 active,下輪 native resume 續跑
            # (若 evict 是配合交人,下輪 external policy 會標 inactive 擋住)
            if res.error_kind == "evicted":
                sess.attempts -= 1
                sess.evict_count += 1           # W6.3:異常計數
                # T10 修(第七輪實測):evict 多半是 hold 觸發——command 執行緒
                # 剛設的 pending_reason 不能被本執行緒的舊物件 upsert 洗掉
                # (洗掉=被殺的票立刻自動 resume,hold 形同虛設)。
                fresh = self.store.get_session(ticket.id)
                if fresh and fresh.pending_reason:
                    sess.pending_reason = fresh.pending_reason
                self.store.upsert_session(sess)
                finalize_transcript(sess.session_id,      # W6.4 evict 定格
                                    engine_of_agent(profile.agent),
                                    sess.workspace, pack=False, reason="evict")
                self.source.add_comment(ticket.id, (
                    f"[agent] attempt 被強制驅逐(即時 killpg 釋放資源;"
                    f"第 {sess.evict_count} 次);session 保留,下輪 resume "
                    f"續跑不重花錢。\n{_resume_hint(sess)}"))
                events.append(self.store.journal(
                    "evicted", ticket.id, ticket.key,
                    session=sess.session_id, count=sess.evict_count))
                log.info("%s attempt evicted(resume=%s)",
                         ticket.key, sess.session_id)
                return events

            # infrastructure failure (server 掛/連不上, N3): NOT the agent's
            # fault → roll back the attempt (don't consume it) + pending:external.
            # server comes back → next poll resumes via session_id (不漏).
            if res.error_kind == "infra":
                sess.attempts -= 1  # infra doesn't burn a task attempt
                sess.pending_reason = "external"
                self.store.upsert_session(sess)
                self.source.add_comment(ticket.id, (
                    f"[agent] pending:external — 基礎設施故障"
                    f"({res.error});不消耗重試,server 恢復後自動續。"
                    f"\n{_resume_hint(sess)}"))
                events.append(self.store.journal(
                    "pending", ticket.id, ticket.key, reason="external",
                    cause="infra"))
                return events

            # timeout 重跑:harness 自己殺的(timeout_sec 到期)→ 原因可證,
            # 使用者可設 timeout_retry_max 允許重跑(global,profile
            # agent.timeout_retry_max 覆蓋;default 0=不重跑)。語意同
            # evicted/infra:不消耗 attempt、session 留 active → 下輪 poll
            # 憑 preassigned sid resume(W5.1)。計數 per-ticket 累計不歸零
            # (防 timeout 迴圈);用完 → 落回 UNKNOWN pending 交人。
            if res.raw_outcome == "unknown" and res.error_kind == "timeout":
                retry_max = int(agent_cfg.get("timeout_retry_max",
                                              self.timeout_retry_max) or 0)
                kind_note = _TIMEOUT_KIND_DESC.get(res.timeout_kind or "", "")
                if sess.timeout_retries < retry_max:
                    sess.attempts -= 1
                    sess.timeout_retries += 1
                    self.store.upsert_session(sess)
                    self.source.add_comment(ticket.id, (
                        f"[agent] attempt 超時{kind_note}(timeout_sec 到期,"
                        f"harness 殺行程);自動重跑 "
                        f"{sess.timeout_retries}/{retry_max},"
                        f"下輪 resume 續跑。\n{_resume_hint(sess)}"))
                    events.append(self.store.journal(
                        "timeout_retry", ticket.id, ticket.key,
                        used=sess.timeout_retries, max=retry_max))
                    log.info("%s attempt timeout → 重跑 %d/%d(resume=%s)",
                             ticket.key, sess.timeout_retries, retry_max,
                             sess.session_id)
                    return events

            if res.raw_outcome == "unknown":
                sess.outcome, sess.pending_reason = "UNKNOWN", "unknown"
                self.store.upsert_session(sess)
                if res.error_kind == "timeout":
                    cause = ("attempt 超時"
                             + _TIMEOUT_KIND_DESC.get(res.timeout_kind or "",
                                                      "")
                             + (f"(timeout 重跑已用 {sess.timeout_retries} 次)"
                                if sess.timeout_retries else ""))
                else:
                    cause = "執行行程消失"
                self.source.add_comment(ticket.id, (
                    f"[agent] outcome=UNKNOWN(attempt {sess.attempts}):"
                    f"{cause},無法證明副作用是否發生。不會自動重試,"
                    f"請人工檢查後下指令。\n{_resume_hint(sess)}"))
                events.append(self.store.journal(
                    "pending", ticket.id, ticket.key, reason="unknown"))
                self._pack_transcript(sess, profile, ticket, events)  # W4.2
                self._post_deliverables(sess, ticket, "UNKNOWN", res, events)
                return events

            # F3/G1(W2.5):agent 自報 handoff(status=handoff + next)→ 不
            # grade。kind=human:交人(pending:human-decision,不排 agent 隊列);
            # kind=agent:重置 session 鎖定新 profile,下輪經 gate 重新排隊
            #(目標 require_approval 則重走審批門)。A↔B 換手迴圈由 A4 budget
            # 上限擋(cost_usd 跨換手累計、不歸零)。
            nxt = (res.structured or {}).get("next") or {}
            if ((res.structured or {}).get("status") == "handoff"
                    and nxt.get("to")):
                if nxt.get("kind") == "human":
                    sess.pending_reason = "human-decision"
                    self.store.upsert_session(sess)
                    # W4.3 離手定格:交人前 final HTML(不打包,close 才打包)
                    finalize_transcript(sess.session_id,
                                        engine_of_agent(profile.agent),
                                        sess.workspace, pack=False,
                                        reason="handoff-human")
                    # W11:assignee 恆定,不再 assign 人;要 agent 繼續 → 指令台 run
                    self.source.add_comment(ticket.id, (
                        f"[agent] handoff→human:{summarize(res.structured)}\n"
                        f"請人工接手;要 agent 繼續請用本票的指令台按 run。"
                        f"\n{_resume_hint(sess)}"))
                    events.append(self.store.journal(
                        "handoff", ticket.id, ticket.key, kind="human",
                        to=nxt["to"]))
                    log.info("%s handoff → human", ticket.key)
                    return events
                target = str(nxt["to"])
                if target in self.profiles and target != profile.name:
                    old = profile.name
                    # W4.3 離手定格:換手前把舊 agent 的 session 定格(sid/ws
                    # 即將被 reset,先取)
                    finalize_transcript(sess.session_id,
                                        engine_of_agent(profile.agent),
                                        sess.workspace, pack=False,
                                        reason="handoff-agent")
                    sess.profile = target
                    sess.session_id = None
                    sess.attempts = 0
                    sess.outcome, sess.pending_reason = None, None
                    sess.workspace = "(handoff)"   # 新 instance,下輪重 provision
                    self.store.upsert_session(sess)
                    self.source.add_comment(ticket.id, (
                        f"[agent] 同票換手(same-ticket)→ {target}:"
                        f"{summarize(res.structured)}\n"
                        f"已重置 session,下輪由 {target} 在同一張票重新排隊接手。"))
                    events.append(self.store.journal(
                        "handoff", ticket.id, ticket.key, kind="agent",
                        from_profile=old, to=target))
                    log.info("%s handoff %s → %s", ticket.key, old, target)
                    return events
                events.append(self.store.journal(     # 目標無效:當一般失敗
                    "handoff_invalid", ticket.id, ticket.key, to=target))

            verdict = grader.grade(sess.workspace)
            if verdict.passed and res.raw_outcome == "completed":
                sess.outcome, sess.pending_reason = "SUCCESS", None
                self.store.upsert_session(sess)
                checks = "\n".join(f"- {r}" for r in verdict.reasons)
                self_eval = (f"\nagent 自評:{summarize(res.structured)}"
                             if res.structured else "")
                self.source.add_comment(ticket.id, (
                    f"[agent] outcome=SUCCESS(attempt {sess.attempts},"
                    f" 累計 ${sess.cost_usd:.4f})\n驗證結果:\n{checks}{self_eval}\n"
                    f"進入 HIL(End):稍後會發一次性評分/裁決表單連結給你。"))
                # W3.5 C3:公式 v1 = est 平計(attempts>1 不折減——人也會重試)。
                # W7(R3):未設估時→預設 240 分,效益一律算得出。
                events.append(self.store.journal(
                    "resolved", ticket.id, ticket.key,
                    attempts=sess.attempts, cost_usd=sess.cost_usd,
                    human_minutes_saved=profile.est_minutes()))
                log.info("%s SUCCESS attempt=%d cost=$%.4f",
                         ticket.key, sess.attempts, sess.cost_usd)
                self._pack_transcript(sess, profile, ticket, events)  # W4.2
                self._post_deliverables(sess, ticket, "SUCCESS", res, events)
                # W11:HIL(End) 評分改由 ScoreGate 發 score_and_close 表單
                return events

            feedback = verdict.summary()
            if res.error:
                feedback += f"\nrunner error: {res.error}"
            self.store.upsert_session(sess)

            # A4/W7.3:budget 上限 — 本次未過驗證,若累計(單次/override)或當月
            # 已達上限就別再燒錢,交人(pending:budget)。通過的 attempt 已上面 return。
            # 用同一個預檢(涵蓋 per-ticket soft/hard + 月 + 全站);last attempt
            # 剛好超支也會在此擋成 budget(而非落到下方 max-attempts FAILURE)。
            blocked = self._budget_precheck(ticket, profile, sess)
            if blocked:
                events.extend(blocked)
                return events

        sess.outcome, sess.pending_reason = "FAILURE", "max-attempts"
        self.store.upsert_session(sess)
        self_eval = (f"\nagent 自評:{summarize(res.structured)}"
                     if res is not None and res.structured else "")
        self.source.add_comment(ticket.id, (
            f"[agent] outcome=FAILURE:{profile.max_attempts} 次嘗試未過驗證。"
            f"最後失敗證據:\n{feedback}{self_eval}\n"
            f"進入 HIL(End):稍後會發一次性評分/裁決表單連結給你。"))
        events.append(self.store.journal(
            "pending", ticket.id, ticket.key, reason="max-attempts"))
        log.info("%s FAILURE (max-attempts=%d, cost=$%.4f)",
                 ticket.key, profile.max_attempts, sess.cost_usd)
        self._pack_transcript(sess, profile, ticket, events)          # W4.2
        self._post_deliverables(sess, ticket, "FAILURE", res, events)
        # W11:HIL(End) 評分改由 ScoreGate 發 score_and_close 表單
        return events
