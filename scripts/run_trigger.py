#!/usr/bin/env python3
"""W3.4 oneshot 觸發 CLI — 立即跑一個 trigger(忽略 every/last_run)。

Usage: python3 run_trigger.py <trigger名> [runtime_dir]   (預設 runtime_live)
routes.yaml 的 outer_loop.triggers 需有該名字(every 可省略=純 oneshot)。
"""
from __future__ import annotations

import os
import sys

from arcp.paths import config_path, harness_dir
from arcp.profiles import load_profiles
from arcp.store import Store
from arcp.triggers import load_triggers, run_trigger


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    name = sys.argv[1]
    root = (sys.argv[2] if len(sys.argv) > 2
            else os.path.join(harness_dir() or ".", "runtime_live"))
    cfg = config_path()                       # W12.4:repo-root 相對,不綁 cwd
    profiles = load_profiles(cfg)
    triggers = {t.name: t for t in load_triggers(cfg, profiles)}
    if name not in triggers:
        print(f"[trigger] 不認得 '{name}';可用:{sorted(triggers) or '(無)'}")
        return 2
    store = Store(root)
    try:
        for e in run_trigger(triggers[name], profiles, store, root):
            extra = {k: v for k, v in e.items()
                     if k not in ("ts", "issue_id", "key")}
            print(f"[trigger] {extra}", flush=True)
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
