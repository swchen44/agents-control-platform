"""Escalation loop (FR-C4 / FR-L5): denials → Jira ticket; terminal → comment.

Measured basis (§9.3-3): headless claude NEVER blocks waiting for an approval
— a denial is an immediate in-stream tool_result (normalized by the driver to
WAITING_PERMISSION) plus a structured `permission_denials` list on the final
`result` event. Escalation is therefore EVENT-driven, not stall-driven: react
to denial events as they happen, then write the structured truth back when the
run ends.

The observer opens ONE ticket per run (first denial) and appends further
denials as comments. On the terminal event it posts the outcome — including
the structured denial list from the raw result event and a copy-pasteable
resume command — to the originating issue (or the escalation ticket).

JiraClient is a two-method protocol; DryRunJiraClient appends JSONL to an
outbox file so the whole loop is verifiable offline against captured real
streams (fixtures/claude_p_denial_real.jsonl). A REST implementation slots in
without touching the observer.
"""

from __future__ import annotations

import json
from typing import Protocol

from .events import AgentEvent, EventType
from .supervisor import RunHandle

TERMINAL_EVENTS = {EventType.RUN_COMPLETED, EventType.RUN_FAILED}


class JiraClient(Protocol):
    def create_ticket(self, summary: str, description: str) -> str: ...
    def comment(self, issue_key: str, body: str) -> None: ...


class DryRunJiraClient:
    """Writes every would-be Jira call to an append-only JSONL outbox."""

    def __init__(self, outbox_path: str):
        self.outbox_path = outbox_path
        self._n = 0

    def _write(self, record: dict) -> None:
        with open(self.outbox_path, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def create_ticket(self, summary: str, description: str) -> str:
        self._n += 1
        key = f"DRY-{self._n}"
        self._write({"action": "create_ticket", "key": key,
                     "summary": summary, "description": description})
        return key

    def comment(self, issue_key: str, body: str) -> None:
        self._write({"action": "comment", "issue_key": issue_key, "body": body})


def resume_hint(h: RunHandle) -> str:
    """Copy-pasteable takeover instructions for the engineer (FR-C4)."""
    lines = [f"workspace: {h.cwd}"]
    if h.session_id:
        cmd = (f"claude --resume {h.session_id}" if h.agent == "claude"
               else f"codex exec resume {h.session_id}")
        lines.append(f"resume: cd {h.cwd} && {cmd}")
    return "\n".join(lines)


class EscalationObserver:
    """Supervisor observer wiring denial events and outcomes to Jira."""

    def __init__(self, jira: JiraClient, issue_key: str | None = None):
        self.jira = jira
        self.issue_key = issue_key      # the originating Jira issue, if any
        self.ticket: str | None = None  # escalation ticket, opened on demand

    def __call__(self, event: AgentEvent, h: RunHandle) -> None:
        if event.type == EventType.WAITING_PERMISSION:
            detail = (f"tool: {event.tool_name or '(unknown)'}\n"
                      f"denial: {event.text or ''}\n{resume_hint(h)}")
            if self.ticket is None:
                self.ticket = self.jira.create_ticket(
                    f"[ARCP] run {h.run_id}: permission denied "
                    f"({event.tool_name or 'tool'})", detail)
            else:
                self.jira.comment(self.ticket, detail)
        elif event.type in TERMINAL_EVENTS:
            target = self.issue_key or self.ticket
            if target is None:
                return  # nothing to report to — no issue, no escalation
            denials = (event.raw or {}).get("permission_denials") or []
            body = (f"run {h.run_id}: {h.state.value}"
                    f" (cost ${h.cost_usd:.4f})\n")
            if denials:
                tools = ", ".join(d.get("tool_name", "?") for d in denials)
                body += f"permission_denials ({len(denials)}): {tools}\n"
            if event.text:
                body += f"result: {event.text[:300]}\n"
            body += resume_hint(h)
            self.jira.comment(target, body)
