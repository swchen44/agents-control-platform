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
                 profiles: dict[str, Profile], root: str):
        self.source = source
        self.store = store
        self.profiles = profiles
        self.root = root

    def handle(self, ticket: Ticket, profile_name: str) -> list[dict]:
        """Idempotent: terminal/pending sessions are skipped silently."""
        events: list[dict] = []
        profile = self.profiles[profile_name]
        sess = self.store.get_session(ticket.id)
        if sess and (sess.outcome == "SUCCESS" or sess.pending_reason):
            return events  # done or awaiting a human — nothing to do

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
        feedback: str | None = None

        while sess.attempts < profile.max_attempts:
            sess.attempts += 1
            prompt = BASE_PROMPT if not feedback else (
                f"{BASE_PROMPT}\n\n上次嘗試未通過驗證,失敗證據:\n{feedback}\n"
                f"請只修正缺失的部分,不要重做已完成的部分。")
            res = run_attempt(profile.agent, sess.workspace, prompt,
                              artifacts, sess.attempts,
                              resume_session_id=sess.session_id)
            sess.session_id = res.session_id or sess.session_id
            sess.cost_usd += res.cost_usd or 0.0
            events.append(self.store.journal(
                "attempt_finished", ticket.id, ticket.key,
                attempt=sess.attempts, raw=res.raw_outcome,
                truly_resumed=res.truly_resumed,
                envelope=res.envelope_path))

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

        sess.outcome, sess.pending_reason = "FAILURE", "max-attempts"
        self.store.upsert_session(sess)
        self.source.add_comment(ticket.id, (
            f"[agent] outcome=FAILURE:{profile.max_attempts} 次嘗試未過驗證。"
            f"最後失敗證據:\n{feedback}\n{_resume_hint(sess)}"))
        events.append(self.store.journal(
            "pending", ticket.id, ticket.key, reason="max-attempts"))
        return events
