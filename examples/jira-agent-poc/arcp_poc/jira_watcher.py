"""Jira watcher: poll a Jira Server/Data Center for issues matching a JQL, apply
the rule engine, provision a workspace + skills, and dispatch a supervised run.

Uses polling (JQL `updated >= -Nm`) rather than webhooks: it needs no Jira admin
rights and no inbound endpoint, and reconciles missed events naturally — the
report §3 argues this is the pragmatic default for Jira Server.

Auth: Jira Server PAT via `Authorization: Bearer <token>` (JIRA_TOKEN env) or
basic auth. Network calls use only the stdlib so the PoC has zero dependencies.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from .rules import Issue, RuleEngine, Decision


@dataclass
class JiraConfig:
    base_url: str                  # https://jira.example.com
    jql: str                       # e.g. 'assignee = currentUser() AND updated >= -5m'
    token: str | None = None       # PAT (Bearer)
    user: str | None = None        # for basic auth
    password: str | None = None
    poll_seconds: int = 60


class JiraClient:
    def __init__(self, cfg: JiraConfig):
        self.cfg = cfg

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.cfg.token:
            h["Authorization"] = f"Bearer {self.cfg.token}"
        elif self.cfg.user:
            raw = f"{self.cfg.user}:{self.cfg.password}".encode()
            h["Authorization"] = "Basic " + base64.b64encode(raw).decode()
        return h

    def search(self, jql: str, fields: str = "summary,assignee,status,description",
               max_results: int = 50) -> list[dict[str, Any]]:
        url = f"{self.cfg.base_url}/rest/api/2/search"
        body = json.dumps({"jql": jql, "fields": fields.split(","),
                           "maxResults": max_results}).encode()
        req = urllib.request.Request(url, data=body, headers=self._headers(),
                                     method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read()).get("issues", [])

    def comment(self, issue_key: str, text: str) -> None:
        url = f"{self.cfg.base_url}/rest/api/2/issue/{issue_key}/comment"
        body = json.dumps({"body": text}).encode()
        req = urllib.request.Request(url, data=body, headers=self._headers(),
                                     method="POST")
        urllib.request.urlopen(req, timeout=30).read()


# A dispatch callback receives (Issue, Decision) and does the real work
# (provision + supervise). Kept as a callback so the watcher stays testable.
Dispatcher = Callable[[Issue, Decision], None]


class JiraWatcher:
    def __init__(self, client: JiraClient, engine: RuleEngine,
                 dispatcher: Dispatcher, state_path: str = "./runtime/seen.json"):
        self.client = client
        self.engine = engine
        self.dispatcher = dispatcher
        self.state_path = state_path
        self._seen: set[str] = self._load_seen()

    def _load_seen(self) -> set[str]:
        if os.path.exists(self.state_path):
            return set(json.load(open(self.state_path)))
        return set()

    def _save_seen(self) -> None:
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        json.dump(sorted(self._seen), open(self.state_path, "w"))

    def poll_once(self) -> list[str]:
        """One polling pass. Returns keys of issues dispatched this pass."""
        dispatched: list[str] = []
        for raw in self.client.search(self.client.cfg.jql):
            issue = Issue.from_jira(raw)
            # De-dup on (key, status) so a status change re-triggers but a plain
            # re-poll of the same state does not.
            fp = f"{issue.key}:{issue.status}"
            if fp in self._seen:
                continue
            decision = self.engine.evaluate(issue)
            if decision.matched:
                self.dispatcher(issue, decision)
                dispatched.append(issue.key)
            self._seen.add(fp)
        self._save_seen()
        return dispatched

    def run_forever(self) -> None:
        while True:
            try:
                self.poll_once()
            except Exception as e:  # never let one bad poll kill the watcher
                print(f"[watcher] poll error: {e}")
            time.sleep(self.client.cfg.poll_seconds)
