#!/usr/bin/env python3
"""oneshot 觸發 CLI — 立即跑一個 job(忽略 every/last_run)。

Usage: python3 run_trigger.py <trigger名> [runtime_dir]   (預設 runtime)
config.yaml 的 outer_loop.triggers 需有該名字。script-job 直接跑;agent-job 需 ~/.env
的 Jira 憑證(它會像人一樣建票 → 之後由 poller route/triage)。
"""
from __future__ import annotations

import sys

from arcp.paths import config_path, runtime_dir
from arcp.routing import load_config
from arcp.store import Store
from arcp.triggers import fire_agent_job, load_triggers, run_script_trigger


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    name = sys.argv[1]
    root = (sys.argv[2] if len(sys.argv) > 2
            else (runtime_dir() or "./runtime"))
    cfg = config_path()                       # repo-root 相對,不綁 cwd
    source_cfg, _routes = load_config(cfg)
    triggers = {t.name: t for t in load_triggers(cfg)}
    if name not in triggers:
        print(f"[trigger] 不認得 '{name}';可用:{sorted(triggers) or '(無)'}")
        return 2
    tr = triggers[name]
    store = Store(root)
    try:
        if tr.trigger_type == "script-job":
            evs = run_script_trigger(tr, store, root)
        else:                                 # agent-job:需 Jira source 建票
            from arcp.config import jira_credentials
            from arcp.jira_source import JiraCloudSource
            _flavor = source_cfg.get("jira_flavor", "cloud")   # 主題 L
            source = JiraCloudSource(*jira_credentials(
                base_url_override=source_cfg.get("jira_base_url"),
                flavor=_flavor), flavor=_flavor)
            source.issue_type_id = (source_cfg.get("issue_type_id")
                                    or "10003")
            project = source_cfg.get("project") or "SCRUM"
            evs = fire_agent_job(tr, source, store, root, project)
        for e in evs:
            extra = {k: v for k, v in e.items()
                     if k not in ("ts", "issue_id", "key")}
            print(f"[trigger] {extra}", flush=True)
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
