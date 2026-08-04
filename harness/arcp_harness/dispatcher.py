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

_A_ROUTE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))),
    "examples", "jira-agent-poc")
if _A_ROUTE not in sys.path:
    sys.path.insert(0, _A_ROUTE)

from arcp_poc.grader import AllOf, CommandGrader, FileChecklistGrader  # noqa: E402

from .inner_runner import run_attempt  # noqa: E402
from .jira_source import JiraCloudSource  # noqa: E402
from .profiles import Profile  # noqa: E402
from .store import Store, TicketSession  # noqa: E402
from .ticket import Ticket  # noqa: E402
from .workspace import health_check, provision  # noqa: E402

BASE_PROMPT = ("請先閱讀工作目錄裡的 TICKET.md,完成其中「描述」段落交付的任務。"
               "完成後回覆一行 TASK_DONE。")


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
                 server_manager=None):
        self.source = source
        self.store = store
        self.profiles = profiles
        self.root = root
        self.server_manager = server_manager   # conc.3 long-lived shared server

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
        # auto-recover pending:external once infra is back (N1/N3): server
        # healthy again → clear the block and resume this poll (不漏)
        if (sess and sess.pending_reason == "external"
                and self.server_manager is not None
                and self.server_manager.ensure()):
            sess.pending_reason = None
            self.store.upsert_session(sess)
            events.append(self.store.journal(
                "external_cleared", ticket.id, ticket.key, cause="server-back"))
        if sess and (sess.outcome in ("SUCCESS", "ABORTED")
                     or sess.pending_reason):
            return events  # done/cancelled or awaiting a human — nothing to do

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
                # empty-template workspaces are safe to rebuild; journal it
                events.append(self.store.journal(
                    "workspace_unhealthy", ticket.id, ticket.key,
                    reason=reason))
                provision(self.root, ticket, profile)

        grader = _grader(profile)
        artifacts = os.path.join(os.path.dirname(sess.workspace), "attempts")
        agent_cfg = self._effective_agent(profile)  # conc.3 server injection
        feedback: str | None = None

        while sess.attempts < profile.max_attempts:
            sess.attempts += 1
            prompt = BASE_PROMPT if not feedback else (
                f"{BASE_PROMPT}\n\n上次嘗試未通過驗證,失敗證據:\n{feedback}\n"
                f"請只修正缺失的部分,不要重做已完成的部分。")
            res = run_attempt(agent_cfg, sess.workspace, prompt,
                              artifacts, sess.attempts,
                              resume_session_id=sess.session_id)
            sess.session_id = res.session_id or sess.session_id
            sess.cost_usd += res.cost_usd or 0.0
            events.append(self.store.journal(
                "attempt_finished", ticket.id, ticket.key,
                attempt=sess.attempts, raw=res.raw_outcome,
                error_kind=res.error_kind,
                truly_resumed=res.truly_resumed,
                envelope=res.envelope_path))

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
                return events

            verdict = grader.grade(sess.workspace)
            if verdict.passed and res.raw_outcome == "completed":
                sess.outcome, sess.pending_reason = "SUCCESS", None
                self.store.upsert_session(sess)
                checks = "\n".join(f"- {r}" for r in verdict.reasons)
                self.source.add_comment(ticket.id, (
                    f"[agent] outcome=SUCCESS(attempt {sess.attempts},"
                    f" 累計 ${sess.cost_usd:.4f})\n驗證結果:\n{checks}\n"
                    f"{_resume_hint(sess)}"))
                events.append(self.store.journal(
                    "resolved", ticket.id, ticket.key,
                    attempts=sess.attempts, cost_usd=sess.cost_usd))
                return events

            feedback = verdict.summary()
            if res.error:
                feedback += f"\nrunner error: {res.error}"
            self.store.upsert_session(sess)

            # A4:budget 上限 — 本次未過驗證,若累計花費達上限就別再燒錢,交
            # 人工(pending:budget)。通過的 attempt 已在上面 return SUCCESS。
            if (profile.max_budget_usd is not None
                    and sess.cost_usd >= profile.max_budget_usd):
                sess.pending_reason = "budget"
                self.store.upsert_session(sess)
                self.source.add_comment(ticket.id, (
                    f"[agent] pending:budget — 累計 ${sess.cost_usd:.4f} 達上限 "
                    f"${profile.max_budget_usd:.4f}(attempt {sess.attempts})。"
                    f"不再自動重試,請人工檢查後解除。\n{_resume_hint(sess)}"))
                events.append(self.store.journal(
                    "pending", ticket.id, ticket.key, reason="budget",
                    cost_usd=sess.cost_usd))
                return events

        sess.outcome, sess.pending_reason = "FAILURE", "max-attempts"
        self.store.upsert_session(sess)
        self.source.add_comment(ticket.id, (
            f"[agent] outcome=FAILURE:{profile.max_attempts} 次嘗試未過驗證。"
            f"最後失敗證據:\n{feedback}\n{_resume_hint(sess)}"))
        events.append(self.store.journal(
            "pending", ticket.id, ticket.key, reason="max-attempts"))
        return events
