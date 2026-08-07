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
import sys
import time
import uuid

_A_ROUTE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))),
    "examples", "jira-agent-poc")
if _A_ROUTE not in sys.path:
    sys.path.insert(0, _A_ROUTE)

from arcp_poc.grader import AllOf, CommandGrader, FileChecklistGrader  # noqa: E402

from .contract import summarize  # noqa: E402
from .inner_runner import run_attempt  # noqa: E402
from .jira_source import JiraCloudSource  # noqa: E402
from .logutil import get_logger  # noqa: E402
from .profiles import Profile  # noqa: E402
from .scoring import collect_budget_override, write_handoff_sections  # noqa: E402
from .sections import parse as parse_sections  # noqa: E402
from .store import Store, TicketSession  # noqa: E402
from .ticket import Ticket  # noqa: E402
from .transcript import engine_of_agent  # noqa: E402
from .transcript import finalize as finalize_transcript  # noqa: E402
from .workspace import health_check, provision  # noqa: E402

BASE_PROMPT = ("請先閱讀工作目錄裡的 TICKET.md,完成其中「描述」段落交付的任務。"
               "完成後回覆一行 TASK_DONE。")

log = get_logger("dispatcher")


def _grader(profile: Profile):
    parts = []
    for step in profile.verify:
        if step.files:
            parts.append(FileChecklistGrader(step.files))
        if step.cmd:
            parts.append(CommandGrader(step.cmd))
    return AllOf(*parts)


def _resume_hint(sess: TicketSession) -> str:
    lines = [f"workspace: {sess.workspace}"]
    if sess.session_id:
        lines.append(f"acp_resume_session_id: {sess.session_id}")
    return "\n".join(lines)


class Dispatcher:
    def __init__(self, source: JiraCloudSource, store: Store,
                 profiles: dict[str, Profile], root: str,
                 server_manager=None, approval=None):
        self.source = source
        self.store = store
        self.profiles = profiles
        self.root = root
        self.server_manager = server_manager   # conc.3 long-lived shared server
        self.approval = approval               # W2.3 ApprovalGate | None

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

    def _budget_precheck(self, ticket: Ticket, profile: Profile,
                         sess: TicketSession) -> list[dict]:
        """W7.3 預算閘:此票單次(human `budget_override` 優先於 profile
        max_budget_usd)或此 profile 當月累計達上限 → pending:budget、不 spawn。
        回傳 pending 事件清單(空=可續跑)。"""
        override = collect_budget_override(ticket.description or "")
        single = override if override is not None else profile.max_budget_usd
        if single is not None and sess.cost_usd >= single:
            src = ("human budget_override" if override is not None
                   else "profile 單次上限")
            return self._budget_block(ticket, profile, sess, "single", (
                f"[agent] pending:budget(單次)— 此票累計 ${sess.cost_usd:.4f} "
                f"達{src} ${single:.4f}。放寬:在 human 段填 "
                f"`budget_override: <USD>`(僅此票);或改 Profile。"
                f"\n{_resume_hint(sess)}"))
        cap = profile.max_budget_monthly_usd
        if cap is not None:
            spent = self.store.monthly_cost(profile.name)
            if spent >= cap:
                return self._budget_block(ticket, profile, sess, "monthly", (
                    f"[agent] pending:budget(月上限)— profile «{profile.name}» "
                    f"當月已花 ${spent:.4f} 達月上限 ${cap:.4f}。"
                    f"需調整 Profile.max_budget_monthly_usd 才能續跑。"
                    f"\n{_resume_hint(sess)}"))
        return []

    def _budget_block(self, ticket: Ticket, profile: Profile,
                      sess: TicketSession, scope: str, msg: str) -> list[dict]:
        sess.pending_reason = "budget"
        self.store.upsert_session(sess)
        self.source.add_comment(ticket.id, msg)
        ev = [self.store.journal("pending", ticket.id, ticket.key,
                                 reason="budget", scope=scope,
                                 cost_usd=sess.cost_usd)]
        finalize_transcript(sess.session_id,          # W6.4 等人類也產 transcript
                            engine_of_agent(profile.agent),
                            sess.workspace, pack=False, reason="pending:budget")
        return ev

    def _handoff_for_scoring(self, ticket: Ticket, profile: Profile,
                             sess: TicketSession) -> None:
        """W7.2:終態(SUCCESS/FAILURE)交人評分——把 goal 寫進 agent 段 + seed
        human score placeholder;best-effort 交人 assignee;記首次「催評時間」為現在,
        讓 ScoreGate 首次催評延後一輪(∵ 終態留言本身已提示評分),不立刻重催。"""
        try:
            write_handoff_sections(self.source, ticket, profile)
        except Exception as e:  # noqa: BLE001 — 寫 section 壞不擋結案流程
            log.warning("%s 交人評分 section 寫入失敗:%s", ticket.key, e)
        assignee = self._human_assignee(ticket, profile)
        if assignee and (ticket.assignee_id or "") != assignee:
            try:
                self.source.assign(ticket.id, assignee)
            except Exception as e:  # noqa: BLE001
                log.warning("%s 交人 assign 失敗:%s", ticket.key, e)
        sess.score_reminded_at = time.time()
        self.store.upsert_session(sess)

    def _human_assignee(self, ticket: Ticket, profile: Profile) -> str | None:
        """轉人類時的 assignee:description human 段 `human_email` →
        profile.approver(fallback 鏈)。email 經 user-search 解析成 accountId,
        解析不到 → 試下一候選;離線 mock(無 user-search)→ 原樣回。"""
        _b, secs, _a = parse_sections(ticket.description or "")
        email = next((str(s.data().get("human_email") or "").strip()
                      for s in secs if s.owner == "human"), "")
        find = getattr(self.source, "find_account_id", None)
        for cand in (email, profile.approver or ""):
            if not cand:
                continue
            if "@" in cand and find is not None:
                acct = find(cand)
                if acct:
                    return acct
                continue               # 解析不到 → fallback 下一候選
            return cand                # 已是 accountId(或離線 mock)
        return None

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
        """Idempotent: terminal/pending sessions are skipped silently."""
        events: list[dict] = []
        profile = self.profiles[profile_name]
        sess = self.store.get_session(ticket.id)
        # F3(W2.5):session pin 的 profile 優先於 route 推導——換手後 route
        # 標籤仍指舊 profile,session 存在即以其 profile 為準
        if (sess is not None and sess.profile != profile.name
                and sess.profile in self.profiles):
            profile = self.profiles[sess.profile]
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
                sess = TicketSession(
                    issue_id=ticket.id, key=ticket.key, profile=profile.name,
                    workspace="(pending-approval)", session_id=None, attempts=0,
                    outcome=None, pending_reason=None, cost_usd=0.0)
            decision = self.approval.gate(ticket, profile, sess)
            self.store.upsert_session(sess)
            events.append(self.store.journal(
                "approval", ticket.id, ticket.key, decision=decision,
                revisions=sess.approval_revisions))
            if decision != "proceed":
                return events
            sess.workspace = provision(self.root, ticket, profile)
            self.store.upsert_session(sess)

        if sess and (sess.outcome in ("SUCCESS", "ABORTED")
                     or sess.pending_reason or sess.inactive):
            # done/cancelled、awaiting a human,或 W12 inactive(assignee 在
            # 人類手上 = 資源開關關閉,不派工)— nothing to do
            return events

        if sess is None:
            ws = provision(self.root, ticket, profile)
            sess = TicketSession(
                issue_id=ticket.id, key=ticket.key, profile=profile.name,
                workspace=ws, session_id=None, attempts=0,
                outcome=None, pending_reason=None, cost_usd=0.0)
            self.store.upsert_session(sess)
            events.append(self.store.journal(
                "session_created", ticket.id, ticket.key,
                profile=profile.name, workspace=ws))
        else:
            healthy, reason = health_check(sess.workspace, ticket)
            if not healthy:
                # 重建(empty-template 安全)/ 換手哨值「(handoff)」→ 依現行
                # profile 重 provision 新 instance;路徑要回存(換手後路徑不同)
                events.append(self.store.journal(
                    "workspace_unhealthy", ticket.id, ticket.key,
                    reason=reason))
                sess.workspace = provision(self.root, ticket, profile)
                self.store.upsert_session(sess)

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
            # W7.3:spawn 前預算閘——此票單次(human budget_override 優先)或此
            # profile 當月累計達上限 → pending:budget、不 spawn(跑前擋才不多燒)
            blocked = self._budget_precheck(ticket, profile, sess)
            if blocked:
                events.extend(blocked)
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
            res = run_attempt(agent_cfg, sess.workspace, prompt,
                              artifacts, sess.attempts,
                              resume_session_id=resume_sid,
                              preassigned_session_id=preassigned)
            sess.session_id = res.session_id or sess.session_id
            sess.cost_usd += res.cost_usd or 0.0
            events.append(self.store.journal(
                "attempt_finished", ticket.id, ticket.key,
                attempt=sess.attempts, raw=res.raw_outcome,
                error_kind=res.error_kind,
                truly_resumed=res.truly_resumed,
                structured=res.structured,               # G1:agent 自評(記錄)
                envelope=res.envelope_path,
                cost=res.cost_usd or 0.0,                # W7.3 月預算彙總資料源
                profile=profile.name))

            # E3(W5.3):被主動驅逐(control /evict → killpg)——非故障,
            # 不消耗 attempt;session 留 active,下輪 native resume 續跑
            # (若 evict 是配合交人,下輪 external policy 會標 inactive 擋住)
            if res.error_kind == "evicted":
                sess.attempts -= 1
                sess.evict_count += 1           # W6.3:異常計數
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

            if res.raw_outcome == "unknown":
                sess.outcome, sess.pending_reason = "UNKNOWN", "unknown"
                self.store.upsert_session(sess)
                self.source.add_comment(ticket.id, (
                    f"[agent] outcome=UNKNOWN(attempt {sess.attempts}):"
                    f"執行行程消失,無法證明副作用是否發生。不會自動重試,"
                    f"請人工檢查後下指令。\n{_resume_hint(sess)}"))
                events.append(self.store.journal(
                    "pending", ticket.id, ticket.key, reason="unknown"))
                self._pack_transcript(sess, profile, ticket, events)  # W4.2
                return events

            # F3/G1(W2.5):agent 自報 handoff(status=handoff + next)→ 不
            # grade。kind=human:交人(pending:human-decision,不排 agent 隊列);
            # kind=agent:重置 session pin 新 profile,下輪經 gate 重新排隊
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
                    self.source.add_comment(ticket.id, (
                        f"[agent] handoff→human:{summarize(res.structured)}\n"
                        f"請人工接手;要 agent 繼續請留言 @agent run。"
                        f"\n{_resume_hint(sess)}"))
                    # assignee 不信 agent 自由文字 next.to:human 段
                    # human_email → approver(fallback);解析不到就不改
                    assignee = self._human_assignee(ticket, profile)
                    if assignee:
                        self.source.assign(ticket.id, assignee)
                    events.append(self.store.journal(
                        "handoff", ticket.id, ticket.key, kind="human",
                        to=nxt["to"], assignee=assignee))
                    log.info("%s handoff → human(assignee=%s)",
                             ticket.key, assignee)
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
                        f"[agent] handoff→{target}:{summarize(res.structured)}\n"
                        f"已重置 session,下輪由 {target} 重新排隊接手。"))
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
                    f"請在 description 的 human 段填 `score: <0–10>` 給完成度評分。\n"
                    f"{_resume_hint(sess)}"))
                # W3.5 C3:公式 v1 = est 平計(attempts>1 不折減——人也會重試)。
                # W7(R3):未設估時→預設 240 分,效益一律算得出。
                events.append(self.store.journal(
                    "resolved", ticket.id, ticket.key,
                    attempts=sess.attempts, cost_usd=sess.cost_usd,
                    human_minutes_saved=profile.est_minutes()))
                log.info("%s SUCCESS attempt=%d cost=$%.4f",
                         ticket.key, sess.attempts, sess.cost_usd)
                self._pack_transcript(sess, profile, ticket, events)  # W4.2
                self._handoff_for_scoring(ticket, profile, sess)      # W7.2
                return events

            feedback = verdict.summary()
            if res.error:
                feedback += f"\nrunner error: {res.error}"
            self.store.upsert_session(sess)

            # A4/W7.3:budget 上限 — 本次未過驗證,若累計(單次/override)或當月
            # 已達上限就別再燒錢,交人(pending:budget)。通過的 attempt 已上面 return。
            # 用同一個預檢(涵蓋 單次 + human budget_override + 月上限);last attempt
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
            f"請在 description 的 human 段填 `score: <0–10>` 評 agent 幫了多少"
            f"(反映還有多少 gap)。\n{_resume_hint(sess)}"))
        events.append(self.store.journal(
            "pending", ticket.id, ticket.key, reason="max-attempts"))
        log.info("%s FAILURE (max-attempts=%d, cost=$%.4f)",
                 ticket.key, profile.max_attempts, sess.cost_usd)
        self._pack_transcript(sess, profile, ticket, events)          # W4.2
        self._handoff_for_scoring(ticket, profile, sess)              # W7.2
        return events
