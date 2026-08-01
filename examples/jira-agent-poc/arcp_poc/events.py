"""Unified cross-CLI event schema and run state machine.

This is the ONE layer the v3 research argues nobody has built: a normalized
event vocabulary and execution state machine that is identical whether the
underlying worker is `claude -p`, `codex exec`, or an OpenHands ACP agent.

Everything above this module (supervisor, trace, control, watcher) speaks only
`AgentEvent` and `RunState`. Everything below (the drivers) is responsible for
translating a specific CLI's native JSON into these types.

Ground truth for the mapping came from real captured streams under fixtures/:
  - claude -p  --output-format stream-json  (claude_p_real.jsonl)
  - codex exec --json                       (codex_exec_real.jsonl)
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class EventType(str, Enum):
    """Normalized event vocabulary — the cross-CLI lingua franca.

    Kept deliberately small. A driver that cannot map a native event to one of
    these emits RAW (preserved for trace, ignored by the state machine).
    """

    RUN_STARTED = "run.started"          # worker process up, session id known
    THINKING = "thinking"                # model is reasoning (token deltas)
    MESSAGE = "message"                  # assistant text chunk / final answer
    TOOL_STARTED = "tool.started"        # a tool/command invocation began
    TOOL_FINISHED = "tool.finished"      # tool/command returned
    WAITING_PERMISSION = "waiting.permission"  # blocked on an approval decision
    WAITING_HUMAN = "waiting.human"      # escalated to a human (our own signal)
    API_RETRY = "api.retry"              # transient API error / rate-limit retry
    TOKEN_USAGE = "token.usage"          # incremental or final cost/token report
    RUN_COMPLETED = "run.completed"      # terminal success
    RUN_FAILED = "run.failed"            # terminal failure
    RAW = "raw"                          # unmapped native event (trace only)


class RunState(str, Enum):
    """Execution state machine. Transitions are driven only by AgentEvents.

    NEW -> STARTING -> RUNNING <-> THINKING/TOOL ... -> DONE | FAILED
    WAITING_* and STALLED are the states the supervisor acts on.
    """

    NEW = "new"
    STARTING = "starting"
    RUNNING = "running"
    THINKING = "thinking"
    RUNNING_TOOL = "running_tool"
    WAITING_PERMISSION = "waiting_permission"
    WAITING_HUMAN = "waiting_human"
    STALLED = "stalled"
    RECOVERING = "recovering"
    DONE = "done"
    FAILED = "failed"


TERMINAL_STATES = {RunState.DONE, RunState.FAILED}


@dataclass
class AgentEvent:
    """A single normalized event. `raw` always carries the untouched native dict
    so no information is lost and trace remains fully auditable."""

    run_id: str
    agent: str                       # "claude" | "codex" | "opencode" | "openhands"
    type: EventType
    ts: float = field(default_factory=time.time)
    session_id: str | None = None    # native session/thread id (for resume)
    text: str | None = None          # message/tool summary text if applicable
    tool_name: str | None = None
    cost_usd: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        return d


# --- State machine ---------------------------------------------------------

# Which RunState each event type drives the run *into*. None => no state change
# (e.g. TOKEN_USAGE, RAW, MESSAGE mid-run are observational only).
_EVENT_TO_STATE: dict[EventType, RunState | None] = {
    EventType.RUN_STARTED: RunState.RUNNING,
    EventType.THINKING: RunState.THINKING,
    EventType.TOOL_STARTED: RunState.RUNNING_TOOL,
    EventType.TOOL_FINISHED: RunState.RUNNING,
    EventType.WAITING_PERMISSION: RunState.WAITING_PERMISSION,
    EventType.WAITING_HUMAN: RunState.WAITING_HUMAN,
    EventType.API_RETRY: RunState.RECOVERING,
    EventType.RUN_COMPLETED: RunState.DONE,
    EventType.RUN_FAILED: RunState.FAILED,
    EventType.MESSAGE: None,
    EventType.TOKEN_USAGE: None,
    EventType.RAW: None,
}


def next_state(current: RunState, event: AgentEvent) -> RunState:
    """Pure transition function. Terminal states are sticky."""
    if current in TERMINAL_STATES:
        return current
    target = _EVENT_TO_STATE.get(event.type)
    return target if target is not None else current
