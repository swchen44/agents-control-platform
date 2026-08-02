"""Automatic recovery loop: run → grade → resume, until EVIDENCE passes.

This is the Loop layer of the three-layer map ("do not loop on confidence,
loop on evidence") wired from parts that are each individually measured:

  - the Supervisor runs one attempt and (with a grader) refuses to call a run
    DONE without evidence — catching both real crashes and the measured
    codex SIGTERM-rc=0 fake-DONE;
  - failed attempts escalate down the recovery ladder, one rung per failure:

        native resume        rung 1  (claude --resume / codex exec resume)
        transcript bootstrap rung 2  (fresh session fed from the journal)
        fresh rerun          rung 3  (last resort, brand-new task)

  - the ladder never retries the same rung twice: a rung that failed once is
    evidence it doesn't work here, so escalate instead of spinning.

A grader is REQUIRED: without evidence there is nothing trustworthy to loop
on (exit codes and terminal events both measured insufficient, report §9.3).

Testability: `runner` is injectable — selftest drives the policy with a
scripted fake; live callers use the default Supervisor-backed runner.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field, replace
from typing import Callable

from .drivers import Driver, Task
from .events import RunState
from .grader import Grader
from .resume_transcript import build_transcript_resume_task, load_journal_events
from .supervisor import Observer, RunHandle, Supervisor

DEFAULT_RESUME_PROMPT = "繼續完成先前的任務。"

# runner(task, resume, observers) -> RunHandle
Runner = Callable[[Task, bool, list[Observer] | None], RunHandle]


@dataclass
class Attempt:
    mode: str                 # initial | native | transcript | rerun
    run_id: str
    state: str
    session_id: str | None
    detail: str | None        # result text / evidence summary


@dataclass
class RecoveryResult:
    succeeded: bool
    attempts: list[Attempt] = field(default_factory=list)

    def summary(self) -> str:
        chain = " → ".join(f"{a.mode}:{a.state}" for a in self.attempts)
        return f"{'DONE' if self.succeeded else 'GAVE UP'} after {chain}"


def _default_runner(driver: Driver, journal_root: str,
                    grader: Grader) -> Runner:
    def run(task: Task, resume: bool,
            observers: list[Observer] | None) -> RunHandle:
        sup = Supervisor(driver, journal_root=journal_root,
                         observers=observers or [], grader=grader)
        return sup.run(task, resume=resume)
    return run


def _collect_journal_events(journal_root: str, run_ids: list[str]) -> list[dict]:
    events: list[dict] = []
    for rid in run_ids:
        path = os.path.join(journal_root, rid, "events.jsonl")
        if os.path.exists(path):
            events.extend(load_journal_events(path))
    return events


def run_with_recovery(
    driver: Driver,
    task: Task,
    grader: Grader,
    journal_root: str = "./runtime",
    max_attempts: int = 4,
    resume_prompt: str = DEFAULT_RESUME_PROMPT,
    first_attempt_observers: list[Observer] | None = None,
    runner: Runner | None = None,
) -> RecoveryResult:
    """Drive the task to evidence-verified DONE or exhaust the ladder.

    `first_attempt_observers` exists for experiments (attach a KillTrigger to
    the initial run only); recovery attempts run unobserved.
    """
    runner = runner or _default_runner(driver, journal_root, grader)
    result = RecoveryResult(succeeded=False)
    session_id = task.session_id
    current, resume, mode = task, False, "initial"
    observers = first_attempt_observers
    run_ids: list[str] = []

    for n in range(1, max_attempts + 1):
        h = runner(current, resume, observers)
        observers = None
        session_id = h.session_id or session_id
        run_ids.append(current.run_id)
        result.attempts.append(Attempt(
            mode=mode, run_id=current.run_id, state=h.state.value,
            session_id=h.session_id, detail=h.result_text))
        if h.state == RunState.DONE:      # grader-verified by the Supervisor
            result.succeeded = True
            return result

        # escalate one rung; never retry a rung that already failed
        if mode == "initial" and session_id:
            mode = "native"
            current = replace(task, run_id=f"{task.run_id}-native{n}",
                              prompt=resume_prompt, session_id=session_id)
            resume = True
        elif mode in ("initial", "native"):
            mode = "transcript"
            events = _collect_journal_events(journal_root, run_ids)
            current = build_transcript_resume_task(
                task, events, run_id=f"{task.run_id}-transcript{n}")
            resume = False
        elif mode == "transcript":
            mode = "rerun"
            current = replace(
                task, run_id=f"{task.run_id}-rerun{n}",
                session_id=str(uuid.uuid4()) if task.session_id else None)
            resume = False
        else:                              # rerun already failed — give up
            return result
    return result
