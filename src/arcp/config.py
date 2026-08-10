"""Credential loading. stdlib only.

Reads KEY=VALUE lines from ~/.env (export prefix and quotes tolerated).
Values are returned to the caller and NEVER printed, logged, or written
anywhere by this module; they also do not touch os.environ, so child
processes don't inherit the token unless a caller passes it explicitly.
"""

from __future__ import annotations

import os

ENV_PATH = os.path.expanduser("~/.env")
# 憑證(email/token)只在 ~/.env;base_url 非機密,可改放 config.yaml
# (source.jira_base_url),~/.env 的 JIRA_BASE_URL 為後備。
REQUIRED = ("JIRA_EMAIL", "JIRA_API_TOKEN")


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


def jira_credentials(path: str = ENV_PATH,
                     base_url_override: str | None = None
                     ) -> tuple[str, str, str]:
    """Return (base_url, email, api_token) or raise with a fix hint.

    base_url:config 的 source.jira_base_url(base_url_override)優先,否則 ~/.env
    的 JIRA_BASE_URL;兩者皆無 → 報錯。email/token 一律只從 ~/.env。"""
    env = load_env(path)
    missing = [k for k in REQUIRED if not env.get(k)]
    if missing:
        raise RuntimeError(
            f"missing {', '.join(missing)} in {path} "
            f"(expected KEY=VALUE lines; values are never logged)")
    base_url = (base_url_override or env.get("JIRA_BASE_URL") or "").rstrip("/")
    if not base_url:
        raise RuntimeError(
            "missing Jira base_url: set source.jira_base_url in config.yaml "
            f"or JIRA_BASE_URL in {path}")
    return base_url, env["JIRA_EMAIL"], env["JIRA_API_TOKEN"]
