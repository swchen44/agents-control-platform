"""Outer-loop poller: poll → diff → events → routing → journal.

Grey-rollout stance (v5 P1): every route today is expected to be
`notify_only`/`ignore` — the poller RECORDS what it would do and never acts.
Dispatch (create_or_resume) arrives in Phase 2 behind the same event stream.

Idempotency: change detection diffs against the SQLite watch state, keyed by
numeric issue_id; comments advance a per-ticket watermark (max comment id),
so a re-poll — or a crash-restart mid-poll — never replays old events.
"""

from __future__ import annotations

from .jira_source import JiraCloudSource
from .routing import Route, match
from .store import Store, TicketWatch


class OuterLoop:
    def __init__(self, source: JiraCloudSource, store: Store,
                 routes: list[Route], jql: str, dispatcher=None,
                 commands=None, external=None):
        self.source = source
        self.store = store
        self.routes = routes
        self.jql = jql
        self.dispatcher = dispatcher   # None = pure grey mode (Phase 1)
        self.commands = commands       # CommandHandler (Phase 3)
        self.external = external       # ExternalChangePolicy (Phase 3)

    def poll_once(self) -> list[dict]:
        """One reconciliation pass. Returns the events it journaled."""
        events: list[dict] = []
        for t in self.source.search(self.jql):
            prev = self.store.get(t.id)
            t.comments = self.source.get_comments(t.id)
            route = match(t, self.routes)

            if prev is None:
                events.append(self.store.journal(
                    "new_issue", t.id, t.key, state=t.state,
                    summary=t.summary[:120]))
                if route is not None:
                    events.append(self.store.journal(
                        "route_matched", t.id, t.key, route=route.name,
                        profile=route.profile, on_match=route.on_match))
            else:
                if t.state != prev.last_state:
                    events.append(self.store.journal(
                        "status_changed", t.id, t.key,
                        old=prev.last_state, new=t.state))
                    if self.external is not None:
                        events.extend(
                            self.external.on_status_changed(t, t.state))
                if (t.assignee_id or "") != prev.last_assignee_id:
                    events.append(self.store.journal(
                        "assignee_changed", t.id, t.key,
                        old=prev.last_assignee_id, new=t.assignee_id or ""))
                    if self.external is not None:
                        events.extend(self.external.on_assignee_changed(t))

            watermark = prev.last_comment_id if prev else 0
            for c in t.comments:
                if c.id > watermark:
                    events.append(self.store.journal(
                        "comment_added", t.id, t.key, comment_id=c.id,
                        author=c.author, body=c.body[:200]))
                    # 指令在 dispatch 之前處理:retry/run 解除 pending 後,
                    # 同一輪 poll 就會重新派工
                    if self.commands is not None:
                        events.extend(self.commands.handle(t, c))
            new_watermark = max([watermark] + [c.id for c in t.comments])

            self.store.upsert(TicketWatch(
                issue_id=t.id, key=t.key,
                last_comment_id=new_watermark,
                last_state=t.state,
                last_assignee_id=t.assignee_id or "",
                route_name=route.name if route else None))

            # dispatch AFTER the watch state is persisted: a crash mid-dispatch
            # must not replay watch events on restart (idempotency first)
            if (route is not None and route.on_match == "create_or_resume"
                    and route.profile and self.dispatcher is not None):
                events.extend(self.dispatcher.handle(t, route.profile))
        return events
