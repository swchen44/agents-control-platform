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
import signal
import subprocess
import threading
import time
import uuid

from openhands.sdk import Message, TextContent
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.llm import LLM, content_to_str
from pydantic import Field, PrivateAttr


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
    # execution isolation (no docker). claude has NO built-in OS sandbox, so we
    # wrap it in macOS seatbelt (sandbox-exec) confining file-write to the
    # workspace. codex has its OWN --sandbox (below) so os_sandbox is a no-op
    # for it. Verified: workspace writes pass, /tmp writes are blocked.
    os_sandbox: bool = False                # claude: macOS sandbox-exec wrap
    # fault injection (TEST ONLY; None in production): kill the CLI child once
    # <file> appears, +delay — mirrors A-route KillTrigger for the resume matrix
    fault_kill_on_file: str | None = None
    fault_delay: float = 1.0
    # stall watchdog (N13, ported from A-route supervisor._watchdog): if no
    # event advances the run for stall_seconds, EXIT (killpg) so the harness
    # can resume instead of hanging. 0 = off. "slow is legal; stalled is not."
    stall_seconds: float = 0.0
    # G1:structured-output 契約(DESIGN §4.2)。dict=JSON Schema 傳給 CLI 強制
    # 結構化輸出;None=關(向後相容,行為同現狀)。
    output_schema: dict | None = None

    # exposed to the runner after step() (C.3 envelope)
    _final_session_id: str | None = PrivateAttr(default=None)
    _cost_usd: float | None = PrivateAttr(default=None)
    _error: str | None = PrivateAttr(default=None)
    _structured: dict | None = PrivateAttr(default=None)   # G1 agent 自評
    # terminal event seen (claude `result` / codex `turn.completed`). A crash
    # kills the child BEFORE this → completed stays False even though the
    # process "ended" (A-route SIGTERM-rc=0 lesson, RawCLIAgent edition).
    _got_terminal: bool = PrivateAttr(default=False)
    _stalled: bool = PrivateAttr(default=False)
    _last_progress: float = PrivateAttr(default=0.0)
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
            if self.output_schema:                       # G1:codex 要檔案路徑
                base += ["--output-schema", self._schema_file()]
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
        if self.output_schema:                           # G1:claude 收 inline JSON
            cmd += ["--json-schema", json.dumps(self.output_schema)]
        if self.allowed_tools:
            cmd += ["--allowedTools", *self.allowed_tools]
        return cmd

    def _schema_file(self) -> str:
        """Write output_schema to a temp file for codex --output-schema <FILE>."""
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".schema.json", prefix="arcp-")
        with os.fdopen(fd, "w") as f:
            json.dump(self.output_schema, f)
        return path

    def step(self, conversation, on_event, on_token=None) -> None:  # noqa: ARG002
        state = conversation.state
        prompt = "Reply with exactly: pong"
        for ev in reversed(list(state.events)):
            if isinstance(ev, MessageEvent) and ev.source == "user":
                prompt = " ".join(content_to_str(ev.llm_message.content))
                break
        wd = getattr(getattr(state, "workspace", None), "working_dir", None) \
            or os.getcwd()

        cmd = self._build_command(prompt)
        if self.os_sandbox and self.engine == "claude":
            cmd = ["sandbox-exec", "-f", self._write_sandbox_profile(wd)] + cmd
        raw_f = open(self.raw_events_path, "w") if self.raw_events_path else None
        proc = subprocess.Popen(
            cmd, cwd=wd,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, text=True, bufsize=1,
            start_new_session=True)
        self._last_progress = time.time()
        if self.fault_kill_on_file:
            threading.Thread(target=self._fault_kill, args=(proc, wd),
                             daemon=True).start()
        if self.stall_seconds > 0:
            threading.Thread(target=self._stall_watchdog, args=(proc,),
                             daemon=True).start()
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                # ANY stream line = progress (partial token deltas included):
                # "slow is legal" — model still producing. Only a TOOL running
                # with zero output (e.g. a hung command) starves progress →
                # stall. (Bug found: emitting-only reset killed live streaming.)
                self._last_progress = time.time()
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

    # -- stall watchdog (N13, reset-on-progress) --------------------------- #
    def _stall_watchdog(self, proc) -> None:
        """No event for stall_seconds → EXIT (killpg) so the harness resumes."""
        while proc.poll() is None:
            time.sleep(1.0)
            if time.time() - self._last_progress > self.stall_seconds:
                self._stalled = True
                self._error = (f"stalled: no progress for "
                               f"{self.stall_seconds:.0f}s")
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
                return

    # -- execution isolation (macOS seatbelt, claude) ---------------------- #
    def _write_sandbox_profile(self, wd: str) -> str:
        """Write a seatbelt profile confining file-write to the workspace.

        allow default + deny file-write* + whitelist (workspace, claude's own
        state dirs, TMPDIR). NOTE: never whitelist /private/tmp — /tmp symlinks
        to it and that reopens the escape (spike found this). Verified: writes
        outside the workspace are blocked, claude itself runs fine.
        """
        home = os.path.expanduser("~")
        prof = (
            "(version 1)\n(allow default)\n(deny file-write*)\n"
            "(allow file-write*\n"
            f'  (subpath "{os.path.abspath(wd)}")\n'
            f'  (subpath "{home}/.claude")\n'
            f'  (subpath "{home}/.config")\n'
            f'  (subpath "{home}/.npm")\n'
            '  (subpath "/private/var/folders")\n'
            '  (literal "/dev/null") (literal "/dev/stdout")\n'
            '  (literal "/dev/stderr") (literal "/dev/dtracehelper")\n'
            '  (literal "/dev/tty"))\n')
        path = os.path.join(os.path.dirname(os.path.abspath(wd)),
                            ".arcp_sandbox.sb")
        with open(path, "w") as f:
            f.write(prof)
        return path

    # -- fault injection (test only) --------------------------------------- #
    def _fault_kill(self, proc, wd: str) -> None:
        target = os.path.join(wd, self.fault_kill_on_file or "")
        end = time.time() + 120
        while time.time() < end and proc.poll() is None:
            if os.path.exists(target):
                time.sleep(self.fault_delay)
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
                return
            time.sleep(0.1)

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
            self._got_terminal = True
            self._cost_usd = o.get("total_cost_usd")
            if o.get("structured_output") is not None:   # G1:claude 直接給物件
                self._structured = o.get("structured_output")
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
                if self.output_schema:                   # G1:最終訊息即 schema JSON
                    try:
                        self._structured = json.loads(item.get("text") or "")
                    except (ValueError, TypeError):
                        pass
            elif item.get("type") in ("command_execution", "tool_call"):
                self._emit(on_event, f"📋 {str(item.get('type'))[:80]}")
        elif t == "turn.completed":
            self._got_terminal = True
            u = o.get("usage") or {}
            self._cost_usd = u.get("total_cost_usd") or self._cost_usd
            # 瞬態 error(stream 斷線等)被 CLI 自己重連救回 → turn 仍完成,
            # 不該污染 envelope(W3.1 實測:Reconnecting 3/5 後成功)。
            # turn.failed 是終態、之後不會有 turn.completed,不受影響。
            self._error = None
        elif t in ("turn.failed", "error"):
            self._error = str((o.get("error") or {}).get("message") or o)[:300]
