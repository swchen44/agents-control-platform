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

from arcp_harness.approval import ApprovalGate
from arcp_harness.commands import CommandHandler, ExternalChangePolicy
from arcp_harness.config import jira_credentials
from arcp_harness.control_api import ControlAPI
from arcp_harness.dispatcher import Dispatcher
from arcp_harness.jira_source import JiraCloudSource
from arcp_harness.poller import OuterLoop
from arcp_harness.profiles import load_profiles
from arcp_harness.routing import load_config, match
from arcp_harness.snapshotter import Snapshotter
from arcp_harness.store import Store, TicketSession, TicketWatch
from arcp_harness.triggers import load_triggers


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
            route_name=route.name if route else None,
            last_assignee=t.assignee or "",
            summary=t.summary or "", description=t.description or ""))
        if (route and route.on_match == "create_or_resume"
                and store.get_session(t.id) is None):
            store.upsert_session(TicketSession(
                issue_id=t.id, key=t.key, profile=route.profile or "-",
                workspace="(adopted)", session_id=None, attempts=0,
                outcome="ABORTED", pending_reason=None, cost_usd=0))
        store.journal("adopted", t.id, t.key)
        n += 1
    return n


def make_reload(loop, disp, cmds, ext, config_path: str = "routes.yaml"):
    """W13/W4.5 hot reload(POST /reload):重讀 config、swap 引用。

    範圍與限制的完整說明見 DESIGN_hotreload.md。壞 config → load_config/
    load_profiles/load_triggers 擲 ConfigError → control API 回 400,
    **舊設定原封續用**(fail-safe)。
    """
    def _reload():
        s_cfg, new_routes = load_config(config_path)
        new_profiles = load_profiles(config_path)
        new_triggers = load_triggers(config_path, new_profiles)
        loop.routes = new_routes
        loop.jql = s_cfg["jql"]
        loop.concurrency = s_cfg.get("concurrency") or loop.concurrency
        loop.triggers = new_triggers                   # W4.5:triggers 可 reload
        disp.profiles = new_profiles
        cmds.profiles = new_profiles
        ext.profiles = new_profiles                    # W4.5:離手定格查表同步
        new_cmt = (s_cfg.get("commands") or {}).get("allowed_commenters")
        if new_cmt:
            cmds.allowed = new_cmt                     # W4.5:白名單可 reload
        new_cancel = (s_cfg.get("external_change") or {}).get("cancel_states")
        if new_cancel:
            ext.cancel_states = new_cancel             # W4.5:終止狀態可 reload
        return {"routes": len(new_routes), "profiles": len(new_profiles),
                "triggers": len(new_triggers)}
    return _reload


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
    # W12/W2.3:機器人身份(assignee 方向判定 + 審批放行偵測)。
    # config 可覆寫(source.bot_account_id),否則啟動時 myself() 解析一次。
    bot_id = (source_cfg.get("bot_account_id")
              or src.myself().get("accountId", ""))
    disp = Dispatcher(src, store, profiles, root="./runtime_live",
                      approval=ApprovalGate(src, store, bot_id))
    # W4.5:allowed_commenters / cancel_states 從 config 接線(原 hardcode)
    cmds = CommandHandler(
        src, store,
        (source_cfg.get("commands") or {}).get("allowed_commenters")
        or ["Shao-wei Chen"],
        profiles=profiles)
    ext = ExternalChangePolicy(
        src, store,
        (source_cfg.get("external_change") or {}).get("cancel_states")
        or ["完成", "Done", "Concluído"],
        bot_account_id=bot_id, profiles=profiles)      # W4.3 離手定格
    loop = OuterLoop(
        src, store, routes, jql,
        dispatcher=disp, commands=cmds, external=ext,
        max_running=source_cfg.get("max_running", 1),
        concurrency=source_cfg.get("concurrency"),
        triggers=load_triggers("routes.yaml", profiles))   # W3.4 scheduled

    _reload = make_reload(loop, disp, cmds, ext)       # W13/W4.5 hot reload

    ctl = source_cfg.get("control") or {}
    api = ControlAPI(loop, store, reload_fn=_reload,
                     host=ctl.get("host", "127.0.0.1"),
                     port=int(ctl.get("port", 8787)))
    api.start()
    # W4.3 快照器:active session 每 N 秒重產 transcript HTML(肉眼監控)
    snap = Snapshotter(store, lambda: disp.profiles,
                       interval_sec=float(
                           source_cfg.get("snapshot_interval_sec", 60)))
    snap.start()
    print(f"[poller] control API on http://{ctl.get('host', '127.0.0.1')}:"
          f"{api.port} (/status /health /pause /resume /reload)", flush=True)

    adopted = adopt_existing(src, store, routes, jql)
    print(f"[poller] adopted {adopted} pre-existing ticket(s); "
          f"live for {minutes:.0f}m, interval {interval:.0f}s", flush=True)

    # 迭代計數時間盒:機器睡眠造成的 wall-clock 跳躍不會吃掉時間盒
    # (lesson:睡眠凍結行程但時鐘照走)
    cycles = max(1, int(minutes * 60 / interval))
    for i in range(cycles):
        if loop.stopping:              # W4.5 graceful shutdown(POST /shutdown)
            print("[poller] graceful shutdown(當前輪已完成)", flush=True)
            break
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
    snap.stop()
    api.stop()
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
