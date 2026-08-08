"""Workspace provisioning + health check (v5 §4.4).

Layout per ticket (keyed by NUMERIC issue id — v5 C3; the path never changes
once created, because native resume is cwd-bound):

    <root>/tickets/<issue_id>/
        ws/                  the agent's working directory
        ws/.claude/skills/   injected skills (from profile.skills paths)
        ws/TICKET.md         rendered ticket context (re-rendered when stale)

Health check before any resume (v5 §6-16): never assume the last run ended
cleanly.
"""

from __future__ import annotations

import os
import shutil

from .profiles import Profile
from .ticket import Ticket

_HARNESS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TICKET_TEMPLATE = """# {key}: {summary}

- issue_id: {id}
- 狀態: {state}
- assignee: {assignee}
- labels: {labels}

## 描述

{description}

## 最新留言(最多 5 則)

{comments}
"""


def render_ticket_md(t: Ticket) -> str:
    comments = "\n".join(
        f"- [{c.author}] {c.body[:300]}" for c in t.comments[-5:]) or "(無)"
    return TICKET_TEMPLATE.format(
        key=t.key, summary=t.summary, id=t.id, state=t.state,
        assignee=t.assignee or "-", labels=", ".join(t.labels) or "-",
        description=t.description or "(無)", comments=comments)


def _slug(s: str) -> str:
    """Folder-safe token: keep it readable, replace anything odd with '-'."""
    return "".join(c if (c.isalnum() or c in "-.") else "-" for c in s)


def provision(root: str, ticket: Ticket, profile: Profile) -> str:
    """Create (or refresh) the ticket workspace; returns the ws path.

    template=class → workspace=instance (DESIGN_lifecycle §1): when
    profile.workspace_template is a folder path (not "empty"), a fresh instance
    is a copytree of it; skills layer on top. Path is keyed by the never-changing
    issue_id tail so native resume (cwd-bound) survives even if summary/key are
    edited (§2). Existing ws is never re-copied (instance state preserved).
    """
    base = os.path.join(root, profile.workspace_folder.format(
        agent=_slug(profile.name), key=_slug(ticket.key), issue_id=ticket.id))
    ws = os.path.join(base, "ws")
    if not os.path.isdir(ws) and profile.workspace_template != "empty":
        # W2 atomicity: copytree to a temp sibling, then rename into place, so a
        # crash mid-copy never leaves a half-populated ws that looks healthy.
        template = os.path.join(_HARNESS_ROOT, profile.workspace_template)
        os.makedirs(base, exist_ok=True)
        tmp = ws + ".tmp"
        if os.path.isdir(tmp):
            shutil.rmtree(tmp)
        shutil.copytree(template, tmp)
        os.rename(tmp, ws)
    else:
        os.makedirs(ws, exist_ok=True)
    for skill_path in profile.skills:          # skills layer on top of template
        name = os.path.splitext(os.path.basename(skill_path))[0]
        dst = os.path.join(ws, ".claude", "skills", name)
        os.makedirs(dst, exist_ok=True)
        shutil.copy(skill_path, os.path.join(dst, "SKILL.md"))
    with open(os.path.join(ws, "TICKET.md"), "w") as f:
        f.write(render_ticket_md(ticket))
    return ws


def health_check(ws: str, ticket: Ticket) -> tuple[bool, str]:
    """(healthy, reason). Run before every resume (v5 §4.4)."""
    if not os.path.isdir(ws):
        return False, "workspace 目錄不存在"
    if not os.access(ws, os.W_OK):
        return False, "workspace 不可寫"
    ticket_md = os.path.join(ws, "TICKET.md")
    if not os.path.isfile(ticket_md):
        return False, "TICKET.md 遺失"
    if open(ticket_md).read() != render_ticket_md(ticket):
        # ticket 內容變了:重新渲染後仍算健康(資訊更新非損壞)
        with open(ticket_md, "w") as f:
            f.write(render_ticket_md(ticket))
    return True, "ok"
