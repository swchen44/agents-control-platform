"""Outer-loop routing (v5 C1/D2): YAML decides WHICH profile and WHEN —
never task steps. Guardrails enforced at load time:

  - any `steps`/`then` key anywhere in a route -> hard error (the C1 slope)
  - every regex precompiles at load; a bad config dies at startup, not runtime
  - unknown `when` fields or `on_match` values -> hard error

Matching semantics: first route wins; fields inside `when` are AND; a list
value is OR within the field. `comments` regex scans the newest
`comments_lookback` comments (default 5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

from .ticket import Ticket

FORBIDDEN_KEYS = {"steps", "then", "sequence", "pipeline"}
ON_MATCH = {"notify_only", "create_or_resume", "resume_only", "ignore"}
WHEN_FIELDS = {"summary", "description", "comments", "assignee", "state",
               "labels"}


@dataclass
class Route:
    name: str
    when: dict = field(default_factory=dict)   # compiled regexes / lists
    profile: str | None = None
    on_match: str = "notify_only"
    comments_lookback: int = 5


class ConfigError(ValueError):
    pass


def _reject_forbidden(node, path: str) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k in FORBIDDEN_KEYS:
                raise ConfigError(
                    f"{path}.{k}: 步驟序列越界(v5 C1)——outer loop 只能決定"
                    f" profile/接管/交還,步驟由 inner loop 的模型自決")
            _reject_forbidden(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _reject_forbidden(v, f"{path}[{i}]")


def load_config(path: str) -> tuple[dict, list[Route]]:
    """Load + validate. Raises ConfigError before anything runs (v5 §6-7)."""
    with open(path) as f:
        doc = yaml.safe_load(f)
    outer = (doc or {}).get("outer_loop") or {}
    _reject_forbidden(outer.get("routes", []), "routes")

    routes: list[Route] = []
    for i, r in enumerate(outer.get("routes", [])):
        name = r.get("name") or f"route[{i}]"
        on_match = r.get("on_match", "notify_only")
        if on_match not in ON_MATCH:
            raise ConfigError(f"{name}: on_match 必須是 {sorted(ON_MATCH)}")
        when_raw = r.get("when") or {}
        unknown = set(when_raw) - WHEN_FIELDS
        if unknown:
            raise ConfigError(f"{name}: 不認得的 when 欄位 {sorted(unknown)}")
        when: dict = {}
        for fld, value in when_raw.items():
            if fld in ("summary", "description", "comments"):
                try:
                    when[fld] = re.compile(value)
                except re.error as e:
                    raise ConfigError(f"{name}.when.{fld}: 壞 regex: {e}") from e
            else:  # assignee / state / labels — list membership
                when[fld] = [value] if isinstance(value, str) else list(value)
        routes.append(Route(name=name, when=when,
                            profile=r.get("profile"), on_match=on_match,
                            comments_lookback=int(r.get("comments_lookback", 5))))
    return outer.get("source") or {}, routes


def match(ticket: Ticket, routes: list[Route]) -> Route | None:
    """First route whose `when` fields ALL hold. None = no route."""
    for route in routes:
        if all(_field_holds(ticket, fld, cond, route)
               for fld, cond in route.when.items()):
            return route
    return None


def _field_holds(t: Ticket, fld: str, cond, route: Route) -> bool:
    if fld == "summary":
        return bool(cond.search(t.summary))
    if fld == "description":
        return bool(cond.search(t.description))
    if fld == "comments":
        recent = t.comments[-route.comments_lookback:]
        return any(cond.search(c.body) for c in recent)
    if fld == "assignee":
        return (t.assignee in cond) or (t.assignee_id in cond)
    if fld == "state":
        return t.state in cond
    if fld == "labels":
        return any(label in t.labels for label in cond)
    return False
