"""Supervisor: run a worker, trace every event, drive the state machine,
watch for stalls, and expose control (pause/resume/kill/escalate).

This is the "從零寫" seed the report argues for: ~200 lines gives you the
cross-CLI trace + control layer that neither raw CLIs nor OpenHands-for-ACP
provide out of the box.

Two modes:
  - live:   spawn the real CLI subprocess (costs tokens)
  - replay: feed a captured fixtures/*.jsonl through the SAME pipeline (free),
            proving the parser + state machine + watchdog work offline.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Callable

from .drivers import Driver, Task
from .events import AgentEvent, EventType, RunState, TERMINAL_STATES, next_state
from .grader import Grader


@dataclass
class RunHandle:
    """Live, externalized state for one run. Everything here is written to the
    journal so a fresh supervisor can reconcile after its own crash."""

    run_id: str
    agent: str
    cwd: str
    session_id: str | None = None
    state: RunState = RunState.NEW
    pid: int | None = None
    last_event_ts: float = field(default_factory=time.time)
    last_event_type: str | None = None
    cost_usd: float = 0.0
    tokens_out: int = 0
    result_text: str | None = None
    stalled_flag: bool = False


class Journal:
    """Append-only JSONL trace + a compact per-run snapshot. The append-only log
    is the audit trail; the snapshot is what reconciliation reads on restart."""

    def __init__(self, root: str, run_id: str):
        self.dir = os.path.join(root, run_id)
        os.makedirs(self.dir, exist_ok=True)
        self.events_path = os.path.join(self.dir, "events.jsonl")
        self.snapshot_path = os.path.join(self.dir, "snapshot.json")

    def append(self, event: AgentEvent) -> None:
        with open(self.events_path, "a") as f:
            f.write(json.dumps(event.to_json(), ensure_ascii=False) + "\n")

    def snapshot(self, h: RunHandle) -> None:
        with open(self.snapshot_path, "w") as f:
            json.dump({
                "run_id": h.run_id, "agent": h.agent, "cwd": h.cwd,
                "session_id": h.session_id, "state": h.state.value,
                "pid": h.pid, "last_event_ts": h.last_event_ts,
                "last_event_type": h.last_event_type, "cost_usd": h.cost_usd,
                "tokens_out": h.tokens_out, "result_text": h.result_text,
            }, f, ensure_ascii=False, indent=2)


# Callback signature for observers (dashboards, Jira escalation, alerts).
Observer = Callable[[AgentEvent, RunHandle], None]


class Supervisor:
    def __init__(self, driver: Driver, journal_root: str = "./runtime",
                 stall_seconds: float = 30.0,
                 observers: list[Observer] | None = None,
                 grader: Grader | None = None):
        self.driver = driver
        self.journal_root = journal_root
        self.stall_seconds = stall_seconds
        self.observers = observers or []
        self.grader = grader
        self._proc: subprocess.Popen | None = None
        self._stop = threading.Event()

    # -- control surface ---------------------------------------------------- #
    def pause(self) -> None:
        """Best-effort pause: SIGSTOP the process group. (Raw CLIs have no
        cooperative pause; this is the honest floor. OpenHands offers a
        cooperative POST /pause — see report §7.)"""
        if self._proc and self._proc.poll() is None:
            os.killpg(os.getpgid(self._proc.pid), signal.SIGSTOP)

    def resume_process(self) -> None:
        if self._proc and self._proc.poll() is None:
            os.killpg(os.getpgid(self._proc.pid), signal.SIGCONT)

    def kill(self) -> None:
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)

    # -- the run loop ------------------------------------------------------- #
    def run(self, task: Task, resume: bool = False) -> RunHandle:
        """Live run: spawn CLI, stream stdout, normalize, drive state machine."""
        cmd = self.driver.build_command(task, resume=resume)
        h = RunHandle(run_id=task.run_id, agent=self.driver.name, cwd=task.cwd,
                      session_id=task.session_id, state=RunState.STARTING)
        journal = Journal(self.journal_root, task.run_id)
        journal.snapshot(h)

        # stderr goes to a journal file, not a pipe: nobody drains a pipe (risking
        # a blocked worker on a chatty CLI) and it preserves the crash forensics
        # that a PIPE would silently discard.
        stderr_f = open(os.path.join(journal.dir, "stderr.log"), "w")
        self._proc = subprocess.Popen(
            cmd, cwd=task.cwd, stdout=subprocess.PIPE, stderr=stderr_f,
            stdin=subprocess.DEVNULL,   # codex exec reads stdin if it's a pipe
            text=True, bufsize=1, start_new_session=True,
        )
        stderr_f.close()  # the child holds its own fd now
        h.pid = self._proc.pid
        watchdog = threading.Thread(target=self._watchdog, args=(h, journal),
                                    daemon=True)
        watchdog.start()

        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            if self._stop.is_set():
                break
            line = line.strip()
            if not line:
                continue
            try:
                native = json.loads(line)
            except json.JSONDecodeError:
                continue  # non-JSON log line (e.g. codex stderr banner) -> ignore
            self._ingest(native, h, journal)
            if h.state in TERMINAL_STATES:
                break

        rc = self._proc.wait()
        # If the worker exited without emitting a terminal event, decide by exit
        # code: rc==0 is a clean finish (some CLIs signal "done" only by exiting),
        # rc!=0 is a crash — where a real system would attempt `--resume`.
        # ⚠️ Measured limit of that heuristic: codex exits rc=0 on SIGTERM, so a
        # half-finished run can land here as DONE — which is exactly why the
        # evidence check below exists.
        if h.state not in TERMINAL_STATES:
            self._finalize_on_exit(h, journal, rc)
        if self.grader and h.state == RunState.DONE:
            self._enforce_evidence(h, journal, task.cwd)
        return h

    def replay(self, native_events: Iterator[dict[str, Any]],
               run_id: str, cwd: str = ".", delay: float = 0.0) -> RunHandle:
        """Offline run: feed captured native events through the identical
        normalize -> state-machine -> journal -> observer pipeline. No tokens."""
        h = RunHandle(run_id=run_id, agent=self.driver.name, cwd=cwd,
                      state=RunState.STARTING)
        journal = Journal(self.journal_root, run_id)
        for native in native_events:
            self._ingest(native, h, journal)
            if delay:
                time.sleep(delay)
            if h.state in TERMINAL_STATES:
                break
        if self.grader and h.state == RunState.DONE:
            self._enforce_evidence(h, journal, cwd)
        return h

    # -- internals ---------------------------------------------------------- #
    def _ingest(self, native: dict[str, Any], h: RunHandle, journal: Journal) -> None:
        event = self.driver.normalize(native, h.run_id)
        if event is None:
            return
        journal.append(event)
        h.last_event_ts = event.ts
        h.last_event_type = event.type.value
        if event.session_id and not h.session_id:
            h.session_id = event.session_id
        if event.cost_usd:
            h.cost_usd = event.cost_usd
        if event.tokens_out:
            h.tokens_out = max(h.tokens_out, event.tokens_out)
        if event.type == EventType.RUN_COMPLETED:
            h.result_text = event.text
        h.state = next_state(h.state, event)
        journal.snapshot(h)
        for obs in self.observers:
            obs(event, h)

    def _watchdog(self, h: RunHandle, journal: Journal) -> None:
        """Reset-on-progress stall detector (Agent AFK principle: 'slow is legal;
        stalled is not'). If no event advances the run for `stall_seconds` while
        it's not in a legit waiting state, mark STALLED and emit an event."""
        while not self._stop.is_set() and h.state not in TERMINAL_STATES:
            time.sleep(1.0)
            idle = time.time() - h.last_event_ts
            legit_wait = h.state in (RunState.WAITING_PERMISSION,
                                     RunState.WAITING_HUMAN)
            if idle > self.stall_seconds and not legit_wait and not h.stalled_flag:
                h.stalled_flag = True
                h.state = RunState.STALLED
                ev = AgentEvent(run_id=h.run_id, agent=h.agent,
                                type=EventType.WAITING_HUMAN,
                                session_id=h.session_id,
                                text=f"stalled: no progress for {idle:.0f}s")
                journal.append(ev)
                journal.snapshot(h)
                for obs in self.observers:
                    obs(ev, h)

    def _enforce_evidence(self, h: RunHandle, journal: Journal, workdir: str) -> None:
        """Evidence outranks self-report. A run that reached DONE (terminal
        event or clean exit) but fails its grader is overridden to FAILED —
        the single sanctioned override of a sticky terminal state. The verdict
        reasons go into the journal either way, so completion is auditable."""
        verdict = self.grader.grade(workdir)
        if verdict.passed:
            ev = AgentEvent(run_id=h.run_id, agent=h.agent, type=EventType.RAW,
                            session_id=h.session_id,
                            text=f"evidence PASS: {verdict.summary()}")
            journal.append(ev)
        else:
            ev = AgentEvent(run_id=h.run_id, agent=h.agent,
                            type=EventType.RUN_FAILED, session_id=h.session_id,
                            text=f"evidence FAIL: {verdict.summary()}")
            journal.append(ev)
            h.state = RunState.FAILED
            h.result_text = ev.text
        journal.snapshot(h)
        for obs in self.observers:
            obs(ev, h)

    def _finalize_on_exit(self, h: RunHandle, journal: Journal, rc: int) -> None:
        if rc == 0:
            etype, text = EventType.RUN_COMPLETED, "clean exit (rc=0)"
        else:
            etype, text = EventType.RUN_FAILED, f"worker exited rc={rc} without terminal event (crash?)"
        ev = AgentEvent(run_id=h.run_id, agent=h.agent, type=etype,
                        session_id=h.session_id, text=text)
        journal.append(ev)
        h.state = next_state(h.state, ev)
        journal.snapshot(h)
        for obs in self.observers:
            obs(ev, h)
