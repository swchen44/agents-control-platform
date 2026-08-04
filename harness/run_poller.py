#!/usr/bin/env python3
"""時間盒常駐 poller — live demo / 準營運模式。

啟動時先做「認養 pass」(lesson #9 的制度化解法):當下已存在的票全部
標記 watch 水位 + 無 session 的 create_or_resume 票補一筆 ABORTED(adopted)
session——**只對啟動之後的新票與新留言反應**,不重跑歷史。

store 用 runtime_live/(持久,重啟不清——冪等靠它)。

Usage: python3 run_poller.py [minutes] [interval_sec]   (預設 30 分鐘、15 秒)
"""

from __future__ import annotations

import sys
import time

from arcp_harness.commands import CommandHandler, ExternalChangePolicy
from arcp_harness.config import jira_credentials
from arcp_harness.dispatcher import Dispatcher
from arcp_harness.jira_source import JiraCloudSource
from arcp_harness.poller import OuterLoop
from arcp_harness.profiles import load_profiles
from arcp_harness.routing import load_config, match
from arcp_harness.store import Store, TicketSession, TicketWatch


def adopt_existing(source, store, routes, jql) -> int:
    """Baseline adoption: existing tickets become invisible to the live loop."""
    n = 0
    for t in source.search(jql):
        if store.get(t.id) is not None:
            continue
        comments = source.get_comments(t.id)
        route = match(t, routes)
        store.upsert(TicketWatch(
            issue_id=t.id, key=t.key,
            last_comment_id=max([0] + [c.id for c in comments]),
            last_state=t.state, last_assignee_id=t.assignee_id or "",
            route_name=route.name if route else None))
        if (route and route.on_match == "create_or_resume"
                and store.get_session(t.id) is None):
            store.upsert_session(TicketSession(
                issue_id=t.id, key=t.key, profile=route.profile or "-",
                workspace="(adopted)", session_id=None, attempts=0,
                outcome="ABORTED", pending_reason=None, cost_usd=0))
        store.journal("adopted", t.id, t.key)
        n += 1
    return n


def main() -> int:
    minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
    interval = float(sys.argv[2]) if len(sys.argv) > 2 else 15.0

    source_cfg, routes = load_config("routes.yaml")
    _wr = source_cfg.get("write_retry") or {}            # A3(N8)
    src = JiraCloudSource(*jira_credentials(),
                          write_retry_max=int(_wr.get("max", 5)),
                          write_retry_base=float(_wr.get("base_sec", 1.0)))
    profiles = load_profiles("routes.yaml")
    store = Store("./runtime_live")          # 持久,絕不 wipe(lesson #9)
    jql = source_cfg["jql"]
    loop = OuterLoop(
        src, store, routes, jql,
        dispatcher=Dispatcher(src, store, profiles, root="./runtime_live"),
        commands=CommandHandler(src, store, ["Shao-wei Chen"]),
        external=ExternalChangePolicy(src, store, ["完成", "Done", "Concluído"]))

    adopted = adopt_existing(src, store, routes, jql)
    print(f"[poller] adopted {adopted} pre-existing ticket(s); "
          f"live for {minutes:.0f}m, interval {interval:.0f}s", flush=True)

    # 迭代計數時間盒:機器睡眠造成的 wall-clock 跳躍不會吃掉時間盒
    # (lesson:睡眠凍結行程但時鐘照走)
    cycles = max(1, int(minutes * 60 / interval))
    for i in range(cycles):
        try:
            for e in loop.poll_once():
                stamp = time.strftime("%H:%M:%S")
                extra = {k: v for k, v in e.items()
                         if k not in ("ts", "type", "issue_id", "key")}
                print(f"[{stamp}] {e['key']} {e['type']} {extra}", flush=True)
        except KeyboardInterrupt:
            break
        except Exception as exc:  # 斷網等瞬態錯誤:記錄、下一輪再試
            print(f"[poller] poll error({type(exc).__name__}): {exc} — "
                  f"retry next cycle", flush=True)
        time.sleep(interval)
    print("[poller] timebox ended", flush=True)
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
