"""Normalized Ticket model (v5 D6b): the ONLY shape upstream code sees.

Jira (Cloud or Server) is one implementation behind it; a future ClearQuest
bridge or another tracker maps into the same model. Keep this file free of
any tracker-specific vocabulary beyond the raw passthrough field.

Primary key rule (v5 C3): `id` is the tracker's NUMERIC issue id — stable
across project moves. `key` (e.g. AGT-1) is display-only and MUST NOT be
used as a persistence key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Comment:
    id: int
    author: str            # display name
    author_id: str         # tracker account id (stable; whitelist matching)
    body: str              # plain text (ADF flattened by the source adapter)
    created: str           # ISO timestamp string


@dataclass
class Ticket:
    id: int                # numeric issue id — the primary key (v5 C3)
    key: str               # display only (AGT-1); changes on project move
    summary: str
    state: str             # status name, e.g. "To Do" / "In Progress"
    assignee: str | None   # display name, None if unassigned
    assignee_id: str | None
    labels: list[str] = field(default_factory=list)
    description: str = ""
    comments: list[Comment] = field(default_factory=list)
    updated: str = ""      # ISO timestamp of last issue update
    raw: dict[str, Any] = field(default_factory=dict)
