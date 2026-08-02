"""Degraded resume: render the journal as a bootstrap transcript (report §6.4).

Rung two of the recovery ladder. When native resume is unavailable — the CLI's
own session store is gone (machine change, worktree moved, store wiped) — we
still hold the supervisor's journal (events.jsonl). Render it into an opening
message for a FRESH session so the agent can continue instead of starting cold.

    native resume (--resume / exec resume)     rung 1, measured in §9.3-1
    bootstrap transcript into a new session    rung 2, THIS MODULE
    plain rerun from scratch                   rung 3, last resort

The rendering policy is borrowed from OpenHands' resume_transcript.py (their
renderer exists but is not yet wired up in the open-source snapshot; the design
is worth stealing regardless):

  - a fixed marker heads the transcript so producers/consumers can detect an
    already-resumed message and avoid double-wrapping;
  - a total budget truncates OLDEST-first (the freshest events matter most to
    an agent picking up where it left off);
  - each message keeps its HEAD (the question/command/path is at the front).

Pair the resumed run with a grader (grader.py): a fresh session has no memory
of what "done" meant, so evidence — not its self-report — must decide DONE.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable

from .drivers import Task

RESUME_CONTEXT_MARKER = "<<RESUMED CONVERSATION>>"

_HEADER = (
    "下面是前一個 session 的事件摘要;它的即時 context 已遺失(無法原生 resume)。"
    "請把它當背景資訊,從先前中斷的地方繼續,不要重做已完成的步驟。"
)
_FOOTER = "--- 前一個 session 摘要結束 ---"

DEFAULT_MAX_CHARS = 60_000
DEFAULT_MAX_MESSAGE_CHARS = 8_000

# Journal event types worth replaying to a fresh session. THINKING/RAW/token
# noise would burn budget without adding recoverable state.
_RENDERED_TYPES = {"message", "tool.started", "run.completed", "run.failed"}


def load_journal_events(path: str) -> list[dict]:
    """Read a journal (events.jsonl of normalized AgentEvents) into dicts."""
    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _render_event(ev: dict, max_message_chars: int) -> str | None:
    etype = ev.get("type")
    if etype not in _RENDERED_TYPES:
        return None
    if etype == "message":
        text = (ev.get("text") or "").strip()
        if not text:
            return None
        if len(text) > max_message_chars:
            text = text[: max_message_chars - 3] + "..."
        return f"[assistant] {text}"
    if etype == "tool.started":
        return f"[tool] {ev.get('tool_name') or '(unknown tool)'}"
    # terminal markers carry crash forensics ("worker exited rc=…")
    return f"[{etype}] {(ev.get('text') or '').strip()}"


def render_resume_transcript(
    events: Iterable[dict],
    original_prompt: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_message_chars: int = DEFAULT_MAX_MESSAGE_CHARS,
) -> str:
    """Render journal events + the original task into a bootstrap prompt.

    The marker/header/task always survive; only the event body is truncated,
    oldest-first, when the budget is exceeded.
    """
    lines = [r for ev in events if (r := _render_event(ev, max_message_chars))]
    body = "\n".join(lines)
    prefix = (
        f"{RESUME_CONTEXT_MARKER}\n{_HEADER}\n\n"
        f"## 原始任務\n{original_prompt}\n\n## 前一個 session 的事件\n"
    )
    suffix = f"\n{_FOOTER}\n\n請檢查目前工作區的實際狀態,接續完成原始任務。"
    budget = max_chars - len(prefix) - len(suffix)
    if budget > 0 and len(body) > budget:
        body = "...\n" + body[-(budget - 4):]
    return prefix + body + suffix


def build_transcript_resume_task(
    original: Task,
    journal_events: Iterable[dict],
    run_id: str,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> Task:
    """A FRESH-session Task whose prompt bootstraps from the old journal.

    claude gets a newly minted --session-id (the old one is unusable by
    definition of this rung); codex gets none — its thread id arrives in
    thread.started as usual. Run it with resume=False: this is a new session.
    """
    prompt = render_resume_transcript(
        journal_events, original.prompt, max_chars=max_chars)
    return Task(
        run_id=run_id,
        prompt=prompt,
        cwd=original.cwd,
        model=original.model,
        session_id=str(uuid.uuid4()) if original.session_id else None,
        allowed_tools=original.allowed_tools,
        permission_mode=original.permission_mode,
        sandbox=original.sandbox,
    )
