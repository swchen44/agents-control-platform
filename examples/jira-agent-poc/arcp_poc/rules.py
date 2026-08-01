"""Rule engine: match a Jira issue against JSON rules to decide
(agent, skills, repo/workspace). This is FR-014/FR-016 territory — the piece
no existing OSS product provides (report §3, §5).

A rule matches on assignee and/or keyword (in summary/title). First match wins;
rules are evaluated top-to-bottom, so put specific rules before catch-alls.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class Issue:
    key: str                       # e.g. "OPS-1234"
    summary: str                   # title
    assignee: str | None
    description: str = ""
    status: str = ""

    @classmethod
    def from_jira(cls, raw: dict[str, Any]) -> "Issue":
        f = raw.get("fields", {}) or {}
        assignee = (f.get("assignee") or {}).get("name") or \
                   (f.get("assignee") or {}).get("emailAddress")
        return cls(key=raw.get("key", "?"), summary=f.get("summary", ""),
                   assignee=assignee, description=f.get("description", "") or "",
                   status=(f.get("status") or {}).get("name", ""))


@dataclass
class Decision:
    matched: bool
    rule_name: str | None = None
    agent: str = "claude"
    skills: list[str] | None = None
    repo: str | None = None
    model: str | None = None
    prompt_template: str = "Work on Jira issue {key}: {summary}\n\n{description}"

    def render_prompt(self, issue: Issue) -> str:
        return self.prompt_template.format(
            key=issue.key, summary=issue.summary, description=issue.description)


class RuleEngine:
    def __init__(self, rules: list[dict[str, Any]]):
        self.rules = rules

    @classmethod
    def from_file(cls, path: str) -> "RuleEngine":
        with open(path) as f:
            data = json.load(f)
        return cls(data.get("rules", []))

    def evaluate(self, issue: Issue) -> Decision:
        hay = f"{issue.summary}\n{issue.description}".lower()
        for r in self.rules:
            m = r.get("match", {})
            ok = True
            if "assignee" in m:
                want = m["assignee"]
                want = want if isinstance(want, list) else [want]
                ok = ok and (issue.assignee in want)
            if "keywords_any" in m:
                ok = ok and any(k.lower() in hay for k in m["keywords_any"])
            if "keywords_all" in m:
                ok = ok and all(k.lower() in hay for k in m["keywords_all"])
            if "status" in m:
                ok = ok and (issue.status == m["status"])
            if ok:
                a = r.get("action", {})
                return Decision(
                    matched=True, rule_name=r.get("name"),
                    agent=a.get("agent", "claude"),
                    skills=a.get("skills", []),
                    repo=a.get("repo"),
                    model=a.get("model"),
                    prompt_template=a.get("prompt_template",
                                          Decision.prompt_template))
        return Decision(matched=False)
