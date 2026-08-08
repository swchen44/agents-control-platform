#!/usr/bin/env python3
"""真 Jira 冒煙測 —— 用 harness 的 JiraCloudSource(測真實程式路徑 + certifi SSL)。

預設**唯讀**(安全):GET /myself(auth + poller 降級偵測用的健康探針)+ search
(poller 讀路徑 + Ticket model round-trip)。加 --write --ticket SCRUM-XX 才做**寫入**
測(add_comment → transition→done → 還原 To Do):驗證 W11 互動服務實際會呼的寫入
路徑 + statusCategory key 'done'。會改到指定票、測完還原——**只對可丟的測試票用**
(假設該票原為 To Do)。

用法:
  python3 smoke_jira.py                              # 唯讀冒煙
  python3 smoke_jira.py --write --ticket SCRUM-36    # 含寫入(測後還原成 To Do)

需 ~/.env(JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN)。不印 token/email/accountId。
"""

from __future__ import annotations

import argparse
import sys
import time

from arcp_harness.config import jira_credentials
from arcp_harness.jira_source import JiraCloudSource

_ok = True


def check(name: str, cond: bool, extra: str = "") -> None:
    global _ok
    tag = "PASS" if cond else "FAIL"
    print(f"  {tag}  {name}" + (f"  ({extra})" if extra else ""))
    _ok = _ok and bool(cond)


def main() -> int:
    ap = argparse.ArgumentParser(description="真 Jira 冒煙測(預設唯讀)")
    ap.add_argument("--write", action="store_true",
                    help="含寫入測(會改指定測試票,測後還原)")
    ap.add_argument("--ticket", help="寫入測用的測試票 key(如 SCRUM-36)")
    ap.add_argument("--jql", default=None,
                    help="search 用的 jql(預設=routes.yaml 的 poller jql;"
                         "新端點禁止無界查詢,務必帶 project 等條件)")
    a = ap.parse_args()

    jql = a.jql
    if not jql:                                   # 預設用 poller 實際的 jql
        try:
            from arcp_harness.routing import load_config
            jql = load_config("routes.yaml")[0].get("jql")
        except Exception:  # noqa: BLE001
            jql = None
        jql = jql or "created >= -30d ORDER BY created DESC"  # 有界 fallback

    try:
        src = JiraCloudSource(*jira_credentials())
    except Exception as e:  # noqa: BLE001 — 缺 ~/.env 等
        print(f"  FAIL  讀取憑證 / 建 client:{e}")
        print("smoke-jira: FAIL")
        return 1

    # 1) 健康探針(jira_health_fn / poller 降級偵測用的 myself)
    try:
        me = src.myself()
        check("GET /myself(auth + 健康探針)", bool(me.get("accountId")),
              f"active={me.get('active')} tz={me.get('timeZone')}")
    except Exception as e:  # noqa: BLE001
        check("GET /myself(auth + 健康探針)", False, str(e)[:100])

    # 2) search(poller 讀路徑;新端點 /search/jql,失敗自動 fallback /search)
    try:
        tix = src.search(jql, max_results=5)
        model_ok = all(t.id > 0 and t.key for t in tix)
        check("search(poller 讀路徑 + model round-trip)", model_ok,
              f"{len(tix)} 張;首張 {tix[0].key if tix else '—'}")
    except Exception as e:  # noqa: BLE001
        check("search(poller 讀路徑)", False, str(e)[:100])

    # 3) 寫入(選配;改真票 → 還原)——驗 W11 apply_submission 的寫入動作
    if a.write:
        if not a.ticket:
            check("寫入測", False, "需 --ticket 指定測試票(如 SCRUM-36)")
        else:
            k = a.ticket
            try:
                src.add_comment(
                    k, f"[smoke_jira {int(time.time())}] 冒煙寫入測(可刪)")
                check("add_comment", True, f"ticket={k}")
                before = src.get_ticket(k).state
                done = src.transition(k, "done")          # statusCategory key
                after = src.get_ticket(k).state
                check("transition→done(W11 close 路徑)",
                      done and after != before, f"{before} → {after}")
                src.transition(k, "new")                  # 還原成 To Do
                back = src.get_ticket(k).state
                check("還原→To Do", back != after, f"{after} → {back}")
            except Exception as e:  # noqa: BLE001
                check("寫入測", False, str(e)[:150])

    print("smoke-jira:", "PASS" if _ok else "FAIL")
    return 0 if _ok else 1


if __name__ == "__main__":
    sys.exit(main())
