"""Credential loading. stdlib only.

Reads KEY=VALUE lines from ~/.env (export prefix and quotes tolerated).
Values are returned to the caller and NEVER printed, logged, or written
anywhere by this module; they also do not touch os.environ, so child
processes don't inherit the token unless a caller passes it explicitly.
"""

from __future__ import annotations

import os

ENV_PATH = os.path.expanduser("~/.env")
REQUIRED = ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")


def load_env(path: str = ENV_PATH) -> dict[str, str]:
    values: dict[str, str] = {}
    if not os.path.exists(path):
        return values
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip("'\"")
    return values


def jira_credentials(path: str = ENV_PATH) -> tuple[str, str, str]:
    """Return (base_url, email, api_token) or raise with a fix hint."""
    env = load_env(path)
    missing = [k for k in REQUIRED if not env.get(k)]
    if missing:
        raise RuntimeError(
            f"missing {', '.join(missing)} in {path} "
            f"(expected KEY=VALUE lines; values are never logged)")
    return env["JIRA_BASE_URL"].rstrip("/"), env["JIRA_EMAIL"], env["JIRA_API_TOKEN"]
