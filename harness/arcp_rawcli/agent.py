"""RawCLIAgent — spawn `claude -p` / `codex exec`, parse stream-json → events.

W5.5:純 stdlib(零 OpenHands 依賴)。`run(prompt, ws, on_event)` 跑一次完整
CLI 呼叫到結束,把原生 stream-json 解析成細粒度事件(dict,經 on_event 回傳,
runner 落 JSONL——與舊 SDK MessageEvent 同形,dashboard 零改)。

session id / cost / structured 曝在 instance 上供 runner 組 envelope;預派的
`--session-id` 讓 crash→resume 可行(W5.1)。
"""

from __future__ import annotations

import datetime
import json
import os
import signal
import subprocess
import threading
import time
import uuid


def _msg_event(text: str, source: str) -> dict:
    """細粒度事件(舊 SDK MessageEvent 的 JSONL 子集;dashboard 只讀
    kind/source/llm_message.content,故保留這三者即可)。"""
    return {
        "kind": "MessageEvent",
        "id": str(uuid.uuid4()),
        "timestamp": datetime.datetime.now().isoformat(),
        "source": source,
        "parent_id": None,
        "llm_message": {"role": "assistant" if source == "agent" else "user",
                        "content": [{"type": "text", "text": text}]},
    }


class RawCLIAgent:
    """執行單元 = `claude -p`(print 模式)/ `codex exec`。純 stdlib。"""

    def __init__(self, engine: str = "claude", model: str | None = "haiku",
                 permission_mode: str = "acceptEdits",
                 sandbox: str = "workspace-write",
                 allowed_tools: list[str] | None = None,
                 session_id: str | None = None, resume: bool = False,
                 raw_events_path: str | None = None, os_sandbox: bool = False,
                 fault_kill_on_file: str | None = None, fault_delay: float = 1.0,
                 evict_file: str | None = None, stall_seconds: float = 0.0,
                 output_schema: dict | None = None):
        self.engine = engine                   # claude | codex
        self.model = model
        self.permission_mode = permission_mode
        self.sandbox = sandbox                 # codex
        self.allowed_tools = (allowed_tools if allowed_tools is not None
                              else ["Write", "Read", "Bash(sleep:*)"])
        self.session_id = session_id           # pre-assigned for resume (W5.1)
        self.resume = resume
        self.raw_events_path = raw_events_path  # full-fidelity stream dump (L3)
        # 執行隔離(無 docker)。claude 無內建 OS sandbox → macOS seatbelt 包住
        # 檔案寫入限於 workspace;codex 有自己的 --sandbox 故 os_sandbox 對它無效。
        self.os_sandbox = os_sandbox
        # fault injection(TEST ONLY;生產為 None):<file> 出現即 kill CLI 子進程
        self.fault_kill_on_file = fault_kill_on_file
        self.fault_delay = fault_delay
        # E3 evict(W5.3,生產用):harness 寫此檔 → 即刻 killpg 釋放資源
        self.evict_file = evict_file
        # stall watchdog(N13):stall_seconds 內無進展 → killpg,harness resume
        self.stall_seconds = stall_seconds
        # G1 結構化契約(DESIGN §4.2):dict=傳 JSON Schema 給 CLI;None=關
        self.output_schema = output_schema
        # runner 組 envelope 用(run() 後曝出)
        self._final_session_id: str | None = None
        self._cost_usd: float | None = None
        self._error: str | None = None
        self._structured: dict | None = None    # G1 agent 自評
        # terminal 事件(claude result / codex turn.completed)。crash 在此之前
        # 殺子進程 → completed 保持 False(A 路 SIGTERM-rc=0 教訓)。
        self._got_terminal = False
        self._stalled = False
        self._evicted = False                    # E3(W5.3)
        self._last_progress = 0.0
        self._raw_count = 0
        self._event_count = 0

    # -- command (ported from arcp_poc.drivers) ---------------------------- #
    def _build_command(self, prompt: str) -> list[str]:
        sid = self.session_id or str(uuid.uuid4())
        self.session_id = sid              # remember for the envelope
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

    def run(self, prompt: str, ws: str, on_event) -> None:
        """跑一次完整 CLI 呼叫到結束。on_event(dict) 落每個細粒度事件。"""
        wd = ws or os.getcwd()
        on_event(_msg_event(prompt, "user"))    # Conversation 視圖的起手 prompt
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
        if self.evict_file:
            threading.Thread(target=self._evict_watchdog, args=(proc,),
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
            self.session_id = self._final_session_id

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

    # -- E3 evict watchdog(W5.3,鏡射 stall watchdog 模式)------------------ #
    def _evict_watchdog(self, proc) -> None:
        """EVICT 檔出現 → 即刻 killpg(釋放 CPU/memory);harness 之後憑
        session id native resume,不重工(DESIGN §6 實時 killpg)。"""
        while proc.poll() is None:
            time.sleep(1.0)
            if self.evict_file and os.path.exists(self.evict_file):
                self._evicted = True
                self._error = "evicted by harness (killpg)"
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
        on_event(_msg_event(text, "agent"))

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
