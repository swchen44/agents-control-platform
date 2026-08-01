"""Drivers: translate a specific worker's native output into AgentEvents.

Design: raw-subprocess drivers are FIRST-CLASS (the user primarily runs
`claude -p` and `codex exec` directly). An OpenHands ACP driver is sketched at
the bottom as a drop-in alternative that speaks the same Driver interface, so
the two paths can be compared side by side (see report §7).

Each driver defines:
  - build_command(task)  -> argv for the subprocess
  - normalize(native)    -> AgentEvent | None   (None = drop this native line)

The native->normalized mappings are derived from REAL captured streams
(fixtures/claude_p_real.jsonl, fixtures/codex_exec_real.jsonl). See report §6.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .events import AgentEvent, EventType


@dataclass
class Task:
    run_id: str
    prompt: str
    cwd: str
    model: str | None = None
    session_id: str | None = None       # for a deterministic id / resume
    allowed_tools: list[str] | None = None
    permission_mode: str = "acceptEdits"  # claude: acceptEdits|dontAsk|bypassPermissions...
    sandbox: str = "workspace-write"       # codex: read-only|workspace-write|danger-full-access


class Driver(Protocol):
    name: str
    def build_command(self, task: Task, resume: bool = False) -> list[str]: ...
    def normalize(self, native: dict[str, Any], run_id: str) -> AgentEvent | None: ...


# --------------------------------------------------------------------------- #
# Claude Code  (claude -p --output-format stream-json --verbose)
# --------------------------------------------------------------------------- #
class ClaudeDriver:
    """Raw `claude -p` driver. Verified against claude 2.1.206."""

    name = "claude"

    def build_command(self, task: Task, resume: bool = False) -> list[str]:
        cmd = [
            "claude", "-p", task.prompt,
            "--output-format", "stream-json",
            "--verbose",                       # required for stream-json event detail
            "--include-partial-messages",
        ]
        if task.model:
            cmd += ["--model", task.model]
        # A pre-assigned session id is the key to supervisor-side resume:
        # persist (session_id, cwd, flags) then `claude --resume <id>` after a crash.
        if task.session_id:
            cmd += (["--resume", task.session_id] if resume
                    else ["--session-id", task.session_id])
        cmd += ["--permission-mode", task.permission_mode]
        if task.allowed_tools:
            cmd += ["--allowedTools", *task.allowed_tools]
        return cmd

    def normalize(self, o: dict[str, Any], run_id: str) -> AgentEvent | None:
        t = o.get("type")
        sid = o.get("session_id")

        def ev(etype: EventType, **kw) -> AgentEvent:
            return AgentEvent(run_id=run_id, agent=self.name, type=etype,
                              session_id=sid, raw=o, **kw)

        if t == "system":
            sub = o.get("subtype")
            if sub == "init":
                return ev(EventType.RUN_STARTED)
            if sub == "thinking_tokens":
                return ev(EventType.THINKING,
                          tokens_out=o.get("estimated_tokens"))
            return ev(EventType.RAW)

        if t == "assistant":
            msg = o.get("message", {})
            usage = msg.get("usage", {}) or {}
            blocks = msg.get("content", []) or []
            # tool_use block => a tool is starting
            for b in blocks:
                if b.get("type") == "tool_use":
                    return ev(EventType.TOOL_STARTED, tool_name=b.get("name"))
            texts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
            return ev(EventType.MESSAGE,
                      text=" ".join(texts).strip() or None,
                      tokens_in=usage.get("input_tokens"),
                      tokens_out=usage.get("output_tokens"))

        if t == "user":
            # tool_result arrives as a user turn in the stream
            return ev(EventType.TOOL_FINISHED)

        if t == "rate_limit_event":
            info = o.get("rate_limit_info", {}) or {}
            if info.get("status") != "allowed":
                return ev(EventType.API_RETRY)
            return ev(EventType.RAW)

        if t == "result":
            cost = o.get("total_cost_usd")
            usage = o.get("usage", {}) or {}
            if o.get("is_error") or o.get("subtype") not in ("success", None):
                return ev(EventType.RUN_FAILED, cost_usd=cost, text=o.get("result"))
            return ev(EventType.RUN_COMPLETED, cost_usd=cost, text=o.get("result"),
                      tokens_in=usage.get("input_tokens"),
                      tokens_out=usage.get("output_tokens"))

        return ev(EventType.RAW)


# --------------------------------------------------------------------------- #
# Codex CLI  (codex exec --json)
# --------------------------------------------------------------------------- #
class CodexDriver:
    """Raw `codex exec --json` driver. Verified against codex-cli 0.142.5."""

    name = "codex"

    def build_command(self, task: Task, resume: bool = False) -> list[str]:
        base = ["codex", "exec", "--json", "--sandbox", task.sandbox,
                "--skip-git-repo-check"]
        if task.model:
            base += ["--model", task.model]
        if resume and task.session_id:
            # `codex exec resume <SESSION_ID> <PROMPT>` re-enters the same thread.
            # Verified 0.142.5: the resume subcommand rejects `--sandbox` (rc=2);
            # the sandbox must be set via a `-c` config override instead.
            return ["codex", "exec", "resume", task.session_id, "--json",
                    "--skip-git-repo-check",
                    "-c", f'sandbox_mode="{task.sandbox}"', task.prompt]
        return base + [task.prompt]

    def normalize(self, o: dict[str, Any], run_id: str) -> AgentEvent | None:
        t = o.get("type")

        def ev(etype: EventType, sid: str | None = None, **kw) -> AgentEvent:
            return AgentEvent(run_id=run_id, agent=self.name, type=etype,
                              session_id=sid, raw=o, **kw)

        if t == "thread.started":
            return ev(EventType.RUN_STARTED, sid=o.get("thread_id"))
        if t == "turn.started":
            return ev(EventType.THINKING)
        if t == "item.started":
            item = o.get("item", {}) or {}
            if item.get("type") in ("command_execution", "tool_call", "mcp_tool_call"):
                return ev(EventType.TOOL_STARTED,
                          tool_name=item.get("command") or item.get("type"))
            return ev(EventType.RAW)
        if t == "item.completed":
            item = o.get("item", {}) or {}
            itype = item.get("type")
            if itype == "agent_message":
                return ev(EventType.MESSAGE, text=item.get("text"))
            if itype in ("command_execution", "tool_call", "mcp_tool_call"):
                return ev(EventType.TOOL_FINISHED,
                          tool_name=item.get("command") or itype)
            return ev(EventType.RAW)
        if t == "turn.completed":
            # `codex exec` is single-turn non-interactive: there is NO separate
            # thread.completed event — turn.completed (then process exit) IS the
            # terminal success signal. (Contrast claude's explicit `result`.)
            # This asymmetry is exactly why the unified layer earns its keep.
            u = o.get("usage", {}) or {}
            return ev(EventType.RUN_COMPLETED,
                      tokens_in=u.get("input_tokens"),
                      tokens_out=u.get("output_tokens"))
        if t == "turn.failed" or t == "error":
            err = o.get("error", {}) or {}
            return ev(EventType.RUN_FAILED, text=err.get("message") or str(o))
        return ev(EventType.RAW)


# --------------------------------------------------------------------------- #
# OpenCode  (opencode acp  — custom stdio ACP; verified `opencode acp` exists)
# OpenHands ACP  (agent-server subprocess) — sketch of the "buy" path.
# Both would implement the same Driver Protocol; left as a documented stub so
# the report can contrast them without pretending they're tested here.
# --------------------------------------------------------------------------- #
class OpenHandsACPNote:
    """Not a runnable driver. See report §7: the OpenHands path does NOT spawn a
    raw CLI — you POST /api/conversations to an agent-server (agent_kind="acp",
    acp_server="claude-code"|"codex", or acp_command=["opencode","acp"]) and
    subscribe to WS /sockets/events/{id}. The server emits its OWN event stream
    (ACPToolCallEvent, MessageEvent, ...), so a real OpenHandsACPDriver would
    normalize THOSE, not claude/codex native JSON. Trade-offs in report §7."""


DRIVERS: dict[str, Driver] = {
    "claude": ClaudeDriver(),
    "codex": CodexDriver(),
}
