"""RawCLIAgent (C.1 minimal impl): spawn `claude -p` stream-json, emit events.

step() runs one full `claude -p` invocation to completion, parsing the
stream-json lines into OpenHands events. C.1 emits the basic set (assistant
MessageEvent + a summary of tool calls) and finishes; C.2 wires the full
fine-grained mapping (thinking/token deltas, per-tool ActionEvent/Observation)
by porting arcp_poc.drivers, and adds the codex engine.

session id / cost are exposed on the instance for the runner's envelope
(C.3); a pre-assigned --session-id makes crash→resume possible (C.4).
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid

from pydantic import Field, PrivateAttr

from openhands.sdk import Message, TextContent
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.llm import LLM, content_to_str


def _dummy_llm() -> LLM:
    # the LLM field is mandatory on AgentBase but never called — the CLI is
    # the model (same trick ACPAgent uses).
    try:
        return LLM(model="rawcli", usage_id="rawcli")
    except Exception:
        return LLM(model="rawcli")


class RawCLIAgent(AgentBase):
    """Custom agent whose execution unit is `claude -p` (print mode)."""

    llm: LLM = Field(default_factory=_dummy_llm)
    engine: str = "claude"                 # claude | codex
    model: str | None = "haiku"
    permission_mode: str = "acceptEdits"
    sandbox: str = "workspace-write"       # codex
    allowed_tools: list[str] = Field(
        default_factory=lambda: ["Write", "Read", "Bash(sleep:*)"])
    session_id: str | None = None          # pre-assigned for resume (C.4)
    resume: bool = False
    raw_events_path: str | None = None      # full-fidelity stream dump (L3)

    # exposed to the runner after step() (C.3 envelope)
    _final_session_id: str | None = PrivateAttr(default=None)
    _cost_usd: float | None = PrivateAttr(default=None)
    _error: str | None = PrivateAttr(default=None)
    _raw_count: int = PrivateAttr(default=0)
    _event_count: int = PrivateAttr(default=0)

    def init_state(self, state, on_event) -> None:  # noqa: ARG002
        return  # the CLI brings its own tools; no SDK tool resolution

    # -- command (ported from arcp_poc.drivers) ---------------------------- #
    def _build_command(self, prompt: str) -> list[str]:
        sid = self.session_id or str(uuid.uuid4())
        self.__dict__["session_id"] = sid  # remember for the envelope
        if self.engine == "codex":
            base = ["codex", "exec", "--json", "--sandbox", self.sandbox,
                    "--skip-git-repo-check"]
            if self.model:
                base += ["--model", self.model]
            if self.resume:
                return ["codex", "exec", "resume", sid, "--json",
                        "--skip-git-repo-check",
                        "-c", f'sandbox_mode="{self.sandbox}"', prompt]
            return base + [prompt]
        cmd = ["claude", "-p", prompt, "--output-format", "stream-json",
               "--verbose", "--include-partial-messages"]
        if self.model:
            cmd += ["--model", self.model]
        cmd += (["--resume", sid] if self.resume else ["--session-id", sid])
        cmd += ["--permission-mode", self.permission_mode]
        if self.allowed_tools:
            cmd += ["--allowedTools", *self.allowed_tools]
        return cmd

    def step(self, conversation, on_event, on_token=None) -> None:  # noqa: ARG002
        state = conversation.state
        prompt = "Reply with exactly: pong"
        for ev in reversed(list(state.events)):
            if isinstance(ev, MessageEvent) and ev.source == "user":
                prompt = " ".join(content_to_str(ev.llm_message.content))
                break
        wd = getattr(getattr(state, "workspace", None), "working_dir", None) \
            or os.getcwd()

        raw_f = open(self.raw_events_path, "w") if self.raw_events_path else None
        proc = subprocess.Popen(
            self._build_command(prompt), cwd=wd,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, text=True, bufsize=1,
            start_new_session=True)
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._raw_count += 1
                if raw_f:                     # full A-level fidelity preserved
                    raw_f.write(json.dumps(o, ensure_ascii=False) + "\n")
                (self._ingest_codex if self.engine == "codex"
                 else self._ingest_claude)(o, on_event)
        finally:
            if raw_f:
                raw_f.close()
        proc.wait()

        if self._final_session_id:
            self.__dict__["session_id"] = self._final_session_id
        state.execution_status = ConversationExecutionStatus.FINISHED

    # -- helpers ----------------------------------------------------------- #
    def _emit(self, on_event, text: str) -> None:
        if not text.strip():
            return
        self._event_count += 1
        on_event(MessageEvent(
            source="agent",
            llm_message=Message(role="assistant",
                                content=[TextContent(text=text)])))

    # -- fine-grained stream-json → OpenHands events (C.2) ----------------- #
    def _ingest_claude(self, o: dict, on_event) -> None:
        t = o.get("type")
        if o.get("session_id"):
            self._final_session_id = o["session_id"]
        if t == "assistant":
            for b in (o.get("message") or {}).get("content") or []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and b.get("text", "").strip():
                    self._emit(on_event, b["text"])
                elif b.get("type") == "tool_use":
                    inp = b.get("input") or {}
                    hint = inp.get("file_path") or inp.get("command") or ""
                    self._emit(on_event,
                               f"🔧 {b.get('name')} {str(hint)[:80]}")
                elif b.get("type") == "thinking" and b.get("thinking"):
                    self._emit(on_event, f"💭 {b['thinking'][:200]}")
        elif t == "user":
            for b in (o.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    c = b.get("content")
                    if isinstance(c, list):
                        c = " ".join(x.get("text", "") for x in c
                                     if isinstance(x, dict))
                    self._emit(on_event, f"📋 {str(c or '')[:120]}")
        elif t == "result":
            self._cost_usd = o.get("total_cost_usd")
            if o.get("is_error"):
                self._error = str(o.get("result") or "cli error")[:300]

    def _ingest_codex(self, o: dict, on_event) -> None:
        t = o.get("type")
        if t == "thread.started":
            self._final_session_id = o.get("thread_id")
        elif t == "item.started":
            item = o.get("item") or {}
            if item.get("type") in ("command_execution", "tool_call",
                                    "mcp_tool_call"):
                self._emit(on_event,
                           f"🔧 {str(item.get('command') or item.get('type'))[:80]}")
        elif t == "item.completed":
            item = o.get("item") or {}
            if item.get("type") == "agent_message":
                self._emit(on_event, item.get("text", ""))
            elif item.get("type") in ("command_execution", "tool_call"):
                self._emit(on_event, f"📋 {str(item.get('type'))[:80]}")
        elif t == "turn.completed":
            u = o.get("usage") or {}
            self._cost_usd = u.get("total_cost_usd") or self._cost_usd
        elif t in ("turn.failed", "error"):
            self._error = str((o.get("error") or {}).get("message") or o)[:300]
