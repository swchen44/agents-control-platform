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
    engine: str = "claude"                 # claude | codex (C.2)
    model: str | None = "haiku"
    permission_mode: str = "acceptEdits"
    allowed_tools: list[str] = Field(
        default_factory=lambda: ["Write", "Read", "Bash(sleep:*)"])
    session_id: str | None = None          # pre-assigned for resume (C.4)
    resume: bool = False

    # exposed to the runner after step() (C.3 envelope)
    _final_session_id: str | None = PrivateAttr(default=None)
    _cost_usd: float | None = PrivateAttr(default=None)
    _error: str | None = PrivateAttr(default=None)

    def init_state(self, state, on_event) -> None:  # noqa: ARG002
        return  # the CLI brings its own tools; no SDK tool resolution

    # -- command (ported from arcp_poc.drivers.ClaudeDriver) --------------- #
    def _build_command(self, prompt: str) -> list[str]:
        sid = self.session_id or str(uuid.uuid4())
        self.__dict__["session_id"] = sid  # remember for the envelope
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

        proc = subprocess.Popen(
            self._build_command(prompt), cwd=wd,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, text=True, bufsize=1,
            start_new_session=True)
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._ingest(o, on_event)
        proc.wait()

        if self._final_session_id:
            self.__dict__["session_id"] = self._final_session_id
        state.execution_status = ConversationExecutionStatus.FINISHED

    # -- stream-json → OpenHands events (C.1 basic; C.2 fine-grained) ------- #
    def _ingest(self, o: dict, on_event) -> None:
        t = o.get("type")
        if o.get("session_id"):
            self._final_session_id = o["session_id"]
        if t == "assistant":
            blocks = (o.get("message") or {}).get("content") or []
            texts = [b.get("text", "") for b in blocks
                     if isinstance(b, dict) and b.get("type") == "text"]
            tools = [b.get("name") for b in blocks
                     if isinstance(b, dict) and b.get("type") == "tool_use"]
            body = " ".join(t for t in texts if t).strip()
            if tools:
                body = (body + " " if body else "") + \
                    "🔧 " + ", ".join(str(x) for x in tools)
            if body:
                on_event(MessageEvent(
                    source="agent",
                    llm_message=Message(role="assistant",
                                        content=[TextContent(text=body)])))
        elif t == "result":
            self._cost_usd = o.get("total_cost_usd")
            if o.get("is_error"):
                self._error = str(o.get("result") or "cli error")[:300]
